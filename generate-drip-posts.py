"""Fetch recent WordPress posts and generate three scheduled social media drip posts each."""

import argparse
import json
import os
import random
import sys
import tempfile
import uuid
from datetime import datetime, timedelta
from html import unescape

import requests

import buffer_api
from buffer_api import schedule_to_all_platforms
from social_text import (
    ICYMI_PROMPT_TEMPLATE,
    INTERESTING_FACT_PROMPT_TEMPLATE,
    extract_first_image_url,
    generate_social_text,
    get_featured_image_url,
    get_wp_config,
    load_examples,
    strip_html,
    wait_for_user_edit,
)

BUFFER_MODE = "customScheduled"


def fetch_recent_posts(
    wp_url: str, auth: tuple[str, str], num_posts: int
) -> list[dict]:
    """Fetch the most recent published posts from WordPress.

    Returns a list of dicts with title, url, content, featured_image.
    """
    api_url = f"{wp_url}/wp-json/wp/v2/posts"
    resp = requests.get(
        api_url,
        params={
            "per_page": num_posts,
            "orderby": "date",
            "order": "desc",
            "status": "publish",
            "_fields": "title,link,content,featured_media",
        },
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    wp_posts = resp.json()

    posts = []
    for wp in wp_posts:
        title = unescape(wp["title"]["rendered"])
        url = wp["link"]
        raw_html = wp["content"]["rendered"]
        content = strip_html(raw_html)
        featured_media_id = wp.get("featured_media") or None

        # Get featured image URL
        img_url = None
        if featured_media_id:
            img_url = get_featured_image_url(featured_media_id, wp_url, auth)

        if not img_url:
            img_url = extract_first_image_url(raw_html)

        posts.append({
            "title": title,
            "url": url,
            "content": content,
            "featured_image": img_url or "",
        })

    return posts


def random_am_time() -> tuple[int, int]:
    """Generate a random AM time suitable for US readers (7-11 AM ET)."""
    hour = random.randint(7, 11)
    minute = random.randint(0, 59)
    return hour, minute


def generate_schedule_dates(
    base_date: datetime,
) -> list[tuple[str, datetime]]:
    """Generate three schedule dates: tomorrow, +1 week, +1 month.

    Returns list of (label, datetime) tuples.
    """
    tomorrow = base_date + timedelta(days=1)
    one_week = base_date + timedelta(weeks=1)
    one_month = base_date + timedelta(days=30)

    dates = []
    for label, dt in [
        ("tomorrow", tomorrow),
        ("one_week", one_week),
        ("one_month", one_month),
    ]:
        hour, minute = random_am_time()
        scheduled = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        dates.append((label, scheduled))

    return dates


def datetime_to_iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def generate_drip_posts(
    posts: list[dict], examples_text: str = ""
) -> list[dict]:
    """Generate three drip posts per article.

    Returns a flat list of drip post dicts ready for review.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    drip_posts = []

    for post in posts:
        title = post["title"]
        url = post["url"]
        content = post["content"]
        featured_image = post["featured_image"]

        schedule_dates = generate_schedule_dates(today)

        print(f"  Generating drip posts for: {title}...", file=sys.stderr)

        # Post 1: Interesting fact (tomorrow)
        print(f"    Tomorrow: generating interesting fact...", file=sys.stderr)
        fact_text_1 = generate_social_text(
            content, title, url, INTERESTING_FACT_PROMPT_TEMPLATE, examples_text
        )

        # Post 2: Different interesting fact (one week)
        print(f"    One week: generating interesting fact...", file=sys.stderr)
        fact_text_2 = generate_social_text(
            content, title, url, INTERESTING_FACT_PROMPT_TEMPLATE, examples_text
        )

        # Post 3: ICYMI summary (one month)
        print(f"    One month: generating ICYMI post...", file=sys.stderr)
        icymi_text = generate_social_text(
            content, title, url, ICYMI_PROMPT_TEMPLATE, examples_text
        )

        texts = [fact_text_1, fact_text_2, icymi_text]
        labels = ["Interesting fact", "Interesting fact", "ICYMI"]

        for (label_key, scheduled_dt), text, label in zip(
            schedule_dates, texts, labels
        ):
            drip_posts.append({
                "title": title,
                "url": url,
                "featured_image": featured_image,
                "social_text": text,
                "post_type": label,
                "scheduled_date": scheduled_dt.strftime("%m/%d/%Y"),
                "scheduled_time": scheduled_dt.strftime("%I:%M%p"),
                "due_at_iso": datetime_to_iso(scheduled_dt),
            })

    return drip_posts


def schedule_drip_posts(
    drip_posts: list[dict], tag_ids: list[str] | None = None,
) -> list[dict]:
    """Schedule all drip posts to Buffer."""
    results = []

    for post in drip_posts:
        title = post["title"]
        url = post["url"]
        img_url = post["featured_image"] or None
        text = post["social_text"]
        due_at = post["due_at_iso"]
        post_type = post["post_type"]
        sched_date = post["scheduled_date"]
        sched_time = post["scheduled_time"]

        if not text:
            results.append({**post, "buffer_result": "SKIPPED"})
            continue

        label = f"{post_type} ({sched_date} {sched_time})"
        print(f"  Scheduling: {title} [{label}]...", file=sys.stderr)

        platform_results = schedule_to_all_platforms(
            text, title, url, img_url, BUFFER_MODE, due_at, tag_ids
        )

        buffer_result = ", ".join(
            f"{k}: {v}" for k, v in platform_results.items()
        )
        results.append({**post, "buffer_result": buffer_result})

    return results


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate drip social media posts for recent WordPress articles."
    )
    parser.add_argument(
        "--num-posts", type=int, required=True,
        help="Number of most recent posts to generate drip posts for",
    )
    parser.add_argument(
        "--examples", default=None,
        help="Path to a directory containing example social media posts for style guidance",
    )
    parser.add_argument(
        "--drafts", action="store_true",
        help="Save posts as drafts in Buffer instead of scheduling",
    )
    parser.add_argument(
        "--tags", default=None,
        help="Comma-delimited list of pre-created Buffer tag IDs to apply to posts",
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

    tag_ids = None
    if parsed.tags:
        tag_ids = [t.strip() for t in parsed.tags.split(",") if t.strip()]
        print(f"Using {len(tag_ids)} tag ID(s): {', '.join(tag_ids)}\n", file=sys.stderr)

    # Load examples if provided
    examples_text = ""
    if parsed.examples:
        print(f"Loading style examples from: {parsed.examples}", file=sys.stderr)
        examples_text = load_examples(parsed.examples)
        if examples_text:
            print(f"  Loaded examples successfully.\n", file=sys.stderr)

    # Phase 1: Fetch recent posts and generate drip content
    print("Phase 1: Fetching recent posts...", file=sys.stderr)
    wp_url, wp_user, wp_pass = get_wp_config()
    wp_auth = (wp_user, wp_pass)

    posts = fetch_recent_posts(wp_url, wp_auth, parsed.num_posts)
    print(f"  Found {len(posts)} post(s).\n", file=sys.stderr)

    print("Generating drip posts...", file=sys.stderr)
    drip_posts = generate_drip_posts(posts, examples_text)

    # Save to temp JSON for review
    review_path = os.path.join(
        tempfile.gettempdir(),
        f"drip_review_{uuid.uuid4().hex[:8]}.json",
    )
    with open(review_path, "w", encoding="utf-8") as f:
        json.dump(drip_posts, f, indent=2, ensure_ascii=False)

    # Wait for user to edit
    wait_for_user_edit(review_path)

    # Read back edited JSON
    with open(review_path, "r", encoding="utf-8") as f:
        edited_posts = json.load(f)

    # Phase 2: Schedule to Buffer
    print("\nPhase 2: Scheduling drip posts to Buffer...", file=sys.stderr)
    results = schedule_drip_posts(edited_posts, tag_ids)

    print(f"\n{len(results)} drip post(s) scheduled:\n")
    for r in results:
        print(
            f"  {r['title']} [{r['post_type']}] "
            f"({r['scheduled_date']} {r['scheduled_time']})"
        )
        print(f"    Text: {r['social_text'][:100]}...")
        print(f"    Result: {r.get('buffer_result', 'N/A')}")
        print()


if __name__ == "__main__":
    main()
