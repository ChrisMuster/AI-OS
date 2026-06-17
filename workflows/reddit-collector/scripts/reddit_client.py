"""Reddit API client with rate limiting, pagination, retry logic, and RSS fallback."""

import re
import time
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from html.parser import HTMLParser

import requests


class RateLimitError(Exception):
    pass


class SubredditNotFoundError(Exception):
    pass


class JsonApiForbidden(Exception):
    pass


# ─── HTML-to-Markdown converter ────────────────────────────────────────────

class _HtmlToMarkdown(HTMLParser):
    """Lightweight converter for Reddit's post HTML to approximate Markdown."""

    def __init__(self):
        super().__init__()
        self._parts = []
        self._stack = []
        self._pending_href = None

    def handle_starttag(self, tag, attrs):
        self._stack.append(tag)
        if tag in ("p", "div"):
            self._parts.append("\n\n")
        elif tag == "br":
            self._parts.append("\n")
        elif tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag in ("del", "s"):
            self._parts.append("~~")
        elif tag == "a":
            self._pending_href = dict(attrs).get("href", "")
            self._parts.append("[")
        elif tag == "code" and "pre" not in self._stack[:-1]:
            self._parts.append("`")
        elif tag == "pre":
            self._parts.append("\n\n```\n")
        elif tag == "blockquote":
            self._parts.append("\n\n> ")
        elif tag == "li":
            parent = next((t for t in reversed(self._stack[:-1])
                           if t in ("ul", "ol")), "ul")
            self._parts.append("\n- " if parent == "ul" else "\n1. ")
        elif tag == "hr":
            self._parts.append("\n\n---\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            level = int(tag[1])
            self._parts.append(f"\n\n{'#' * level} ")
        elif tag == "sup":
            self._parts.append("^(")
        elif tag in ("td", "th"):
            self._parts.append(" | ")

    def handle_endtag(self, tag):
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in ("strong", "b"):
            self._parts.append("**")
        elif tag in ("em", "i"):
            self._parts.append("*")
        elif tag in ("del", "s"):
            self._parts.append("~~")
        elif tag == "a":
            href = self._pending_href or ""
            self._parts.append(f"]({href})")
            self._pending_href = None
        elif tag == "code" and "pre" not in self._stack:
            self._parts.append("`")
        elif tag == "pre":
            self._parts.append("\n```\n\n")
        elif tag in ("p", "div"):
            self._parts.append("\n\n")
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._parts.append("\n\n")
        elif tag == "sup":
            self._parts.append(")")
        elif tag == "tr":
            self._parts.append("\n")

    def handle_data(self, data):
        self._parts.append(data)

    def get_markdown(self):
        text = "".join(self._parts)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()


def _html_to_markdown(html_str):
    if not html_str:
        return ""
    parser = _HtmlToMarkdown()
    parser.feed(html_str)
    return parser.get_markdown()


# ─── Client ─────────────────────────────────────────────────────────────────

class RedditClient:
    """Reddit API client with JSON and RSS fallback."""

    BASE_URL = "https://www.reddit.com"
    MAX_REQUESTS_PER_WINDOW = 58  # slightly under Reddit's 60/min limit
    WINDOW_SECONDS = 60
    MAX_RETRIES = 3
    RATE_LIMIT_RETRIES = 6  # more patience for 429s during long pagination
    BACKOFF_BASE = 1  # seconds; doubles each retry
    ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}

    def __init__(self, user_agent):
        self.user_agent = user_agent
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": user_agent})
        self._request_times = deque()
        self._json_forbidden = False

    def _wait_for_rate_limit(self):
        now = time.monotonic()
        while (
            len(self._request_times) >= self.MAX_REQUESTS_PER_WINDOW
            and self._request_times[0] > now - self.WINDOW_SECONDS
        ):
            sleep_for = self._request_times[0] - (now - self.WINDOW_SECONDS) + 0.1
            time.sleep(sleep_for)
            now = time.monotonic()
        while self._request_times and self._request_times[0] <= now - self.WINDOW_SECONDS:
            self._request_times.popleft()

    def _get(self, url, params=None):
        max_attempts = self.MAX_RETRIES
        rate_limit_attempts = 0
        attempt = 0
        while attempt < max_attempts:
            self._wait_for_rate_limit()
            self._request_times.append(time.monotonic())
            try:
                resp = self.session.get(url, params=params, timeout=15)
            except requests.RequestException as exc:
                attempt += 1
                if attempt >= max_attempts:
                    raise ConnectionError(f"Network error after {attempt} retries: {exc}") from exc
                time.sleep(self.BACKOFF_BASE * (2 ** (attempt - 1)))
                continue

            if resp.status_code == 200:
                return resp
            if resp.status_code == 429:
                rate_limit_attempts += 1
                retry_after = int(resp.headers.get("Retry-After", 60))
                if rate_limit_attempts >= self.RATE_LIMIT_RETRIES:
                    raise RateLimitError(
                        f"Rate limited after {rate_limit_attempts} retries"
                    )
                time.sleep(retry_after)
                continue
            if resp.status_code == 403:
                raise JsonApiForbidden(f"HTTP 403: {url}")
            if resp.status_code == 404:
                raise SubredditNotFoundError(f"HTTP 404: {url}")
            if resp.status_code >= 500:
                attempt += 1
                if attempt >= max_attempts:
                    raise ConnectionError(f"Server error {resp.status_code} after {attempt} retries")
                time.sleep(self.BACKOFF_BASE * (2 ** (attempt - 1)))
                continue
            resp.raise_for_status()
            attempt += 1

        raise ConnectionError("Request failed after all retries")

    # ── JSON API ────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_post(raw):
        data = raw["data"] if "data" in raw else raw
        created = datetime.fromtimestamp(data.get("created_utc", 0), tz=timezone.utc)
        return {
            "id": data.get("id", ""),
            "title": data.get("title", ""),
            "author": data.get("author", "[deleted]"),
            "subreddit": data.get("subreddit", ""),
            "created_utc": created.isoformat(),
            "created_epoch": data.get("created_utc", 0),
            "score": data.get("score", 0),
            "upvote_ratio": data.get("upvote_ratio", 0),
            "flair": data.get("link_flair_text", ""),
            "url": data.get("url", ""),
            "permalink": f"https://www.reddit.com{data.get('permalink', '')}",
            "is_self": data.get("is_self", False),
            "selftext": data.get("selftext", ""),
            "num_comments": data.get("num_comments", 0),
            "crosspost_parent": data.get("crosspost_parent", None),
        }

    def _get_new_posts_json(self, subreddit, limit=100, after=None):
        url = f"{self.BASE_URL}/r/{subreddit}/new.json"
        params = {"limit": min(limit, 100), "raw_json": 1}
        if after:
            params["after"] = after

        resp = self._get(url, params)
        data = resp.json()
        listing = data.get("data", {})
        children = listing.get("children", [])
        next_after = listing.get("after")
        posts = [self._normalise_post(c) for c in children]
        return posts, next_after

    # ── RSS fallback ────────────────────────────────────────────────────────

    def _parse_rss_entry(self, entry):
        ns = self.ATOM_NS
        raw_id = (entry.findtext("atom:id", "", ns) or "").strip()
        post_id = raw_id.removeprefix("t3_")

        title = entry.findtext("atom:title", "", ns) or ""
        updated = entry.findtext("atom:updated", "", ns) or ""
        published = entry.findtext("atom:published", "", ns) or updated

        author_el = entry.find("atom:author", ns)
        author = "[deleted]"
        if author_el is not None:
            name_el = author_el.find("atom:name", ns)
            if name_el is not None and name_el.text:
                author = name_el.text.removeprefix("/u/")

        link_el = entry.find("atom:link", ns)
        permalink = link_el.get("href", "") if link_el is not None else ""

        content_el = entry.find("atom:content", ns)
        html_content = content_el.text if content_el is not None else ""
        selftext = _html_to_markdown(html_content) if html_content else ""

        category_el = entry.find("atom:category", ns)
        subreddit = category_el.get("term", "") if category_el is not None else ""

        try:
            dt = datetime.fromisoformat(published.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            dt = datetime.now(timezone.utc)

        return {
            "id": post_id,
            "title": title,
            "author": author,
            "subreddit": subreddit,
            "created_utc": dt.isoformat(),
            "created_epoch": dt.timestamp(),
            "score": 0,
            "upvote_ratio": 0,
            "flair": "",
            "url": permalink,
            "permalink": permalink,
            "is_self": True,
            "selftext": selftext,
            "num_comments": 0,
            "crosspost_parent": None,
            "_source": "rss",
        }

    def _get_new_posts_rss(self, subreddit, limit=25, after=None):
        url = f"{self.BASE_URL}/r/{subreddit}/new.rss"
        params = {"limit": min(limit, 100)}
        if after:
            params["after"] = after

        resp = self._get(url, params)
        root = ET.fromstring(resp.text)
        entries = root.findall("atom:entry", self.ATOM_NS)
        posts = [self._parse_rss_entry(e) for e in entries]

        next_after = None
        if posts:
            next_after = f"t3_{posts[-1]['id']}"

        return posts, next_after

    # ── Public interface ────────────────────────────────────────────────────

    def get_new_posts(self, subreddit, limit=100, after=None):
        """Fetch posts from /r/{subreddit}/new.
        Tries JSON API first; falls back to RSS on 403.
        Returns (posts, next_after)."""
        if not self._json_forbidden:
            try:
                return self._get_new_posts_json(subreddit, limit, after)
            except JsonApiForbidden:
                self._json_forbidden = True
                print("  JSON API returned 403 — falling back to RSS feed.")

        return self._get_new_posts_rss(subreddit, limit, after)

    def get_all_new_posts(self, subreddit, stop_before_epoch=None, known_ids=None):
        """Paginate /new until reaching stop_before_epoch or a known post. Yields posts."""
        after = None
        while True:
            posts, next_after = self.get_new_posts(subreddit, after=after)
            if not posts:
                break
            for post in posts:
                if stop_before_epoch and post["created_epoch"] <= stop_before_epoch:
                    return
                if known_ids and post["id"] in known_ids:
                    return
                yield post
            if not next_after:
                break
            after = next_after
