"""Unit tests for non-secret server AI configuration storage."""

from __future__ import annotations

import json
import stat
from dataclasses import replace
from pathlib import Path

import pytest

from kronika.infrastructure.ai.configuration import (
    AiConfigConflictError,
    AiConfigurationError,
    AiServerConfig,
    AiStatusSnapshot,
    AiTestState,
    default_ai_config_path,
    load_ai_server_config_snapshot,
    load_ai_status_snapshot,
    load_ai_server_config,
    load_ai_test_state,
    mutate_ai_server_config,
    now_ms,
    write_ai_status_snapshot,
    write_ai_server_config,
    write_ai_test_state,
)
from kronika.infrastructure.ai.constants import VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
from kronika.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
)
from kronika.infrastructure.ai.research_configuration import (
    default_research_configuration,
    serialize_research_configuration,
)

DECLARED_PROVIDER_ID = "opencode-go"
DECLARED_MODEL_ID = "deepseek-v4-flash-vision-exp"


def _declared_record() -> AiProviderRecord:
    return AiProviderRecord(
        provider_id=DECLARED_PROVIDER_ID,
        display_name="OpenCode Go",
        protocol="openai-chat-completions",
        base_url="https://opencode.ai/zen/go/v1",
        credential_env="OPENCODE_API_KEY",
        models=(
            AiProviderModel(
                model_id=DECLARED_MODEL_ID,
                display_name="DeepSeek V4 Flash Vision Exp",
                capabilities=("vision_input",),
            ),
        ),
        source="declared",
    )


def _declared_config() -> AiServerConfig:
    return AiServerConfig(
        active_provider_id=DECLARED_PROVIDER_ID,
        provider_models={DECLARED_PROVIDER_ID: DECLARED_MODEL_ID},
        updated_at_ms=1_725_000_000_000,
        providers={DECLARED_PROVIDER_ID: _declared_record()},
    )


def test_config_path_override_is_absolute(tmp_path: Path) -> None:
    path = tmp_path / "ai" / "config.json"

    assert default_ai_config_path({"FRAMENEST_AI_CONFIG_PATH": str(path)}) == path

    with pytest.raises(AiConfigurationError):
        default_ai_config_path({"FRAMENEST_AI_CONFIG_PATH": "relative.json"})


def test_write_and_load_config_persists_no_secret(tmp_path: Path) -> None:
    path = tmp_path / "config" / "ai.json"

    write_ai_server_config(
        AiServerConfig(
            active_provider_id="vercel-ai-gateway",
            provider_models={"vercel-ai-gateway": VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID},
            updated_at_ms=now_ms(),
        ),
        path,
    )

    loaded = load_ai_server_config(path)
    assert loaded is not None
    assert loaded.active_provider_id == "vercel-ai-gateway"
    assert loaded.provider_models["vercel-ai-gateway"] == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert loaded.providers == {}
    raw = path.read_text(encoding="utf-8")
    assert "API_KEY" not in raw
    assert "Authorization" not in raw
    assert "Bearer" not in raw
    assert "data:" not in raw


def test_v2_declared_config_round_trips_with_exact_keys(tmp_path: Path) -> None:
    path = tmp_path / "config" / "ai.json"

    write_ai_server_config(_declared_config(), path)

    loaded = load_ai_server_config(path)
    assert loaded == _declared_config()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(payload) == [
        "active_provider_id",
        "provider_models",
        "providers",
        "schema_version",
        "updated_at_ms",
    ]
    assert payload["schema_version"] == 3
    assert "research" not in payload
    assert loaded.research is None
    record = payload["providers"][DECLARED_PROVIDER_ID]
    assert sorted(record) == ["base_url", "credential_env", "models", "name", "protocol"]
    assert record["credential_env"] == "OPENCODE_API_KEY"
    assert sorted(record["models"][DECLARED_MODEL_ID]) == ["capabilities", "name"]
    raw = path.read_text(encoding="utf-8")
    assert raw.count("API_KEY") == 1
    assert "Authorization" not in raw
    assert "Bearer" not in raw
    assert "data:" not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_v1_read_upgrades_to_v2_without_inventing_providers(tmp_path: Path) -> None:
    path = tmp_path / "config" / "ai.json"
    path.parent.mkdir()
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_provider_id": "nvidia-nim",
                "provider_models": {"nvidia-nim": "nvidia/example", "vercel-ai-gateway": "google/custom"},
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_ai_server_config(path)
    assert loaded is not None
    assert loaded.schema_version == 3
    assert loaded.research is None
    assert loaded.active_provider_id == "nvidia-nim"
    assert loaded.provider_models == {
        "nvidia-nim": "nvidia/example",
        "vercel-ai-gateway": "google/custom",
    }
    assert loaded.providers == {}

    upgraded_path = tmp_path / "config" / "upgraded.json"
    write_ai_server_config(loaded, upgraded_path)
    upgraded = json.loads(upgraded_path.read_text(encoding="utf-8"))
    assert upgraded["schema_version"] == 3
    assert "research" not in upgraded
    assert upgraded["providers"] == {}
    assert upgraded["active_provider_id"] == "nvidia-nim"
    assert upgraded["provider_models"] == {
        "nvidia-nim": "nvidia/example",
        "vercel-ai-gateway": "google/custom",
    }


def test_v1_read_keeps_builtin_default_selection(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "active_provider_id": "vercel-ai-gateway",
                "provider_models": {},
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_ai_server_config(path)
    assert loaded is not None
    assert loaded.provider_models == {
        "vercel-ai-gateway": VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    }


def test_unsupported_or_missing_config_version_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    for schema_version in (999, 4, "2", "3", None, True, 3.0):
        path.write_text(
            json.dumps(
                {
                    "schema_version": schema_version,
                    "active_provider_id": "vercel-ai-gateway",
                    "provider_models": {},
                    "updated_at_ms": 5,
                }
            ),
            encoding="utf-8",
        )
        with pytest.raises(AiConfigurationError, match="version is unsupported"):
            load_ai_server_config(path)


@pytest.mark.parametrize(
    "providers",
    [
        {"opencode-go": {"name": "OpenCode Go"}},
        {
            "opencode-go": {
                "name": "OpenCode Go",
                "protocol": "openai-chat-completions",
                "base_url": "https://opencode.ai/zen/go/v1",
                "credential_env": "OPENCODE_API_KEY",
                "models": {
                    DECLARED_MODEL_ID: {
                        "name": "DeepSeek V4 Flash Vision Exp",
                        "capabilities": ["vision_input"],
                        "extra": True,
                    }
                },
            }
        },
        {
            "opencode-go": {
                "name": "OpenCode Go",
                "protocol": "openai-chat-completions",
                "base_url": "http://opencode.ai/zen/go/v1",
                "credential_env": "OPENCODE_API_KEY",
                "models": {
                    DECLARED_MODEL_ID: {
                        "name": "DeepSeek V4 Flash Vision Exp",
                        "capabilities": ["vision_input"],
                    }
                },
            }
        },
        {"nvidia-nim": {"name": "Collision"}},
    ],
)
def test_malformed_declared_records_fail_closed(
    tmp_path: Path,
    providers: dict[str, object],
) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_provider_id": "vercel-ai-gateway",
                "provider_models": {},
                "providers": providers,
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AiConfigurationError):
        load_ai_server_config(path)


def test_declared_active_without_selected_model_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_provider_id": DECLARED_PROVIDER_ID,
                "provider_models": {},
                "providers": {DECLARED_PROVIDER_ID: _serialized_declared_record()},
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AiConfigurationError, match="malformed"):
        load_ai_server_config(path)


def test_selection_for_undeclared_provider_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_provider_id": "vercel-ai-gateway",
                "provider_models": {"opencode-go": DECLARED_MODEL_ID},
                "providers": {},
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AiConfigurationError, match="malformed"):
        load_ai_server_config(path)


def test_oversized_config_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "active_provider_id": "vercel-ai-gateway",
                "provider_models": {},
                "providers": {},
                "updated_at_ms": 5,
                "padding": "x" * (64 * 1024 + 1),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AiConfigurationError):
        load_ai_server_config(path)


def _serialized_declared_record() -> dict[str, object]:
    return {
        "name": "OpenCode Go",
        "protocol": "openai-chat-completions",
        "base_url": "https://opencode.ai/zen/go/v1",
        "credential_env": "OPENCODE_API_KEY",
        "models": {
            DECLARED_MODEL_ID: {
                "name": "DeepSeek V4 Flash Vision Exp",
                "capabilities": ["vision_input"],
            }
        },
    }


def test_malformed_config_is_sanitized(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(AiConfigurationError, match="malformed"):
        load_ai_server_config(path)


def test_symlink_config_path_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    symlink = tmp_path / "config.json"
    symlink.symlink_to(target)

    with pytest.raises(AiConfigurationError, match="symlink"):
        write_ai_server_config(
            AiServerConfig(
                active_provider_id="vercel-ai-gateway",
                provider_models={"vercel-ai-gateway": VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID},
                updated_at_ms=now_ms(),
            ),
            symlink,
        )


def test_safe_test_state_contains_only_category_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "test-state.json"

    write_ai_test_state(
        AiTestState(
            provider_id="vercel-ai-gateway",
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            status="success",
            tested_at_ms=123,
        ),
        path,
    )

    assert load_ai_test_state(path) == AiTestState(
        provider_id="vercel-ai-gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        status="success",
        tested_at_ms=123,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(payload) == [
        "model_id",
        "provider_id",
        "schema_version",
        "status",
        "tested_at_ms",
    ]


def test_safe_status_snapshot_contains_only_config_state_and_identity(tmp_path: Path) -> None:
    path = tmp_path / "status-snapshot.json"

    write_ai_status_snapshot(
        AiStatusSnapshot(
            provider_id="vercel-ai-gateway",
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            configuration_state="configured",
            checked_at_ms=456,
        ),
        path,
    )

    assert load_ai_status_snapshot(path) == AiStatusSnapshot(
        provider_id="vercel-ai-gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        configuration_state="configured",
        checked_at_ms=456,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert sorted(payload) == [
        "checked_at_ms",
        "configuration_state",
        "model_id",
        "provider_id",
        "schema_version",
    ]


def test_safe_status_snapshot_round_trips_fully_unconfigured_state(tmp_path: Path) -> None:
    path = tmp_path / "status-snapshot.json"

    write_ai_status_snapshot(
        AiStatusSnapshot(
            provider_id=None,
            model_id=None,
            configuration_state="not_configured",
            checked_at_ms=456,
        ),
        path,
    )

    assert load_ai_status_snapshot(path) == AiStatusSnapshot(
        provider_id=None,
        model_id=None,
        configuration_state="not_configured",
        checked_at_ms=456,
    )
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["provider_id"] is None
    assert payload["model_id"] is None


@pytest.mark.parametrize(
    "snapshot",
    [
        AiStatusSnapshot(
            provider_id=None,
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            configuration_state="not_configured",
            checked_at_ms=456,
        ),
        AiStatusSnapshot(
            provider_id="vercel-ai-gateway",
            model_id=None,
            configuration_state="not_configured",
            checked_at_ms=456,
        ),
        AiStatusSnapshot(
            provider_id=None,
            model_id=None,
            configuration_state="configured",
            checked_at_ms=456,
        ),
        AiStatusSnapshot(
            provider_id="Unsupported Provider",
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            configuration_state="not_configured",
            checked_at_ms=456,
        ),
        AiStatusSnapshot(
            provider_id="vercel-ai-gateway",
            model_id="bad model",
            configuration_state="not_configured",
            checked_at_ms=456,
        ),
        AiStatusSnapshot(
            provider_id="vercel-ai-gateway",
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            configuration_state="unknown",
            checked_at_ms=456,
        ),
    ],
)
def test_safe_status_snapshot_rejects_invalid_identity_combinations(
    tmp_path: Path,
    snapshot: AiStatusSnapshot,
) -> None:
    with pytest.raises(AiConfigurationError):
        write_ai_status_snapshot(snapshot, tmp_path / "status-snapshot.json")


def test_test_state_and_status_snapshot_accept_declared_identifiers(tmp_path: Path) -> None:
    test_state_path = tmp_path / "test-state.json"
    snapshot_path = tmp_path / "status-snapshot.json"

    write_ai_test_state(
        AiTestState(
            provider_id=DECLARED_PROVIDER_ID,
            model_id=DECLARED_MODEL_ID,
            status="success",
            tested_at_ms=123,
        ),
        test_state_path,
    )
    write_ai_status_snapshot(
        AiStatusSnapshot(
            provider_id=DECLARED_PROVIDER_ID,
            model_id=DECLARED_MODEL_ID,
            configuration_state="configured",
            checked_at_ms=456,
        ),
        snapshot_path,
    )

    state = load_ai_test_state(test_state_path)
    assert state is not None
    assert state.provider_id == DECLARED_PROVIDER_ID
    snapshot = load_ai_status_snapshot(snapshot_path)
    assert snapshot is not None
    assert snapshot.provider_id == DECLARED_PROVIDER_ID


def test_test_state_rejects_unbounded_identifier(tmp_path: Path) -> None:
    with pytest.raises(AiConfigurationError):
        write_ai_test_state(
            AiTestState(
                provider_id="bad provider",
                model_id=DECLARED_MODEL_ID,
                status="success",
                tested_at_ms=123,
            ),
            tmp_path / "test-state.json",
        )


def test_v2_read_is_lossless_and_leaves_research_disabled(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    original = {
        "schema_version": 2,
        "active_provider_id": DECLARED_PROVIDER_ID,
        "provider_models": {DECLARED_PROVIDER_ID: DECLARED_MODEL_ID},
        "providers": {DECLARED_PROVIDER_ID: _serialized_declared_record()},
        "updated_at_ms": 5,
    }
    path.write_text(json.dumps(original), encoding="utf-8")
    before = path.read_bytes()

    loaded = load_ai_server_config(path)

    assert path.read_bytes() == before
    assert loaded is not None
    assert loaded.schema_version == 3
    assert loaded.research is None
    assert loaded.active_provider_id == DECLARED_PROVIDER_ID
    assert loaded.provider_models == {DECLARED_PROVIDER_ID: DECLARED_MODEL_ID}
    assert loaded.providers[DECLARED_PROVIDER_ID] == _declared_record()

    saved = tmp_path / "saved.json"
    write_ai_server_config(loaded, saved)
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["active_provider_id"] == original["active_provider_id"]
    assert payload["provider_models"] == original["provider_models"]
    assert payload["providers"] == original["providers"]
    assert payload["updated_at_ms"] == original["updated_at_ms"]
    assert "research" not in payload


def test_v3_round_trip_preserves_media_settings_and_research(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    research = default_research_configuration(enabled=False)
    write_ai_server_config(replace(_declared_config(), research=research), path)

    loaded = load_ai_server_config(path)
    assert loaded is not None
    assert loaded.schema_version == 3
    assert loaded.active_provider_id == DECLARED_PROVIDER_ID
    assert loaded.provider_models == {DECLARED_PROVIDER_ID: DECLARED_MODEL_ID}
    assert loaded.providers[DECLARED_PROVIDER_ID] == _declared_record()
    assert loaded.research == research
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 3
    assert payload["providers"][DECLARED_PROVIDER_ID]["credential_env"] == "OPENCODE_API_KEY"
    assert payload["research"]["enabled"] is False
    assert payload["research"]["credential_identifier"] == "KRONIKA_RESEARCH_OPENAI_API_KEY"
    assert "sk-" not in path.read_text(encoding="utf-8")
    assert "endpoint" not in payload["research"]


def test_absent_research_section_is_disabled(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 3,
                "active_provider_id": "vercel-ai-gateway",
                "provider_models": {},
                "updated_at_ms": 5,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_ai_server_config(path)

    assert loaded is not None
    assert loaded.research is None
    assert loaded.provider_models["vercel-ai-gateway"] == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID


@pytest.mark.parametrize(
    "mutation",
    ["unknown-field", "secret-field", "endpoint-field", "budget", "tool", "null-section", "top-level"],
)
def test_malformed_research_section_is_rejected(tmp_path: Path, mutation: str) -> None:
    research = serialize_research_configuration(default_research_configuration())
    secret = "sk-test-secret-value"
    if mutation == "unknown-field":
        research["extra"] = True
    elif mutation == "secret-field":
        research["api_key"] = secret
    elif mutation == "endpoint-field":
        research["endpoint"] = "https://example.invalid/v1"
    elif mutation == "budget":
        research["daily_budget_usd_micros"] = 10_000_001
    elif mutation == "tool":
        research["search"]["tool_allowlist"] = ["web_search", "code_interpreter"]
    elif mutation == "null-section":
        research = None
    document = {
        "schema_version": 3,
        "active_provider_id": "vercel-ai-gateway",
        "provider_models": {},
        "updated_at_ms": 5,
        "research": research,
    }
    if mutation == "top-level":
        document["unexpected"] = True
    path = tmp_path / "ai.json"
    path.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(AiConfigurationError, match="malformed") as caught:
        load_ai_server_config(path)

    assert secret not in str(caught.value)


def test_snapshot_revision_tracks_content_and_absence(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    assert load_ai_server_config_snapshot(path).revision == "absent"

    write_ai_server_config(_declared_config(), path)
    first = load_ai_server_config_snapshot(path)
    assert first.revision != "absent"
    assert first.config == _declared_config()

    updated = mutate_ai_server_config(
        path,
        lambda current: replace(current, active_provider_id="vercel-ai-gateway"),
        expected_revision=first.revision,
    )
    second = load_ai_server_config_snapshot(path)
    assert second.revision != first.revision
    assert second.config is not None
    assert second.config.active_provider_id == "vercel-ai-gateway"
    assert second.config.updated_at_ms > first.config.updated_at_ms
    assert updated.updated_at_ms == second.config.updated_at_ms


def test_direct_write_is_creation_only_and_cas_is_strict(tmp_path: Path) -> None:
    path = tmp_path / "ai.json"
    write_ai_server_config(_declared_config(), path)
    with pytest.raises(AiConfigConflictError):
        write_ai_server_config(_declared_config(), path)

    revision = load_ai_server_config_snapshot(path).revision
    with pytest.raises(AiConfigConflictError):
        mutate_ai_server_config(path, lambda current: current, expected_revision="deadbeef")

    # A fresh no-op with the correct revision succeeds without changing bytes.
    before = path.read_bytes()
    same = mutate_ai_server_config(
        path,
        lambda current: current,
        expected_revision=revision,
    )
    assert path.read_bytes() == before
    assert load_ai_server_config_snapshot(path).revision == revision
    assert same.updated_at_ms == load_ai_server_config(path).updated_at_ms


def test_research_save_preserves_media_and_media_save_preserves_research(
    tmp_path: Path,
) -> None:
    path = tmp_path / "ai.json"
    research = default_research_configuration(enabled=False)
    write_ai_server_config(replace(_declared_config(), research=research), path)

    revision = load_ai_server_config_snapshot(path).revision
    mutate_ai_server_config(
        path,
        lambda current: replace(
            current,
            research=replace(current.research, model_id="gpt-5.6-luna"),
        ),
        expected_revision=revision,
    )
    after_research = load_ai_server_config(path)
    assert after_research is not None
    assert after_research.providers == _declared_config().providers
    assert after_research.provider_models == _declared_config().provider_models
    assert after_research.active_provider_id == _declared_config().active_provider_id
    assert after_research.research is not None
    assert after_research.research.model_id == "gpt-5.6-luna"

    revision = load_ai_server_config_snapshot(path).revision
    mutate_ai_server_config(
        path,
        lambda current: replace(current, updated_at_ms=1234),
        expected_revision=revision,
    )
    after_media = load_ai_server_config(path)
    assert after_media is not None
    assert after_media.research == after_research.research
    assert after_media.providers == after_research.providers
