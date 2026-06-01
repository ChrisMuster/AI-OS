"""Stack Exchange source adapter — public API v2.3, no key required (300 req/day)."""
import html
import re
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from exceptions import check_response

BASE_URL = 'https://api.stackexchange.com/2.3/search/advanced'


def fetch(query, max_results=5, site='stackoverflow'):
    """
    Fetch Stack Exchange questions matching query.
    Tries stackoverflow first; falls back to broader stackexchange.com sites
    if fewer than max_results are returned.
    """
    results = _query_site(query, max_results, site)

    # If we got very few results from stackoverflow, also try superuser
    if len(results) < 2 and site == 'stackoverflow':
        results += _query_site(query, max_results - len(results), 'superuser')

    return results[:max_results]


def _query_site(query, max_results, site):
    params = {
        'q': query,
        'site': site,
        'pagesize': max_results,
        'sort': 'relevance',
        'order': 'desc',
        'filter': 'withbody',
    }

    try:
        resp = requests.get(BASE_URL, params=params, timeout=10)
        check_response(resp, f'Stack Exchange ({site})')
        resp.raise_for_status()
        items = resp.json().get('items', [])
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Stack Exchange ({site}) search failed: {exc}')

    results = []
    for q in items[:max_results]:
        title = html.unescape(q.get('title', ''))
        body = _clean_html(q.get('body') or '')
        link = q.get('link', '')
        score = q.get('score', 0)
        is_answered = q.get('is_answered', False)
        answer_count = q.get('answer_count', 0)

        content = (
            f'Score: {score} | Answered: {is_answered} | Answers: {answer_count}\n\n'
            + body[:2000]
        )

        results.append({
            'url': link,
            'title': title,
            'content': content,
            'source_type': 'stackexchange',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'site': site,
                'score': score,
                'is_answered': is_answered,
                'answer_count': answer_count,
                'tags': q.get('tags', []),
            },
        })

    return results


def _clean_html(text):
    text = re.sub(r'<[^>]+>', ' ', text)
    text = html.unescape(text)
    return re.sub(r'\s+', ' ', text).strip()
