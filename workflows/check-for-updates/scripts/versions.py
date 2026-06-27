"""Minimal semantic-version parsing and change classification (stdlib only).

Kept deliberately small: a tiny parser plus a classifier is all the workflow
needs, so there is no reason to pull in an external version-parsing package.
"""
import re

_SEMVER_RE = re.compile(r"(\d+)\.(\d+)\.(\d+)")
_PRERELEASE_RE = re.compile(
    r"\d+\.\d+\.\d+[-.]?(?:a|b|rc|alpha|beta|dev|pre)", re.IGNORECASE
)


def parse(version):
    """Return (major, minor, patch) from a version string, or None if not semver-like."""
    if not version:
        return None
    match = _SEMVER_RE.search(str(version))
    if not match:
        return None
    return tuple(int(part) for part in match.groups())


def is_prerelease(version):
    """Return True when the version string looks like an alpha/beta/rc/dev build."""
    return bool(version and _PRERELEASE_RE.search(str(version)))


def classify(installed, latest):
    """Classify the change from *installed* to *latest*.

    Returns one of: "up to date", "patch", "minor", "major", "unknown".
    A pre-release latest never reports a stable install as behind.
    """
    current = parse(installed)
    newer = parse(latest)
    if current is None or newer is None:
        # Fall back to a string compare for non-semver versions.
        if installed and latest and str(installed).strip() == str(latest).strip():
            return "up to date"
        return "unknown"
    if newer <= current:
        return "up to date"
    if is_prerelease(latest) and not is_prerelease(installed):
        return "up to date"
    if newer[0] > current[0]:
        return "major"
    if newer[1] > current[1]:
        return "minor"
    return "patch"
