"""Phase 2: Schedule approved posts to Buffer and update the data file.

Reads the review JSON (with social_text filled in by Claude/user),
schedules to all Buffer platforms, updates dates, re-sorts, and writes back.
"""

import json
import sys
from datetime import timedelta
from pathlib import Path

import buffer_api
from buffer_api import schedule_to_all_platforms
from repost_picker import parse_date, write_grouped_json


def schedule_from_review(review_path: str, drafts: bool = False) -> None:
    """Schedule posts from a review JSON file and update the data file."""
    with open(review_path, "r", encoding="utf-8") as f:
        review_data = json.load(f)

    config_path = review_data["config_path"]
    data_path = Path(review_data["data_path"])

    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    with open(data_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    start_date = parse_date(config["startDate"])
    posts = review_data["posts"]
    results = []
    created_post_ids: list[str] = []

    try:
        for post in posts:
            title = post["title"]
            url = post["url"]
            img_url = (post.get("alt_image") or post.get("featured_image") or None)
            best_text = post.get("social_text", "").strip()
            offset = post["offset"]
            idx = post["row_index"]
            buffer_mode = post["mode"]
            due_at = post.get("due_at")
            tag_ids = post.get("tags")

            # Update date in data
            new_date = start_date + timedelta(days=offset)
            rows[idx]["last_posted_social"] = new_date.strftime("%m/%d/%Y")

            # Clear static_text after scheduling
            if post.get("is_static") and rows[idx].get("static_text"):
                rows[idx]["static_text"] = ""

            if not best_text:
                print(f"  SKIPPED (no text): {title}", file=sys.stderr)
                results.append(f"SKIPPED: {title}")
                continue

            mode_label = f" ({buffer_mode})" if buffer_mode == "customScheduled" else ""
            print(f"  Scheduling to Buffer{mode_label}: {title}...", file=sys.stderr)

            platform_results = schedule_to_all_platforms(
                best_text, title, url, img_url, buffer_mode, due_at, tag_ids
            )

            for v in platform_results.values():
                if v and not str(v).startswith("ERROR"):
                    created_post_ids.append(v)

            buffer_result = ", ".join(f"{k}: {v}" for k, v in platform_results.items())
            results.append(f"{title}: {buffer_result}")
            print(f"    {buffer_result}", file=sys.stderr)
    except buffer_api.ImageUploadError as exc:
        created_post_ids.extend(exc.successful_post_ids)
        print(f"\nImage upload failed: {exc}", file=sys.stderr)
        print(
            f"Rolling back: deleting {len(created_post_ids)} post(s) created in this run...",
            file=sys.stderr,
        )
        for pid in created_post_ids:
            ok = buffer_api.delete_buffer_post(pid)
            print(f"  delete {pid}: {'ok' if ok else 'FAILED'}", file=sys.stderr)
        print("Data file not updated.", file=sys.stderr)
        sys.exit(1)

    # Re-sort by type ascending, then by date descending within each type
    def _sort_key(r: dict) -> tuple[str, str]:
        t = r.get("type", "")
        dt = parse_date(r.get("last_posted_social", ""))
        date_val = dt.strftime("%Y%m%d") if dt else "00000000"
        inv_date = "".join(str(9 - int(c)) for c in date_val)
        return (t, inv_date)

    rows.sort(key=_sort_key)

    # Write the updated JSON with visual section separators
    write_grouped_json(rows, data_path)

    print(f"\n{len(results)} post(s) processed.", file=sys.stderr)
    print(f"Data file updated: {data_path}", file=sys.stderr)

    # Print results to stdout
    for r in results:
        print(r)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 2: Schedule approved posts to Buffer and update data file."
    )
    parser.add_argument(
        "--review-file", required=True,
        help="Path to the review JSON file (output from repost_select.py)",
    )
    parser.add_argument(
        "--drafts", action="store_true",
        help="Save posts as drafts in Buffer instead of scheduling",
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Dump Buffer GraphQL requests to stdout",
    )
    parsed = parser.parse_args()

    if parsed.debug:
        buffer_api.debug = True

    if parsed.drafts:
        buffer_api.save_drafts = True
        print("Draft mode enabled: posts will be saved as drafts.\n", file=sys.stderr)

    print("Phase 2: Scheduling posts to Buffer...", file=sys.stderr)
    schedule_from_review(parsed.review_file, parsed.drafts)


if __name__ == "__main__":
    main()
