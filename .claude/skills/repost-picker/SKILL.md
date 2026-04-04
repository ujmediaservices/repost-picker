---
name: repost-picker
description: Pick the N oldest-shared posts from the repost CSV and assign them new future dates
command: repost
arguments: "<number_of_posts> <start_date MM/DD/YYYY>"
---

# Repost Picker

Pick the blog posts that were shared the furthest back in the past from the social-media repost CSV and schedule them for resharing on consecutive days starting from a given date.

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
   - Open `C:\Users\allen\Downloads\uj-repost-content.csv`
   - Find the N oldest posts by the "Last posted - social" column
   - Update each selected post's date to start_date + 1 day, +2 days, etc.
   - Re-sort the entire CSV by date descending before saving
   - Print the selected posts (name and URL)

3. Present the results to the user as a numbered list showing each post's **name** and **URL**.

4. Let the user know the CSV has been updated with the new dates.
