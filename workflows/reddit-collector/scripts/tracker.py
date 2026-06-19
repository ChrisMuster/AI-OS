"""State management for the Reddit collector — tracks downloaded posts and backfill progress."""

import json
from datetime import datetime, timezone
from pathlib import Path


class Tracker:
    SAVE_INTERVAL = 10  # save after every N new posts

    def __init__(self, state_dir):
        self.state_dir = Path(state_dir)
        self.ids_dir = self.state_dir / "ids"
        self.tracker_path = self.state_dir / "tracker.json"
        self._data = {}
        self._id_sets = {}  # subreddit -> set of post IDs
        self._dirty_count = 0
        self._load()

    def _load(self):
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.ids_dir.mkdir(exist_ok=True)
        if self.tracker_path.exists():
            self._data = json.loads(self.tracker_path.read_text(encoding="utf-8"))
        else:
            self._data = {"version": 1, "subreddits": {}}

    def _ensure_sub(self, subreddit):
        key = subreddit.lower()
        if key not in self._data["subreddits"]:
            self._data["subreddits"][key] = {
                "last_collected_utc": None,
                "total_posts_downloaded": 0,
                "total_posts_skipped": 0,
                "backfill": {
                    "status": "not_started",
                    "completed_windows": [],
                    "current_window": None,
                },
            }
        return key

    def _load_ids(self, subreddit):
        key = subreddit.lower()
        if key in self._id_sets:
            return self._id_sets[key]
        ids_file = self.ids_dir / f"{key}.txt"
        if ids_file.exists():
            lines = ids_file.read_text(encoding="utf-8").splitlines()
            self._id_sets[key] = set(line.strip() for line in lines if line.strip())
        else:
            self._id_sets[key] = set()
        return self._id_sets[key]

    def _save_ids(self, subreddit):
        key = subreddit.lower()
        ids = self._id_sets.get(key, set())
        ids_file = self.ids_dir / f"{key}.txt"
        ids_file.write_text("\n".join(sorted(ids)) + "\n", encoding="utf-8",
                            newline="\n")

    def is_downloaded(self, subreddit, post_id):
        ids = self._load_ids(subreddit)
        return post_id in ids

    def mark_downloaded(self, subreddit, post_id):
        key = self._ensure_sub(subreddit)
        ids = self._load_ids(subreddit)
        if post_id not in ids:
            ids.add(post_id)
            self._data["subreddits"][key]["total_posts_downloaded"] += 1
            self._dirty_count += 1
            if self._dirty_count >= self.SAVE_INTERVAL:
                self.save()
                self._dirty_count = 0

    def mark_skipped(self, subreddit):
        key = self._ensure_sub(subreddit)
        self._data["subreddits"][key]["total_posts_skipped"] += 1

    def get_known_ids(self, subreddit):
        return self._load_ids(subreddit)

    def get_last_collected_epoch(self, subreddit):
        key = self._ensure_sub(subreddit)
        ts = self._data["subreddits"][key].get("last_collected_utc")
        if ts:
            dt = datetime.fromisoformat(ts)
            return dt.timestamp()
        return None

    def update_last_collected(self, subreddit):
        key = self._ensure_sub(subreddit)
        self._data["subreddits"][key]["last_collected_utc"] = (
            datetime.now(timezone.utc).isoformat()
        )

    def get_backfill_status(self, subreddit):
        key = self._ensure_sub(subreddit)
        return self._data["subreddits"][key]["backfill"]

    def is_window_complete(self, subreddit, window_key):
        key = self._ensure_sub(subreddit)
        return window_key in self._data["subreddits"][key]["backfill"]["completed_windows"]

    def mark_window_complete(self, subreddit, window_key):
        key = self._ensure_sub(subreddit)
        bf = self._data["subreddits"][key]["backfill"]
        if window_key not in bf["completed_windows"]:
            bf["completed_windows"].append(window_key)
        bf["current_window"] = None

    def set_current_window(self, subreddit, window_key):
        key = self._ensure_sub(subreddit)
        self._data["subreddits"][key]["backfill"]["current_window"] = window_key

    def mark_backfill_complete(self, subreddit):
        key = self._ensure_sub(subreddit)
        self._data["subreddits"][key]["backfill"]["status"] = "complete"

    def mark_backfill_started(self, subreddit):
        key = self._ensure_sub(subreddit)
        self._data["subreddits"][key]["backfill"]["status"] = "in_progress"

    def get_stats(self, subreddit):
        key = self._ensure_sub(subreddit)
        sub_data = self._data["subreddits"][key]
        return {
            "total_downloaded": sub_data["total_posts_downloaded"],
            "total_skipped": sub_data["total_posts_skipped"],
            "last_collected": sub_data["last_collected_utc"],
            "backfill_status": sub_data["backfill"]["status"],
            "windows_completed": len(sub_data["backfill"]["completed_windows"]),
        }

    def save(self):
        self.tracker_path.write_text(
            json.dumps(self._data, indent=2) + "\n", encoding="utf-8",
            newline="\n",
        )
        for sub_key in self._id_sets:
            self._save_ids(sub_key)
