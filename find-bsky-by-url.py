"""Search sent Bluesky posts in Buffer for specific text."""

import argparse
import os
import sys
import time

import requests

from buffer_api import BUFFER_API_URL, BUFFER_BLUESKY_CHANNEL_ID

POSTS_PER_PAGE = 50
MAX_RETRIES = 5
INITIAL_BACKOFF = 1  # seconds


def buffer_request(api_key: str, query: str, variables: dict | None = None) -> dict:
    """Send a request to the Buffer API with exponential backoff on 429."""
    payload = {"query": query}
    if variables:
        payload["variables"] = variables

    for attempt in range(MAX_RETRIES):
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
            print(f"  Rate limited. Retrying in {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()

    print("ERROR: Max retries exceeded due to rate limiting.", file=sys.stderr)
    sys.exit(1)


def get_api_key() -> str:
    api_key = os.environ.get("BUFFER_API_KEY")
    if not api_key:
        print("ERROR: BUFFER_API_KEY environment variable not set", file=sys.stderr)
        sys.exit(1)
    return api_key


def get_organization_id(api_key: str) -> str:
    query = """
    query {
      account {
        organizations {
          id
          name
        }
      }
    }
    """
    data = buffer_request(api_key, query)
    orgs = data["data"]["account"]["organizations"]
    if not orgs:
        print("ERROR: No organizations found.", file=sys.stderr)
        sys.exit(1)
    return orgs[0]["id"]


def search_sent_posts(api_key: str, org_id: str, search_text: str) -> list[dict]:
    query = """
    query Posts($input: PostsInput!, $first: Int, $after: String) {
      posts(input: $input, first: $first, after: $after) {
        edges {
          node {
            id
            text
            status
            sentAt
            dueAt
            externalLink
          }
        }
        pageInfo {
          hasNextPage
          endCursor
        }
      }
    }
    """

    matches = []
    cursor = None

    while True:
        variables = {
            "input": {
                "organizationId": org_id,
                "filter": {
                    "channelIds": [BUFFER_BLUESKY_CHANNEL_ID],
                    "status": ["sent"],
                },
            },
            "first": POSTS_PER_PAGE,
        }
        if cursor:
            variables["after"] = cursor

        data = buffer_request(api_key, query, variables)

        if "errors" in data:
            print(f"ERROR: {data['errors']}", file=sys.stderr)
            sys.exit(1)

        posts_data = data["data"]["posts"]
        edges = posts_data["edges"]

        for edge in edges:
            post = edge["node"]
            if search_text.lower() in (post.get("text") or "").lower():
                matches.append(post)

        page_info = posts_data["pageInfo"]
        if page_info["hasNextPage"]:
            cursor = page_info["endCursor"]
        else:
            break

    return matches


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Search sent Bluesky posts in Buffer for specific text."
    )
    parser.add_argument("--text", required=True, help="Text to search for in sent posts (case-insensitive)")
    args = parser.parse_args()

    api_key = get_api_key()

    print("Fetching organization ID...", file=sys.stderr)
    org_id = get_organization_id(api_key)

    print(f"Searching sent Bluesky posts for: {args.text}", file=sys.stderr)
    matches = search_sent_posts(api_key, org_id, args.text)

    if not matches:
        print("No matching posts found.")
        return

    print(f"\n{len(matches)} matching post(s) found:\n")
    for post in matches:
        print(f"  ID: {post['id']}")
        print(f"  Sent: {post.get('sentAt', 'N/A')}")
        print(f"  URL: {post.get('externalLink', 'N/A')}")
        print(f"  Text: {post.get('text', '')[:200]}")
        print()


if __name__ == "__main__":
    main()
