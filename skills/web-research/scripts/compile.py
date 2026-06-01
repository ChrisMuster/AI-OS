"""
Assembles raw source results into a verified, tiered research package.
Called by research.py after all source adapters have run.
"""
from datetime import datetime, timezone
from collections import defaultdict

# Credibility tiers — lower is better.
# Sources added in later steps (tavily, brave, guardian) are pre-registered here
# so tier assignment works as soon as they are wired in.
SOURCE_TIERS = {
    'arxiv':            1,
    'semantic_scholar': 1,
    'tavily':           1,  # Step 2
    'guardian':         1,  # Step 4
    'wikipedia':        2,
    'stackexchange':    2,
    'rss':              2,
    'brave':            2,  # Step 3
    'hackernews':       3,
    'devto':            3,
    'reddit':           3,
    'scraper':          4,
}

TIER_LABELS = {1: 'high', 2: 'medium', 3: 'low', 4: 'unverified'}


def compile_research_package(topic, raw_results, max_sources=8, kwargs=None):
    """
    Deduplicate, tier, score, and trim raw results into a research package dict.

    Args:
        topic (str): The research topic.
        raw_results (list): Raw dicts from all source adapters.
        max_sources (int): Max entries to include in the final package.
        kwargs (dict): Additional metadata to embed (tone, words, etc.).

    Returns:
        dict: Research package.
    """
    kwargs = kwargs or {}

    deduped = _deduplicate(raw_results)

    for result in deduped:
        tier = SOURCE_TIERS.get(result.get('source_type', 'scraper'), 4)
        result['tier'] = tier
        result['tier_label'] = TIER_LABELS.get(tier, 'unverified')

    # Best sources first: tier asc, then preserve original order within tier
    deduped.sort(key=lambda x: x.get('tier', 4))

    selected = deduped[:max_sources]

    for result in selected:
        result['content'] = _trim_content(result.get('content', ''), max_chars=3000)

    corroboration = _compute_corroboration(selected)

    return {
        'topic': topic,
        'generated_at': datetime.now(timezone.utc).isoformat(),
        'query_params': {'max_sources': max_sources, **kwargs},
        'source_count': len(selected),
        'tier_summary': _tier_summary(selected),
        'corroboration': corroboration,
        'sources': selected,
    }


def _deduplicate(results):
    seen_urls = set()
    out = []
    for r in results:
        url = r.get('url', '').rstrip('/')
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        out.append(r)
    return out


def _tier_summary(sources):
    counts = defaultdict(int)
    for s in sources:
        counts[s.get('tier_label', 'unverified')] += 1
    return dict(counts)


def _compute_corroboration(sources):
    """
    Proxy corroboration score: ratio of Tier 1/2 sources to total.

    A true claim-level analysis requires NLP beyond what a script can
    reliably do — Biblio applies claim-level confidence markers when
    writing the report using this metadata as context.
    """
    if len(sources) < 2:
        return {
            'score': 0.0,
            'high_tier_sources': 0,
            'total_sources': len(sources),
            'note': 'Insufficient sources for corroboration assessment.',
        }

    high_tier = sum(1 for s in sources if s.get('tier', 4) <= 2)
    total = len(sources)
    score = round(high_tier / total, 2)

    if score >= 0.6:
        note = 'Good corroboration — majority of sources are Tier 1 or 2.'
    elif score >= 0.3:
        note = 'Moderate corroboration — mix of source tiers. Treat low-tier claims carefully.'
    else:
        note = 'Low corroboration — most sources are low-tier. Verify key claims independently.'

    return {
        'score': score,
        'high_tier_sources': high_tier,
        'total_sources': total,
        'note': note,
    }


def _trim_content(text, max_chars=3000):
    if len(text) <= max_chars:
        return text
    trimmed = text[:max_chars]
    last_period = trimmed.rfind('.')
    if last_period > int(max_chars * 0.8):
        return trimmed[:last_period + 1]
    return trimmed + '…'
