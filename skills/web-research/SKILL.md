# Web Research — SKILL.md

## Purpose
Fetch and package web research on any topic. Returns a structured dict containing tiered, deduplicated sources with credibility scores and corroboration data. Any workflow in Book Dragon can call this instead of building its own search logic.

---

## Quick start

### From another workflow script
```python
import sys
from pathlib import Path

# Add the skill scripts directory to path
sys.path.insert(0, str(Path('skills/web-research/scripts')))
from research import research

package = research(
    topic="UK AI regulation 2026",
    sources=8,
    include=['wikipedia', 'arxiv', 'rss'],
    rss_category='ai-news',
)
```

### Via the CLI (user-facing workflow)
```bash
python workflows/web-research/scripts/run.py --topic "UK AI regulation 2026" --words 1500 --type article
```

---

## Function signature

```python
research(
    topic,              # str, required
    sources=8,          # int — max sources in the package
    include=None,       # list[str] — source names to query; None = all enabled
    exclude=None,       # list[str] — source names to skip
    rss_category=None,  # str — key from skills/web-research/config/rss_feeds.yaml
    scrape_urls=None,   # list[str] — URLs for the direct scraper
    **kwargs            # passed through to package metadata (tone, words, etc.)
)
```

Returns: `dict` (research package — see structure below)

---

## Return structure

```json
{
  "topic": "UK AI regulation 2026",
  "generated_at": "2026-05-29T14:00:00+00:00",
  "query_params": { "max_sources": 8, "tone": "journalistic" },
  "source_count": 8,
  "tier_summary": { "high": 2, "medium": 3, "low": 3 },
  "corroboration": {
    "score": 0.62,
    "high_tier_sources": 5,
    "total_sources": 8,
    "note": "Good corroboration — majority of sources are Tier 1 or 2."
  },
  "sources": [
    {
      "url": "https://...",
      "title": "...",
      "content": "... (max 3000 chars)",
      "source_type": "arxiv",
      "tier": 1,
      "tier_label": "high",
      "fetched_at": "2026-05-29T14:00:01+00:00",
      "metadata": { "authors": ["..."], "published": "..." }
    }
  ]
}
```

---

## Source reference

| Name | Tier | Key required | Notes |
|---|---|---|---|
| `wikipedia` | 2 — medium | No | Encyclopaedic background; good for definitions and context |
| `arxiv` | 1 — high | No | Academic papers; best for science, AI, CS topics |
| `semantic_scholar` | 1 — high | No | Broad academic search; free, may rate-limit |
| `stackexchange` | 2 — medium | No | Technical Q&A; useful for how-to and practical topics |
| `hackernews` | 3 — low | No | Tech community discussion; high signal for tech topics |
| `rss` | 2 — medium | No | Curated news; configure categories in rss_feeds.yaml |
| `devto` | 3 — low | No | Developer community articles |
| `reddit` | 3 — low | No | Community opinions and experience reports |
| `scraper` | 4 — unverified | No | Direct URL scraping; only activates when `scrape_urls` passed |
| `tavily` | 1 — high | Yes ✓ | Best general research API; clean content extraction |
| `brave` | 2 — medium | Yes ✓ | Broad web search; good complement to Tavily |
| `guardian` | 1 — high | Yes ✓ | Quality long-form journalism; full article text; 5,000 free calls/day |

---

## Credibility tiers

| Tier | Label | Meaning | Confidence marker in report |
|---|---|---|---|
| 1 | high | Academic papers, major institutions, quality press | `[confirmed]` (if 2+ Tier 1 sources) |
| 2 | medium | Wikipedia, Stack Exchange, established tech press | `[reported]` |
| 3 | low | Reddit, HN, blogs — useful context, needs corroboration | `[reported]` (with caution note if sole source) |
| 4 | unverified | Direct scrapes of unknown sites | `[unverified]` |

---

## Dependencies
Set up the canonical Book Dragon runtime once:
```bash
python workflows/biblio-tools/scripts/setup.py
```

For keyed sources, set environment variables (or use a `.env` file in the project root):
```
TAVILY_API_KEY=...       # ✓ configured
BRAVE_API_KEY=...        # ✓ configured
GUARDIAN_API_KEY=...     # ✓ configured
```

---

## Verification

`research(...)` returns a package dict that is internally consistent: `source_count` equals the length of `sources`, `tier_summary` counts add up to `source_count`, and `corroboration` reflects the actual tier mix. A correct run cites only sources that were really fetched, each with its URL, tier, and `fetched_at`.

A failed check looks like a package that claims sources it did not fetch, a `source_count` that disagrees with the `sources` list, or an unhandled crash when a source is down (a healthy run degrades with a typed unavailable error and returns the sources it did reach). The engine's behaviour is exercised by the web-research workflow test suite (`python workflows/web-research/tests/run_tests.py`); a red run means the package cannot be trusted.

---

## Hardening
Safety envelope for this skill. All five fields are required.

- **Allowed tool intent:** Outbound HTTPS to the configured research sources; read-only access to the skill's own config (`skills/web-research/config/`) and to API keys from the environment or `.env`. Returns data to the caller.
- **Never:** Log, echo, or write out API keys or secrets; fabricate sources, URLs, or citations that a real fetch did not return.
- **Approval-gated:** None for the read-only fetches themselves. Any workflow that persists a report is responsible for its own writes and permission under that workflow's rules.
- **Write boundaries:** The skill returns a dict and writes no project files. A report file, when produced, is written by the calling workflow (e.g. `workflows/web-research/scripts/run.py`) inside that workflow's own output area, not by this engine.
- **Verification / escape hatch:** A reviewer confirms the returned counts, tiers, and corroboration match the `sources` list (see Verification). When a source fails it degrades gracefully with a typed unavailable error rather than inventing content; a keyed source with no key is simply skipped, not faked.
