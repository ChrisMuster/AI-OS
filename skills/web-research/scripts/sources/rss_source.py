"""RSS feed source adapter — feedparser, no key required."""
import re
from pathlib import Path
from datetime import datetime, timezone

try:
    import feedparser
except ImportError:
    raise ImportError('feedparser is required: pip install feedparser')

try:
    import yaml
except ImportError:
    raise ImportError('pyyaml is required: pip install pyyaml')

# Config lives at skills/web-research/config/rss_feeds.yaml
_CONFIG_PATH = Path(__file__).parent.parent.parent / 'config' / 'rss_feeds.yaml'


def fetch(query, max_results=5, category=None):
    """
    Fetch RSS feed entries relevant to the query.

    Args:
        query (str): Search topic to score entries against.
        max_results (int): Max entries to return.
        category (str): Feed category key from rss_feeds.yaml. If None, all feeds are used.

    Returns:
        list: Result dicts.
    """
    feeds = _load_feeds(category)
    if not feeds:
        return []

    query_words = set(query.lower().split())
    scored = []

    for feed_url in feeds:
        try:
            parsed = feedparser.parse(feed_url)
            feed_title = parsed.feed.get('title', feed_url)
            for entry in parsed.entries[:15]:
                title = entry.get('title', '')
                summary = entry.get('summary') or entry.get('description') or ''
                link = entry.get('link', '')
                published = entry.get('published', '')

                combined = (title + ' ' + summary).lower()
                score = sum(1 for w in query_words if w in combined)

                scored.append({
                    '_score': score,
                    'url': link,
                    'title': title,
                    'content': _strip_html(summary),
                    'source_type': 'rss',
                    'fetched_at': datetime.now(timezone.utc).isoformat(),
                    'metadata': {
                        'feed_title': feed_title,
                        'feed_url': feed_url,
                        'published': published,
                    },
                })
        except Exception:
            continue

    scored.sort(key=lambda x: x['_score'], reverse=True)

    results = []
    for item in scored[:max_results]:
        item.pop('_score', None)
        results.append(item)

    return results


def _load_feeds(category):
    if not _CONFIG_PATH.exists():
        return []
    with open(_CONFIG_PATH, 'r', encoding='utf-8') as f:
        config = yaml.safe_load(f) or {}
    if category and category in config:
        return config[category]
    # No category — return all feeds flattened
    all_feeds = []
    for feeds in config.values():
        if isinstance(feeds, list):
            all_feeds.extend(feeds)
    return all_feeds


def _strip_html(text):
    return re.sub(r'<[^>]+>', '', text)
