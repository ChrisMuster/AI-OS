#!/usr/bin/env python3
"""Rule B3 (layer 1) - block personal data being written into committable files.

Before an AI write lands, scan the incoming content for high-confidence personal
markers (email, the user's name, OS username, personal home path) and block if
the target file is committable (tracked or new, i.e. NOT gitignored). Writes to
gitignored personal files (USER.md, LOG.md, memory/, .env, ...) are left alone -
those are exactly where personal data belongs.

Detection reuses the personal-data-guard workflow as the single source of truth
(one detection source, two triggers: this per-write hook and the git pre-commit
hook). The guard module is loaded lazily so a normal hook fire that is not a
write pays nothing for it.
"""

import importlib.util
import os
import subprocess

from core import Decision, format_block

RULE_ID = "B3"

_GUARD_CACHE = {}


def _load_guard(project_root):
    """Load personal-data-guard's run.py as a module (cached per root)."""
    key = str(project_root)
    if key in _GUARD_CACHE:
        return _GUARD_CACHE[key]
    guard_path = (
        project_root / "workflows" / "personal-data-guard" / "scripts" / "run.py"
    )
    spec = importlib.util.spec_from_file_location("pdg_guard", str(guard_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _GUARD_CACHE[key] = module
    return module


def _is_gitignored(file_path, project_root):
    """True if git would ignore file_path. On any git error, return None
    (cannot determine) so the caller can fail open rather than wrongly block."""
    try:
        result = subprocess.run(
            ["git", "check-ignore", "-q", file_path],
            cwd=str(project_root), capture_output=True, encoding="utf-8",
            timeout=5,
        )
    except Exception:
        return None
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    return None  # 128 etc. - not a git repo / error


def check(ctx):
    if not ctx.content or not ctx.file_path or not ctx.project_root:
        return None

    ignored = _is_gitignored(ctx.file_path, ctx.project_root)
    if ignored is None or ignored:
        # Cannot determine, or a gitignored personal file -> do not block.
        return None

    guard = _load_guard(ctx.project_root)
    markers, _ = guard.build_markers()
    rel = os.path.basename(ctx.file_path.replace("\\", "/"))
    findings = guard.scan_text(rel, ctx.content, markers)
    fails = [f for f in findings if f[0] == "FAIL"]
    if not fails:
        return None

    detail = "; ".join(f[2] for f in fails[:5])
    return Decision.block(RULE_ID, format_block(
        f"This write would put personal data into a committable file "
        f"(`{rel}`).",
        f"personal-data-guard flagged: {detail}. Tracked files must use "
        f"generic language only; personal details belong in gitignored files "
        f"(USER.md, .env, memory/).",
        "Reword the content to remove the personal data (use 'the user' "
        "instead of a name, a placeholder instead of a real value), or write "
        "it into a gitignored file if that is where it belongs.",
    ))
