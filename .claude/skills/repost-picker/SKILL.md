---
name: repost-picker
description: Pick the oldest posts by type from the repost JSON according to a config roadmap, generate social media text via Claude, schedule to Buffer, and assign new future dates
command: repost
arguments: "--config CONFIG --repost-file DATA --examples DIR --drafts"
---

# Repost Picker

Pick the oldest posts by type from the social-media repost JSON file according to a config roadmap, fetch each post's content from WordPress, generate social media text, schedule each post to Buffer, and update the JSON with new dates.

This is a **hybrid skill**: Python scripts handle post selection, WordPress fetching, Buffer scheduling, and data file updates. Claude generates the social media text directly (no Anthropic API key needed).

## Inputs

All arguments have defaults and are optional:

| Argument | Default | Description |
|---|---|---|
| `--config` | `G:\My Drive\Unseen Japan\Code\repost-picker-config\config.json` | Path to the config JSON file |
| `--repost-file` | `G:\My Drive\Unseen Japan\Code\repost-picker-config\uj-repost-content.json` | Path to the repost data JSON file |
| `--examples` | `G:\My Drive\Unseen Japan\Code\repost-picker-config\one-shot-examples` | Path to a directory of example social media posts for style guidance |
| `--drafts` | off | Save posts as Buffer drafts instead of scheduling |

Example: `/repost` (uses all defaults)
Example with drafts: `/repost --drafts`

## Steps

### Phase 1: Select Posts and Fetch Content (Python)

Run the selection script from the project directory:

```
cd "G:\My Drive\Unseen Japan\Code\repost-picker"
python repost_select.py [--config <path>] [--repost-file <path>] [--examples <path>]
```

Pass through any `--config`, `--repost-file`, or `--examples` arguments the user provided. The script outputs a review JSON file path to stdout.

The review JSON contains:
- `posts[]`: array of selected posts, each with `title`, `url`, `featured_image`, `content` (article text), `social_text` (pre-filled if static), `is_static`, `offset`, `row_index`, `mode`, `due_at`, `tags`
- `examples_text`: loaded style examples
- `config_path` / `data_path`: file paths for Phase 2

### Phase 2: Generate Social Media Text (Claude)

Read the review JSON. For each post where `is_static` is false (i.e., `social_text` is empty):

1. **Calculate the character limit**:
   - Bluesky hard limit: 300 characters total (text + 2 newlines + URL)
   - `max_text_length = 300 - 2 - len(post.url)`

2. **Generate social media text** using the post's `content` field, `title`, the `examples_text` from the review JSON, and these editorial guidelines:

   > Select an interesting fact or portion of this article from Unseen Japan for use as a social media post. Do not rephrase in your own words. Only change slightly to fit character count or to add missing post context (e.g., someone's full name), keeping original tone. Do not be overly promotional, cute, or use marketing jargon or emojis. Be factual, as we are a serious news and media organization. Reword phrases such as "recent" and "new" to avoid time references — e.g., instead of "a recent survey" or "a new survey," say "one survey."

   Think through several alternatives internally. Pick the single best one. Output only that text.

   **The text must be {max_text_length} characters or fewer.** Verify the count before finalizing.

3. **Write the generated text** into the post's `social_text` field.

### Phase 3: Review

Present ALL posts to the user in a numbered list showing:
- Post title
- Post URL
- Featured image URL (if any)
- Social media text (generated or static)
- Character count: `len(social_text) + 2 + len(url)` / 300
- Scheduling mode (and due_at if customScheduled)
- Tags (if any)

**Ask the user to review and approve.** Let them request edits to any post's text. Do NOT proceed to Phase 4 until the user explicitly confirms.

After approval, write the updated posts back to the review JSON file (update only the `social_text` fields in `posts[]`).

### Phase 4: Schedule to Buffer and Update Data (Python)

Run the scheduling script:

```
cd "G:\My Drive\Unseen Japan\Code\repost-picker"
python repost_schedule.py --review-file <review_json_path> [--drafts] [--debug]
```

Pass `--drafts` if the user specified it. The script:
- Schedules each post to Bluesky, Mastodon, Threads, and X via Buffer
- Updates `last_posted_social` dates in the data file (startDate + offset days)
- Clears `static_text` for static posts after scheduling
- Re-sorts the data file by type (ascending) then date (descending)
- Writes the data file with blank lines between type groups

### Phase 5: Report Results

Read the script's stdout output and present a summary to the user:
- Each post's title, URL, and Buffer status per platform
- Confirm the data file has been updated

## Config File Format

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
| `defaultMode` | Buffer scheduling mode for all entries unless overridden. Valid: `addToQueue`, `shareNow`, `shareNext`, `customScheduled`, `recommendedTime`. |
| `startDate` | Reference date (MM/DD/YYYY). New `last_posted_social` dates start the day after this. |
| `reposts[].post_type` | Array of type strings matched against the data file's `type` field. |
| `reposts[].count` | Number of oldest posts to pick. Defaults to 1. |
| `reposts[].mode` | Optional per-entry scheduling mode override. |
| `reposts[].due_at` | Required when mode is `customScheduled`. Format: `MM/DD/YYYY HH:MMAM/PM`. |
| `reposts[].tags` | Optional array of pre-created Buffer tag IDs. |

## Data File Format

| Field | Description |
|---|---|
| `name` | Post title |
| `type` | Post type string matched against config |
| `url` | Public URL of the post |
| `last_posted_social` | Date last shared (MM/DD/YYYY) |
| `static_text` | If non-empty, used as social text instead of generating. Cleared after scheduling. |
