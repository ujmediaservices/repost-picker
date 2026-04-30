"""One-time fix: recreate X posts with missing URLs in second thread item."""

import os
import sys
import time

import requests

api_key = os.environ.get("BUFFER_API_KEY")
BUFFER_API_URL = "https://api.buffer.com"
X_CHANNEL_ID = "5f371d0a1c14ed2014066090"
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}


def gql(query, variables=None):
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    for attempt in range(8):
        resp = requests.post(BUFFER_API_URL, json=payload, headers=headers, timeout=30)
        if resp.status_code == 429:
            wait = min(2 ** attempt, 30)
            print(f"    Rate limited, waiting {wait}s...", file=sys.stderr)
            time.sleep(wait)
            continue
        resp.raise_for_status()
        return resp.json()
    raise RuntimeError("Max retries exceeded")


GET_POST_Q = """query G($id: PostId!) {
  post(input: {id: $id}) {
    id text dueAt shareMode
    tags { id }
    metadata {
      ... on TwitterPostMetadata {
        thread { text assets { ... on ImageAsset { source } } }
      }
    }
  }
}"""

CREATE_Q = """mutation C($input: CreatePostInput!) {
  createPost(input: $input) {
    ... on PostActionSuccess { post { id } }
    ... on RestProxyError { message }
    ... on InvalidInputError { message }
    ... on UnexpectedError { message }
  }
}"""

DELETE_Q = """mutation D($id: PostId!) {
  deletePost(input: {id: $id}) {
    ... on DeletePostSuccess { id }
    ... on VoidMutationError { message }
  }
}"""


def create_post(main_text, t0, t1, share_mode, due_at, tag_ids):
    cv = {
        "input": {
            "text": main_text,
            "channelId": X_CHANNEL_ID,
            "schedulingType": "automatic",
            "mode": share_mode,
            "metadata": {"twitter": {"thread": [t0, t1]}},
        }
    }
    # Only include dueAt for customScheduled mode
    if share_mode == "customScheduled" and due_at:
        cv["input"]["dueAt"] = due_at
    if tag_ids:
        cv["input"]["tagIds"] = tag_ids

    data = gql(CREATE_Q, cv)
    result = data.get("data", {}).get("createPost", {})
    if "post" in result:
        return result["post"]["id"]
    else:
        return f"FAILED: {result.get('message', result)}"


def fix_existing_post(post_id, url):
    """Delete an existing post and recreate with URL in thread[1]."""
    print(f"Processing {post_id}...", file=sys.stderr)

    # Get current post
    data = gql(GET_POST_Q, {"id": post_id})
    post = data["data"]["post"]
    main_text = post["text"]
    due_at = post.get("dueAt")
    share_mode = post["shareMode"]
    tag_ids = [t["id"] for t in post.get("tags", [])]
    thread = post["metadata"]["thread"]
    t0_text = thread[0]["text"]
    t0_assets = thread[0].get("assets", [])
    t1_title = thread[1]["text"]

    # Check if already fixed
    if "\n" in t1_title or "http" in t1_title:
        print(f"  Already fixed, skipping", file=sys.stderr)
        return "ALREADY FIXED"

    new_t1_text = f"{t1_title}\n\n{url}"

    t0 = {"text": t0_text}
    if t0_assets:
        img = t0_assets[0].get("source", "")
        if img:
            t0["assets"] = {"images": [{"url": img}]}
    t1 = {"text": new_t1_text}

    # Delete old
    gql(DELETE_Q, {"id": post_id})
    print(f"  Deleted old post", file=sys.stderr)
    time.sleep(0.5)

    # Create new
    new_id = create_post(main_text, t0, t1, share_mode, due_at, tag_ids)
    print(f"  Result: {new_id}", file=sys.stderr)
    time.sleep(1)
    return new_id


def recreate_deleted_post(main_text, t0_text, t0_img, t1_title, url, tag_ids):
    """Recreate a post that was already deleted (addToQueue posts)."""
    print(f"Recreating: {t1_title[:50]}...", file=sys.stderr)

    t0 = {"text": t0_text}
    if t0_img:
        t0["assets"] = {"images": [{"url": t0_img}]}
    t1 = {"text": f"{t1_title}\n\n{url}"}

    new_id = create_post(main_text, t0, t1, "addToQueue", None, tag_ids)
    print(f"  Result: {new_id}", file=sys.stderr)
    time.sleep(1)
    return new_id


def main():
    # Part 1: Recreate the 6 addToQueue posts that were deleted but not recreated
    print("=== Part 1: Recreating 6 deleted addToQueue posts ===", file=sys.stderr)

    deleted_posts = [
        {
            "main_text": 'In Japan, some women are told to avoid dating the "three C men": cameramen, creators, and \u2014 yes, really \u2014 men who make their own curry roux from scratch. The supposed red flag? Being too fussy.',
            "t0_img": "https://litter.catbox.moe/r16ois.png",
            "t1_title": "The Three Cs: The Men Japan Says You Should Avoid Dating",
            "url": "https://unseen-japan.com/3c-man-womens-dating-rules/",
            "tag_ids": [],
        },
        {
            "main_text": "Wanna discuss all things Japan with other UJ fans and our writers? Hop on our Discord. We promise we won\u2019t bite. (Unless you go on #biting. In which case, all bets are off.)",
            "t0_img": None,
            "t1_title": "Join the Unseen Japan Discord server",
            "url": "https://discord.gg/uPwBhuBCwQ",
            "tag_ids": ["69509c330fe36ac332053018"],
        },
        {
            "main_text": "Planning a trip to Japan? We\u2019ve spent years living here and running tours. From navigating the train system to debunking outdated travel advice, we\u2019ve compiled what actually works into one guide. Link below.",
            "t0_img": "https://litter.catbox.moe/aq5ghm.jpg",
            "t1_title": "Plan your Japan trip",
            "url": "https://unseen-japan.com/japan-trip-preparing/",
            "tag_ids": ["69509c1ddb3b3442dd004ddd"],
        },
        {
            "main_text": "Over 60% of Japan\u2019s population doesn\u2019t watch anime at all. And the country\u2019s most popular animated show isn\u2019t One Piece or Naruto \u2014 it\u2019s Sazae-san, a slice-of-life series about a typical Japanese family.",
            "t0_img": "https://litter.catbox.moe/oc1wgp.jpg",
            "t1_title": "Is Anime Popular in Japan? Survey says\u2026",
            "url": "https://unseen-japan.com/is-anime-popular-in-japan/",
            "tag_ids": [],
        },
        {
            "main_text": "Shinjuku Station sees over 3 million users every day. What most tourists don\u2019t realize: it\u2019s not one transit system but a conglomeration of multiple private and public companies sharing a single, sprawling station.",
            "t0_img": "https://litter.catbox.moe/xsgw8f.png",
            "t1_title": "[Insider] Shinjuku Station: How to Navigate It Like a Pro",
            "url": "https://unseen-japan.com/shinjuku-station-how-to-navigate/",
            "tag_ids": ["69509c4c21bac7107f05e2ab"],
        },
        {
            "main_text": "Japan\u2019s word for cutting class \u2014 saboru (\u30b5\u30dc\u308b) \u2014 comes from an unlikely source: the French word \"sabotage.\" It entered Japanese during the labor strikes of the Taish\u014d era.",
            "t0_img": "https://litter.catbox.moe/hrpyuw.png",
            "t1_title": "Saboru: The Strange Origins of \u201cCutting Class\u201d in Japanese",
            "url": "https://unseen-japan.com/saboru-the-strange-origins-of-cutting-class-in-japan/",
            "tag_ids": [],
        },
    ]

    for p in deleted_posts:
        recreate_deleted_post(
            p["main_text"], p["main_text"], p["t0_img"],
            p["t1_title"], p["url"], p["tag_ids"],
        )

    # Part 2: Fix the 6 remaining posts that still exist
    print("\n=== Part 2: Fixing 6 remaining existing posts ===", file=sys.stderr)

    remaining = [
        ("69d6f7fa16f821c97a2d7a0e", "https://unseen-japan.com/iran-war-protests-japan-escalating/"),
        ("69d5881d114d232c096ade39", "https://unseen-japan.com/anime-manga-genai-squeeze/"),
        ("69d4499f45bc467d3d914b78", "https://unseen-japan.com/suwa-onsen-town-nagano-prefecture/"),
        ("69d4499345bc467d3d914ab2", "https://unseen-japan.com/oshino-hakkai-coin-pollution/"),
        ("69d3113bbfce933f1f5845d5", "https://unseen-japan.com/best-onsen-winter-japan/"),
        ("69d20e08bfce933f1f5374c4", "https://unseen-japan.com/five-hidden-travel-spots-japan/"),
    ]

    for post_id, url in remaining:
        fix_existing_post(post_id, url)

    print("\nDone!", file=sys.stderr)


if __name__ == "__main__":
    main()
