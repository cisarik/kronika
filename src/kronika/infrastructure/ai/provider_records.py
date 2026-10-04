"""Declarative non-secret AI provider records and record validation."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping
from urllib.parse import urlsplit

from kronika.infrastructure.ai.constants import (
    BUILTIN_PROVIDER_IDS,
    DEFAULT_MODEL_ID,
    DEFAULT_PROVIDER_ID,
    MAX_MODEL_ID_LENGTH,
    NVIDIA_CHAT_COMPLETIONS_URL,
    VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL,
    VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
    VERCEL_AI_GATEWAY_PROVIDER_ID,
)
from kronika.infrastructure.ai.credentials import (
    NVIDIA_API_KEY_ENVIRONMENT_NAME,
    VERCEL_AI_GATEWAY_API_KEY_ENVIRONMENT_NAME,
)

OPENAI_CHAT_COMPLETIONS_PROTOCOL = "openai-chat-completions"
NVIDIA_NIM_PROTOCOL = "nvidia-nim"
DECLARED_PROTOCOLS = frozenset({OPENAI_CHAT_COMPLETIONS_PROTOCOL})

PROVIDER_MODEL_CAPABILITIES = frozenset(
    {
        "vision_input",
        "video_input",
        "structured_text_output",
        "image_generation",
        "image_editing",
        "reference_image",
        "local_execution",
        "cloud_execution",
    }
)

PROVIDER_SOURCES = frozenset({"builtin", "declared"})

MAX_DECLARED_PROVIDERS = 16
MAX_DECLARED_MODELS_PER_PROVIDER = 64
MAX_AI_CONFIG_BYTES = 64 * 1024
MAX_PROVIDER_DISPLAY_NAME_LENGTH = 80
MAX_HOSTNAME_LENGTH = 253
MAX_DNS_LABEL_LENGTH = 63

_PROVIDER_IDENTIFIER_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_CREDENTIAL_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_RESERVED_TRANSPORT_HEADER_NAMES = frozenset(
    {"Authorization", "Cookie", "Content-Type", "Host", "User-Agent"}
)
_DNS_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9-]+$")
_SECRET_SHAPE_MARKERS = ("apikey", "api_key", "authorization", "bearer ", "data:")
_RECORD_KEYS = frozenset({"name", "protocol", "base_url", "credential_env", "models"})
_MODEL_KEYS = frozenset({"name", "capabilities"})

PROVIDER_IDENTIFIER_INVALID_MESSAGE = "AI provider identifier is invalid."
PROVIDER_DISPLAY_NAME_INVALID_MESSAGE = "AI provider display name is invalid."
PROVIDER_PROTOCOL_UNSUPPORTED_MESSAGE = "AI provider protocol is not supported."
PROVIDER_BASE_URL_INVALID_MESSAGE = "AI provider base URL is invalid."
PROVIDER_CREDENTIAL_ENVIRONMENT_NAME_INVALID_MESSAGE = (
    "AI provider credential environment name is invalid."
)
PROVIDER_RECORD_MALFORMED_MESSAGE = "AI provider record is malformed."
PROVIDER_CAPABILITY_UNSUPPORTED_MESSAGE = "AI provider capability is not supported."
PROVIDER_SECRET_SHAPE_MESSAGE = "AI provider record contains a secret-like value."
PROVIDER_MODEL_INVALID_MESSAGE = "AI model is invalid."


class AiProviderRecordError(ValueError):
    """Sanitized provider-record validation failure."""


@dataclass(frozen=True, slots=True)
class AiProviderModel:
    """One model declared by an AI provider record."""

    model_id: str
    display_name: str
    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AiProviderRecord:
    """One non-secret AI provider definition."""

    provider_id: str
    display_name: str
    protocol: str
    base_url: str
    credential_env: str
    models: tuple[AiProviderModel, ...]
    source: str

    def __post_init__(self) -> None:
        if self.source not in PROVIDER_SOURCES:
            raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)


def validate_provider_identifier(value: object) -> str:
    """Validate one bounded provider identifier."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_IDENTIFIER_INVALID_MESSAGE)
    normalized = value.strip()
    if not _PROVIDER_IDENTIFIER_PATTERN.fullmatch(normalized):
        raise AiProviderRecordError(PROVIDER_IDENTIFIER_INVALID_MESSAGE)
    return normalized


def validate_model_identifier(value: object) -> str:
    """Validate one bounded model identifier."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_MODEL_INVALID_MESSAGE)
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_MODEL_ID_LENGTH:
        raise AiProviderRecordError(PROVIDER_MODEL_INVALID_MESSAGE)
    if any(character.isspace() for character in normalized):
        raise AiProviderRecordError(PROVIDER_MODEL_INVALID_MESSAGE)
    _reject_secret_shapes(normalized)
    return normalized


def validate_provider_display_name(value: object) -> str:
    """Validate one bounded provider or model display name."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_DISPLAY_NAME_INVALID_MESSAGE)
    normalized = value.strip()
    if not normalized or len(normalized) > MAX_PROVIDER_DISPLAY_NAME_LENGTH:
        raise AiProviderRecordError(PROVIDER_DISPLAY_NAME_INVALID_MESSAGE)
    if any(unicodedata.category(character) == "Cc" for character in normalized):
        raise AiProviderRecordError(PROVIDER_DISPLAY_NAME_INVALID_MESSAGE)
    _reject_secret_shapes(normalized)
    return normalized


def validate_credential_environment_name(value: object) -> str:
    """Validate one credential environment-variable name, never a value."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_CREDENTIAL_ENVIRONMENT_NAME_INVALID_MESSAGE)
    normalized = value.strip()
    if not _CREDENTIAL_ENVIRONMENT_NAME_PATTERN.fullmatch(normalized):
        raise AiProviderRecordError(PROVIDER_CREDENTIAL_ENVIRONMENT_NAME_INVALID_MESSAGE)
    if normalized in _RESERVED_TRANSPORT_HEADER_NAMES:
        raise AiProviderRecordError(PROVIDER_CREDENTIAL_ENVIRONMENT_NAME_INVALID_MESSAGE)
    return normalized


def validate_declared_protocol(value: object) -> str:
    """Validate one declared provider protocol."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_PROTOCOL_UNSUPPORTED_MESSAGE)
    normalized = value.strip()
    if normalized not in DECLARED_PROTOCOLS:
        raise AiProviderRecordError(PROVIDER_PROTOCOL_UNSUPPORTED_MESSAGE)
    return normalized


def validate_declared_capabilities(value: object) -> tuple[str, ...]:
    """Validate and canonicalize one declared capability list."""
    if not isinstance(value, (list, tuple)):
        raise AiProviderRecordError(PROVIDER_CAPABILITY_UNSUPPORTED_MESSAGE)
    validated: set[str] = set()
    for capability in value:
        if not isinstance(capability, str):
            raise AiProviderRecordError(PROVIDER_CAPABILITY_UNSUPPORTED_MESSAGE)
        normalized = capability.strip()
        if normalized not in PROVIDER_MODEL_CAPABILITIES:
            raise AiProviderRecordError(PROVIDER_CAPABILITY_UNSUPPORTED_MESSAGE)
        validated.add(normalized)
    return tuple(sorted(validated))


def validate_declared_base_url(value: object) -> str:
    """Validate one declared HTTPS base URL under the declared-record policy."""
    if not isinstance(value, str):
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    normalized = value.strip()
    if not normalized:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    try:
        parsed = urlsplit(normalized)
    except ValueError:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE) from None
    if parsed.scheme != "https":
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    try:
        port = parsed.port
    except ValueError:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE) from None
    if port is not None:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    if "?" in normalized or "#" in normalized:
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    if normalized.endswith("/"):
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    host = parsed.hostname
    if host is None or not _is_dns_name(host) or _is_loopback_host(host):
        raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    path = parsed.path
    if path:
        if not path.startswith("/"):
            raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
        segments = path.split("/")[1:]
        if any(segment in {"", ".", ".."} for segment in segments):
            raise AiProviderRecordError(PROVIDER_BASE_URL_INVALID_MESSAGE)
    _reject_secret_shapes(normalized)
    return normalized


def parse_declared_provider_record(provider_id: object, payload: object) -> AiProviderRecord:
    """Parse one declared provider record, rejecting every unknown shape."""
    validated_id = validate_provider_identifier(provider_id)
    if validated_id in BUILTIN_PROVIDER_IDS:
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    if not isinstance(payload, Mapping) or set(payload.keys()) != _RECORD_KEYS:
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    display_name = validate_provider_display_name(payload["name"])
    protocol = validate_declared_protocol(payload["protocol"])
    base_url = validate_declared_base_url(payload["base_url"])
    credential_env = validate_credential_environment_name(payload["credential_env"])
    models_payload = payload["models"]
    if not isinstance(models_payload, Mapping) or not models_payload:
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    if len(models_payload) > MAX_DECLARED_MODELS_PER_PROVIDER:
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    models: list[AiProviderModel] = []
    seen_model_ids: set[str] = set()
    for model_id, model_payload in models_payload.items():
        validated_model_id = validate_model_identifier(model_id)
        if validated_model_id in seen_model_ids:
            raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
        seen_model_ids.add(validated_model_id)
        if not isinstance(model_payload, Mapping) or set(model_payload.keys()) != _MODEL_KEYS:
            raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
        model_display_name = validate_provider_display_name(model_payload["name"])
        capabilities = validate_declared_capabilities(model_payload["capabilities"])
        models.append(
            AiProviderModel(
                model_id=validated_model_id,
                display_name=model_display_name,
                capabilities=capabilities,
            )
        )
    return AiProviderRecord(
        provider_id=validated_id,
        display_name=display_name,
        protocol=protocol,
        base_url=base_url,
        credential_env=credential_env,
        models=tuple(models),
        source="declared",
    )


def validate_declared_provider_map(payload: object) -> dict[str, AiProviderRecord]:
    """Validate one declared-provider map from a configuration file."""
    if payload is None:
        return {}
    if not isinstance(payload, Mapping):
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    if len(payload) > MAX_DECLARED_PROVIDERS:
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    records: dict[str, AiProviderRecord] = {}
    for provider_id, record_payload in payload.items():
        record = parse_declared_provider_record(provider_id, record_payload)
        if record.provider_id in records:
            raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
        records[record.provider_id] = record
    return records


def normalize_declared_provider_record(record: object) -> AiProviderRecord:
    """Re-validate one programmatically constructed declared record."""
    if not isinstance(record, AiProviderRecord):
        raise AiProviderRecordError(PROVIDER_RECORD_MALFORMED_MESSAGE)
    return parse_declared_provider_record(
        record.provider_id,
        serialize_declared_provider_record(record),
    )


def serialize_declared_provider_record(record: AiProviderRecord) -> dict[str, Any]:
    """Serialize one declared record into its exact non-secret file shape."""
    return {
        "name": record.display_name,
        "protocol": record.protocol,
        "base_url": record.base_url,
        "credential_env": record.credential_env,
        "models": {
            model.model_id: {
                "name": model.display_name,
                "capabilities": list(model.capabilities),
            }
            for model in record.models
        },
    }


def builtin_provider_records() -> dict[str, AiProviderRecord]:
    """Return the code-defined built-in provider records."""
    return {
        DEFAULT_PROVIDER_ID: AiProviderRecord(
            provider_id=DEFAULT_PROVIDER_ID,
            display_name="NVIDIA NIM",
            protocol=NVIDIA_NIM_PROTOCOL,
            base_url=NVIDIA_CHAT_COMPLETIONS_URL,
            credential_env=NVIDIA_API_KEY_ENVIRONMENT_NAME,
            models=(
                AiProviderModel(
                    model_id=DEFAULT_MODEL_ID,
                    display_name=DEFAULT_MODEL_ID,
                    capabilities=("vision_input",),
                ),
            ),
            source="builtin",
        ),
        VERCEL_AI_GATEWAY_PROVIDER_ID: AiProviderRecord(
            provider_id=VERCEL_AI_GATEWAY_PROVIDER_ID,
            display_name="Vercel AI Gateway",
            protocol=OPENAI_CHAT_COMPLETIONS_PROTOCOL,
            base_url=VERCEL_AI_GATEWAY_CHAT_COMPLETIONS_URL.removesuffix("/chat/completions"),
            credential_env=VERCEL_AI_GATEWAY_API_KEY_ENVIRONMENT_NAME,
            models=(
                AiProviderModel(
                    model_id=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
                    display_name=VERCEL_AI_GATEWAY_DEFAULT_MODEL_ID,
                    capabilities=("vision_input",),
                ),
            ),
            source="builtin",
        ),
    }


def builtin_default_model_id(provider_id: str) -> str:
    """Return the default model id of one built-in provider."""
    record = builtin_provider_records().get(provider_id)
    if record is None:
        raise AiProviderRecordError(PROVIDER_IDENTIFIER_INVALID_MESSAGE)
    return record.models[0].model_id


def _reject_secret_shapes(value: str) -> None:
    lowered = value.lower()
    if any(marker in lowered for marker in _SECRET_SHAPE_MARKERS):
        raise AiProviderRecordError(PROVIDER_SECRET_SHAPE_MESSAGE)


def _is_dns_name(host: str) -> bool:
    if len(host) > MAX_HOSTNAME_LENGTH:
        return False
    labels = host.split(".")
    for label in labels:
        if not label or len(label) > MAX_DNS_LABEL_LENGTH:
            return False
        if not _DNS_LABEL_PATTERN.fullmatch(label):
            return False
    return True


def _is_loopback_host(host: str) -> bool:
    candidate = host.lower()
    if candidate == "localhost" or candidate.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(candidate)
    except ValueError:
        return False
    return address.is_loopback
