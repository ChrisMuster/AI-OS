"""Historical backfill engine using Arctic Shift API for full subreddit archives."""

import re
import time
from calendar import monthrange
from datetime import datetime, timezone

import requests


ARCTIC_SHIFT_URL = "https://arctic-shift.photon-reddit.com/api/posts/search"
COURTESY_DELAY = 0.5  # seconds between requests
BACKOFF_DELAY = 2.0   # seconds on rate limit / error
PAGE_SIZE = 100


class ArcticShiftClient:
    """Thin client for the Arctic Shift community Reddit archive API."""

    def __init__(self, user_agent):
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})

    def fetch_posts(self, subreddit, after_epoch, before_epoch, limit=PAGE_SIZE):
        """Fetch posts from Arctic Shift. Returns list of post dicts."""
        params = {
            "subreddit": subreddit,
            "after": int(after_epoch),
            "before": int(before_epoch),
            "sort": "asc",
            "sort_type": "created_utc",
            "limit": limit,
        }
        for attempt in range(3):
            try:
                resp = self.session.get(ARCTIC_SHIFT_URL, params=params, timeout=30)
                if resp.status_code == 200:
                    return resp.json().get("data", [])
                if resp.status_code == 429:
                    time.sleep(BACKOFF_DELAY * (2 ** attempt))
                    continue
                if resp.status_code >= 500:
                    time.sleep(BACKOFF_DELAY * (2 ** attempt))
                    continue
                resp.raise_for_status()
            except requests.RequestException:
                if attempt == 2:
                    raise
                time.sleep(BACKOFF_DELAY * (2 ** attempt))
        return []


def _normalise_arctic_post(raw):
    """Normalise an Arctic Shift post to match RedditClient's format."""
    created = datetime.fromtimestamp(raw.get("created_utc", 0), tz=timezone.utc)
    return {
        "id": raw.get("id", ""),
        "title": raw.get("title", ""),
        "author": raw.get("author", "[deleted]"),
        "subreddit": raw.get("subreddit", ""),
        "created_utc": created.isoformat(),
        "created_epoch": raw.get("created_utc", 0),
        "score": raw.get("score", 0),
        "upvote_ratio": raw.get("upvote_ratio", 0),
        "flair": raw.get("link_flair_text", ""),
        "url": raw.get("url", ""),
        "permalink": f"https://www.reddit.com{raw.get('permalink', '')}",
        "is_self": raw.get("is_self", False),
        "selftext": raw.get("selftext", ""),
        "num_comments": raw.get("num_comments", 0),
        "crosspost_parent": raw.get("crosspost_parent", None),
    }


def generate_monthly_windows(start_date_str, end_date=None):
    """Generate (window_key, start_epoch, end_epoch) tuples for each month."""
    start = datetime.strptime(start_date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = end_date or datetime.now(timezone.utc)

    year, month = start.year, start.month
    while datetime(year, month, 1, tzinfo=timezone.utc) <= end:
        window_key = f"{year:04d}-{month:02d}"
        start_epoch = datetime(year, month, 1, tzinfo=timezone.utc).timestamp()
        _, last_day = monthrange(year, month)
        # End of month is start of next month
        if month == 12:
            end_epoch = datetime(year + 1, 1, 1, tzinfo=timezone.utc).timestamp()
        else:
            end_epoch = datetime(year, month + 1, 1, tzinfo=timezone.utc).timestamp()
        yield window_key, start_epoch, end_epoch

        if month == 12:
            year += 1
            month = 1
        else:
            month += 1


def backfill_subreddit(
    subreddit, feed_config, tracker, save_post_fn,
    dry_run=False, progress_fn=None,
):
    """
    Run a full historical backfill for a subreddit.

    Args:
        subreddit: Subreddit name (e.g. "HFY")
        feed_config: Dict with content_filter, min_score, flair_filter, etc.
        tracker: Tracker instance for state management
        save_post_fn: Callable(post_dict) -> bool that saves a post, returns True if saved
        dry_run: If True, preview only
        progress_fn: Optional callable(msg) for progress output

    Returns:
        Dict with stats: posts_collected, posts_skipped, windows_completed
    """
    def log(msg):
        if progress_fn:
            progress_fn(msg)
        else:
            print(msg)

    user_agent = f"BookDragon/1.0 reddit-collector-backfill"
    client = ArcticShiftClient(user_agent)
    start_date = feed_config.get("start_date", "2012-01-01")
    windows = list(generate_monthly_windows(start_date))
    total_windows = len(windows)

    if dry_run:
        log(f"[DRY RUN] Would backfill r/{subreddit} from {start_date}")
        log(f"[DRY RUN] {total_windows} monthly windows to process")
        return {"posts_collected": 0, "posts_skipped": 0, "windows_completed": 0}

    tracker.mark_backfill_started(subreddit)
    stats = {"posts_collected": 0, "posts_skipped": 0, "windows_completed": 0}
    known_ids = tracker.get_known_ids(subreddit)

    for idx, (window_key, start_epoch, end_epoch) in enumerate(windows, 1):
        if tracker.is_window_complete(subreddit, window_key):
            stats["windows_completed"] += 1
            continue

        tracker.set_current_window(subreddit, window_key)
        window_collected = 0
        window_skipped = 0
        cursor = start_epoch

        while True:
            try:
                raw_posts = client.fetch_posts(subreddit, cursor, end_epoch, PAGE_SIZE)
            except Exception as exc:
                log(f"  Error in window {window_key} at cursor {cursor}: {exc}")
                break

            if not raw_posts:
                break

            for raw in raw_posts:
                post = _normalise_arctic_post(raw)

                if post["id"] in known_ids:
                    continue

                if not _passes_filter(post, feed_config):
                    window_skipped += 1
                    tracker.mark_skipped(subreddit)
                    continue

                if save_post_fn(post):
                    window_collected += 1
                    tracker.mark_downloaded(subreddit, post["id"])
                    known_ids.add(post["id"])

            last_epoch = raw_posts[-1].get("created_utc", cursor)
            if last_epoch <= cursor:
                break
            cursor = last_epoch
            time.sleep(COURTESY_DELAY)

        tracker.mark_window_complete(subreddit, window_key)
        tracker.save()
        stats["posts_collected"] += window_collected
        stats["posts_skipped"] += window_skipped
        stats["windows_completed"] += 1

        pct = (idx / total_windows) * 100
        log(
            f"  Window {window_key}: {window_collected} collected, "
            f"{window_skipped} skipped ({idx}/{total_windows}, {pct:.0f}%)"
        )

    tracker.mark_backfill_complete(subreddit)
    tracker.update_last_collected(subreddit)
    tracker.save()
    return stats


def _passes_filter(post, feed_config):
    content_filter = feed_config.get("content_filter", "all")
    if content_filter == "self" and not post["is_self"]:
        return False
    if content_filter == "link" and post["is_self"]:
        return False

    min_score = feed_config.get("min_score", 0)
    if post["score"] < min_score:
        return False

    flair_filter = feed_config.get("flair_filter")
    if flair_filter and (post.get("flair") or "").lower() != flair_filter.lower():
        return False

    title_regex = feed_config.get("title_regex")
    if title_regex:
        try:
            if not re.search(title_regex, post["title"], re.IGNORECASE):
                return False
        except re.error:
            pass

    return True
