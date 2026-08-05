"""Normalisation test suite for series_detector.py.

Covers: comma separation, bracket wrapping, trailing subtitles, pipe titles,
year-like numbers, Ralts-style patterns, tag suffixes, prologue/epilogue,
and outlier validation.

Run: python workflows/reddit-collector/scripts/test_normalisation.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from series_detector import (
    extract_series_info, _normalise_series_name, _slugify,
    _group_root, _group_display_name, _order_parts,
)


# ─── Test cases ───────────────────────────────────────────────────────────

# Each tuple: (title, expected_series_name, expected_part_number, expected_part_type)
# Use None for fields that should be None (no match expected).

EXTRACT_CASES = [
    # === Basic keyword patterns ===
    ("My Story - Part 1", "My Story", 1, "Part"),
    ("My Story, Part 42", "My Story", 42, "Part"),
    ("My Story: Part III", "My Story", 3, "Part"),
    ("My Story (Part 5)", "My Story", 5, "Part"),
    ("My Story [Part 12]", "My Story", 12, "Part"),
    ("My Story - Chapter 3", "My Story", 3, "Chapter"),
    ("My Story, Ch. 16", "My Story", 16, "Chapter"),
    ("My Story - Episode 5", "My Story", 5, "Episode"),
    ("My Story (Ep. 7)", "My Story", 7, "Episode"),
    ("My Story - Book 2", "My Story", 2, "Book"),
    ("My Story - Volume 3", "My Story", 3, "Volume"),
    ("My Story: Arc 4", "My Story", 4, "Arc"),

    # === Decimal numbering ===
    ("My Story Part 3.5", "My Story", 3.5, "Part"),

    # === Fraction pattern ===
    ("My Story (3/10)", "My Story", 3, "Part"),
    ("My Story [7/20]", "My Story", 7, "Part"),

    # === Comma-separated ===
    ("Adventures in Space, Part 7", "Adventures in Space", 7, "Part"),
    ("Adventures in Space, Chapter 12", "Adventures in Space", 12, "Chapter"),

    # === Whitespace-only separator ===
    ("My Story Part 8", "My Story", 8, "Part"),
    ("My Story Chapter 2", "My Story", 2, "Chapter"),
    ("My Story Episode 3", "My Story", 3, "Episode"),

    # === Bare number patterns ===
    ("My Story - 42", "My Story", 42, "Part"),
    ("My Story 42", "My Story", 42, "Part"),

    # === Ralts-style: number before subtitle ===
    ("First Contact - 268 - P'Thok & The Great Ice Cream Raid",
     "First Contact", 268, "Part"),
    ("My Series - 15 - The Beginning", "My Series", 15, "Part"),

    # === Prologue / Epilogue / Interlude ===
    ("My Story - Prologue", "My Story", 0, "Prologue"),
    ("My Story, Epilogue", "My Story", None, "Epilogue"),
    ("My Story - Interlude", "My Story", None, "Interlude"),
    ("My Story - Interlewd", "My Story", None, "Interlude"),
    ("My Story - Side Story", "My Story", None, "Interlude"),

    # === Year-detection guard (bare number only) ===
    # "Like It's 2999" — NOT a year, should match
    ("My Story - 2999", "My Story", 2999, "Part"),
    # "August 2022" — year range, bare number, should NOT match
    ("August 2022", None, None, None),
    # "Like It's 1999" — year range, bare number, should NOT match
    ("Like It's 1999", None, None, None),
    # "Part 2022" — keyword-based, SHOULD match (year guard is bare-only)
    ("My Story Part 2022", "My Story", 2022, "Part"),

    # === Tag suffix stripping ===
    ("Everyone's a Catgirl! [Comedy, Isekai, LitRPG] - Chapter 5",
     "Everyone's a Catgirl!", 5, "Chapter"),

    # === Bracket wrapping ===
    ("[OC] My Story - Part 3", "My Story", 3, "Part"),
    ("[PI] Cool Title - Chapter 1", "Cool Title", 1, "Chapter"),

    # === Multi-level: chapter marker + Part sub-marker -> composite decimal ===
    # The chapter is the primary number, the part the secondary (12 Part 1 ->
    # 12.01), so multi-part chapters slot into the parent series in order.
    ("My Story, Ch 16 - Grown Up - Part 2", "My Story", 16.02, "Chapter"),
    ("[The Book of the Chosen] - Chapter Twelve - The Blacksmith's Boy - Part I",
     "The Book of the Chosen", 12.01, "Chapter"),
    ("[The Book of the Chosen] - Chapter Twelve - The Blacksmith's Boy - Part II",
     "The Book of the Chosen", 12.02, "Chapter"),
    # A nameless orphan (no series name before the chapter marker) does NOT
    # match the multi-level pattern — it stays put rather than being merged.
    ("Chapter 3 - Part 1: Meeting a hero", "Chapter 3", 1, "Part"),

    # === Pipe in title (should not break extraction) ===
    ("Love | War - Part 5", "Love | War", 5, "Part"),

    # === Vol. abbreviation standardisation ===
    ("English Magic, Vol. 2 - Chapter 5", "English Magic, Volume 2", 5, "Chapter"),
    ("English Magic Vol 3 - Chapter 1", "English Magic Volume 3", 1, "Chapter"),

    # === Spelled-out chapter/part numbers (keyword-gated only) ===
    ("[The Book of the Chosen] - Chapter Eighteen - Caged",
     "The Book of the Chosen", 18, "Chapter"),
    ("My Story, Chapter Eighteen", "My Story", 18, "Chapter"),
    ("My Story - Chapter Twenty-One", "My Story", 21, "Chapter"),
    ("My Story Chapter Nine", "My Story", 9, "Chapter"),
    ("My Story - Part Forty Two", "My Story", 42, "Part"),
    # A trailing word that is not a number must not be read as a part number
    ("My Story Chapter Banana", None, None, None),
    # Spelled-out numbers are keyword-gated: a bare trailing word never matches
    ("Just A Title One", None, None, None),

    # === Unclosed-bracket chapter leak still extracts cleanly ===
    ("C'Leena Thomas, Prosthetist (Ch. 34) - Part 2",
     "C'Leena Thomas, Prosthetist", 2, "Part"),
]

# Normalisation-only test cases: (input, expected_output)
NORMALISE_CASES = [
    ("  My Story  ", "My Story"),
    ("[OC] My Story", "My Story"),
    ("[PI] Cool Title", "Cool Title"),
    ("[My Wrapped Title]", "My Wrapped Title"),
    ("My Story, Ch 16 - Grown Up", "My Story"),
    ("My Story---", "My Story"),
    ("My Story...", "My Story"),
    ("My Story,,,", "My Story"),
    ("My Story|||", "My Story"),
    ("Everyone's a Catgirl! [Comedy, Isekai, LitRPG]", "Everyone's a Catgirl!"),
    # Trailing "Part 1" is stripped by the part-marker regex; meaningful
    # brackets survive because the bracket-wrap check already ran
    ("[Berk Van Polan VS The Cursed Levels] Part 1",
     "[Berk Van Polan VS The Cursed Levels]"),
    # Vol. abbreviation standardisation
    ("English Magic, Vol. 2", "English Magic, Volume 2"),
    ("English Magic Vol 3", "English Magic Volume 3"),
    ("Stories of the Apex - Vol.2", "Stories of the Apex - Volume 2"),
    # Unclosed-bracket chapter/part leaks are stripped
    ("C'Leena Thomas, Prosthetist (Ch. 34", "C'Leena Thomas, Prosthetist"),
    ("C'Leena Thomas, Prosthetist (Ch. 34)", "C'Leena Thomas, Prosthetist"),
    ("My Story [Pt 3", "My Story"),
    ("My Story (Chapter 5)", "My Story"),
    # Book/Volume/Arc markers are preserved — separate multi-book entries must
    # stay distinct (slug keeps the book number; grouping handles them later)
    ("DIE. RESPAWN. REPEAT. (Book 2", "DIE. RESPAWN. REPEAT. (Book 2"),
    ("The Curators Book 3", "The Curators Book 3"),
]

# Slugify test cases: (input, expected_slug)
SLUGIFY_CASES = [
    ("My Story", "my-story"),
    ("First Contact", "first-contact"),
    ("Everyone's a Catgirl!", "everyone-s-a-catgirl"),
    ("Love | War", "love-war"),
    ("", "untitled"),
    ("A" * 100, "a" * 80),
    # Leading article stripping
    ("The Council of Ages", "council-of-ages"),
    ("A World of Survival", "world-of-survival"),
    ("An Epic Journey", "epic-journey"),
    ("The", "the"),  # Too short after stripping — kept
    ("A X", "a-x"),  # Only 1 char after stripping — kept
    ("An OK Story", "ok-story"),  # 3+ chars after stripping — stripped
    # Article stripping should not affect non-leading articles
    ("Council of the Ages", "council-of-the-ages"),
]


# Series grouping (Phase 3): (input_name, expected_group_root)
GROUP_ROOT_CASES = [
    ("The Swarm", "swarm"),
    ("The Swarm volume 2", "swarm"),
    ("[The Swarm] volume 5", "swarm"),
    ("Ballistic Coefficient - Book 2", "ballistic-coefficient"),
    ("Darkworld: Earth Book 2", "darkworld-earth"),
    ("Cyber Core: Book Two", "cyber-core"),
    # Leading bracket is the real title, not a tag — keep its content
    ("[House of the Golden Oak] Chapter II", "house-of-the-golden-oak"),
    ("[House of the Golden Oak] Chapter VI", "house-of-the-golden-oak"),
]

# Series grouping display names: (input_name, expected_display_name)
GROUP_NAME_CASES = [
    ("The Swarm volume 2", "The Swarm"),
    ("Ballistic Coefficient - Book 2", "Ballistic Coefficient"),
    ("[House of the Golden Oak] Chapter II", "House of the Golden Oak"),
]


# Part ordering (_order_parts): (parts, expected_id_order)
def _P(pid, num, date):
    return {"id": pid, "part_num": num, "created_utc": date}

ORDER_CASES = [
    # Interlude posted before everything -> sorts to the front, not the end
    ([_P("prologue", 0, "2023-04-25"), _P("ch1", 1, "2023-04-28"),
      _P("ch2", 2, "2023-04-29"), _P("interlude", None, "2023-04-24")],
     ["interlude", "prologue", "ch1", "ch2"]),
    # Epilogue posted last -> sorts to the end
    ([_P("ch1", 1, "2023-01-01"), _P("ch2", 2, "2023-01-02"),
      _P("epilogue", None, "2023-01-05")],
     ["ch1", "ch2", "epilogue"]),
    # Interlude posted between ch2 and ch3 -> interleaves in the middle
    ([_P("ch1", 1, "2023-01-01"), _P("ch2", 2, "2023-01-02"),
      _P("ch3", 3, "2023-01-10"), _P("interlude", None, "2023-01-05")],
     ["ch1", "ch2", "interlude", "ch3"]),
    # Unnumbered with no date -> goes last
    ([_P("ch1", 1, "2023-01-01"), _P("x", None, "")],
     ["ch1", "x"]),
    # Pure numbered, given out of order -> ordered by number
    ([_P("ch3", 3, "2023-01-03"), _P("ch1", 1, "2023-01-01"),
      _P("ch2", 2, "2023-01-02")],
     ["ch1", "ch2", "ch3"]),
    # Multi-level decimal parts stay in chapter order
    ([_P("c12p2", 12.02, "2023-06-02"), _P("c12p1", 12.01, "2023-06-01"),
      _P("c11", 11, "2023-05-29"), _P("c13", 13, "2023-06-05")],
     ["c11", "c12p1", "c12p2", "c13"]),
]


# ─── Runner ───────────────────────────────────────────────────────────────

def run_tests():
    failed = 0
    passed = 0

    print("=== extract_series_info() ===\n")
    for title, exp_name, exp_num, exp_type in EXTRACT_CASES:
        got_name, got_num, got_type = extract_series_info(title)
        ok = (got_name == exp_name and got_num == exp_num and got_type == exp_type)
        if not ok:
            print(f"  FAIL: {title!r}")
            print(f"    expected: ({exp_name!r}, {exp_num!r}, {exp_type!r})")
            print(f"    got:      ({got_name!r}, {got_num!r}, {got_type!r})")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _normalise_series_name() ===\n")
    for inp, expected in NORMALISE_CASES:
        got = _normalise_series_name(inp)
        if got != expected:
            print(f"  FAIL: {inp!r}")
            print(f"    expected: {expected!r}")
            print(f"    got:      {got!r}")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _slugify() ===\n")
    for inp, expected in SLUGIFY_CASES:
        got = _slugify(inp)
        if got != expected:
            print(f"  FAIL: {inp!r}")
            print(f"    expected: {expected!r}")
            print(f"    got:      {got!r}")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _group_root() ===\n")
    for inp, expected in GROUP_ROOT_CASES:
        got = _group_root(inp)
        if got != expected:
            print(f"  FAIL: {inp!r}")
            print(f"    expected: {expected!r}")
            print(f"    got:      {got!r}")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _group_display_name() ===\n")
    for inp, expected in GROUP_NAME_CASES:
        got = _group_display_name(inp)
        if got != expected:
            print(f"  FAIL: {inp!r}")
            print(f"    expected: {expected!r}")
            print(f"    got:      {got!r}")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _order_parts() ===\n")
    for parts, expected in ORDER_CASES:
        got = [p["id"] for p in _order_parts(parts)]
        if got != expected:
            print(f"  FAIL: ordering")
            print(f"    expected: {expected!r}")
            print(f"    got:      {got!r}")
            failed += 1
        else:
            passed += 1

    print(f"\n=== _apply_overrides() ===\n")
    import json as _json
    import tempfile
    from series_detector import _apply_overrides
    _all = {
        "a": {"id": "a", "title": "X Ch1", "author": "auth", "created_utc": "2023-01-01", "score": 1},
        "b": {"id": "b", "title": "Y Ch2", "author": "auth", "created_utc": "2023-01-02", "score": 1},
        "junk": {"id": "junk", "title": "X dup", "author": "auth", "created_utc": "2023-01-03", "score": 0},
    }
    _smap = {
        "auto": {"name": "Auto", "slug": "auto", "author": "auth",
                 "parts": [dict(_all["a"]), dict(_all["junk"])],
                 "detection_method": "title_pattern"},
    }
    _ov = {"series": [{"slug": "x", "name": "X", "author": "auth",
                       "parts": [{"id": "a", "num": 1}, {"id": "b", "num": 2}],
                       "exclude": ["junk"]}]}
    with tempfile.TemporaryDirectory() as _d:
        _p = Path(_d) / "_overrides.json"
        _p.write_bytes(_json.dumps(_ov).encode("utf-8"))
        _res = _apply_overrides(_smap, _all, _p)
    _checks = {
        "override series built": "x" in _res,
        "curated order kept": [p["id"] for p in _res.get("x", {}).get("parts", [])] == ["a", "b"],
        "method tagged": _res.get("x", {}).get("detection_method") == "manual_override",
        "emptied auto series dropped": "auto" not in _res,
    }
    for label, ok in _checks.items():
        if ok:
            passed += 1
        else:
            print(f"  FAIL: {label}")
            failed += 1

    print(f"\n{'=' * 40}")
    print(f"  {passed} passed, {failed} failed")
    if failed:
        print(f"\n  {failed} test(s) FAILED.")
        sys.exit(1)
    else:
        print(f"\n  All tests passed.")


if __name__ == "__main__":
    run_tests()
