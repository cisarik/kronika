"""Import-boundary tests for the pure FrameNest domain package."""

from __future__ import annotations

import ast
import importlib
import socket
import sys
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DOMAIN_ROOT = REPOSITORY_ROOT / "src" / "kronika" / "domain"
FORBIDDEN_DOMAIN_IMPORT_ROOTS = frozenset(
    {
        "alembic",
        "fastapi",
        "kronika_capture",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
    }
)
FORBIDDEN_KRONIKA_DOMAIN_IMPORT_PREFIXES = (
    "kronika.infrastructure",
    "kronika.adapters",
    "kronika.application",
)


def _module_name_from_import(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name.split(".")[0]
    if node.module is None:
        return None
    return node.module.split(".")[0]


def test_domain_package_imports_no_framework_or_persistence_modules() -> None:
    violations: list[str] = []
    for path in sorted(DOMAIN_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden_roots: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            root = _module_name_from_import(node)
            if root in FORBIDDEN_DOMAIN_IMPORT_ROOTS:
                forbidden_roots.append(root)
            if root == "kronika":
                module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                if module.startswith(FORBIDDEN_KRONIKA_DOMAIN_IMPORT_PREFIXES):
                    forbidden_roots.append(module)
        if forbidden_roots:
            violations.append(
                f"{path.relative_to(REPOSITORY_ROOT)}: {sorted(set(forbidden_roots))}"
            )
    assert violations == []


def test_application_ports_import_no_infrastructure_or_sqlalchemy() -> None:
    application_root = REPOSITORY_ROOT / "src" / "kronika" / "application"
    violations: list[str] = []
    forbidden_roots = FORBIDDEN_DOMAIN_IMPORT_ROOTS | {"sqlite3"}
    for path in sorted(application_root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            root = _module_name_from_import(node)
            if root in forbidden_roots:
                found.append(root or "")
            if root == "kronika":
                module = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                if module.startswith("kronika.infrastructure"):
                    found.append(module)
        if found:
            violations.append(
                f"{path.relative_to(REPOSITORY_ROOT)}: {sorted(set(found))}"
            )
    assert violations == []


@pytest.mark.parametrize(
    ("package_path", "forbidden_package"),
    [
        ("src/kronika/domain", "kronika_capture"),
        ("src/kronika/application", "kronika_capture"),
        ("src/kronika_capture", "kronika"),
    ],
)
def test_application_and_capture_packages_are_independent(
    package_path: str, forbidden_package: str,
) -> None:
    paths = sorted((REPOSITORY_ROOT / package_path).rglob("*.py"))
    assert paths
    violations: list[str] = []
    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
            else:
                continue
            for module in modules:
                if module == forbidden_package or module.startswith(f"{forbidden_package}."):
                    violations.append(f"{path.relative_to(REPOSITORY_ROOT)}: {module}")
    assert violations == []


def test_importing_domain_has_no_identity_or_io_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_attempts: list[tuple[Any, ...]] = []
    original_bind = socket.socket.bind

    def tracked_bind(self: socket.socket, address: tuple[Any, ...]) -> None:
        bind_attempts.append(address)
        return original_bind(self, address)

    def fail_uuid4() -> object:
        raise AssertionError("domain import must not generate identities")

    def fail_open(*args: Any, **kwargs: Any) -> object:
        raise AssertionError("domain import must not open files or databases")

    def fail_getenv(*args: Any, **kwargs: Any) -> object:
        raise AssertionError("domain import must not read environment settings")

    monkeypatch.setattr(socket.socket, "bind", tracked_bind)
    monkeypatch.setattr("uuid.uuid4", fail_uuid4)
    monkeypatch.setattr("builtins.open", fail_open)
    monkeypatch.setattr("pathlib.Path.open", fail_open)
    monkeypatch.setattr("os.getenv", fail_getenv)
    monkeypatch.setattr("os.environ.get", fail_getenv)

    # The fresh import must not leak a second copy of the domain package into
    # sys.modules: later tests keep references to the originally imported
    # domain classes, and a replaced module object would break their
    # isinstance identity. Preserve and restore the exact prior state.
    def _domain_module_names() -> set[str]:
        return {
            name
            for name in sys.modules
            if name == "kronika.domain" or name.startswith("kronika.domain.")
        }

    preexisting = _domain_module_names()
    original_domain = sys.modules.pop("kronika.domain", None)
    original_identities = sys.modules.pop("kronika.domain.identities", None)
    try:
        importlib.import_module("kronika.domain")

        assert bind_attempts == []
    finally:
        for name in _domain_module_names() - preexisting:
            sys.modules.pop(name, None)
        sys.modules.pop("kronika.domain", None)
        sys.modules.pop("kronika.domain.identities", None)
        if original_domain is not None:
            sys.modules["kronika.domain"] = original_domain
        if original_identities is not None:
            sys.modules["kronika.domain.identities"] = original_identities
