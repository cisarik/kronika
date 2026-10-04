"""Server AI provider registry and composition boundary."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable
from typing import Mapping

from kronika.application.media_suggestion import (
    MediaSuggestionProviderUnavailableError,
    SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE,
)
from kronika.configuration import KronikaSettings
from kronika.infrastructure.ai.configuration import (
    AiConfigurationError,
    AiServerConfig,
    AiStatusSnapshot,
    AiTestState,
    default_ai_config_path,
    default_ai_status_snapshot_path,
    default_ai_test_state_path,
    load_ai_server_config,
    load_ai_status_snapshot,
    load_ai_test_state,
    validate_model_id,
    validate_provider_id,
)
from kronika.infrastructure.ai.constants import (
    BUILTIN_PROVIDER_IDS,
    DEFAULT_PROVIDER_ID,
)
from kronika.infrastructure.ai.credentials import (
    NVIDIA_API_KEY_ENVIRONMENT_NAME,
    load_ai_credential,
    load_nvidia_api_credential,
)
from kronika.infrastructure.ai.nvidia_nim import JsonTransport, NvidiaNimMediaSuggestionProvider
from kronika.infrastructure.ai.openai_chat_completions import (
    OpenAiChatCompletionsMediaSuggestionProvider,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderModel,
    AiProviderRecord,
    OPENAI_CHAT_COMPLETIONS_PROTOCOL,
    builtin_provider_records,
)


@dataclass(frozen=True, slots=True)
class AiProviderDefinition:
    provider_id: str
    display_name: str
    protocol: str
    base_url: str
    credential_environment_name: str
    source: str
    default_model_id: str
    models: tuple[AiProviderModel, ...]
    builtin: bool


@dataclass(frozen=True, slots=True)
class ResolvedAiProvider:
    provider_id: str | None
    display_name: str | None
    model_id: str | None
    source: str
    credential_environment_name: str | None
    credential_available: bool
    provider: object | None
    last_test: AiTestState | None
    last_status: AiStatusSnapshot | None
    config_path: Path
    test_state_path: Path
    status_snapshot_path: Path
    protocol: str | None = None
    base_url: str | None = None
    provider_source: str | None = None
    models: tuple[AiProviderModel, ...] = ()

    @property
    def configured(self) -> bool:
        return self.provider is not None

    def capabilities_for(self, model_id: str) -> tuple[str, ...]:
        for model in self.models:
            if model.model_id == model_id:
                return model.capabilities
        return ()


@dataclass(frozen=True, slots=True)
class AiProviderPersistedStatus:
    last_test: AiTestState | None
    last_status: AiStatusSnapshot | None


def _definition_from_record(record: AiProviderRecord, *, builtin: bool) -> AiProviderDefinition:
    return AiProviderDefinition(
        provider_id=record.provider_id,
        display_name=record.display_name,
        protocol=record.protocol,
        base_url=record.base_url,
        credential_environment_name=record.credential_env,
        source=record.source,
        default_model_id=record.models[0].model_id,
        models=record.models,
        builtin=builtin,
    )


def _builtin_definitions() -> dict[str, AiProviderDefinition]:
    return {
        provider_id: _definition_from_record(record, builtin=True)
        for provider_id, record in builtin_provider_records().items()
    }


PROVIDER_DEFINITIONS = _builtin_definitions()


def provider_definitions(config: AiServerConfig | None) -> dict[str, AiProviderDefinition]:
    """Merge built-in definitions with one config's declared records."""
    definitions = _builtin_definitions()
    if config is not None:
        for provider_id, record in config.providers.items():
            definitions[provider_id] = _definition_from_record(record, builtin=False)
    return definitions


def resolve_ai_provider(
    settings: KronikaSettings,
    *,
    environ: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    transport: JsonTransport | None = None,
) -> ResolvedAiProvider:
    """Resolve the active server AI provider and instantiate it when credentialed."""
    source = os.environ if environ is None else environ
    resolved_config_path = config_path or default_ai_config_path(source)
    persisted: AiServerConfig | None = None
    provider_id: str | None = None
    model_id: str | None = None
    configuration_source = "unconfigured"
    if settings.ai_provider_id is not None:
        provider_id = validate_provider_id(settings.ai_provider_id)
        if settings.ai_model_id is not None:
            model_id = validate_model_id(settings.ai_model_id)
        configuration_source = "environment"
        if provider_id not in BUILTIN_PROVIDER_IDS:
            persisted = load_ai_server_config(resolved_config_path)
    else:
        persisted = load_ai_server_config(resolved_config_path)
        if persisted is not None:
            provider_id = persisted.active_provider_id
            model_id = persisted.provider_models.get(provider_id)
            configuration_source = "server config"
        elif source.get(NVIDIA_API_KEY_ENVIRONMENT_NAME, "").strip():
            provider_id = DEFAULT_PROVIDER_ID
            configuration_source = "legacy compatibility"
    definition = None
    credential_available = False
    provider = None
    if provider_id is not None:
        definition = provider_definitions(persisted).get(provider_id)
        if definition is None:
            raise AiConfigurationError("AI provider is not supported.")
        if model_id is None:
            model_id = definition.default_model_id
        model_id = validate_model_id(model_id)
        provider = _build_provider(definition, model_id, source, transport=transport)
        credential_available = provider is not None
    test_state_path = default_ai_test_state_path(resolved_config_path)
    status_snapshot_path = default_ai_status_snapshot_path(resolved_config_path)
    last_test = None
    last_status = None
    if provider_id is not None and model_id is not None:
        last_test = _load_matching_test_state(test_state_path, provider_id=provider_id, model_id=model_id)
        last_status = _load_matching_status_snapshot(
            status_snapshot_path,
            provider_id=provider_id,
            model_id=model_id,
        )
    return ResolvedAiProvider(
        provider_id=provider_id,
        display_name=None if definition is None else definition.display_name,
        model_id=model_id,
        source=configuration_source,
        credential_environment_name=None if definition is None else definition.credential_environment_name,
        credential_available=credential_available,
        provider=provider,
        last_test=last_test,
        last_status=last_status,
        config_path=resolved_config_path,
        test_state_path=test_state_path,
        status_snapshot_path=status_snapshot_path,
        protocol=None if definition is None else definition.protocol,
        base_url=None if definition is None else definition.base_url,
        provider_source=None if definition is None else definition.source,
        models=() if definition is None else definition.models,
    )


class DynamicAiProviderResolver:
    """Resolve the active provider from current state on every call."""

    def __init__(
        self,
        settings: KronikaSettings,
        *,
        environ: Mapping[str, str] | None = None,
        config_path: Path | None = None,
        transport: JsonTransport | None = None,
    ) -> None:
        self._settings = settings
        self._environ = environ
        self._config_path = config_path
        self._transport = transport

    def resolve(self) -> ResolvedAiProvider:
        """Re-read the persisted config and environment for one resolution."""
        return resolve_ai_provider(
            self._settings,
            environ=self._environ,
            config_path=self._config_path,
            transport=self._transport,
        )


class LazyResolvedAiProvider:
    """Delegate one provider operation to a per-operation dynamic resolution."""

    def __init__(self, resolver: DynamicAiProviderResolver) -> None:
        self._resolver = resolver

    def suggest(self, request: object) -> object:
        return self._require_provider().suggest(request)

    def test_connection(self) -> None:
        self._require_provider().test_connection()

    def probe_vision(self, *, prompt: str, image_png: bytes) -> str:
        return self._require_provider().probe_vision(
            prompt=prompt,
            image_png=image_png,
        )

    def _require_provider(self) -> object:
        resolved = self._resolver.resolve()
        provider = resolved.provider
        if provider is None:
            raise MediaSuggestionProviderUnavailableError(
                SUGGESTION_PROVIDER_UNAVAILABLE_MESSAGE
            )
        return provider

    def __repr__(self) -> str:
        return "LazyResolvedAiProvider(<redacted>)"


def ai_provider_persisted_status_reader(
    *,
    provider_id: str | None,
    model_id: str | None,
    test_state_path: Path,
    status_snapshot_path: Path,
) -> Callable[[], AiProviderPersistedStatus]:
    """Build a network-free reader for current sanitized AI status state."""

    def read_status() -> AiProviderPersistedStatus:
        if provider_id is None or model_id is None:
            return AiProviderPersistedStatus(last_test=None, last_status=None)
        return AiProviderPersistedStatus(
            last_test=_load_matching_test_state(
                test_state_path,
                provider_id=provider_id,
                model_id=model_id,
            ),
            last_status=_load_matching_status_snapshot(
                status_snapshot_path,
                provider_id=provider_id,
                model_id=model_id,
            ),
        )

    return read_status


def _build_provider(
    definition: AiProviderDefinition,
    model_id: str,
    environ: Mapping[str, str],
    *,
    transport: JsonTransport | None,
) -> object | None:
    if definition.provider_id == DEFAULT_PROVIDER_ID:
        credential = load_nvidia_api_credential(environ)
        if credential is None:
            return None
        return NvidiaNimMediaSuggestionProvider(credential, transport=transport, model_id=model_id)
    if definition.protocol == OPENAI_CHAT_COMPLETIONS_PROTOCOL:
        credential = load_ai_credential(definition.credential_environment_name, environ)
        if credential is None:
            return None
        return OpenAiChatCompletionsMediaSuggestionProvider(
            credential,
            base_url=definition.base_url,
            provider_id=definition.provider_id,
            model_id=model_id,
            transport=transport,
        )
    raise AiConfigurationError("AI provider protocol is not supported.")


def _load_matching_test_state(
    path: Path,
    *,
    provider_id: str,
    model_id: str,
) -> AiTestState | None:
    try:
        state = load_ai_test_state(path)
    except AiConfigurationError:
        return None
    if state is None:
        return None
    if state.provider_id != provider_id or state.model_id != model_id:
        return None
    return state


def _load_matching_status_snapshot(
    path: Path,
    *,
    provider_id: str,
    model_id: str,
) -> AiStatusSnapshot | None:
    try:
        snapshot = load_ai_status_snapshot(path)
    except AiConfigurationError:
        return None
    if snapshot is None:
        return None
    if snapshot.provider_id != provider_id or snapshot.model_id != model_id:
        return None
    return snapshot
