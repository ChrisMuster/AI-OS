# Web Research — sources/

**Last modified:** 2026-05-29

## Purpose
Source adapter modules for the web research skill. Each file exposes a single `fetch(query, max_results, **kwargs)` function that queries one data source and returns a list of result dicts. All adapters produce the same output shape so `compile.py` can process them uniformly.

## Contents
- tavily_source.py — `skills/web-research/scripts/sources/tavily_source.py` — Tavily Search API; best general research source, clean content extraction (requires TAVILY_API_KEY).
- brave_source.py — `skills/web-research/scripts/sources/brave_source.py` — Brave Search API; broad web search, good complement to Tavily (requires BRAVE_API_KEY).
- guardian_source.py — `skills/web-research/scripts/sources/guardian_source.py` — Guardian Content API; quality long-form journalism with full article text (requires GUARDIAN_API_KEY).
- wikipedia_source.py — `skills/web-research/scripts/sources/wikipedia_source.py` — MediaWiki API; encyclopaedic background.
- hackernews_source.py — `skills/web-research/scripts/sources/hackernews_source.py` — Algolia HN Search API; tech community discussion.
- reddit_source.py — `skills/web-research/scripts/sources/reddit_source.py` — Reddit public JSON API; community opinions.
- arxiv_source.py — `skills/web-research/scripts/sources/arxiv_source.py` — ArXiv Atom API; academic papers.
- semantic_scholar_source.py — `skills/web-research/scripts/sources/semantic_scholar_source.py` — Semantic Scholar Academic Graph API; broad academic search.
- stackexchange_source.py — `skills/web-research/scripts/sources/stackexchange_source.py` — Stack Exchange API v2.3; technical Q&A.
- devto_source.py — `skills/web-research/scripts/sources/devto_source.py` — Dev.to public API; developer community articles.
- rss_source.py — `skills/web-research/scripts/sources/rss_source.py` — feedparser-based RSS reader; curated news feeds.
- scraper_source.py — `skills/web-research/scripts/sources/scraper_source.py` — trafilatura-based scraper; direct URL content extraction.

## Inputs
None directly. Each adapter is called by `research.py` with a query string and result limit.

## Outputs
None directly. Each adapter returns a list of dicts with keys: url, title, content, source_type, fetched_at, metadata.

## Steps
N/A. This is a container for adapter modules, not a workflow itself.

## Dependencies
- `requests` — HTTP client for all API-based adapters.
- `feedparser` — RSS parsing for rss_source.py.
- `trafilatura` — Content extraction for scraper_source.py.
- `pyyaml` — RSS config loading in rss_source.py.
- `skills/web-research/config/rss_feeds.yaml` — Feed list consumed by rss_source.py.

## Known Issues
- None. All planned adapters are now active.

## Revision History
- 2026-05-29 — Initial creation. Nine adapters added: wikipedia, hackernews, reddit, arxiv, semantic_scholar, stackexchange, devto, rss, scraper.
- 2026-05-29 — Step 2: tavily_source.py added. TAVILY_API_KEY read from .env via python-dotenv.
- 2026-05-29 — Step 3: brave_source.py added. BRAVE_API_KEY read from .env.
- 2026-05-29 — Step 4: guardian_source.py added. GUARDIAN_API_KEY read from .env. All planned adapters now active.
- 2026-05-29 — Step 5: all adapters updated to raise typed exceptions (QuotaExceededError, AuthError, SourceUnavailableError) from exceptions.py. check_response() shared helper used where applicable.
