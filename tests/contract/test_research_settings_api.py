"""Administrator research-settings API contract: security and concurrency."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kronika.adapters.api.ai_admin_api import (
    AiAdminApiDependencies,
    create_ai_admin_api_router,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from kronika.infrastructure.ai.configuration import (
    AiServerConfig,
    load_ai_server_config,
    load_ai_server_config_snapshot,
    write_ai_server_config,
)
from kronika.infrastructure.ai.research_configuration import (
    default_research_configuration,
)
from kronika.infrastructure.ai.registry import DynamicAiProviderResolver
from tests.support.record_access import install_synthetic_caller

CREDENTIAL_ENV = "KRONIKA_RESEARCH_OPENAI_API_KEY"
CREDENTIAL_VALUE = "synthetic-research-credential"
ADMIN = "ada@example.com"
USER = "bob@example.com"


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        _env_file=None,
    )


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "ai" / "config.json"


def _client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    caller: str | None = ADMIN,
    role: str = ROLE_ADMIN,
    seed: bool = False,
    enabled: bool = False,
) -> tuple[TestClient, Path]:
    monkeypatch.setenv(CREDENTIAL_ENV, CREDENTIAL_VALUE)
    config_path = _config_path(tmp_path / f"{caller or 'anonymous'}-{role}")
    resolver = DynamicAiProviderResolver(_settings(tmp_path), config_path=config_path)
    if seed:
        write_ai_server_config(
            AiServerConfig(
                active_provider_id="vercel-ai-gateway",
                provider_models={},
                updated_at_ms=1,
                research=default_research_configuration(enabled=enabled),
            ),
            config_path,
        )
    app = FastAPI()
    app.include_router(
        create_ai_admin_api_router(
            AiAdminApiDependencies(resolver=resolver, config_path=config_path)
        )
    )
    if caller is not None:
        install_synthetic_caller(app, caller, role=role)
    return TestClient(app), config_path


def _body(**overrides) -> dict[str, object]:
    body = {
        "enabled": False,
        "model_id": "gpt-5.5-2026-04-23",
        "daily_budget_usd_micros": 10_000_000,
        "monthly_budget_usd_micros": 30_000_000,
        "search_budget_reservation_usd_micros": 500_000,
        "research_budget_reservation_usd_micros": 5_000_000,
    }
    body.update(overrides)
    return body


def _etag(response) -> str:
    return response.headers["etag"]


def test_settings_routes_require_verified_identity_and_capability(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    anonymous, _ = _client(tmp_path, monkeypatch, caller=None, seed=True)
    assert anonymous.get("/api/admin/ai/research-settings").status_code == 401
    assert anonymous.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": '"absent"'},
        json=_body(),
    ).status_code == 401

    ordinary, _ = _client(tmp_path, monkeypatch, caller=USER, role=ROLE_USER, seed=True)
    denied = ordinary.get("/api/admin/ai/research-settings")
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == "CAPABILITY_DENIED"


def test_get_absent_configuration_returns_disabled_defaults(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=False)
    response = client.get("/api/admin/ai/research-settings")
    assert response.status_code == 200
    body = response.json()
    assert body["configuration_present"] is False
    assert body["revision"] == "absent"
    assert body["provider_id"] == "openai-responses"
    assert body["settings"]["enabled"] is False
    assert body["settings"]["model_id"] == "gpt-5.5-2026-04-23"
    assert body["credential_available"] is False
    assert [model["model_id"] for model in body["models"]] == [
        "gpt-5.5-2026-04-23",
        "gpt-5.6-sol",
        "gpt-5.6-terra",
        "gpt-5.6-luna",
    ]
    assert body["limits"]["max_daily_budget_usd_micros"] == 10_000_000
    assert _etag(response) == '"absent"'
    assert response.headers["cache-control"] == "no-store"
    assert not config_path.exists(), "GET must not create a configuration file"


def test_get_returns_revision_and_extended_pricing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    response = client.get("/api/admin/ai/research-settings")
    assert response.status_code == 200
    body = response.json()
    assert body["configuration_present"] is True
    assert body["credential_available"] is True
    snapshot = load_ai_server_config_snapshot(config_path)
    assert body["revision"] == snapshot.revision
    assert _etag(response) == f'"{snapshot.revision}"'
    luna = next(m for m in body["models"] if m["model_id"] == "gpt-5.6-luna")
    assert luna["pricing"]["short"]["cache_write_input_micro_usd_per_million"] == 250_000
    assert luna["pricing"]["long_context"]["output_micro_usd_per_million"] == 1_800_000
    assert luna["pricing"]["long_context_threshold_tokens"] == 272_000
    sol = next(m for m in body["models"] if m["model_id"] == "gpt-5.6-sol")
    assert sol["valid_until"] == "2026-11-22T00:00:00Z"


def test_put_refuses_absent_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, seed=False)
    response = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": '"absent"'},
        json=_body(),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_CONFIG_UNAVAILABLE"
    assert "Set up the server AI configuration first" in response.json()["error"]["message"]


def test_put_requires_if_match_and_rejects_stale_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, seed=True)
    missing = client.put("/api/admin/ai/research-settings", json=_body())
    assert missing.status_code == 409
    assert missing.json()["error"]["code"] == "AI_CONFIG_CONFLICT"

    stale = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": '"deadbeef"'},
        json=_body(),
    )
    assert stale.status_code == 409
    assert stale.json()["error"]["code"] == "AI_CONFIG_CONFLICT"

    wildcard = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": "*"},
        json=_body(),
    )
    assert wildcard.status_code == 409


def test_put_validates_bounds_and_unknown_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    revision = load_ai_server_config_snapshot(config_path).revision
    headers = {"If-Match": f'"{revision}"'}

    over_budget = client.put(
        "/api/admin/ai/research-settings",
        headers=headers,
        json=_body(daily_budget_usd_micros=10_000_001),
    )
    assert over_budget.status_code == 422
    assert over_budget.json()["error"]["code"] == "VALIDATION_FAILED"

    daily_over_monthly = client.put(
        "/api/admin/ai/research-settings",
        headers=headers,
        json=_body(daily_budget_usd_micros=30_000_000, monthly_budget_usd_micros=10_000_000),
    )
    assert daily_over_monthly.status_code == 422

    unknown_model = client.put(
        "/api/admin/ai/research-settings",
        headers=headers,
        json=_body(model_id="gpt-5.5"),
    )
    assert unknown_model.status_code == 422
    assert unknown_model.json()["error"]["code"] == "E_CAPABILITY_UNAVAILABLE"


def test_put_enable_requires_credential_but_disabled_edit_is_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    monkeypatch.delenv(CREDENTIAL_ENV, raising=False)
    revision = load_ai_server_config_snapshot(config_path).revision
    headers = {"If-Match": f'"{revision}"'}

    enabling = client.put(
        "/api/admin/ai/research-settings",
        headers=headers,
        json=_body(enabled=True),
    )
    assert enabling.status_code == 503
    assert enabling.json()["error"]["code"] == "E_NOT_CONFIGURED"

    disabled_edit = client.put(
        "/api/admin/ai/research-settings",
        headers=headers,
        json=_body(enabled=False, model_id="gpt-5.6-luna"),
    )
    assert disabled_edit.status_code == 200
    assert disabled_edit.json()["settings"]["model_id"] == "gpt-5.6-luna"


def test_put_persists_and_get_reflects_the_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    revision = load_ai_server_config_snapshot(config_path).revision
    response = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{revision}"'},
        json=_body(enabled=True, model_id="gpt-5.6-luna"),
    )
    assert response.status_code == 200
    body = response.json()
    assert body["changed"] is True
    assert body["settings"]["enabled"] is True
    assert body["settings"]["model_id"] == "gpt-5.6-luna"
    assert _etag(response) == f'"{body["revision"]}"'
    assert body["revision"] != revision

    stored = load_ai_server_config(config_path)
    assert stored is not None
    assert stored.research is not None
    assert stored.research.enabled is True
    assert stored.research.model_id == "gpt-5.6-luna"
    assert CREDENTIAL_VALUE not in response.text


def test_put_is_a_noop_result_when_nothing_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    revision = load_ai_server_config_snapshot(config_path).revision
    response = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{revision}"'},
        json=_body(),
    )
    assert response.status_code == 200
    assert response.json()["changed"] is False


def test_research_save_preserves_media_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from kronika.infrastructure.ai.provider_records import (
        AiProviderModel,
        AiProviderRecord,
    )

    record = AiProviderRecord(
        provider_id="opencode-go",
        display_name="OpenCode Go",
        protocol="openai-chat-completions",
        base_url="https://opencode.ai/zen/go/v1",
        credential_env="OPENCODE_API_KEY",
        models=(
            AiProviderModel(
                model_id="deepseek-v4-flash-vision-exp",
                display_name="DeepSeek V4 Flash Vision Exp",
                capabilities=("vision_input",),
            ),
        ),
        source="declared",
    )
    monkeypatch.setenv(CREDENTIAL_ENV, CREDENTIAL_VALUE)
    config_path = _config_path(tmp_path)
    write_ai_server_config(
        AiServerConfig(
            active_provider_id="vercel-ai-gateway",
            provider_models={"vercel-ai-gateway": "google/custom"},
            updated_at_ms=1,
            providers={"opencode-go": record},
        ),
        config_path,
    )
    resolver = DynamicAiProviderResolver(_settings(tmp_path), config_path=config_path)
    app = FastAPI()
    app.include_router(
        create_ai_admin_api_router(
            AiAdminApiDependencies(resolver=resolver, config_path=config_path)
        )
    )
    install_synthetic_caller(app, ADMIN, role=ROLE_ADMIN)
    client = TestClient(app)

    revision = load_ai_server_config_snapshot(config_path).revision
    response = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{revision}"'},
        json=_body(enabled=True, model_id="gpt-5.6-luna"),
    )
    assert response.status_code == 200
    stored = load_ai_server_config(config_path)
    assert stored is not None
    assert stored.provider_models == {"vercel-ai-gateway": "google/custom"}
    assert stored.providers["opencode-go"] == record
    assert stored.active_provider_id == "vercel-ai-gateway"


def test_expired_model_cannot_be_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    monkeypatch.setattr(
        "kronika.adapters.api.ai_admin_api.now_ms",
        lambda: 1_900_000_000_000,
    )
    revision = load_ai_server_config_snapshot(config_path).revision
    response = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{revision}"'},
        json=_body(enabled=True, model_id="gpt-5.6-sol"),
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "E_NOT_CONFIGURED"
    assert "pricing must be reviewed" in response.json()["error"]["message"]


def test_get_body_contains_no_secret_material(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, seed=True)
    response = client.get("/api/admin/ai/research-settings")
    text = response.text.lower()
    assert CREDENTIAL_VALUE.lower() not in text
    assert "authorization" not in text
    assert "bearer" not in text
    assert "api_key" not in text
    assert json.loads(response.text)["provider_id"] == "openai-responses"


def test_get_returns_exactly_the_seven_planned_fields_and_put_adds_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, config_path = _client(tmp_path, monkeypatch, seed=True)
    get_body = client.get("/api/admin/ai/research-settings").json()
    assert sorted(get_body) == [
        "configuration_present",
        "credential_available",
        "limits",
        "models",
        "provider_id",
        "revision",
        "settings",
    ]
    assert "changed" not in get_body

    revision = load_ai_server_config_snapshot(config_path).revision
    put_body = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{revision}"'},
        json=_body(enabled=True, model_id="gpt-5.6-luna"),
    ).json()
    assert sorted(put_body) == sorted([*get_body, "changed"])
    assert put_body["changed"] is True
    assert isinstance(put_body["changed"], bool)

    unchanged = client.put(
        "/api/admin/ai/research-settings",
        headers={"If-Match": f'"{put_body["revision"]}"'},
        json=_body(enabled=True, model_id="gpt-5.6-luna"),
    ).json()
    assert unchanged["changed"] is False


def test_settings_object_has_exactly_the_six_writable_fields(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, _ = _client(tmp_path, monkeypatch, seed=True)
    settings = client.get("/api/admin/ai/research-settings").json()["settings"]
    assert sorted(settings) == [
        "daily_budget_usd_micros",
        "enabled",
        "model_id",
        "monthly_budget_usd_micros",
        "research_budget_reservation_usd_micros",
        "search_budget_reservation_usd_micros",
    ]
