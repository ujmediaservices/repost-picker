import csv
import os
import re
import sys
from datetime import datetime, timedelta
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import keyring
import requests
import json

CSV_PATH = Path(r".\uj-repost-content.csv")
DATE_COL = "Last posted - social"
TYPE_COL = "Post type"
ESSAY_TYPE = "Essay"
WP_SITE = "https://unseen-japan.com"
CREDENTIAL_TARGET = "https://unseen-japan.com"
BUFFER_API_URL = "https://api.buffer.com"
BUFFER_BLUESKY_CHANNEL_ID = "66997475602872be45e429ee"
BUFFER_THREADS_CHANNEL_ID = "667b1dcd7839e9e87976ad0c"
BUFFER_X_CHANNEL_ID = "5f371d0a1c14ed2014066090"
BUFFER_MASTODON_CHANNEL_ID = "6982c8e331b76c40ca2929b5"

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


def get_wp_credentials() -> tuple[str, str]:
    cred = keyring.get_credential(CREDENTIAL_TARGET, None)
    if cred is None:
        print(
            f"No credential found in Windows Credential Manager "
            f"for '{CREDENTIAL_TARGET}'.",
            file=sys.stderr,
        )
        sys.exit(1)
    return cred.username, cred.password


def slug_from_url(url: str) -> str:
    path = urlparse(url).path.strip("/")
    return path.split("/")[-1]


def fetch_post_content(slug: str, auth: tuple[str, str]) -> tuple[str, int | None]:
    """Fetch post content and featured image ID.

    Returns (plain_text_content, featured_media_id).
    """
    api_url = f"{WP_SITE}/wp-json/wp/v2/posts"
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
    media_id: int, auth: tuple[str, str]
) -> str | None:
    """Get the source URL of a post's featured image.

    Returns the image URL on success, or None.
    """
    api_url = f"{WP_SITE}/wp-json/wp/v2/media/{media_id}"
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


def _buffer_api_key() -> str | None:
    return os.environ.get("BUFFER_API_KEY")


def _buffer_create_post(variables: dict) -> str:
    """Send a createPost mutation to Buffer.

    Returns the post ID on success, or an error string.
    """
    api_key = _buffer_api_key()
    if not api_key:
        return "ERROR: BUFFER_API_KEY environment variable not set"

    query = """
    mutation CreatePost($input: CreatePostInput!) {
      createPost(input: $input) {
        ... on PostActionSuccess {
          post {
            id
            text
          }
        }
        ... on MutationError {
          message
        }
      }
    }
    """

    resp = requests.post(
        BUFFER_API_URL,
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    sent_payload = json.loads(resp.request.body)
    print("Query Sent:\n", sent_payload['query'])
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        return f"ERROR: {data['errors']}"

    result = data.get("data", {}).get("createPost", {})
    if "post" in result:
        return result["post"]["id"]
    if "message" in result:
        return f"ERROR: {result['message']}"
    return f"ERROR: Unexpected response: {data}"


def schedule_to_buffer_bluesky(text: str, post_url: str) -> str:
    """Schedule a post to Buffer Bluesky channel.

    Returns the Buffer post ID on success, or an error message.
    """
    composed_text = f"{text}\n\n{post_url}"
    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_BLUESKY_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNext",
        }
    }
    return _buffer_create_post(variables)


def schedule_to_buffer_mastodon(
    text: str, post_url: str, image_url: str | None
) -> str:
    """Schedule a post to Buffer Mastodon channel with featured image.

    Returns the Buffer post ID on success, or an error message.
    """
    composed_text = f"{text}\n\n{post_url}"
    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_MASTODON_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNext",
        }
    }
    if image_url:
        variables["input"]["assets"] = {"images": [{"url": image_url}]}
    return _buffer_create_post(variables)


def schedule_to_buffer_threads(
    text: str, post_title: str, post_url: str, image_url: str | None
) -> str:
    """Schedule a threaded post to Buffer Threads channel.

    Thread post 1: social media text + featured image
    Thread post 2: post title + two newlines + post URL

    Returns the Buffer post ID on success, or an error message.
    """
    # Thread post 1: social media text + featured image
    thread_post_1 = {"text": text}
    if image_url:
        thread_post_1["assets"] = {"images": [{"url": image_url}]}

    # Thread post 2: post title + link
    thread_post_2 = {
        "text": post_title,
        "assets": {
            "link": {"url": post_url},
        },
    }

    variables = {
        "input": {
            "text": text,
            "channelId": BUFFER_THREADS_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNext",
            "metadata": {
                "threads": {
                    "type": "post",
                    "topic": "Japan",
                    "thread": [thread_post_1, thread_post_2],
                }
            },
        }
    }

    return _buffer_create_post(variables)


def schedule_to_buffer_x(
    text: str, post_title: str, post_url: str, image_url: str | None
) -> str:
    """Schedule a threaded post to Buffer X channel.

    Thread post 1: social media text + featured image
    Thread post 2: post title + link

    Returns the Buffer post ID on success, or an error message.
    """
    # Thread post 1: social media text + featured image
    thread_post_1 = {"text": text}
    if image_url:
        thread_post_1["assets"] = {"images": [{"url": image_url}]}

    # Thread post 2: post title + link
    thread_post_2 = {
        "text": post_title,
        "assets": {
            "link": {"url": post_url},
        },
    }

    variables = {
        "input": {
            "text": text,
            "channelId": BUFFER_X_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNext",
            "metadata": {
                "twitter": {
                    "thread": [thread_post_1, thread_post_2],
                }
            },
        }
    }

    return _buffer_create_post(variables)


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


def pick_reposts(
    num_essays: int, num_travel: int, start_date: datetime
) -> list[tuple[str, str, str, str]]:
    wp_auth = get_wp_credentials()

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

    # Update dates, generate social text, and schedule to Buffer
    results: list[tuple[str, str, str, str]] = []
    for offset, (_, idx) in enumerate(selected, start=1):
        new_date = start_date + timedelta(days=offset)
        rows[idx][DATE_COL] = new_date.strftime("%m/%d/%Y")

        title = rows[idx]["Post Name"]
        url = rows[idx]["Post Link"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content, featured_media_id = fetch_post_content(slug, wp_auth)

        if content:
            # Download featured image
            img_url = None
            if featured_media_id:
                print(f"  Fetching featured image URL...", file=sys.stderr)
                img_url = get_featured_image_url(featured_media_id, wp_auth)
                if img_url:
                    print(f"  Image: {img_url}", file=sys.stderr)
                else:
                    print(f"  WARNING: Could not get featured image URL.", file=sys.stderr)

            print(f"  Generating social text...", file=sys.stderr)
            full_response, best_text = generate_social_text(content, title, url)

            # Schedule to Bluesky channel
            print(f"  Scheduling to Buffer (Bluesky)...", file=sys.stderr)
            bluesky_result = schedule_to_buffer_bluesky(best_text, url)
            print(f"  Bluesky: {bluesky_result}", file=sys.stderr)

            # Schedule to Mastodon channel (with featured image)
            print(f"  Scheduling to Buffer (Mastodon)...", file=sys.stderr)
            mastodon_result = schedule_to_buffer_mastodon(best_text, url, img_url)
            print(f"  Mastodon: {mastodon_result}", file=sys.stderr)

            # Schedule to Threads channel (threaded post with image)
            print(f"  Scheduling to Buffer (Threads)...", file=sys.stderr)
            threads_result = schedule_to_buffer_threads(
                best_text, title, url, img_url
            )
            print(f"  Threads: {threads_result}", file=sys.stderr)

            # Schedule to X channel (threaded post with image)
            print(f"  Scheduling to Buffer (X)...", file=sys.stderr)
            x_result = schedule_to_buffer_x(
                best_text, title, url, img_url
            )
            print(f"  X: {x_result}", file=sys.stderr)

            buffer_result = f"Bluesky: {bluesky_result}, Mastodon: {mastodon_result}, Threads: {threads_result}, X: {x_result}"
        else:
            full_response = "(Could not retrieve post content)"
            best_text = ""
            buffer_result = "SKIPPED"

        results.append((title, url, full_response, buffer_result))

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
    if len(sys.argv) != 4:
        print("Usage: python repost_picker.py <num_essays> <num_travel> <start_date MM/DD/YYYY>")
        sys.exit(1)

    try:
        num_essays = int(sys.argv[1])
    except ValueError:
        print("First argument (num_essays) must be a valid integer.")
        sys.exit(1)

    try:
        num_travel = int(sys.argv[2])
    except ValueError:
        print("Second argument (num_travel) must be a valid integer.")
        sys.exit(1)

    start_date = parse_date(sys.argv[3])
    if start_date is None:
        print("Invalid date format. Use MM/DD/YYYY.")
        sys.exit(1)

    results = pick_reposts(num_essays, num_travel, start_date)

    print(f"\n{len(results)} post(s) selected:\n")
    for title, url, social_text, buffer_id in results:
        print(f"{title}, {url}, {social_text}, {buffer_id}\n")


if __name__ == "__main__":
    main()
