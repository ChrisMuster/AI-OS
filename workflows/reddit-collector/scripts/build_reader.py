"""Local web server for browsing collected Reddit posts and series."""

import argparse
import atexit
import html
import json
import os
import re
import secrets
import sys
import webbrowser
from datetime import datetime, timezone
from functools import lru_cache
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

SCRIPT_DIR = Path(__file__).resolve().parent
WORKFLOW_DIR = SCRIPT_DIR.parent
PROJECT_ROOT = WORKFLOW_DIR.parent.parent
COLLECTIONS = WORKFLOW_DIR / "collections"
PID_FILE = WORKFLOW_DIR / ".reader_server.pid"

sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(PROJECT_ROOT / "workflows" / "biblio-tools" / "scripts"))
from post_formatter import parse_post_file
from runtime import process_is_alive


# ─── Markdown to HTML ──────────────────────────────────────────────────────

def md_to_html(text):
    """Convert simple Reddit-flavour Markdown to HTML."""
    if not text:
        return ""

    lines = text.split("\n")
    out = []
    in_list = None
    in_blockquote = False
    in_code_block = False
    paragraph = []

    def flush_paragraph():
        if paragraph:
            p = " ".join(paragraph)
            out.append(f"<p>{_inline(p)}</p>")
            paragraph.clear()

    def close_list():
        nonlocal in_list
        if in_list:
            out.append(f"</{in_list}>")
            in_list = None

    def close_blockquote():
        nonlocal in_blockquote
        if in_blockquote:
            out.append("</blockquote>")
            in_blockquote = False

    for line in lines:
        stripped = line.strip()

        if in_code_block:
            if stripped.startswith("```"):
                out.append("</code></pre>")
                in_code_block = False
            else:
                out.append(html.escape(line))
            continue

        if stripped.startswith("```"):
            flush_paragraph()
            close_list()
            close_blockquote()
            in_code_block = True
            out.append("<pre><code>")
            continue

        if not stripped:
            flush_paragraph()
            close_list()
            close_blockquote()
            continue

        if stripped.startswith("#"):
            flush_paragraph()
            close_list()
            close_blockquote()
            m = re.match(r'^(#{1,6})\s+(.*)', stripped)
            if m:
                level = len(m.group(1))
                heading = _inline(m.group(2))
                out.append(f"<h{level}>{heading}</h{level}>")
                continue

        if stripped in ("---", "***", "___"):
            flush_paragraph()
            close_list()
            close_blockquote()
            out.append("<hr>")
            continue

        if stripped.startswith("> ") or stripped == ">":
            flush_paragraph()
            close_list()
            if not in_blockquote:
                in_blockquote = True
                out.append("<blockquote>")
            content = stripped[2:] if stripped.startswith("> ") else ""
            out.append(f"<p>{_inline(content)}</p>" if content else "")
            continue

        li_match = re.match(r'^[-*+]\s+(.*)', stripped)
        if li_match:
            flush_paragraph()
            close_blockquote()
            if in_list != "ul":
                close_list()
                in_list = "ul"
                out.append("<ul>")
            out.append(f"<li>{_inline(li_match.group(1))}</li>")
            continue

        ol_match = re.match(r'^\d+[.)]\s+(.*)', stripped)
        if ol_match:
            flush_paragraph()
            close_blockquote()
            if in_list != "ol":
                close_list()
                in_list = "ol"
                out.append("<ol>")
            out.append(f"<li>{_inline(ol_match.group(1))}</li>")
            continue

        close_list()
        close_blockquote()
        paragraph.append(stripped)

    flush_paragraph()
    close_list()
    close_blockquote()
    if in_code_block:
        out.append("</code></pre>")

    return "\n".join(out)


def _inline(text):
    """Convert inline Markdown: bold, italic, code, links, strikethrough."""
    text = html.escape(text)
    parts = text.split("`")
    if len(parts) >= 3:
        result = []
        i = 0
        while i < len(parts):
            if i + 1 < len(parts) and i % 2 == 0:
                result.append(parts[i])
                i += 1
                result.append(f"<code>{parts[i]}</code>")
            else:
                result.append(parts[i])
            i += 1
        text = "".join(result)
    text = re.sub(
        r'\[([^\]]+)\]\(([^)]+)\)',
        lambda m: f'<a href="{m.group(2)}" rel="noopener">{m.group(1)}</a>',
        text,
    )
    text = re.sub(r'\*\*\*(.+?)\*\*\*', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'___(.+?)___', r'<strong><em>\1</em></strong>', text)
    text = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', text)
    text = re.sub(r'__(.+?)__', r'<strong>\1</strong>', text)
    text = re.sub(r'\*(.+?)\*', r'<em>\1</em>', text)
    text = re.sub(r'(?<!\w)_(.+?)_(?!\w)', r'<em>\1</em>', text)
    text = re.sub(r'~~(.+?)~~', r'<del>\1</del>', text)
    text = re.sub(r'\^\(([^)]+)\)', r'<sup>\1</sup>', text)
    text = re.sub(r'\^(\S+)', r'<sup>\1</sup>', text)
    return text


# ─── CSS ────────────────────────────────────────────────────────────────────

STYLE = """\
:root {
  --bg: #fafaf9; --bg2: #f5f5f4; --fg: #1c1917; --fg2: #57534e;
  --accent: #b45309; --accent2: #92400e; --border: #d6d3d1;
  --link: #b45309; --link-hover: #92400e;
  --code-bg: #f5f5f4; --pre-bg: #292524; --pre-fg: #e7e5e4;
  --max-w: 42rem;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #1c1917; --bg2: #292524; --fg: #e7e5e4; --fg2: #a8a29e;
    --accent: #f59e0b; --accent2: #fbbf24; --border: #44403c;
    --link: #f59e0b; --link-hover: #fbbf24;
    --code-bg: #292524; --pre-bg: #0c0a09; --pre-fg: #e7e5e4;
  }
}
[data-theme="dark"] {
  --bg: #1c1917; --bg2: #292524; --fg: #e7e5e4; --fg2: #a8a29e;
  --accent: #f59e0b; --accent2: #fbbf24; --border: #44403c;
  --link: #f59e0b; --link-hover: #fbbf24;
  --code-bg: #292524; --pre-bg: #0c0a09; --pre-fg: #e7e5e4;
}
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
html { font-size: 18px; scroll-behavior: smooth; }
body {
  font-family: 'Charter', 'Bitstream Charter', 'Sitka Text', Cambria, serif;
  background: var(--bg); color: var(--fg);
  line-height: 1.7; padding: 2rem 1rem;
  -webkit-font-smoothing: antialiased;
}
.container { max-width: var(--max-w); margin: 0 auto; }
a { color: var(--link); text-decoration: none; }
a:hover { color: var(--link-hover); text-decoration: underline; }
h1, h2, h3, h4 {
  font-family: system-ui, -apple-system, 'Segoe UI', sans-serif;
  line-height: 1.3; margin: 1.5em 0 0.5em;
}
h1 { font-size: 1.8rem; }
h2 { font-size: 1.4rem; border-bottom: 1px solid var(--border); padding-bottom: 0.3em; }
h3 { font-size: 1.15rem; }
p { margin: 0.8em 0; }
blockquote {
  border-left: 3px solid var(--accent); padding: 0.5em 1em;
  margin: 1em 0; color: var(--fg2); background: var(--bg2); border-radius: 4px;
}
pre {
  background: var(--pre-bg); color: var(--pre-fg);
  padding: 1em; border-radius: 6px; overflow-x: auto;
  margin: 1em 0; font-size: 0.85rem; line-height: 1.5;
}
code {
  font-family: 'Cascadia Code', 'Fira Code', 'JetBrains Mono', monospace;
  background: var(--code-bg); padding: 0.15em 0.35em; border-radius: 3px;
  font-size: 0.9em;
}
pre code { background: none; padding: 0; }
hr { border: none; border-top: 1px solid var(--border); margin: 2em 0; }
ul, ol { margin: 0.8em 0; padding-left: 1.5em; }
li { margin: 0.3em 0; }
img { max-width: 100%; height: auto; border-radius: 4px; }
del { opacity: 0.6; }
sup { font-size: 0.75em; }
.meta { color: var(--fg2); font-size: 0.85rem; margin-bottom: 1.5em; }
.related {
  font-size: 0.9rem; margin: 0 0 1.5em; padding: 0.5em 0.9em;
  border-left: 3px solid var(--accent); background: var(--bg2);
  border-radius: 4px;
}
.meta span { margin-right: 1.2em; }
.nav {
  display: flex; justify-content: space-between; align-items: center;
  padding: 1em 0; border-top: 1px solid var(--border); margin-top: 2em;
  font-size: 0.9rem;
}
.nav a {
  padding: 0.4em 0.8em; border: 1px solid var(--border);
  border-radius: 4px; transition: background 0.15s;
}
.nav a:hover { background: var(--bg2); text-decoration: none; }
.nav .disabled {
  color: var(--fg2); opacity: 0.4; padding: 0.4em 0.8em;
  border: 1px solid var(--border); border-radius: 4px;
}
.header {
  display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 2em;
}
.header h1 { margin: 0; }
.theme-toggle {
  background: var(--bg2); border: 1px solid var(--border); border-radius: 4px;
  padding: 0.3em 0.6em; cursor: pointer; font-size: 0.85rem;
  color: var(--fg); transition: background 0.15s; flex-shrink: 0;
}
.theme-toggle:hover { background: var(--border); }
.home-link { font-size: 0.9rem; margin-bottom: 1em; display: block; }
.series-card {
  border: 1px solid var(--border); border-radius: 6px; padding: 1em 1.2em;
  margin: 0.8em 0; transition: background 0.15s;
}
.series-card:hover { background: var(--bg2); }
.series-card h3 { margin: 0 0 0.3em; font-size: 1.05rem; }
.series-card .info { color: var(--fg2); font-size: 0.85rem; }
.series-card .info span { margin-right: 1em; }
.group-card { border-left: 3px solid var(--accent); }
.group-tag {
  display: inline-block; font-size: 0.7rem; padding: 0.12em 0.5em;
  border-radius: 3px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.04em; background: var(--accent); color: #fff;
  vertical-align: middle;
}
.status {
  display: inline-block; font-size: 0.75rem; padding: 0.15em 0.5em;
  border-radius: 3px; font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.03em;
}
.status-ongoing { background: #166534; color: #bbf7d0; }
.status-complete { background: #1e40af; color: #bfdbfe; }
.status-hiatus { background: #854d0e; color: #fef08a; }
.status-unknown { background: var(--bg2); color: var(--fg2); }
.toc-table { width: 100%; border-collapse: collapse; margin: 1em 0; }
.toc-table th {
  text-align: left; border-bottom: 2px solid var(--border);
  padding: 0.5em 0.8em; font-size: 0.85rem; color: var(--fg2);
}
.toc-table td { padding: 0.5em 0.8em; border-bottom: 1px solid var(--border); }
.toc-table tr:hover td { background: var(--bg2); }
.toc-table .num { width: 3em; text-align: center; color: var(--fg2); }
.toc-table .date { width: 7em; color: var(--fg2); font-size: 0.85rem; }
.standalones { margin-top: 2em; }
.standalone-item { padding: 0.5em 0; border-bottom: 1px solid var(--border); }
.standalone-item:last-child { border-bottom: none; }
.standalone-item .info { color: var(--fg2); font-size: 0.85rem; }
.search { margin: 1em 0; }
.search input {
  width: 100%; padding: 0.5em 0.8em; border: 1px solid var(--border);
  border-radius: 4px; font-size: 0.9rem; background: var(--bg);
  color: var(--fg); font-family: inherit;
}
.search input:focus { outline: 2px solid var(--accent); border-color: transparent; }
.search input::placeholder { color: var(--fg2); }
.hidden { display: none !important; }
.sort-controls {
  display: flex; align-items: center; gap: 0.6em;
  margin: 1em 0 0.5em; font-size: 0.85rem;
}
.sort-btn {
  background: var(--bg2); border: 1px solid var(--border); border-radius: 4px;
  padding: 0.3em 0.7em; cursor: pointer; font-size: 0.85rem;
  color: var(--fg); transition: background 0.15s; font-family: inherit;
}
.sort-btn:hover { background: var(--border); }
.sort-btn.active { background: var(--accent); color: #fff; border-color: var(--accent); }
.count { color: var(--fg2); font-size: 0.9rem; margin-bottom: 1em; }
.page-nav {
  display: flex; justify-content: center; gap: 0.5em;
  margin: 1.5em 0; flex-wrap: wrap;
}
.page-nav a, .page-nav span {
  padding: 0.3em 0.7em; border: 1px solid var(--border);
  border-radius: 4px; font-size: 0.85rem;
}
.page-nav .current { background: var(--accent); color: #fff; border-color: var(--accent); }
.page-nav a:hover { background: var(--bg2); text-decoration: none; }
"""

THEME_JS = """\
(function() {
  var t = localStorage.getItem('theme');
  if (t) document.documentElement.setAttribute('data-theme', t);
  document.addEventListener('DOMContentLoaded', function() {
    var btn = document.getElementById('theme-toggle');
    if (!btn) return;
    btn.addEventListener('click', function() {
      var current = document.documentElement.getAttribute('data-theme');
      var next = current === 'dark' ? 'light' : 'dark';
      if (!current) {
        next = window.matchMedia('(prefers-color-scheme: dark)').matches ? 'light' : 'dark';
      }
      document.documentElement.setAttribute('data-theme', next);
      localStorage.setItem('theme', next);
    });
  });
})();
"""

SEARCH_JS = """\
document.addEventListener('DOMContentLoaded', function() {
  var input = document.getElementById('search');
  var results = document.getElementById('search-results');
  var defaultView = document.getElementById('index-default');
  if (!input || !results || !defaultView) return;

  var timer = null;
  var lastSeq = 0;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function card(href, title, infoBits) {
    var info = infoBits.map(function(b) {
      return '<span>' + esc(b) + '</span>';
    }).join('');
    return '<a href="' + esc(href) + '" class="series-card" ' +
           'style="display:block;text-decoration:none;color:inherit">' +
           '<h3>' + esc(title) + '</h3><div class="info">' + info + '</div></a>';
  }

  function render(data) {
    var h = [];
    var t = data.totals;
    h.push('<p class="count">' + t.groups + ' groups, ' + t.series +
           ' series, ' + t.standalones + ' standalone posts match</p>');

    if (data.groups.length) {
      h.push('<h2>Series Groups</h2>');
      data.groups.forEach(function(g) {
        h.push(card('/group/' + g.slug, g.name + ' (group)',
          ['by ' + g.author, g.count + ' series',
           g.total_parts + ' parts', 'Last: ' + g.last_posted]));
      });
    }
    if (data.series.length) {
      h.push('<h2>Series</h2>');
      data.series.forEach(function(s) {
        h.push(card('/series/' + s.slug, s.name,
          ['by ' + s.author, s.total_parts + ' parts',
           s.status, 'Last: ' + s.last_posted]));
      });
    }
    if (data.standalones.length) {
      var more = (t.standalones > data.standalones.length)
        ? ' <span class="count">(showing ' + data.standalones.length +
          ' of ' + t.standalones + ')</span>' : '';
      h.push('<div class="standalones"><h2>Standalone Posts' + more + '</h2>');
      data.standalones.forEach(function(p) {
        h.push('<div class="standalone-item">' +
          '<a href="/post/' + esc(p.id) + '">' + esc(p.title) + '</a>' +
          '<div class="info"><span>by ' + esc(p.author) + '</span>' +
          '<span>' + esc(p.date) + '</span></div></div>');
      });
      h.push('</div>');
    }
    if (!data.groups.length && !data.series.length && !data.standalones.length) {
      h.push('<p class="count">No matches.</p>');
    }
    results.innerHTML = h.join('');
  }

  function run(q) {
    if (!q) {
      results.classList.add('hidden');
      results.innerHTML = '';
      defaultView.classList.remove('hidden');
      return;
    }
    var seq = ++lastSeq;
    fetch('/api/search?q=' + encodeURIComponent(q))
      .then(function(r) { return r.json(); })
      .then(function(data) {
        if (seq !== lastSeq) return;  // a newer query has superseded this one
        defaultView.classList.add('hidden');
        render(data);
        results.classList.remove('hidden');
      })
      .catch(function() {
        if (seq !== lastSeq) return;
        defaultView.classList.add('hidden');
        results.innerHTML = '<p class="count">Search failed. Try again.</p>';
        results.classList.remove('hidden');
      });
  }

  input.addEventListener('input', function() {
    var q = this.value.trim();
    if (timer) clearTimeout(timer);
    timer = setTimeout(function() { run(q); }, 200);
  });
});
"""

SORT_JS = """\
document.addEventListener('DOMContentLoaded', function() {
  var ascBtn = document.getElementById('sort-asc');
  var descBtn = document.getElementById('sort-desc');
  var tbody = document.querySelector('.toc-table tbody');
  var startLink = document.getElementById('start-reading');
  if (!ascBtn || !descBtn || !tbody) return;

  var rows = Array.from(tbody.querySelectorAll('tr'));
  var firstHref = rows.length ? rows[0].querySelector('a') : null;
  var lastHref = rows.length ? rows[rows.length - 1].querySelector('a') : null;

  function applySort(order) {
    var sorted = order === 'desc' ? rows.slice().reverse() : rows.slice();
    sorted.forEach(function(r) { tbody.appendChild(r); });
    ascBtn.classList.toggle('active', order === 'asc');
    descBtn.classList.toggle('active', order === 'desc');
    localStorage.setItem('series-sort', order);
    if (startLink) {
      var target = order === 'desc' ? lastHref : firstHref;
      if (target) startLink.href = target.href;
    }
  }

  ascBtn.addEventListener('click', function() { applySort('asc'); });
  descBtn.addEventListener('click', function() { applySort('desc'); });

  var saved = localStorage.getItem('series-sort');
  if (saved === 'desc') applySort('desc');
});
"""


# ─── HTML helpers ───────────────────────────────────────────────────────────

def _page(title, body_html, extra_head=""):
    return f"""\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>{STYLE}</style>
<script>{THEME_JS}</script>
{extra_head}
</head>
<body>
<div class="container">
{body_html}
</div>
</body>
</html>"""


def _status_badge(status):
    s = (status or "unknown").lower()
    return f'<span class="status status-{s}">{s.capitalize()}</span>'


def _esc(text):
    return html.escape(str(text)) if text else ""


# ─── Data layer ─────────────────────────────────────────────────────────────

class ReaderData:
    """Holds all metadata for a subreddit. Post bodies are loaded on demand."""

    def __init__(self, subreddit):
        self.subreddit = subreddit
        self.display_name = subreddit.upper()
        self.sub_dir = COLLECTIONS / subreddit.lower()
        self.posts_dir = self.sub_dir / "posts"
        self.series_dir = self.sub_dir / "series"

        self.post_meta = {}      # id -> {title, author, created_utc, score, url, ...}
        self.series_list = []    # [{name, slug, author, chapters, ...}, ...]
        self.series_by_slug = {} # slug -> series dict
        self.series_post_ids = set()
        self.post_series_ctx = {}  # post_id -> {name, slug, prev_id, next_id}
        self.standalone_ids = []   # sorted by date descending
        self.groups = {}           # group_slug -> {name, author, members: [...]}
        self.series_to_group = {}  # series_slug -> group_slug
        self.hidden_ids = set()    # post IDs excluded by manual overrides

    def load(self, force_rescan=False):
        """Scan posts and series indexes. Call once at startup."""
        self._load_post_metadata(force_rescan)
        self._load_series()
        self._load_groups()
        self._load_overrides()
        self._build_navigation()

    def refresh(self):
        """Rescan posts and series from disk. Call to pick up new content."""
        self.post_meta.clear()
        self.series_list.clear()
        self.series_by_slug.clear()
        self.series_post_ids.clear()
        self.post_series_ctx.clear()
        self.standalone_ids.clear()
        self.groups.clear()
        self.series_to_group.clear()
        self.hidden_ids.clear()
        self._load_post_metadata(force_rescan=True)
        self._load_series()
        self._load_groups()
        self._load_overrides()
        self._build_navigation()

    def _load_post_metadata(self, force_rescan=False):
        if not self.posts_dir.exists():
            return

        cache_file = self.sub_dir / ".reader_cache.json"

        if force_rescan:
            self._full_scan(cache_file)
            return

        cached = self._load_cache(cache_file)

        if cached is not None:
            self.post_meta = cached
            print(f"  {len(self.post_meta)} posts loaded from cache.")
            return

        self._full_scan(cache_file)

    def _full_scan(self, cache_file):
        """Scan every post file and rebuild the cache."""
        import os
        entries = [e.name for e in os.scandir(self.posts_dir) if e.name.endswith(".md")]
        total = len(entries)
        print(f"  Scanning {total} posts (this only happens once)...",
              flush=True)
        for i, name in enumerate(entries):
            pid = name[:-3]
            meta = self._parse_frontmatter_only(self.posts_dir / name)
            if meta:
                self.post_meta[pid] = meta
            if (i + 1) % 10000 == 0:
                print(f"  {i + 1}/{total}...", flush=True)
        print(f"  {len(self.post_meta)} posts indexed.")
        self._save_cache(cache_file)

    def _load_cache(self, cache_file):
        """Load cached metadata if it exists and is recent enough."""
        if not cache_file.exists():
            return None
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
            return data.get("posts", {})
        except (json.JSONDecodeError, OSError):
            return None

    def _scan_new_posts(self, cached, cache_file):
        """Check for any post files not yet in the cache."""
        new_count = 0
        for fp in self.posts_dir.glob("*.md"):
            pid = fp.stem
            if pid not in cached:
                meta = self._parse_frontmatter_only(fp)
                if meta:
                    self.post_meta[pid] = meta
                    new_count += 1
        if new_count:
            self._save_cache(cache_file)
        return new_count

    def _save_cache(self, cache_file):
        """Write post metadata to a JSON cache file."""
        try:
            cache_file.write_text(
                json.dumps({"posts": self.post_meta}, ensure_ascii=False),
                encoding="utf-8", newline="\n",
            )
        except OSError:
            pass

    @staticmethod
    def _parse_frontmatter_only(filepath):
        """Read just the YAML frontmatter without parsing the body."""
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

    def _load_series(self):
        if not self.series_dir.exists():
            return
        for idx_file in sorted(self.series_dir.glob("*/_index.md")):
            s = self._parse_series_index(idx_file)
            if s:
                self.series_list.append(s)
                self.series_by_slug[s["slug"]] = s
        print(f"  {len(self.series_list)} series loaded.")

    def _load_groups(self):
        """Load series groups from series/_groups.json, if present."""
        groups_file = self.series_dir / "_groups.json"
        if not groups_file.exists():
            return
        try:
            payload = json.loads(groups_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        raw_groups = payload.get("groups", {})
        # Keep only groups whose members actually loaded as series.
        for slug, g in raw_groups.items():
            members = [m for m in g.get("members", []) if m in self.series_by_slug]
            if len(members) >= 2:
                g["members"] = members
                self.groups[slug] = g
                for m in members:
                    self.series_to_group[m] = slug
        print(f"  {len(self.groups)} series groups loaded.")

    def _load_overrides(self):
        """Read manual-override excludes so junk posts are hidden everywhere."""
        ov_file = self.sub_dir / "_overrides.json"
        if not ov_file.exists():
            return
        try:
            data = json.loads(ov_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return
        for ov in data.get("series", []):
            self.hidden_ids.update(ov.get("exclude", []))
        if self.hidden_ids:
            print(f"  {len(self.hidden_ids)} posts hidden by overrides.")

    @staticmethod
    def _parse_series_index(idx_file):
        try:
            text = idx_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        if not text.startswith("---"):
            return None
        parts = text.split("---", 2)
        if len(parts) < 3:
            return None

        fm = {}
        for line in parts[1].strip().splitlines():
            if ": " in line:
                key, _, val = line.partition(": ")
                fm[key.strip()] = val.strip().strip('"')

        chapters = []
        for row in re.findall(r'^\|\s*(.+?)\s*\|$', parts[2], re.MULTILINE):
            # Split on unescaped pipes only: a pipe preceded by backslash
            # is part of the title, not a column separator
            cells = [c.strip() for c in re.split(r'(?<!\\)\|', row)]
            if len(cells) >= 5 and cells[0] not in ("#", "---", ""):
                try:
                    num = int(cells[0]) if cells[0].isdigit() else None
                except ValueError:
                    num = None
                # Post ID is always the last column; title is everything
                # between the first cell and the last 3 (date, score, id)
                post_id = cells[-1].strip()
                score = cells[-2].strip() if len(cells) >= 4 else "0"
                date = cells[-3].strip() if len(cells) >= 5 else ""
                title = "|".join(cells[1:-3]).replace(r"\|", "|").strip()
                chapters.append({
                    "num": num,
                    "title": title,
                    "date": date,
                    "score": score,
                    "post_id": post_id,
                })

        return {
            "name": fm.get("series_name", "Untitled"),
            "slug": fm.get("series_slug", idx_file.parent.name),
            "author": fm.get("author", "Unknown"),
            "total_parts": int(fm.get("total_parts", 0)),
            "first_posted": fm.get("first_posted", ""),
            "last_posted": fm.get("last_posted", ""),
            "status": fm.get("status", "unknown"),
            "total_score": int(fm.get("total_score", 0)),
            "detection_method": fm.get("detection_method", ""),
            "related_text": fm.get("related_text", ""),
            "related_url": fm.get("related_url", ""),
            "chapters": chapters,
        }

    def _build_navigation(self):
        for s in self.series_list:
            chapters = s["chapters"]
            for i, ch in enumerate(chapters):
                pid = ch["post_id"]
                self.series_post_ids.add(pid)
                self.post_series_ctx[pid] = {
                    "name": s["name"],
                    "slug": s["slug"],
                    "prev_id": chapters[i - 1]["post_id"] if i > 0 else None,
                    "next_id": chapters[i + 1]["post_id"] if i < len(chapters) - 1 else None,
                }

        standalones = [
            (pid, meta) for pid, meta in self.post_meta.items()
            if pid not in self.series_post_ids and pid not in self.hidden_ids
        ]
        standalones.sort(key=lambda x: str(x[1].get("created_utc", "")), reverse=True)
        self.standalone_ids = [pid for pid, _ in standalones]
        print(f"  {len(self.standalone_ids)} standalone posts.")

    def get_post_full(self, post_id):
        """Load a single post with its full body (on demand)."""
        fp = self.posts_dir / f"{post_id}.md"
        if not fp.exists():
            return None
        return parse_post_file(fp)


# ─── Page rendering ─────────────────────────────────────────────────────────

STANDALONES_PER_PAGE = 100

# Result caps for /api/search. The true total is always returned alongside the
# capped lists so the UI can show "showing X of N".
SEARCH_CAP_GROUPS = 100
SEARCH_CAP_SERIES = 100
SEARCH_CAP_STANDALONES = 300


def search_dataset(data, q):
    """Search the full in-memory dataset for a query string.

    Matches ``q`` case-insensitively against series/group name+author and
    standalone post title+author. This is the authoritative search over every
    post, not just the page currently rendered — the index page only lists 100
    standalones at a time, so client-side DOM filtering could never reach the
    ~83k standalones on other pages.

    Standalones are iterated in the pre-sorted date-descending order, so when
    matches exceed the cap the most recent ones are kept. Series and groups are
    sorted by last-posted before capping for the same reason. Returns capped
    lists plus the true total counts.
    """
    q = (q or "").strip().lower()
    empty = {"query": "", "groups": [], "series": [], "standalones": [],
             "totals": {"groups": 0, "series": 0, "standalones": 0}}
    if not q:
        return empty

    matched_groups = []
    for gslug, g in data.groups.items():
        if q in f'{g["name"]} {g["author"]}'.lower():
            st = _group_stats(data, g)
            matched_groups.append({
                "slug": gslug, "name": g["name"], "author": g["author"],
                "count": st["count"], "total_parts": st["total_parts"],
                "last_posted": (st["last_posted"] or "")[:10],
            })
    matched_groups.sort(key=lambda x: x["last_posted"], reverse=True)

    matched_series = []
    for s in data.series_list:
        if q in f'{s["name"]} {s["author"]}'.lower():
            matched_series.append({
                "slug": s["slug"], "name": s["name"], "author": s["author"],
                "total_parts": s["total_parts"], "status": s["status"],
                "last_posted": (s.get("last_posted") or "")[:10],
            })
    matched_series.sort(key=lambda x: x["last_posted"], reverse=True)

    matched_standalones = []
    standalone_total = 0
    for pid in data.standalone_ids:
        meta = data.post_meta.get(pid, {})
        title = meta.get("title", "Untitled")
        author = meta.get("author", "Unknown")
        if q in f'{title} {author}'.lower():
            standalone_total += 1
            if len(matched_standalones) < SEARCH_CAP_STANDALONES:
                matched_standalones.append({
                    "id": pid, "title": title, "author": author,
                    "date": str(meta.get("created_utc", ""))[:10],
                })

    return {
        "query": q,
        "groups": matched_groups[:SEARCH_CAP_GROUPS],
        "series": matched_series[:SEARCH_CAP_SERIES],
        "standalones": matched_standalones,
        "totals": {
            "groups": len(matched_groups),
            "series": len(matched_series),
            "standalones": standalone_total,
        },
    }


def render_index(data, page=1):
    """Render the home page."""
    # Series that belong to a group are shown under their group card, not in
    # the flat series list, so each series appears exactly once.
    ungrouped_series = [s for s in data.series_list
                        if s["slug"] not in data.series_to_group]
    series_sorted = sorted(
        ungrouped_series,
        key=lambda s: s.get("last_posted", ""),
        reverse=True,
    )

    group_cards = []
    for gslug, g in data.groups.items():
        st = _group_stats(data, g)
        group_cards.append((gslug, g, st))
    group_cards.sort(key=lambda x: x[2]["last_posted"], reverse=True)

    total_standalones = len(data.standalone_ids)
    total_pages = max(1, (total_standalones + STANDALONES_PER_PAGE - 1) // STANDALONES_PER_PAGE)
    page = max(1, min(page, total_pages))
    start = (page - 1) * STANDALONES_PER_PAGE
    end = start + STANDALONES_PER_PAGE
    page_ids = data.standalone_ids[start:end]

    h = []
    h.append(f'<div class="header">')
    h.append(f'<h1>r/{_esc(data.display_name)}</h1>')
    h.append(f'<button class="theme-toggle" id="theme-toggle">Toggle theme</button>')
    h.append(f'</div>')

    h.append(f'<div class="search">'
             f'<input type="text" id="search" placeholder="Search all series, groups and posts…">'
             f'</div>')

    # Live results are rendered here by SEARCH_JS from the /api/search endpoint;
    # the default index below is hidden while a search is active.
    h.append(f'<div id="search-results" class="hidden"></div>')
    h.append(f'<div id="index-default">')

    total_items = len(group_cards) + len(series_sorted) + total_standalones
    h.append(f'<p class="count"><span id="count">{total_items} shown</span> '
             f'&mdash; {len(group_cards)} groups, '
             f'{len(series_sorted)} series, '
             f'{total_standalones} standalone posts</p>')

    if group_cards:
        h.append(f'<h2>Series Groups</h2>')
        for gslug, g, st in group_cards:
            h.append(
                f'<a href="/group/{_esc(gslug)}" '
                f'class="series-card group-card" style="display:block;text-decoration:none;color:inherit">'
                f'<h3>{_esc(g["name"])} <span class="group-tag">group</span></h3>'
                f'<div class="info">'
                f'<span>by {_esc(g["author"])}</span>'
                f'<span>{st["count"]} series</span>'
                f'<span>{st["total_parts"]} parts</span>'
                f'<span>Last: {st["last_posted"][:10]}</span>'
                f'</div></a>'
            )

    if series_sorted:
        h.append(f'<h2>Series</h2>')
        for s in series_sorted:
            h.append(
                f'<a href="/series/{_esc(s["slug"])}" '
                f'class="series-card" style="display:block;text-decoration:none;color:inherit">'
                f'<h3>{_esc(s["name"])} {_status_badge(s["status"])}</h3>'
                f'<div class="info">'
                f'<span>by {_esc(s["author"])}</span>'
                f'<span>{s["total_parts"]} parts</span>'
                f'<span>Last: {s["last_posted"][:10]}</span>'
                f'</div></a>'
            )

    h.append(f'<div class="standalones">')
    h.append(f'<h2>Standalone Posts</h2>')

    for pid in page_ids:
        meta = data.post_meta.get(pid, {})
        title = meta.get("title", "Untitled")
        author = meta.get("author", "Unknown")
        date = str(meta.get("created_utc", ""))[:10]
        h.append(
            f'<div class="standalone-item">'
            f'<a href="/post/{_esc(pid)}">{_esc(title)}</a>'
            f'<div class="info">'
            f'<span>by {_esc(author)}</span>'
            f'<span>{date}</span>'
            f'</div></div>'
        )

    h.append(f'</div>')

    if total_pages > 1:
        h.append(f'<div class="page-nav">')
        if page > 1:
            h.append(f'<a href="/?page={page - 1}">&laquo; Prev</a>')
        for p in range(1, total_pages + 1):
            if abs(p - page) <= 3 or p == 1 or p == total_pages:
                if p == page:
                    h.append(f'<span class="current">{p}</span>')
                else:
                    h.append(f'<a href="/?page={p}">{p}</a>')
            elif abs(p - page) == 4:
                h.append(f'<span>&hellip;</span>')
        if page < total_pages:
            h.append(f'<a href="/?page={page + 1}">Next &raquo;</a>')
        h.append(f'</div>')

    h.append(f'</div>')  # close #index-default

    extra = f"<script>{SEARCH_JS}</script>"
    return _page(f"r/{data.display_name} — Reader", "\n".join(h), extra)


def render_series(data, slug):
    """Render a series table-of-contents page."""
    series = data.series_by_slug.get(slug)
    if not series:
        return None

    h = []
    grp_slug = data.series_to_group.get(slug)
    if grp_slug and grp_slug in data.groups:
        grp = data.groups[grp_slug]
        h.append(f'<a class="home-link" href="/group/{_esc(grp_slug)}">'
                 f'&larr; Part of: {_esc(grp["name"])}</a>')
    else:
        h.append(f'<a class="home-link" href="/">&larr; Back to index</a>')
    h.append(f'<div class="header">')
    h.append(f'<h1>{_esc(series["name"])}</h1>')
    h.append(f'<button class="theme-toggle" id="theme-toggle">Toggle theme</button>')
    h.append(f'</div>')

    h.append(f'<div class="meta">')
    h.append(f'<span>by {_esc(series["author"])}</span>')
    h.append(f'<span>{series["total_parts"]} parts</span>')
    h.append(f'<span>{_status_badge(series["status"])}</span>')
    h.append(f'</div>')

    if series.get("first_posted"):
        h.append(
            f'<p class="meta">First posted: {series["first_posted"][:10]} '
            f'&mdash; Last posted: {series["last_posted"][:10]}</p>'
        )

    if series.get("related_text"):
        rel = _esc(series["related_text"])
        url = series.get("related_url", "")
        if url:
            rel = f'<a href="{_esc(url)}" rel="noopener">{rel}</a>'
        h.append(f'<p class="related">&#128279; Related: {rel}</p>')

    h.append(f'<div class="sort-controls">'
             f'<span>Sort:</span>'
             f'<button class="sort-btn active" id="sort-asc">Oldest first</button>'
             f'<button class="sort-btn" id="sort-desc">Newest first</button>'
             f'</div>')

    h.append(f'<table class="toc-table">')
    h.append(f'<thead><tr>'
             f'<th class="num">#</th><th>Title</th><th class="date">Date</th>'
             f'</tr></thead><tbody>')

    for i, ch in enumerate(series["chapters"]):
        num = ch.get("num") or (i + 1)
        pid = ch["post_id"]
        title = ch.get("title", "Untitled")
        date = ch.get("date", "")
        has_file = pid in data.post_meta
        if has_file:
            h.append(
                f'<tr><td class="num">{num}</td>'
                f'<td><a href="/post/{_esc(pid)}">{_esc(title)}</a></td>'
                f'<td class="date">{date}</td></tr>'
            )
        else:
            h.append(
                f'<tr><td class="num">{num}</td>'
                f'<td>{_esc(title)} <em>(file missing)</em></td>'
                f'<td class="date">{date}</td></tr>'
            )

    h.append(f'</tbody></table>')

    first_ch = series["chapters"][0] if series["chapters"] else None
    if first_ch:
        pid = first_ch["post_id"]
        h.append(
            f'<div class="nav"><span></span>'
            f'<a id="start-reading" href="/post/{_esc(pid)}">Start reading &rarr;</a></div>'
        )

    extra = f"<script>{SORT_JS}</script>"
    return _page(f'{series["name"]} — r/{data.display_name}', "\n".join(h), extra)


def _group_stats(data, group):
    """Aggregate member stats for a group."""
    members = [data.series_by_slug[m] for m in group["members"]
               if m in data.series_by_slug]
    firsts = [m.get("first_posted", "") for m in members if m.get("first_posted")]
    lasts = [m.get("last_posted", "") for m in members if m.get("last_posted")]
    return {
        "members": members,
        "count": len(members),
        "total_parts": sum(m.get("total_parts", 0) for m in members),
        "first_posted": min(firsts) if firsts else "",
        "last_posted": max(lasts) if lasts else "",
    }


def render_group(data, slug):
    """Render a group page listing member series chronologically."""
    group = data.groups.get(slug)
    if not group:
        return None
    stats = _group_stats(data, group)
    members = sorted(stats["members"], key=lambda m: m.get("first_posted", ""))

    h = []
    h.append(f'<a class="home-link" href="/">&larr; Back to index</a>')
    h.append(f'<div class="header">')
    h.append(f'<h1>{_esc(group["name"])} <span class="group-tag">group</span></h1>')
    h.append(f'<button class="theme-toggle" id="theme-toggle">Toggle theme</button>')
    h.append(f'</div>')

    h.append(f'<div class="meta">')
    h.append(f'<span>by {_esc(group["author"])}</span>')
    h.append(f'<span>{stats["count"]} series</span>')
    h.append(f'<span>{stats["total_parts"]} parts total</span>')
    h.append(f'</div>')
    h.append(f'<p class="meta">Related series, in order. Each keeps its own '
             f'chapter numbering and reading order.</p>')

    for m in members:
        h.append(
            f'<a href="/series/{_esc(m["slug"])}" '
            f'class="series-card" style="display:block;text-decoration:none;color:inherit">'
            f'<h3>{_esc(m["name"])} {_status_badge(m["status"])}</h3>'
            f'<div class="info">'
            f'<span>{m["total_parts"]} parts</span>'
            f'<span>First: {m["first_posted"][:10]}</span>'
            f'<span>Last: {m["last_posted"][:10]}</span>'
            f'</div></a>'
        )
    return _page(f'{group["name"]} — r/{data.display_name}', "\n".join(h))


def render_post(data, post_id):
    """Render an individual post reading page."""
    post = data.get_post_full(post_id)
    if not post:
        return None

    title = post.get("title", "Untitled")
    author = post.get("author", "Unknown")
    date = str(post.get("created_utc", ""))[:10]
    body = post.get("_body", "")
    url = post.get("url", "")
    series_ctx = data.post_series_ctx.get(post_id)

    h = []

    if series_ctx:
        h.append(f'<a class="home-link" href="/series/{_esc(series_ctx["slug"])}">'
                 f'&larr; {_esc(series_ctx["name"])}</a>')
    else:
        h.append(f'<a class="home-link" href="/">&larr; Back to index</a>')

    h.append(f'<div class="header">')
    h.append(f'<h1>{_esc(title)}</h1>')
    h.append(f'<button class="theme-toggle" id="theme-toggle">Toggle theme</button>')
    h.append(f'</div>')

    h.append(f'<div class="meta">')
    h.append(f'<span>by {_esc(author)}</span>')
    h.append(f'<span>{date}</span>')
    if url:
        h.append(f'<span><a href="{_esc(url)}">original post</a></span>')
    h.append(f'</div>')

    h.append(f'<article>{md_to_html(body)}</article>')

    if series_ctx:
        prev_id = series_ctx.get("prev_id")
        next_id = series_ctx.get("next_id")
        nav = ['<div class="nav">']
        if prev_id:
            nav.append(f'<a href="/post/{_esc(prev_id)}">&larr; Previous</a>')
        else:
            nav.append(f'<span class="disabled">&larr; Previous</span>')
        nav.append(f'<a href="/series/{_esc(series_ctx["slug"])}">Contents</a>')
        if next_id:
            nav.append(f'<a href="/post/{_esc(next_id)}">Next &rarr;</a>')
        else:
            nav.append(f'<span class="disabled">Next &rarr;</span>')
        nav.append('</div>')
        h.append("\n".join(nav))
    else:
        h.append(f'<div class="nav">'
                 f'<a href="/">&larr; Back to index</a>'
                 f'<span></span><span></span></div>')

    return _page(f'{title} — r/{data.display_name}', "\n".join(h))


def render_404():
    body = ('<div class="header"><h1>Not Found</h1></div>'
            '<p>The page you requested does not exist.</p>'
            '<p><a href="/">Back to index</a></p>')
    return _page("Not Found", body)


# ─── PID file management ───────────────────────────────────────────────────

def _read_pid_meta():
    """Read reader process metadata from the PID file."""
    try:
        text = PID_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        try:
            return {"pid": int(text)}
        except ValueError:
            return None
    if not isinstance(data, dict):
        return None
    return data


def _write_pid(port, subreddit, token):
    """Write current reader instance metadata to the PID file."""
    payload = {
        "pid": os.getpid(),
        "port": port,
        "subreddit": subreddit,
        "started_at": datetime.now(timezone.utc).astimezone().isoformat(
            timespec="seconds"
        ),
        "token": token,
    }
    PID_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8",
                        newline="\n")
    atexit.register(_remove_pid)


def _remove_pid():
    """Remove the PID file on clean exit."""
    try:
        meta = _read_pid_meta()
        if meta and meta.get("pid") == os.getpid():
            PID_FILE.unlink(missing_ok=True)
    except OSError:
        pass


def _server_health(port):
    """Return reader health metadata from a running local server, if any."""
    try:
        req = Request(f"http://127.0.0.1:{port}/health", method="GET")
        with urlopen(req, timeout=2) as response:
            if response.status != 200:
                return None
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return None
    if payload.get("app") != "reddit-reader":
        return None
    return payload


def _check_existing_server(port, subreddit):
    """Check if a server is already running. Returns True if we should exit."""
    if not PID_FILE.exists():
        return False

    meta = _read_pid_meta()
    if not meta or not isinstance(meta.get("pid"), int):
        PID_FILE.unlink(missing_ok=True)
        return False

    pid = meta["pid"]
    if not process_is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        return False

    health = _server_health(meta.get("port", port))
    if (
        health
        and health.get("pid") == pid
        and health.get("token") == meta.get("token")
        and health.get("subreddit") == subreddit.lower()
    ):
        return True

    # The PID belongs to some other live process or an old reader instance that
    # cannot identify itself. Remove only our stale metadata; never kill it.
    PID_FILE.unlink(missing_ok=True)
    return False


# ─── HTTP server ────────────────────────────────────────────────────────────

class ReaderHandler(BaseHTTPRequestHandler):
    data = None
    server_ref = None
    token = None

    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        params = parse_qs(parsed.query)

        path = path.rstrip("/") or "/"

        if path == "/":
            page = 1
            try:
                page = int(params.get("page", ["1"])[0])
            except ValueError:
                pass
            content = render_index(self.data, page)
            self._respond(200, content)

        elif path.startswith("/series/"):
            slug = path[8:]
            content = render_series(self.data, slug)
            if content:
                self._respond(200, content)
            else:
                self._respond(404, render_404())

        elif path.startswith("/group/"):
            slug = path[7:]
            content = render_group(self.data, slug)
            if content:
                self._respond(200, content)
            else:
                self._respond(404, render_404())

        elif path.startswith("/post/"):
            post_id = path[6:]
            content = render_post(self.data, post_id)
            if content:
                self._respond(200, content)
            else:
                self._respond(404, render_404())

        elif path == "/api/search":
            q = params.get("q", [""])[0]
            self._respond_json(200, search_dataset(self.data, q))

        elif path == "/refresh":
            print("Refreshing index...", flush=True)
            self.data.refresh()
            print(f"Done — {len(self.data.post_meta)} posts, "
                  f"{len(self.data.series_list)} series.", flush=True)
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()

        elif path == "/health":
            self._respond_json(200, {
                "ok": True,
                "app": "reddit-reader",
                "subreddit": self.data.subreddit,
                "pid": os.getpid(),
                "token": self.token,
            })

        elif path == "/shutdown":
            token = params.get("token", [""])[0]
            if token != self.token:
                self._respond_text(403, "Forbidden.\n")
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.end_headers()
            self.wfile.write(b"Shutting down.\n")
            if self.server_ref:
                import threading
                threading.Thread(target=self.server_ref.shutdown, daemon=True).start()

        elif path == "/favicon.ico":
            self.send_response(204)
            self.end_headers()

        else:
            self._respond(404, render_404())

    def _respond(self, status, content):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _respond_json(self, status, payload):
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _respond_text(self, status, content):
        encoded = content.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format, *args):
        pass


def serve(subreddit, port=8080, no_open=False, refresh_cache=False):
    """Start the reader server for a subreddit."""
    subreddit = subreddit.lower()
    if _check_existing_server(port, subreddit):
        url = f"http://127.0.0.1:{port}"
        print(f"Reader already running at {url}")
        if not no_open:
            webbrowser.open(url)
        return

    data = ReaderData(subreddit)

    print(f"r/{subreddit.upper()} Reader")
    print(f"Indexing posts...")
    data.load(force_rescan=refresh_cache)
    print()

    ReaderHandler.data = data
    ReaderHandler.token = secrets.token_urlsafe(24)
    server = HTTPServer(("127.0.0.1", port), ReaderHandler)
    ReaderHandler.server_ref = server
    url = f"http://127.0.0.1:{port}"

    _write_pid(port, subreddit, ReaderHandler.token)

    print(f"Serving at {url}")
    print(f"Press Ctrl+C to stop.\n")

    if not no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.shutdown()


# ─── Entry point ────────────────────────────────────────────────────────────

def stop_server(port=8080):
    """Stop a running reader server using the local instance token."""
    meta = _read_pid_meta()
    if not meta:
        print("Reader is not running.")
        return

    pid = meta.get("pid")
    token = meta.get("token")
    if not isinstance(pid, int) or not token:
        PID_FILE.unlink(missing_ok=True)
        print("Removed stale reader metadata.")
        return

    if not process_is_alive(pid):
        PID_FILE.unlink(missing_ok=True)
        print("Reader is not running.")
        return

    health = _server_health(meta.get("port", port))
    if not health or health.get("pid") != pid or health.get("token") != token:
        PID_FILE.unlink(missing_ok=True)
        print("Removed stale reader metadata; no matching reader was stopped.")
        return

    shutdown_port = meta.get("port", port)
    try:
        req = Request(
            f"http://127.0.0.1:{shutdown_port}/shutdown?token={token}",
            method="GET",
        )
        with urlopen(req, timeout=2) as response:
            response.read()
        print("Reader stopped.")
    except Exception as exc:
        print(f"Failed to stop reader: {exc}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Browse collected Reddit posts and series in your browser.",
    )
    parser.add_argument("--subreddit", "-s",
                        help="Subreddit to browse (e.g. 'hfy')")
    parser.add_argument("--port", "-p", type=int, default=8080,
                        help="Port to serve on (default: 8080)")
    parser.add_argument("--no-open", action="store_true",
                        help="Don't auto-open the browser")
    parser.add_argument("--refresh-cache", action="store_true",
                        help="Rescan all posts and rebuild the cache")
    parser.add_argument("--stop", action="store_true",
                        help="Stop a running reader server")

    args = parser.parse_args()
    if args.stop:
        stop_server(port=args.port)
        return
    if not args.subreddit:
        parser.error("--subreddit is required unless --stop is used")
    serve(args.subreddit, port=args.port, no_open=args.no_open,
          refresh_cache=args.refresh_cache)


if __name__ == "__main__":
    main()
