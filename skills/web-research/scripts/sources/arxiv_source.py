"""ArXiv source adapter — ArXiv Atom API, no key required."""
import re
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

ARXIV_URL = 'https://export.arxiv.org/api/query'
NS = {'atom': 'http://www.w3.org/2005/Atom'}


def fetch(query, max_results=5):
    """Fetch ArXiv papers matching query. Returns list of result dicts."""
    params = {
        'search_query': f'all:{query}',
        'max_results': max_results,
        'sortBy': 'relevance',
        'sortOrder': 'descending',
    }

    try:
        resp = requests.get(ARXIV_URL, params=params, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
    except Exception as exc:
        raise RuntimeError(f'ArXiv search failed: {exc}')

    results = []
    for entry in root.findall('atom:entry', NS)[:max_results]:
        title = _text(entry.find('atom:title', NS))
        abstract = _text(entry.find('atom:summary', NS))
        arxiv_url = _text(entry.find('atom:id', NS))
        published = _text(entry.find('atom:published', NS))
        authors = [
            _text(a.find('atom:name', NS))
            for a in entry.findall('atom:author', NS)
        ]

        year = published[:4] if published else ''
        display_title = f'{title} ({year})' if year else title

        results.append({
            'url': arxiv_url,
            'title': display_title,
            'content': abstract,
            'source_type': 'arxiv',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'authors': authors[:5],
                'published': published,
            },
        })

    return results


def _text(el):
    if el is None:
        return ''
    return re.sub(r'\s+', ' ', el.text or '').strip()
