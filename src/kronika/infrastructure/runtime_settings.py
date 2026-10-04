"""Atomic JSON sidecar for administrator-writable runtime settings.

Persists beside the catalog database. This file is not part of catalog backup.
A missing or unreadable overlay fails closed to the process settings fallback.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kronika.configuration import KronikaSettings, resolved_runtime_settings_path

RUNTIME_SETTINGS_SCHEMA_VERSION = 1
RUNTIME_SETTINGS_FILENAME = "runtime-settings.json"


class RuntimeSettingsError(RuntimeError):
    """Sanitized runtime-settings persistence failure."""


def default_now_ms() -> int:
    return time.time_ns() // 1_000_000


class RuntimeSettingsStore:
    """Read and atomically write the automatic-analysis overlay."""

    def __init__(
        self,
        path: Path,
        *,
        fallback_enabled: bool,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> None:
        self._path = path
        self._fallback_enabled = bool(fallback_enabled)
        self._now_ms = now_ms

    @classmethod
    def from_settings(
        cls,
        settings: KronikaSettings,
        *,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> RuntimeSettingsStore:
        return cls(
            resolved_runtime_settings_path(settings),
            fallback_enabled=settings.automatic_media_analysis_enabled,
            now_ms=now_ms,
        )

    def is_enabled(self) -> bool:
        overlay = self._read_overlay()
        if overlay is None:
            return self._fallback_enabled
        return overlay

    def set_enabled(self, enabled: bool) -> bool:
        flag = bool(enabled)
        self._write(flag)
        return flag

    def _read_overlay(self) -> bool | None:
        try:
            normalized = _prepare_existing_or_missing_path(self._path)
        except RuntimeSettingsError:
            return None
        if not normalized.exists():
            return None
        try:
            payload = json.loads(normalized.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None
        if payload.get("schema_version") != RUNTIME_SETTINGS_SCHEMA_VERSION:
            return None
        value = payload.get("automatic_media_analysis_enabled")
        if not isinstance(value, bool):
            return None
        return value

    def _write(self, enabled: bool) -> None:
        payload = {
            "schema_version": RUNTIME_SETTINGS_SCHEMA_VERSION,
            "automatic_media_analysis_enabled": bool(enabled),
            "updated_at_ms": int(self._now_ms()),
        }
        _atomic_write_json(self._path, payload)


def _validated_absolute_path(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value).expanduser()
    except (RuntimeError, TypeError, ValueError):
        raise RuntimeSettingsError("runtime settings path must be absolute.") from None
    if not path.is_absolute():
        raise RuntimeSettingsError("runtime settings path must be absolute.")
    return path.resolve(strict=False)


def _prepare_existing_or_missing_path(path: Path) -> Path:
    original = Path(path).expanduser()
    if original.is_symlink():
        raise RuntimeSettingsError("runtime settings path must not be a symlink.")
    normalized = _validated_absolute_path(path)
    if normalized.is_symlink():
        raise RuntimeSettingsError("runtime settings path must not be a symlink.")
    parent = normalized.parent
    if parent.exists() and parent.is_symlink():
        raise RuntimeSettingsError("runtime settings directory must not be a symlink.")
    return normalized


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    normalized = _prepare_existing_or_missing_path(path)
    parent = normalized.parent
    if parent.exists() and not parent.is_dir():
        raise RuntimeSettingsError("runtime settings directory is invalid.")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise RuntimeSettingsError("runtime settings directory must not be a symlink.")
    try:
        os.chmod(parent, 0o700)
    except OSError:
        pass
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    fd = -1
    temp_name = ""
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{normalized.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
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
        raise RuntimeSettingsError("runtime settings could not be written.") from None
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass
