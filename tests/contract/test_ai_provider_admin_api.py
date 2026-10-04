"""Contract tests for the administrator AI provider API."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.ai_admin_api import AiAdminApiDependencies
from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.ai.configuration import (
    AiServerConfig,
    load_ai_server_config,
    write_ai_server_config,
)
from kronika.infrastructure.ai.registry import (
    DynamicAiProviderResolver,
    LazyResolvedAiProvider,
)
from kronika.infrastructure.ai.transport import HttpsJsonResponse
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_LOGIN = "user@example.com"
STRANGER_LOGIN = "stranger@example.com"

DECLARED_PROVIDER_ID = "opencode-go"
DECLARED_MODEL_ID = "deepseek-v4-flash-vision-exp"
SECOND_MODEL_ID = "second-declared-model"
CREDENTIAL_ENV = "OPENCODE_API_KEY"
CREDENTIAL_VALUE = "synthetic-declared-credential"


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


class _FakeTransport:
    def __init__(self, contents: tuple[str, ...] = ("ok",)) -> None:
        self.contents = list(contents)
        self.calls: list[tuple[str, dict[str, str], bytes, int]] = []

    def post_json(
        self,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse:
        self.calls.append((url, dict(headers), body, max_request_bytes))
        content = self.contents.pop(0) if self.contents else "ok"
        return HttpsJsonResponse(
            status_code=200,
            body=json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8"),
        )


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin", USER_LOGIN: "user"},
        _env_file=None,
    )


def _config_path(tmp_path: Path) -> Path:
    return tmp_path / "ai" / "config.json"


def _client(
    tmp_path: Path,
    monkeypatch,
    *,
    transport: _FakeTransport | None = None,
    configured: bool = False,
) -> tuple[TestClient, KronikaSettings, _FakeTransport, Path]:
    config_path = _config_path(tmp_path)
    monkeypatch.setenv("FRAMENEST_AI_CONFIG_PATH", str(config_path))
    monkeypatch.setenv(CREDENTIAL_ENV, CREDENTIAL_VALUE)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    fake = transport or _FakeTransport()
    resolver = DynamicAiProviderResolver(
        settings,
        config_path=config_path,
        transport=fake,
    )
    if configured:
        _write_declared_config(config_path, active=True)
    app = create_app(
        settings=settings,
        ai_admin_api_dependencies=AiAdminApiDependencies(resolver=resolver),
    )
    return TestClient(app), settings, fake, config_path


def _record_body(
    *,
    capabilities: tuple[str, ...] = ("vision_input",),
    model_id: str = DECLARED_MODEL_ID,
) -> dict[str, object]:
    return {
        "name": "OpenCode Go",
        "protocol": "openai-chat-completions",
        "base_url": "https://opencode.ai/zen/go/v1",
        "credential_env": CREDENTIAL_ENV,
        "models": {
            model_id: {"name": "Declared Model", "capabilities": list(capabilities)}
        },
    }


def _write_declared_config(config_path: Path, *, active: bool) -> None:
    from kronika.infrastructure.ai.provider_records import (
        AiProviderModel,
        AiProviderRecord,
    )

    record = AiProviderRecord(
        provider_id=DECLARED_PROVIDER_ID,
        display_name="OpenCode Go",
        protocol="openai-chat-completions",
        base_url="https://opencode.ai/zen/go/v1",
        credential_env=CREDENTIAL_ENV,
        models=(
            AiProviderModel(
                model_id=DECLARED_MODEL_ID,
                display_name="Declared Model",
                capabilities=("vision_input",),
            ),
        ),
        source="declared",
    )
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=DECLARED_PROVIDER_ID if active else "vercel-ai-gateway",
            provider_models=({DECLARED_PROVIDER_ID: DECLARED_MODEL_ID} if active else {}),
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: record},
        ),
        config_path,
    )


def _error_code(response) -> str | None:
    payload = response.json()
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = error.get("code")
        return code if isinstance(code, str) else None
    return None


def _audit_rows(database_path: Path) -> list[sqlite3.Row]:
    connection = sqlite3.connect(database_path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(
            "SELECT action, capability, target_type, target_id, outcome, http_status"
            " FROM security_audit_events ORDER BY occurred_at_ms, id"
        ).fetchall()
    finally:
        connection.close()


def test_admin_crud_ping_and_pong_happy_paths(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, transport, config_path = _client(
        tmp_path,
        monkeypatch,
        transport=_FakeTransport(("ok", "red")),
    )

    listed = client.get("/api/admin/ai/providers", headers=_serve_headers())
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["active_provider_id"] is None
    assert payload["configuration_source"] == "unconfigured"
    assert payload["supported_protocols"] == ["openai-chat-completions"]
    assert payload["limits"]["max_declared_providers"] == 16
    assert [entry["provider_id"] for entry in payload["providers"]] == [
        "nvidia-nim",
        "vercel-ai-gateway",
    ]
    assert transport.calls == []

    put = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json=_record_body(),
    )
    assert put.status_code == 200
    stored = put.json()
    assert stored["provider_id"] == DECLARED_PROVIDER_ID
    assert stored["source"] == "declared"
    assert stored["credential_env"] == CREDENTIAL_ENV
    assert stored["models"][0]["capabilities"] == ["vision_input"]
    assert CREDENTIAL_VALUE not in put.text

    activate = client.put(
        "/api/admin/ai/active-selection",
        headers=_mutation_headers(),
        json={"provider_id": DECLARED_PROVIDER_ID, "model_id": DECLARED_MODEL_ID},
    )
    assert activate.status_code == 200
    assert activate.json() == {
        "active_provider_id": DECLARED_PROVIDER_ID,
        "active_model_id": DECLARED_MODEL_ID,
    }

    ping = client.post(
        "/api/admin/ai/ping",
        headers=_mutation_headers(),
        json={},
    )
    assert ping.status_code == 200
    ping_payload = ping.json()
    assert ping_payload["status"] == "success"
    assert ping_payload["provider_id"] == DECLARED_PROVIDER_ID
    assert ping_payload["model_id"] == DECLARED_MODEL_ID
    assert ping_payload["credential_available"] is True
    assert len(transport.calls) == 1

    pong = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={"confirm_cloud_upload": True},
    )
    assert pong.status_code == 200
    pong_payload = pong.json()
    assert pong_payload["status"] == "success"
    assert pong_payload["matched"] is True
    assert pong_payload["expected_color"] == "red"
    assert pong_payload["observed_color"] == "red"
    assert len(transport.calls) == 2
    assert CREDENTIAL_VALUE not in pong.text

    after = client.get("/api/admin/ai/providers", headers=_serve_headers())
    assert after.status_code == 200
    after_payload = after.json()
    assert after_payload["active_provider_id"] == DECLARED_PROVIDER_ID
    assert after_payload["active_model_id"] == DECLARED_MODEL_ID
    assert after_payload["configuration_source"] == "server config"
    entry = next(
        item
        for item in after_payload["providers"]
        if item["provider_id"] == DECLARED_PROVIDER_ID
    )
    assert entry["credential_available"] is True
    assert entry["selected_model_id"] == DECLARED_MODEL_ID
    assert entry["supports_vision"] is True
    assert entry["last_test"]["status"] == "success"
    assert entry["last_vision_probe"]["status"] == "success"
    assert entry["last_vision_probe"]["observed_color"] == "red"
    assert len(transport.calls) == 2
    assert CREDENTIAL_VALUE not in after.text
    assert str(config_path) not in after.text


def test_authorization_matrix_and_audited_denial(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, settings, transport, _ = _client(tmp_path, monkeypatch)
    body = _record_body()

    ordinary_get = client.get("/api/admin/ai/providers", headers=_serve_headers(USER_LOGIN))
    assert ordinary_get.status_code == 403
    assert _error_code(ordinary_get) == "CAPABILITY_DENIED"

    ordinary_put = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(USER_LOGIN),
        json=body,
    )
    assert ordinary_put.status_code == 403
    assert _error_code(ordinary_put) == "CAPABILITY_DENIED"

    denied_rows = [
        row for row in _audit_rows(settings.database_path) if row["outcome"] == "denied"
    ]
    assert any(row["action"] == "ai.provider.put" for row in denied_rows)

    unmapped = client.get(
        "/api/admin/ai/providers",
        headers=_serve_headers(STRANGER_LOGIN),
    )
    assert unmapped.status_code == 403
    assert _error_code(unmapped) == "IDENTITY_NOT_AUTHORIZED"

    missing_header = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers={**_serve_headers(), "Origin": EXTERNAL_ORIGIN},
        json=body,
    )
    assert missing_header.status_code == 403
    assert _error_code(missing_header) == "MUTATION_HEADER_REQUIRED"

    wrong_origin = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers={
            **_serve_headers(),
            "Origin": "https://attacker.example.ts.net",
            "X-FrameNest-Request": "1",
        },
        json=body,
    )
    assert wrong_origin.status_code == 403
    assert _error_code(wrong_origin) == "MUTATION_ORIGIN_FORBIDDEN"
    assert transport.calls == []


def test_builtin_active_and_validation_refusals(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, _, config_path = _client(
        tmp_path,
        monkeypatch,
        configured=True,
    )

    builtin_put = client.put(
        "/api/admin/ai/providers/nvidia-nim",
        headers=_mutation_headers(),
        json=_record_body(),
    )
    assert builtin_put.status_code == 409
    assert _error_code(builtin_put) == "AI_PROVIDER_BUILTIN"

    builtin_delete = client.delete(
        "/api/admin/ai/providers/vercel-ai-gateway",
        headers=_mutation_headers(),
    )
    assert builtin_delete.status_code == 409
    assert _error_code(builtin_delete) == "AI_PROVIDER_BUILTIN"

    active_delete = client.delete(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
    )
    assert active_delete.status_code == 409
    assert _error_code(active_delete) == "AI_PROVIDER_ACTIVE"

    active_model_removal = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json=_record_body(model_id=SECOND_MODEL_ID),
    )
    assert active_model_removal.status_code == 409
    assert _error_code(active_model_removal) == "AI_PROVIDER_ACTIVE"

    unknown_provider = client.put(
        "/api/admin/ai/active-selection",
        headers=_mutation_headers(),
        json={"provider_id": "undeclared-provider", "model_id": "some-model"},
    )
    assert unknown_provider.status_code == 404
    assert _error_code(unknown_provider) == "AI_PROVIDER_NOT_FOUND"

    unknown_model = client.put(
        "/api/admin/ai/active-selection",
        headers=_mutation_headers(),
        json={"provider_id": DECLARED_PROVIDER_ID, "model_id": "undeclared-model"},
    )
    assert unknown_model.status_code == 422
    assert _error_code(unknown_model) == "AI_PROVIDER_INVALID"

    unsupported_protocol = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json={**_record_body(), "protocol": "anthropic-messages"},
    )
    assert unsupported_protocol.status_code == 422
    assert _error_code(unsupported_protocol) == "AI_PROVIDER_PROTOCOL_UNSUPPORTED"

    malformed_record = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json={**_record_body(), "base_url": "http://opencode.ai/zen/go/v1"},
    )
    assert malformed_record.status_code == 422
    assert _error_code(malformed_record) == "AI_PROVIDER_INVALID"

    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == DECLARED_PROVIDER_ID
    assert config.provider_models[DECLARED_PROVIDER_ID] == DECLARED_MODEL_ID


def test_pong_requires_confirmation_and_declared_vision_capability(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, transport, _ = _client(tmp_path, monkeypatch)

    missing_confirm = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={},
    )
    assert missing_confirm.status_code == 409
    assert _error_code(missing_confirm) == "CLOUD_CONFIRMATION_REQUIRED"
    assert transport.calls == []

    put = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json=_record_body(capabilities=()),
    )
    assert put.status_code == 200
    activate = client.put(
        "/api/admin/ai/active-selection",
        headers=_mutation_headers(),
        json={"provider_id": DECLARED_PROVIDER_ID, "model_id": DECLARED_MODEL_ID},
    )
    assert activate.status_code == 200

    no_vision = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={"confirm_cloud_upload": True},
    )
    assert no_vision.status_code == 409
    assert _error_code(no_vision) == "AI_MODEL_CAPABILITY_MISSING"
    assert transport.calls == []


def test_busy_activity_locks_return_conflict_without_provider_calls(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, transport, config_path = _client(
        tmp_path,
        monkeypatch,
        configured=True,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)

    (config_path.parent / ".test.lock").write_text("", encoding="utf-8")
    ping = client.post("/api/admin/ai/ping", headers=_mutation_headers(), json={})
    assert ping.status_code == 409
    assert _error_code(ping) == "AI_PROVIDER_BUSY"
    assert transport.calls == []
    (config_path.parent / ".test.lock").unlink()

    (config_path.parent / ".vision-probe.lock").write_text("", encoding="utf-8")
    pong = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={"confirm_cloud_upload": True},
    )
    assert pong.status_code == 409
    assert _error_code(pong) == "AI_PROVIDER_BUSY"
    assert transport.calls == []


def test_audit_rows_exist_before_provider_execution(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, settings, transport, config_path = _client(
        tmp_path,
        monkeypatch,
        configured=True,
    )
    config_path.parent.mkdir(parents=True, exist_ok=True)
    (config_path.parent / ".test.lock").write_text("", encoding="utf-8")

    busy_ping = client.post("/api/admin/ai/ping", headers=_mutation_headers(), json={})
    assert busy_ping.status_code == 409
    assert transport.calls == []
    rows = _audit_rows(settings.database_path)
    ping_rows = [row for row in rows if row["action"] == "ai.provider.ping"]
    assert len(ping_rows) == 1
    assert ping_rows[0]["outcome"] == "allowed"
    assert ping_rows[0]["capability"] == "provider.operate"
    assert ping_rows[0]["target_type"] == "ai_provider"

    (config_path.parent / ".test.lock").unlink()
    pong = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={"confirm_cloud_upload": True},
    )
    assert pong.status_code in {200, 409, 429, 502, 503}
    rows = _audit_rows(settings.database_path)
    pong_rows = [row for row in rows if row["action"] == "ai.provider.pong"]
    assert len(pong_rows) == 1
    assert pong_rows[0]["outcome"] == "allowed"
    assert pong_rows[0]["target_type"] == "ai_provider"


def test_missing_credential_is_reported_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, transport, _ = _client(tmp_path, monkeypatch, configured=True)
    monkeypatch.delenv(CREDENTIAL_ENV, raising=False)

    ping = client.post("/api/admin/ai/ping", headers=_mutation_headers(), json={})
    assert ping.status_code == 503
    assert _error_code(ping) == "AI_PROVIDER_NOT_CONFIGURED"
    assert CREDENTIAL_ENV in ping.json()["error"]["message"]
    assert transport.calls == []

    pong = client.post(
        "/api/admin/ai/pong",
        headers=_mutation_headers(),
        json={"confirm_cloud_upload": True},
    )
    assert pong.status_code == 503
    assert _error_code(pong) == "AI_PROVIDER_NOT_CONFIGURED"

    listed = client.get("/api/admin/ai/providers", headers=_serve_headers())
    entry = next(
        item
        for item in listed.json()["providers"]
        if item["provider_id"] == DECLARED_PROVIDER_ID
    )
    assert entry["credential_available"] is False


def test_delete_removes_an_inactive_declared_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, _, _, config_path = _client(tmp_path, monkeypatch)
    second_provider_id = "second-provider"

    put = client.put(
        f"/api/admin/ai/providers/{second_provider_id}",
        headers=_mutation_headers(),
        json={**_record_body(), "name": "Second Provider"},
    )
    assert put.status_code == 200

    removed = client.delete(
        f"/api/admin/ai/providers/{second_provider_id}",
        headers=_mutation_headers(),
    )
    assert removed.status_code == 200
    assert removed.json() == {"removed_provider_id": second_provider_id}

    config = load_ai_server_config(config_path)
    assert config is not None
    assert second_provider_id not in config.providers
    assert second_provider_id not in config.provider_models


def test_dynamic_effect_without_restart(
    tmp_path: Path,
    monkeypatch,
) -> None:
    client, settings, transport, config_path = _client(
        tmp_path,
        monkeypatch,
        transport=_FakeTransport(("ok",)),
    )

    put = client.put(
        f"/api/admin/ai/providers/{DECLARED_PROVIDER_ID}",
        headers=_mutation_headers(),
        json=_record_body(),
    )
    assert put.status_code == 200
    activate = client.put(
        "/api/admin/ai/active-selection",
        headers=_mutation_headers(),
        json={"provider_id": DECLARED_PROVIDER_ID, "model_id": DECLARED_MODEL_ID},
    )
    assert activate.status_code == 200

    capability = client.get(
        "/api/ai/media-suggestion-capability",
        headers=_serve_headers(),
    )
    assert capability.status_code == 200
    capability_payload = capability.json()
    assert capability_payload["provider_id"] == DECLARED_PROVIDER_ID
    assert capability_payload["model_id"] == DECLARED_MODEL_ID
    assert capability_payload["available"] is True
    assert capability_payload["credential_available"] is True

    analysis_capability = client.get(
        "/api/ai/automatic-analysis-capability",
        headers=_serve_headers(),
    )
    assert analysis_capability.status_code == 200
    assert analysis_capability.json()["provider_id"] == DECLARED_PROVIDER_ID
    assert analysis_capability.json()["model_id"] == DECLARED_MODEL_ID
    assert analysis_capability.json()["provider_configured"] is True

    resolver = DynamicAiProviderResolver(
        settings,
        config_path=config_path,
        transport=transport,
    )
    lazy = LazyResolvedAiProvider(resolver)
    before = len(transport.calls)
    lazy.test_connection()
    assert len(transport.calls) == before + 1
    assert transport.calls[-1][0] == "https://opencode.ai/zen/go/v1/chat/completions"
