"""Buffer GraphQL API client for scheduling social media posts."""

import json
import os
import sys
import time

import requests

from io import BytesIO
from urllib.parse import urlparse

BUFFER_API_URL = "https://api.buffer.com"
LITTERBOX_API_URL = "https://litterbox.catbox.moe/resources/internals/api.php"

BUFFER_BLUESKY_CHANNEL_ID = "66997475602872be45e429ee"
BUFFER_THREADS_CHANNEL_ID = "667b1dcd7839e9e87976ad0c"
BUFFER_X_CHANNEL_ID = "5f371d0a1c14ed2014066090"
BUFFER_MASTODON_CHANNEL_ID = "6982c8e331b76c40ca2929b5"

VALID_MODES = (
    "addToQueue",
    "shareNow",
    "shareNext",
    "customScheduled",
    "recommendedTime",
)

IMAGE_DIMENSION_ERROR = "Failed to fetch image dimensions"

debug = False
save_drafts = False

MAX_RETRIES = 5
INITIAL_BACKOFF = 1  # seconds

# Cache of original image URL -> Litterbox URL from earlier in the same run.
# When a Litterbox upload succeeds, the mapping is stored here so that
# subsequent calls skip the failing original URL entirely.
_litterbox_cache: dict[str, str] = {}


def upload_to_litterbox(img_url: str) -> str | None:
    """Download an image and re-host it on Litterbox (72h expiry).

    Returns the Litterbox URL on success, or None on any failure.
    """
    try:
        resp = requests.get(img_url, timeout=30)
        resp.raise_for_status()

        # Derive a filename from the URL path, falling back to "image.jpg"
        path = urlparse(img_url).path
        filename = os.path.basename(path) if os.path.basename(path) else "image.jpg"

        upload_resp = requests.post(
            LITTERBOX_API_URL,
            data={"reqtype": "fileupload", "time": "72h"},
            files={"fileToUpload": (filename, BytesIO(resp.content))},
            timeout=60,
        )
        upload_resp.raise_for_status()

        litterbox_url = upload_resp.text.strip()
        if litterbox_url.startswith("http"):
            return litterbox_url

        print(f"  Litterbox returned unexpected response: {litterbox_url}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"  Litterbox upload failed: {exc}", file=sys.stderr)
        return None


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

    if save_drafts:
        variables["input"]["saveToDraft"] = True

    payload = {"query": query, "variables": variables}

    if debug:
        print("\n=== DEBUG: Buffer GraphQL Request ===", flush=True)
        print("Query:", query.strip(), flush=True)
        print("Variables:", json.dumps(variables, indent=2, ensure_ascii=False), flush=True)
        print("=====================================\n", flush=True)

    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.post(
                BUFFER_API_URL,
                json=payload,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                timeout=30,
            )
            if resp.status_code == 429:
                wait = INITIAL_BACKOFF * (2 ** attempt)
                print(f"  Buffer rate limited. Retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            break
        except (requests.ConnectionError, requests.Timeout, OSError) as e:
            wait = INITIAL_BACKOFF * (2 ** attempt)
            print(f"  Buffer request error: {e}. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
    else:
        return "ERROR: Max retries exceeded"

    data = resp.json()

    if "errors" in data:
        return f"ERROR: {data['errors']}"

    result = data.get("data", {}).get("createPost", {})
    if "post" in result:
        return result["post"]["id"]
    if "message" in result:
        return f"ERROR: {result['message']}"
    return f"ERROR: Unexpected response: {data}"


def _buffer_graphql_query(query_str: str, variables: dict | None = None) -> dict:
    """Execute a read-only GraphQL query against Buffer API."""
    api_key = _buffer_api_key()
    if not api_key:
        raise RuntimeError("BUFFER_API_KEY environment variable not set")

    payload = {"query": query_str}
    if variables:
        payload["variables"] = variables

    resp = requests.post(
        BUFFER_API_URL,
        json=payload,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()

    if "errors" in data:
        raise RuntimeError(f"Buffer GraphQL error: {data['errors']}")
    return data.get("data", {})


def get_organization_tags() -> list[dict]:
    """Fetch all tags for the current Buffer organization."""
    org_data = _buffer_graphql_query("""
        query {
            account {
                currentOrganization {
                    id
                }
            }
        }
    """)
    org_id = org_data.get("account", {}).get("currentOrganization", {}).get("id")
    if not org_id:
        raise RuntimeError("Could not determine Buffer organization ID")

    tags_data = _buffer_graphql_query("""
        query GetTags($organizationId: String!) {
            tags(input: { organizationId: $organizationId }) {
                id
                name
                color
            }
        }
    """, {"organizationId": org_id})

    return tags_data.get("tags", [])


def resolve_tag_names_to_ids(tag_names: list[str]) -> list[str]:
    """Resolve tag names to Buffer tag IDs.

    Raises RuntimeError if any tag name is not found.
    """
    if not tag_names:
        return []

    all_tags = get_organization_tags()
    name_to_id = {t["name"].lower(): t["id"] for t in all_tags}

    tag_ids = []
    missing = []
    for name in tag_names:
        tag_id = name_to_id.get(name.strip().lower())
        if tag_id:
            tag_ids.append(tag_id)
        else:
            missing.append(name)

    if missing:
        available = [t["name"] for t in all_tags]
        raise RuntimeError(
            f"Tags not found in Buffer: {', '.join(missing)}. "
            f"Available tags: {', '.join(available) if available else '(none)'}"
        )

    return tag_ids


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}"
        )


def schedule_to_buffer_bluesky(
    text: str, post_url: str, mode: str, due_at: str | None = None,
    tag_ids: list[str] | None = None,
) -> str:
    """Schedule a post to Buffer Bluesky channel.

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    composed_text = f"{text}\n\n{post_url}"
    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_BLUESKY_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_to_buffer_mastodon(
    text: str, post_url: str, image_url: str | None, mode: str,
    due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a post to Buffer Mastodon channel with featured image.

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    composed_text = f"{text}\n\n{post_url}"
    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_MASTODON_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if image_url:
        variables["input"]["assets"] = {"images": [{"url": image_url}]}
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_to_buffer_threads(
    text: str, post_title: str, post_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a threaded post to Buffer Threads channel.

    Thread post 1: social media text + featured image
    Thread post 2: post title + link

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
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
            "mode": mode,
            "metadata": {
                "threads": {
                    "type": "post",
                    "topic": "Japan",
                    "thread": [thread_post_1, thread_post_2],
                }
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids

    return _buffer_create_post(variables)


def schedule_to_buffer_instagram(
    channel_id: str, text: str, image_url: str, mode: str,
    due_at: str | None = None, first_comment: str | None = None,
    tag_ids: list[str] | None = None,
) -> str:
    """Schedule a post to a Buffer Instagram channel.

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    variables = {
        "input": {
            "text": text,
            "channelId": channel_id,
            "schedulingType": "automatic",
            "mode": mode,
            "assets": {"images": [{"url": image_url}]},
            "metadata": {
                "instagram": {
                    "type": "post",
                    "shouldShareToFeed": True,
                }
            },
        }
    }
    if first_comment:
        variables["input"]["metadata"]["instagram"]["firstComment"] = first_comment
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_to_buffer_x(
    text: str, post_title: str, post_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a threaded post to Buffer X channel.

    Thread post 1: social media text + featured image
    Thread post 2: post title + link

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
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
            "mode": mode,
            "metadata": {
                "twitter": {
                    "thread": [thread_post_1, thread_post_2],
                }
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids

    return _buffer_create_post(variables)


def schedule_to_all_platforms(
    text: str, title: str, url: str, img_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> dict[str, str]:
    """Schedule to all Buffer platforms, prompting to retry without image on failure.

    Returns a dict of platform name -> result string.
    """
    results = {}

    # Use cached Litterbox URL if this image already failed earlier in the run
    original_img_url = img_url
    if img_url and img_url in _litterbox_cache:
        cached = _litterbox_cache[img_url]
        print(f"    Using cached Litterbox URL for image: {cached}", file=sys.stderr)
        img_url = cached

    results["Bluesky"] = schedule_to_buffer_bluesky(text, url, mode, due_at, tag_ids)
    print(f"    Bluesky: {results['Bluesky']}", file=sys.stderr)

    results["Mastodon"] = schedule_to_buffer_mastodon(
        text, url, img_url, mode, due_at, tag_ids
    )
    print(f"    Mastodon: {results['Mastodon']}", file=sys.stderr)

    results["Threads"] = schedule_to_buffer_threads(
        text, title, url, img_url, mode, due_at, tag_ids
    )
    print(f"    Threads: {results['Threads']}", file=sys.stderr)

    results["X"] = schedule_to_buffer_x(
        text, title, url, img_url, mode, due_at, tag_ids
    )
    print(f"    X: {results['X']}", file=sys.stderr)

    # Check for image dimension failures; try Litterbox re-host, then offer no-image retry
    if img_url:
        img_failures = [
            k for k, v in results.items()
            if IMAGE_DIMENSION_ERROR in str(v)
        ]
        if img_failures:
            print(
                f"\n  Image dimension fetch failed on: {', '.join(img_failures)}",
                file=sys.stderr,
            )
            print("  Attempting to re-host image via Litterbox...", file=sys.stderr)
            litterbox_url = upload_to_litterbox(img_url)

            if litterbox_url:
                _litterbox_cache[original_img_url] = litterbox_url
                print(f"  Litterbox URL: {litterbox_url}", file=sys.stderr)
                for platform in img_failures:
                    if platform == "Mastodon":
                        results["Mastodon"] = schedule_to_buffer_mastodon(
                            text, url, litterbox_url, mode, due_at, tag_ids
                        )
                    elif platform == "Threads":
                        results["Threads"] = schedule_to_buffer_threads(
                            text, title, url, litterbox_url, mode, due_at, tag_ids
                        )
                    elif platform == "X":
                        results["X"] = schedule_to_buffer_x(
                            text, title, url, litterbox_url, mode, due_at, tag_ids
                        )
                    print(
                        f"    {platform} (Litterbox retry): {results[platform]}",
                        file=sys.stderr,
                    )

                # Check if any still failed after Litterbox retry
                img_failures = [
                    k for k, v in results.items()
                    if IMAGE_DIMENSION_ERROR in str(v)
                ]

            if img_failures:
                print(
                    f"\n  Image still failing on: {', '.join(img_failures)}",
                    file=sys.stderr,
                )
                answer = input("  Retry without image? (y/n): ").strip().lower()
                if answer == "y":
                    for platform in img_failures:
                        if platform == "Mastodon":
                            results["Mastodon"] = schedule_to_buffer_mastodon(
                                text, url, None, mode, due_at, tag_ids
                            )
                        elif platform == "Threads":
                            results["Threads"] = schedule_to_buffer_threads(
                                text, title, url, None, mode, due_at, tag_ids
                            )
                        elif platform == "X":
                            results["X"] = schedule_to_buffer_x(
                                text, title, url, None, mode, due_at, tag_ids
                            )
                        print(
                            f"    {platform} (no-image retry): {results[platform]}",
                            file=sys.stderr,
                        )

    return results
