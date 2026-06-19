"""
Audit script for HFY series directories.
Finds systematic bugs in series detection and indexing.
"""
import io
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

# Force stdout to UTF-8 to handle Unicode series names on Windows
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SERIES_ROOT = Path("workflows/reddit-collector/collections/HFY/series")
SAMPLE_LIMIT = 200  # how many None-part titles to sample for pattern analysis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_frontmatter(text):
    """Extract YAML frontmatter fields as a dict (simple key: value only)."""
    fm = {}
    if not text.startswith("---"):
        return fm
    end = text.find("\n---", 3)
    if end == -1:
        return fm
    block = text[3:end]
    for line in block.splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            fm[key.strip()] = val.strip().strip('"').strip("'")
    return fm


def parse_table_rows(text):
    """
    Yield (post_id, part_num, title) from the markdown table in _index.md.
    Standard format: | # | Title | Date | Score | Post ID |

    Pipe-aware (matches the reader): titles may contain escaped pipes (``\\|``)
    which are NOT column separators. We split only on unescaped pipes and read
    fixed fields from the ends — part number is the first cell, post ID the
    last, title is everything between the first cell and the trailing
    date/score/id columns. A naive ``split("|")`` miscounts columns on any row
    whose title contains a pipe, producing phantom duplicates and wrong IDs.
    """
    lines = text.splitlines()
    in_table = False

    for line in lines:
        stripped = line.strip()
        # Detect header: | # | Title | ... | Post ID |
        if stripped.startswith("|") and "Post ID" in stripped and "Title" in stripped:
            in_table = True
            continue

        if in_table:
            # Skip separator line
            if re.match(r"^\s*\|[-| :]+\|\s*$", line):
                continue
            if not stripped.startswith("|"):
                break
            # Split on unescaped pipes only, then drop the empty edge cells
            # produced by the leading/trailing table pipes.
            cells = [c.strip() for c in re.split(r"(?<!\\)\|", stripped)]
            if cells and cells[0] == "":
                cells = cells[1:]
            if cells and cells[-1] == "":
                cells = cells[:-1]
            if len(cells) < 5 or cells[0] in ("#", "---", ""):
                continue
            part = cells[0]
            post_id = cells[-1]
            title = "|".join(cells[1:-3]).replace(r"\|", "|").strip()
            if post_id:
                yield post_id, part, title


# ---------------------------------------------------------------------------
# Main audit
# ---------------------------------------------------------------------------

def main():
    series_dirs = sorted(d for d in SERIES_ROOT.iterdir() if d.is_dir()) \
        if SERIES_ROOT.exists() else []

    # Storage
    slugs = {}           # slug -> (dir_name, series_name)
    all_rows = []        # (post_id, part, title, slug)
    bracket_names = []   # (dir_name, series_name)
    comma_series = []    # (dir_name, series_name, example_title)
    none_part_titles = []  # titles where part == "None"

    print(f"Scanning {len(series_dirs)} series directories...\n")

    for d in series_dirs:
        index_file = d / "_index.md"
        if not index_file.exists():
            continue

        text = index_file.read_text(encoding="utf-8", errors="replace")
        fm = parse_frontmatter(text)

        slug = fm.get("series_slug", "")
        name = fm.get("series_name", "")

        if slug:
            slugs[slug] = (d.name, name)

        # Task 3: bracket-wrapped names
        if name.startswith("["):
            bracket_names.append((d.name, name))

        # Parse table rows
        found_comma_example = None
        for post_id, part, title in parse_table_rows(text):
            all_rows.append((post_id, part, title, slug))

            # Task 2: comma-separated titles (Title, Part N / Title, Chapter N)
            if re.search(r",\s*(part|chapter|vol|volume|episode|book|arc)\s*\d+", title, re.IGNORECASE):
                if found_comma_example is None:
                    found_comma_example = title

            # Task 5: collect None-part titles for pattern analysis
            if part.lower() == "none":
                none_part_titles.append(title)

        if found_comma_example:
            comma_series.append((d.name, name, found_comma_example))

    # -----------------------------------------------------------------------
    # Task 1: Slug fragmentation
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("TASK 1 — SLUG FRAGMENTATION")
    print("=" * 70)

    slug_list = sorted(slugs.keys())
    fragmented = defaultdict(list)  # base_slug -> [slug, ...]

    # Minimum slug length of 5 chars to avoid trivially short prefixes like "the", "a"
    MIN_BASE_LEN = 5
    for i, s1 in enumerate(slug_list):
        if len(s1) < MIN_BASE_LEN:
            continue
        for s2 in slug_list[i + 1:]:
            if s2.startswith(s1 + "-"):
                fragmented[s1].append(s2)

    # Also handle cases where s2 is the base of s1
    # (already covered by iterating sorted list — shorter comes first)

    # Separate: "true" fragmentation where the fragment slug ends with a
    # part/chapter/episode marker (these are definitely split series),
    # vs general prefix matches (may or may not be related).
    FRAG_SUFFIXES = re.compile(r'-(part|chapter|ep|pt|vol|volume|book|arc|ch|episode|section|update)(-\d+)?$')

    true_frags = {}   # base -> [frags]
    general_frags = {}

    for base, frags in fragmented.items():
        true_f = [f for f in frags if FRAG_SUFFIXES.search(f)]
        other_f = [f for f in frags if not FRAG_SUFFIXES.search(f)]
        if true_f:
            true_frags[base] = true_f
        if other_f:
            general_frags[base] = other_f

    print(f"\nTrue fragmentation (slug ends with part/chapter/ep/etc marker): {len(true_frags)} base slugs\n")
    for base, frags in sorted(true_frags.items(), key=lambda x: -len(x[1])):
        print(f"  BASE: {base!r}  ({slugs[base][1]!r})")
        for f in frags:
            dir_name, sname = slugs[f]
            print(f"    +-- {f!r}  ({sname!r})")
        print()

    print(f"\nGeneral prefix matches (may be related series, not necessarily fragmented): {len(general_frags)} base slugs")
    print("(Top 20 by fragment count)")
    for base, frags in sorted(general_frags.items(), key=lambda x: -len(x[1]))[:20]:
        print(f"  {base!r}: {len(frags)} variants — e.g. {frags[0]!r}")
    print()

    # -----------------------------------------------------------------------
    # Task 2: Comma-separated titles
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("TASK 2 — COMMA-SEPARATED PART/CHAPTER TITLES")
    print("=" * 70)
    print(f"\nSeries with comma-separated part titles: {len(comma_series)}\n")
    for dir_name, name, example in comma_series[:30]:
        print(f"  [{dir_name}]  name={name!r}")
        print(f"    e.g. {example!r}")
    if len(comma_series) > 30:
        print(f"  ... and {len(comma_series) - 30} more")
    print()

    # -----------------------------------------------------------------------
    # Task 3: Bracket-wrapped series names
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("TASK 3 — BRACKET-WRAPPED SERIES NAMES")
    print("=" * 70)
    print(f"\nSeries with names starting with '[': {len(bracket_names)}\n")
    for dir_name, name in bracket_names[:30]:
        print(f"  {dir_name}: {name!r}")
    if len(bracket_names) > 30:
        print(f"  ... and {len(bracket_names) - 30} more")
    print()

    # -----------------------------------------------------------------------
    # Task 4: "None" part numbers
    # -----------------------------------------------------------------------
    print("=" * 70)
    print('TASK 4 — "None" PART NUMBERS')
    print("=" * 70)
    none_count = sum(1 for _, part, _, _ in all_rows if part.lower() == "none")
    total_rows = len(all_rows)
    print(f"\nTotal table rows: {total_rows}")
    print(f'Rows with part == "None": {none_count}  ({100*none_count/total_rows:.1f}% of all rows)\n')

    # -----------------------------------------------------------------------
    # Task 5: Patterns in None-part titles
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("TASK 5 — PATTERNS IN None-PART TITLES")
    print("=" * 70)

    patterns = {
        "part_no_space":   (r'\bpart\d+', "partN (no space)"),
        "episode":         (r'\b(episode|ep\.?)\s*\d+', "Episode N"),
        "book":            (r'\bbook\s*\d+', "Book N"),
        "arc":             (r'\barc\s*\d+', "Arc N"),
        "volume":          (r'\b(volume|vol\.?)\s*\d+', "Volume N"),
        "chapter_no_space":(r'\bchapter\d+', "chapterN (no space)"),
        "ch_abbrev":       (r'\bch\.?\s*\d+', "Ch. N"),
        "number_suffix":   (r'\s#\d+', "#N suffix"),
        "ordinal":         (r'\b\d+(st|nd|rd|th)\b', "ordinal (1st/2nd)"),
        "roman":           (r'\b(i{2,}|iv|vi{0,3}|ix|x+)\b', "Roman numerals"),
        "dash_num":        (r'\s-\s*\d+$', "trailing dash-N"),
        "colon_part":      (r':\s*(part|chapter|episode)\s*\d+', ": Part N"),
        "standalone_num":  (r'^\d+[:\s]', "leading number"),
        "part_word":       (r'\bpart\s+[a-z]+\b', "Part (word)"),
        "update":          (r'\bupdate\s*\d+', "Update N"),
        "section":         (r'\bsection\s*\d+', "Section N"),
    }

    counts = defaultdict(int)
    examples = defaultdict(list)

    for title in none_part_titles:
        tl = title.lower()
        matched_any = False
        for key, (pat, _) in patterns.items():
            if re.search(pat, tl):
                counts[key] += 1
                if len(examples[key]) < 3:
                    examples[key].append(title)
                matched_any = True
        if not matched_any:
            counts["_unmatched"] += 1
            if len(examples["_unmatched"]) < 5:
                examples["_unmatched"].append(title)

    print(f"\nNone-part titles total: {len(none_part_titles)}")
    print("\nPattern breakdown (titles may match multiple patterns):\n")
    for key, (pat, label) in sorted(patterns.items(), key=lambda x: -counts[x[0]]):
        if counts[key]:
            print(f"  {label}: {counts[key]}")
            for ex in examples[key]:
                print(f"    - {ex!r}")

    if counts["_unmatched"]:
        print(f"\n  No pattern matched: {counts['_unmatched']}")
        for ex in examples["_unmatched"]:
            print(f"    - {ex!r}")
    print()

    # -----------------------------------------------------------------------
    # Task 6: Duplicate post IDs across series
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("TASK 6 — DUPLICATE POST IDs ACROSS SERIES")
    print("=" * 70)

    post_to_slugs = defaultdict(set)
    for post_id, _, _, slug in all_rows:
        if post_id and post_id != "Post ID":
            post_to_slugs[post_id].add(slug)

    dupes = {pid: sl for pid, sl in post_to_slugs.items() if len(sl) > 1}
    print(f"\nPost IDs appearing in multiple series: {len(dupes)}\n")
    for pid, sl_set in sorted(dupes.items())[:30]:
        print(f"  {pid}: in {sorted(sl_set)}")
    if len(dupes) > 30:
        print(f"  ... and {len(dupes) - 30} more")
    print()

    # -----------------------------------------------------------------------
    # Baseline comparison
    # -----------------------------------------------------------------------
    print("=" * 70)
    print("BASELINE COMPARISON (pre-Phase 1 → post-Phase 1)")
    print("=" * 70)

    baseline = {
        "Total series dirs":           6030,
        "Total table rows":            None,
        "Slug fragmentation groups":   None,
        "Comma-separated series":      None,
        "Bracket-named series":        None,
        "None-part rows":              1652,
        "Duplicate post IDs":          1151,
    }
    now_vals = {
        "Total series dirs":           len(series_dirs),
        "Total table rows":            total_rows,
        "Slug fragmentation groups":   len(fragmented),
        "Comma-separated series":      len(comma_series),
        "Bracket-named series":        len(bracket_names),
        "None-part rows":              none_count,
        "Duplicate post IDs":          len(dupes),
    }

    print(f"\n{'Metric':<30} {'Baseline':>10} {'Now':>10} {'Delta':>10}")
    print("-" * 62)
    for label in baseline:
        b = baseline[label]
        n = now_vals[label]
        if b is not None:
            delta = n - b
            sign = "+" if delta > 0 else ""
            print(f"  {label:<28} {b:>10} {n:>10} {sign}{delta:>9}")
        else:
            print(f"  {label:<28} {'n/a':>10} {n:>10} {'':>10}")

    # -----------------------------------------------------------------------
    # Summary
    # -----------------------------------------------------------------------
    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Total series dirs scanned : {len(series_dirs)}")
    print(f"  Total table rows          : {total_rows}")
    print(f"  Slug fragmentation groups : {len(fragmented)}")
    print(f"  Comma-separated series    : {len(comma_series)}")
    print(f"  Bracket-named series      : {len(bracket_names)}")
    print(f'  "None" part rows          : {none_count}')
    print(f"  Duplicate post IDs        : {len(dupes)}")


if __name__ == "__main__":
    main()
