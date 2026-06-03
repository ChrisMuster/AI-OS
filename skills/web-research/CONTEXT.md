# Web Research (Skill)

**Last modified:** 2026-05-29

## Purpose
Shared research engine for Book Dragon. Fetches content from multiple free and paid web sources, deduplicates and tiers the results by credibility, and returns a structured research package. Any workflow that needs to research a topic before acting imports this skill rather than building its own search logic.

## Contents
- SKILL.md — `skills/web-research/SKILL.md` [[skills/web-research/SKILL]] — Full API reference and usage guide for calling this skill from other workflows.
- scripts/research.py — `skills/web-research/scripts/research.py` [[skills/web-research/scripts/CONTEXT]] — Main importable entry point: `from research import research`.
- scripts/compile.py — `skills/web-research/scripts/compile.py` [[skills/web-research/scripts/CONTEXT]] — Deduplication, tier assignment, corroboration scoring, and package assembly.
- scripts/exceptions.py — `skills/web-research/scripts/exceptions.py` [[skills/web-research/scripts/CONTEXT]] — Typed exception classes (QuotaExceededError, AuthError, SourceUnavailableError) and the shared check_response() helper used by all source adapters.
- scripts/sources/ — `skills/web-research/scripts/sources/` [[skills/web-research/scripts/sources/CONTEXT]] — One adapter per source (tavily, brave, guardian, wikipedia, hackernews, reddit, arxiv, semantic_scholar, stackexchange, devto, rss, scraper).
- scripts/requirements.txt — `skills/web-research/scripts/requirements.txt` [[skills/web-research/scripts/CONTEXT]] — Python dependencies for the skill.
- config/rss_feeds.yaml — `skills/web-research/config/rss_feeds.yaml` [[skills/web-research/config/CONTEXT]] — RSS feed URLs organised by category.

## Inputs
- `topic` (str, required) — The research question or subject.
- `sources` (int, optional) — Max number of sources to include. Default: 8.
- `include` (list, optional) — Source names to query. Defaults to all enabled sources.
- `exclude` (list, optional) — Source names to skip.
- `rss_category` (str, optional) — Category key from rss_feeds.yaml.
- `scrape_urls` (list, optional) — Specific URLs to scrape directly.
- `**kwargs` — Any additional metadata to embed in the research package (content_type, words, tone, audience, etc.).

## Outputs
A research package dict with the following structure:
- `topic`, `generated_at`, `query_params`
- `source_count`, `tier_summary`, `corroboration`
- `sources[]` — Each entry: url, title, content (≤3000 chars), source_type, tier (1–4), tier_label, fetched_at, metadata.

When called via the workflow CLI, the package is also saved as a JSON file in `workflows/web-research/outputs/` [[workflows/web-research/outputs/CONTEXT]].

## Steps
N/A. This is a shared skill module, not a standalone workflow. See `workflows/web-research/` [[workflows/web-research/CONTEXT]] for the user-facing CLI wrapper that orchestrates the full research-to-report flow.

## Dependencies
- Python 3.8+ with packages listed in `skills/web-research/scripts/requirements.txt` [[skills/web-research/scripts/CONTEXT]].
- Free sources (no keys required): wikipedia, hackernews, reddit, arxiv, semantic_scholar, stackexchange, devto, rss, scraper.
- Keyed sources (API keys in `.env` at project root): tavily (TAVILY_API_KEY), brave (BRAVE_API_KEY), guardian (GUARDIAN_API_KEY). All three are active.
- `workflows/web-research/config/rss_feeds.yaml` is not used by this skill directly — RSS config lives at `skills/web-research/config/rss_feeds.yaml` [[skills/web-research/config/CONTEXT]].

## Known Issues
- Semantic Scholar rate-limits unauthenticated requests. If it 429s frequently, either add an API key (free) or exclude it via `--exclude semantic_scholar`.
- Reddit's public JSON endpoint occasionally returns 429 if queried rapidly. The scraper uses a User-Agent header to mitigate this.
- RSS feed URLs in rss_feeds.yaml may go stale over time and will need periodic updating.
- Corroboration scoring in compile.py is currently a proxy (high-tier source ratio) rather than true claim-level analysis. Claim-level verification is handled by Biblio when writing the report.

## Revision History
- 2026-05-29 — Initial creation. Step 1: all free sources implemented (wikipedia, hackernews, reddit, arxiv, semantic_scholar, stackexchange, devto, rss, scraper).
- 2026-05-29 — Step 2: tavily_source.py added. TAVILY_API_KEY wired via .env. Tavily registered as default Tier 1 source.
- 2026-05-29 — Step 3: brave_source.py added. BRAVE_API_KEY wired via .env. Brave registered as default Tier 2 source.
- 2026-05-29 — Step 4: guardian_source.py added. GUARDIAN_API_KEY wired via .env. All 12 sources now active.
- 2026-05-29 — Step 5: exceptions.py added with typed exception classes and check_response() helper. All source adapters updated to raise typed exceptions. research.py updated to track source_status and quality_flags per run.
