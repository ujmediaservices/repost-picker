import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path

CSV_PATH = Path(r".\uj-repost-content.csv")
DATE_COL = "Last posted - social"


def parse_date(date_str: str) -> datetime | None:
    date_str = date_str.strip()
    if not date_str:
        return None
    for fmt in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue
    return None


def pick_reposts(num_posts: int, start_date: datetime) -> list[tuple[str, str]]:
    with open(CSV_PATH, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Build list of (parsed_date, index) for rows that have a date
    dated = []
    for i, row in enumerate(rows):
        dt = parse_date(row[DATE_COL])
        if dt is not None:
            dated.append((dt, i))

    # Sort by date ascending (oldest first) and take the top n
    dated.sort(key=lambda x: x[0])
    selected = dated[:num_posts]

    # Update dates: day after start_date, incrementing by 1 for each entry
    results: list[tuple[str, str]] = []
    for offset, (_, idx) in enumerate(selected, start=1):
        new_date = start_date + timedelta(days=offset)
        rows[idx][DATE_COL] = new_date.strftime("%m/%d/%Y")
        results.append((rows[idx]["Post Name"], rows[idx]["Post Link"]))

    # Sort rows by date descending before saving (undated rows go to the end)
    rows.sort(
        key=lambda r: parse_date(r[DATE_COL]) or datetime.min,
        reverse=True,
    )

    # Write the updated CSV back
    fieldnames = list(rows[0].keys())
    with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    return results


def main() -> None:
    try:
        num_posts = int(input("Number of posts to retrieve: "))
    except ValueError:
        print("Please enter a valid integer.")
        sys.exit(1)

    date_str = input("Date to search from (MM/DD/YYYY): ").strip()
    start_date = parse_date(date_str)
    if start_date is None:
        print("Invalid date format. Use MM/DD/YYYY.")
        sys.exit(1)

    results = pick_reposts(num_posts, start_date)

    print(f"\n{len(results)} post(s) selected:\n")
    for name, url in results:
        print(f"  {name}")
        print(f"  {url}\n")


if __name__ == "__main__":
    main()
