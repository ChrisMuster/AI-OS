"""
Core research engine for Book Dragon.
Importable by any workflow: from research import research

Usage from another workflow:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path('skills/web-research/scripts')))
    from research import research
    package = research(topic="your topic here", sources=8)
"""
import os
import sys
from pathlib import Path

# Load .env from the project root before any source adapters run
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent
    load_dotenv(_PROJECT_ROOT / '.env')
except ImportError:
    pass  # python-dotenv not installed; fall back to environment variables

# Ensure sources/ and compile are importable when called from anywhere
sys.path.insert(0, str(Path(__file__).parent))

from sources.tavily_source import fetch as fetch_tavily
from sources.brave_source import fetch as fetch_brave
from sources.guardian_source import fetch as fetch_guardian
from sources.wikipedia_source import fetch as fetch_wikipedia
from sources.hackernews_source import fetch as fetch_hackernews
from sources.reddit_source import fetch as fetch_reddit
from sources.arxiv_source import fetch as fetch_arxiv
from sources.semantic_scholar_source import fetch as fetch_semantic_scholar
from sources.stackexchange_source import fetch as fetch_stackexchange
from sources.devto_source import fetch as fetch_devto
from sources.rss_source import fetch as fetch_rss
from sources.scraper_source import fetch as fetch_scraper
from compile import compile_research_package
from exceptions import QuotaExceededError, AuthError, SourceUnavailableError

# Domains the user has paid subscriptions to.
# Loaded from SUBSCRIBED_DOMAINS in .env (comma-separated list).
# When the scraper encounters paywalled content from these domains, it returns
# a flagged entry rather than silently discarding the URL.
_subscribed_domains = set(
    d.strip().lower()
    for d in os.environ.get('SUBSCRIBED_DOMAINS', '').split(',')
    if d.strip()
)

# Registry: source name -> (fetch_function, enabled_by_default)
# Keyed sources auto-enable when their environment variable is present.
SOURCE_REGISTRY = {
    'tavily':           (fetch_tavily,           bool(os.environ.get('TAVILY_API_KEY'))),
    'guardian':         (fetch_guardian,         bool(os.environ.get('GUARDIAN_API_KEY'))),
    'brave':            (fetch_brave,            bool(os.environ.get('BRAVE_API_KEY'))),
    'wikipedia':        (fetch_wikipedia,        True),
    'arxiv':            (fetch_arxiv,             True),
    'semantic_scholar': (fetch_semantic_scholar,  True),
    'stackexchange':    (fetch_stackexchange,     True),
    'hackernews':       (fetch_hackernews,        True),
    'rss':              (fetch_rss,               True),
    'devto':            (fetch_devto,             True),
    'reddit':           (fetch_reddit,            True),
    'scraper':          (fetch_scraper,           False),  # only when urls supplied
}


def research(
    topic,
    sources=8,
    include=None,
    exclude=None,
    rss_category=None,
    scrape_urls=None,
    **kwargs
):
    """
    Run web research on a topic and return a research package dict.

    Args:
        topic (str): The research topic or question.
        sources (int): Max number of sources to include in the package.
        include (list): Source names to query. None = all enabled defaults.
        exclude (list): Source names to skip.
        rss_category (str): RSS feed category from rss_feeds.yaml.
        scrape_urls (list): Specific URLs to pass to the scraper.
        **kwargs: Passed through to the research package metadata
                  (e.g. content_type, words, tone, audience, style).

    Returns:
        dict: Research package with metadata, sources, and corroboration data.
    """
    include = [s.lower() for s in include] if include else None
    exclude = [s.lower() for s in exclude] if exclude else []

    active_sources = _resolve_sources(include, exclude, bool(scrape_urls))

    # Spread the per-source limit so we gather enough to hit the target after dedup
    per_source_limit = max(3, (sources // max(len(active_sources), 1)) + 2)

    raw_results = []

    # Track what happened to each source so the package is fully auditable
    source_status = {
        'succeeded': [],
        'quota_exceeded': [],
        'auth_error': [],
        'unavailable': [],
        'failed': [],
    }
    quality_flags = []

    for source_name in active_sources:
        fetch_fn, _ = SOURCE_REGISTRY[source_name]
        print(f'  Querying {source_name}…')
        try:
            if source_name == 'rss':
                results = fetch_fn(topic, max_results=per_source_limit, category=rss_category)
            elif source_name == 'scraper':
                results = fetch_fn(topic, max_results=per_source_limit, urls=scrape_urls or [],
                                   subscribed_domains=_subscribed_domains)
            else:
                results = fetch_fn(topic, max_results=per_source_limit)
            raw_results.extend(results)
            source_status['succeeded'].append(source_name)

        except QuotaExceededError as exc:
            source_status['quota_exceeded'].append(source_name)
            quality_flags.append(
                f'QUOTA_EXCEEDED | {source_name} | {exc}'
            )
            print(f'  [! QUOTA EXCEEDED] {source_name}: {exc}')

        except AuthError as exc:
            source_status['auth_error'].append(source_name)
            quality_flags.append(
                f'AUTH_ERROR | {source_name} | {exc} — check .env'
            )
            print(f'  [X AUTH ERROR] {source_name}: {exc}')

        except SourceUnavailableError as exc:
            source_status['unavailable'].append(source_name)
            quality_flags.append(
                f'UNAVAILABLE | {source_name} | {exc}'
            )
            print(f'  [! UNAVAILABLE] {source_name}: {exc}')

        except Exception as exc:
            source_status['failed'].append(source_name)
            print(f'  [WARNING] {source_name} failed: {exc}')

    # Separate paywalled entries from regular results before compiling.
    # These are subscribed-domain URLs the scraper could not read; they are
    # preserved for the user to fetch manually rather than silently discarded.
    paywalled_raw = [r for r in raw_results if r.get('source_type') == 'paywalled_subscribed']
    raw_results    = [r for r in raw_results if r.get('source_type') != 'paywalled_subscribed']

    package = compile_research_package(
        topic=topic,
        raw_results=raw_results,
        max_sources=sources,
        kwargs=kwargs,
    )

    # Attach full source audit to the package
    package['source_status'] = source_status
    package['quality_flags'] = quality_flags

    # Paywalled URLs from subscribed domains — flagged for manual retrieval
    package['paywalled_urls'] = [
        {
            'url':    r['url'],
            'title':  r.get('title', r['url']),
            'domain': r.get('domain', ''),
        }
        for r in paywalled_raw
    ]

    return package


def _resolve_sources(include, exclude, has_scrape_urls):
    if include:
        valid = [s for s in include if s in SOURCE_REGISTRY]
        return [s for s in valid if s not in exclude]

    resolved = []
    for name, (_, default_on) in SOURCE_REGISTRY.items():
        if name in exclude:
            continue
        if not default_on and name == 'scraper' and not has_scrape_urls:
            continue
        if default_on:
            resolved.append(name)
    if has_scrape_urls and 'scraper' not in resolved:
        resolved.append('scraper')
    return resolved
