# Reddit Collector

**Last modified:** 2026-06-17

## Purpose
General-purpose Reddit post collector. Downloads posts from configured subreddits, saves them as Markdown files with YAML frontmatter, detects multi-part series and groups them with navigable indexes. Supports full historical backfill and incremental daily collection.

## Contents
- Scripts — `workflows/reddit-collector/scripts/` [[workflows/reddit-collector/scripts/CONTEXT]] — Python modules: CLI entry point, Reddit API client, backfill engine, series detector, post formatter, state tracker.
- Config — `workflows/reddit-collector/config/` [[workflows/reddit-collector/config/CONTEXT]] — Feed configuration files defining which subreddits to collect and filtering options.
- Collections — `workflows/reddit-collector/collections/` — Downloaded posts and series indexes, organised by subreddit. Gitignored (personal content).
- State — `workflows/reddit-collector/state/` — Download tracker and post-ID files for resumability. Gitignored (machine-specific state).
- Requirements — `workflows/reddit-collector/requirements.txt` [[workflows/reddit-collector/CONTEXT]] — Python package dependencies (`truststore`).
- Setup guide — `workflows/reddit-collector/SETUP.md` [[workflows/reddit-collector/SETUP]] — First-run instructions, environment variable configuration, and OAuth upgrade path.

## Inputs
- `config/feeds.json` — feed definitions specifying subreddits, content filters, score thresholds, and series detection preferences. Created from `feeds.example.json` on first run.
- `USER_EMAIL` from `.env` — used in the User-Agent header for Reddit API politeness.
- CLI flags: `--subreddit <name>`, `--all`, `--backfill`, `--reindex-series`, `--dry-run`.

## Outputs
- `collections/<subreddit>/posts/<post-id>.md` — individual post files with YAML frontmatter and Markdown body.
- `collections/<subreddit>/series/<series-slug>/_index.md` — series index with ordered table of all parts.
- `collections/<subreddit>/series/<series-slug>/NNN-<slug>.md` — reference files linking to the post in `posts/`.

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
- `requests` — HTTP client (already in project requirements).
- `python-dotenv` — `.env` loading (already in project requirements).
- `truststore` — uses the OS certificate store for SSL verification; required for Arctic Shift connectivity on Windows. Listed in `workflows/reddit-collector/requirements.txt` [[workflows/reddit-collector/CONTEXT]].
- Reddit RSS feed — unauthenticated Atom feed used for incremental collection. Fallback from the JSON API which now returns 403 for unauthenticated requests.
- Reddit public JSON API — currently blocked (403) without OAuth. The client tries JSON first and falls back to RSS automatically. When OAuth credentials become available, this path will resume working with no code changes beyond adding authentication.
- Arctic Shift API (`arctic-shift.photon-reddit.com/api/posts/search`) — community Reddit archive for historical backfill.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — canonical project runtime hand-off.

## Known Issues
- Reddit's public JSON API returns 403 for unauthenticated requests. Incremental collection uses the RSS feed as a fallback. RSS does not provide score, flair, upvote ratio, or comment count — these fields default to 0/empty for RSS-sourced posts. Adding OAuth credentials later will restore full metadata.
- Arctic Shift is a community service with no SLA. Its API endpoint has changed in the past (from `/api/posts` to `/api/posts/search`) and may change again.
- For active subreddits, the first incremental run may hit Reddit's rate limits before finishing. Run `--backfill` first for the initial population, then use incremental runs for daily updates.
- Series detection relies on title patterns and navigation links. Series with highly inconsistent naming may not be detected automatically.
- The tracker stores post IDs in a flat text file. For subreddits with 100,000+ posts, this file may grow large but should remain performant (loaded as a set).

## Revision History
- 2026-06-14 — Initial creation.
- 2026-06-17 — Fixed Arctic Shift URL (`/api/posts` → `/api/posts/search`). Added `truststore` for SSL cert handling. Added RSS fallback for incremental collection when JSON API returns 403. Improved rate-limit resilience with separate retry counter for 429 responses.
