"""Series detection and grouping for multi-part stories."""

import json
import os
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

NUM = r'\d+(?:\.\d+)?|[IVXLCDM]+'

# Spelled-out numbers, e.g. "Chapter Eighteen", "Part Twenty-One". Only ever
# used in keyword-gated patterns (after Part/Chapter/Episode/etc) — never in
# bare-number patterns, where a trailing word would create false matches.
_WORD_ONES = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
    "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_WORD_TENS = {
    "twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
    "seventy": 70, "eighty": 80, "ninety": 90,
}
# Longest-first so "eighteen" is tried before "eight", "seventeen" before
# "seven", etc. (regex alternation is ordered).
_WORD_TOKENS = sorted(list(_WORD_ONES) + list(_WORD_TENS), key=len, reverse=True)
WORDNUM = (
    r'(?:' + '|'.join(_WORD_TOKENS) + r')'
    r'(?:[-\s](?:' + '|'.join(_WORD_TOKENS) + r'))?'
)
# Keyword-gated number: digits, roman, or spelled-out.
NUM_KW = NUM + '|' + WORDNUM

PART_PATTERNS = [
    # === Multi-level: chapter-level marker AND a Part sub-marker (highest
    #     priority). "Series - Chapter 12 - Subtitle - Part II" means part II
    #     of chapter 12, not a series called "...Chapter 12 - Subtitle". The
    #     chapter is the primary number, the part the secondary; they are
    #     combined into a decimal (chapter 12, part 2 -> 12.02) so the parts
    #     slot into their place in the parent series in the right order. ===
    # Only within-book markers (Chapter/Episode) are treated as the primary
    # here — Book/Volume/Arc denote separate multi-book entries that stay as
    # distinct series (linked by grouping, not merged).
    (r'^(?P<series>.+?)\s*[-–—:,]\s*'
     r'(?:Chapter|Ch\.?|Episode|Ep\.?)\s*'
     r'(?P<chap>' + NUM_KW + r')\b'
     r'.*?[-–—:,(\[]\s*(?:Part|Pt\.?)\s*(?P<part>' + NUM_KW + r')\b',
     "Chapter"),

    # === Punctuation-separated patterns (highest priority) ===

    # "Title - Part 42" / "Title, Part 42" / "Title: Part XLII"
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Part|Pt\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Part"),
    # "Title (Part 42)" / "Title [Part 42]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<ptype>Part|Pt\.?)\s*(?P<num>' + NUM_KW + r')\s*[\)\]]',
     "Part"),
    # "Title - Chapter 42" / "Title, Ch. 3"
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Chapter|Ch\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Chapter"),
    # "Title (Chapter 42)" / "Title [Ch 3]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<ptype>Chapter|Ch\.?)\s*(?P<num>' + NUM_KW + r')\s*[\)\]]',
     "Chapter"),
    # "Title - Episode 5" / "Title, Ep 5"
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Episode|Ep\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Episode"),
    # "Title (Episode 5)" / "Title [Ep. 5]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<ptype>Episode|Ep\.?)\s*(?P<num>' + NUM_KW + r')\s*[\)\]]',
     "Episode"),
    # "Title - Book 2" / "Title, Volume 3" / "Title: Arc 4"
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Book|Volume|Vol\.?|Arc)\s*(?P<num>' + NUM_KW + r')\b',
     "Part"),

    # === Fraction pattern ===
    # "Title (3/10)" or "Title [3/10]"
    (r'^(?P<series>.+?)\s*[\(\[]\s*(?P<num>\d+)\s*/\s*\d+\s*[\)\]]',
     "Part"),

    # === Whitespace-only separator (lower priority) ===
    # "Title Part 42" / "Title Pt5"
    (r'^(?P<series>.+?)\s+(?P<ptype>Part|Pt\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Part"),
    # "Title Chapter 42" / "Title Ch3"
    (r'^(?P<series>.+?)\s+(?P<ptype>Chapter|Ch\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Chapter"),
    # "Title Episode 5" / "Title Ep5"
    (r'^(?P<series>.+?)\s+(?P<ptype>Episode|Ep\.?)\s*(?P<num>' + NUM_KW + r')\b',
     "Episode"),

    # === Number-before-keyword (Ralts-style) ===
    # "Title - 268 - Subtitle" (number between dashes, before arc/subtitle)
    (r'^(?P<series>.+?)\s*[-–—]\s*(?P<num>\d+)\s*[-–—]\s*.+',
     "Part"),

    # === Prologue / Epilogue / Interlude ===
    # "Title - Prologue" / "Title, Epilogue" / "Title - Interlude"
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Prologue)\b',
     "Prologue"),
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Epilogue)\b',
     "Epilogue"),
    (r'^(?P<series>.+?)\s*[-–—:,]\s*(?P<ptype>Interlude|Interlewd|Side\s*Story)\b',
     "Interlude"),

    # === Bare number patterns (lowest priority) ===
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
    raw = s.strip()
    key = raw.upper()
    if key in ROMAN_MAP:
        return ROMAN_MAP[key]
    try:
        return float(raw) if '.' in raw else int(raw)
    except ValueError:
        pass
    word = _word_to_int(raw)
    if word is not None:
        return word
    return _roman_to_int(key)


def _word_to_int(s):
    """Convert a spelled-out number ("eighteen", "twenty-one") to an int.

    Returns None if any token is not a recognised number word.
    """
    s = s.strip().lower().replace("-", " ")
    if not s:
        return None
    total = 0
    for tok in s.split():
        if tok in _WORD_ONES:
            total += _WORD_ONES[tok]
        elif tok in _WORD_TENS:
            total += _WORD_TENS[tok]
        else:
            return None
    return total


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
    # Strip common tag prefixes like [OC], [PI], etc.
    name = re.sub(r'^\[(?:OC|PI|WP|EU|IP|RF|TT|MP|SP|CC|CW)\]\s*', '', name, flags=re.IGNORECASE)
    # Strip wrapping brackets: [Series Name] → Series Name
    if name.startswith('['):
        close = name.find(']')
        if close == len(name) - 1:
            name = name[1:close]
    # Strip trailing chapter/part/episode markers (from multi-level titles),
    # including optional subtitles after the number: "Ch 16 - Grown Up".
    # The leading char class also allows an unclosed "(" or "[" so leaked
    # markers like "Prosthetist (Ch. 34" are stripped, with an optional
    # closing bracket tolerated. Book/Volume/Arc are deliberately NOT in the
    # keyword list — those denote separate multi-book entries that must stay
    # as distinct series (handled by grouping, not merging).
    name = re.sub(
        r'[,\s:(\[]+(?:Ch\.?|Chapter|Pt\.?|Part|Ep\.?|Episode)\s*\d+(?:\.\d+)?'
        r'\s*[\)\]]?'
        r'(?:\s*[-–—:].+)?\s*$',
        '', name, flags=re.IGNORECASE)
    # Strip trailing tag suffixes like [Comedy, Isekai, LitRPG]
    # Only strip if content is comma-separated tags (no lowercase-only words
    # longer than 20 chars, which likely indicates a meaningful title bracket)
    name = re.sub(r'\s*\[(?:[A-Za-z]+(?:,\s*[A-Za-z]+)+)\]\s*$', '', name)
    # Standardise keyword abbreviations so "Vol. 2" and "Volume 2" produce
    # the same slug (does not strip the keyword — just normalises spelling)
    name = re.sub(r'\bVol\.?(?=[\s\d]|$)', 'Volume ', name, flags=re.IGNORECASE)
    # Strip trailing punctuation (including comma and pipe)
    name = re.sub(r'[-–—:,\.\s|]+$', '', name)
    # Collapse whitespace
    name = re.sub(r'\s+', ' ', name).strip()
    return name


def _slugify(name):
    slug = unicodedata.normalize("NFKD", name)
    slug = slug.encode("ascii", "ignore").decode("ascii")
    slug = slug.lower()
    slug = re.sub(r'[^a-z0-9]+', '-', slug)
    slug = slug.strip('-')
    # Strip leading articles so "The X" and "X" produce the same slug
    stripped = re.sub(r'^(the|a|an)-', '', slug)
    if len(stripped) >= 3:
        slug = stripped
    if len(slug) > 80:
        slug = slug[:80].rstrip('-')
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
            groups = m.groupdict()

            # Multi-level "Chapter N ... Part M": chapter is the primary number,
            # part the secondary, encoded as a decimal so chapter 12 part 1/2
            # sort as 12.01 / 12.02 within the parent series.
            if groups.get("chap") is not None and groups.get("part") is not None:
                chap = _parse_number(groups["chap"])
                part = _parse_number(groups["part"])
                if (series_name and isinstance(chap, (int, float))
                        and isinstance(part, (int, float))):
                    return series_name, float(chap) + float(part) / 100.0, "Chapter"
                continue

            if default_ptype in ("Prologue", "Epilogue", "Interlude"):
                ptype = groups.get("ptype", default_ptype)
                if ptype:
                    ptype = ptype.strip().capitalize()
                    if ptype in ("Interlewd", "Side story"):
                        ptype = "Interlude"
                part_num = 0 if ptype == "Prologue" else None
                if series_name:
                    return series_name, part_num, ptype
                continue

            num_str = groups.get("num")
            if num_str is None:
                continue
            part_num = _parse_number(num_str)
            ptype = groups.get("ptype", default_ptype)
            if ptype:
                ptype = ptype.rstrip(".").capitalize()
                if ptype in ("Pt",):
                    ptype = "Part"
                elif ptype in ("Ch",):
                    ptype = "Chapter"
                elif ptype in ("Ep",):
                    ptype = "Episode"
                elif ptype in ("Vol",):
                    ptype = "Volume"
            else:
                ptype = default_ptype

            # Year-detection guard: for bare-number patterns (no keyword),
            # reject 4-digit numbers that look like years (1900–2099)
            if ptype == "Part" and "ptype" not in groups:
                if isinstance(part_num, (int, float)) and 1900 <= part_num <= 2099:
                    num_s = num_str.strip()
                    if len(num_s) == 4 and num_s.isdigit():
                        continue

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

def _load_post_metadata(posts_dir):
    """Load metadata for every post, using the reader cache if available.

    Returns a dict of post_id -> {id, title, author, created_utc, score}.
    No post bodies are loaded — this is the lightweight first pass.
    """
    posts_dir = Path(posts_dir)
    cache_file = posts_dir.parent / ".reader_cache.json"

    if cache_file.exists():
        print("  Loading metadata from reader cache...", flush=True)
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            cached = data.get("posts", {})
            if cached:
                all_posts = {}
                for pid, meta in cached.items():
                    all_posts[pid] = {
                        "id": str(meta.get("id", pid)),
                        "title": str(meta.get("title", "")),
                        "author": str(meta.get("author", "[deleted]")),
                        "created_utc": str(meta.get("created_utc", "")),
                        "score": meta.get("score", 0),
                    }
                print(f"  {len(all_posts)} posts loaded from cache.", flush=True)
                return all_posts
        except (json.JSONDecodeError, OSError):
            pass

    print("  No usable cache — scanning post files for metadata...", flush=True)
    all_posts = {}
    entries = [e.name for e in os.scandir(posts_dir) if e.name.endswith(".md")]
    total = len(entries)
    for i, name in enumerate(entries):
        pid = name[:-3]
        meta = _parse_frontmatter_only(posts_dir / name)
        if meta:
            all_posts[pid] = {
                "id": str(meta.get("id", pid)),
                "title": str(meta.get("title", "")),
                "author": str(meta.get("author", "[deleted]")),
                "created_utc": str(meta.get("created_utc", "")),
                "score": meta.get("score", 0),
            }
        if (i + 1) % 10000 == 0:
            print(f"  {i + 1}/{total}...", flush=True)
    print(f"  {len(all_posts)} posts scanned.", flush=True)
    return all_posts


def _parse_frontmatter_only(filepath):
    """Read just the YAML frontmatter without loading the full body."""
    try:
        with open(filepath, "r", encoding="utf-8") as f:
            first = f.read(8)
            if not first.startswith("---"):
                return None
            f.seek(0)
            text = f.read(2000)
    except (OSError, UnicodeDecodeError):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    result = {}
    for line in parts[1].strip().splitlines():
        line = line.rstrip()
        if line.startswith("  "):
            continue
        if ": " in line:
            key, _, val = line.partition(": ")
            key = key.strip()
            val = val.strip().strip('"')
            if val in ("true", "false"):
                val = val == "true"
            else:
                try:
                    val = int(val)
                except ValueError:
                    if val.lower() not in ('inf', '-inf', 'nan', 'infinity'):
                        try:
                            val = float(val)
                        except ValueError:
                            pass
            result[key] = val
    return result


def _order_parts(parts):
    """Return a series' parts in reading order.

    Numbered parts are ordered by their explicit part number — the author's
    stated reading order, which is more reliable than post dates (a date shifts
    to the edit date when a post is edited, so it no longer reflects when the
    part was first published).

    Unnumbered parts (interludes, epilogues, un-numbered prologues) have only a
    date, so they are interleaved into the numbered sequence by that date:
    anchored just after the furthest-numbered part that already existed when
    they were posted, or before every numbered part if they predate them all.
    An unnumbered part with no date at all goes last (nothing to place it by).
    """
    numbered_nums = sorted(
        float(p["part_num"]) for p in parts if p.get("part_num") is not None
    )

    def sort_pos(p):
        num = p.get("part_num")
        if num is not None:
            return float(num)
        ts = p.get("created_utc", "")
        if not ts:
            return float("inf")
        before = [
            float(q["part_num"]) for q in parts
            if q.get("part_num") is not None and q.get("created_utc")
            and q["created_utc"] <= ts
        ]
        if before:
            return max(before) + 0.5
        if numbered_nums:
            return numbered_nums[0] - 0.5
        return 0.0

    return sorted(parts, key=lambda p: (sort_pos(p), p.get("created_utc", "")))


def detect_series(posts_dir):
    """
    Scan all post files in a directory and detect series.

    Uses a two-pass approach for scalability:
      Pass 1 — load metadata only (from cache or frontmatter) and match
               title patterns. No post bodies are read.
      Pass 2 — read full bodies only for posts in detected series to
               extract navigation links and discover additional parts.

    Args:
        posts_dir: Path to the posts/ directory for a subreddit

    Returns:
        Dict of series_slug -> {
            "name": str,
            "slug": str,
            "parts": [{post_id, title, part_num, part_type, created_utc, score}, ...],
            "author": str,
            "detection_method": str,
        }
    """
    posts_dir = Path(posts_dir)
    if not posts_dir.exists():
        return {}

    # ── Pass 1: metadata-only title matching ──────────────────────────
    all_posts = _load_post_metadata(posts_dir)
    if not all_posts:
        return {}

    print(f"  Running title pattern matching on {len(all_posts)} posts...",
          flush=True)

    title_candidates = defaultdict(list)
    matched = 0
    for post_id, meta in all_posts.items():
        title = meta.get("title", "")
        series_name, part_num, part_type = extract_series_info(title)
        if series_name:
            slug = _slugify(series_name)
            title_candidates[slug].append({
                **meta,
                "series_name": series_name,
                "part_num": part_num,
                "part_type": part_type,
            })
            matched += 1

    print(f"  {matched} posts matched a title pattern across "
          f"{len(title_candidates)} candidate groups.", flush=True)

    # Filter to clusters with 2+ posts
    series_map = {}
    for slug, candidates in title_candidates.items():
        if len(candidates) < 2:
            continue
        name_counts = defaultdict(int)
        for c in candidates:
            name_counts[c["series_name"]] += 1
        best_name = max(name_counts, key=name_counts.get)
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

    print(f"  {len(series_map)} series confirmed (2+ parts each).", flush=True)

    # ── Pass 2: nav-link extraction on series posts only ──────────────
    series_post_ids = set()
    for series in series_map.values():
        for p in series["parts"]:
            series_post_ids.add(p["id"])

    print(f"  Reading {len(series_post_ids)} post bodies for nav links...",
          flush=True)

    nav_graph = {}
    done = 0
    for post_id in series_post_ids:
        filepath = posts_dir / f"{post_id}.md"
        if not filepath.exists():
            continue
        parsed = parse_post_file(filepath)
        if not parsed:
            continue
        links = extract_nav_links(parsed.get("_body", ""))
        if links:
            nav_graph[post_id] = links
        done += 1
        if done % 5000 == 0:
            print(f"  {done}/{len(series_post_ids)}...", flush=True)

    print(f"  {len(nav_graph)} posts had navigation links.", flush=True)

    # Use nav links to add missing posts to existing series
    # Build a reverse index: post_id -> series_slug
    post_to_series = {}
    for slug, series in series_map.items():
        for p in series["parts"]:
            post_to_series[p["id"]] = slug

    additions = 0
    for post_id, links in nav_graph.items():
        series_slug = post_to_series.get(post_id)
        if not series_slug:
            continue
        series = series_map[series_slug]
        part_ids = {p["id"] for p in series["parts"]}

        for link_type, linked_id in links.items():
            # Skip if the linked post already belongs to a series — either its
            # own title-matched home or an earlier nav claim. Pulling it in
            # here would duplicate it across series. Only posts that are not
            # yet claimed anywhere are added via navigation links.
            if (linked_id in part_ids
                    or linked_id not in all_posts
                    or linked_id in post_to_series):
                continue
            linked_meta = all_posts[linked_id]
            _, linked_num, linked_ptype = extract_series_info(
                linked_meta.get("title", ""))
            series["parts"].append({
                **linked_meta,
                "series_name": series["name"],
                "part_num": linked_num,
                "part_type": linked_ptype,
            })
            post_to_series[linked_id] = series_slug
            series["detection_method"] = "title_pattern+nav_links"
            additions += 1

    if additions:
        print(f"  {additions} additional posts added via nav links.", flush=True)

    # ── Finalise: deduplicate, validate outliers, and order ─────────────
    for slug, series in series_map.items():
        seen = set()
        unique_parts = []
        for p in series["parts"]:
            if p["id"] not in seen:
                seen.add(p["id"])
                unique_parts.append(p)

        # Outlier validation: if a part number is > 3x the total post count
        # AND > 50, downgrade it to None (date-ordered). This catches bogus
        # numbers like 1,700,231 in a 4-post series without capping genuine
        # long-running series.
        total = len(unique_parts)
        threshold = max(total * 3, 50)
        for p in unique_parts:
            num = p.get("part_num")
            if num is not None and isinstance(num, (int, float)) and num > threshold:
                p["part_num"] = None

        series["parts"] = _order_parts(unique_parts)

    # ── Global uniqueness: each post belongs to exactly one series ──────
    # Even after the nav-link guard, a post can be reached as a member of
    # more than one series when fragments of the same story exist as
    # separate slugs. Resolve every such post to a single home: its own
    # title-matched series if it is a member there, otherwise the largest
    # series it appears in (deterministic tie-break on slug). Remove it
    # from all other series so no post ID is ever duplicated across series.
    post_membership = defaultdict(list)  # post_id -> [slug, ...]
    for slug, series in series_map.items():
        for p in series["parts"]:
            post_membership[p["id"]].append(slug)

    resolved = 0
    for post_id, slugs in post_membership.items():
        if len(slugs) < 2:
            continue
        title = all_posts.get(post_id, {}).get("title", "")
        home_name, _, _ = extract_series_info(title)
        home_slug = _slugify(home_name) if home_name else None
        if home_slug in slugs:
            keep = home_slug
        else:
            keep = max(slugs, key=lambda s: (len(series_map[s]["parts"]), s))
        for s in slugs:
            if s != keep:
                series_map[s]["parts"] = [
                    p for p in series_map[s]["parts"] if p["id"] != post_id
                ]
                resolved += 1

    if resolved:
        print(f"  {resolved} duplicate memberships resolved — every post now "
              f"belongs to exactly one series.", flush=True)

    # Drop series that fell below 2 parts after deduplication.
    series_map = {
        slug: s for slug, s in series_map.items() if len(s["parts"]) >= 2
    }

    # Apply manual curation overrides last (they win over auto-detection).
    series_map = _apply_overrides(series_map, all_posts,
                                  posts_dir.parent / "_overrides.json")

    return series_map


# ─── Manual curation overrides ──────────────────────────────────────────────

def _apply_overrides(series_map, all_posts, overrides_path):
    """Apply hand-curated series definitions from `_overrides.json`.

    Each override declares the canonical membership and order of a series that
    auto-detection cannot reconstruct (e.g. inconsistent titles, duplicate
    reposts). For each one: pull the listed posts into a single series in the
    given order, remove those posts (and any excluded junk) from every
    auto-detected series, and add the curated series with method
    "manual_override". This is purely additive — everything not named in an
    override is still auto-detected.
    """
    overrides_path = Path(overrides_path)
    if not overrides_path.exists():
        return series_map
    try:
        data = json.loads(overrides_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        print(f"  WARNING: could not read {overrides_path.name}: {exc}",
              flush=True)
        return series_map

    overrides = data.get("series", [])
    if not overrides:
        return series_map

    # Every post claimed by, or excluded by, any override is removed from the
    # auto-detected series so it cannot appear in two places.
    drop = set()
    for ov in overrides:
        drop.update(p["id"] for p in ov.get("parts", []))
        drop.update(ov.get("exclude", []))
    for series in series_map.values():
        series["parts"] = [p for p in series["parts"] if p["id"] not in drop]

    # Build each curated series in its declared order.
    applied = 0
    for ov in overrides:
        parts = []
        for entry in ov.get("parts", []):
            meta = all_posts.get(entry["id"])
            if not meta:
                continue  # listed post is not in this corpus
            parts.append({
                **meta,
                "series_name": ov["name"],
                "part_num": entry.get("num"),
                "part_type": entry.get("type", "Chapter"),
            })
        if len(parts) < 2:
            continue
        slug = ov.get("slug") or _slugify(ov["name"])
        series_map[slug] = {
            "name": ov["name"],
            "slug": slug,
            "author": ov.get("author", parts[0].get("author", "[deleted]")),
            "parts": parts,  # explicit curated order — not re-sorted
            "detection_method": "manual_override",
            "note": ov.get("note", ""),
            "related_text": ov.get("related_text", ""),
            "related_url": ov.get("related_url", ""),
        }
        applied += 1

    # Drop any auto-detected series emptied below 2 parts by the removals.
    series_map = {
        s: v for s, v in series_map.items()
        if len(v["parts"]) >= 2 or v.get("detection_method") == "manual_override"
    }

    if applied:
        print(f"  {applied} manual-override series applied.", flush=True)
    return series_map


# ─── Series output ──────────────────────────────────────────────────────────

def build_series_indexes(series_map, collections_dir, series_dir=None):
    """
    Write series index files to the series/ directory.

    Each series gets a single `_index.md` holding the full ordered table of
    parts. This is the only file the reader consumes; no per-part reference
    stubs are written.

    Args:
        series_map: Output from detect_series()
        collections_dir: Path to collections/<subreddit>/
        series_dir: Optional explicit output directory. Defaults to
            collections_dir / "series". Used to build into a temporary
            directory for an atomic swap.
    """
    collections_dir = Path(collections_dir)
    series_dir = Path(series_dir) if series_dir is not None else collections_dir / "series"

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
        ]
        # Optional curated "related works" cross-link (manual overrides).
        if series.get("related_text"):
            index_lines.append(f'related_text: "{_escape(series["related_text"])}"')
        if series.get("related_url"):
            index_lines.append(f'related_url: "{_escape(series["related_url"])}"')
        index_lines += [
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
            num = part.get("part_num")
            if num is None:
                # Show a named marker (Prologue/Epilogue/Interlude) where we
                # have one, otherwise "?" for a genuinely unknown number.
                pt = part.get("part_type") or ""
                num_display = pt if pt in ("Prologue", "Epilogue", "Interlude") else "?"
            elif isinstance(num, float) and num == int(num):
                num_display = int(num)
            else:
                num_display = num
            date_display = part.get("created_utc", "")[:10]
            title_display = part.get("title", "Untitled").replace("|", r"\|")
            score_display = part.get("score", 0)
            post_id = part["id"]
            index_lines.append(
                f"| {num_display} | {title_display} | {date_display} "
                f"| {score_display} | {post_id} |"
            )

        index_lines.append("")
        with (target_dir / "_index.md").open(
            "w", encoding="utf-8", newline="\n"
        ) as fh:
            fh.write("\n".join(index_lines))


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


# ─── Series grouping (Phase 3) ──────────────────────────────────────────────
#
# A group LINKS related series — multi-book series ("The Swarm" + "Volume 2"),
# multi-arc stories, and multi-level chapter splits — without MERGING them.
# Each member keeps its own index and ordering; the group only provides a
# navigable parent that lists members chronologically. Chapter numbers restart
# per book and part numbers repeat per chapter, so merging would break reading
# order — grouping is the safe way to connect them.

_GROUP_MARKER = (
    r'(?:Volume|Vol\.?|Book|Bk\.?|Arc|Season|Saga|Part|Pt\.?|'
    r'Chapter|Ch\.?|Episode|Ep\.?)'
)
_GROUP_NUMTOK = r'(?:\d+(?:\.\d+)?|[IVXLCDM]+|' + WORDNUM + r')'
_GROUP_TRAIL_MARKER = re.compile(
    r'[\s,:\-–—(\[]+' + _GROUP_MARKER + r'\s*' + _GROUP_NUMTOK +
    r'\s*[\)\]]?(?:\s*[-–—:].*)?$', re.IGNORECASE)
_GROUP_TRAIL_NUM = re.compile(
    r'[\s,:\-–—(\[]+' + _GROUP_NUMTOK + r'\s*[\)\]]?$', re.IGNORECASE)
_GROUP_LEAD_TAG = re.compile(r'^\s*\[[^\]]{1,30}\]\s*')


def _strip_group_markers(name):
    """Strip trailing volume/book/arc/chapter markers and bare numbers."""
    root = name
    for _ in range(3):  # handles stacked markers, e.g. "... Book 2 Part 3"
        new = _GROUP_TRAIL_MARKER.sub('', root).strip()
        if new == root:
            break
        root = new
    return _GROUP_TRAIL_NUM.sub('', root).strip()


def _group_display_name(name):
    """Clean, human-readable group name derived from a member's title."""
    base = _strip_group_markers(_GROUP_LEAD_TAG.sub('', name).strip())
    # Reject a base that is trivial or just a bare marker word ("Chapter"):
    # in that case the leading bracket was the actual title, not a throwaway
    # tag (e.g. "[House of the Golden Oak] Chapter II"), so keep its content.
    is_bare_marker = re.fullmatch(_GROUP_MARKER, base.strip(), re.IGNORECASE)
    if len(_slugify(base)) >= 5 and not is_bare_marker:
        return base.strip(' -–—:,')
    unbracketed = name.replace('[', ' ').replace(']', ' ')
    return re.sub(r'\s+', ' ', _strip_group_markers(unbracketed)).strip(' -–—:,')


def _group_root(name):
    """Reduce a series name to a slug shared by all related series."""
    return _slugify(_group_display_name(name))


def detect_series_groups(series_map):
    """Cluster related series into groups keyed by (author, group root).

    Returns a dict of group_slug -> {
        group_slug, name, author,
        members: [series_slug, ...]   # ordered by first post date
    }. Only groups with 2+ member series are returned. Author is used as a
    guard so distinct stories that merely share a name prefix do not merge.
    """
    def _first_posted(slug):
        parts = series_map[slug]["parts"]
        return min((p.get("created_utc", "") for p in parts
                    if p.get("created_utc")), default="")

    clusters = defaultdict(list)
    for slug, series in series_map.items():
        root = _group_root(series["name"])
        if len(root) < 5:
            continue
        clusters[(series["author"], root)].append(slug)

    groups = {}
    for (author, root), member_slugs in clusters.items():
        if len(member_slugs) < 2:
            continue
        members = sorted(member_slugs, key=_first_posted)
        # Display name from the earliest member (usually the cleanly-named base).
        group_name = _group_display_name(series_map[members[0]]["name"])
        groups[root] = {
            "group_slug": root,
            "name": group_name,
            "author": author,
            "members": members,
        }
    return groups


def write_groups(groups, series_dir):
    """Write the group index to `<series_dir>/_groups.json`.

    Stores the groups plus a reverse series->group map for breadcrumbs.
    """
    series_dir = Path(series_dir)
    series_to_group = {}
    for root, g in groups.items():
        for slug in g["members"]:
            series_to_group[slug] = root
    payload = {
        "groups": groups,
        "series_to_group": series_to_group,
    }
    with (series_dir / "_groups.json").open(
        "w", encoding="utf-8", newline="\n"
    ) as fh:
        fh.write(json.dumps(payload, indent=2, ensure_ascii=False))
