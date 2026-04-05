---
name: repost-picker
description: Pick the N oldest-shared essays and M oldest travel promo posts from the repost CSV, generate social media text via Claude, schedule to Buffer, and assign new future dates
command: repost
arguments: "<num_essays> <num_travel> <start_date MM/DD/YYYY>"
---

# Repost Picker

Pick the oldest-shared essay and travel promo posts from the social-media repost CSV, fetch each post's content from WordPress, generate social media text via Claude, schedule each post to Buffer, and update the CSV with new dates.

## Inputs

The user invokes this skill with three arguments:

1. **num_essays** - how many Essay-type posts to retrieve (integer)
2. **num_travel** - how many travel promo posts (all other types) to retrieve (integer)
3. **start_date** - the reference date in MM/DD/YYYY format; new dates begin the day after this date

Example: `/repost 2 3 04/04/2026`

If the user omits arguments or they are invalid, ask for the missing values before proceeding.

## Steps

1. Run the Python script `repost_picker.py` located in the project root with command line arguments:

```bash
cd "G:/マイドライブ/Unseen Japan/Code/repost-picker"
python repost_picker.py <num_essays> <num_travel> <start_date>
```

2. The script will:
   - Read WordPress credentials from Windows Credential Manager (target: `https://unseen-japan.com`)
   - Open `.\uj-repost-content.csv`
   - Select the N oldest Essay posts and M oldest travel promo posts by the "Last posted - social" column
   - Interleave the two types, with travel promo posts given priority
   - Update each selected post's date to start_date + 1 day, +2 days, etc.
   - For each post, fetch its content from the WordPress REST API
   - Send the content to Claude to generate social media text alternatives (two-step: generate alternatives, then extract the best one)
   - Schedule to Buffer channels (Bluesky, Mastodon, Threads, X) via GraphQL API (mode: shareNext)
   - Re-sort the entire CSV by date descending before saving
   - Print comma-delimited tuples of (post title, post URL, social media text, Buffer results)

3. Present the results to the user as a numbered list showing each post's **title**, **URL**, **Claude-generated social media text**, and **Buffer scheduling status**.

4. Let the user know the CSV has been updated with the new dates and the posts have been scheduled in Buffer.
