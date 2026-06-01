"""Dev.to source adapter — public API, no key required."""
import requests
from datetime import datetime, timezone

BASE_URL = 'https://dev.to/api/articles'
HEADERS = {'User-Agent': 'BookDragon/1.0'}

# Common Dev.to tags for tag-based lookup
_KNOWN_TAGS = {
    'python', 'javascript', 'typescript', 'react', 'vue', 'node', 'css',
    'html', 'ai', 'machinelearning', 'webdev', 'programming', 'devops',
    'cloud', 'aws', 'docker', 'kubernetes', 'security', 'career', 'beginners',
    'productivity', 'opensource', 'rust', 'go', 'java', 'database', 'api',
}


def fetch(query, max_results=5):
    """Fetch Dev.to articles relevant to query. Returns list of result dicts."""
    tag = _query_to_tag(query)
    params = {'per_page': max_results * 3, 'top': 30}
    if tag:
        params['tag'] = tag

    try:
        resp = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        articles = resp.json()
    except Exception as exc:
        raise RuntimeError(f'Dev.to search failed: {exc}')

    # Score articles by keyword overlap with query
    query_words = set(query.lower().split())
    scored = []
    for article in articles:
        title_words = set((article.get('title') or '').lower().split())
        tag_words = set(t.lower() for t in (article.get('tag_list') or []))
        description_words = set((article.get('description') or '').lower().split())
        overlap = len(query_words & (title_words | tag_words | description_words))
        scored.append((overlap, article))

    scored.sort(key=lambda x: x[0], reverse=True)

    results = []
    for _, article in scored[:max_results]:
        title = article.get('title', '')
        description = article.get('description') or ''
        url = article.get('url', '')
        tags = article.get('tag_list', [])
        reading_time = article.get('reading_time_minutes', 0)
        reactions = article.get('public_reactions_count', 0)

        results.append({
            'url': url,
            'title': title,
            'content': description,
            'source_type': 'devto',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'tags': tags,
                'reading_time_minutes': reading_time,
                'reactions': reactions,
            },
        })

    return results


def _query_to_tag(query):
    """Extract a usable Dev.to tag from the query, or return None."""
    words = set(query.lower().replace('-', '').replace('_', '').split())
    match = words & _KNOWN_TAGS
    return match.pop() if match else None
