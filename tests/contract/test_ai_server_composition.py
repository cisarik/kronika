"""Contract tests for server AI provider composition."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.ai.configuration import (
    AiServerConfig,
    AiTestState,
    default_ai_test_state_path,
    write_ai_server_config,
    write_ai_test_state,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
)

NVIDIA_MODEL_ID = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
DECLARED_PROVIDER_ID = "opencode-go"
DECLARED_MODEL_ID = "deepseek-v4-flash-vision-exp"
DECLARED_CREDENTIAL_ENV = "OPENCODE_API_KEY"


def _client(tmp_path: Path, *, provider_id: str) -> TestClient:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        ai_provider_id=provider_id,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings))


def _configured_nvidia_client(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    initial_status: str | None = None,
) -> tuple[TestClient, Path]:
    config_path = tmp_path / "ai" / "config.json"
    monkeypatch.setenv("FRAMENEST_AI_CONFIG_PATH", str(config_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "synthetic-nvidia-key")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    test_state_path = default_ai_test_state_path(config_path)
    if initial_status is not None:
        _write_test_state(test_state_path, status=initial_status)
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        ai_provider_id="nvidia-nim",
        ai_model_id=NVIDIA_MODEL_ID,
        _env_file=None,
    )
    return TestClient(create_app(settings=settings)), test_state_path


def _write_test_state(path: Path, *, status: str, model_id: str = NVIDIA_MODEL_ID) -> None:
    write_ai_test_state(
        AiTestState(
            provider_id="nvidia-nim",
            model_id=model_id,
            status=status,
            tested_at_ms=1_725_000_000_000,
        ),
        path,
    )


def test_server_composition_reports_vercel_configured_with_gateway_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "synthetic-gateway-key")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    response = _client(tmp_path, provider_id="vercel-ai-gateway").get(
        "/api/ai/media-suggestion-capability"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["configured"] is True
    assert payload["credential_available"] is True
    assert payload["provider_id"] == "vercel-ai-gateway"
    assert payload["model_id"] == "google/gemini-3.1-flash-lite"
    assert "synthetic" not in response.text


@pytest.mark.parametrize(
    "environ",
    [
        {},
        {"AI_GATEWAY_API_KEY": "   "},
        {"NVIDIA_API_KEY": "synthetic-nvidia-key"},
    ],
)
def test_server_composition_reports_vercel_not_configured_without_gateway_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    environ: dict[str, str],
) -> None:
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    for key, value in environ.items():
        monkeypatch.setenv(key, value)

    response = _client(tmp_path, provider_id="vercel-ai-gateway").get(
        "/api/ai/media-suggestion-capability"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is False
    assert payload["configured"] is True
    assert payload["credential_available"] is False
    assert payload["provider_id"] == "vercel-ai-gateway"
    assert "synthetic" not in response.text


def test_server_composition_reports_nvidia_configured_with_nvidia_credential(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NVIDIA_API_KEY", "synthetic-nvidia-key")
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)

    response = _client(tmp_path, provider_id="nvidia-nim").get(
        "/api/ai/media-suggestion-capability"
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["available"] is True
    assert payload["configured"] is True
    assert payload["credential_available"] is True
    assert payload["provider_id"] == "nvidia-nim"
    assert "synthetic" not in response.text


def test_capability_reads_latest_connection_test_state_without_app_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, test_state_path = _configured_nvidia_client(tmp_path, monkeypatch)

    initial = client.get("/api/ai/media-suggestion-capability")

    assert initial.status_code == 200
    assert initial.headers["cache-control"] == "no-store"
    initial_payload = initial.json()
    assert initial_payload["status"] == "configured_unverified"
    assert initial_payload["credential_available"] is True
    assert "last_connection_test" not in initial_payload

    _write_test_state(test_state_path, status="provider_unreachable")
    unreachable = client.get("/api/ai/media-suggestion-capability")

    assert unreachable.status_code == 200
    unreachable_payload = unreachable.json()
    assert unreachable_payload["status"] == "provider_unreachable"
    assert unreachable_payload["last_connection_test"] == {
        "status": "provider_unreachable",
        "tested_at_ms": 1_725_000_000_000,
    }

    _write_test_state(test_state_path, status="success")
    successful = client.get("/api/ai/media-suggestion-capability")

    assert successful.status_code == 200
    successful_payload = successful.json()
    assert successful_payload["available"] is True
    assert successful_payload["status"] == "available"
    assert successful_payload["last_connection_test"] == {
        "status": "success",
        "tested_at_ms": 1_725_000_000_000,
    }
    assert "synthetic" not in successful.text
    assert "NVIDIA_API_KEY" not in successful.text
    assert "FRAMENEST_AI_CONFIG_PATH" not in successful.text
    assert str(test_state_path) not in successful.text


@pytest.mark.parametrize(
    "replacement",
    ["missing", "malformed", "unsupported", "mismatched"],
)
def test_capability_fails_safely_when_current_test_state_is_unusable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    replacement: str,
) -> None:
    client, test_state_path = _configured_nvidia_client(
        tmp_path,
        monkeypatch,
        initial_status="provider_unreachable",
    )
    assert client.get("/api/ai/media-suggestion-capability").json()["status"] == "provider_unreachable"

    if replacement == "missing":
        test_state_path.unlink()
    elif replacement == "malformed":
        test_state_path.write_text("{not json", encoding="utf-8")
    elif replacement == "unsupported":
        test_state_path.write_text(
            json.dumps(
                {
                    "schema_version": 999,
                    "provider_id": "nvidia-nim",
                    "model_id": NVIDIA_MODEL_ID,
                    "status": "success",
                    "tested_at_ms": 1_725_000_000_000,
                }
            ),
            encoding="utf-8",
        )
    elif replacement == "mismatched":
        _write_test_state(test_state_path, status="success", model_id="nvidia/other-model")

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "configured_unverified"
    assert payload["credential_available"] is True
    assert "last_connection_test" not in payload


def test_capability_fails_safely_when_current_test_state_is_unreadable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, test_state_path = _configured_nvidia_client(
        tmp_path,
        monkeypatch,
        initial_status="provider_unreachable",
    )
    assert client.get("/api/ai/media-suggestion-capability").json()["status"] == "provider_unreachable"

    original_read_text = Path.read_text

    def unreadable(path: Path, *args: object, **kwargs: object) -> str:
        if path == test_state_path:
            raise OSError("permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", unreadable)
    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "configured_unverified"
    assert "last_connection_test" not in payload
    assert "permission denied" not in response.text
    assert str(test_state_path) not in response.text


def test_capability_get_does_not_create_missing_test_state_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, test_state_path = _configured_nvidia_client(tmp_path, monkeypatch)
    assert not test_state_path.parent.exists()

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    assert response.json()["status"] == "configured_unverified"
    assert not test_state_path.parent.exists()


def _declared_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    config_path = tmp_path / "ai" / "config.json"
    monkeypatch.setenv("FRAMENEST_AI_CONFIG_PATH", str(config_path))
    monkeypatch.delenv("OPENCODE_API_KEY", raising=False)
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=DECLARED_PROVIDER_ID,
            provider_models={DECLARED_PROVIDER_ID: DECLARED_MODEL_ID},
            updated_at_ms=1_725_000_000_000,
            providers={
                DECLARED_PROVIDER_ID: AiProviderRecord(
                    provider_id=DECLARED_PROVIDER_ID,
                    display_name="OpenCode Go",
                    protocol="openai-chat-completions",
                    base_url="https://opencode.ai/zen/go/v1",
                    credential_env=DECLARED_CREDENTIAL_ENV,
                    models=(
                        AiProviderModel(
                            model_id=DECLARED_MODEL_ID,
                            display_name="DeepSeek V4 Flash Vision Exp",
                            capabilities=("vision_input",),
                        ),
                    ),
                    source="declared",
                )
            },
        ),
        config_path,
    )
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    return TestClient(create_app(settings=settings))


def test_declared_provider_resolves_at_startup_with_credential_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _declared_client(tmp_path, monkeypatch)

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["provider_id"] == DECLARED_PROVIDER_ID
    assert payload["provider_display_name"] == "OpenCode Go"
    assert payload["model_id"] == DECLARED_MODEL_ID
    assert payload["configured"] is True
    assert payload["available"] is False
    assert payload["credential_available"] is False
    assert payload["status"] == "credential_unavailable"
    assert "synthetic" not in response.text
    assert DECLARED_CREDENTIAL_ENV not in response.text
    assert "FRAMENEST_AI_CONFIG_PATH" not in response.text
