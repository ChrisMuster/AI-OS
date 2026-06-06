"""Direct web scraper source adapter — trafilatura, no key required."""
from datetime import datetime, timezone
from urllib.parse import urlparse

try:
    import trafilatura
except ImportError:
    raise ImportError('trafilatura is required: pip install trafilatura')


def _normalise_domain(url):
    """Return the netloc of a URL with any leading 'www.' stripped."""
    try:
        netloc = urlparse(url).netloc.lower()
        if netloc.startswith('www.'):
            netloc = netloc[4:]
        return netloc
    except Exception:
        return ''


def _is_subscribed(url, subscribed_domains):
    """Return True if the URL's domain is in the subscribed_domains set."""
    if not subscribed_domains:
        return False
    return _normalise_domain(url) in subscribed_domains


def fetch(query, max_results=5, urls=None, subscribed_domains=None):
    """
    Scrape specific URLs for their main text content.

    This adapter is passive — it only runs when urls are explicitly passed.
    It does not perform any search; it fetches and extracts content from
    the provided URLs.

    Args:
        query (str): Included in metadata for traceability.
        max_results (int): Max URLs to process.
        urls (list): URLs to scrape. Required — returns [] if not provided.
        subscribed_domains (set): Domains the user has subscriptions to.
            When a URL from one of these domains cannot be read (paywall),
            a paywalled entry is returned instead of silently skipping.

    Returns:
        list: Result dicts. Paywalled entries have source_type='paywalled_subscribed'
              and are separated out by research.py before compiling the package.
    """
    if not urls:
        return []

    subscribed_domains = set(subscribed_domains or [])

    results = []
    for url in urls[:max_results]:
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                if _is_subscribed(url, subscribed_domains):
                    results.append(_paywalled_entry(url, query))
                    print(f'  [~] Scraper: paywalled content detected — {url}')
                else:
                    print(f'  [WARNING] Scraper: could not download {url}')
                continue

            content = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if not content:
                if _is_subscribed(url, subscribed_domains):
                    # Try to recover a title even if content is empty
                    meta = trafilatura.extract_metadata(downloaded)
                    title = (meta.title if meta and meta.title else url)
                    results.append(_paywalled_entry(url, query, title=title))
                    print(f'  [~] Scraper: paywalled content detected — {url}')
                else:
                    print(f'  [WARNING] Scraper: no content extracted from {url}')
                continue

            meta = trafilatura.extract_metadata(downloaded)
            title = (meta.title if meta and meta.title else url)

            results.append({
                'url': url,
                'title': title,
                'content': content,
                'source_type': 'scraper',
                'fetched_at': datetime.now(timezone.utc).isoformat(),
                'metadata': {'query': query},
            })
        except Exception as exc:
            print(f'  [WARNING] Scraper failed for {url}: {exc}')
            continue

    return results


def _paywalled_entry(url, query, title=None):
    """Build a result dict for a paywalled URL from a subscribed domain."""
    return {
        'url': url,
        'title': title or url,
        'content': '',
        'source_type': 'paywalled_subscribed',
        'domain': _normalise_domain(url),
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'metadata': {'query': query},
    }
