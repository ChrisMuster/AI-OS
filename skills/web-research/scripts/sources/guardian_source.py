"""The Guardian source adapter — Guardian Content API, requires GUARDIAN_API_KEY in .env."""
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

SEARCH_URL = 'https://content.guardianapis.com/search'


def fetch(query, max_results=5):
    """
    Fetch articles from The Guardian Content API.

    Returns full article trail text and, where available, body text.
    The Guardian is Tier 1 — quality long-form journalism with editorial
    standards. Particularly strong for UK news, politics, science, technology,
    and culture.

    Requires GUARDIAN_API_KEY set in .env or the environment.
    Free tier: 5,000 calls/day.
    """
    api_key = os.environ.get('GUARDIAN_API_KEY')
    if not api_key:
        raise RuntimeError(
            'GUARDIAN_API_KEY not found. Set it in .env at the project root.'
        )

    params = {
        'q': query,
        'api-key': api_key,
        'page-size': min(max_results, 50),  # API max is 50
        'show-fields': 'trailText,bodyText,headline,byline,wordcount',
        'order-by': 'relevance',
        'show-tags': 'keyword',
    }

    try:
        resp = requests.get(SEARCH_URL, params=params, timeout=10)
        check_response(resp, 'Guardian')
        resp.raise_for_status()
        data = resp.json()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Guardian API search failed: {exc}')

    items = data.get('response', {}).get('results', [])
    results = []

    for item in items[:max_results]:
        fields = item.get('fields', {})
        title = fields.get('headline') or item.get('webTitle', '')
        url = item.get('webUrl', '')
        published = item.get('webPublicationDate', '')
        byline = fields.get('byline', '')
        trail_text = fields.get('trailText', '')
        body_text = fields.get('bodyText', '')
        wordcount = fields.get('wordcount', '')

        # Use body text where available; fall back to trail text
        content = body_text[:3000] if body_text else trail_text

        tags = [t.get('webTitle', '') for t in item.get('tags', [])[:5]]

        results.append({
            'url': url,
            'title': title,
            'content': content,
            'source_type': 'guardian',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'published': published,
                'byline': byline,
                'wordcount': wordcount,
                'tags': tags,
                'section': item.get('sectionName', ''),
            },
        })

    return results
