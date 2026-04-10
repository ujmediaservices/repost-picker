---
name: repost-picker
description: Pick the oldest posts by type from the repost JSON according to a config roadmap, generate social media text via Claude, schedule to Buffer via MCP, and assign new future dates
command: repost
arguments: "--config <config.json> --repost-file <data.json> [--drafts]"
---

# Repost Picker

Pick the oldest posts by type from the social-media repost JSON file according to a config roadmap, fetch each post's content from WordPress via MCP, generate social media text, schedule each post to Buffer via MCP, and update the JSON with new dates.

## Inputs

The user invokes this skill with named arguments:

| Argument | Required | Description |
|---|---|---|
| `--config` | Yes | Path to the config JSON file defining the repost roadmap |
| `--repost-file` | Yes | Path to the repost data JSON file |
| `--drafts` | No | Save posts as Buffer drafts instead of scheduling |

Example: `/repost --config sample-config.json --repost-file uj-repost-content.json`
Example with drafts: `/repost --config sample-config.json --repost-file uj-repost-content.json --drafts`

If the user omits required arguments, ask for them. If paths are relative, resolve them relative to the project root: `G:/マイドライブ/Unseen Japan/Code/repost-picker/`.

## Config File Format

The config defines a repost roadmap. Each entry in `reposts` specifies which post types to select and how many:

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
            "due_at": "05/03/2026 09:00AM"
        }
    ]
}
```

| Field | Description |
|---|---|
| `defaultMode` | Buffer scheduling mode for all entries unless overridden. Valid: `addToQueue`, `shareNow`, `shareNext`, `customScheduled`, `recommendedTime`. Defaults to `addToQueue`. |
| `startDate` | Reference date (MM/DD/YYYY). New `last_posted_social` dates start the day after this. |
| `reposts` | Array of repost entries, processed in order. |
| `reposts[].post_type` | Array of type strings matched against the data file's `type` field. |
| `reposts[].count` | Number of oldest posts to pick. Defaults to 1. |
| `reposts[].mode` | Optional per-entry scheduling mode override. |
| `reposts[].due_at` | Required when mode is `customScheduled`. Format: `MM/DD/YYYY HH:MMAM/PM`. |

If `count > 1` with `customScheduled` mode, warn the user that multiple posts will be scheduled at the same time and ask for confirmation before proceeding.

## Data File Format

JSON array of post objects:

| Field | Description |
|---|---|
| `name` | Post title; also used in threaded posts on Threads and X. |
| `type` | Post type string matched against config `post_type` arrays. |
| `url` | Public URL of the post. |
| `last_posted_social` | Date last shared (MM/DD/YYYY). Used for oldest-first selection; updated after scheduling. |
| `last_posted_ig` | Date last posted to Instagram (not used by this skill). |
| `notes` | Free-text notes (not used by this skill). |
| `static_text` | If non-empty, used as social text instead of generating via Claude. |

## Style Examples

Style examples directory: `G:/マイドライブ/Unseen Japan/Code/repost-picker/examples/`

Read all `.txt`, `.json`, and `.md` files from this directory (use Glob + Read). Include them in the generation prompt as style guidance.

## Buffer Channel IDs

| Platform | Channel ID |
|----------|-----------|
| Bluesky  | `66997475602872be45e429ee` |
| Threads  | `667b1dcd7839e9e87976ad0c` |
| X        | `5f371d0a1c14ed2014066090` |
| Mastodon | `6982c8e331b76c40ca2929b5` |

## Character Limits

- Bluesky hard limit: **300 characters** total (text + 2 newlines + URL)
- Calculate `max_text_length = 300 - 2 - len(post_url)` for each post
- If generated text exceeds the limit, truncate and append "..."

## Steps

### Phase 1: Select Posts and Generate Social Text

1. **Read and validate the config file**. Check that `startDate` is present and valid (MM/DD/YYYY), and that `reposts` is a non-empty array.

2. **Read the repost data file**. Parse the JSON array.

3. **Select posts according to the config roadmap**:
   - Process each `reposts` entry in array order.
   - For each entry, find rows whose `type` matches any string in `post_type`, excluding already-selected rows.
   - Sort candidates by `last_posted_social` ascending (oldest first).
   - Take the top `count` entries.
   - Track the scheduling mode and `due_at` for each selected post.
   - Assign sequential day offsets starting at 1 (first selected post across all entries gets offset 1, second gets 2, etc.).

4. **Load style examples** from the examples directory.

5. **For each selected post**, in selection order:

   a. **Extract the slug** from the URL (last path segment, e.g., `best-onsen-winter-japan` from `https://unseen-japan.com/best-onsen-winter-japan/`).

   b. **Fetch WordPress content** using MCP:
      - Try `wp_posts_search` with `search` set to the slug. If no results, try `wp_pages_search`.
      - Use `wp_get_post` (or `wp_get_page`) with `context: "view"` to get full content.
      - Strip HTML tags from `content.rendered` to get plain text for the prompt.

   c. **Resolve the featured image**:
      - If the post has a `featured_media` ID, use `wp_get_media` to get the `source_url`.
      - If no featured image, scan `content.rendered` HTML for the first `<img src="...">` URL.
      - Store the image URL for Buffer posts.

   d. **Generate social media text**:
      - If `static_text` is non-empty, use it directly.
      - Otherwise, generate text following this prompt:

      > Select an interesting fact or portion of this post from Unseen Japan for use as a social media post. Do not rephrase in your own words. Only change slightly to fit character count or to add missing post context (e.g., someone's full name), keeping original tone. Do not be overly promotional, cute, or use marketing jargon or emojis. Be factual, as we are a serious news and media organization. Reword phrases such as "recent" and "new" to avoid time references - e.g., instead of "a recent survey" or "a new survey," say "one survey." Do not repeat facts except in the ICYMI. If there are style examples, match that tone and approach.
      >
      > IMPORTANT: The post text will be followed by a URL ({url_length} chars) and two line breaks. The TOTAL including text + two line breaks + URL must not exceed 300 characters. Keep text to {max_text_length} characters or fewer.

      Include the post title, plain text content, and style examples in context. Generate several alternatives, then pick the single best one.

6. **Present all generated posts to the user** in a numbered list showing:
   - Post title
   - Post URL
   - Featured image URL (if any)
   - Generated social media text
   - Total character count (text + 2 newlines + URL)
   - Scheduling mode (and due_at if customScheduled)

7. **Ask the user to review and approve**. Let them request edits to any post's text. Do NOT proceed to Phase 2 until the user confirms.

### Phase 2: Schedule to Buffer and Update Data File

Once the user approves:

8. **For each approved post**, schedule to all four Buffer channels using `mcp__buffer__create_post`. Use the mode from the config (entry-level override or `defaultMode`). Include `saveToDraft: true` only if `--drafts` was specified. Convert `due_at` from `MM/DD/YYYY HH:MMAM/PM` to ISO 8601 format (`YYYY-MM-DDTHH:MM:SS.000Z`) when mode is `customScheduled`.

   **Bluesky** — simple text post:
   ```
   channelId: "66997475602872be45e429ee"
   text: "{social_text}\n\n{post_url}"
   schedulingType: "automatic"
   mode: (from config)
   dueAt: (ISO 8601, only if customScheduled)
   saveToDraft: (only if --drafts)
   ```

   **Mastodon** — text post with featured image:
   ```
   channelId: "6982c8e331b76c40ca2929b5"
   text: "{social_text}\n\n{post_url}"
   schedulingType: "automatic"
   mode: (from config)
   dueAt: (if customScheduled)
   saveToDraft: (if --drafts)
   assets: { images: [{ url: "{image_url}", metadata: { altText: "{post_title}" } }] }
   ```
   Omit `assets` if no image.

   **Threads** — threaded post (text+image, then title+link):
   ```
   channelId: "667b1dcd7839e9e87976ad0c"
   text: "{social_text}"
   schedulingType: "automatic"
   mode: (from config)
   dueAt: (if customScheduled)
   saveToDraft: (if --drafts)
   metadata: {
     threads: {
       type: "post",
       topic: "Japan",
       thread: [
         { text: "{social_text}", assets: { images: [{ url: "{image_url}" }] } },
         { text: "{post_title}", assets: { link: { url: "{post_url}" } } }
       ]
     }
   }
   ```
   Omit `assets` on thread item 1 if no image.

   **X** — threaded post (text+image, then title+link):
   ```
   channelId: "5f371d0a1c14ed2014066090"
   text: "{social_text}"
   schedulingType: "automatic"
   mode: (from config)
   dueAt: (if customScheduled)
   saveToDraft: (if --drafts)
   metadata: {
     twitter: {
       thread: [
         { text: "{social_text}", assets: { images: [{ url: "{image_url}" }] } },
         { text: "{post_title}", assets: { link: { url: "{post_url}" } } }
       ]
     }
   }
   ```
   Omit `assets` on thread item 1 if no image.

9. **Update dates in the data file**:
   - For each selected post (in selection order), set `last_posted_social` to `startDate + offset days` (offset 1, 2, 3, ...). Format as MM/DD/YYYY.
   - If the post had a non-empty `static_text`, clear it (set to `""`) after scheduling.

10. **Re-sort the data file**:
    - Primary: `type` ascending (alphabetical)
    - Secondary: `last_posted_social` descending (newest first) within each type

11. **Write the updated JSON** back to the data file. Format as a JSON array with 4-space indentation per object and a blank line between groups of different `type` values:

    ```json
    [
      {
          "name": "...",
          "type": "Essay",
          ...
      },
      {
          "name": "...",
          "type": "Essay",
          ...
      },

      {
          "name": "...",
          "type": "Food",
          ...
      }
    ]
    ```

12. **Present results** as a numbered summary: title, URL, social text, and Buffer status per channel.

13. **Inform the user** that the data file has been updated and posts have been scheduled (or saved as drafts).
