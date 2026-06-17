# Scripts

**Last modified:** 2026-06-17

## Purpose
Python modules for the Reddit collector workflow. The main entry point is `run.py`; supporting modules handle API communication, state tracking, post formatting, historical backfill, and series detection.

## Contents
- `run.py` — CLI entry point. Parses arguments, loads config, routes to collection or series reindexing, logs results.
- `reddit_client.py` — Reddit API client with JSON and RSS fallback, sliding-window rate limiting, pagination, retry logic, and HTML-to-Markdown conversion for RSS content.
- `backfill.py` — Historical backfill engine using Arctic Shift API with monthly windowing and resumability.
- `series_detector.py` — Detects multi-part series via title patterns and navigation links; generates series indexes.
- `post_formatter.py` — Converts Reddit post data to Markdown with YAML frontmatter; parses post files back to dicts.
- `tracker.py` — Manages download state: tracker metadata (JSON) and per-subreddit post-ID files (text).

## Inputs
None. Scripts are invoked via `run.py` with CLI arguments; configuration is loaded from `config/feeds.json`.

## Outputs
None directly. Scripts write to `collections/` and `state/` directories via `run.py`.

## Steps
N/A. This is a container directory for script modules.

## Dependencies
- `requests` — HTTP client.
- `python-dotenv` — `.env` loading.
- `truststore` — OS certificate store for SSL verification.
- `workflows/biblio-tools/scripts/runtime.py` [[workflows/biblio-tools/scripts/CONTEXT]] — canonical project runtime.

## Known Issues
None.

## Revision History
- 2026-06-14 — Initial creation with six modules.
- 2026-06-17 — Updated `reddit_client.py` with RSS fallback and HTML-to-Markdown converter. Updated `backfill.py` Arctic Shift endpoint. Added `truststore` dependency.
