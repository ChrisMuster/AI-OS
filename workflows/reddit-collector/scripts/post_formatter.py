"""Convert Reddit post data to/from Markdown files with YAML frontmatter."""

import html
import re
from datetime import datetime, timezone
from pathlib import Path


def format_post(post, series_info=None):
    """Convert a Reddit post dict into a Markdown string with YAML frontmatter."""
    fm_lines = [
        "---",
        f'id: "{post["id"]}"',
        f'title: "{_escape_yaml(post["title"])}"',
        f'author: "{_escape_yaml(post["author"])}"',
        f'subreddit: "{post["subreddit"]}"',
        f'created_utc: "{post["created_utc"]}"',
        f'score: {post["score"]}',
        f'upvote_ratio: {post["upvote_ratio"]}',
        f'flair: "{_escape_yaml(post.get("flair") or "")}"',
        f'url: "{post["permalink"]}"',
        f'is_self: {str(post["is_self"]).lower()}',
        f'num_comments: {post["num_comments"]}',
    ]

    if post.get("crosspost_parent"):
        fm_lines.append(f'crosspost_parent: "{post["crosspost_parent"]}"')

    if series_info:
        fm_lines.append("series:")
        fm_lines.append(f'  name: "{_escape_yaml(series_info["name"])}"')
        fm_lines.append(f'  slug: "{series_info["slug"]}"')
        if series_info.get("part") is not None:
            fm_lines.append(f'  part: {series_info["part"]}')
        if series_info.get("part_type"):
            fm_lines.append(f'  part_type: "{series_info["part_type"]}"')

    collected = datetime.now(timezone.utc).isoformat()
    fm_lines.append(f'collected_utc: "{collected}"')
    fm_lines.append("---")

    body = _clean_body(post.get("selftext", ""))
    title = post["title"]

    return "\n".join(fm_lines) + f"\n\n# {title}\n\n{body}\n"


def parse_post_file(filepath):
    """Read a post Markdown file and extract frontmatter fields and body."""
    text = Path(filepath).read_text(encoding="utf-8")
    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    fm_text = parts[1].strip()
    body = parts[2].strip()
    # Remove the leading "# Title" line from body for the raw selftext
    body_lines = body.split("\n", 1)
    if body_lines and body_lines[0].startswith("# "):
        body = body_lines[1].strip() if len(body_lines) > 1 else ""

    result = {"_body": body, "_series": {}}
    in_series = False
    for line in fm_text.splitlines():
        line = line.rstrip()
        if line == "series:":
            in_series = True
            continue
        if in_series and line.startswith("  "):
            key, _, val = line.strip().partition(": ")
            result["_series"][key] = _unquote(val)
        else:
            in_series = False
            if ": " in line:
                key, _, val = line.partition(": ")
                result[key.strip()] = _unquote(val)

    return result


def _escape_yaml(s):
    if not s:
        return ""
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _unquote(val):
    val = val.strip()
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    if val in ("true", "false"):
        return val == "true"
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _clean_body(selftext):
    if not selftext or selftext in ("[deleted]", "[removed]"):
        return "*[Content removed by author or moderators]*"
    text = html.unescape(selftext)
    # Normalise line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return text
