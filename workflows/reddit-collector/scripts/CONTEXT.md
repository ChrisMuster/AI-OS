# Scripts

**Last modified:** 2026-08-04

## Purpose
Python modules for the Reddit collector workflow. The main entry point is `run.py`; supporting modules handle API communication, state tracking, post formatting, historical backfill, series detection, and the local reader server.

## Contents
- `run.py` — CLI entry point. Parses arguments, loads config, routes to collection, series reindexing, or reader startup, and logs results.
- `reddit_client.py` — Reddit API client with JSON and RSS fallback, sliding-window rate limiting, pagination, retry logic, and HTML-to-Markdown conversion for RSS content.
- `backfill.py` — Historical backfill engine using Arctic Shift API with monthly windowing and resumability.
- `series_detector.py` — Detects multi-part series via title patterns and navigation links; generates a single `_index.md` per series. Uses a two-pass approach: metadata from reader cache, then selective full reads for nav-link extraction. Enforces one-post-one-series uniqueness, recognises spelled-out part numbers (e.g. "Chapter Eighteen"), and handles multi-level "Chapter N … Part M" titles by combining chapter and part into a decimal (12.01) so multi-part chapters merge into their parent in order. Clusters related series into groups (`detect_series_groups`/`write_groups` → `series/_groups.json`) without merging them, and applies hand-curated `_apply_overrides()` definitions from `collections/<sub>/_overrides.json` last (forcing canonical membership/order where auto-detection cannot).
- `post_formatter.py` — Converts Reddit post data to Markdown with YAML frontmatter; parses post files back to dicts.
- `tracker.py` — Manages download state: tracker metadata (JSON) and per-subreddit post-ID files (text).
- `build_reader.py` — Local web server for browsing collected posts. Reads Markdown files on demand, converts to HTML, exposes `/health`, uses token-protected `/shutdown`, and stores verified PID metadata to prevent duplicate or orphaned reader instances. Series pages include a sort toggle for ascending/descending order with localStorage persistence. Pipe-aware index parsing splits on unescaped pipes and reads post IDs from the last column. Loads `series/_groups.json` and serves group pages (`/group/<slug>`): group cards on the index, member series listed chronologically, and a "Part of: [Group]" breadcrumb on member series pages. Reads `_overrides.json` to hide manually-excluded posts everywhere, and renders curated "related works" cross-links on series pages. Search is server-side: the `/api/search` endpoint (`search_dataset()`) queries the full in-memory dataset — every series, group, and standalone post — so author and title searches reach all ~83k standalones, not just the 100 on the current index page; the index search box fetches it live and renders capped results with true total counts.
- `test_normalisation.py` — Test suite for series detection normalisation, grouping, ordering, and override functions (105 assertions). Covers keyword patterns, comma separation, bracket wrapping, tag suffixes, Ralts-style patterns, year detection, prologue/epilogue/interlude, spelled-out numbers, unclosed-bracket chapter leaks, multi-level chapter+part composites, slugification, series-group roots/display names, part ordering (date-interleaving of unnumbered parts), and manual-override application.
- `test_reader_search.py` — Test suite for the reader's server-side search (`build_reader.search_dataset`): author/title reach over all standalones beyond the first index page, series and group matching, result cap with true totals, case-insensitivity, and the empty query (21 assertions).
- `audit_series.py` — Diagnostic audit for series detection. Reports slug fragmentation, comma-separated titles, bracket names, unknown parts, duplicate post IDs, and baseline comparison metrics. Uses the same pipe-aware table parsing as the reader (splits on unescaped pipes, reads the post ID from the last column).

## Inputs
None. Scripts are invoked via `run.py` or directly with CLI arguments; configuration is loaded from `config/feeds.json`.

## Outputs
None directly. Scripts write to `collections/`, `state/`, and reader runtime metadata via the workflow entry points.

## Steps
N/A. This is a container directory for script modules.

## Dependencies
- `requests` — HTTP client.
- `python-dotenv` — `.env` loading.
- `truststore` — OS certificate store for SSL verification.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — canonical project runtime.

## Known Issues
- Multi-book series (e.g. "The Swarm" + "Volume 2/3/4") and multi-level chapter-part titles (e.g. "Chapter Eighteen - Caged - Part I/II") remain as separate series by design. Chapter numbers restart per book and part numbers repeat per chapter, so merging them at the normalisation level would collide numbering and break reading order. These are intended for the planned Phase 3 grouping feature (link, don't merge), not normalisation.
- Distinct series that share a name prefix (e.g. "Deathworld" vs "Deathworld Commando: Reborn") are intentionally not merged — prefix merging would produce false positives.

## Revision History
Earlier history archived to LOG.md on 2026-06-24.
- 2026-06-19 — Phase 1 series detection fixes: pipe escaping in index tables, pipe-aware reader parsing, trailing pipe/tag suffix stripping, Ralts-style patterns, year-detection guard, outlier validation, prologue/epilogue/interlude recognition, explicit LF newlines. Added `test_normalisation.py` (55 assertions) and `audit_series.py` with baseline comparison.
- 2026-06-19 — Phase 2.5 normalisation polish: leading article stripping in `_slugify()`, Vol./Volume abbreviation standardisation in `_normalise_series_name()`, old series directory cleanup in `cmd_reindex_series()`. Test suite expanded to 67 assertions.
- 2026-06-19 — Indexing bug-fix pass: removed unused per-part ref files (single `_index.md` per series); reindex now builds into `series.tmp` and swaps atomically with retry/backoff and rollback (`_robust_replace`/`_robust_rmtree` in `run.py`); failure logging gained exception type, `file:line`, and traceback; nav-link guard plus authoritative one-post-one-series pass drove duplicate post IDs to 0; added unclosed-bracket chapter-leak stripping and spelled-out part-number recognition; made `audit_series.py` pipe-aware. Test suite expanded to 81 assertions.
- 2026-06-19 — Phase 3 series grouping: `series_detector.py` gained `detect_series_groups()`/`write_groups()` (clusters related series by author + group root, writes `series/_groups.json`); `run.py` writes groups into the temp tree before the swap; `build_reader.py` gained group loading, the `/group/<slug>` route, index group cards, and member breadcrumbs; `audit_series.py` now counts only real directories. Test suite expanded to 92 assertions.
- 2026-06-19 — Multi-level chapter+part detection: a new highest-priority pattern combines a within-book marker (Chapter/Episode) and a Part sub-marker into a decimal number (12.01) so multi-part chapters merge into their parent series in order (Book of the Chosen: 7 fragments → one 40-part series). Book/Volume/Arc excluded so multi-book series stay separate. Nameless orphans ("Chapter 3 - Part 1") are intentionally left in their generic bucket as a Phase-4 worklist. Test suite expanded to 95 assertions.
- 2026-06-19 — Part-ordering fix: new `_order_parts()` helper interleaves unnumbered parts (interludes, epilogues, un-numbered prologues) into the numbered sequence by post date instead of dumping them at the end (numbered parts still order by explicit number). An interlude posted before everything now sorts to the front. Test suite expanded to 101 assertions.
- 2026-06-19 — Manual curation overrides: `_apply_overrides()` reads `collections/<sub>/_overrides.json` and forces canonical series membership/order where auto-detection cannot, removing claimed/excluded posts from auto series; `build_series_indexes` writes related-works frontmatter and shows Prologue/Epilogue/Interlude in the # column; `build_reader.py` hides excluded posts everywhere and renders related cross-links. First entry: The Soldier Becomes a Cultivator (Connect_Study3875). Test suite expanded to 105 assertions.
- 2026-06-19 — Reader server-side search: `search_dataset()` + the `/api/search` endpoint in `build_reader.py` query the full in-memory dataset (all series, groups, standalones) so author/title searches reach every standalone, not just the 100 on the current index page. Capped results with true totals; `SEARCH_JS` rewritten to a debounced fetch with stale-response guarding; dead `data-searchable` attributes removed. New `test_reader_search.py` (21 assertions). Fixes the search gap that was blocking Phase 4 manual review.
- 2026-06-24 - Encoding hardening: pinned `encoding="utf-8"` on the `date` `subprocess.run` call in run.py so it decodes as UTF-8 rather than the Windows cp1252 default. No behavioural change.
- 2026-08-04 - Line endings pinned on all seven text writes across `run.py`, `tracker.py`, `build_reader.py` and `series_detector.py`. Six already passed `newline="\n"`, but did so through `Path.write_text`, which only accepts that argument on Python 3.10 while the project states a 3.9 floor - so those six would have raised `TypeError` on the minimum supported interpreter. All seven now use an explicit `open(..., newline="\n")`, which is correct on 3.9. Part of the project-wide pass closing this defect class at all 48 write sites.
- 2026-08-04 - The one fixture write in `test_normalisation.py` converted from `Path.write_text(..., encoding="utf-8")` to `write_bytes`. The entry above covered the production modules in this directory; the test module sitting alongside them was missed, because the encoding guard's code check returned early on `encoding=` until later the same day. Part of the pass clearing the last 50 sites project-wide; `encoding` became a close-out blocking label in the same change. Assertion count unchanged at 105.
