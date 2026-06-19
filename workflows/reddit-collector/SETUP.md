# Reddit Collector — Setup

## Requirements

- Python 3.9+ (same as the rest of Book Dragon)
- `requests`, `python-dotenv`, and `truststore` (installed by the project's shared environment)

No Reddit API key or OAuth registration is needed. The collector uses Reddit's RSS feed for incremental collection and the Arctic Shift community archive for historical backfill. If OAuth credentials are added later, the collector automatically switches to the full JSON API for richer metadata.

## Environment variables

The collector reads `USER_EMAIL` from the project `.env` file to build a polite User-Agent header (Reddit rate-limits requests without a descriptive User-Agent). This variable should already be set if you have completed Book Dragon's initial setup.

If not, add to your `.env`:

```
USER_EMAIL=your@email.com
```

## First run

1. **Create your feeds config.** The first time you run the collector, it copies `config/feeds.example.json` to `config/feeds.json` automatically. You can also do this manually:

   ```
   cp workflows/reddit-collector/config/feeds.example.json workflows/reddit-collector/config/feeds.json
   ```

   Edit `feeds.json` to add, remove, or configure subreddit feeds.

2. **Run the pre-flight check** to validate configuration and test connectivity:

   ```
   python workflows/reddit-collector/scripts/run.py --check
   ```

3. **Run a full historical backfill first** (recommended for active subreddits). This fetches the entire archive via Arctic Shift with full metadata — may take 10–30 minutes for large subreddits:

   ```
   python workflows/reddit-collector/scripts/run.py --subreddit hfy --backfill
   ```

4. **Collect new posts** incrementally after the backfill:

   ```
   python workflows/reddit-collector/scripts/run.py --subreddit hfy
   ```

5. **Rebuild series indexes** at any time:

   ```
   python workflows/reddit-collector/scripts/run.py --subreddit hfy --reindex-series
   ```

**Why backfill first?** Incremental collection uses Reddit's RSS feed, which is rate-limited. On a busy subreddit, the first incremental run may take several attempts to page through all existing posts. The backfill uses Arctic Shift, which is faster and returns richer metadata (scores, flair, comment counts). After the backfill, incremental runs only need to fetch posts since the last collection — typically just a few pages.

## Feed configuration

Each feed in `feeds.json` supports these fields:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `subreddit` | string | (required) | Subreddit name without r/ prefix |
| `content_filter` | string | `"all"` | `"self"` for text posts only, `"link"` for links only, `"all"` for both |
| `min_score` | integer | `0` | Minimum score threshold; posts below this are skipped |
| `flair_filter` | string | `null` | Only collect posts with this flair (case-insensitive) |
| `title_regex` | string | `null` | Only collect posts whose title matches this regex |
| `detect_series` | boolean | `false` | Whether to detect and group multi-part series |
| `start_date` | string | `"2012-01-01"` | How far back to go for backfill (ISO date) |
| `enabled` | boolean | `true` | Whether this feed is active |

## Scheduling

The collector is designed to be run on a schedule. Use `--all` to process all enabled feeds:

```
python workflows/reddit-collector/scripts/run.py --all
```

This can be wired into Book Dragon's scheduled task system for daily or hourly runs.

## Dry run

All commands support `--dry-run` to preview what would happen without making any changes:

```
python workflows/reddit-collector/scripts/run.py --subreddit hfy --dry-run
python workflows/reddit-collector/scripts/run.py --subreddit hfy --backfill --dry-run
```

## How it works (API access)

The collector uses two data sources and automatically selects the best available path:

| Mode | Source | Auth required | Metadata |
|------|--------|---------------|----------|
| Incremental | Reddit JSON API | OAuth (if configured) | Full (score, flair, comments) |
| Incremental | Reddit RSS feed | None (automatic fallback) | Partial (no score/flair/comments) |
| Backfill | Arctic Shift archive | None | Full |

The client tries the JSON API first on each incremental run. If it gets a 403 (unauthenticated access blocked), it falls back to RSS for the rest of that run. When OAuth credentials are added, the JSON API starts working and RSS is never used.

## Reader server

After collecting posts, you can browse them locally in your browser:

```
python workflows/reddit-collector/scripts/run.py --subreddit hfy --build-reader
```

The reader server runs on `http://127.0.0.1:8080` and opens your browser automatically. It includes built-in orphan prevention — if you start it again while it's already running, it detects the existing server and opens your browser to it instead of starting a duplicate.

To stop the server, either press Ctrl+C in the terminal or run:

```
python workflows/reddit-collector/scripts/build_reader.py --stop
```

The `/shutdown` endpoint is protected by a local instance token and is used by the stop command rather than called directly.

If you want clickable start/stop scripts for your platform instead of using the command line, ask Biblio to create them for you.

## Upgrading to OAuth (optional, future)

Adding Reddit OAuth will give incremental runs full metadata (scores, flair, comment counts). The upgrade is isolated to configuration — no script changes needed.

1. Go to [reddit.com/prefs/apps](https://www.reddit.com/prefs/apps) and create a "script" type application.
2. Add to `.env`:

   ```
   REDDIT_CLIENT_ID=your_client_id
   REDDIT_CLIENT_SECRET=your_client_secret
   ```

3. Add the variables to `.env.example` with placeholder values.

The collector will detect the credentials and use authenticated requests automatically. The RSS fallback remains available as a safety net.
