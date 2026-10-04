"""Enforce FastAPI import boundaries across production source layout."""

from __future__ import annotations

import ast
from pathlib import Path

FORBIDDEN_IMPORT_ROOTS = frozenset({"fastapi", "starlette"})
ALLOWED_FASTAPI_PACKAGE_ROOT = Path("src/kronika/adapters/api")
CONFIGURATION_MODULE = Path("src/kronika/configuration.py")
SOURCE_ROOT = Path("src/kronika")


def _module_name_from_import(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name.split(".")[0]
    if node.module is None:
        return None
    return node.module.split(".")[0]


def _collect_forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        root = _module_name_from_import(node)
        if root in FORBIDDEN_IMPORT_ROOTS:
            violations.append(root)
    return violations


def test_production_fastapi_imports_are_confined_to_adapters_api() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        relative_path = path.relative_to(repository_root)
        if ALLOWED_FASTAPI_PACKAGE_ROOT in relative_path.parents:
            continue
        forbidden_roots = _collect_forbidden_imports(path)
        if forbidden_roots:
            violations.append(f"{relative_path}: {sorted(set(forbidden_roots))}")
    assert violations == []


def test_configuration_module_remains_independent_of_fastapi_stack() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    configuration_path = repository_root / CONFIGURATION_MODULE
    forbidden_roots = _collect_forbidden_imports(configuration_path)
    assert forbidden_roots == []


def test_capture_package_remains_independent_of_fastapi_stack() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    paths = sorted((repository_root / "src" / "kronika_capture").rglob("*.py"))
    assert paths
    violations: list[str] = []
    for path in paths:
        forbidden_roots = _collect_forbidden_imports(path)
        if forbidden_roots:
            violations.append(
                f"{path.relative_to(repository_root)}: {sorted(set(forbidden_roots))}"
            )
    assert violations == []
