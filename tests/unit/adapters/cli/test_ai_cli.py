"""Unit tests for the server-operated AI CLI."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import stat

import pytest

from kronika.adapters.cli import ai
from kronika.application.media_suggestion import (
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
)
from kronika.configuration import KronikaSettings
from kronika.infrastructure.ai.configuration import (
    AiConfigurationError,
    AiServerConfig,
    load_ai_server_config,
    load_ai_server_config_snapshot,
    load_ai_status_snapshot,
    load_ai_test_state,
    write_ai_server_config,
)
from kronika.infrastructure.ai.constants import (
    DEFAULT_PROVIDER_ID,
    VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
)
from kronika.infrastructure.ai.registry import resolve_ai_provider
from kronika.infrastructure.ai.research_configuration import default_research_configuration
from kronika.infrastructure.ai.transport import HttpsJsonResponse
from kronika.infrastructure.ai.vision_probe import (
    VISION_PROBE_PROMPT,
    default_vision_probe_state_path,
    load_vision_probe_state,
)

DECLARED_PROVIDER_ID = "opencode-go"
DECLARED_MODEL_ID = "deepseek-v4-flash-vision-exp"
DECLARED_CREDENTIAL_ENV = "OPENCODE_API_KEY"


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


def _write_declared_config(config_path: Path, *, active_provider_id: str = "vercel-ai-gateway") -> None:
    provider_models = (
        {DECLARED_PROVIDER_ID: DECLARED_MODEL_ID}
        if active_provider_id == DECLARED_PROVIDER_ID
        else {}
    )
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=active_provider_id,
            provider_models=provider_models,
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: _declared_record()},
        ),
        config_path,
    )


def _provider_add_arguments(config_path: Path) -> list[str]:
    return [
        "--config-path",
        str(config_path),
        "provider",
        "add",
        "--provider-id",
        DECLARED_PROVIDER_ID,
        "--name",
        "OpenCode Go",
        "--protocol",
        "openai-chat-completions",
        "--base-url",
        "https://opencode.ai/zen/go/v1",
        "--credential-env",
        DECLARED_CREDENTIAL_ENV,
        "--model-id",
        DECLARED_MODEL_ID,
        "--capability",
        "vision_input",
        "--yes",
    ]


def test_configure_can_cancel_without_mutation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    lines: list[str] = []

    result = ai.configure_command(
        ai._CliContext(config_path=config_path),
        prompt=lambda _prompt: "cancel",
        output=lines.append,
    )

    assert result == 1
    assert not config_path.exists()
    assert any("No configuration was changed" in line for line in lines)


def test_configure_persists_provider_model_but_no_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    answers = iter(["1", "", "yes"])
    lines: list[str] = []

    result = ai.configure_command(
        ai._CliContext(config_path=config_path),
        prompt=lambda _prompt: next(answers),
        output=lines.append,
    )

    assert result == 0
    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == "vercel-ai-gateway"
    assert config.provider_models["vercel-ai-gateway"] == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    raw = config_path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "API_KEY" not in raw
    assert any("AI_GATEWAY_API_KEY" in line for line in lines)


def test_configure_interactive_refuses_a_stale_save(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    _write_declared_config(config_path)
    answers = iter(["1", "", "yes"])
    lines: list[str] = []

    def prompt(_prompt: str) -> str:
        answer = next(answers)
        if answer == "yes":
            # A concurrent writer changes the file after the revision was
            # captured but before the interactive save commits.
            write_ai_server_config(
                AiServerConfig(
                    active_provider_id="vercel-ai-gateway",
                    provider_models={},
                    updated_at_ms=99,
                ),
                config_path,
                expected_revision=load_ai_server_config_snapshot(config_path).revision,
            )
        return answer

    with pytest.raises(AiConfigurationError):
        ai.configure_command(
            ai._CliContext(config_path=config_path),
            prompt=prompt,
            output=lines.append,
        )
    # The concurrent write survives; the stale interactive save did not clobber it.
    stored = load_ai_server_config(config_path)
    assert stored is not None
    assert stored.updated_at_ms == 99


def test_configure_non_interactive_persists_provider_model_but_no_secret(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    lines: list[str] = []

    result = ai.configure_non_interactive_command(
        ai._CliContext(config_path=config_path),
        provider_id="vercel-ai-gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        output=lines.append,
    )

    assert result == 0
    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == "vercel-ai-gateway"
    assert config.provider_models["vercel-ai-gateway"] == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    raw = config_path.read_text(encoding="utf-8")
    assert "secret" not in raw
    assert "API_KEY" not in raw
    assert any("AI configuration saved" in line for line in lines)


def test_configure_parser_accepts_explicit_non_interactive_provider_model(tmp_path: Path) -> None:
    parser = ai.build_parser()

    args = parser.parse_args(
        [
            "--config-path",
            str(tmp_path / "config.json"),
            "configure",
            "--provider-id",
            "vercel-ai-gateway",
            "--model-id",
            VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            "--yes",
        ]
    )

    assert args.command == "configure"
    assert args.provider_id == "vercel-ai-gateway"
    assert args.model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert args.yes is True


def test_configure_non_interactive_requires_complete_arguments(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "configure",
                "--provider-id",
                "vercel-ai-gateway",
                "--yes",
            ]
        )
        == 2
    )
    assert not config_path.exists()


def test_configure_non_interactive_rejects_invalid_model(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"

    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "configure",
                "--provider-id",
                "vercel-ai-gateway",
                "--model-id",
                "bad model",
                "--yes",
            ]
        )
        == 2
    )
    assert not config_path.exists()


def test_status_fully_unconfigured_exits_zero_without_fabricated_provider(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ai" / "config.json"
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append) == 0

    output = "\n".join(lines)
    assert "Active provider: none" in output
    assert "Model: none" in output
    assert "Configuration source: unconfigured" in output
    assert "Credential available to this process: no" in output
    assert "Analysis state: not configured" in output
    assert "Last connection test: not tested" in output
    assert "Vercel" not in output
    assert "NVIDIA" not in output
    snapshot = load_ai_status_snapshot(tmp_path / "ai" / "status-snapshot.json")
    assert snapshot is not None
    assert snapshot.provider_id is None
    assert snapshot.model_id is None
    assert snapshot.configuration_state == "not_configured"


def test_status_no_write_parser_behavior(tmp_path: Path) -> None:
    parser = ai.build_parser()

    args = parser.parse_args(
        [
            "--config-path",
            str(tmp_path / "config.json"),
            "status",
            "--no-write",
        ]
    )

    assert args.command == "status"
    assert args.no_write is True


def test_status_no_write_fully_unconfigured_exits_zero_without_writes(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "missing" / "config.json"
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append, write_snapshot=False) == 0

    output = "\n".join(lines)
    assert "Active provider: none" in output
    assert "Model: none" in output
    assert "Analysis state: not configured" in output
    assert not config_path.parent.exists()


def test_status_no_write_does_not_modify_existing_snapshot(tmp_path: Path) -> None:
    config_path = tmp_path / "ai" / "config.json"
    snapshot_path = tmp_path / "ai" / "status-snapshot.json"
    snapshot_path.parent.mkdir()
    snapshot_path.write_text('{"existing":true}\n', encoding="utf-8")
    before = snapshot_path.stat().st_mtime_ns

    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append, write_snapshot=False) == 0

    assert snapshot_path.read_text(encoding="utf-8") == '{"existing":true}\n'
    assert snapshot_path.stat().st_mtime_ns == before


def test_status_no_write_works_with_unwritable_state_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "ai" / "config.json"
    config_path.parent.mkdir()
    config_path.parent.chmod(stat.S_IREAD | stat.S_IEXEC)

    try:
        lines: list[str] = []

        assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append, write_snapshot=False) == 0
    finally:
        config_path.parent.chmod(stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)


def test_status_no_write_main_exits_zero_when_fully_unconfigured(tmp_path: Path) -> None:
    config_path = tmp_path / "missing" / "config.json"

    assert ai.main(["--config-path", str(config_path), "status", "--no-write"]) == 0

    assert not config_path.parent.exists()


def test_status_uses_resolver_without_provider_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Provider:
        def test_connection(self) -> None:
            raise AssertionError("status must not test provider connections")

    resolved = ai.ResolvedAiProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        source="server config",
        credential_environment_name="AI_GATEWAY_API_KEY",
        credential_available=True,
        provider=_Provider(),
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=tmp_path / "config.json"), output=lines.append) == 0

    output = "\n".join(lines)
    assert "AI status" in output
    assert "Credential available to this process: yes" in output
    assert "secret" not in output
    snapshot = load_ai_status_snapshot(tmp_path / "status-snapshot.json")
    assert snapshot is not None
    assert snapshot.provider_id == "vercel-ai-gateway"
    assert snapshot.model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert snapshot.configuration_state == "configured"
    assert not (tmp_path / "test-state.json").exists()


def test_status_selected_provider_without_credential_writes_selected_identity(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resolved = ai.ResolvedAiProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        source="server config",
        credential_environment_name="AI_GATEWAY_API_KEY",
        credential_available=False,
        provider=None,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=tmp_path / "config.json"), output=lines.append) == 0

    snapshot = load_ai_status_snapshot(tmp_path / "status-snapshot.json")
    assert snapshot is not None
    assert snapshot.provider_id == "vercel-ai-gateway"
    assert snapshot.model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert snapshot.configuration_state == "not_configured"


def test_test_command_performs_one_text_only_provider_request(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _Provider:
        calls = 0

        def test_connection(self) -> None:
            self.calls += 1

    provider = _Provider()
    resolved = ai.ResolvedAiProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        source="server config",
        credential_environment_name="AI_GATEWAY_API_KEY",
        credential_available=True,
        provider=provider,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    assert ai.test_command(ai._CliContext(config_path=tmp_path / "config.json"), output=lines.append) == 0

    assert provider.calls == 1
    state = load_ai_test_state(tmp_path / "test-state.json")
    assert state is not None
    assert state.status == "success"
    assert "AI test: success" in "\n".join(lines)


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (MediaSuggestionProviderAuthError("raw secret"), "authentication_failed"),
        (MediaSuggestionProviderRateLimitedError("raw quota"), "rate_limited_or_quota_exhausted"),
        (MediaSuggestionProviderModelUnavailableError("raw model"), "model_unavailable"),
        (MediaSuggestionProviderUnavailableError("raw network"), "provider_unreachable"),
        (MediaSuggestionProviderInvalidResponseError("raw provider response"), "invalid_response"),
        (MediaSuggestionProviderFailedError("raw failure"), "provider_error"),
    ],
)
def test_test_command_categorizes_safe_failures(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    error: Exception,
    category: str,
) -> None:
    class _Provider:
        def test_connection(self) -> None:
            raise error

    resolved = ai.ResolvedAiProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        source="server config",
        credential_environment_name="AI_GATEWAY_API_KEY",
        credential_available=True,
        provider=_Provider(),
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    assert ai.test_command(ai._CliContext(config_path=tmp_path / "config.json"), output=lines.append) == 2

    assert load_ai_test_state(tmp_path / "test-state.json").status == category  # type: ignore[union-attr]
    raw_state = (tmp_path / "test-state.json").read_text(encoding="utf-8")
    assert category in raw_state
    assert "raw" not in raw_state
    output = "\n".join(lines)
    assert category in output
    assert "raw" not in output


def test_still_frame_smoke_requires_confirmation(tmp_path: Path) -> None:
    assert (
        ai.main(
            [
                "--config-path",
                str(tmp_path / "config.json"),
                "still-frame-smoke",
                "--image",
                str(tmp_path / "a.jpg"),
            ]
        )
        == 2
    )


def test_still_frame_smoke_performs_one_suggest_without_persistence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from PIL import Image

    from kronika.application.media_suggestion import MediaSuggestion, PROMPT_VERSION

    image_path = tmp_path / "frame.jpg"
    Image.new("RGB", (48, 32), (12, 34, 56)).save(image_path, format="JPEG")

    class _Provider:
        calls = 0
        last_request = None

        def suggest(self, request):  # noqa: ANN001
            self.calls += 1
            self.last_request = request
            return MediaSuggestion(
                title="Synthetic smoke",
                description="Bounded still-frame smoke suggestion.",
                collection="Smoke",
                tags=("synthetic",),
                suggested_filename="still-frame-smoke.jpg",
                confidence=0.5,
                evidence=("flat color frame",),
                uncertainties=("synthetic input",),
                provider_id=DEFAULT_PROVIDER_ID,
                model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
                prompt_version=PROMPT_VERSION,
            )

        def test_connection(self) -> None:
            raise AssertionError("text-only test must not run")

    provider = _Provider()
    resolved = ai.ResolvedAiProvider(
        provider_id=DEFAULT_PROVIDER_ID,
        display_name="NVIDIA NIM",
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        source="server config",
        credential_environment_name="NVIDIA_API_KEY",
        credential_available=True,
        provider=provider,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.still_frame_smoke_command(
        ai._CliContext(config_path=tmp_path / "config.json"),
        image_paths=(image_path,),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 0
    assert provider.calls == 1
    assert provider.last_request is not None
    assert len(provider.last_request.representative_frames) == 1
    assert not (tmp_path / "test-state.json").exists()
    output = "\n".join(lines)
    assert "AI still-frame smoke: success" in output
    assert "Sent frames: 1" in output
    assert "secret" not in output.lower()


def test_still_frame_smoke_rejects_non_nvidia_provider(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resolved = ai.ResolvedAiProvider(
        provider_id="vercel-ai-gateway",
        display_name="Vercel AI Gateway",
        model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
        source="server config",
        credential_environment_name="AI_GATEWAY_API_KEY",
        credential_available=True,
        provider=object(),
        last_test=None,
        last_status=None,
        config_path=tmp_path / "config.json",
        test_state_path=tmp_path / "test-state.json",
        status_snapshot_path=tmp_path / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []
    result = ai.still_frame_smoke_command(
        ai._CliContext(config_path=tmp_path / "config.json"),
        image_paths=(tmp_path / "missing.jpg",),
        confirm_cloud_upload=True,
        output=lines.append,
    )
    assert result == 2
    assert "unsupported_provider" in "\n".join(lines)


def test_provider_add_writes_declared_record_without_secret_values(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"

    assert ai.main(_provider_add_arguments(config_path)) == 0

    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == "vercel-ai-gateway"
    assert sorted(config.providers) == [DECLARED_PROVIDER_ID]
    record = config.providers[DECLARED_PROVIDER_ID]
    assert record.display_name == "OpenCode Go"
    assert record.credential_env == DECLARED_CREDENTIAL_ENV
    assert record.models[0].capabilities == ("vision_input",)
    raw = config_path.read_text(encoding="utf-8")
    assert raw.count("API_KEY") == 1
    assert "Authorization" not in raw
    assert "Bearer" not in raw
    assert "data:" not in raw


def test_provider_add_requires_confirmation(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    arguments = _provider_add_arguments(config_path)
    arguments.remove("--yes")

    assert ai.main(arguments) == 2

    assert not config_path.exists()


def test_provider_add_refuses_builtin_identifier(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    arguments = _provider_add_arguments(config_path)
    arguments[arguments.index(DECLARED_PROVIDER_ID)] = "nvidia-nim"

    assert ai.main(arguments) == 2

    assert not config_path.exists()


def test_provider_add_requires_one_name_per_model_id(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    arguments = _provider_add_arguments(config_path)
    arguments.extend(
        [
            "--model-id",
            "second-model",
            "--model-name",
            "Only one name",
        ]
    )

    assert ai.main(arguments) == 2

    assert not config_path.exists()


def test_provider_add_upserts_declared_record(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    assert ai.main(_provider_add_arguments(config_path)) == 0

    lines: list[str] = []
    context = ai._CliContext(config_path=config_path)

    assert (
        ai.provider_add_command(
            context,
            provider_id=DECLARED_PROVIDER_ID,
            name="OpenCode Go Updated",
            protocol="openai-chat-completions",
            base_url="https://opencode.ai/zen/go/v1",
            credential_env=DECLARED_CREDENTIAL_ENV,
            model_ids=(DECLARED_MODEL_ID,),
            model_names=(),
            capabilities=("vision_input",),
            confirmed=True,
            output=lines.append,
        )
        == 0
    )

    config = load_ai_server_config(config_path)
    assert config is not None
    assert sorted(config.providers) == [DECLARED_PROVIDER_ID]
    assert config.providers[DECLARED_PROVIDER_ID].display_name == "OpenCode Go Updated"
    assert any("updated" in line for line in lines)


def test_provider_add_rejects_empty_model_list(tmp_path: Path) -> None:
    with pytest.raises(AiConfigurationError):
        ai.provider_add_command(
            ai._CliContext(config_path=tmp_path / "config.json"),
            provider_id=DECLARED_PROVIDER_ID,
            name="OpenCode Go",
            protocol="openai-chat-completions",
            base_url="https://opencode.ai/zen/go/v1",
            credential_env=DECLARED_CREDENTIAL_ENV,
            model_ids=(),
            model_names=(),
            capabilities=(),
            confirmed=True,
        )


def test_provider_list_reports_sanitized_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    assert ai.main(_provider_add_arguments(config_path)) == 0
    lines: list[str] = []

    result = ai.provider_list_command(
        ai._CliContext(config_path=config_path),
        output=lines.append,
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
    )

    output = "\n".join(lines)
    assert result == 0
    assert f"- {DECLARED_PROVIDER_ID} | OpenCode Go | source=declared" in output
    assert "protocol=openai-chat-completions" in output
    assert "base_url=https://opencode.ai/zen/go/v1" in output
    assert f"credential_env={DECLARED_CREDENTIAL_ENV}" in output
    assert "credential_available=yes" in output
    assert f"models={DECLARED_MODEL_ID}" in output
    assert "- nvidia-nim | NVIDIA NIM | source=builtin" in output
    assert "- vercel-ai-gateway | Vercel AI Gateway | source=builtin" in output
    assert "synthetic-declared-secret" not in output


def test_provider_list_reports_missing_credential(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)
    lines: list[str] = []

    assert (
        ai.provider_list_command(
            ai._CliContext(config_path=config_path),
            output=lines.append,
            environ={},
        )
        == 0
    )

    assert "credential_available=no" in "\n".join(lines)


def test_provider_remove_refuses_builtin_and_active_records(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"

    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "provider",
                "remove",
                "--provider-id",
                "nvidia-nim",
                "--yes",
            ]
        )
        == 2
    )

    _write_declared_config(config_path, active_provider_id=DECLARED_PROVIDER_ID)
    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "provider",
                "remove",
                "--provider-id",
                DECLARED_PROVIDER_ID,
                "--yes",
            ]
        )
        == 2
    )
    config = load_ai_server_config(config_path)
    assert config is not None
    assert sorted(config.providers) == [DECLARED_PROVIDER_ID]


def test_provider_remove_deletes_inactive_declared_record(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)

    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "provider",
                "remove",
                "--provider-id",
                DECLARED_PROVIDER_ID,
                "--yes",
            ]
        )
        == 0
    )

    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.providers == {}
    assert config.active_provider_id == "vercel-ai-gateway"


def test_provider_remove_requires_confirmation(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)

    assert (
        ai.main(
            [
                "--config-path",
                str(config_path),
                "provider",
                "remove",
                "--provider-id",
                DECLARED_PROVIDER_ID,
            ]
        )
        == 2
    )

    config = load_ai_server_config(config_path)
    assert config is not None
    assert sorted(config.providers) == [DECLARED_PROVIDER_ID]


def test_configure_interactive_selects_declared_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)
    answers = iter(["3", "", "yes"])
    lines: list[str] = []

    result = ai.configure_command(
        ai._CliContext(config_path=config_path),
        prompt=lambda _prompt: next(answers),
        output=lines.append,
    )

    assert result == 0
    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == DECLARED_PROVIDER_ID
    assert config.provider_models[DECLARED_PROVIDER_ID] == DECLARED_MODEL_ID
    assert any("declared" in line for line in lines)


def test_configure_interactive_rejects_undeclared_model(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)
    answers = iter(["3", "some-other-model", "yes"])

    with pytest.raises(AiConfigurationError):
        ai.configure_command(
            ai._CliContext(config_path=config_path),
            prompt=lambda _prompt: next(answers),
            output=lambda _line: None,
        )

    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == "vercel-ai-gateway"


def test_configure_non_interactive_accepts_declared_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)
    lines: list[str] = []

    result = ai.configure_non_interactive_command(
        ai._CliContext(config_path=config_path),
        provider_id=DECLARED_PROVIDER_ID,
        model_id=DECLARED_MODEL_ID,
        output=lines.append,
    )

    assert result == 0
    config = load_ai_server_config(config_path)
    assert config is not None
    assert config.active_provider_id == DECLARED_PROVIDER_ID
    assert config.provider_models[DECLARED_PROVIDER_ID] == DECLARED_MODEL_ID
    assert any(f"Required credential environment variable: {DECLARED_CREDENTIAL_ENV}" in line for line in lines)


def test_configure_non_interactive_rejects_undeclared_model(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)

    with pytest.raises(AiConfigurationError):
        ai.configure_non_interactive_command(
            ai._CliContext(config_path=config_path),
            provider_id=DECLARED_PROVIDER_ID,
            model_id="some-other-model",
            output=lambda _line: None,
        )


def test_configure_non_interactive_rejects_undeclared_provider(tmp_path: Path) -> None:
    config_path = tmp_path / "config" / "ai.json"
    _write_declared_config(config_path)

    with pytest.raises(AiConfigurationError):
        ai.configure_non_interactive_command(
            ai._CliContext(config_path=config_path),
            provider_id="undeclared-provider",
            model_id="some-model",
            output=lambda _line: None,
        )


def test_status_reports_declared_provider_source(tmp_path: Path) -> None:
    config_path = tmp_path / "ai" / "config.json"
    _write_declared_config(config_path, active_provider_id=DECLARED_PROVIDER_ID)
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append) == 0

    output = "\n".join(lines)
    assert "Active provider: OpenCode Go" in output
    assert "Provider source: declared" in output
    assert "Credential available to this process: no" in output


def test_status_reports_builtin_provider_source(tmp_path: Path) -> None:
    config_path = tmp_path / "ai" / "config.json"
    write_ai_server_config(
        AiServerConfig(
            active_provider_id="vercel-ai-gateway",
            provider_models={},
            updated_at_ms=1,
        ),
        config_path,
    )
    lines: list[str] = []

    assert ai.status_command(ai._CliContext(config_path=config_path), output=lines.append) == 0

    assert "Provider source: builtin" in "\n".join(lines)


def test_status_unconfigured_reports_no_provider_source(tmp_path: Path) -> None:
    lines: list[str] = []

    assert (
        ai.status_command(
            ai._CliContext(config_path=tmp_path / "missing" / "config.json"),
            output=lines.append,
            write_snapshot=False,
        )
        == 0
    )

    assert "Provider source: none" in "\n".join(lines)


def test_test_command_against_declared_provider_with_synthetic_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "ai" / "config.json"
    _write_declared_config(config_path, active_provider_id=DECLARED_PROVIDER_ID)

    class _Transport:
        def __init__(self) -> None:
            self.calls = 0

        def post_json(
            self,
            url: str,
            *,
            headers: dict[str, str],
            body: bytes,
            max_request_bytes: int,
        ) -> HttpsJsonResponse:
            self.calls += 1
            return HttpsJsonResponse(
                status_code=200,
                body=b'{"choices":[{"message":{"content":"ok"}}]}',
            )

    transport = _Transport()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    resolved = resolve_ai_provider(
        settings,
        environ={DECLARED_CREDENTIAL_ENV: "synthetic-declared-secret"},
        config_path=config_path,
        transport=transport,
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.test_command(ai._CliContext(config_path=config_path), output=lines.append)

    assert result == 0
    assert transport.calls == 1
    state = load_ai_test_state(tmp_path / "ai" / "test-state.json")
    assert state is not None
    assert state.provider_id == DECLARED_PROVIDER_ID
    assert state.model_id == DECLARED_MODEL_ID
    assert state.status == "success"
    assert "AI test: success" in "\n".join(lines)


class _ProbeProvider:
    def __init__(self, *, result: str = "red", error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[str, bytes]] = []

    def probe_vision(self, *, prompt: str, image_png: bytes) -> str:
        self.calls.append((prompt, image_png))
        if self.error is not None:
            raise self.error
        return self.result


def _vision_resolved(
    tmp_path: Path,
    provider: object,
    *,
    capabilities: tuple[str, ...] = ("vision_input",),
) -> ai.ResolvedAiProvider:
    return ai.ResolvedAiProvider(
        provider_id=DECLARED_PROVIDER_ID,
        display_name="OpenCode Go",
        model_id=DECLARED_MODEL_ID,
        source="server config",
        credential_environment_name=DECLARED_CREDENTIAL_ENV,
        credential_available=True,
        provider=provider,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "ai" / "config.json",
        test_state_path=tmp_path / "ai" / "test-state.json",
        status_snapshot_path=tmp_path / "ai" / "status-snapshot.json",
        protocol="openai-chat-completions",
        base_url="https://opencode.ai/zen/go/v1",
        provider_source="declared",
        models=(
            AiProviderModel(
                model_id=DECLARED_MODEL_ID,
                display_name="DeepSeek V4 Flash Vision Exp",
                capabilities=capabilities,
            ),
        ),
    )


def test_vision_probe_requires_confirmation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def _fail(_context: ai._CliContext) -> ai.ResolvedAiProvider:
        raise AssertionError("vision probe must not resolve before confirmation")

    monkeypatch.setattr(ai, "_resolve", _fail)
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=tmp_path / "config.json"),
        confirm_cloud_upload=False,
        output=lines.append,
    )

    assert result == 2
    assert "AI vision probe: confirmation_required" in "\n".join(lines)


def test_vision_probe_success_persists_safe_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ProbeProvider(result="Red.")
    resolved = _vision_resolved(tmp_path, provider)
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=resolved.config_path),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 0
    assert len(provider.calls) == 1
    prompt, image_png = provider.calls[0]
    assert prompt == VISION_PROBE_PROMPT
    assert image_png[:8] == b"\x89PNG\r\n\x1a\n"
    output = "\n".join(lines)
    assert "AI vision probe: success" in output
    assert "Expected color: red" in output
    assert "Observed color: red" in output
    state = load_vision_probe_state(default_vision_probe_state_path(resolved.config_path))
    assert state is not None
    assert state.status == "success"
    assert state.matched is True
    assert state.observed_color == "red"
    assert not (tmp_path / "ai" / ".vision-probe.lock").exists()


def test_vision_probe_mismatch_is_honest_and_bounded(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ProbeProvider(result="Blue.")
    resolved = _vision_resolved(tmp_path, provider)
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=resolved.config_path),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 2
    output = "\n".join(lines)
    assert "AI vision probe: mismatch" in output
    assert "Expected color: red" in output
    assert "Observed color: blue" in output
    state = load_vision_probe_state(default_vision_probe_state_path(resolved.config_path))
    assert state is not None
    assert state.status == "mismatch"
    assert state.matched is False
    assert state.observed_color == "blue"


def test_vision_probe_refuses_model_without_vision_capability(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ProbeProvider()
    resolved = _vision_resolved(tmp_path, provider, capabilities=())
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=resolved.config_path),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 2
    assert "AI vision probe: unsupported_model_capability" in "\n".join(lines)
    assert provider.calls == []


def test_vision_probe_busy_lock_fails_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ProbeProvider()
    resolved = _vision_resolved(tmp_path, provider)
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lock_path = tmp_path / "ai" / ".vision-probe.lock"
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text("", encoding="utf-8")
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=resolved.config_path),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 2
    assert "Another AI provider operation is already running." in "\n".join(lines)
    assert provider.calls == []
    assert lock_path.exists()
    assert not default_vision_probe_state_path(resolved.config_path).exists()


def test_vision_probe_unconfigured_and_missing_credential_are_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    unconfigured = ai.ResolvedAiProvider(
        provider_id=None,
        display_name=None,
        model_id=None,
        source="unconfigured",
        credential_environment_name=None,
        credential_available=False,
        provider=None,
        last_test=None,
        last_status=None,
        config_path=tmp_path / "ai" / "config.json",
        test_state_path=tmp_path / "ai" / "test-state.json",
        status_snapshot_path=tmp_path / "ai" / "status-snapshot.json",
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: unconfigured)
    lines: list[str] = []

    assert (
        ai.vision_probe_command(
            ai._CliContext(config_path=unconfigured.config_path),
            confirm_cloud_upload=True,
            output=lines.append,
        )
        == 2
    )
    assert "AI vision probe: not configured" in "\n".join(lines)

    configured_without_credential = replace(
        _vision_resolved(tmp_path, _ProbeProvider()),
        credential_available=False,
        provider=None,
    )
    monkeypatch.setattr(ai, "_resolve", lambda _context: configured_without_credential)
    lines.clear()

    assert (
        ai.vision_probe_command(
            ai._CliContext(config_path=configured_without_credential.config_path),
            confirm_cloud_upload=True,
            output=lines.append,
        )
        == 2
    )
    output = "\n".join(lines)
    assert "AI vision probe: authentication_failed" in output
    assert "Credential available to this process: no" in output


def test_vision_probe_provider_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    provider = _ProbeProvider(error=MediaSuggestionProviderAuthError("raw provider secret"))
    resolved = _vision_resolved(tmp_path, provider)
    monkeypatch.setattr(ai, "_resolve", lambda _context: resolved)
    lines: list[str] = []

    result = ai.vision_probe_command(
        ai._CliContext(config_path=resolved.config_path),
        confirm_cloud_upload=True,
        output=lines.append,
    )

    assert result == 2
    output = "\n".join(lines)
    assert "AI vision probe: authentication_failed" in output
    assert "raw" not in output
    assert "secret" not in output
    state = load_vision_probe_state(default_vision_probe_state_path(resolved.config_path))
    assert state is not None
    assert state.status == "authentication_failed"
    assert state.observed_color is None


_MEDIA_PROVIDER_ID = "vercel-ai-gateway"
_EXTRA_PROVIDER_ID = "local-media"
_EXTRA_MODEL_ID = "local-vision"
_EXTRA_CREDENTIAL_ENV = "LOCAL_MEDIA_API_KEY"


def _assert_cli_media_and_research(
    config_path: Path,
    *,
    research: object,
    provider_ids: set[str],
) -> None:
    loaded = load_ai_server_config(config_path)
    assert loaded is not None
    assert loaded.research == research
    assert loaded.active_provider_id == _MEDIA_PROVIDER_ID
    assert loaded.provider_models == {_MEDIA_PROVIDER_ID: VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID}
    assert set(loaded.providers) == provider_ids
    assert loaded.providers[DECLARED_PROVIDER_ID] == _declared_record()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if research is None:
        assert "research" not in payload
    else:
        assert "research" in payload


def _exercise_configuration_writers(config_path: Path, *, research: object) -> None:
    original_providers = {DECLARED_PROVIDER_ID}
    context = ai._CliContext(config_path=config_path)

    assert (
        ai.configure_non_interactive_command(
            context,
            provider_id=_MEDIA_PROVIDER_ID,
            model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
            output=lambda _line: None,
        )
        == 0
    )
    _assert_cli_media_and_research(
        config_path,
        research=research,
        provider_ids=original_providers,
    )

    answers = iter(["", "", "yes"])
    assert (
        ai.configure_command(
            context,
            prompt=lambda _prompt: next(answers),
            output=lambda _line: None,
        )
        == 0
    )
    _assert_cli_media_and_research(
        config_path,
        research=research,
        provider_ids=original_providers,
    )

    assert (
        ai.provider_add_command(
            context,
            provider_id=_EXTRA_PROVIDER_ID,
            name="Local Media",
            protocol="openai-chat-completions",
            base_url="https://opencode.ai/zen/go/v1",
            credential_env=_EXTRA_CREDENTIAL_ENV,
            model_ids=(_EXTRA_MODEL_ID,),
            model_names=(),
            capabilities=("vision_input",),
            confirmed=True,
            output=lambda _line: None,
        )
        == 0
    )
    _assert_cli_media_and_research(
        config_path,
        research=research,
        provider_ids=original_providers | {_EXTRA_PROVIDER_ID},
    )

    assert (
        ai.provider_remove_command(
            context,
            provider_id=_EXTRA_PROVIDER_ID,
            confirmed=True,
            output=lambda _line: None,
        )
        == 0
    )
    _assert_cli_media_and_research(
        config_path,
        research=research,
        provider_ids=original_providers,
    )


def test_cli_writers_preserve_research_configuration(tmp_path: Path) -> None:
    research = default_research_configuration(enabled=True)
    config_path = tmp_path / "with-research.json"
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=_MEDIA_PROVIDER_ID,
            provider_models={_MEDIA_PROVIDER_ID: VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID},
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: _declared_record()},
            research=research,
        ),
        config_path,
    )
    stored = load_ai_server_config(config_path)
    assert stored is not None
    assert stored.research == research

    _exercise_configuration_writers(config_path, research=research)

    bare_path = tmp_path / "without-research.json"
    write_ai_server_config(
        AiServerConfig(
            active_provider_id=_MEDIA_PROVIDER_ID,
            provider_models={_MEDIA_PROVIDER_ID: VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID},
            updated_at_ms=1,
            providers={DECLARED_PROVIDER_ID: _declared_record()},
        ),
        bare_path,
    )
    _exercise_configuration_writers(bare_path, research=None)
