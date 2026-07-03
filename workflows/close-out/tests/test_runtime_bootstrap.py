#!/usr/bin/env python3
"""Regression checks for workflow scripts that need project-only packages."""
import ast
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def imports_yaml(path: Path) -> bool:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name == "yaml" for alias in node.names):
                return True
        if isinstance(node, ast.ImportFrom) and node.module == "yaml":
            return True
    return False


class RuntimeBootstrapTests(unittest.TestCase):
    def test_yaml_imports_bootstrap_runtime_or_degrade(self):
        offenders = []
        for path in sorted((PROJECT_ROOT / "workflows").glob("*/scripts/*.py")):
            if not imports_yaml(path):
                continue
            text = path.read_text(encoding="utf-8")
            has_runtime_handoff = "ensure_project_runtime()" in text
            has_documented_degrade = (
                "PyYAML not available" in text and "skipped" in text
            )
            if not (has_runtime_handoff or has_documented_degrade):
                offenders.append(path.relative_to(PROJECT_ROOT).as_posix())

        self.assertEqual(
            offenders,
            [],
            "Scripts that import PyYAML must use the project .venv runtime "
            "handoff, or explicitly degrade when PyYAML is unavailable.",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
