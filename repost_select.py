"""Phase 1: Select posts from config roadmap, fetch WordPress content, output review JSON.

This script does NOT generate social media text — that's handled by Claude
in the skill workflow. It outputs a JSON file with all the metadata Claude
needs to generate text and the user needs to review.
"""

import json
import os
import sys
import tempfile
import uuid
from pathlib import Path

from repost_picker import (
    parse_date,
    parse_due_at,
    select_posts_from_config,
)
from social_text import (
    fetch_post_content,
    get_wp_config,
    load_examples,
    resolve_post_image,
    slug_from_url,
    strip_html,
)


def select_and_fetch(
    config: dict, data_path: Path, examples_text: str = "",
) -> dict:
    """Select posts per config, fetch WP content, return review structure.

    Returns a dict with keys:
      - posts: list of post dicts (title, url, image, content, static_text, etc.)
      - selected: list of (offset, row_index, mode, due_at, tags) tuples
      - examples_text: style examples string
      - config_path: path to config file
      - data_path: path to data file
    """
    wp_url, wp_user, wp_pass = get_wp_config()
    wp_auth = (wp_user, wp_pass)

    with open(data_path, "r", encoding="utf-8") as f:
        rows = json.load(f)

    selected = select_posts_from_config(config, rows)

    posts = []
    for offset, idx, mode, due_at, tags in selected:
        title = rows[idx]["name"]
        url = rows[idx]["url"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content, featured_media_id, raw_html = fetch_post_content(slug, wp_url, wp_auth)

        img_url = None
        static_text = rows[idx].get("static_text", "").strip()

        if content:
            img_url = resolve_post_image(featured_media_id, raw_html, wp_url, wp_auth)

        # Truncate content to ~4000 chars for the review file (keeps it manageable)
        content_excerpt = content[:4000] if content else ""

        posts.append({
            "title": title,
            "url": url,
            "featured_image": img_url or "",
            "content": content_excerpt,
            "social_text": static_text if static_text else "",
            "is_static": bool(static_text),
            "offset": offset,
            "row_index": idx,
            "mode": mode,
            "due_at": due_at,
            "tags": tags,
        })

    return {
        "posts": posts,
        "examples_text": examples_text,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase 1: Select posts and fetch WordPress content for review."
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
    parsed = parser.parse_args()

    # Validate inputs
    data_path = Path(parsed.repost_file)
    if not data_path.exists():
        print(f"ERROR: Repost file not found: {data_path}", file=sys.stderr)
        sys.exit(1)

    with open(parsed.config, "r", encoding="utf-8") as f:
        config = json.load(f)

    if "startDate" not in config:
        print("Config must include 'startDate'.", file=sys.stderr)
        sys.exit(1)
    if parse_date(config["startDate"]) is None:
        print("Invalid startDate format. Use MM/DD/YYYY.", file=sys.stderr)
        sys.exit(1)
    if "reposts" not in config or not config["reposts"]:
        print("Config must include a non-empty 'reposts' array.", file=sys.stderr)
        sys.exit(1)

    # Load examples
    examples_text = ""
    if parsed.examples:
        print(f"Loading style examples from: {parsed.examples}", file=sys.stderr)
        examples_text = load_examples(parsed.examples)
        if examples_text:
            print(f"  Loaded examples successfully.", file=sys.stderr)

    # Select and fetch
    print("\nPhase 1: Selecting posts and fetching content...", file=sys.stderr)
    review_data = select_and_fetch(config, data_path, examples_text)

    # Add file paths for Phase 2
    review_data["config_path"] = str(parsed.config)
    review_data["data_path"] = str(parsed.repost_file)

    # Write review JSON
    review_path = os.path.join(
        tempfile.gettempdir(),
        f"repost_review_{uuid.uuid4().hex[:8]}.json",
    )
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(review_data, f, indent=2, ensure_ascii=False)

    print(f"\nReview file: {review_path}", file=sys.stderr)
    # Print path to stdout for the skill to capture
    print(review_path)


if __name__ == "__main__":
    main()
