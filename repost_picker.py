import json
import os
import re
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import requests

import buffer_api
from buffer_api import (
    schedule_to_buffer_bluesky,
    schedule_to_buffer_mastodon,
    schedule_to_buffer_threads,
    schedule_to_buffer_x,
)

DEBUG = False

BLUESKY_CHAR_LIMIT = 300

SOCIAL_PROMPT_TEMPLATE = (
    "Select an interesting portion of this post from Unseen Japan for use as "
    "a social media post. Do not rephrase in your own words. Only change "
    "slightly to fit character count or to add missing post context (e.g., "
    "someone's full name), keeping original tone. Do not be overly "
    "promotional, cute, or use marketing jargon or emojis. Be factual, as we "
    "are a serious news and media organization. Reword phrases such as "
    "'recent' and 'new' to avoid time references - e.g., instead of 'a recent survey' or "
    " 'a new survey,' say 'one survey.' Generate several alternatives "
    "and mark the best one.\n\n"
    "IMPORTANT: The post text will be followed by a URL ({url_length} chars) "
    "and two line breaks. The TOTAL post including text + two line breaks + "
    "URL must not exceed {char_limit} characters. So keep each alternative "
    "to {max_text_length} characters or fewer."
)

EXTRACT_BEST_PROMPT = (
    "Extract only the text of the best/recommended social media post from "
    "the above. Return just the text, nothing else. Do not add quotation "
    "marks or any other formatting."
)


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


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return unescape(text).strip()


def get_wp_config() -> tuple[str, str, str]:
    """Read WordPress URL, username, and password from environment variables.

    Returns (wp_url, username, password).
    """
    wp_url = os.environ.get("WORDPRESS_URL")
    username = os.environ.get("WORDPRESS_USERNAME")
    password = os.environ.get("WORDPRESS_PASSWORD")
    missing = []
    if not wp_url:
        missing.append("WORDPRESS_URL")
    if not username:
        missing.append("WORDPRESS_USERNAME")
    if not password:
        missing.append("WORDPRESS_PASSWORD")
    if missing:
        print(
            f"Missing environment variable(s): {', '.join(missing)}",
            file=sys.stderr,
        )
        sys.exit(1)
    return wp_url, username, password


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1]


def fetch_post_content(
    slug: str, wp_url: str, auth: tuple[str, str]
) -> tuple[str, int | None, str]:
    """Fetch post content and featured image ID.

    Searches posts first, then pages if not found.

    Returns (plain_text_content, featured_media_id, raw_html).
    """
    for endpoint in ("posts", "pages"):
        api_url = f"{wp_url}/wp-json/wp/v2/{endpoint}"
        resp = requests.get(
            api_url,
            params={"slug": slug, "_fields": "content,featured_media"},
            auth=auth,
            timeout=30,
        )
        resp.raise_for_status()
        results = resp.json()
        if results:
            raw_html = results[0]["content"]["rendered"]
            content = strip_html(raw_html)
            featured_media = results[0].get("featured_media") or None
            return content, featured_media, raw_html

    return "", None, ""


def extract_first_image_url(html: str) -> str | None:
    """Extract the first <img> src URL from HTML content."""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None


def get_featured_image_url(
    media_id: int, wp_url: str, auth: tuple[str, str]
) -> str | None:
    """Get the source URL of a post's featured image.

    Returns the image URL on success, or None.
    """
    api_url = f"{wp_url}/wp-json/wp/v2/media/{media_id}"
    resp = requests.get(
        api_url,
        params={"_fields": "source_url"},
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    media = resp.json()
    return media.get("source_url") or None


def generate_social_text(
    post_content: str, post_title: str, post_url: str
) -> tuple[str, str]:
    """Generate social media alternatives and extract the best one.

    Returns (full_response, best_text).
    """
    client = anthropic.Anthropic()

    # Calculate max text length: limit - 2 (newlines) - URL length
    url_length = len(post_url)
    max_text_length = BLUESKY_CHAR_LIMIT - 2 - url_length

    social_prompt = SOCIAL_PROMPT_TEMPLATE.format(
        url_length=url_length,
        char_limit=BLUESKY_CHAR_LIMIT,
        max_text_length=max_text_length,
    )

    # Step 1: Generate alternatives
    alternatives_msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": (
                    f"Post title: {post_title}\n\n"
                    f"Post content:\n{post_content}\n\n"
                    f"{social_prompt}"
                ),
            }
        ],
    )
    full_response = alternatives_msg.content[0].text

    # Step 2: Extract the best one
    best_msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": f"{full_response}\n\n{EXTRACT_BEST_PROMPT}",
            }
        ],
    )
    best_text = best_msg.content[0].text.strip()

    # Safety check: truncate if still over limit
    if len(best_text) > max_text_length:
        print(
            f"  WARNING: Text ({len(best_text)} chars) exceeds limit "
            f"({max_text_length}), truncating.",
            file=sys.stderr,
        )
        best_text = best_text[:max_text_length - 1] + "…"

    return full_response, best_text



def parse_due_at(due_at_str: str) -> str:
    """Parse a due_at string in MM/DD/YYYY HH:MMAM/PM format to ISO 8601 UTC."""
    dt = datetime.strptime(due_at_str.strip(), "%m/%d/%Y %I:%M%p")
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def select_posts_from_config(
    config: dict, rows: list
) -> list[tuple[int, int, str, str | None]]:
    """Select posts according to the config roadmap.

    Each entry in config["reposts"] specifies post_type(s) and a count.
    Posts are selected in config array order, oldest first within each group.

    Returns a list of (offset, row_index, mode, due_at_iso) tuples.
    """
    default_mode = config.get("defaultMode", "addToQueue")
    already_selected: set[int] = set()
    selected: list[tuple[int, int, str, str | None]] = []
    offset = 1

    for entry in config["reposts"]:
        post_types = [t.strip() for t in entry["post_type"]]
        count = entry.get("count", 1)
        entry_mode = entry.get("mode", default_mode)

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
            selected.append((offset, idx, entry_mode, due_at_iso))
            already_selected.add(idx)
            offset += 1

    return selected


def generate_posts(
    config: dict, data_path: Path,
) -> tuple[list[dict], list, list[tuple[int, int, str, str | None]]]:
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
    for offset, idx, _mode, _due_at in selected:
        title = rows[idx]["name"]
        url = rows[idx]["url"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content, featured_media_id, raw_html = fetch_post_content(slug, wp_url, wp_auth)

        img_url = None
        social_text = ""
        static_text = rows[idx].get("static_text", "").strip()
        if content:
            if featured_media_id:
                print(f"  Fetching featured image URL...", file=sys.stderr)
                img_url = get_featured_image_url(featured_media_id, wp_url, wp_auth)
                if img_url:
                    print(f"  Featured image: {img_url}", file=sys.stderr)

            if not img_url and raw_html:
                img_url = extract_first_image_url(raw_html)
                if img_url:
                    print(f"  Image from content: {img_url}", file=sys.stderr)

            if not img_url:
                print(f"  WARNING: No image found.", file=sys.stderr)

            if static_text:
                print(f"  Using static text.", file=sys.stderr)
                social_text = static_text
            else:
                print(f"  Generating social text...", file=sys.stderr)
                _full_response, best_text = generate_social_text(content, title, url)
                social_text = best_text

        posts_data.append({
            "title": title,
            "url": url,
            "featured_image": img_url or "",
            "social_text": social_text,
        })

    return posts_data, rows, selected


def wait_for_user_edit(file_path: str) -> None:
    """Prompt the user to edit a file and wait for them to press Enter."""
    print(f"\n  Review file ready for editing:\n  {file_path}\n", file=sys.stderr)
    print("  Edit the social_text fields as needed, then save the file.", file=sys.stderr)
    input("  Press Enter when done to continue scheduling...")


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
            # Indent the whole block by 2 spaces
            indented = "\n".join("  " + line for line in entry.splitlines())
            comma = "," if i < len(rows) - 1 else ""
            f.write(f"{indented}{comma}\n")
        f.write("]\n")


def schedule_posts(
    posts_data: list[dict], rows: list,
    selected_indices: list[tuple[int, int, str, str | None]],
    config: dict, data_path: Path
) -> list[tuple[str, str, str, str]]:
    """Phase 2: Read edited posts and schedule to Buffer, update data file."""
    start_date = parse_date(config["startDate"])
    results: list[tuple[str, str, str, str]] = []

    for post, (offset, idx, buffer_mode, due_at) in zip(posts_data, selected_indices):
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

        # Schedule to Bluesky channel
        print(f"  Scheduling to Buffer (Bluesky){mode_label}: {title}...", file=sys.stderr)
        bluesky_result = schedule_to_buffer_bluesky(best_text, url, buffer_mode, due_at)
        print(f"  Bluesky: {bluesky_result}", file=sys.stderr)

        # Schedule to Mastodon channel (with featured image)
        print(f"  Scheduling to Buffer (Mastodon){mode_label}...", file=sys.stderr)
        mastodon_result = schedule_to_buffer_mastodon(best_text, url, img_url, buffer_mode, due_at)
        print(f"  Mastodon: {mastodon_result}", file=sys.stderr)

        # Schedule to Threads channel (threaded post with image)
        print(f"  Scheduling to Buffer (Threads){mode_label}...", file=sys.stderr)
        threads_result = schedule_to_buffer_threads(
            best_text, title, url, img_url, buffer_mode, due_at
        )
        print(f"  Threads: {threads_result}", file=sys.stderr)

        # Schedule to X channel (threaded post with image)
        print(f"  Scheduling to Buffer (X){mode_label}...", file=sys.stderr)
        x_result = schedule_to_buffer_x(
            best_text, title, url, img_url, buffer_mode, due_at
        )
        print(f"  X: {x_result}", file=sys.stderr)

        buffer_result = f"Bluesky: {bluesky_result}, Mastodon: {mastodon_result}, Threads: {threads_result}, X: {x_result}"
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
    parser.add_argument("--config", required=True, help="Path to the config JSON file")
    parser.add_argument("--repost-file", required=True, help="Path to the repost data JSON file")
    parser.add_argument("--debug", action="store_true", help="Dump Buffer GraphQL requests to stdout")
    parsed = parser.parse_args()

    if parsed.debug:
        DEBUG = True
        buffer_api.debug = True

    data_path = Path(parsed.repost_file)
    if not data_path.exists():
        print(f"ERROR: Repost file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

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
    posts_data, rows, selected_indices = generate_posts(config, data_path)

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
