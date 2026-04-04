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

CSV_PATH = Path(r".\uj-repost-content.csv")
DATE_COL = "Last posted - social"
WP_SITE = "https://unseen-japan.com"
CREDENTIAL_TARGET = "https://unseen-japan.com"
BUFFER_API_URL = "https://api.buffer.com"
BUFFER_CHANNEL_ID = "66997475602872be45e429ee"

BLUESKY_CHAR_LIMIT = 300

SOCIAL_PROMPT_TEMPLATE = (
    "Select an interesting portion of this post from Unseen Japan for use as "
    "a social media post. Do not rephrase in your own words. Only change "
    "slightly to fit character count or to add missing post context (e.g., "
    "someone's full name), keeping original tone. Do not be overly "
    "promotional, cute, or use marketing jargon or emojis. Be factual, as we "
    "are a serious news and media organization. Generate several alternatives "
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


def fetch_post_content(slug: str, auth: tuple[str, str]) -> str:
    api_url = f"{WP_SITE}/wp-json/wp/v2/posts"
    resp = requests.get(
        api_url,
        params={"slug": slug, "_fields": "content"},
        auth=auth,
        timeout=30,
    )
    resp.raise_for_status()
    posts = resp.json()
    if not posts:
        return ""
    return strip_html(posts[0]["content"]["rendered"])


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


def schedule_to_buffer(text: str, post_url: str) -> str:
    """Schedule a post to Buffer via GraphQL API.

    Returns the Buffer post ID on success, or an error message.
    """
    api_key = os.environ.get("BUFFER_API_KEY")
    if not api_key:
        return "ERROR: BUFFER_API_KEY environment variable not set"

    composed_text = f"{text}\n\n{post_url}"

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

    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": "shareNext",
        }
    }

    resp = requests.post(
        BUFFER_API_URL,
        json={"query": query, "variables": variables},
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
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


def pick_reposts(
    num_posts: int, start_date: datetime
) -> list[tuple[str, str, str, str]]:
    wp_auth = get_wp_credentials()

    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Build list of (parsed_date, index) for rows that have a date
    dated = []
    for i, row in enumerate(rows):
        dt = parse_date(row[DATE_COL])
        if dt is not None:
            dated.append((dt, i))

    # Sort by date ascending (oldest first) and take the top n
    dated.sort(key=lambda x: x[0])
    selected = dated[:num_posts]

    # Update dates, generate social text, and schedule to Buffer
    results: list[tuple[str, str, str, str]] = []
    for offset, (_, idx) in enumerate(selected, start=1):
        new_date = start_date + timedelta(days=offset)
        rows[idx][DATE_COL] = new_date.strftime("%m/%d/%Y")

        title = rows[idx]["Post Name"]
        url = rows[idx]["Post Link"]
        slug = slug_from_url(url)

        print(f"  Fetching: {title}...", file=sys.stderr)
        content = fetch_post_content(slug, wp_auth)

        if content:
            print(f"  Generating social text...", file=sys.stderr)
            full_response, best_text = generate_social_text(content, title, url)

            print(f"  Scheduling to Buffer...", file=sys.stderr)
            buffer_result = schedule_to_buffer(best_text, url)
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
    try:
        num_posts = int(input("Number of posts to retrieve: "))
    except ValueError:
        print("Please enter a valid integer.")
        sys.exit(1)

    date_str = input("Date to search from (MM/DD/YYYY): ").strip()
    start_date = parse_date(date_str)
    if start_date is None:
        print("Invalid date format. Use MM/DD/YYYY.")
        sys.exit(1)

    results = pick_reposts(num_posts, start_date)

    print(f"\n{len(results)} post(s) selected:\n")
    for title, url, social_text, buffer_id in results:
        print(f"{title}, {url}, {social_text}, {buffer_id}\n")


if __name__ == "__main__":
    main()
