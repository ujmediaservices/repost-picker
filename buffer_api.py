"""Buffer GraphQL API client for scheduling social media posts."""

import json
import os

import requests

BUFFER_API_URL = "https://api.buffer.com"

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

debug = False


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

    payload = {"query": query, "variables": variables}

    if debug:
        print("\n=== DEBUG: Buffer GraphQL Request ===", flush=True)
        print("Query:", query.strip(), flush=True)
        print("Variables:", json.dumps(variables, indent=2, ensure_ascii=False), flush=True)
        print("=====================================\n", flush=True)

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
        return f"ERROR: {data['errors']}"

    result = data.get("data", {}).get("createPost", {})
    if "post" in result:
        return result["post"]["id"]
    if "message" in result:
        return f"ERROR: {result['message']}"
    return f"ERROR: Unexpected response: {data}"


def _validate_mode(mode: str) -> None:
    if mode not in VALID_MODES:
        raise ValueError(
            f"Invalid mode '{mode}'. Must be one of: {', '.join(VALID_MODES)}"
        )


def schedule_to_buffer_bluesky(
    text: str, post_url: str, mode: str, due_at: str | None = None
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
    return _buffer_create_post(variables)


def schedule_to_buffer_mastodon(
    text: str, post_url: str, image_url: str | None, mode: str,
    due_at: str | None = None
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
    return _buffer_create_post(variables)


def schedule_to_buffer_threads(
    text: str, post_title: str, post_url: str, image_url: str | None,
    mode: str, due_at: str | None = None
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

    return _buffer_create_post(variables)


def schedule_to_buffer_x(
    text: str, post_title: str, post_url: str, image_url: str | None,
    mode: str, due_at: str | None = None
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

    return _buffer_create_post(variables)
