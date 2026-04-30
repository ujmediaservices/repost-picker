"""Buffer GraphQL API client for scheduling social media posts."""

import json
import os
import sys
import time

import requests

BUFFER_API_URL = "https://api.buffer.com"

BUFFER_BLUESKY_CHANNEL_ID = "66997475602872be45e429ee"
BUFFER_THREADS_CHANNEL_ID = "667b1dcd7839e9e87976ad0c"
BUFFER_X_CHANNEL_ID = "5f371d0a1c14ed2014066090"
BUFFER_MASTODON_CHANNEL_ID = "6982c8e331b76c40ca2929b5"
BUFFER_FACEBOOK_CHANNEL_ID = "657dd26b1a33d19b1f29d0d0"

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


class ImageUploadError(Exception):
    """Raised when Buffer fails to fetch image dimensions for a post.

    Carries the IDs of platforms that succeeded earlier in the same
    schedule_to_all_platforms call, so the caller can roll them back.
    """

    def __init__(self, message: str, successful_post_ids: list[str]):
        super().__init__(message)
        self.successful_post_ids = successful_post_ids


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



def delete_buffer_post(post_id: str) -> bool:
    """Delete a Buffer post by ID. Returns True on success.

    Logs failures to stderr but does not raise — callers use this during
    rollback and should not double-fail.
    """
    api_key = _buffer_api_key()
    if not api_key:
        print(f"  Cannot delete {post_id}: BUFFER_API_KEY not set", file=sys.stderr)
        return False

    query = """
    mutation DeletePost($input: DeletePostInput!) {
      deletePost(input: $input) {
        ... on DeletePostSuccess { id }
        ... on VoidMutationError { message }
      }
    }
    """
    payload = {"query": query, "variables": {"input": {"id": post_id}}}

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
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            print(f"  Failed to delete post {post_id}: {data['errors']}", file=sys.stderr)
            return False
        result = data.get("data", {}).get("deletePost", {})
        if "id" in result:
            return True
        if "message" in result:
            print(f"  Failed to delete post {post_id}: {result['message']}", file=sys.stderr)
        return False
    except Exception as exc:
        print(f"  Failed to delete post {post_id}: {exc}", file=sys.stderr)
        return False


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}"
        )


def schedule_to_buffer_bluesky(
    text: str, post_url: str, mode: str, due_at: str | None = None,
    tag_ids: list[str] | None = None,
    link_title: str | None = None,
    link_description: str | None = None,
    link_thumbnail_url: str | None = None,
) -> str:
    """Schedule a post to Buffer Bluesky channel with a link attachment card.

    The post URL is attached as a link card rather than appended inline to the
    text.  Provide link_title, link_description, and link_thumbnail_url to
    populate the card explicitly (recommended).
    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    link_asset: dict = {"url": post_url}
    if link_title:
        link_asset["title"] = link_title
    if link_description:
        link_asset["description"] = link_description
    if link_thumbnail_url:
        link_asset["thumbnailUrl"] = link_thumbnail_url
    variables = {
        "input": {
            "text": text,
            "channelId": BUFFER_BLUESKY_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "assets": {
                "link": link_asset,
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_to_buffer_facebook(
    text: str, post_url: str, mode: str, image_url: str | None = None,
    due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a post to Buffer Facebook channel with the URL inline.

    The post URL is appended inline to the text (no link attachment card),
    matching the Mastodon style. If ``image_url`` is provided, the image is
    attached as a regular image asset.

    Buffer's Facebook link-card path proved unreliable for our channel; this
    bare-link form is what we ship.
    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    composed_text = f"{text}\n\n{post_url}"
    variables = {
        "input": {
            "text": composed_text,
            "channelId": BUFFER_FACEBOOK_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "metadata": {
                "facebook": {"type": "post"},
            },
        }
    }
    if image_url:
        variables["input"]["assets"] = {"images": [{"url": image_url}]}
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
    thread_2_text: str | None = None,
) -> str:
    """Schedule a threaded post to Buffer Threads channel.

    Thread post 1: social media text + featured image
    Thread post 2: thread_2_text (if provided) or post_title, with link attached

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    # Thread post 1: social media text + featured image
    thread_post_1 = {"text": text}
    if image_url:
        thread_post_1["assets"] = {"images": [{"url": image_url}]}

    # Thread post 2: hook text (or fallback to post_title) + link
    thread_post_2 = {
        "text": thread_2_text if thread_2_text else post_title,
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
    thread_2_text: str | None = None,
) -> str:
    """Schedule a threaded post to Buffer X channel.

    Thread post 1: social media text + featured image
    Thread post 2: thread_2_text (if provided) or post_title, followed by URL inline

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    # Thread post 1: social media text + featured image
    thread_post_1 = {"text": text}
    if image_url:
        thread_post_1["assets"] = {"images": [{"url": image_url}]}

    # Thread post 2: hook text (or fallback to post_title) + URL inline
    lead_text = thread_2_text if thread_2_text else post_title
    thread_post_2 = {
        "text": f"{lead_text}\n\n{post_url}",
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


def _build_thread_from_posts(
    posts: list[str], source_url: str, image_url: str | None,
    link_attachment: bool,
) -> list[dict]:
    """Compose a thread payload from body posts + a final Source post.

    Body posts are passed through verbatim. A final post is always appended
    with text "Source:\\n{source_url}". When ``link_attachment`` is True (for
    Bluesky and Threads, which render link cards nicely), the source URL is
    also attached as a link asset on that final post.

    The featured ``image_url`` (if any) is attached to the FIRST body post.
    """
    if not posts:
        raise ValueError("posts must contain at least one body post")

    thread: list[dict] = []
    for i, text in enumerate(posts):
        post: dict = {"text": text}
        if i == 0 and image_url:
            post["assets"] = {"images": [{"url": image_url}]}
        thread.append(post)

    source_post: dict = {"text": f"Source:\n{source_url}"}
    if link_attachment:
        source_post["assets"] = {"link": {"url": source_url}}
    thread.append(source_post)

    return thread


def schedule_thread_to_buffer_bluesky(
    posts: list[str], source_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a multi-post thread to Buffer Bluesky channel.

    ``posts`` is the list of body-post texts; a final "Source:\\n{url}" post
    with link-card attachment is appended automatically.

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    thread = _build_thread_from_posts(posts, source_url, image_url, link_attachment=True)
    variables = {
        "input": {
            "text": posts[0],
            "channelId": BUFFER_BLUESKY_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "metadata": {
                "bluesky": {"thread": thread},
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_thread_to_buffer_x(
    posts: list[str], source_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a multi-post thread to Buffer X (Twitter) channel.

    ``posts`` is the list of body-post texts; a final "Source:\\n{url}" post
    is appended automatically (no link card — X expands the URL inline).

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    thread = _build_thread_from_posts(posts, source_url, image_url, link_attachment=False)
    variables = {
        "input": {
            "text": posts[0],
            "channelId": BUFFER_X_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "metadata": {
                "twitter": {"thread": thread},
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_thread_to_buffer_threads(
    posts: list[str], source_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a multi-post thread to Buffer Threads channel.

    ``posts`` is the list of body-post texts; a final "Source:\\n{url}" post
    with link-card attachment is appended automatically. The Threads ``topic``
    is fixed to "Japan" (UJ standard).

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    thread = _build_thread_from_posts(posts, source_url, image_url, link_attachment=True)
    variables = {
        "input": {
            "text": posts[0],
            "channelId": BUFFER_THREADS_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "metadata": {
                "threads": {
                    "type": "post",
                    "topic": "Japan",
                    "thread": thread,
                }
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_thread_to_buffer_mastodon(
    posts: list[str], source_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> str:
    """Schedule a multi-post thread to Buffer Mastodon channel.

    ``posts`` is the list of body-post texts; a final "Source:\\n{url}" post
    is appended automatically. Mastodon expands URLs inline (no link card).

    For single-post Mastodon scenarios, prefer ``schedule_to_buffer_mastodon``.

    Returns the Buffer post ID on success, or an error message.
    """
    _validate_mode(mode)
    thread = _build_thread_from_posts(posts, source_url, image_url, link_attachment=False)
    variables = {
        "input": {
            "text": posts[0],
            "channelId": BUFFER_MASTODON_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": mode,
            "metadata": {
                "mastodon": {"thread": thread},
            },
        }
    }
    if due_at:
        variables["input"]["dueAt"] = due_at
    if tag_ids:
        variables["input"]["tagIds"] = tag_ids
    return _buffer_create_post(variables)


def schedule_thread_to_all_social(
    posts_by_platform: dict[str, list[str]],
    source_url: str, image_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> dict[str, str]:
    """Stage a per-platform-tailored thread to Bluesky, Mastodon, Threads, X.

    ``posts_by_platform`` maps platform name (lowercase: ``bluesky``,
    ``mastodon``, ``threads``, ``x``) to the list of body-post texts for
    that platform. A "Source:\\n{url}" post is appended automatically on
    each platform that's a thread (all four when called via this helper).

    Returns a dict of platform → result string. Unlike
    ``schedule_to_all_platforms``, this does NOT raise on image errors —
    each platform's failure is reported in the result dict and processing
    continues.
    """
    dispatchers = {
        "bluesky":  schedule_thread_to_buffer_bluesky,
        "mastodon": schedule_thread_to_buffer_mastodon,
        "threads":  schedule_thread_to_buffer_threads,
        "x":        schedule_thread_to_buffer_x,
    }
    results: dict[str, str] = {}
    for platform, fn in dispatchers.items():
        body = posts_by_platform.get(platform)
        if not body:
            results[platform] = "SKIPPED: no posts provided"
            continue
        try:
            results[platform] = fn(body, source_url, image_url, mode, due_at, tag_ids)
        except Exception as exc:
            results[platform] = f"ERROR: {exc}"
        print(f"    {platform}: {results[platform]}", file=sys.stderr)
    return results


def schedule_to_all_platforms(
    text: str, title: str, url: str, img_url: str | None,
    mode: str, due_at: str | None = None, tag_ids: list[str] | None = None,
) -> dict[str, str]:
    """Schedule to all Buffer platforms (Bluesky, Mastodon, Threads, X).

    Returns a dict of platform name -> result string. On image dimension
    failure, raises ImageUploadError carrying the IDs of platforms that
    succeeded earlier in this call so the caller can roll them back.
    Other (non-image) errors are returned in the dict as before.
    """
    results: dict[str, str] = {}
    successful_ids: list[str] = []

    def _record(platform: str, result: str) -> None:
        results[platform] = result
        print(f"    {platform}: {result}", file=sys.stderr)
        if IMAGE_DIMENSION_ERROR in str(result):
            raise ImageUploadError(
                f"{platform} image upload failed: {result}",
                successful_ids,
            )
        if not str(result).startswith("ERROR"):
            successful_ids.append(result)

    _record("Bluesky", schedule_to_buffer_bluesky(text, url, mode, due_at, tag_ids))
    _record("Mastodon", schedule_to_buffer_mastodon(text, url, img_url, mode, due_at, tag_ids))
    _record("Threads", schedule_to_buffer_threads(text, title, url, img_url, mode, due_at, tag_ids))
    _record("X", schedule_to_buffer_x(text, title, url, img_url, mode, due_at, tag_ids))

    return results
