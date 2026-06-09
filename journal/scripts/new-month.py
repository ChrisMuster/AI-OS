#!/usr/bin/env python3
"""
new-month.py — Creates the journal entry file for the next (or a specified) month.

Pre-fills the file with a heading for every day of the month so you can
scroll straight to today and start writing.

Usage:
    python journal/scripts/new-month.py [--month YYYY-MM] [--dry-run] [--force]

Options:
    --month YYYY-MM   Create a specific month instead of next month.
    --dry-run         Print what would happen without creating any files.
    --force           Override the date gate and create the file regardless of
                      how many days remain in the current month.

Date gate:
    When the target month is the calendar month immediately after the current
    month, this script checks whether today falls within the last 7 days of the
    current month. If not, it exits without creating anything. Use --force to
    override this gate (e.g. for legitimate early creation).

Examples:
    python journal/scripts/new-month.py
    python journal/scripts/new-month.py --month 2026-07
    python journal/scripts/new-month.py --dry-run
    python journal/scripts/new-month.py --month 2026-07 --force
"""

import argparse
import calendar
from pathlib import Path
from datetime import datetime, date

# ---------------------------------------------------------------------------
# Paths
# Script lives at: journal/scripts/new-month.py
# Project root:    ../../  relative to this file
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent

JOURNAL_DIR = PROJECT_ROOT / "journal"
ENTRIES_DIR = JOURNAL_DIR / "entries"
JOURNAL_LOG = JOURNAL_DIR / "LOG.md"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def now_ts() -> str:
    return datetime.now().astimezone().isoformat(timespec='seconds')


def append_log(path: Path, ts: str, action: str, note: str) -> None:
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(entry)


def next_month_from(today: date) -> tuple[int, int]:
    """Return (year, month) for the month after today's month."""
    if today.month == 12:
        return today.year + 1, 1
    return today.year, today.month + 1


def month_display(year: int, month: int) -> str:
    """E.g. 'June 2026'"""
    return date(year, month, 1).strftime("%B %Y")


def day_heading(day: int, month: int) -> str:
    """UK-format day heading with no leading zero, e.g. '1 June'"""
    month_name = date(2000, month, 1).strftime("%B")
    return f"## {day} {month_name}"


def generate_month_content(year: int, month: int) -> str:
    """Generate the full content of a monthly journal file."""
    days_in_month = calendar.monthrange(year, month)[1]
    lines = [f"# Journal — {month_display(year, month)}", ""]
    for day in range(1, days_in_month + 1):
        lines.append(day_heading(day, month))
        lines.append("")
        lines.append("")  # blank line as writing space
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create the journal entry file for next month (or a specified month).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "Examples:\n"
            "  python journal/scripts/new-month.py\n"
            "  python journal/scripts/new-month.py --month 2026-07\n"
            "  python journal/scripts/new-month.py --dry-run"
        ),
    )
    parser.add_argument(
        "--month",
        metavar="YYYY-MM",
        help="Month to create in YYYY-MM format (default: next calendar month)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would happen without creating any files or writing logs.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override the date gate and create next month's file regardless of the current date.",
    )
    args = parser.parse_args()

    today = date.today()

    # Resolve target month
    if args.month:
        try:
            parsed = datetime.strptime(args.month, "%Y-%m")
            year, month = parsed.year, parsed.month
        except ValueError:
            print("ERROR: --month must be in YYYY-MM format (e.g. 2026-06).")
            raise SystemExit(1)
    else:
        year, month = next_month_from(today)

    # Date gate: if the target is the month immediately after the current month,
    # creation is only permitted within the last 7 days of the current month.
    next_year, next_month = next_month_from(today)
    is_next_month = (year == next_year and month == next_month)
    if is_next_month and not args.force:
        days_in_current_month = calendar.monthrange(today.year, today.month)[1]
        days_remaining = days_in_current_month - today.day
        if days_remaining >= 7:
            print(
                f"DATE GATE: Today is {today} — {days_remaining} days remain in "
                f"{today.strftime('%B %Y')}. Next month's file is only created "
                f"within the last 7 days of the current month. Exiting."
            )
            print("Use --force to override this gate if you have a specific reason.")
            raise SystemExit(0)

    month_str   = f"{year}-{month:02d}"
    month_label = month_display(year, month)
    file_path   = ENTRIES_DIR / f"{month_str}.md"

    if args.dry_run:
        print("=== DRY RUN — no files will be created ===")
        print()

    print(f"Target month : {month_label}")
    print(f"File         : journal/entries/{month_str}.md")
    print()

    if file_path.exists():
        print(f"[=] journal/entries/{month_str}.md — already exists, skipping")
    elif args.dry_run:
        print(f"[DRY RUN] would create journal/entries/{month_str}.md")
    else:
        content = generate_month_content(year, month)
        file_path.write_text(content, encoding="utf-8")
        print(f"[+] journal/entries/{month_str}.md created")
        append_log(
            JOURNAL_LOG,
            now_ts(),
            "created",
            f"Created journal entry file for {month_label}: entries/{month_str}.md",
        )

    print()
    if args.dry_run:
        print("=== DRY RUN complete — no changes made ===")
    else:
        print(f"Done. Open journal/entries/{month_str}.md and start writing.")


if __name__ == "__main__":
    main()
