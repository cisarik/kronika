"""Contract tests for the administrator automatic-analysis runtime setting."""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "user@example.com"
COMPANION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def _settings(tmp_path: Path, *, companion: bool = False) -> KronikaSettings:
    kwargs: dict[str, object] = {
        "database_path": tmp_path / "catalog.sqlite3",
        "gallery_preview_cache_path": tmp_path / "previews",
        "cover_storage_root": tmp_path / "covers",
        "cover_thumbnail_cache_path": tmp_path / "thumbs",
        "automatic_media_analysis_enabled": False,
        "ingress_mode": "tailscale_uds",
        "uds_path": tmp_path / "framenest.sock",
        "external_origin": EXTERNAL_ORIGIN,
        "identity_map": {ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        "_env_file": None,
    }
    if companion:
        kwargs["companion_extension_origins"] = [COMPANION_ORIGIN]
    return KronikaSettings(**kwargs)  # type: ignore[arg-type]


def _client(tmp_path: Path, *, companion: bool = False) -> tuple[TestClient, KronikaSettings]:
    settings = _settings(tmp_path, companion=companion)
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    return TestClient(app), settings


def _serve_headers(login: str = ADMIN_LOGIN, name: str = "Admin User") -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": name,
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _mutation_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login),
        "Origin": EXTERNAL_ORIGIN,
        "X-FrameNest-Request": "1",
    }


def _companion_headers(login: str = ADMIN_LOGIN) -> dict[str, str]:
    return {
        **_serve_headers(login, "User"),
        "Origin": COMPANION_ORIGIN,
        "X-FrameNest-Request": "1",
    }


def _error_code(response) -> str | None:
    payload = response.json()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        return code if isinstance(code, str) else None
    return None


def test_admin_put_enable_requires_confirm_and_persists(tmp_path: Path) -> None:
    client, settings = _client(tmp_path)
    missing = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_mutation_headers(),
        json={"automatic_media_analysis_enabled": True},
    )
    assert missing.status_code == 422
    assert _error_code(missing) == "CLOUD_CONFIRMATION_REQUIRED"
    enabled = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_mutation_headers(),
        json={
            "automatic_media_analysis_enabled": True,
            "confirm_cloud_upload": True,
        },
    )
    assert enabled.status_code == 200
    assert enabled.json() == {"automatic_media_analysis_enabled": True}
    capability = client.get(
        "/api/ai/automatic-analysis-capability",
        headers=_serve_headers(),
    )
    assert capability.status_code == 200
    assert capability.json()["automatic_analysis_enabled"] is True
    sidecar = settings.database_path.parent / "runtime-settings.json"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["automatic_media_analysis_enabled"] is True
    assert payload["schema_version"] == 1
    disabled = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_mutation_headers(),
        json={"automatic_media_analysis_enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json() == {"automatic_media_analysis_enabled": False}
    after_disable = client.get(
        "/api/ai/automatic-analysis-capability",
        headers=_serve_headers(),
    )
    assert after_disable.json()["automatic_analysis_enabled"] is False


def test_ordinary_put_is_capability_denied(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_mutation_headers(USER_LOGIN),
        json={"automatic_media_analysis_enabled": False},
    )
    assert response.status_code == 403
    assert _error_code(response) == "CAPABILITY_DENIED"


def test_put_without_mutation_header_is_rejected(tmp_path: Path) -> None:
    client, _ = _client(tmp_path)
    response = client.put(
        "/api/admin/settings/automatic-analysis",
        headers={**_serve_headers(), "Origin": EXTERNAL_ORIGIN},
        json={"automatic_media_analysis_enabled": False},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_HEADER_REQUIRED"


def test_companion_origin_put_succeeds_when_flagged(tmp_path: Path) -> None:
    client, _ = _client(tmp_path, companion=True)
    response = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_companion_headers(),
        json={
            "automatic_media_analysis_enabled": True,
            "confirm_cloud_upload": True,
        },
    )
    assert response.status_code == 200
    assert _error_code(response) != "MUTATION_ORIGIN_FORBIDDEN"
    assert response.json() == {"automatic_media_analysis_enabled": True}


def test_empty_companion_allowlist_rejects_extension_origin_put(
    tmp_path: Path,
) -> None:
    client, _ = _client(tmp_path, companion=False)
    response = client.put(
        "/api/admin/settings/automatic-analysis",
        headers=_companion_headers(),
        json={"automatic_media_analysis_enabled": False},
    )
    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"


def test_setting_survives_new_app_on_same_sidecar(tmp_path: Path) -> None:
    first, settings = _client(tmp_path)
    enabled = first.put(
        "/api/admin/settings/automatic-analysis",
        headers=_mutation_headers(),
        json={
            "automatic_media_analysis_enabled": True,
            "confirm_cloud_upload": True,
        },
    )
    assert enabled.status_code == 200
    first.close()
    second_app = create_app(settings=settings)
    with TestClient(second_app) as second:
        capability = second.get(
            "/api/ai/automatic-analysis-capability",
            headers=_serve_headers(),
        )
        assert capability.status_code == 200
        assert capability.json()["automatic_analysis_enabled"] is True
