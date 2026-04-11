import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import buffer_api
from buffer_api import schedule_to_all_platforms
from social_text import (
    INTERESTING_FACT_PROMPT_TEMPLATE,
    fetch_post_content,
    generate_social_text,
    get_wp_config,
    load_examples,
    resolve_post_image,
    slug_from_url,
    wait_for_user_edit,
)

DEBUG = False


def parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def parse_due_at(due_at_str: str) -> str:
    """Parse a due_at string in MM/DD/YYYY HH:MMAM/PM format to ISO 8601 UTC."""
    dt = datetime.strptime(due_at_str.strip(), "%m/%d/%Y %I:%M%p")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def select_posts_from_config(
    config: dict, rows: list
) -> list[tuple[int, int, str, str | None, list[str] | None]]:
    """Select posts according to the config roadmap.

    Each entry in config["reposts"] specifies post_type(s) and a count.
    Posts are selected in config array order, oldest first within each group.

    Returns a list of (offset, row_index, mode, due_at_iso, tag_ids) tuples.
    """
    default_mode = config.get("defaultMode", "addToQueue")
    already_selected: set[int] = set()
    selected: list[tuple[int, int, str, str | None, list[str] | None]] = []
    offset = 1

    for entry in config["reposts"]:
        post_types = [t.strip() for t in entry["post_type"]]
        count = entry.get("count", 1)
        entry_mode = entry.get("mode", default_mode)
        entry_tags = entry.get("tags") or None

        # Parse due_at if mode is customScheduled
        due_at_iso = None
        if entry_mode == "customScheduled":
            due_at_str = entry.get("due_at")
            if not due_at_str:
                print(
                    f"ERROR: mode 'customScheduled' requires 'due_at' field "
                    f"for post_type {post_types}.",
                    file=sys.stderr,
                )
                sys.exit(1)
            due_at_iso = parse_due_at(due_at_str)
            if count > 1:
                print(
                    f"  WARNING: count={count} with mode 'customScheduled' "
                    f"for post_type {post_types}.\n"
                    f"  This will schedule {count} posts at the same time "
                    f"({due_at_str}).",
                    file=sys.stderr,
                )
                confirm = input("  Continue? (y/n): ").strip().lower()
                if confirm != "y":
                    print("Aborted.", file=sys.stderr)
                    sys.exit(0)

        # Find matching rows with dates, excluding already-selected
        candidates = []
        for i, row in enumerate(rows):
            if i in already_selected:
                continue
            dt = parse_date(row["last_posted_social"])
            if dt is None:
                continue
            if row.get("type", "").strip() in post_types:
                candidates.append((dt, i))

        # Sort by date ascending (oldest first) and take requested count
        candidates.sort(key=lambda x: x[0])
        for _, idx in candidates[:count]:
            selected.append((offset, idx, entry_mode, due_at_iso, entry_tags))
            already_selected.add(idx)
            offset += 1

    return selected


def generate_posts(
    config: dict, data_path: Path, examples_text: str = "",
) -> tuple[list[dict], list, list[tuple[int, int, str, str | None, list[str] | None]]]:
    """Phase 1: Select posts, fetch content, generate social text.

    Returns (posts_data, data_rows, selected_indices) where posts_data is a
    list of dicts ready for JSON review.
    """
    wp_url, wp_user, wp_pass = get_wp_config()
    wp_auth = (wp_user, wp_pass)

    with open(data_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    selected = select_posts_from_config(config, rows)

    # Generate social text for each post
    posts_data: list[dict] = []
    for offset, idx, _mode, _due_at, _tags in selected:
        title = rows[idx]["name"]
        url = rows[idx]["url"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content, featured_media_id, raw_html = fetch_post_content(slug, wp_url, wp_auth)

        img_url = None
        social_text = ""
        static_text = rows[idx].get("static_text", "").strip()
        if content:
            img_url = resolve_post_image(featured_media_id, raw_html, wp_url, wp_auth)

            if static_text:
                print(f"  Using static text.", file=sys.stderr)
                social_text = static_text
            else:
                print(f"  Generating social text...", file=sys.stderr)
                social_text = generate_social_text(
                    content, title, url,
                    INTERESTING_FACT_PROMPT_TEMPLATE, examples_text,
                )

        posts_data.append({
            "title": title,
            "url": url,
            "featured_image": img_url or "",
            "social_text": social_text,
        })

    return posts_data, rows, selected


def write_grouped_json(rows: list[dict], data_path: Path) -> None:
    """Write rows as pretty-printed JSON with blank lines between type sections."""
    with open(data_path, "w", encoding="utf-8") as f:
        f.write("[\n")
        prev_type = None
        for i, row in enumerate(rows):
            cur_type = row.get("type", "")
            if prev_type is not None and cur_type != prev_type:
                f.write("\n")
            prev_type = cur_type
            entry = json.dumps(row, indent=4, ensure_ascii=False)
            indented = "\n".join("  " + line for line in entry.splitlines())
            comma = "," if i < len(rows) - 1 else ""
            f.write(f"{indented}{comma}\n")
        f.write("]\n")


def schedule_posts(
    posts_data: list[dict], rows: list,
    selected_indices: list[tuple[int, int, str, str | None, list[str] | None]],
    config: dict, data_path: Path,
) -> list[tuple[str, str, str, str]]:
    """Phase 2: Read edited posts and schedule to Buffer, update data file."""
    start_date = parse_date(config["startDate"])
    results: list[tuple[str, str, str, str]] = []

    for post, (offset, idx, buffer_mode, due_at, tag_ids) in zip(posts_data, selected_indices):
        title = post["title"]
        url = post["url"]
        img_url = post["featured_image"] or None
        best_text = post["social_text"]

        # Update date in data
        new_date = start_date + timedelta(days=offset)
        rows[idx]["last_posted_social"] = new_date.strftime("%m/%d/%Y")

        if not best_text:
            results.append((title, url, "", "SKIPPED"))
            continue

        mode_label = f" ({buffer_mode})" if buffer_mode == "customScheduled" else ""
        print(f"  Scheduling to Buffer{mode_label}: {title}...", file=sys.stderr)

        platform_results = schedule_to_all_platforms(
            best_text, title, url, img_url, buffer_mode, due_at, tag_ids
        )

        buffer_result = ", ".join(
            f"{k}: {v}" for k, v in platform_results.items()
        )
        results.append((title, url, best_text, buffer_result))

    # Sort by type ascending, then by date descending within each type
    def _sort_key(r: dict) -> tuple[str, str]:
        t = r.get("type", "")
        dt = parse_date(r.get("last_posted_social", ""))
        date_val = dt.strftime("%Y%m%d") if dt else "00000000"
        inv_date = "".join(str(9 - int(c)) for c in date_val)
        return (t, inv_date)

    rows.sort(key=_sort_key)

    # Write the updated JSON with visual section separators
    write_grouped_json(rows, data_path)

    return results


def main() -> None:
    global DEBUG
    import argparse

    parser = argparse.ArgumentParser(
        description="Select and schedule repost content to social media via Buffer."
    )
    default_config = r"G:\My Drive\Unseen Japan\Code\repost-picker-config\config.json"
    default_repost = r"G:\My Drive\Unseen Japan\Code\repost-picker-config\uj-repost-content.json"
    default_examples = r"G:\My Drive\Unseen Japan\Code\repost-picker-config\one-shot-examples"
    parser.add_argument("--config", default=default_config, help="Path to the config JSON file")
    parser.add_argument("--repost-file", default=default_repost, help="Path to the repost data JSON file")
    parser.add_argument(
        "--examples", default=default_examples,
        help="Path to a directory containing example social media posts for style guidance",
    )
    parser.add_argument("--drafts", action="store_true", help="Save posts as drafts in Buffer instead of scheduling")
    parser.add_argument("--debug", action="store_true", help="Dump Buffer GraphQL requests to stdout")
    parsed = parser.parse_args()

    if parsed.debug:
        DEBUG = True
        buffer_api.debug = True

    if parsed.drafts:
        buffer_api.save_drafts = True
        print("Draft mode enabled: posts will be saved as drafts.\n", file=sys.stderr)

    data_path = Path(parsed.repost_file)
    if not data_path.exists():
        print(f"ERROR: Repost file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    # Load examples if provided
    examples_text = ""
    if parsed.examples:
        print(f"Loading style examples from: {parsed.examples}", file=sys.stderr)
        examples_text = load_examples(parsed.examples)
        if examples_text:
            print(f"  Loaded examples successfully.\n", file=sys.stderr)

    with open(parsed.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    # Validate config
    if "startDate" not in config:
        print("Config must include 'startDate'.", file=sys.stderr)
        sys.exit(1)
    if parse_date(config["startDate"]) is None:
        print("Invalid startDate format. Use MM/DD/YYYY.", file=sys.stderr)
        sys.exit(1)
    if "reposts" not in config or not config["reposts"]:
        print("Config must include a non-empty 'reposts' array.", file=sys.stderr)
        sys.exit(1)

    # Phase 1: Generate social text for all posts
    print("Phase 1: Generating social media posts...", file=sys.stderr)
    posts_data, rows, selected_indices = generate_posts(config, data_path, examples_text)

    # Save to JSON for review
    review_path = os.path.join(tempfile.gettempdir(), f"repost_review_{uuid.uuid4().hex[:8]}.json")
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(posts_data, f, indent=2, ensure_ascii=False)

    # Wait for user to edit the file
    wait_for_user_edit(review_path)

    # Read back edited JSON
    with open(review_path, "r", encoding="utf-8") as f:
        edited_posts = json.load(f)

    # Phase 2: Schedule edited posts to Buffer
    print("\nPhase 2: Scheduling posts to Buffer...", file=sys.stderr)
    results = schedule_posts(edited_posts, rows, selected_indices, config, data_path)

    print(f"\n{len(results)} post(s) scheduled:\n")
    for title, url, social_text, buffer_id in results:
        print(f"{title}, {url}, {social_text}, {buffer_id}\n")


if __name__ == "__main__":
    main()
