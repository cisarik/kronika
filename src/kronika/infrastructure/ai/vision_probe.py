"""Provider-neutral vision probe fixture, bounded color judge, and safe state."""

from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from kronika.infrastructure.ai.configuration import (
    AiConfigurationError,
    _atomic_write_json,
    _prepare_existing_or_missing_path,
    validate_model_id,
    validate_provider_id,
)

VISION_PROBE_PROMPT = "What color is this? Answer with one word."
VISION_PROBE_PROMPT_VERSION = "framenest-vision-probe-v1"
VISION_PROBE_FIXTURE_PACKAGE = "kronika.infrastructure.ai.fixtures"
VISION_PROBE_FIXTURE_NAME = "vision-probe-red-8x8.png"
VISION_PROBE_STATE_FILENAME = "vision-probe-state.json"
VISION_PROBE_STATE_SCHEMA_VERSION = 1
VISION_PROBE_STATE_MAX_BYTES = 4096
EXPECTED_COLOR = "red"
MAX_OBSERVED_TOKEN_LENGTH = 32

VISION_PROBE_STATUSES = frozenset(
    {
        "success",
        "mismatch",
        "authentication_failed",
        "rate_limited_or_quota_exhausted",
        "model_unavailable",
        "provider_unreachable",
        "invalid_response",
        "provider_error",
    }
)

_ACCEPTED_COLOR_TOKENS = frozenset(
    {
        "red",
        "crimson",
        "scarlet",
        "vermillion",
        "červená",
        "cervena",
        "ff0000",
    }
)
_SEPARATOR_CATEGORIES = ("P", "S", "C")
_REJECTED_TOKEN_CATEGORIES = ("P", "S", "C", "Z")


@dataclass(frozen=True, slots=True)
class VisionProbeState:
    """Safe historical vision-probe result without raw completion text."""

    provider_id: str
    model_id: str
    status: str
    matched: bool
    observed_color: str | None
    probed_at_ms: int
    schema_version: int = VISION_PROBE_STATE_SCHEMA_VERSION


def load_vision_probe_fixture() -> bytes:
    """Load the committed golden probe fixture through the package boundary."""
    fixture = resources.files(VISION_PROBE_FIXTURE_PACKAGE).joinpath(
        VISION_PROBE_FIXTURE_NAME
    )
    return fixture.read_bytes()


def match_expected_color(text: str) -> tuple[bool, str | None]:
    """Judge one completion against the bounded accepted color set."""
    if not isinstance(text, str):
        return False, None
    tokens = _normalize_completion_text(text).split()
    if not tokens:
        return False, None
    first_token = tokens[0]
    observed_token = first_token[:MAX_OBSERVED_TOKEN_LENGTH]
    if len(tokens) != 1 or first_token not in _ACCEPTED_COLOR_TOKENS:
        return False, observed_token
    return True, observed_token


def default_vision_probe_state_path(config_path: Path) -> Path:
    """Return the safe vision-probe state path beside the config file."""
    return config_path.parent / VISION_PROBE_STATE_FILENAME


def load_vision_probe_state(path: Path) -> VisionProbeState | None:
    """Load optional safe vision-probe state, treating any defect as absent."""
    try:
        normalized = _prepare_existing_or_missing_path(path)
    except AiConfigurationError:
        return None
    if not normalized.exists():
        return None
    try:
        raw_text = normalized.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    if payload.get("schema_version") != VISION_PROBE_STATE_SCHEMA_VERSION:
        return None
    try:
        provider_id = validate_provider_id(payload.get("provider_id"))
        model_id = validate_model_id(payload.get("model_id"))
    except AiConfigurationError:
        return None
    status = payload.get("status")
    if status not in VISION_PROBE_STATUSES:
        return None
    matched = payload.get("matched")
    if not isinstance(matched, bool):
        return None
    try:
        observed_color = _validated_observed_color(payload.get("observed_color"))
    except AiConfigurationError:
        return None
    probed_at_ms = payload.get("probed_at_ms")
    if not _is_safe_timestamp(probed_at_ms):
        return None
    return VisionProbeState(
        provider_id=provider_id,
        model_id=model_id,
        status=status,
        matched=matched,
        observed_color=observed_color,
        probed_at_ms=probed_at_ms,
    )


def load_matching_vision_probe_state(
    path: Path,
    *,
    provider_id: str,
    model_id: str,
) -> VisionProbeState | None:
    """Load vision-probe state only for the exact provider/model identity."""
    state = load_vision_probe_state(path)
    if state is None:
        return None
    if state.provider_id != provider_id or state.model_id != model_id:
        return None
    return state


def write_vision_probe_state(state: VisionProbeState, path: Path) -> None:
    """Atomically write safe vision-probe state beside the config file."""
    if state.status not in VISION_PROBE_STATUSES:
        raise AiConfigurationError("AI vision probe state is malformed.")
    if not isinstance(state.matched, bool):
        raise AiConfigurationError("AI vision probe state is malformed.")
    if not _is_safe_timestamp(state.probed_at_ms):
        raise AiConfigurationError("AI vision probe state is malformed.")
    payload = {
        "schema_version": VISION_PROBE_STATE_SCHEMA_VERSION,
        "provider_id": validate_provider_id(state.provider_id),
        "model_id": validate_model_id(state.model_id),
        "status": state.status,
        "matched": state.matched,
        "observed_color": _validated_observed_color(state.observed_color),
        "probed_at_ms": state.probed_at_ms,
    }
    _atomic_write_json(path, payload, max_payload_bytes=VISION_PROBE_STATE_MAX_BYTES)


def _normalize_completion_text(text: str) -> str:
    lowered = text.lower()
    characters: list[str] = []
    for character in lowered:
        category = unicodedata.category(character)
        if category.startswith(_SEPARATOR_CATEGORIES):
            characters.append(" ")
            continue
        characters.append(character)
    return " ".join("".join(characters).split())


def _validated_observed_color(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise AiConfigurationError("AI vision probe state is malformed.")
    if not value or len(value) > MAX_OBSERVED_TOKEN_LENGTH:
        raise AiConfigurationError("AI vision probe state is malformed.")
    for character in value:
        category = unicodedata.category(character)
        if category.startswith(_REJECTED_TOKEN_CATEGORIES):
            raise AiConfigurationError("AI vision probe state is malformed.")
    return value


def _is_safe_timestamp(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0
