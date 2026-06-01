"""Direct web scraper source adapter — trafilatura, no key required."""
from datetime import datetime, timezone

try:
    import trafilatura
except ImportError:
    raise ImportError('trafilatura is required: pip install trafilatura')


def fetch(query, max_results=5, urls=None):
    """
    Scrape specific URLs for their main text content.

    This adapter is passive — it only runs when urls are explicitly passed.
    It does not perform any search; it fetches and extracts content from
    the provided URLs.

    Args:
        query (str): Included in metadata for traceability.
        max_results (int): Max URLs to process.
        urls (list): URLs to scrape. Required — returns [] if not provided.

    Returns:
        list: Result dicts.
    """
    if not urls:
        return []

    results = []
    for url in urls[:max_results]:
        try:
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                print(f'  [WARNING] Scraper: could not download {url}')
                continue

            content = trafilatura.extract(
                downloaded,
                include_comments=False,
                include_tables=True,
                no_fallback=False,
            )
            if not content:
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
