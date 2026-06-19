"""Test suite for the reader's server-side search (build_reader.search_dataset).

Covers the gap that motivated the endpoint: an author/title search must reach
*every* standalone post, not just the 100 on the currently rendered index page.
Also covers series and group matching, the result cap, case-insensitivity, and
the empty query.

Run: python workflows/reddit-collector/scripts/test_reader_search.py
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent))

import build_reader
from build_reader import search_dataset


# ─── Fixture ──────────────────────────────────────────────────────────────

def make_data():
    """A small in-memory dataset standing in for ReaderData.

    Only the attributes search_dataset (and _group_stats) read are populated.
    """
    series_list = [
        {"slug": "alices-epic", "name": "Alice's Epic", "author": "alice",
         "total_parts": 5, "status": "complete",
         "first_posted": "2023-01-01", "last_posted": "2023-05-01T00:00:00"},
        {"slug": "bobs-tale", "name": "Bob's Tale", "author": "bob",
         "total_parts": 3, "status": "ongoing",
         "first_posted": "2023-02-01", "last_posted": "2023-06-01"},
        {"slug": "saga-bk1", "name": "Saga Book 1", "author": "alice",
         "total_parts": 4, "status": "complete",
         "first_posted": "2023-01-15", "last_posted": "2023-03-01"},
        {"slug": "saga-bk2", "name": "Saga Book 2", "author": "alice",
         "total_parts": 4, "status": "ongoing",
         "first_posted": "2023-04-01", "last_posted": "2023-07-01"},
    ]
    series_by_slug = {s["slug"]: s for s in series_list}
    groups = {
        "saga": {"name": "Saga", "author": "alice",
                 "members": ["saga-bk1", "saga-bk2"]},
    }

    # 150 standalones. Alice-authored ones sit well past index 100 to prove the
    # search is not limited to the first rendered page; one carol post matches on
    # title only. standalone_ids is date-descending in production; order here is
    # irrelevant to the search but we keep it list-like.
    post_meta = {}
    standalone_ids = []
    for i in range(150):
        pid = f"z{i:03d}"
        post_meta[pid] = {"title": f"Filler story {i}", "author": "zoltan",
                          "created_utc": "2020-01-01T00:00:00"}
        standalone_ids.append(pid)

    alice_posts = {
        "sa1": {"title": "Alice rambles about cats", "author": "alice",
                "created_utc": "2023-08-01T12:00:00"},
        "sa2": {"title": "Another alice oneshot", "author": "alice",
                "created_utc": "2023-08-02T12:00:00"},
        "sa3": {"title": "alice's third standalone", "author": "alice",
                "created_utc": "2023-08-03T12:00:00"},
        "ca1": {"title": "A letter to Alice", "author": "carol",
                "created_utc": "2023-08-04T12:00:00"},  # title-only match
    }
    # Insert them around index 120 so they are far past the 100-post page cut.
    for offset, (pid, meta) in enumerate(alice_posts.items()):
        post_meta[pid] = meta
        standalone_ids.insert(120 + offset, pid)

    return SimpleNamespace(
        series_list=series_list,
        series_by_slug=series_by_slug,
        groups=groups,
        post_meta=post_meta,
        standalone_ids=standalone_ids,
    )


# ─── Runner ───────────────────────────────────────────────────────────────

def run_tests():
    data = make_data()
    passed = 0
    failed = 0

    def check(label, cond):
        nonlocal passed, failed
        if cond:
            passed += 1
        else:
            print(f"  FAIL: {label}")
            failed += 1

    print("=== search_dataset() ===\n")

    # Empty query → everything empty.
    r = search_dataset(data, "")
    check("empty query: no groups", r["groups"] == [])
    check("empty query: no series", r["series"] == [])
    check("empty query: no standalones", r["standalones"] == [])
    check("empty query: zero totals",
          r["totals"] == {"groups": 0, "series": 0, "standalones": 0})

    # Author search reaches standalones far past the first index page.
    r = search_dataset(data, "alice")
    sa_ids = {p["id"] for p in r["standalones"]}
    check("author: alice's authored standalones found",
          {"sa1", "sa2", "sa3"} <= sa_ids)
    check("author: title-only 'Alice' match found", "ca1" in sa_ids)
    check("author: standalone total counts all four", r["totals"]["standalones"] == 4)
    check("author: alice's series surfaced",
          {s["slug"] for s in r["series"]} == {"alices-epic", "saga-bk1", "saga-bk2"})
    check("author: alice's group surfaced",
          [g["slug"] for g in r["groups"]] == ["saga"])
    check("author: filler not matched",
          all(not p["id"].startswith("z") for p in r["standalones"]))

    # Group stats are aggregated correctly.
    g = r["groups"][0]
    check("group: member count", g["count"] == 2)
    check("group: total parts summed", g["total_parts"] == 8)
    check("group: last_posted truncated to 10 chars", len(g["last_posted"]) == 10)

    # Series name match (not author).
    r = search_dataset(data, "saga")
    check("name: both saga books matched", r["totals"]["series"] == 2)
    check("name: saga group matched", r["totals"]["groups"] == 1)
    check("name: no standalones matched", r["totals"]["standalones"] == 0)
    check("series: last_posted truncated",
          all(len(s["last_posted"]) == 10 for s in r["series"]))

    # Case-insensitivity.
    r_lower = search_dataset(data, "alice")
    r_upper = search_dataset(data, "ALICE")
    check("case-insensitive: same standalone total",
          r_lower["totals"]["standalones"] == r_upper["totals"]["standalones"])

    # No match.
    r = search_dataset(data, "xyzzy-no-such-thing")
    check("no match: empty results and totals",
          r["groups"] == [] and r["series"] == [] and r["standalones"] == []
          and r["totals"]["standalones"] == 0)

    # Result cap: lists are capped while totals report the true count.
    original = build_reader.SEARCH_CAP_STANDALONES
    build_reader.SEARCH_CAP_STANDALONES = 2
    try:
        r = search_dataset(data, "alice")
    finally:
        build_reader.SEARCH_CAP_STANDALONES = original
    check("cap: standalone list capped to 2", len(r["standalones"]) == 2)
    check("cap: standalone total still reports 4", r["totals"]["standalones"] == 4)

    print(f"\n{'=' * 40}")
    print(f"  {passed} passed, {failed} failed")
    if failed:
        print(f"\n  {failed} test(s) FAILED.")
        sys.exit(1)
    else:
        print(f"\n  All tests passed.")


if __name__ == "__main__":
    run_tests()
