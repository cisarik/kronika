"""Unit tests for declarative non-secret provider record validation."""

from __future__ import annotations

from typing import Any

import pytest

from kronika.infrastructure.ai.constants import (
    DEFAULT_MODEL_ID,
    NVIDIA_CHAT_COMPLETIONS_URL,
    VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderRecordError,
    MAX_DECLARED_MODELS_PER_PROVIDER,
    MAX_DECLARED_PROVIDERS,
    PROVIDER_MODEL_CAPABILITIES,
    builtin_default_model_id,
    builtin_provider_records,
    parse_declared_provider_record,
    serialize_declared_provider_record,
    validate_credential_environment_name,
    validate_declared_base_url,
    validate_declared_capabilities,
    validate_declared_provider_map,
    validate_model_identifier,
    validate_provider_display_name,
    validate_provider_identifier,
)


def _model_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "DeepSeek V4 Flash Vision Exp",
        "capabilities": ["vision_input"],
    }
    payload.update(overrides)
    return payload


def _record_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": "OpenCode Go",
        "protocol": "openai-chat-completions",
        "base_url": "https://opencode.ai/zen/go/v1",
        "credential_env": "OPENCODE_API_KEY",
        "models": {"deepseek-v4-flash-vision-exp": _model_payload()},
    }
    payload.update(overrides)
    return payload


def _parse(**overrides: Any):
    return parse_declared_provider_record("opencode-go", _record_payload(**overrides))


def test_declared_record_round_trips_without_secret_values() -> None:
    record = _parse()

    assert record.provider_id == "opencode-go"
    assert record.display_name == "OpenCode Go"
    assert record.protocol == "openai-chat-completions"
    assert record.base_url == "https://opencode.ai/zen/go/v1"
    assert record.source == "declared"
    assert record.credential_env == "OPENCODE_API_KEY"
    assert len(record.models) == 1
    assert record.models[0].model_id == "deepseek-v4-flash-vision-exp"
    assert record.models[0].capabilities == ("vision_input",)
    serialized = serialize_declared_provider_record(record)
    assert serialized["credential_env"] == "OPENCODE_API_KEY"
    assert "OPENCODE_API_KEY" not in serialized["models"]
    assert "OPENCODE_API_KEY" not in serialized["name"]


def test_credential_environment_name_is_a_name_not_a_value() -> None:
    assert validate_credential_environment_name("OPENCODE_API_KEY") == "OPENCODE_API_KEY"

    with pytest.raises(AiProviderRecordError):
        validate_credential_environment_name("sk-live-0123456789")


@pytest.mark.parametrize(
    "provider_id",
    ["opencode-go", "a", "a1", "my.provider_2", "x" * 64],
)
def test_provider_identifier_accepts_bounded_ids(provider_id: str) -> None:
    assert validate_provider_identifier(provider_id) == provider_id


@pytest.mark.parametrize(
    "provider_id",
    ["", "-leading", ".dot", "_under", "Upper", "has space", "x" * 65, "sk/ash", None, 1],
)
def test_provider_identifier_rejects_invalid_ids(provider_id: object) -> None:
    with pytest.raises(AiProviderRecordError):
        validate_provider_identifier(provider_id)


@pytest.mark.parametrize("name", ["A", "NVIDIA NIM", "x" * 80])
def test_display_name_accepts_bounded_names(name: str) -> None:
    assert validate_provider_display_name(name) == name


@pytest.mark.parametrize("name", ["", " ", "x" * 81, "bad\x07name", "line\nbreak", None])
def test_display_name_rejects_invalid_names(name: object) -> None:
    with pytest.raises(AiProviderRecordError):
        validate_provider_display_name(name)


@pytest.mark.parametrize(
    "model_id",
    ["", "has space", "x" * 121, None],
)
def test_model_identifier_rejects_invalid_ids(model_id: object) -> None:
    with pytest.raises(AiProviderRecordError):
        validate_model_identifier(model_id)


def test_protocol_allowlist_rejects_other_protocols() -> None:
    assert _parse().protocol == "openai-chat-completions"

    for protocol in ("nvidia-nim", "openai-responses", "anthropic-messages", ""):
        with pytest.raises(AiProviderRecordError):
            _parse(protocol=protocol)


@pytest.mark.parametrize(
    "base_url",
    [
        "http://opencode.ai/zen/go/v1",
        "file:///tmp/provider",
        "unix:///tmp/provider",
        "https://user:pass@opencode.ai/zen/go/v1",
        "https://user@opencode.ai/zen/go/v1",
        "https://opencode.ai:8443/zen/go/v1",
        "https://opencode.ai/zen/go/v1?query=1",
        "https://opencode.ai/zen/go/v1#fragment",
        "https://opencode.ai/zen/go/v1?",
        "https://opencode.ai/zen/go/v1#",
        "https://opencode.ai/zen/go/v1/",
        "https://localhost/zen/go/v1",
        "https://api.localhost/zen/go/v1",
        "https://127.0.0.1/zen/go/v1",
        "https://127.1.2.3/zen/go/v1",
        "https://[::1]/zen/go/v1",
        "https://opencode.ai/zen/../go/v1",
        "https://opencode.ai//zen/go/v1",
        "https:///zen/go/v1",
        "https://exa mple.com/v1",
    ],
)
def test_declared_base_url_policy_rejects_unsafe_urls(base_url: str) -> None:
    with pytest.raises(AiProviderRecordError):
        validate_declared_base_url(base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://opencode.ai/zen/go/v1",
        "https://ai-gateway.vercel.sh/v1",
        "https://example.com",
        "https://example.com/v1",
    ],
)
def test_declared_base_url_policy_accepts_https_dns_urls(base_url: str) -> None:
    assert validate_declared_base_url(base_url) == base_url


def test_credential_environment_name_pattern_and_reserved_names() -> None:
    for name in ("OPENCODE_API_KEY", "A", "A1_B2", "X" * 64):
        assert validate_credential_environment_name(name) == name

    for name in (
        "opencode_api_key",
        "1KEY",
        "OPENCODE-API-KEY",
        "OPENCODE API KEY",
        "X" * 65,
        "Authorization",
        "Cookie",
        "Content-Type",
        "Host",
        "User-Agent",
        None,
    ):
        with pytest.raises(AiProviderRecordError):
            validate_credential_environment_name(name)


def test_capability_allowlist_covers_spec_names() -> None:
    assert validate_declared_capabilities(sorted(PROVIDER_MODEL_CAPABILITIES)) == tuple(
        sorted(PROVIDER_MODEL_CAPABILITIES)
    )

    with pytest.raises(AiProviderRecordError):
        validate_declared_capabilities(["teleportation"])
    with pytest.raises(AiProviderRecordError):
        validate_declared_capabilities("vision_input")
    with pytest.raises(AiProviderRecordError):
        validate_declared_capabilities([1])


def test_capabilities_are_deduplicated_and_canonicalized() -> None:
    assert validate_declared_capabilities(["vision_input", "vision_input"]) == (
        "vision_input",
    )
    assert validate_declared_capabilities(["vision_input", "cloud_execution"]) == (
        "cloud_execution",
        "vision_input",
    )


@pytest.mark.parametrize(
    "provider_id",
    ["nvidia-nim", "vercel-ai-gateway"],
)
def test_builtin_ids_cannot_be_declared(provider_id: str) -> None:
    with pytest.raises(AiProviderRecordError):
        parse_declared_provider_record(provider_id, _record_payload())


@pytest.mark.parametrize(
    "payload",
    [
        _record_payload(unknown_key="value"),
        _record_payload(protocol_extra="value"),
    ],
)
def test_unknown_record_keys_are_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(AiProviderRecordError):
        parse_declared_provider_record("opencode-go", payload)


def test_unknown_model_keys_and_missing_keys_are_rejected() -> None:
    with pytest.raises(AiProviderRecordError):
        _parse(models={"model": {**_model_payload(), "extra": True}})

    with pytest.raises(AiProviderRecordError):
        _parse(models={"model": {"name": "Only name"}})

    with pytest.raises(AiProviderRecordError):
        _parse(models={})

    with pytest.raises(AiProviderRecordError):
        _parse(models=[])


def test_model_count_and_provider_count_bounds() -> None:
    too_many_models = {
        f"model-{index}": _model_payload()
        for index in range(MAX_DECLARED_MODELS_PER_PROVIDER + 1)
    }
    with pytest.raises(AiProviderRecordError):
        _parse(models=too_many_models)

    too_many_providers = {
        f"provider-{index}": _record_payload()
        for index in range(MAX_DECLARED_PROVIDERS + 1)
    }
    with pytest.raises(AiProviderRecordError):
        validate_declared_provider_map(too_many_providers)


def test_duplicate_model_identifiers_are_rejected() -> None:
    with pytest.raises(AiProviderRecordError):
        _parse(
            models={
                "deepseek-v4-flash-vision-exp": _model_payload(),
                "deepseek-v4-flash-vision-exp ": _model_payload(),
            }
        )


def test_duplicate_normalized_provider_identifiers_are_rejected() -> None:
    with pytest.raises(AiProviderRecordError):
        validate_declared_provider_map(
            {
                "opencode-go": _record_payload(),
                " opencode-go ": _record_payload(
                    credential_env="SECOND_API_KEY",
                ),
            }
        )


@pytest.mark.parametrize(
    "value",
    [
        "My apiKey provider",
        "api_key holder",
        "Bearer abc123",
        "data:image/png;base64,AAAA",
        "Authorization header",
    ],
)
def test_secret_shaped_record_strings_are_rejected(value: str) -> None:
    with pytest.raises(AiProviderRecordError):
        _parse(name=value)

    with pytest.raises(AiProviderRecordError):
        _parse(
            models={
                "model": {"name": value, "capabilities": []},
            }
        )


def test_declared_provider_map_parses_multiple_records() -> None:
    records = validate_declared_provider_map(
        {
            "opencode-go": _record_payload(),
            "second-provider": _record_payload(
                name="Second",
                credential_env="SECOND_API_KEY",
                models={"model": _model_payload()},
            ),
        }
    )

    assert sorted(records) == ["opencode-go", "second-provider"]
    assert records["second-provider"].credential_env == "SECOND_API_KEY"


def test_builtin_records_use_the_shared_record_type() -> None:
    records = builtin_provider_records()

    assert sorted(records) == ["nvidia-nim", "vercel-ai-gateway"]
    nvidia = records["nvidia-nim"]
    assert nvidia.display_name == "NVIDIA NIM"
    assert nvidia.protocol == "nvidia-nim"
    assert nvidia.base_url == NVIDIA_CHAT_COMPLETIONS_URL
    assert nvidia.credential_env == "NVIDIA_API_KEY"
    assert nvidia.source == "builtin"
    assert nvidia.models[0].model_id == DEFAULT_MODEL_ID
    assert nvidia.models[0].capabilities == ("vision_input",)
    assert builtin_default_model_id("nvidia-nim") == DEFAULT_MODEL_ID

    vercel = records["vercel-ai-gateway"]
    assert vercel.display_name == "Vercel AI Gateway"
    assert vercel.protocol == "openai-chat-completions"
    assert vercel.base_url == "https://ai-gateway.vercel.sh/v1"
    assert vercel.credential_env == "AI_GATEWAY_API_KEY"
    assert vercel.models[0].model_id == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID
    assert vercel.models[0].capabilities == ("vision_input",)
    assert builtin_default_model_id("vercel-ai-gateway") == VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID


def test_builtin_default_model_rejects_unknown_provider() -> None:
    with pytest.raises(AiProviderRecordError):
        builtin_default_model_id("opencode-go")
