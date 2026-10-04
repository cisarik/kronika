"""Architecture and privacy boundary tests for persistence infrastructure."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path
from typing import Iterable
from unittest.mock import patch

import pytest

SOURCE_ROOT = Path("src/kronika")
PERSISTENCE_ROOT = Path("src/kronika/infrastructure/persistence")
FORBIDDEN_PERSISTENCE_IMPORT_ROOTS = frozenset({"fastapi", "starlette", "uvicorn"})
FORBIDDEN_DEPENDENCY_SNIPPETS = (
    "sqlalchemy.orm",
    "DeclarativeBase",
    "sqlmodel",
    "SQLModel",
    "aiosqlite",
    "sqlalchemy.ext.asyncio",
    "create_async_engine",
    "AsyncEngine",
    "AsyncSession",
)


def _module_root(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name.split(".")[0]
    if node.module is None:
        return None
    return node.module.split(".")[0]


def _imports(path: Path) -> Iterable[ast.Import | ast.ImportFrom]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            yield node


def test_sqlalchemy_and_alembic_imports_are_confined_to_persistence_package() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        relative = path.relative_to(repository_root)
        if relative == Path("src/kronika/configuration.py"):
            forbidden_roots = {"sqlalchemy", "alembic", "sqlite3"}
        elif PERSISTENCE_ROOT in relative.parents or relative == PERSISTENCE_ROOT:
            continue
        else:
            forbidden_roots = {"sqlalchemy", "alembic", "sqlite3"}
        found = sorted(
            {
                root
                for node in _imports(path)
                if (root := _module_root(node)) in forbidden_roots
            }
        )
        if found:
            violations.append(f"{relative}: {found}")
    assert violations == []


def test_forbidden_orm_sqlmodel_and_async_sqlite_symbols_absent_from_source() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        found = [snippet for snippet in FORBIDDEN_DEPENDENCY_SNIPPETS if snippet in text]
        if found:
            violations.append(f"{path.relative_to(repository_root)}: {found}")
    assert violations == []


def test_persistence_package_does_not_import_http_or_runtime_stack() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / PERSISTENCE_ROOT).rglob("*.py")):
        relative = path.relative_to(repository_root)
        found = sorted(
            {
                root
                for node in _imports(path)
                if (root := _module_root(node)) in FORBIDDEN_PERSISTENCE_IMPORT_ROOTS
            }
        )
        if found:
            violations.append(f"{relative}: {found}")
    assert violations == []


def test_no_module_level_engine_creation_or_database_work() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / PERSISTENCE_ROOT).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
                call_name = ast.unparse(node.value.func)
                if call_name.endswith(("create_engine", "create_sqlite_engine", "connect")):
                    violations.append(f"{path.relative_to(repository_root)}: {call_name}")
    assert violations == []


def test_importing_persistence_modules_does_not_connect_or_bind_socket() -> None:
    modules = (
        "kronika.infrastructure.persistence",
        "kronika.infrastructure.persistence.engine",
        "kronika.infrastructure.persistence.migrations",
        "kronika.infrastructure.persistence.cli",
        "kronika.infrastructure.persistence.device_repository",
        "kronika.infrastructure.persistence.library_repository",
        "kronika.infrastructure.persistence.media_repository",
        "kronika.infrastructure.persistence.catalog_schema",
    )
    with (
        patch("sqlite3.connect", side_effect=AssertionError("sqlite3.connect must not run")),
        patch("socket.socket.bind", side_effect=AssertionError("socket bind must not run")),
    ):
        for module_name in modules:
            importlib.import_module(module_name)


def test_framenest_persistence_error_strings_are_sanitized() -> None:
    from kronika.infrastructure.persistence.errors import (
        FrameNestMigrationError,
        FrameNestPersistenceError,
    )

    private_path = "/Users/agile/private/catalog.sqlite3"
    raw_sql = "INSERT INTO probe VALUES ('secret-sql-parameter')"
    raw_message = f"{private_path} {raw_sql}"

    persistence_error = FrameNestPersistenceError(
        "Database operation failed.",
        error_code="PERSISTENCE_OPERATION_FAILED",
        cause=RuntimeError(raw_message),
    )
    migration_error = FrameNestMigrationError(
        "Database migration failed.",
        error_code="MIGRATION_FAILED",
        cause=RuntimeError(raw_message),
    )

    for rendered in (str(persistence_error), repr(persistence_error), str(migration_error)):
        assert private_path not in rendered
        assert "secret-sql-parameter" not in rendered
        assert "INSERT INTO" not in rendered


def test_persistence_package_does_not_log_paths_sql_or_parameters() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    for path in sorted((repository_root / PERSISTENCE_ROOT).rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        assert "get_logger" not in text
        assert "logging." not in text
