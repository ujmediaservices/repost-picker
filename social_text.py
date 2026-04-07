"""Shared utilities for generating social media text and working with WordPress."""

import os
import re
import sys
from html import unescape
from pathlib import Path
from urllib.parse import urlparse

import anthropic
import requests

BLUESKY_CHAR_LIMIT = 300

INTERESTING_FACT_PROMPT_TEMPLATE = (
    "Select an interesting fact or portion of this post from Unseen Japan for "
    "use as a social media post. Do not rephrase in your own words. Only change "
    "slightly to fit character count or to add missing post context (e.g., "
    "someone's full name), keeping original tone. Do not be overly "
    "promotional, cute, or use marketing jargon or emojis. Be factual, as we "
    "are a serious news and media organization. Reword phrases such as "
    "'recent' and 'new' to avoid time references - e.g., instead of 'a recent "
    "survey' or 'a new survey,' say 'one survey.' Generate several alternatives "
    "and mark the best one.\n\n"
    "IMPORTANT: The post text will be followed by a URL ({url_length} chars) "
    "and two line breaks. The TOTAL post including text + two line breaks + "
    "URL must not exceed {char_limit} characters. So keep each alternative "
    "to {max_text_length} characters or fewer."
)

ICYMI_PROMPT_TEMPLATE = (
    "Write an ICYMI (In Case You Missed It) social media post summarizing the "
    "main gist of this article from Unseen Japan. Start with 'ICYMI:' and "
    "keep the tone factual and informative. Do not be overly promotional, "
    "cute, or use marketing jargon or emojis. We are a serious news and media "
    "organization. Generate several alternatives and mark the best one.\n\n"
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


def load_examples(examples_dir: str) -> str:
    """Load all text/JSON files from a directory as style examples.

    Returns a formatted string of examples to include in prompts.
    """
    examples_path = Path(examples_dir)
    if not examples_path.is_dir():
        print(f"ERROR: Examples directory not found: {examples_dir}", file=sys.stderr)
        sys.exit(1)

    examples = []
    for f in sorted(examples_path.iterdir()):
        if f.suffix in (".txt", ".json", ".md"):
            content = f.read_text(encoding="utf-8").strip()
            if content:
                examples.append(f"--- Example from {f.name} ---\n{content}")

    if not examples:
        print(f"WARNING: No example files found in {examples_dir}", file=sys.stderr)
        return ""

    return (
        "Here are examples of our social media posting style. "
        "Match this tone and approach:\n\n"
        + "\n\n".join(examples)
        + "\n\n"
    )


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return unescape(text).strip()


def extract_first_image_url(html: str) -> str | None:
    """Extract the first <img> src URL from HTML content."""
    match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html)
    return match.group(1) if match else None


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


def resolve_post_image(
    featured_media_id: int | None, raw_html: str,
    wp_url: str, auth: tuple[str, str]
) -> str | None:
    """Resolve the best image URL for a post.

    Tries featured image first, then falls back to the first image in content.
    """
    img_url = None
    if featured_media_id:
        img_url = get_featured_image_url(featured_media_id, wp_url, auth)
        if img_url:
            print(f"  Featured image: {img_url}", file=sys.stderr)
    if not img_url and raw_html:
        img_url = extract_first_image_url(raw_html)
        if img_url:
            print(f"  Image from content: {img_url}", file=sys.stderr)
    if not img_url:
        print(f"  WARNING: No image found.", file=sys.stderr)
    return img_url


def generate_social_text(
    post_content: str, post_title: str, post_url: str,
    prompt_template: str, examples_text: str = ""
) -> str:
    """Generate social media text using a given prompt template.

    Returns the best text.
    """
    client = anthropic.Anthropic()

    url_length = len(post_url)
    max_text_length = BLUESKY_CHAR_LIMIT - 2 - url_length

    prompt = prompt_template.format(
        url_length=url_length,
        char_limit=BLUESKY_CHAR_LIMIT,
        max_text_length=max_text_length,
    )

    # Step 1: Generate alternatives
    user_content = f"Post title: {post_title}\n\nPost content:\n{post_content}\n\n"
    if examples_text:
        user_content += examples_text
    user_content += prompt

    alternatives_msg = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1024,
        messages=[
            {
                "role": "user",
                "content": user_content,
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

    # Safety check
    if len(best_text) > max_text_length:
        print(
            f"  WARNING: Text ({len(best_text)} chars) exceeds limit "
            f"({max_text_length}), truncating.",
            file=sys.stderr,
        )
        best_text = best_text[: max_text_length - 1] + "…"

    return best_text


def wait_for_user_edit(file_path: str) -> None:
    """Prompt the user to edit a file and wait for them to press Enter."""
    print(f"\n  Review file ready for editing:\n  {file_path}\n", file=sys.stderr)
    print("  Edit the social_text fields as needed, then save the file.", file=sys.stderr)
    input("  Press Enter when done to continue scheduling...")
