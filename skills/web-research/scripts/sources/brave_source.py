"""Brave Search source adapter — Brave Search API, requires BRAVE_API_KEY in .env."""
import os
from pathlib import Path
from datetime import datetime, timezone

import sys
import requests

# Load .env from the project root (four levels up from this file)
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
    load_dotenv(_PROJECT_ROOT / '.env')
except ImportError:
    pass  # python-dotenv not installed; fall back to os.environ

sys.path.insert(0, str(Path(__file__).parent.parent))
from exceptions import check_response

SEARCH_URL = 'https://api.search.brave.com/res/v1/web/search'


def fetch(query, max_results=5):
    """
    Fetch web search results from Brave Search.

    Brave provides broad general web search — a good complement to Tavily for
    topics where wider coverage matters. Results include title, URL, and
    description snippets.

    Requires BRAVE_API_KEY set in .env or the environment.
    """
    api_key = os.environ.get('BRAVE_API_KEY')
    if not api_key:
        raise RuntimeError(
            'BRAVE_API_KEY not found. Set it in .env at the project root.'
        )

    headers = {
        'Accept': 'application/json',
        'Accept-Encoding': 'gzip',
        'X-Subscription-Token': api_key,
    }
    params = {
        'q': query,
        'count': min(max_results, 20),  # Brave API max is 20 per request
        'safesearch': 'moderate',
        'text_decorations': 'false',
        'spellcheck': 'true',
    }

    try:
        resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=10)
        check_response(resp, 'Brave Search')
        resp.raise_for_status()
        data = resp.json()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Brave Search failed: {exc}')

    results = []
    for item in data.get('web', {}).get('results', [])[:max_results]:
        title = item.get('title', '')
        url = item.get('url', '')
        description = item.get('description', '')

        # Extra snippets give additional context beyond the main description
        extra = item.get('extra_snippets', [])
        content_parts = [description] + extra[:3]
        content = '\n\n'.join(p for p in content_parts if p)

        age = item.get('age', '')  # e.g. "2 days ago", "May 2026"

        results.append({
            'url': url,
            'title': title,
            'content': content,
            'source_type': 'brave',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'age': age,
                'language': item.get('language', ''),
            },
        })

    return results
