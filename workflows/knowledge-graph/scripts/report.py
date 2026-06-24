"""
report.py — Shared finding model and audit-style report formatter.

A Finding is a (severity, subject, message) triple, mirroring the audit
workflow's report style (FAIL / WARN / INFO) so the two read consistently.
Validation produces Findings; this module renders them to Markdown and decides
the process exit code.
"""

from dataclasses import dataclass

# Severity ranking — higher is more serious. FAIL is unused by validation today
# (every check is WARN or INFO) but kept so the formatter matches the audit
# vocabulary and can carry hard failures if a future check needs one.
SEVERITIES = ("FAIL", "WARN", "INFO")


@dataclass(frozen=True)
class Finding:
    severity: str  # one of SEVERITIES
    subject: str   # node id or path the finding is about
    message: str   # human-readable description


def _bucket(findings, severity):
    return [f for f in findings if f.severity == severity]


def format_report(findings, *, node_count: int, edge_count: int, run_at: str) -> str:
    """Render findings as a Markdown report mirroring the audit report layout."""
    fails = _bucket(findings, "FAIL")
    warns = _bucket(findings, "WARN")
    infos = _bucket(findings, "INFO")

    lines = [
        "# Book Dragon — Knowledge Graph Validation Report",
        "",
        f"**Run at:** {run_at}",
        f"**Nodes:** {node_count}",
        f"**Edges:** {edge_count}",
        f"**Failures:** {len(fails)}",
        f"**Warnings:** {len(warns)}",
        f"**Info:** {len(infos)}",
        "",
    ]

    if not fails and not warns:
        lines += ["All graph validation checks passed (no failures or warnings).", ""]
    else:
        if fails:
            lines += ["## Failures", ""]
            lines += [f"- `{f.subject}` — {f.message}" for f in fails]
            lines.append("")
        if warns:
            lines += ["## Warnings", ""]
            lines += [f"- `{f.subject}` — {f.message}" for f in warns]
            lines.append("")

    if infos:
        lines += ["## Info", ""]
        lines += [f"- `{f.subject}` — {f.message}" for f in infos]
        lines.append("")

    return "\n".join(lines)


def exit_code(findings) -> int:
    """1 if any FAIL is present, else 0. Warnings and info do not fail the run,
    matching the audit script's convention (warnings are advisory)."""
    return 1 if any(f.severity == "FAIL" for f in findings) else 0
