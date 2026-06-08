#!/usr/bin/env python3
"""
search.py — Book Dragon session history search.

Queries all machine shards of the FTS5 index and returns merged, ranked results.

Usage:
  python search.py "your query"
  python search.py "memory workflow" --limit 5
  python search.py "USER.md" --since 2026-06-01
  python search.py "journal" --source claude-code
  python search.py "session" --hostname DESKTOP-XXXXX
"""

import argparse
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR   = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
DATA_DIR     = WORKFLOW_DIR / 'data'


# ---------------------------------------------------------------------------
# Shard discovery
# ---------------------------------------------------------------------------

def find_shards() -> list:
    """Return all session database shard paths in data/."""
    if not DATA_DIR.exists():
        return []
    return sorted(DATA_DIR.glob('sessions-*.db'))


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

def search(
    query: str,
    limit: int = 10,
    since: str = None,
    hostname_filter: str = None,
    source_filter: str = None,
) -> list:
    """
    Search across all shards and return merged results sorted by FTS5 rank.

    Returns a list of dicts, each containing:
        snippet, hostname, source, session_id, session_title, timestamp, role, rank
    """
    shards = find_shards()
    if not shards:
        return []

    all_results = []

    for shard in shards:
        try:
            conn = sqlite3.connect(f'file:{shard}?mode=ro', uri=True)
            conn.row_factory = sqlite3.Row

            # Build WHERE clause dynamically
            conditions = ['sessions MATCH ?']
            params = [query]

            if since:
                conditions.append('timestamp >= ?')
                params.append(since)
            if hostname_filter:
                conditions.append('hostname = ?')
                params.append(hostname_filter)
            if source_filter:
                conditions.append('source = ?')
                params.append(source_filter)

            where = ' AND '.join(conditions)

            sql = f"""
                SELECT
                    snippet(sessions, 6, "[", "]", "...", 20) AS snippet,
                    hostname,
                    source,
                    session_id,
                    session_title,
                    timestamp,
                    role,
                    rank
                FROM sessions
                WHERE {where}
                ORDER BY rank
                LIMIT ?
            """
            params.append(limit * 2)  # fetch more than needed for cross-shard merge

            rows = conn.execute(sql, params).fetchall()
            all_results.extend([dict(r) for r in rows])
            conn.close()

        except sqlite3.OperationalError as e:
            # FTS5 query syntax error or missing table
            print(f'  [WARNING] Query error on {shard.name}: {e}')
            continue
        except Exception as e:
            print(f'  [WARNING] Could not query {shard.name}: {e}')
            continue

    # Merge and re-rank across shards; lower rank = better match in FTS5
    all_results.sort(key=lambda r: r.get('rank', 0))
    return all_results[:limit]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------

def format_results(results: list) -> None:
    """Print results in a readable format."""
    if not results:
        print('No results found.')
        return

    print(f'\n{len(results)} result(s):\n')
    for i, r in enumerate(results, 1):
        title = r.get('session_title') or r.get('session_id', 'Unknown session')
        source    = r.get('source', '')
        hostname  = r.get('hostname', '')
        date_str  = (r.get('timestamp') or '')[:10]
        role      = r.get('role', '')
        snippet   = r.get('snippet', '')

        print(f'[{i}] {title}')
        print(f'    {date_str}  |  {source}  |  {hostname}  |  {role}')
        print(f'    {snippet}')
        print()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    shards = find_shards()
    if not shards:
        print('No session database found.')
        print('Run: python workflows/session-search/scripts/index.py')
        return

    parser = argparse.ArgumentParser(
        description='Search Book Dragon session history',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('query', help='Search query (FTS5 syntax)')
    parser.add_argument('--limit',    type=int, default=10,
                        help='Maximum results to return (default: 10)')
    parser.add_argument('--since',    metavar='YYYY-MM-DD',
                        help='Only return results after this date')
    parser.add_argument('--hostname', metavar='NAME',
                        help='Limit results to a specific machine')
    parser.add_argument('--source',   choices=['claude-code', 'cowork'],
                        help='Limit results to a specific source')
    args = parser.parse_args()

    print(f'Searching {len(shards)} shard(s) for: {args.query!r}')
    results = search(
        args.query,
        limit=args.limit,
        since=args.since,
        hostname_filter=args.hostname,
        source_filter=args.source,
    )
    format_results(results)


if __name__ == '__main__':
    main()
