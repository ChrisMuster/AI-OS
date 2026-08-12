# Reddit Collector

**Last modified:** 2026-08-12

## Purpose
General-purpose Reddit post collector. Downloads posts from configured subreddits, saves them as Markdown files with YAML frontmatter, detects multi-part series, and groups them with navigable indexes. Supports full historical backfill, incremental daily collection, and local browser reading.

## Contents
- Scripts — `workflows/reddit-collector/scripts/` [[workflows/reddit-collector/scripts/CONTEXT]] — Python modules: CLI entry point, Reddit API client, backfill engine, series detector, post formatter, state tracker, and reader server.
- Config — `workflows/reddit-collector/config/` [[workflows/reddit-collector/config/CONTEXT]] — Feed configuration files defining which subreddits to collect and filtering options.
- Collections — `workflows/reddit-collector/collections/` — Downloaded posts and series indexes, organised by subreddit. Gitignored personal content.
- State — `workflows/reddit-collector/state/` — Download tracker and post-ID files for resumability. Gitignored machine-specific state.
- Requirements — `workflows/reddit-collector/requirements.txt` [[workflows/reddit-collector/CONTEXT]] — Python package dependencies (`truststore`).
- Reader — `workflows/reddit-collector/scripts/build_reader.py` [[workflows/reddit-collector/scripts/CONTEXT]] — Local web server that serves collected posts as browsable HTML pages on demand. No static file generation; reads post Markdown files at request time. Browses standalone posts, series, and series groups (`/group/<slug>`), and offers full-dataset search via `/api/search` that reaches every post regardless of pagination.
- Reader launcher — `workflows/reddit-collector/Read r-HFY.bat` [[workflows/reddit-collector/CONTEXT]] — Clickable Windows batch file to start the r/HFY reader server. Gitignored.
- Reader stop launcher — `workflows/reddit-collector/Stop r-HFY.bat` [[workflows/reddit-collector/CONTEXT]] — Clickable Windows batch file to stop the r/HFY reader server through the token-aware script path. Gitignored.
- Setup guide — `workflows/reddit-collector/SETUP.md` [[workflows/reddit-collector/SETUP]] — First-run instructions, environment variable configuration, reader usage, and OAuth upgrade path.

## Inputs
- `config/feeds.json` — feed definitions specifying subreddits, content filters, score thresholds, and series detection preferences. Created from `feeds.example.json` on first run.
- `USER_EMAIL` from `.env` — used in the User-Agent header for Reddit API politeness.
- `collections/<subreddit>/_overrides.json` (optional) — hand-curated series definitions applied during reindex, for cases auto-detection cannot reconstruct (inconsistent titles, duplicate reposts). Each entry declares canonical membership/order, junk exclusions, a related-works cross-link, and why-broken/how-fixed notes. Local-only curation (the collection is gitignored).
- CLI flags: `--subreddit <name>`, `--all`, `--backfill`, `--reindex-series`, `--build-reader`, `--dry-run`, and reader-specific flags such as `--stop`.

## Outputs
- `collections/<subreddit>/posts/<post-id>.md` — individual post files with YAML frontmatter and Markdown body.
- `collections/<subreddit>/series/<series-slug>/_index.md` — series index with ordered table of all parts. This is the only file written per series (the reader reads it directly); a temporary `series.tmp` directory is built and atomically swapped into place on reindex.
- `collections/<subreddit>/series/_groups.json` — series groups: related series clustered by author + shared root, plus a reverse series→group map. Consumed by the reader for group pages and breadcrumbs.
- `collections/<subreddit>/.reader_cache.json` — JSON metadata cache for the reader server. Generated on first reader start; refreshable via `--refresh-cache` or the `/refresh` endpoint.
- `.reader_server.pid` — gitignored JSON runtime metadata for the reader process, including PID, port, subreddit, start time, and instance token.
- `state/` - the download tracker (`state/tracker.json`) and per-subreddit seen-post-ID files (`state/ids/<subreddit>.txt`), written after each batch so a run is resumable. Gitignored machine-specific state.
- `config/feeds.json` - written once, by the collector, when it is created from `feeds.example.json` on first run, and read on every run after that. It is deliberately listed in both Inputs and Outputs: those describe two different moments in its life rather than the same fact twice, and a path the workflow writes has to appear in Outputs or stage 3b's unregistered-output detection, which reads Outputs and not prose, cannot see it. Its Inputs entry stays because reading it is its dominant role.

## Steps
1. Load feed configuration from `config/feeds.json`.
2. For each target subreddit (or all enabled feeds if `--all`):
   a. Load download state from `state/tracker.json` and `state/ids/<subreddit>.txt`.
   b. If `--backfill`: query Arctic Shift API in monthly windows from start date to present, paginating each window and saving new posts.
   c. If regular run: query Reddit `/r/<subreddit>/new.json`, paginating until reaching the last-seen timestamp, saving new posts.
   d. Update tracker state after each batch of posts.
3. If series detection is enabled for the feed, run the series detector over all downloaded posts.
4. Append `LOG.md` with a completion or failure entry.

## Dependencies
- `requests` — HTTP client already in project requirements.
- `python-dotenv` — `.env` loading already in project requirements.
- `truststore` — uses the OS certificate store for SSL verification; required for Arctic Shift connectivity on Windows. Listed in `workflows/reddit-collector/requirements.txt` [[workflows/reddit-collector/CONTEXT]].
- Reddit RSS feed — unauthenticated Atom feed used for incremental collection. Fallback from the JSON API which now returns 403 for unauthenticated requests.
- Reddit public JSON API — currently blocked (403) without OAuth. The client tries JSON first and falls back to RSS automatically. When OAuth credentials become available, this path will resume working with no code changes beyond adding authentication.
- Arctic Shift API (`arctic-shift.photon-reddit.com/api/posts/search`) — community Reddit archive for historical backfill.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — canonical project runtime hand-off.

## Known Issues
- Reddit's public JSON API returns 403 for unauthenticated requests. Incremental collection uses the RSS feed as a fallback. RSS does not provide score, flair, upvote ratio, or comment count; these fields default to 0 or empty for RSS-sourced posts. Adding OAuth credentials later will restore full metadata.
- Arctic Shift is a community service with no SLA. Its API endpoint has changed in the past (from `/api/posts` to `/api/posts/search`) and may change again.
- For active subreddits, the first incremental run may hit Reddit's rate limits before finishing. Run `--backfill` first for the initial population, then use incremental runs for daily updates.
- Series detection relies on title patterns and navigation links. Series with highly inconsistent naming may not be detected automatically.
- The tracker stores post IDs in a flat text file. For subreddits with 100,000+ posts, this file may grow large but should remain performant when loaded as a set.

## Revision History
- Earlier history archived to LOG.md on 2026-06-19.
- 2026-06-17 — Hardened reader lifecycle safety: reader PID metadata is now tokenised JSON, duplicate detection verifies `/health`, stale live-PID files are removed without killing unknown processes, and the stop launcher uses the token-aware `--stop` path.
- 2026-06-18 — Rewrote series detector for 160k-post scale: cache-first metadata loading, two-pass body reads (only series candidates get full reads), slug length cap (80 chars), type-safe cache parsing. 5,109 series detected and indexed (up from 82).
- 2026-06-18 — Deep series detection fixes: 14 title patterns (comma separators, Episode/Book/Volume/Arc), normalisation (bracket stripping, trailing chapter/subtitle removal), nav-link series info extraction. Reindex unified fragmented series (e.g. Humans for Hire 3→1 dir, Vaid Empire: Conquest 40→1 dir) and produced 6,030 series total.
- 2026-06-18 — Added ascending/descending sort toggle to the reader's series pages. Users can view parts oldest-first or newest-first with a persistent preference.
- 2026-06-19 — Phase 1 series detection fixes (10 items from HANDOVER.md). Pipe escaping, pipe-aware reader parsing, trailing pipe strip, tag suffix strip, Ralts-style patterns, year guard, outlier validation, prologue/epilogue/interlude, explicit LF newlines, normalisation test suite. Reindex: 6,107 series. Pipe corruption 164→32, trailing pipe names 110→0, missing files 1,681→14.
- 2026-06-19 — Phase 2.5 normalisation polish. Leading article stripping in slug generation, Vol./Volume abbreviation standardisation, reindex cleanup (old series directory deletion before rebuild). Reindex: 6,070 series. Duplicate post IDs 1,610→994.
- 2026-06-19 — Indexing bug-fix pass. Removed unused per-part ref files (only `_index.md` per series now); reindex builds into `series.tmp` and atomically swaps with retry/backoff + rollback (fixes intermittent `[Errno 22]`/`[WinError 5]` swap crash); added exception-type/`file:line`/traceback to failure logging. Nav-link guard + authoritative one-post-one-series pass: duplicate post IDs 994→0. Normalisation: unclosed-bracket chapter-leak stripping, spelled-out part numbers. Fixed pipe-aware parsing in `audit_series.py`. Reindex: 6,414 series, 76,986 series-posts, 0 duplicates. Tests 67→81.
- 2026-06-19 — Phase 3 series grouping. Detector clusters related series (multi-book, multi-arc, multi-level chapter splits) by author + shared root and writes `series/_groups.json` without merging them. Reader gained group pages (`/group/<slug>`), index group cards, and "Part of: [Group]" breadcrumbs. 117 groups over 341 series. Tests 81→92.
- 2026-06-19 — Multi-level chapter+part merging (Book of the Chosen → one 40-part series), date-interleaving of unnumbered parts (interludes now sort to their true position), and a manual curation override mechanism. New optional input `collections/<sub>/_overrides.json` lets hand-curated series definitions force canonical membership/order where auto-detection cannot; the reader hides excluded junk posts and renders related-works cross-links. First override: "The Soldier Becomes a Cultivator". Tests 92→105.
- 2026-06-19 — Reader full-dataset search: new `/api/search` endpoint queries every series, group, and standalone post in memory, so author/title searches reach all ~83k standalones instead of only the 100 on the current index page. Fixes the search gap blocking Phase 4 manual review.
- 2026-08-12 - Guard-coverage stage 3c: `state/` and `config/feeds.json` added to Outputs. Both were already described elsewhere in this file, `state/` in Contents and Steps and `feeds.json` in Inputs and Steps, so this is a documentation gap rather than a privacy finding; both paths are gitignored and both already carry tracked inventory rows. `feeds.json` is the judgement the stage was required to settle rather than assume: it is created once by the collector from `feeds.example.json` and read on every run thereafter, and it is now listed in both Inputs and Outputs, because those describe two different moments in its life and because stage 3b's unregistered-output detection reads Outputs rather than prose, so a written path absent from Outputs is invisible to it.
