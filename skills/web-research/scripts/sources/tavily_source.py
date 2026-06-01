"""Tavily source adapter — Tavily Search API, requires TAVILY_API_KEY in .env."""
import os
from pathlib import Path
from datetime import datetime, timezone

# Load .env from the project root (four levels up from this file)
try:
    from dotenv import load_dotenv
    _PROJECT_ROOT = Path(__file__).parent.parent.parent.parent.parent
    load_dotenv(_PROJECT_ROOT / '.env')
except ImportError:
    pass  # python-dotenv not installed; fall back to os.environ

try:
    from tavily import TavilyClient
except ImportError:
    raise ImportError('tavily-python is required: pip install tavily-python')

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from exceptions import QuotaExceededError, AuthError, check_response


def fetch(query, max_results=5):
    """
    Fetch search results from Tavily.

    Tavily is purpose-built for AI research — it returns clean, pre-extracted
    content rather than raw HTML, making it the highest-quality general source
    in this skill.

    Requires TAVILY_API_KEY set in .env or the environment.
    """
    api_key = os.environ.get('TAVILY_API_KEY')
    if not api_key:
        raise RuntimeError(
            'TAVILY_API_KEY not found. Set it in .env at the project root.'
        )

    client = TavilyClient(api_key=api_key)

    try:
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth='advanced',   # deeper crawl; uses 2 credits per search
            include_answer=False,
            include_raw_content=False,
        )
    except Exception as exc:
        # Tavily SDK wraps HTTP errors — surface quota/auth issues by message
        msg = str(exc).lower()
        if '429' in msg or 'rate limit' in msg or 'too many requests' in msg:
            raise QuotaExceededError(
                'Tavily rate limit hit. Try again later or reduce query frequency.'
            )
        if '402' in msg or 'quota' in msg or 'credits' in msg:
            raise QuotaExceededError(
                'Tavily monthly quota exceeded. Check your plan at app.tavily.com.'
            )
        if '401' in msg or '403' in msg or 'unauthori' in msg or 'invalid key' in msg:
            raise AuthError('Tavily API key rejected. Check TAVILY_API_KEY in .env.')
        raise RuntimeError(f'Tavily search failed: {exc}')

    results = []
    for item in response.get('results', [])[:max_results]:
        title = item.get('title', '')
        url = item.get('url', '')
        content = item.get('content', '')
        score = item.get('score', 0.0)
        published_date = item.get('published_date', '')

        results.append({
            'url': url,
            'title': title,
            'content': content,
            'source_type': 'tavily',
            'fetched_at': datetime.now(timezone.utc).isoformat(),
            'metadata': {
                'score': score,
                'published_date': published_date,
            },
        })

    return results
