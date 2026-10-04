"""Non-secret server AI configuration storage."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from kronika.infrastructure.ai.constants import BUILTIN_PROVIDER_IDS
from kronika.infrastructure.ai.research_configuration import (
    ResearchConfiguration,
    ResearchConfigurationError,
    parse_research_configuration,
    serialize_research_configuration,
)
from kronika.infrastructure.ai.provider_records import (
    AiProviderRecord,
    AiProviderRecordError,
    MAX_AI_CONFIG_BYTES,
    MAX_DECLARED_PROVIDERS,
    builtin_default_model_id,
    normalize_declared_provider_record,
    serialize_declared_provider_record,
    validate_declared_provider_map,
    validate_model_identifier,
    validate_provider_identifier,
)
from kronika.identity_env import (
    COMPATIBLE_ENVIRONMENT_PREFIX,
    IdentityEnvironmentConflictError,
    lookup_env,
)

AI_CONFIG_SCHEMA_VERSION = 3
AI_CONFIG_SCHEMA_VERSIONS = frozenset({1, 2, AI_CONFIG_SCHEMA_VERSION})
_V3_CONFIG_KEYS = frozenset(
    {
        "schema_version",
        "active_provider_id",
        "provider_models",
        "providers",
        "updated_at_ms",
        "research",
    }
)
AI_TEST_STATE_SCHEMA_VERSION = 1
AI_STATUS_SNAPSHOT_SCHEMA_VERSION = 1
AI_CONFIG_PATH_ENVIRONMENT_NAME = f"{COMPATIBLE_ENVIRONMENT_PREFIX}AI_CONFIG_PATH"
AI_CONFIG_PATH_ENVIRONMENT_SUFFIX = "AI_CONFIG_PATH"

SAFE_TEST_STATUSES = frozenset(
    {
        "success",
        "authentication_failed",
        "rate_limited_or_quota_exhausted",
        "model_unavailable",
        "provider_unreachable",
        "invalid_response",
        "provider_error",
    }
)


class AiConfigurationError(RuntimeError):
    """Sanitized AI configuration failure."""


class AiConfigConflictError(AiConfigurationError):
    """A compare-and-set revision did not match the stored configuration."""

    def __init__(self) -> None:
        super().__init__("AI configuration changed.")


ABSENT_REVISION = "absent"
_UNSET = object()
_PROCESS_LOCKS: dict[str, threading.Lock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class AiServerConfigSnapshot:
    """A validated configuration plus the revision of its bounded raw bytes."""

    config: AiServerConfig | None
    revision: str


@dataclass(frozen=True, slots=True)
class AiServerConfig:
    """Non-secret server AI provider configuration."""

    active_provider_id: str
    provider_models: dict[str, str]
    updated_at_ms: int
    schema_version: int = AI_CONFIG_SCHEMA_VERSION
    providers: dict[str, AiProviderRecord] = field(default_factory=dict)
    research: ResearchConfiguration | None = None


@dataclass(frozen=True, slots=True)
class AiTestState:
    """Safe historical AI connection-test result."""

    provider_id: str
    model_id: str
    status: str
    tested_at_ms: int
    schema_version: int = AI_TEST_STATE_SCHEMA_VERSION


@dataclass(frozen=True, slots=True)
class AiStatusSnapshot:
    """Safe network-free AI status snapshot."""

    provider_id: str | None
    model_id: str | None
    configuration_state: str
    checked_at_ms: int
    schema_version: int = AI_STATUS_SNAPSHOT_SCHEMA_VERSION


def validate_provider_id(provider_id: object) -> str:
    """Validate one bounded provider identifier."""
    try:
        return validate_provider_identifier(provider_id)
    except AiProviderRecordError:
        raise AiConfigurationError("AI provider is not supported.") from None


def validate_model_id(model_id: object) -> str:
    """Validate one bounded model identifier."""
    try:
        return validate_model_identifier(model_id)
    except AiProviderRecordError:
        raise AiConfigurationError("AI model is invalid.") from None


def now_ms() -> int:
    return int(time.time() * 1000)


def default_ai_config_path(
    environ: Mapping[str, str] | None = None,
    *,
    platform: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the configured or platform default non-secret AI config path."""
    source = os.environ if environ is None else environ
    try:
        override = lookup_env(AI_CONFIG_PATH_ENVIRONMENT_SUFFIX, environ=source)
    except IdentityEnvironmentConflictError as exc:
        raise AiConfigurationError(str(exc)) from exc
    if override is not None and override.strip():
        return _validated_absolute_path(override)
    resolved_home = Path.home() if home is None else home
    resolved_platform = sys.platform if platform is None else platform
    if resolved_platform == "darwin":
        return (
            resolved_home
            / "Library"
            / "Application Support"
            / "FrameNest"
            / "ai"
            / "config.json"
        )
    xdg_config = source.get("XDG_CONFIG_HOME")
    config_home = _validated_absolute_path(xdg_config) if xdg_config else resolved_home / ".config"
    return config_home / "framenest" / "ai" / "config.json"


def default_ai_test_state_path(config_path: Path) -> Path:
    """Return the safe test-state path beside the non-secret config file."""
    return config_path.parent / "test-state.json"


def default_ai_status_snapshot_path(config_path: Path) -> Path:
    """Return the safe status snapshot path beside the non-secret config file."""
    return config_path.parent / "status-snapshot.json"


def load_ai_server_config(path: Path) -> AiServerConfig | None:
    """Load one optional non-secret server AI config file."""
    normalized = _prepare_existing_or_missing_path(path)
    if not normalized.exists():
        return None
    try:
        raw_text = normalized.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        raise AiConfigurationError("AI configuration is malformed.") from None
    if len(raw_text.encode("utf-8")) > MAX_AI_CONFIG_BYTES:
        raise AiConfigurationError("AI configuration is malformed.")
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        raise AiConfigurationError("AI configuration is malformed.") from None
    if not isinstance(payload, dict):
        raise AiConfigurationError("AI configuration is malformed.")
    schema_version = _accepted_schema_version(payload.get("schema_version"))
    try:
        if schema_version == 1:
            providers: dict[str, AiProviderRecord] = {}
        else:
            providers = _parse_declared_providers(payload.get("providers"))
    except AiProviderRecordError as exc:
        raise AiConfigurationError(str(exc)) from None
    research = _parse_research_section(schema_version, payload)
    provider_id = validate_provider_id(payload.get("active_provider_id"))
    provider_models_payload = payload.get("provider_models")
    if not isinstance(provider_models_payload, dict):
        raise AiConfigurationError("AI configuration is malformed.")
    provider_models: dict[str, str] = {}
    for key, value in provider_models_payload.items():
        selected_provider_id = validate_provider_id(key)
        _reject_unknown_provider_id(selected_provider_id, providers)
        provider_models[selected_provider_id] = validate_model_id(value)
    if provider_id in providers:
        if provider_id not in provider_models:
            raise AiConfigurationError("AI configuration is malformed.")
    elif provider_id in BUILTIN_PROVIDER_IDS:
        provider_models.setdefault(provider_id, builtin_default_model_id(provider_id))
    else:
        raise AiConfigurationError("AI configuration is malformed.")
    updated_at_ms = payload.get("updated_at_ms")
    if not isinstance(updated_at_ms, int) or updated_at_ms < 0:
        raise AiConfigurationError("AI configuration is malformed.")
    return AiServerConfig(
        active_provider_id=provider_id,
        provider_models=provider_models,
        updated_at_ms=updated_at_ms,
        schema_version=AI_CONFIG_SCHEMA_VERSION,
        providers=providers,
        research=research,
    )


def load_ai_server_config_snapshot(path: Path) -> AiServerConfigSnapshot:
    """Return the validated config and the revision of its bounded raw bytes.

    An absent file has revision ``"absent"``. The revision is a content hash, so
    it changes whenever the stored bytes change and cannot drift from the
    validated configuration.
    """
    normalized = _prepare_existing_or_missing_path(path)
    if not normalized.exists():
        return AiServerConfigSnapshot(config=None, revision=ABSENT_REVISION)
    try:
        raw = normalized.read_bytes()
    except OSError:
        raise AiConfigurationError("AI configuration is malformed.") from None
    if len(raw) > MAX_AI_CONFIG_BYTES:
        raise AiConfigurationError("AI configuration is malformed.")
    revision = hashlib.sha256(raw).hexdigest()
    return AiServerConfigSnapshot(
        config=load_ai_server_config(path),
        revision=revision,
    )


def write_ai_server_config(
    config: AiServerConfig,
    path: Path,
    *,
    expected_revision: object = _UNSET,
) -> None:
    """Atomically write one non-secret server AI config file.

    A direct call without an expected revision is creation-only: replacing an
    existing file requires the revision observed by a prior snapshot. Passing
    ``ABSENT_REVISION`` asserts the file must not yet exist.
    """
    with _config_file_guard(path):
        snapshot = load_ai_server_config_snapshot(path)
        if expected_revision is _UNSET:
            if snapshot.revision != ABSENT_REVISION:
                raise AiConfigConflictError()
        elif expected_revision != snapshot.revision:
            raise AiConfigConflictError()
        _write_ai_server_config_unlocked(config, path)


def mutate_ai_server_config(
    config_path: Path,
    mutator: Callable[[AiServerConfig | None], AiServerConfig],
    *,
    expected_revision: object = _UNSET,
) -> AiServerConfig:
    """Read, compare, transform, and atomically write under one file guard."""
    with _config_file_guard(config_path):
        snapshot = load_ai_server_config_snapshot(config_path)
        if expected_revision is not _UNSET and expected_revision != snapshot.revision:
            raise AiConfigConflictError()
        updated = mutator(snapshot.config)
        if snapshot.config is not None and updated == snapshot.config:
            # A fresh no-op returns success without rewriting or advancing the
            # revision. A real change advances updated_at_ms monotonically.
            return snapshot.config
        previous_ms = 0 if snapshot.config is None else snapshot.config.updated_at_ms
        refreshed = replace(
            updated,
            updated_at_ms=max(now_ms(), previous_ms + 1),
        )
        _write_ai_server_config_unlocked(refreshed, config_path)
        return refreshed


def _write_ai_server_config_unlocked(config: AiServerConfig, path: Path) -> None:
    provider_id = validate_provider_id(config.active_provider_id)
    try:
        providers = _validate_declared_providers(config.providers)
    except AiProviderRecordError as exc:
        raise AiConfigurationError(str(exc)) from None
    provider_models = {
        validate_provider_id(key): validate_model_id(value)
        for key, value in config.provider_models.items()
    }
    for selected_provider_id in provider_models:
        _reject_unknown_provider_id(selected_provider_id, providers)
    if provider_id in providers:
        if provider_id not in provider_models:
            raise AiConfigurationError("AI configuration is malformed.")
    elif provider_id in BUILTIN_PROVIDER_IDS:
        provider_models.setdefault(provider_id, builtin_default_model_id(provider_id))
    else:
        raise AiConfigurationError("AI configuration is malformed.")
    payload = {
        "schema_version": AI_CONFIG_SCHEMA_VERSION,
        "active_provider_id": provider_id,
        "provider_models": provider_models,
        "providers": {
            record_provider_id: serialize_declared_provider_record(record)
            for record_provider_id, record in providers.items()
        },
        "updated_at_ms": config.updated_at_ms,
    }
    if config.research is not None:
        payload["research"] = _serialize_research_section(config.research)
    _atomic_write_json(path, payload, max_payload_bytes=MAX_AI_CONFIG_BYTES)


def _accepted_schema_version(value: object) -> int:
    if type(value) is not int or value not in AI_CONFIG_SCHEMA_VERSIONS:
        raise AiConfigurationError("AI configuration version is unsupported.")
    return value


def _parse_research_section(
    schema_version: int,
    payload: Mapping[str, Any],
) -> ResearchConfiguration | None:
    """Read the optional research section. Versions 1 and 2 stay media-only."""
    if schema_version < 3:
        return None
    if not set(payload).issubset(_V3_CONFIG_KEYS):
        raise AiConfigurationError("AI configuration is malformed.")
    if "research" not in payload:
        return None
    try:
        return parse_research_configuration(payload["research"])
    except ResearchConfigurationError:
        raise AiConfigurationError("AI research configuration is malformed.") from None


def _serialize_research_section(research: ResearchConfiguration) -> dict[str, Any]:
    try:
        return serialize_research_configuration(research)
    except ResearchConfigurationError:
        raise AiConfigurationError("AI research configuration is malformed.") from None


def _parse_declared_providers(payload: object) -> dict[str, AiProviderRecord]:
    return validate_declared_provider_map(payload)


def _validate_declared_providers(
    providers: Mapping[str, AiProviderRecord],
) -> dict[str, AiProviderRecord]:
    if not isinstance(providers, Mapping) or len(providers) > MAX_DECLARED_PROVIDERS:
        raise AiProviderRecordError("AI provider record is malformed.")
    validated: dict[str, AiProviderRecord] = {}
    for key, record in providers.items():
        normalized = normalize_declared_provider_record(record)
        if normalized.provider_id != key:
            raise AiProviderRecordError("AI provider record is malformed.")
        validated[normalized.provider_id] = normalized
    return validated


def _reject_unknown_provider_id(
    provider_id: str,
    providers: Mapping[str, AiProviderRecord],
) -> None:
    if provider_id in BUILTIN_PROVIDER_IDS or provider_id in providers:
        return
    raise AiConfigurationError("AI configuration is malformed.")


def load_ai_test_state(path: Path) -> AiTestState | None:
    """Load optional safe historical AI test state."""
    normalized = _prepare_existing_or_missing_path(path)
    if not normalized.exists():
        return None
    try:
        payload = json.loads(normalized.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AiConfigurationError("AI test state is malformed.") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != AI_TEST_STATE_SCHEMA_VERSION:
        raise AiConfigurationError("AI test state version is unsupported.")
    provider_id = validate_provider_id(payload.get("provider_id"))
    model_id = validate_model_id(payload.get("model_id"))
    status = payload.get("status")
    tested_at_ms = payload.get("tested_at_ms")
    if status not in SAFE_TEST_STATUSES or not isinstance(tested_at_ms, int) or tested_at_ms < 0:
        raise AiConfigurationError("AI test state is malformed.")
    return AiTestState(provider_id=provider_id, model_id=model_id, status=status, tested_at_ms=tested_at_ms)


def write_ai_test_state(state: AiTestState, path: Path) -> None:
    """Atomically write safe historical AI test state."""
    payload = {
        "schema_version": AI_TEST_STATE_SCHEMA_VERSION,
        "provider_id": validate_provider_id(state.provider_id),
        "model_id": validate_model_id(state.model_id),
        "status": state.status,
        "tested_at_ms": state.tested_at_ms,
    }
    if state.status not in SAFE_TEST_STATUSES:
        raise AiConfigurationError("AI test state is malformed.")
    _atomic_write_json(path, payload)


def load_ai_status_snapshot(path: Path) -> AiStatusSnapshot | None:
    """Load optional safe network-free AI status snapshot."""
    normalized = _prepare_existing_or_missing_path(path)
    if not normalized.exists():
        return None
    try:
        payload = json.loads(normalized.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise AiConfigurationError("AI status snapshot is malformed.") from None
    if not isinstance(payload, dict) or payload.get("schema_version") != AI_STATUS_SNAPSHOT_SCHEMA_VERSION:
        raise AiConfigurationError("AI status snapshot version is unsupported.")
    configuration_state = payload.get("configuration_state")
    provider_id, model_id = _validate_status_snapshot_identity(
        provider_id=payload.get("provider_id"),
        model_id=payload.get("model_id"),
        configuration_state=configuration_state,
    )
    checked_at_ms = payload.get("checked_at_ms")
    if not isinstance(checked_at_ms, int) or checked_at_ms < 0:
        raise AiConfigurationError("AI status snapshot is malformed.")
    return AiStatusSnapshot(
        provider_id=provider_id,
        model_id=model_id,
        configuration_state=configuration_state,
        checked_at_ms=checked_at_ms,
    )


def write_ai_status_snapshot(snapshot: AiStatusSnapshot, path: Path) -> None:
    """Atomically write safe network-free AI status snapshot."""
    provider_id, model_id = _validate_status_snapshot_identity(
        provider_id=snapshot.provider_id,
        model_id=snapshot.model_id,
        configuration_state=snapshot.configuration_state,
    )
    payload = {
        "schema_version": AI_STATUS_SNAPSHOT_SCHEMA_VERSION,
        "provider_id": provider_id,
        "model_id": model_id,
        "configuration_state": snapshot.configuration_state,
        "checked_at_ms": snapshot.checked_at_ms,
    }
    _atomic_write_json(path, payload)


def _validate_status_snapshot_identity(
    *,
    provider_id: object,
    model_id: object,
    configuration_state: object,
) -> tuple[str | None, str | None]:
    if configuration_state not in {"configured", "not_configured"}:
        raise AiConfigurationError("AI status snapshot is malformed.")
    if provider_id is None and model_id is None:
        if configuration_state == "configured":
            raise AiConfigurationError("AI status snapshot is malformed.")
        return None, None
    if provider_id is None or model_id is None:
        raise AiConfigurationError("AI status snapshot is malformed.")
    return validate_provider_id(provider_id), validate_model_id(model_id)


def _validated_absolute_path(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value).expanduser()
    except (RuntimeError, TypeError, ValueError):
        raise AiConfigurationError("AI configuration path must be absolute.") from None
    if not path.is_absolute():
        raise AiConfigurationError("AI configuration path must be absolute.")
    return path.resolve(strict=False)


def _prepare_existing_or_missing_path(path: Path) -> Path:
    original = Path(path).expanduser()
    if original.is_symlink():
        raise AiConfigurationError("AI configuration path must not be a symlink.")
    normalized = _validated_absolute_path(path)
    if normalized.is_symlink():
        raise AiConfigurationError("AI configuration path must not be a symlink.")
    parent = normalized.parent
    if parent.exists() and parent.is_symlink():
        raise AiConfigurationError("AI configuration directory must not be a symlink.")
    return normalized


def _process_lock(normalized: Path) -> threading.Lock:
    key = str(normalized)
    with _PROCESS_LOCKS_GUARD:
        lock = _PROCESS_LOCKS.get(key)
        if lock is None:
            lock = threading.Lock()
            _PROCESS_LOCKS[key] = lock
        return lock


def _open_lock_descriptor(lock_path: Path) -> int:
    parent = lock_path.parent
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if lock_path.is_symlink():
        raise AiConfigurationError("AI configuration lock must not be a symlink.")
    if lock_path.exists() and not lock_path.is_file():
        raise AiConfigurationError("AI configuration lock is invalid.")
    try:
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    except OSError:
        raise AiConfigurationError("AI configuration lock could not be created.") from None
    try:
        lock_stat = os.fstat(descriptor)
    except OSError:
        os.close(descriptor)
        raise AiConfigurationError("AI configuration lock is invalid.") from None
    if not stat.S_ISREG(lock_stat.st_mode):
        os.close(descriptor)
        raise AiConfigurationError("AI configuration lock is invalid.")
    return descriptor


def _acquire_os_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows only
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_EX)
    except OSError:
        raise AiConfigurationError("AI configuration lock could not be acquired.") from None


def _release_os_lock(descriptor: int) -> None:
    try:
        if os.name == "nt":  # pragma: no cover - exercised on Windows only
            import msvcrt

            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(descriptor, fcntl.LOCK_UN)
    except OSError:
        return


@contextmanager
def _config_file_guard(path: Path) -> Iterator[None]:
    """Serialize one configuration path across threads and processes.

    A per-canonical-path process lock composes with a stable sibling OS advisory
    lock that the operating system releases on process exit. The lock file is
    private, is never removed after release and rejects symlinks.
    """
    normalized = _prepare_existing_or_missing_path(path)
    process_lock = _process_lock(normalized)
    with process_lock:
        lock_path = normalized.parent / f".{normalized.name}.lock"
        descriptor = _open_lock_descriptor(lock_path)
        _acquire_os_lock(descriptor)
        try:
            yield
        finally:
            _release_os_lock(descriptor)
            os.close(descriptor)


def _atomic_write_json(
    path: Path,
    payload: dict[str, Any],
    *,
    max_payload_bytes: int | None = None,
) -> None:
    normalized = _prepare_existing_or_missing_path(path)
    parent = normalized.parent
    if parent.exists() and not parent.is_dir():
        raise AiConfigurationError("AI configuration directory is invalid.")
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    if max_payload_bytes is not None and len(body) > max_payload_bytes:
        raise AiConfigurationError("AI configuration is too large.")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise AiConfigurationError("AI configuration directory must not be a symlink.")
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    fd = -1
    temp_name = ""
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{normalized.name}.", suffix=".tmp", dir=str(parent))
        temp_path = Path(temp_name)
        os.chmod(temp_path, 0o600)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, normalized)
        try:
            os.chmod(normalized, 0o600)
        except OSError:
            pass
    except OSError:
        raise AiConfigurationError("AI configuration could not be written.") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
