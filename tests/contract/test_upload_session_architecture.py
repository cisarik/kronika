"""Negative architecture contracts for quarantine upload transport."""

from __future__ import annotations

import ast
from pathlib import Path
import tomllib

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UPLOAD_FOUNDATION_FILES = (
    REPOSITORY_ROOT / "src" / "kronika" / "domain" / "uploads.py",
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "ports" / "upload_sessions.py",
    REPOSITORY_ROOT
    / "src"
    / "kronika"
    / "infrastructure"
    / "persistence"
    / "upload_session_repository.py",
    REPOSITORY_ROOT / "src" / "kronika" / "infrastructure" / "persistence" / "upload_schema.py",
)
UPLOAD_TRANSPORT_FILES = (
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "upload_transport.py",
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "ports" / "quarantine_storage.py",
    REPOSITORY_ROOT / "src" / "kronika" / "adapters" / "api" / "upload_api.py",
    REPOSITORY_ROOT
    / "src"
    / "kronika"
    / "infrastructure"
    / "filesystem"
    / "quarantine_storage.py",
)


def test_upload_transport_routes_exist_but_fail_closed_when_unconfigured() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/uploads",
        json={"display_filename": "example.gif", "declared_size_bytes": 1},
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPLOAD_CAPABILITY_NOT_CONFIGURED"


def test_upload_validation_adds_no_public_route() -> None:
    app = create_app()
    paths = {route.path for route in app.routes if hasattr(route, "path")}

    assert "/api/uploads/{upload_id}/validate" not in paths
    assert "/api/uploads/{upload_id}/validation" not in paths
    assert "/api/upload-validation" not in paths


def test_upload_session_foundation_contains_no_media_filesystem_or_provider_calls() -> None:
    forbidden_import_roots = {
        "openai",
        "requests",
        "httpx",
        "PIL",
        "subprocess",
    }
    forbidden_call_names = {
        "open",
        "read_bytes",
        "write_bytes",
        "rename",
        "replace",
        "unlink",
        "rmdir",
        "mkdir",
    }
    found_imports: list[str] = []
    found_calls: list[str] = []
    for path in UPLOAD_FOUNDATION_FILES:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found_imports.extend(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module is not None:
                found_imports.append(node.module.split(".")[0])
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    found_calls.append(node.func.id)
                elif isinstance(node.func, ast.Attribute):
                    found_calls.append(node.func.attr)

    assert sorted(set(found_imports) & forbidden_import_roots) == []
    assert sorted(set(found_calls) & forbidden_call_names) == []


def test_upload_session_foundation_does_not_insert_catalog_or_analysis_state() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in UPLOAD_FOUNDATION_FILES)

    assert "logical_media" not in combined
    assert "physical_media_locations" not in combined
    assert "media_metadata" not in combined
    assert "media_canonical_tags" not in combined
    assert "media_analysis" not in combined
    assert "suggestion" not in combined
    assert "provider" not in combined


def test_upload_transport_uses_no_multipart_body_buffering_or_media_parser() -> None:
    combined = "\n".join(path.read_text(encoding="utf-8") for path in UPLOAD_TRANSPORT_FILES)

    assert "multipart" not in combined.lower()
    assert "request.body(" not in combined
    assert "PIL" not in combined
    assert "ffprobe" not in combined
    assert "ffmpeg" not in combined
    assert "hashlib" not in combined
    assert "logical_media" not in combined
    assert "physical_media_locations" not in combined
    assert "media_metadata" not in combined
    assert "provider" not in combined.lower()


def test_upload_transport_adds_no_migration_or_dependency() -> None:
    versions = (
        REPOSITORY_ROOT
        / "src"
        / "kronika"
        / "infrastructure"
        / "persistence"
        / "alembic_environment"
        / "versions"
    )

    assert not versions.joinpath("0010_upload_transport.py").exists()
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = "\n".join(pyproject["project"]["dependencies"])
    assert "python-multipart" not in dependencies

    lock = tomllib.loads((REPOSITORY_ROOT / "poetry.lock").read_text(encoding="utf-8"))
    package_names = {package["name"] for package in lock["package"]}
    assert "python-multipart" not in package_names
