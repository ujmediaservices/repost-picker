import csv
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

CSV_PATH = Path(r".\uj-repost-content.csv")
DATE_COL = "Last posted - social"
TYPE_COL = "Post type"
ESSAY_TYPE = "Essay"
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


def fetch_post_content(slug: str, wp_url: str, auth: tuple[str, str]) -> tuple[str, int | None]:
    """Fetch post content and featured image ID.

    Returns (plain_text_content, featured_media_id).
    """
    api_url = f"{wp_url}/wp-json/wp/v2/posts"
    resp = requests.get(
        api_url,
        params={"slug": slug, "_fields": "content,featured_media"},
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    posts = resp.json()
    if not posts:
        return "", None
    content = strip_html(posts[0]["content"]["rendered"])
    featured_media = posts[0].get("featured_media") or None
    return content, featured_media


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



def interleave(primary: list, secondary: list) -> list:
    """Interleave two lists, giving priority to primary (appears first)."""
    result = []
    pi, si = 0, 0
    while pi < len(primary) or si < len(secondary):
        if pi < len(primary):
            result.append(primary[pi])
            pi += 1
        if si < len(secondary):
            result.append(secondary[si])
            si += 1
    return result


def generate_posts(
    num_essays: int, num_travel: int, start_date: datetime
) -> tuple[list[dict], list, list[tuple]]:
    """Phase 1: Select posts, fetch content, generate social text.

    Returns (posts_data, csv_rows, selected_indices) where posts_data is a
    list of dicts ready for JSON review.
    """
    wp_url, wp_user, wp_pass = get_wp_config()
    wp_auth = (wp_user, wp_pass)

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Split into essays and travel promo posts by Post type
    essays = []
    travel = []
    for i, row in enumerate(rows):
        dt = parse_date(row[DATE_COL])
        if dt is None:
            continue
        if row.get(TYPE_COL, "").strip() == ESSAY_TYPE:
            essays.append((dt, i))
        else:
            travel.append((dt, i))

    # Sort each by date ascending (oldest first) and take the requested count
    essays.sort(key=lambda x: x[0])
    travel.sort(key=lambda x: x[0])
    selected_essays = essays[:num_essays]
    selected_travel = travel[:num_travel]

    # Interleave with travel promo posts as priority (first)
    selected = interleave(selected_travel, selected_essays)

    # Generate social text for each post
    posts_data: list[dict] = []
    selected_indices: list[tuple] = []  # (offset, idx) for CSV updates
    for offset, (_, idx) in enumerate(selected, start=1):
        title = rows[idx]["Post Name"]
        url = rows[idx]["Post Link"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content, featured_media_id = fetch_post_content(slug, wp_url, wp_auth)

        img_url = None
        social_text = ""
        if content:
            if featured_media_id:
                print(f"  Fetching featured image URL...", file=sys.stderr)
                img_url = get_featured_image_url(featured_media_id, wp_url, wp_auth)
                if img_url:
                    print(f"  Image: {img_url}", file=sys.stderr)
                else:
                    print(f"  WARNING: Could not get featured image URL.", file=sys.stderr)

            print(f"  Generating social text...", file=sys.stderr)
            _full_response, best_text = generate_social_text(content, title, url)
            social_text = best_text

        posts_data.append({
            "title": title,
            "url": url,
            "featured_image": img_url or "",
            "social_text": social_text,
        })
        selected_indices.append((offset, idx))

    return posts_data, rows, selected_indices


def wait_for_user_edit(file_path: str) -> None:
    """Prompt the user to edit a file and wait for them to press Enter."""
    print(f"\n  Review file ready for editing:\n  {file_path}\n", file=sys.stderr)
    print("  Edit the social_text fields as needed, then save the file.", file=sys.stderr)
    input("  Press Enter when done to continue scheduling...")


def schedule_posts(
    posts_data: list[dict], rows: list, selected_indices: list[tuple],
    start_date: datetime
) -> list[tuple[str, str, str, str]]:
    """Phase 2: Read edited posts and schedule to Buffer, update CSV."""
    results: list[tuple[str, str, str, str]] = []

    for post, (offset, idx) in zip(posts_data, selected_indices):
        title = post["title"]
        url = post["url"]
        img_url = post["featured_image"] or None
        best_text = post["social_text"]

        # Update CSV date
        new_date = start_date + timedelta(days=offset)
        rows[idx][DATE_COL] = new_date.strftime("%m/%d/%Y")

        if not best_text:
            results.append((title, url, "", "SKIPPED"))
            continue

        BUFFER_MODE = "addToQueue"

        # Schedule to Bluesky channel
        print(f"  Scheduling to Buffer (Bluesky): {title}...", file=sys.stderr)
        bluesky_result = schedule_to_buffer_bluesky(best_text, url, BUFFER_MODE)
        print(f"  Bluesky: {bluesky_result}", file=sys.stderr)

        # Schedule to Mastodon channel (with featured image)
        print(f"  Scheduling to Buffer (Mastodon)...", file=sys.stderr)
        mastodon_result = schedule_to_buffer_mastodon(best_text, url, img_url, BUFFER_MODE)
        print(f"  Mastodon: {mastodon_result}", file=sys.stderr)

        # Schedule to Threads channel (threaded post with image)
        print(f"  Scheduling to Buffer (Threads)...", file=sys.stderr)
        threads_result = schedule_to_buffer_threads(
            best_text, title, url, img_url, BUFFER_MODE
        )
        print(f"  Threads: {threads_result}", file=sys.stderr)

        # Schedule to X channel (threaded post with image)
        print(f"  Scheduling to Buffer (X)...", file=sys.stderr)
        x_result = schedule_to_buffer_x(
            best_text, title, url, img_url, BUFFER_MODE
        )
        print(f"  X: {x_result}", file=sys.stderr)

        buffer_result = f"Bluesky: {bluesky_result}, Mastodon: {mastodon_result}, Threads: {threads_result}, X: {x_result}"
        results.append((title, url, best_text, buffer_result))

    # Sort rows by date descending before saving (undated rows go to the end)
    rows.sort(
        key=lambda r: parse_date(r[DATE_COL]) or datetime.min,
        reverse=True,
    )

    # Write the updated CSV back
    fieldnames = list(rows[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return results


def main() -> None:
    global DEBUG
    args = [a for a in sys.argv[1:] if a != "--debug"]
    if "--debug" in sys.argv:
        DEBUG = True
        buffer_api.debug = True

    if len(args) != 3:
        print("Usage: python repost_picker.py [--debug] <num_essays> <num_travel> <start_date MM/DD/YYYY>")
        sys.exit(1)

    try:
        num_essays = int(args[0])
    except ValueError:
        print("First argument (num_essays) must be a valid integer.")
        sys.exit(1)

    try:
        num_travel = int(args[1])
    except ValueError:
        print("Second argument (num_travel) must be a valid integer.")
        sys.exit(1)

    start_date = parse_date(args[2])
    if start_date is None:
        print("Invalid date format. Use MM/DD/YYYY.")
        sys.exit(1)

    # Phase 1: Generate social text for all posts
    print("Phase 1: Generating social media posts...", file=sys.stderr)
    posts_data, rows, selected_indices = generate_posts(
        num_essays, num_travel, start_date
    )

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
    results = schedule_posts(edited_posts, rows, selected_indices, start_date)

    print(f"\n{len(results)} post(s) scheduled:\n")
    for title, url, social_text, buffer_id in results:
        print(f"{title}, {url}, {social_text}, {buffer_id}\n")


if __name__ == "__main__":
    main()
