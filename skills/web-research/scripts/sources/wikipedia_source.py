"""Wikipedia source adapter — MediaWiki API, no key required."""
import os
import re
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()
BASE_URL = 'https://en.wikipedia.org/w/api.php'
_contact = os.getenv('USER_EMAIL', 'contact@example.com')
HEADERS = {'User-Agent': f'BookDragon/1.0 ({_contact})'}


def fetch(query, max_results=5):
    """Fetch Wikipedia articles matching query. Returns list of result dicts."""
    search_params = {
        'action': 'query',
        'list': 'search',
        'srsearch': query,
        'srlimit': max_results,
        'format': 'json',
        'utf8': 1,
    }

    try:
        resp = requests.get(BASE_URL, params=search_params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get('query', {}).get('search', [])
    except Exception as exc:
        raise RuntimeError(f'Wikipedia search failed: {exc}')

    results = []
    for hit in hits[:max_results]:
        title = hit['title']
        snippet = _strip_html(hit.get('snippet', ''))
        content = _get_extract(title) or snippet

        results.append({
            'url': f'https://en.wikipedia.org/wiki/{title.replace(" ", "_")}',
            'title': title,
            'content': content,
            'source_type': 'wikipedia',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {'snippet': snippet},
        })

    return results


def _get_extract(title):
    params = {
        'action': 'query',
        'prop': 'extracts',
        'exintro': True,
        'explaintext': True,
        'titles': title,
        'format': 'json',
        'utf8': 1,
    }
    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        pages = resp.json().get('query', {}).get('pages', {})
        page = next(iter(pages.values()))
        return page.get('extract', '')
    except Exception:
        return ''


def _strip_html(text):
    return re.sub(r'<[^>]+>', '', text)
