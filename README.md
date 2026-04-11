# repost-picker

A suite of tools for generating AI-powered social media text and scheduling posts to multiple channels via the Buffer API.

## Scripts

### repost_picker.py

Selects the oldest blog posts from a JSON data file by type, generates social media text using Claude, and schedules them to multiple channels via Buffer.

### generate-drip-posts.py

Fetches the most recent posts from WordPress and generates three scheduled "drip" posts per article: two interesting facts (tomorrow and one week out) and an ICYMI summary (one month out).

### find-bsky-by-url.py

Searches sent Bluesky posts in Buffer for specific text.

## Requirements

- Python 3.12+
- `anthropic`, `requests` (install via `pip install anthropic requests`)

## Environment variables

| Variable | Description |
|---|---|
| `ANTHROPIC_API_KEY` | API key for Claude |
| `BUFFER_API_KEY` | API key for Buffer |
| `WORDPRESS_URL` | WordPress site URL (e.g., `https://unseen-japan.com`) |
| `WORDPRESS_USERNAME` | WordPress username |
| `WORDPRESS_PASSWORD` | WordPress application password |

## repost_picker.py

Runs in two phases:

1. **Generate** -- Selects posts from the data file according to a config, fetches content and images from WordPress, and uses Claude to generate social media text. Results are saved to a temp JSON file for review.
2. **Schedule** -- After the user edits and confirms, schedules each post to all channels via Buffer, then updates the data file with new dates.

### Usage

```
python repost_picker.py
python repost_picker.py --config <config.json> --repost-file <data.json>
python repost_picker.py --examples ./examples
python repost_picker.py --drafts
python repost_picker.py --debug
```

| Argument | Default | Description |
|---|---|---|
| `--config` | `G:\My Drive\...\repost-picker-config\config.json` | Path to the config JSON file |
| `--repost-file` | `G:\My Drive\...\repost-picker-config\uj-repost-content.json` | Path to the repost data JSON file |
| `--examples` | `G:\My Drive\...\repost-picker-config\one-shot-examples` | Path to a directory of example social media posts for style guidance |
| `--drafts` | off | Save posts as drafts in Buffer instead of scheduling them |
| `--debug` | off | Dump all Buffer GraphQL queries and variables to stdout |

### Config file

The config file defines a repost roadmap. See `sample-config.json` for an example.

```json
{
    "defaultMode": "addToQueue",
    "startDate": "05/01/2026",
    "reposts": [
        {
            "post_type": ["Essay"],
            "count": 2
        },
        {
            "post_type": ["Travel", "Japanese", "Food"],
            "count": 3
        },
        {
            "post_type": ["ToursPromo"],
            "count": 1,
            "mode": "customScheduled",
            "due_at": "05/03/2026 09:00AM",
            "tags": ["69509c1ddb3b3442dd004ddd"]
        }
    ]
}
```

| Field | Description |
|---|---|
| `defaultMode` | Buffer scheduling mode applied to all entries unless overridden. Valid values: `addToQueue`, `shareNow`, `shareNext`, `customScheduled`, `recommendedTime`. Defaults to `addToQueue` if omitted. |
| `startDate` | Reference date in MM/DD/YYYY format. Posts are scheduled starting the day after this date. |
| `reposts` | Array of repost entries. Posts are scheduled in array order. |
| `reposts[].post_type` | Array of post type strings to match against the data file's `type` field. |
| `reposts[].count` | Number of oldest posts to pick for the matching types. Defaults to 1 if omitted. |
| `reposts[].mode` | Optional per-entry override for the scheduling mode. Falls back to `defaultMode` if omitted. |
| `reposts[].due_at` | Required when mode is `customScheduled`. Format: `MM/DD/YYYY HH:MMAM/PM` (e.g., `05/03/2026 09:00AM`). |
| `reposts[].tags` | Optional array of pre-created Buffer tag IDs to apply to all posts in this entry. |

If `count > 1` with `customScheduled` mode, the script warns that multiple posts will be scheduled at the same time and asks for confirmation.

### Data file

The data file is a JSON array of post entries. After scheduling, it is re-sorted by type (ascending) then date (descending), with blank lines separating each type section.

```json
{
    "name": "Post Title",
    "type": "Travel",
    "url": "https://example.com/post-slug/",
    "last_posted_social": "04/01/2026",
    "last_posted_ig": "",
    "notes": "",
    "static_text": ""
}
```

| Field | Description |
|---|---|
| `name` | Post title, also used in threaded posts on Threads and X. |
| `type` | Post type string matched against config `post_type` arrays. |
| `url` | Public URL of the post. |
| `last_posted_social` | Date the post was last shared (MM/DD/YYYY). Used for selecting the oldest posts and updated after scheduling. |
| `last_posted_ig` | Date last posted to Instagram (not used by this script). |
| `notes` | Free-text notes (not used by this script). |
| `static_text` | Optional. If present and non-empty, used as the social media post instead of generating via Claude. |

## generate-drip-posts.py

Fetches the N most recent published posts from WordPress and generates three drip posts per article, each scheduled at a random AM time (7-11 AM ET):

| When | Type | Content |
|---|---|---|
| Tomorrow | Interesting fact | An interesting excerpt from the post |
| +1 week | Interesting fact | A different interesting excerpt |
| +1 month | ICYMI | "ICYMI:" summary of the article |

All drip posts use Buffer's `customScheduled` mode.

### Usage

```
python generate-drip-posts.py --num-posts 3
python generate-drip-posts.py --num-posts 3 --examples ./examples
python generate-drip-posts.py --num-posts 3 --drafts
python generate-drip-posts.py --num-posts 3 --tags "tagid1,tagid2"
python generate-drip-posts.py --num-posts 3 --debug
```

| Argument | Required | Description |
|---|---|---|
| `--num-posts` | Yes | Number of most recent WordPress posts to process |
| `--examples` | No | Path to a directory of example social media posts for style guidance |
| `--drafts` | No | Save posts as drafts in Buffer instead of putting them directly into the queue |
| `--tags` | No | Comma-delimited list of pre-created Buffer tag IDs to apply to all posts |
| `--debug` | No | Dump all Buffer GraphQL queries and variables to stdout |

## Style examples

Both `repost_picker.py` and `generate-drip-posts.py` support the `--examples` argument. Point it to a directory containing `.txt`, `.json`, or `.md` files with edited examples of your social media posts. These are included in the Claude prompt as style guidance when generating text.

## find-bsky-by-url.py

Searches sent Bluesky posts in Buffer for specific text (case-insensitive). Returns the Buffer post ID, Bluesky URL, send date, and post text. Includes exponential backoff for Buffer API rate limiting.

```
python find-bsky-by-url.py --text "search term"
```

## Tagging

`repost_picker.py` supports per-entry tags in the config file via the `tags` field (an array of Buffer tag IDs). `generate-drip-posts.py` supports the `--tags` CLI argument for applying tags to all drip posts. Tags must be pre-created in your Buffer organization; the scripts do not validate IDs before scheduling.

## Image error handling

If Buffer returns a "Failed to fetch image dimensions" error for any platform, the script attempts to re-host the image on [Litterbox](https://litterbox.catbox.moe) (a temporary file host with 72-hour expiry) and retries the failed platforms with the new URL. If the Litterbox upload fails or the retried platforms still return the same error, the script falls back to prompting whether to retry without an image. Platforms that succeeded on the first attempt are not affected.

## Social media channels

Each post is scheduled to four Buffer channels:

- **Bluesky** -- Single post with social text and URL. Text is constrained to fit within Bluesky's 300-character limit (including the URL).
- **Mastodon** -- Single post with social text, URL, and featured image.
- **Threads** -- Threaded post. First post: social text with featured image. Second post: post title with link.
- **X** -- Threaded post. First post: social text with featured image. Second post: post title with link.

## Image selection

For each post, the best available image is selected in order:

1. **Featured image** -- fetched via the WordPress media API.
2. **First content image** -- the first `<img>` found in the post/page HTML.

This works with both posts and pages, including those without a featured image.

## WordPress content lookup

Content is looked up by slug in the WordPress REST API, checking posts first then pages.

## Review workflow

Both scripts save generated social media text to a temporary JSON file in the system temp directory and print its path. The user can edit `social_text` fields in any editor, then press Enter to continue scheduling.

## Project structure

| File | Description |
|---|---|
| `repost_picker.py` | Repost scheduling script |
| `generate-drip-posts.py` | Drip post generation and scheduling script |
| `social_text.py` | Shared library: WordPress API, Claude text generation, examples loading, image resolution |
| `buffer_api.py` | Shared library: Buffer GraphQL API client with retry/backoff |
| `find-bsky-by-url.py` | Search sent Bluesky posts by text |
| `sample-config.json` | Example config file for repost_picker.py |
