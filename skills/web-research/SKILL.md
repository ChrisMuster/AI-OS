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
Install once:
```bash
pip install -r skills/web-research/scripts/requirements.txt
```

For keyed sources, set environment variables (or use a `.env` file in the project root):
```
TAVILY_API_KEY=...       # ✓ configured
BRAVE_API_KEY=...        # ✓ configured
GUARDIAN_API_KEY=...     # ✓ configured
```
