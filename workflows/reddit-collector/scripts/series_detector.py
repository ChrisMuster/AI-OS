"""Series detection and grouping for multi-part stories."""

import re
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from post_formatter import parse_post_file


# ─── Title pattern extraction ───────────────────────────────────────────────

ROMAN_MAP = {
    "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
    "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
    "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18, "XIX": 19,
    "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23, "XXIV": 24, "XXV": 25,
    "XXX": 30, "XL": 40, "L": 50, "LX": 60, "LXX": 70, "LXXX": 80,
    "XC": 90, "C": 100,
}

PART_PATTERNS = [
    # "Title - Part 42" / "Title — Part XLII"
    (r'^(?P<series>.+?)\s*[-–—:]\s*(?P<ptype>Part|Pt\.?)\s*(?P<num>\d+|[IVXLCDM]+)\b',
     "Part"),
    # "Title (Part 42)" / "Title [Part 42]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<ptype>Part|Pt\.?)\s*(?P<num>\d+|[IVXLCDM]+)\s*[\)\]]',
     "Part"),
    # "Title - Chapter 42"
    (r'^(?P<series>.+?)\s*[-–—:]\s*(?P<ptype>Chapter|Ch\.?)\s*(?P<num>\d+|[IVXLCDM]+)\b',
     "Chapter"),
    # "Title (Chapter 42)"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<ptype>Chapter|Ch\.?)\s*(?P<num>\d+|[IVXLCDM]+)\s*[\)\]]',
     "Chapter"),
    # "Title (3/10)" or "Title [3/10]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<num>\d+)\s*/\s*\d+\s*[\)\]]',
     "Part"),
    # "Title - 42" (dash then bare number)
    (r'^(?P<series>.+?)\s*[-–—]\s*(?P<num>\d+)\s*$',
     "Part"),
    # "Title 42" (bare trailing number — only used if 2+ matches in corpus)
    (r'^(?P<series>.+?)\s+(?P<num>\d+)\s*$',
     "Part"),
]

# Navigation link patterns in post body
NAV_LINK_PATTERNS = [
    (r'\[(?:First|Beginning|Start)\]\s*\(\s*(https?://[^\s\)]+)\s*\)', "first"),
    (r'\[(?:Prev(?:ious)?)\]\s*\(\s*(https?://[^\s\)]+)\s*\)', "prev"),
    (r'\[(?:Next)\]\s*\(\s*(https?://[^\s\)]+)\s*\)', "next"),
    (r'\[(?:Last|Latest)\]\s*\(\s*(https?://[^\s\)]+)\s*\)', "last"),
]

REDDIT_POST_ID_PATTERN = re.compile(
    r'reddit\.com/r/\w+/comments/([a-z0-9]+)'
)


def _parse_number(s):
    s = s.strip().upper()
    if s in ROMAN_MAP:
        return ROMAN_MAP[s]
    try:
        return int(s)
    except ValueError:
        return _roman_to_int(s)


def _roman_to_int(s):
    roman_vals = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}
    total = 0
    prev = 0
    for ch in reversed(s.upper()):
        val = roman_vals.get(ch, 0)
        if val == 0:
            return None
        if val < prev:
            total -= val
        else:
            total += val
        prev = val
    return total if total > 0 else None


def _normalise_series_name(name):
    name = name.strip()
    # Strip common prefixes/tags
    name = re.sub(r'^\[(?:OC|PI|WP|EU|IP|RF|TT|MP|SP|CC|CW)\]\s*', '', name, flags=re.IGNORECASE)
    # Strip trailing punctuation
    name = re.sub(r'[-–—:,\.\s]+$', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _slugify(name):
    slug = unicodedata.normalize("NFKD", name)
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = slug.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    return slug or "untitled"


def extract_series_info(title):
    """
    Try to extract series name and part number from a post title.
    Returns (series_name, part_number, part_type) or (None, None, None).
    """
    for pattern, default_ptype in PART_PATTERNS:
        m = re.match(pattern, title, re.IGNORECASE)
        if m:
            series_name = _normalise_series_name(m.group("series"))
            num_str = m.group("num")
            part_num = _parse_number(num_str)
            ptype = m.groupdict().get("ptype", default_ptype)
            if ptype:
                ptype = ptype.rstrip(".").capitalize()
                if ptype in ("Pt", "Pt."):
                    ptype = "Part"
                elif ptype in ("Ch", "Ch."):
                    ptype = "Chapter"
            else:
                ptype = default_ptype
            if series_name and part_num is not None:
                return series_name, part_num, ptype
    return None, None, None


def extract_nav_links(body):
    """Extract navigation links (first, prev, next, last) from post body."""
    links = {}
    for pattern, link_type in NAV_LINK_PATTERNS:
        m = re.search(pattern, body, re.IGNORECASE)
        if m:
            url = m.group(1)
            post_id_match = REDDIT_POST_ID_PATTERN.search(url)
            if post_id_match:
                links[link_type] = post_id_match.group(1)
    return links


# ─── Series detection engine ────────────────────────────────────────────────

def detect_series(posts_dir):
    """
    Scan all post files in a directory and detect series.

    Args:
        posts_dir: Path to the posts/ directory for a subreddit

    Returns:
        Dict of series_slug -> {
            "name": str,
            "slug": str,
            "parts": [{post_id, title, part_num, part_type, created_utc, score, filepath}, ...],
            "author": str,
            "detection_method": str,
        }
    """
    posts_dir = Path(posts_dir)
    if not posts_dir.exists():
        return {}

    # Phase 1: Parse all posts and extract title patterns
    all_posts = {}
    title_candidates = defaultdict(list)
    nav_graph = {}  # post_id -> {first, prev, next, last}

    for filepath in sorted(posts_dir.glob("*.md")):
        parsed = parse_post_file(filepath)
        if not parsed:
            continue

        post_id = parsed.get("id", filepath.stem)
        title = parsed.get("title", "")
        body = parsed.get("_body", "")

        all_posts[post_id] = {
            "id": post_id,
            "title": title,
            "author": parsed.get("author", "[deleted]"),
            "created_utc": parsed.get("created_utc", ""),
            "score": parsed.get("score", 0),
            "filepath": str(filepath),
        }

        # Extract title-based series info
        series_name, part_num, part_type = extract_series_info(title)
        if series_name:
            slug = _slugify(series_name)
            title_candidates[slug].append({
                **all_posts[post_id],
                "series_name": series_name,
                "part_num": part_num,
                "part_type": part_type,
            })

        # Extract nav links
        links = extract_nav_links(body)
        if links:
            nav_graph[post_id] = links

    # Phase 2: Filter to clusters with 2+ posts
    series_map = {}
    for slug, candidates in title_candidates.items():
        if len(candidates) < 2:
            continue
        # Use the most common series name variant
        name_counts = defaultdict(int)
        for c in candidates:
            name_counts[c["series_name"]] += 1
        best_name = max(name_counts, key=name_counts.get)
        # Use the most common author
        author_counts = defaultdict(int)
        for c in candidates:
            author_counts[c["author"]] += 1
        primary_author = max(author_counts, key=author_counts.get)

        series_map[slug] = {
            "name": best_name,
            "slug": slug,
            "author": primary_author,
            "parts": candidates,
            "detection_method": "title_pattern",
        }

    # Phase 3: Use nav links to confirm or discover series
    # Build chains from nav links
    for post_id, links in nav_graph.items():
        if post_id not in all_posts:
            continue
        for link_type, linked_id in links.items():
            if linked_id not in all_posts:
                continue
            # Check if both posts are already in the same series
            already_grouped = False
            for slug, series in series_map.items():
                part_ids = {p["id"] for p in series["parts"]}
                if post_id in part_ids and linked_id in part_ids:
                    already_grouped = True
                    series["detection_method"] = "title_pattern+nav_links"
                    break
            if already_grouped:
                continue

            # Check if one is in a series but the other isn't — add it
            for slug, series in series_map.items():
                part_ids = {p["id"] for p in series["parts"]}
                if post_id in part_ids and linked_id not in part_ids:
                    linked_post = all_posts[linked_id]
                    series["parts"].append({
                        **linked_post,
                        "series_name": series["name"],
                        "part_num": None,
                        "part_type": None,
                    })
                    series["detection_method"] = "title_pattern+nav_links"
                    break
                elif linked_id in part_ids and post_id not in part_ids:
                    this_post = all_posts[post_id]
                    series["parts"].append({
                        **this_post,
                        "series_name": series["name"],
                        "part_num": None,
                        "part_type": None,
                    })
                    series["detection_method"] = "title_pattern+nav_links"
                    break

    # Phase 4: Order parts within each series
    for slug, series in series_map.items():
        # Deduplicate by post ID
        seen = set()
        unique_parts = []
        for p in series["parts"]:
            if p["id"] not in seen:
                seen.add(p["id"])
                unique_parts.append(p)
        # Sort by part number if available, then by timestamp
        def sort_key(p):
            num = p.get("part_num")
            ts = p.get("created_utc", "")
            return (num if num is not None else 99999, ts)
        unique_parts.sort(key=sort_key)
        series["parts"] = unique_parts

    return series_map


# ─── Series output ──────────────────────────────────────────────────────────

def build_series_indexes(series_map, collections_dir):
    """
    Write series index files and reference files to the series/ directory.

    Args:
        series_map: Output from detect_series()
        collections_dir: Path to collections/<subreddit>/
    """
    collections_dir = Path(collections_dir)
    series_dir = collections_dir / "series"

    for slug, series in series_map.items():
        target_dir = series_dir / slug
        target_dir.mkdir(parents=True, exist_ok=True)

        parts = series["parts"]
        first_posted = min((p["created_utc"] for p in parts if p["created_utc"]), default="")
        last_posted = max((p["created_utc"] for p in parts if p["created_utc"]), default="")
        total_score = sum(p.get("score", 0) for p in parts)

        # Determine status
        status = _infer_status(last_posted)

        # Write _index.md
        now = datetime.now(timezone.utc).isoformat()
        index_lines = [
            "---",
            f'series_name: "{_escape(series["name"])}"',
            f'series_slug: "{slug}"',
            f'author: "{_escape(series["author"])}"',
            f'total_parts: {len(parts)}',
            f'first_posted: "{first_posted}"',
            f'last_posted: "{last_posted}"',
            f'status: "{status}"',
            f'total_score: {total_score}',
            f'detection_method: "{series["detection_method"]}"',
            f'last_indexed: "{now}"',
            "---",
            "",
            f'# {series["name"]}',
            "",
            f'**Author:** {series["author"]}',
            f'**Parts:** {len(parts)} | **Status:** {status.capitalize()}',
            f'**First posted:** {first_posted[:10]} | **Last posted:** {last_posted[:10]}',
            "",
            "## Parts",
            "",
            "| # | Title | Date | Score | Post ID |",
            "|---|-------|------|-------|---------|",
        ]

        for i, part in enumerate(parts, 1):
            num_display = part.get("part_num", i)
            date_display = part.get("created_utc", "")[:10]
            title_display = part.get("title", "Untitled")
            score_display = part.get("score", 0)
            post_id = part["id"]
            index_lines.append(
                f"| {num_display} | {title_display} | {date_display} "
                f"| {score_display} | {post_id} |"
            )

        index_lines.append("")
        (target_dir / "_index.md").write_text(
            "\n".join(index_lines), encoding="utf-8"
        )

        # Write reference files
        for i, part in enumerate(parts, 1):
            num = str(i).zfill(3)
            part_slug = _slugify(part.get("title", "untitled"))[:60]
            ref_name = f"{num}-{part_slug}.md"
            post_id = part["id"]
            ref_path = f"../../posts/{post_id}.md"
            ref_content = (
                "---\n"
                f'ref: "{ref_path}"\n'
                f'post_id: "{post_id}"\n'
                f"series_part: {i}\n"
                "---\n"
                f"\nSee [{ref_path}]({ref_path})\n"
            )
            (target_dir / ref_name).write_text(ref_content, encoding="utf-8")


def _infer_status(last_posted_iso):
    if not last_posted_iso:
        return "unknown"
    try:
        last_dt = datetime.fromisoformat(last_posted_iso)
        now = datetime.now(timezone.utc)
        days = (now - last_dt).days
        if days < 90:
            return "ongoing"
        elif days < 365:
            return "hiatus"
        else:
            return "complete"
    except (ValueError, TypeError):
        return "unknown"


def _escape(s):
    return (s or "").replace('"', '\\"')
