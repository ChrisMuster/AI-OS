#!/usr/bin/env python3
"""
Reddit collector — downloads posts from configured subreddits,
saves as Markdown, detects multi-part series and groups them.
"""

import argparse
import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# ─── Paths ───────────────────────────────────────────────────────────────────

SCRIPT_DIR    = Path(__file__).resolve().parent
WORKFLOW_DIR  = SCRIPT_DIR.parent
PROJECT_ROOT  = WORKFLOW_DIR.parent.parent
CONFIG_DIR    = WORKFLOW_DIR / "config"
COLLECTIONS   = WORKFLOW_DIR / "collections"
STATE_DIR     = WORKFLOW_DIR / "state"
LOG_FILE      = WORKFLOW_DIR / "LOG.md"

FEEDS_FILE    = CONFIG_DIR / "feeds.json"
FEEDS_EXAMPLE = CONFIG_DIR / "feeds.example.json"

# ─── Runtime hand-off ────────────────────────────────────────────────────────

sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"))
try:
    from runtime import ensure_project_runtime
    ensure_project_runtime()
except (ImportError, RuntimeError):
    pass  # fall through to system Python if runtime not set up

try:
    import truststore
    truststore.inject_into_ssl()
except ImportError:
    pass

# ─── Environment ─────────────────────────────────────────────────────────────

def load_env():
    env_path = PROJECT_ROOT / ".env"
    result = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, val = line.partition("=")
                result[key.strip()] = val.strip().strip('"').strip("'")
    return result


# ─── Logging ─────────────────────────────────────────────────────────────────

def get_timestamp():
    try:
        result = subprocess.run(
            ["date", '+%Y-%m-%dT%H:%M:%S%:z'],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return datetime.now().astimezone().isoformat(timespec="seconds")


def append_log(action, note):
    ts = get_timestamp()
    entry = f"[{ts}] | Actor: Biblio | Action: {action} | Note: {note}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(entry)


# ─── Config loading ─────────────────────────────────────────────────────────

def load_feeds():
    if not FEEDS_FILE.exists():
        if FEEDS_EXAMPLE.exists():
            shutil.copy2(FEEDS_EXAMPLE, FEEDS_FILE)
            print(f"Created {FEEDS_FILE} from example config.")
        else:
            print("Error: no feeds.json or feeds.example.json found.", file=sys.stderr)
            sys.exit(1)
    return json.loads(FEEDS_FILE.read_text(encoding="utf-8"))


def get_feed_config(feeds_data, feed_key):
    defaults = feeds_data.get("defaults", {})
    feed = feeds_data.get("feeds", {}).get(feed_key)
    if not feed:
        return None
    merged = {**defaults, **feed}
    return merged


# ─── Post saving ─────────────────────────────────────────────────────────────

def make_save_post_fn(subreddit, dry_run=False):
    """Return a callable that saves a post dict to disk."""
    posts_dir = COLLECTIONS / subreddit.lower() / "posts"

    if not dry_run:
        posts_dir.mkdir(parents=True, exist_ok=True)

    # Deferred import to keep module-level fast
    sys.path.insert(0, str(SCRIPT_DIR))
    from post_formatter import format_post

    def save(post):
        filepath = posts_dir / f"{post['id']}.md"
        if filepath.exists():
            return False
        if dry_run:
            print(f"  [DRY RUN] Would save: {post['title'][:80]}")
            return True
        content = format_post(post)
        filepath.write_text(content, encoding="utf-8")
        return True

    return save


# ─── Commands ────────────────────────────────────────────────────────────────

def cmd_collect(feed_key, feed_config, dry_run=False):
    """Collect new posts from a subreddit (incremental mode)."""
    from reddit_client import RedditClient
    from tracker import Tracker

    subreddit = feed_config["subreddit"]
    env = load_env()
    user_email = env.get("USER_EMAIL", "contact@example.com")
    user_agent = f"BookDragon/1.0 reddit-collector ({user_email})"

    client = RedditClient(user_agent)
    tracker = Tracker(STATE_DIR)
    save_fn = make_save_post_fn(subreddit, dry_run)

    last_epoch = tracker.get_last_collected_epoch(subreddit)
    known_ids = tracker.get_known_ids(subreddit)

    if dry_run:
        print(f"[DRY RUN] Collecting new posts from r/{subreddit}")
        if last_epoch:
            print(f"[DRY RUN] Last collected: {datetime.fromtimestamp(last_epoch, tz=timezone.utc).isoformat()}")
        else:
            print(f"[DRY RUN] No previous collection — will fetch recent posts")

    collected = 0
    skipped = 0

    from backfill import _passes_filter

    for post in client.get_all_new_posts(subreddit, stop_before_epoch=last_epoch, known_ids=known_ids):
        if not _passes_filter(post, feed_config):
            skipped += 1
            tracker.mark_skipped(subreddit)
            continue
        if save_fn(post):
            collected += 1
            if not dry_run:
                tracker.mark_downloaded(subreddit, post["id"])

    if not dry_run:
        tracker.update_last_collected(subreddit)
        tracker.save()

    print(f"\nr/{subreddit}: {collected} new posts collected, {skipped} skipped by filter.")
    return collected, skipped


def cmd_backfill(feed_key, feed_config, dry_run=False):
    """Run full historical backfill for a subreddit."""
    from backfill import backfill_subreddit
    from tracker import Tracker

    subreddit = feed_config["subreddit"]
    tracker = Tracker(STATE_DIR)
    save_fn = make_save_post_fn(subreddit, dry_run)

    print(f"Starting backfill for r/{subreddit} from {feed_config.get('start_date', '2012-01-01')}...")
    stats = backfill_subreddit(
        subreddit, feed_config, tracker, save_fn,
        dry_run=dry_run,
    )
    print(
        f"\nBackfill complete: {stats['posts_collected']} collected, "
        f"{stats['posts_skipped']} skipped, "
        f"{stats['windows_completed']} windows processed."
    )
    return stats


def cmd_reindex_series(feed_key, feed_config, dry_run=False):
    """Rebuild series indexes from downloaded posts."""
    from series_detector import detect_series, build_series_indexes

    subreddit = feed_config["subreddit"]
    sub_dir = COLLECTIONS / subreddit.lower()
    posts_dir = sub_dir / "posts"

    if not posts_dir.exists():
        print(f"No posts directory for r/{subreddit}. Collect posts first.")
        return {}

    print(f"Detecting series in r/{subreddit}...")
    series_map = detect_series(posts_dir)

    if not series_map:
        print("No series detected.")
        return series_map

    if dry_run:
        print(f"\n[DRY RUN] {len(series_map)} series detected:")
        for slug, series in sorted(series_map.items()):
            print(f"  {series['name']} — {len(series['parts'])} parts ({series['detection_method']})")
        return series_map

    build_series_indexes(series_map, sub_dir)
    print(f"\n{len(series_map)} series detected and indexed:")
    for slug, series in sorted(series_map.items()):
        print(f"  {series['name']} — {len(series['parts'])} parts ({series['detection_method']})")

    return series_map


def cmd_check(feeds_data):
    """Pre-flight check: validate config and connectivity."""
    from reddit_client import RedditClient

    env = load_env()
    user_email = env.get("USER_EMAIL", "contact@example.com")
    user_agent = f"BookDragon/1.0 reddit-collector ({user_email})"

    print("Pre-flight check:")
    print(f"  User-Agent: {user_agent}")
    print(f"  Feeds file: {FEEDS_FILE}")
    print(f"  Collections: {COLLECTIONS}")
    print(f"  State dir: {STATE_DIR}")
    print()

    feeds = feeds_data.get("feeds", {})
    client = RedditClient(user_agent)

    for key, feed in feeds.items():
        subreddit = feed.get("subreddit", key)
        enabled = feed.get("enabled", True)
        status = "enabled" if enabled else "disabled"
        print(f"  Feed '{key}' (r/{subreddit}) [{status}]")

        if enabled:
            try:
                posts, _ = client.get_new_posts(subreddit, limit=1)
                if posts:
                    print(f"    Connectivity: OK (latest: {posts[0]['title'][:60]})")
                else:
                    print(f"    Connectivity: OK (empty listing)")
            except Exception as exc:
                print(f"    Connectivity: FAILED ({exc})")
    print("\nCheck complete.")


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Collect Reddit posts and detect series.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  python run.py --subreddit hfy                  Collect new posts from r/HFY
  python run.py --subreddit hfy --backfill       Full historical download
  python run.py --subreddit hfy --reindex-series Rebuild series indexes
  python run.py --all                            Collect from all enabled feeds
  python run.py --subreddit hfy --dry-run        Preview without downloading
  python run.py --check                          Validate config and connectivity
""",
    )
    parser.add_argument("--subreddit", "-s",
                        help="Feed key from feeds.json (e.g. 'hfy')")
    parser.add_argument("--all", action="store_true",
                        help="Process all enabled feeds")
    parser.add_argument("--backfill", action="store_true",
                        help="Run full historical backfill (uses Arctic Shift)")
    parser.add_argument("--reindex-series", action="store_true",
                        help="Rebuild series indexes from downloaded posts")
    parser.add_argument("--check", action="store_true",
                        help="Pre-flight check: validate config and test connectivity")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned actions without making changes")

    args = parser.parse_args()
    feeds_data = load_feeds()

    if args.check:
        cmd_check(feeds_data)
        return

    if not args.subreddit and not args.all:
        parser.print_help()
        sys.exit(1)

    # Build list of feed keys to process
    if args.all:
        feed_keys = [
            k for k, v in feeds_data.get("feeds", {}).items()
            if v.get("enabled", True)
        ]
    else:
        feed_keys = [args.subreddit]

    if not feed_keys:
        print("No enabled feeds found in config.", file=sys.stderr)
        sys.exit(1)

    for feed_key in feed_keys:
        feed_config = get_feed_config(feeds_data, feed_key)
        if not feed_config:
            print(f"Error: feed '{feed_key}' not found in config.", file=sys.stderr)
            sys.exit(1)

        subreddit = feed_config["subreddit"]
        mode = "backfill" if args.backfill else "reindex-series" if args.reindex_series else "collect"
        append_log("started", f"Reddit collector {mode} for r/{subreddit}.")

        try:
            if args.backfill:
                stats = cmd_backfill(feed_key, feed_config, args.dry_run)
                if feed_config.get("detect_series") and not args.dry_run:
                    print("\nRunning series detection...")
                    cmd_reindex_series(feed_key, feed_config, args.dry_run)
                note = (
                    f"Reddit collector backfill completed for r/{subreddit}. "
                    f"{stats['posts_collected']} collected, {stats['posts_skipped']} skipped."
                )
            elif args.reindex_series:
                series_map = cmd_reindex_series(feed_key, feed_config, args.dry_run)
                note = f"Series reindex completed for r/{subreddit}. {len(series_map)} series detected."
            else:
                collected, skipped = cmd_collect(feed_key, feed_config, args.dry_run)
                if feed_config.get("detect_series") and collected > 0 and not args.dry_run:
                    print("\nRunning series detection...")
                    cmd_reindex_series(feed_key, feed_config, args.dry_run)
                note = (
                    f"Reddit collector completed for r/{subreddit}. "
                    f"{collected} new posts, {skipped} skipped."
                )

            if not args.dry_run:
                append_log("completed", note)

        except KeyboardInterrupt:
            append_log("failed", f"Reddit collector interrupted for r/{subreddit}.")
            print("\nInterrupted by user.")
            sys.exit(130)
        except Exception as exc:
            append_log("failed", f"Reddit collector failed for r/{subreddit}: {exc}")
            print(f"\nError: {exc}", file=sys.stderr)
            sys.exit(1)


if __name__ == "__main__":
    main()
