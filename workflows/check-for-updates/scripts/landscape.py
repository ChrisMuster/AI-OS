"""Landscape mode (Phase 2): watch supported AI tools for product-status changes.

Advisory only. For each watched product, this runs a focused web-research query
and scans the returned source content for status-change signal keywords (renames,
deprecations, replacements). It surfaces candidate signals with their sources for
you to verify; it never concludes on its own that a product has changed.

The deterministic ~90% lives here: build the query, scan content for keywords,
structure the findings. The judgement (is this signal real?) stays with the
reader, which is why this section is labelled advisory in the report.
"""
import sys
from pathlib import Path

_SKILL_SCRIPTS = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "skills" / "web-research" / "scripts"
)


def _load_research():
    """Import the web-research entry point lazily so tests stay offline."""
    if str(_SKILL_SCRIPTS) not in sys.path:
        sys.path.insert(0, str(_SKILL_SCRIPTS))
    from research import research
    return research


def _scan_text(text, terms):
    """Return the sorted terms that appear (case-insensitive substring) in text."""
    lowered = (text or "").lower()
    return sorted({term for term in terms if term and term.lower() in lowered})


def scan_product(spec, keywords, research_fn, sources=5):
    """Research one watched product and scan its sources for signal keywords.

    Returns a finding dict: name, signals (sorted matched terms across all
    sources), hits (per-source detail for matches), and status ("ok" or a short
    error string). Aliases are scanned alongside the keywords so a replacement
    name surfacing in the sources is itself flagged.
    """
    name = spec.get("name", "?")
    query = spec.get("query") or f"{name} deprecated renamed replaced status"
    terms = list(keywords) + list(spec.get("aliases") or [])

    try:
        package = research_fn(topic=query, sources=sources)
    except Exception as exc:  # network boundary: degrade, never crash the run
        return {"name": name, "signals": [], "hits": [], "status": f"research failed ({exc})"}

    package_sources = package.get("sources", []) or []
    hits = []
    matched = set()
    for src in package_sources:
        blob = f"{src.get('title', '')}\n{src.get('content', '')}"
        signals = _scan_text(blob, terms)
        if signals:
            matched.update(signals)
            hits.append({
                "title": src.get("title", ""),
                "url": src.get("url", ""),
                "tier": src.get("tier"),
                "signals": signals,
            })
    return {
        "name": name, "signals": sorted(matched), "hits": hits,
        "sources_read": len(package_sources), "status": "ok",
    }


def check_landscape(config, research_fn=None, sources=5):
    """Run landscape mode for every watched product in *config*.

    Returns (findings, status). status is "ok", "disabled", or a short error
    string. research_fn is injectable for tests; production resolves the
    web-research skill lazily so an absent skill degrades to a status, not a crash.
    """
    landscape = (config or {}).get("landscape", {}) or {}
    if not landscape.get("enabled", False):
        return [], "disabled"
    watch = landscape.get("watch", []) or []
    if not watch:
        return [], "no products configured"
    keywords = landscape.get("signal_keywords", []) or []

    if research_fn is None:
        try:
            research_fn = _load_research()
        except Exception as exc:  # missing skill must not break the version check
            return [], f"web-research unavailable ({exc})"

    findings = [scan_product(spec, keywords, research_fn, sources=sources) for spec in watch]
    return findings, "ok"
