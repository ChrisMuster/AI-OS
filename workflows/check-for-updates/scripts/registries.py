"""Resolvers that fetch the latest published version of a tool from a registry.

Each resolver takes an identifier and returns a version string. Network calls
use the standard library only (no extra dependency) and short timeouts; failures
are caught by resolve_latest and reported as a source status, never raised.
"""
import json
import urllib.error
import urllib.request

_TIMEOUT = 10
_HEADERS = {"User-Agent": "book-dragon-check-for-updates"}


def _get_json(url):
    request = urllib.request.Request(url, headers=_HEADERS)
    with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
        return json.load(response)


def latest_from_npm(package):
    """Return the latest published version of an npm package."""
    data = _get_json(f"https://registry.npmjs.org/{package}/latest")
    return data["version"]


def latest_from_github(repo):
    """Return the latest release tag of a GitHub repo (owner/name), without a leading v."""
    data = _get_json(f"https://api.github.com/repos/{repo}/releases/latest")
    return data.get("tag_name", "").lstrip("v")


RESOLVERS = {
    "npm": latest_from_npm,
    "github": latest_from_github,
}


def resolve_latest(source, identifier):
    """Resolve the latest version for *identifier* from *source*.

    Returns (version, error). version is None on failure; error is a short
    human-readable string, or None on success.
    """
    resolver = RESOLVERS.get(source)
    if resolver is None:
        return None, f"unknown source '{source}'"
    if not identifier:
        return None, "no package id configured"
    try:
        return resolver(identifier), None
    except urllib.error.HTTPError as exc:
        return None, f"HTTP {exc.code}"
    except urllib.error.URLError as exc:
        return None, f"network error ({exc.reason})"
    except (KeyError, ValueError, TimeoutError) as exc:
        return None, f"bad response ({exc})"
