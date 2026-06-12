#!/usr/bin/env python3
"""Create or repair Book Dragon's canonical project Python environment."""

import argparse
import json
import subprocess
import sys
import venv
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
VENV_DIR = PROJECT_ROOT / ".venv"
ROOT_REQUIREMENTS = PROJECT_ROOT / "requirements.txt"

PACKAGE_IMPORTS = {
    "beautifulsoup4": "bs4",
    "feedparser": "feedparser",
    "lxml": "lxml",
    "pypdf": "pypdf",
    "python-dotenv": "dotenv",
    "pyyaml": "yaml",
    "requests": "requests",
    "tavily-python": "tavily",
    "textstat": "textstat",
    "trafilatura": "trafilatura",
}


def venv_python() -> Path:
    """Return the expected platform-specific virtual-environment Python."""
    if sys.platform == "win32":
        return VENV_DIR / "Scripts" / "python.exe"
    return VENV_DIR / "bin" / "python"


def package_status(python: Path) -> dict:
    """Return package availability inside the project environment."""
    imports = dict(PACKAGE_IMPORTS)
    if sys.version_info >= (3, 10):
        imports["mcp"] = "mcp"

    code = (
        "import importlib.util,json;"
        f"mods={json.dumps(imports)};"
        "print(json.dumps({name: importlib.util.find_spec(module) is not None "
        "for name,module in mods.items()}))"
    )
    result = subprocess.run(
        [str(python), "-c", code],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "Package check failed.")
    return json.loads(result.stdout)


def check() -> int:
    """Print runtime health and return a process exit code."""
    python = venv_python()
    if not python.is_file():
        print("Project runtime: MISSING")
        print("Run: python workflows/biblio-tools/scripts/setup.py")
        return 1

    status = package_status(python)
    missing = sorted(name for name, available in status.items() if not available)
    print(f"Project runtime: {python.relative_to(PROJECT_ROOT)}")
    print(f"Packages checked: {len(status)}")
    if missing:
        print(f"Missing: {', '.join(missing)}")
        return 1
    print("All declared runtime packages are available.")
    return 0


def setup(dry_run: bool) -> int:
    """Create the environment if needed and install the root manifest."""
    if sys.version_info < (3, 9):
        raise RuntimeError("Python 3.9 or later is required.")
    if not ROOT_REQUIREMENTS.is_file():
        raise RuntimeError("Root requirements.txt is missing.")

    python = venv_python()
    if dry_run:
        action = "repair" if python.is_file() else "create"
        print(f"[DRY RUN] Would {action} .venv.")
        print("[DRY RUN] Would install root requirements.txt without pip cache.")
        print("[DRY RUN] Would verify all declared runtime packages.")
        return 0

    if not python.is_file():
        print("Creating .venv...")
        venv.EnvBuilder(with_pip=True).create(VENV_DIR)
    else:
        print("Using existing .venv.")

    print("Installing Book Dragon requirements...")
    result = subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--no-cache-dir",
            "-r",
            str(ROOT_REQUIREMENTS),
        ],
        cwd=PROJECT_ROOT,
    )
    if result.returncode != 0:
        return result.returncode
    return check()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check the canonical runtime without modifying it.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show setup actions without creating or modifying files.",
    )
    args = parser.parse_args()

    try:
        code = check() if args.check else setup(args.dry_run)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    main()
