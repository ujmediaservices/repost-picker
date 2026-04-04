---
name: repost-picker
description: Pick the N oldest-shared posts from the repost CSV, generate social media text via Claude, schedule to Buffer, and assign new future dates
command: repost
arguments: "<number_of_posts> <start_date MM/DD/YYYY>"
---

# Repost Picker

Pick the blog posts that were shared the furthest back in the past from the social-media repost CSV, fetch each post's content from WordPress, generate social media text via Claude, schedule each post to Buffer, and update the CSV with new dates.

## Inputs

The user invokes this skill with two arguments:

1. **number_of_posts** - how many posts to retrieve (integer)
2. **start_date** - the reference date in MM/DD/YYYY format; new dates begin the day after this date

Example: `/repost 5 04/04/2026`

If the user omits arguments or they are invalid, ask for the missing values before proceeding.

## Steps

1. Run the Python script `repost_picker.py` located in the project root, passing the arguments via stdin:

```bash
cd "G:/マイドライブ/Unseen Japan/Code/repost-picker"
echo -e "<number_of_posts>\n<start_date>" | python repost_picker.py
```

2. The script will:
   - Read WordPress credentials from Windows Credential Manager (target: `https://unseen-japan.com`)
   - Open `C:\Users\allen\Downloads\uj-repost-content.csv`
   - Find the N oldest posts by the "Last posted - social" column
   - Update each selected post's date to start_date + 1 day, +2 days, etc.
   - For each post, fetch its content from the WordPress REST API
   - Send the content to Claude to generate social media text alternatives (two-step: generate alternatives, then extract the best one)
   - Schedule the best social media text + post URL to Buffer via GraphQL API (mode: shareNext)
   - Re-sort the entire CSV by date descending before saving
   - Print comma-delimited tuples of (post title, post URL, social media text, Buffer post ID)

3. Present the results to the user as a numbered list showing each post's **title**, **URL**, **Claude-generated social media text**, and **Buffer scheduling status**.

4. Let the user know the CSV has been updated with the new dates and the posts have been scheduled in Buffer.
