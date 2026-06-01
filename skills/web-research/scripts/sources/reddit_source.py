"""Reddit source adapter — public JSON API, no key required."""
import os
import sys
import requests
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
sys.path.insert(0, str(Path(__file__).parent.parent))
from exceptions import check_response

SEARCH_URL = 'https://www.reddit.com/search.json'
_contact = os.getenv('USER_EMAIL', 'contact@example.com')
# Reddit requires a descriptive User-Agent to avoid 429s.
HEADERS = {'User-Agent': f'BookDragon/1.0 research-tool ({_contact})'}


def fetch(query, max_results=5):
    """Fetch Reddit posts matching query. Returns list of result dicts."""
    params = {
        'q': query,
        'sort': 'relevance',
        'limit': max_results,
        't': 'year',
        'type': 'link',
    }

    try:
        resp = requests.get(SEARCH_URL, params=params, headers=HEADERS, timeout=10)
        check_response(resp, 'Reddit')
        resp.raise_for_status()
        children = resp.json().get('data', {}).get('children', [])
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f'Reddit search failed: {exc}')

    results = []
    for child in children[:max_results]:
        post = child.get('data', {})
        title = post.get('title', '')
        selftext = (post.get('selftext') or '').strip()
        subreddit = post.get('subreddit', '')
        permalink = f"https://www.reddit.com{post.get('permalink', '')}"
        score = post.get('score', 0)
        num_comments = post.get('num_comments', 0)

        content_parts = [f'[r/{subreddit}] Score: {score} | Comments: {num_comments}']
        if selftext and selftext not in ('[deleted]', '[removed]'):
            content_parts.append(selftext[:2000])

        results.append({
            'url': permalink,
            'title': f'[r/{subreddit}] {title}',
            'content': '\n\n'.join(content_parts),
            'source_type': 'reddit',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'subreddit': subreddit,
                'score': score,
                'num_comments': num_comments,
                'upvote_ratio': post.get('upvote_ratio', 0),
                'external_url': post.get('url', ''),
            },
        })

    return results
