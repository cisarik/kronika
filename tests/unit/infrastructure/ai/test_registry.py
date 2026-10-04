"""Unit tests for server AI provider resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.ai.configuration import (
    AiConfigurationError,
    AiServerConfig,
    AiStatusSnapshot,
    default_ai_status_snapshot_path,
    write_ai_status_snapshot,
    write_ai_server_config,
)
from kronika.infrastructure.ai.constants import VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
from kronika.application.media_suggestion import (
    MediaSuggestionProviderUnavailableError,
)
from kronika.infrastructure.ai.openai_chat_completions import (
    OpenAiChatCompletionsMediaSuggestionProvider,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
)
from kronika.infrastructure.ai.registry import (
    PROVIDER_DEFINITIONS,
    DynamicAiProviderResolver,
    LazyResolvedAiProvider,
    ResolvedAiProvider,
    provider_definitions,
    resolve_ai_provider,
)
from kronika.infrastructure.ai.transport import HttpsJsonResponse

DECLARED_PROVIDER_ID = "opencode-go"
DECLARED_MODEL_ID = "deepseek-v4-flash-vision-exp"
DECLARED_CREDENTIAL_ENV = "OPENCODE_API_KEY"


def _settings(tmp_path: Path, **kwargs: object) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
        **kwargs,
    )


def _declared_record() -> AiProviderRecord:
    return AiProviderRecord(
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


def _write_declared_config(config_path: Path) -> None:
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=DECLARED_PROVIDER_ID,
            provider_models={DECLARED_PROVIDER_ID: DECLARED_MODEL_ID},
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: _declared_record()},
        ),
        config_path,
    )


def test_environment_provider_model_override_has_precedence(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    write_ai_server_config(
        AiServerConfig(
            active_provider_id="nvidia-nim",
            provider_models={"nvidia-nim": "nvidia/example"},
            updated_at_ms=1,
        ),
        config_path,
    )

    resolved = resolve_ai_provider(
        _settings(tmp_path, ai_provider_id="vercel-ai-gateway", ai_model_id="google/custom"),
        environ={"AI_GATEWAY_API_KEY": "secret"},
        config_path=config_path,
    )

    assert resolved.provider_id == "vercel-ai-gateway"
    assert resolved.model_id == "google/custom"
    assert resolved.source == "environment"
    assert resolved.configured is True


def test_persisted_config_uses_vercel_default_model(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    write_ai_server_config(
        AiServerConfig(
            active_provider_id="vercel-ai-gateway",
            provider_models={},
            updated_at_ms=1,
        ),
        config_path,
    )

    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={"AI_GATEWAY_API_KEY": "secret"},
        config_path=config_path,
    )

    assert resolved.provider_id == "vercel-ai-gateway"
    assert resolved.model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert resolved.source == "server config"
    assert resolved.configured is True


def test_legacy_nvidia_credential_is_backward_compatible(tmp_path: Path) -> None:
    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={"NVIDIA_API_KEY": "secret"},
        config_path=tmp_path / "missing.json",
    )

    assert resolved.provider_id == "nvidia-nim"
    assert resolved.source == "legacy compatibility"
    assert resolved.configured is True


def test_unconfigured_has_no_fallback_provider_identity(tmp_path: Path) -> None:
    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={},
        config_path=tmp_path / "missing.json",
    )

    assert resolved.provider_id is None
    assert resolved.model_id is None
    assert resolved.display_name is None
    assert resolved.credential_environment_name is None
    assert resolved.source == "unconfigured"
    assert resolved.configured is False


def test_selected_provider_without_credential_preserves_safe_identity(tmp_path: Path) -> None:
    resolved = resolve_ai_provider(
        _settings(tmp_path, ai_provider_id="vercel-ai-gateway", ai_model_id="google/custom"),
        environ={},
        config_path=tmp_path / "missing.json",
    )

    assert resolved.provider_id == "vercel-ai-gateway"
    assert resolved.model_id == "google/custom"
    assert resolved.display_name == "Vercel AI Gateway"
    assert resolved.credential_available is False
    assert resolved.configured is False


def test_status_snapshot_is_loaded_only_for_matching_identity(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    status_path = default_ai_status_snapshot_path(config_path)
    write_ai_status_snapshot(
        AiStatusSnapshot(
            provider_id="vercel-ai-gateway",
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            configuration_state="configured",
            checked_at_ms=123,
        ),
        status_path,
    )

    matching = resolve_ai_provider(
        _settings(tmp_path, ai_provider_id="vercel-ai-gateway"),
        environ={"AI_GATEWAY_API_KEY": "secret"},
        config_path=config_path,
    )
    stale = resolve_ai_provider(
        _settings(tmp_path, ai_provider_id="nvidia-nim"),
        environ={"NVIDIA_API_KEY": "secret"},
        config_path=config_path,
    )

    assert matching.last_status is not None
    assert matching.last_status.checked_at_ms == 123
    assert stale.last_status is None


def test_builtin_definitions_remain_available_for_existing_imports() -> None:
    assert sorted(PROVIDER_DEFINITIONS) == ["nvidia-nim", "vercel-ai-gateway"]
    assert PROVIDER_DEFINITIONS["nvidia-nim"].builtin is True
    assert PROVIDER_DEFINITIONS["vercel-ai-gateway"].source == "builtin"
    builtins = provider_definitions(None)
    assert sorted(builtins) == ["nvidia-nim", "vercel-ai-gateway"]


def test_declared_provider_resolves_with_environment_credential(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_declared_config(config_path)

    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
    )

    assert resolved.provider_id == DECLARED_PROVIDER_ID
    assert resolved.display_name == "OpenCode Go"
    assert resolved.model_id == DECLARED_MODEL_ID
    assert resolved.source == "server config"
    assert resolved.provider_source == "declared"
    assert resolved.protocol == "openai-chat-completions"
    assert resolved.base_url == "https://opencode.ai/zen/go/v1"
    assert resolved.credential_environment_name == DECLARED_CREDENTIAL_ENV
    assert resolved.credential_available is True
    assert resolved.configured is True
    assert isinstance(resolved.provider, OpenAiChatCompletionsMediaSuggestionProvider)
    assert resolved.capabilities_for(DECLARED_MODEL_ID) == ("vision_input",)
    assert resolved.capabilities_for("unknown-model") == ()


def test_declared_provider_without_credential_preserves_identity(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_declared_config(config_path)

    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={},
        config_path=config_path,
    )

    assert resolved.provider_id == DECLARED_PROVIDER_ID
    assert resolved.model_id == DECLARED_MODEL_ID
    assert resolved.display_name == "OpenCode Go"
    assert resolved.source == "server config"
    assert resolved.provider_source == "declared"
    assert resolved.credential_available is False
    assert resolved.configured is False
    assert resolved.provider is None


def test_environment_override_selects_declared_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_declared_config(config_path)
    settings = _settings(
        tmp_path,
        ai_provider_id=DECLARED_PROVIDER_ID,
        ai_model_id=DECLARED_MODEL_ID,
    )

    resolved = resolve_ai_provider(
        settings,
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
    )

    assert resolved.provider_id == DECLARED_PROVIDER_ID
    assert resolved.model_id == DECLARED_MODEL_ID
    assert resolved.source == "environment"
    assert resolved.provider_source == "declared"
    assert resolved.configured is True


def test_environment_override_to_undeclared_provider_is_sanitized(tmp_path: Path) -> None:
    settings = _settings(tmp_path).model_copy(
        update={"ai_provider_id": "undeclared-provider"}
    )

    with pytest.raises(AiConfigurationError, match="not supported"):
        resolve_ai_provider(
            settings,
            environ={},
            config_path=tmp_path / "missing.json",
        )


def test_declared_provider_selection_uses_persisted_model_not_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    second_model_id = "deepseek-v4-flash-text"
    record = AiProviderRecord(
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
            AiProviderModel(
                model_id=second_model_id,
                display_name="DeepSeek V4 Flash Text",
                capabilities=(),
            ),
        ),
        source="declared",
    )
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=DECLARED_PROVIDER_ID,
            provider_models={DECLARED_PROVIDER_ID: second_model_id},
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: record},
        ),
        config_path,
    )

    resolved = resolve_ai_provider(
        _settings(tmp_path),
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
    )

    assert resolved.model_id == second_model_id
    assert resolved.capabilities_for(second_model_id) == ()
    assert resolved.capabilities_for(DECLARED_MODEL_ID) == ("vision_input",)


class _SuccessfulTransport:
    def __init__(self) -> None:
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
        return HttpsJsonResponse(
            status_code=200,
            body=b'{"choices":[{"message":{"content":"ok"}}]}',
        )


class _RecordingProvider:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def test_connection(self) -> None:
        self.calls.append("test_connection")

    def probe_vision(self, *, prompt: str, image_png: bytes) -> str:
        self.calls.append("probe_vision")
        return "red"

    def suggest(self, request: object) -> str:
        self.calls.append("suggest")
        return "suggestion"


class _StubResolver:
    def __init__(self, resolved: ResolvedAiProvider) -> None:
        self._resolved = resolved

    def resolve(self) -> ResolvedAiProvider:
        return self._resolved


def _resolved_with_provider(tmp_path: Path, provider: object) -> ResolvedAiProvider:
    return ResolvedAiProvider(
        provider_id=DECLARED_PROVIDER_ID,
        display_name="OpenCode Go",
        model_id=DECLARED_MODEL_ID,
        source="server config",
        credential_environment_name=DECLARED_CREDENTIAL_ENV,
        credential_available=True,
        provider=provider,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
        provider_source="declared",
    )


def test_dynamic_resolver_rereads_rewritten_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    resolver = DynamicAiProviderResolver(
        _settings(tmp_path),
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
    )

    first = resolver.resolve()
    assert first.provider_id is None
    assert first.source == "unconfigured"

    _write_declared_config(config_path)

    second = resolver.resolve()
    assert second.provider_id == DECLARED_PROVIDER_ID
    assert second.model_id == DECLARED_MODEL_ID
    assert second.source == "server config"
    assert second.configured is True


def test_lazy_provider_resolves_the_current_configuration_per_operation(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.json"
    transport = _SuccessfulTransport()
    resolver = DynamicAiProviderResolver(
        _settings(tmp_path),
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
        transport=transport,
    )
    lazy = LazyResolvedAiProvider(resolver)

    with pytest.raises(MediaSuggestionProviderUnavailableError):
        lazy.test_connection()
    assert transport.calls == []

    _write_declared_config(config_path)

    lazy.test_connection()
    assert len(transport.calls) == 1
    assert transport.calls[0][0] == "https://opencode.ai/zen/go/v1/chat/completions"


def test_lazy_provider_delegates_each_operation_to_the_resolved_provider(
    tmp_path: Path,
) -> None:
    provider = _RecordingProvider()
    lazy = LazyResolvedAiProvider(_StubResolver(_resolved_with_provider(tmp_path, provider)))

    lazy.test_connection()
    lazy.probe_vision(prompt="prompt", image_png=b"png")
    lazy.suggest(object())

    assert provider.calls == ["test_connection", "probe_vision", "suggest"]
    assert "synthetic-declared-secret" not in repr(lazy)


def test_lazy_provider_raises_sanitized_unavailable_when_unconfigured(
    tmp_path: Path,
) -> None:
    unconfigured = resolve_ai_provider(
        _settings(tmp_path),
        environ={},
        config_path=tmp_path / "missing.json",
    )
    lazy = LazyResolvedAiProvider(_StubResolver(unconfigured))

    with pytest.raises(MediaSuggestionProviderUnavailableError):
        lazy.test_connection()
    with pytest.raises(MediaSuggestionProviderUnavailableError):
        lazy.suggest(object())
    with pytest.raises(MediaSuggestionProviderUnavailableError):
        lazy.probe_vision(prompt="prompt", image_png=b"png")
