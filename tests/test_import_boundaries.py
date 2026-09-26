"""Rule 1/2 enforcement: packages never import apps, apps never import each other.

Copied deliberately from lighter-core, including the reason it exists: a convention nobody tests
is a convention that decays. Three Lighter tools had already drifted into three independent
clients before anyone decided they shouldn't.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ROOT / "packages"
APPS = ROOT / "apps"


def top_level_imports(src: str) -> list[str]:
    """Absolute top-level module names imported by a source string."""
    out: list[str] = []
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                out.append(node.module.split(".")[0])
    return out


def scan_source(src: str, rel_path: str, app_names: set[str] | None = None) -> list[str]:
    app_names = app_names or set()
    violations: list[str] = []
    mods = top_level_imports(src)
    parts = rel_path.split("/")
    if len(parts) < 2:
        return violations

    if parts[0] == "packages":
        for m in mods:
            if m == "apps" or m in app_names:
                violations.append(
                    f"{rel_path} imports app module '{m}' (packages must not import apps)"
                )
    elif parts[0] == "apps":
        siblings = app_names - {parts[1]}
        for m in mods:
            if m in siblings:
                violations.append(
                    f"{rel_path} imports sibling app '{m}' (apps must not import each other)"
                )
    return violations


def _py_files(base: Path) -> list[Path]:
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def test_no_boundary_violations() -> None:
    app_names = {p.name for p in APPS.iterdir() if p.is_dir()} if APPS.is_dir() else set()
    violations: list[str] = []
    for base in (PACKAGES, APPS):
        if not base.is_dir():
            continue
        for path in _py_files(base):
            rel = path.relative_to(ROOT).as_posix()
            violations.extend(scan_source(path.read_text(), rel, app_names))
    assert not violations, "import boundary violations:\n" + "\n".join(violations)


def test_the_scanner_actually_catches_violations() -> None:
    """A guard nobody has seen fail is a guard nobody should trust."""
    app_names = {"coverage_cli", "derive_adapter"}
    caught = scan_source("from coverage_cli import main\n", "apps/derive_adapter/x.py",
                         app_names)
    assert caught, "scanner failed to catch an app importing a sibling app"
    caught2 = scan_source("import coverage_cli\n", "packages/options_core/x.py", app_names)
    assert caught2, "scanner failed to catch a package importing an app"
