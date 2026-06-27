"""Formatting for the check-for-updates report (text and the --json shape)."""

_ACTIONABLE = {"patch", "minor", "major", "unknown"}


def count_updates(results):
    """Number of results that represent an available update."""
    return sum(1 for r in results if r.get("change") in _ACTIONABLE)


def _upgrade_command(result):
    if result.get("category") == "Python packages":
        return f"python -m pip install -U {result['name']}"
    return result.get("upgrade") or "(see vendor instructions)"


def build_report(results, source_status, timestamp, suggest_commands=False):
    """Return the human-readable text report."""
    lines = ["Book Dragon - Update Check", f"Run at: {timestamp}", ""]

    categories = []
    for r in results:
        if r["category"] not in categories:
            categories.append(r["category"])

    for category in categories:
        rows = [r for r in results if r["category"] == category]
        lines.append(category.upper())
        actionable = [r for r in rows if r["change"] in _ACTIONABLE]
        for r in rows:
            installed = r["installed"] if r["installed"] is not None else "(not installed)"
            latest = r["latest"] if r["latest"] is not None else "-"
            note = f"  {r['note']}" if r.get("note") else ""
            lines.append(
                f"  {r['name']:<26} {str(installed):<14} {str(latest):<14} {r['change']}{note}")
            if suggest_commands and r["change"] in _ACTIONABLE:
                lines.append(f"      -> {_upgrade_command(r)}")
        if category == "Python packages" and not actionable:
            lines.append("  (all up to date)")
        lines.append("")

    status = " | ".join(f"{k}: {v}" for k, v in source_status.items()) or "none"
    lines.append(f"SOURCE STATUS  {status}")
    lines.append("")
    total = count_updates(results)
    lines.append(f"{total} update(s) available." if total else "Everything is up to date.")
    return "\n".join(lines)


def build_landscape_report(findings, status):
    """Return the advisory AI-tool-landscape section as text.

    *status* is "ok", "disabled", or a short error string from check_landscape.
    Flagged products list their matched signals and the sources behind them;
    products whose own research failed are listed separately so a partial run is
    never silently dropped.
    """
    lines = ["AI TOOL LANDSCAPE  (advisory - verify before acting)"]
    if status == "disabled":
        lines.append("  Landscape watch is disabled in config.")
        return "\n".join(lines)
    if status != "ok":
        lines.append(f"  Could not run landscape watch: {status}")
        return "\n".join(lines)

    flagged = [f for f in findings if f.get("signals")]
    if not flagged:
        total_read = sum(f.get("sources_read", 0) for f in findings)
        if total_read == 0:
            lines.append("  No sources could be read - web research returned nothing "
                         "(check connectivity and API keys). Result is not conclusive.")
        else:
            lines.append("  No status-change signals found for any watched product.")
    for finding in flagged:
        lines.append(f"  {finding['name']}: possible change - {', '.join(finding['signals'])}")
        for hit in finding.get("hits", []):
            tier = f"T{hit['tier']}" if hit.get("tier") else "T?"
            lines.append(f"      [{tier}] {hit.get('title', '')}")
            if hit.get("url"):
                lines.append(f"           {hit['url']}")

    errors = [f for f in findings if f.get("status") not in ("ok", None)]
    if errors:
        lines.append("")
        for finding in errors:
            lines.append(f"  ! {finding['name']}: {finding['status']}")
    return "\n".join(lines)
