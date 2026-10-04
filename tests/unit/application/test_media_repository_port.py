"""Architecture tests for the application media repository port."""

from __future__ import annotations

import ast
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_PORT = (
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "ports" / "media_repository.py"
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
        "kronika.infrastructure",
    }
)


def test_media_repository_port_imports_no_infrastructure_or_sqlalchemy() -> None:
    tree = ast.parse(APPLICATION_PORT.read_text(encoding="utf-8"), filename=str(APPLICATION_PORT))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        root = module.split(".")[0]
        if root in FORBIDDEN_IMPORT_ROOTS or module.startswith("kronika.infrastructure"):
            violations.append(module)
    assert violations == []
