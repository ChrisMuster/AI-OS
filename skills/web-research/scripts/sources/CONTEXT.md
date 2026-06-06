# Web Research — sources/

**Last modified:** 2026-06-06

## Purpose
Source adapter modules for the web research skill. Each file exposes a single `fetch(query, max_results, **kwargs)` function that queries one data source and returns a list of result dicts. All adapters produce the same output shape so `compile.py` can process them uniformly.

## Contents
- tavily_source.py — `skills/web-research/scripts/sources/tavily_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Tavily Search API; best general research source, clean content extraction (requires TAVILY_API_KEY).
- brave_source.py — `skills/web-research/scripts/sources/brave_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Brave Search API; broad web search, good complement to Tavily (requires BRAVE_API_KEY).
- guardian_source.py — `skills/web-research/scripts/sources/guardian_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Guardian Content API; quality long-form journalism with full article text (requires GUARDIAN_API_KEY).
- wikipedia_source.py — `skills/web-research/scripts/sources/wikipedia_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — MediaWiki API; encyclopaedic background.
- hackernews_source.py — `skills/web-research/scripts/sources/hackernews_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Algolia HN Search API; tech community discussion.
- reddit_source.py — `skills/web-research/scripts/sources/reddit_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Reddit public JSON API; community opinions.
- arxiv_source.py — `skills/web-research/scripts/sources/arxiv_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — ArXiv Atom API; academic papers.
- semantic_scholar_source.py — `skills/web-research/scripts/sources/semantic_scholar_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Semantic Scholar Academic Graph API; broad academic search.
- stackexchange_source.py — `skills/web-research/scripts/sources/stackexchange_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Stack Exchange API v2.3; technical Q&A.
- devto_source.py — `skills/web-research/scripts/sources/devto_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — Dev.to public API; developer community articles.
- rss_source.py — `skills/web-research/scripts/sources/rss_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — feedparser-based RSS reader; curated news feeds.
- scraper_source.py — `skills/web-research/scripts/sources/scraper_source.py` [[skills/web-research/scripts/sources/CONTEXT]] — trafilatura-based scraper; direct URL content extraction. Accepts optional `subscribed_domains` parameter — when a paywalled response is detected for a subscribed domain, returns a flagged entry rather than silently discarding the URL.

## Inputs
None directly. Each adapter is called by `research.py` with a query string and result limit.

## Outputs
None directly. Each adapter returns a list of dicts with keys: url, title, content, source_type, fetched_at, metadata. scraper_source.py may additionally return entries with `source_type: 'paywalled_subscribed'`; these are separated out by `research.py` and stored in the `paywalled_urls` field of the research package.

## Steps
N/A. This is a container for adapter modules, not a workflow itself.

## Dependencies
- `requests` — HTTP client for all API-based adapters.
- `feedparser` — RSS parsing for rss_source.py.
- `trafilatura` — Content extraction for scraper_source.py.
- `pyyaml` — RSS config loading in rss_source.py.
- `skills/web-research/config/rss_feeds.yaml` [[skills/web-research/config/CONTEXT]] — Feed list consumed by rss_source.py.

## Known Issues
- All planned adapters are now active. Paywalled content detection in scraper_source.py depends on `SUBSCRIBED_DOMAINS` in `.env`; if that variable is absent or empty, paywalled URLs are silently discarded as before.

## Revision History
- 2026-05-29 — Initial creation. Nine adapters added: wikipedia, hackernews, reddit, arxiv, semantic_scholar, stackexchange, devto, rss, scraper.
- 2026-05-29 — Step 2: tavily_source.py added. TAVILY_API_KEY read from .env via python-dotenv.
- 2026-05-29 — Step 3: brave_source.py added. BRAVE_API_KEY read from .env.
- 2026-05-29 — Step 4: guardian_source.py added. GUARDIAN_API_KEY read from .env. All planned adapters now active.
- 2026-05-29 — Step 5: all adapters updated to raise typed exceptions (QuotaExceededError, AuthError, SourceUnavailableError) from exceptions.py. check_response() shared helper used where applicable.
- 2026-06-06 — scraper_source.py updated: added `subscribed_domains` parameter and paywall detection. Paywalled URLs from subscribed sites returned as flagged entries rather than silently discarded.
