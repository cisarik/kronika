"""Configured local owner is loopback-only and never manufactures an admin."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _settings(tmp_path: Path, login: str, role: str) -> KronikaSettings:
    settings = KronikaSettings(
        host="127.0.0.1",
        port=8000,
        database_path=tmp_path / "catalog.sqlite3",
        identity_map={login: role},
        local_owner_login=login,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    return settings


def test_loopback_mutation_without_origin_is_forbidden(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, "alice", "user"))
    client = TestClient(app, client=("127.0.0.1", 50000))
    response = client.post(
        "/api/uploads",
        json={"display_filename": "clip.gif", "declared_size_bytes": 8},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "MUTATION_ORIGIN_FORBIDDEN"


def test_non_loopback_client_does_not_receive_local_owner(tmp_path: Path) -> None:
    app = create_app(settings=_settings(tmp_path, "alice", "user"))
    client = TestClient(app, client=("192.0.2.10", 50000))
    response = client.get("/api/media")
    assert response.status_code == 200
    assert response.json()["items"] == []


def test_configured_loopback_echoes_mapped_identity(tmp_path: Path) -> None:
    user_root = tmp_path / "user"
    admin_root = tmp_path / "admin"
    user_root.mkdir()
    admin_root.mkdir()
    user_app = create_app(settings=_settings(user_root, "alice", "user"))
    user = TestClient(user_app, client=("127.0.0.1", 9))
    echoed = user.get(
        "/api/audience/me",
        headers={"X-Forwarded-User": "ada", "Tailscale-User-Login": "ada@example.com"},
    )
    assert echoed.status_code == 200
    body = echoed.json()
    assert body["audience"] == "trusted_loopback"
    assert body["identity"]["login"] == "alice"
    assert body["identity"]["role"] == "user"
    assert body["identity"]["provenance"] == "local-config"
    assert "research.run" in body["capabilities"]
    assert "records.approve" not in body["capabilities"]

    admin_app = create_app(settings=_settings(admin_root, "ada", "admin"))
    admin = TestClient(admin_app, client=("127.0.0.1", 9))
    admin_body = admin.get("/api/audience/me").json()
    assert admin_body["identity"]["login"] == "ada"
    assert admin_body["identity"]["role"] == "admin"
    assert "records.approve" in admin_body["capabilities"]

    outsider = TestClient(user_app, client=("192.0.2.10", 9))
    assert outsider.get("/api/audience/me").json()["identity"] is None


def test_missing_local_owner_keeps_null_identity(tmp_path: Path) -> None:
    settings = KronikaSettings(
        host="127.0.0.1",
        port=8000,
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    client = TestClient(create_app(settings=settings), client=("127.0.0.1", 9))
    response = client.get(
        "/api/audience/me",
        headers={"X-Forwarded-User": "ada"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["identity"] is None
    assert body["audience"] == "trusted_loopback"
    assert "gallery.read" in body["capabilities"]


def test_unmapped_local_owner_is_rejected() -> None:
    from pydantic import ValidationError

    try:
        KronikaSettings(
            identity_map={"alice": "user"},
            local_owner_login="intruder",
            _env_file=None,
        )
    except ValidationError as exc:
        assert "local owner" in str(exc).lower()
    else:
        raise AssertionError("unmapped local owner was accepted")
