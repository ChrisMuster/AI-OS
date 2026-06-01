"""Hacker News source adapter — Algolia HN Search API, no key required."""
import requests
from datetime import datetime, timezone

ALGOLIA_URL = 'https://hn.algolia.com/api/v1/search'


def fetch(query, max_results=5):
    """Fetch Hacker News stories matching query. Returns list of result dicts."""
    params = {
        'query': query,
        'tags': 'story',
        'hitsPerPage': max_results,
    }

    try:
        resp = requests.get(ALGOLIA_URL, params=params, timeout=10)
        resp.raise_for_status()
        hits = resp.json().get('hits', [])
    except Exception as exc:
        raise RuntimeError(f'Hacker News search failed: {exc}')

    results = []
    for hit in hits[:max_results]:
        title = hit.get('title', '')
        story_text = hit.get('story_text') or ''
        points = hit.get('points', 0)
        num_comments = hit.get('num_comments', 0)
        hn_url = f"https://news.ycombinator.com/item?id={hit.get('objectID', '')}"
        external_url = hit.get('url') or hn_url

        content_parts = []
        if story_text:
            content_parts.append(story_text[:2000])
        content_parts.append(f'Points: {points} | Comments: {num_comments}')
        if hit.get('url'):
            content_parts.append(f'External link: {hit["url"]}')

        results.append({
            'url': hn_url,
            'title': title,
            'content': '\n'.join(content_parts),
            'source_type': 'hackernews',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'external_url': external_url,
                'points': points,
                'num_comments': num_comments,
                'author': hit.get('author', ''),
                'created_at': hit.get('created_at', ''),
            },
        })

    return results
