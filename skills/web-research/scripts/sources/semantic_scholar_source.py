"""Semantic Scholar source adapter — Academic Graph API, no key required for basic use."""
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from exceptions import check_response

BASE_URL = 'https://api.semanticscholar.org/graph/v1/paper/search'
HEADERS = {'User-Agent': 'BookDragon/1.0'}


def fetch(query, max_results=5):
    """Fetch academic papers from Semantic Scholar. Returns list of result dicts."""
    params = {
        'query': query,
        'limit': max_results,
        'fields': 'title,abstract,year,authors,url,externalIds',
    }

    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=15)
        check_response(resp, 'Semantic Scholar')
        resp.raise_for_status()
        papers = resp.json().get('data', [])
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Semantic Scholar search failed: {exc}')

    results = []
    for paper in papers[:max_results]:
        title = paper.get('title', '')
        abstract = paper.get('abstract') or ''
        year = paper.get('year', '')
        url = paper.get('url', '')

        if not url:
            paper_id = paper.get('paperId', '')
            url = f'https://www.semanticscholar.org/paper/{paper_id}' if paper_id else ''

        authors = [a.get('name', '') for a in paper.get('authors', [])[:5]]
        display_title = f'{title} ({year})' if year else title

        results.append({
            'url': url,
            'title': display_title,
            'content': abstract,
            'source_type': 'semantic_scholar',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'year': year,
                'authors': authors,
            },
        })

    return results
