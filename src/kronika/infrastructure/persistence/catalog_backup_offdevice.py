"""Mounted-filesystem off-device catalog backup copy and restore verification.

This module implements a provider-neutral copy of a verified local scheduled
catalog recovery point onto a distinct mounted destination, with exact-byte
verification, Linux no-replace publication, and disposable restore verification
from the published destination bundle.

Repository implementation proves the filesystem contract only. It does not claim
that a particular physical destination survives host loss until separately
authorized host acceptance proves the failure domain.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import errno
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
from typing import Any, Literal

from kronika.infrastructure.persistence.catalog_backup import (
    CATALOG_NAME,
    MANIFEST_NAME,
    BackupError,
    sha256_file,
)
from kronika.infrastructure.persistence.catalog_backup_transfer import (
    BundleIdentity,
    TransferError,
    capture_bundle_identity,
    fsync_directory,
    identities_match,
    rename_noreplace as transfer_rename_noreplace,
)
from kronika.identity_env import (
    IdentityEnvironmentConflictError,
    IdentityEnvironmentConflictFailure,
    lookup_env,
)

DEFAULT_OFFDEVICE_ROOT = Path("/mnt/framenest-catalog-offdevice")
MARKER_NAME = ".kronika-catalog-offdevice.json"
#: The marker filename written before the durable-writer cut. Readers keep
#: accepting it, and the accepted tuple must name it explicitly.
COMPATIBLE_MARKER_NAME = ".framenest-catalog-offdevice.json"
#: Accepted marker filenames, current writer spelling first.
ACCEPTED_MARKER_NAMES = (MARKER_NAME, COMPATIBLE_MARKER_NAME)
BUNDLES_DIRNAME = "bundles"
MARKER_PURPOSE = "kronika-catalog-offdevice"
#: The purpose written before the durable-writer cut. Readers keep accepting it.
COMPATIBLE_MARKER_PURPOSE = "framenest-catalog-offdevice"
ACCEPTED_MARKER_PURPOSES = frozenset({MARKER_PURPOSE, COMPATIBLE_MARKER_PURPOSE})
MARKER_SCHEMA_VERSION = 1
DESTINATION_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
STAGE_PREFIX = ".kronika-offdevice-stage-"
#: The staging prefix written before the durable-writer cut. The cleanup
#: recognizer keeps accepting it so an abandoned former staging directory is
#: still identified as owned.
COMPATIBLE_STAGE_PREFIX = ".framenest-offdevice-stage-"
ACCEPTED_STAGE_PREFIXES = (STAGE_PREFIX, COMPATIBLE_STAGE_PREFIX)
OFFDEVICE_STALE_AFTER = timedelta(hours=48)
OffdeviceReadiness = Literal[
    "disabled",
    "busy",
    "unavailable",
    "never_verified",
    "failed",
    "stale",
    "ready",
]

class OffdeviceError(BackupError):
    """Sanitized off-device catalog copy failure."""


class OffdeviceIdentityEnvironmentConflictError(
    IdentityEnvironmentConflictFailure,
    OffdeviceError,
):
    """Off-device failure caused by one setting name set under both prefixes.

    The off-device destination identifier is resolved through the same identity
    resolver as the settings boundary, so a conflicting pair keeps the existing
    off-device error code while gaining the uniform fail-closed exit status. The
    message names the two variable suffixes only.
    """


@dataclass(frozen=True, slots=True)
class ValidatedOffdeviceDestination:
    """Trusted destination layout after fail-closed validation."""

    root: Path
    bundles_dir: Path
    destination_id: str


@dataclass(frozen=True, slots=True)
class OffdeviceOsHooks:
    """Injectable OS inspection boundary for deterministic tests."""

    is_mountpoint: Callable[[Path], bool] = os.path.ismount
    device_id: Callable[[Path], int] = lambda path: path.stat().st_dev
    rename_noreplace: Callable[[Path, Path], None] | None = None
    fsync: Callable[[int], None] = os.fsync
    marker_uid_allowed: Callable[[int], bool] = lambda uid: uid == 0
    bundles_uid_allowed: Callable[[int], bool] = lambda uid: uid == os.geteuid()


@dataclass(frozen=True, slots=True)
class OffdeviceCopyResult:
    """Result of a successful off-device copy and restore verification."""

    bundle_id: str
    catalog_sha256: str
    alembic_revision: str
    catalog_size_bytes: int
    reused_existing: bool
    pending_cleanup: bool
    semantic: Mapping[str, Any]


def parse_configured_destination_id(
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the optional configured destination ID, or None when disabled."""
    env = os.environ if environ is None else environ
    try:
        raw = lookup_env("CATALOG_OFFDEVICE_DESTINATION_ID", environ=env)
    except IdentityEnvironmentConflictError as exc:
        raise OffdeviceIdentityEnvironmentConflictError(
            str(exc),
            error_code="OFFDEVICE_DESTINATION_ID_INVALID",
        ) from exc
    if raw is None or raw == "":
        return None
    value = raw.strip()
    if not DESTINATION_ID_PATTERN.fullmatch(value):
        raise OffdeviceError(
            "Off-device destination configuration is invalid.",
            error_code="OFFDEVICE_DESTINATION_ID_INVALID",
        )
    return value


def _accepted_marker(root: Path) -> Path | None:
    """Return the accepted marker path for a destination root, or None.

    The current writer spelling wins when both spellings exist. An accepted name
    that exists but is unsafe is returned as-is so the caller fails closed on
    that spelling instead of silently reading the other one.
    """
    for name in ACCEPTED_MARKER_NAMES:
        candidate = root / name
        if candidate.is_symlink() or candidate.exists():
            return candidate
    return None


def validate_offdevice_destination(
    *,
    destination_root: Path = DEFAULT_OFFDEVICE_ROOT,
    configured_destination_id: str | None,
    local_backup_root: Path,
    hooks: OffdeviceOsHooks | None = None,
) -> ValidatedOffdeviceDestination:
    """Fail closed unless the mounted destination trust boundary holds.

    Production scheduled operation must pass the fixed
    ``DEFAULT_OFFDEVICE_ROOT``. Tests may inject a disposable root together with
    mount/device hooks; arbitrary CLI destination selection remains forbidden.
    """
    probe = hooks or OffdeviceOsHooks()
    root = destination_root
    if root.is_symlink() or not root.exists() or not root.is_dir():
        raise OffdeviceError(
            "Off-device destination is unavailable.",
            error_code="OFFDEVICE_DESTINATION_UNAVAILABLE",
        )
    if not probe.is_mountpoint(root):
        raise OffdeviceError(
            "Off-device destination is not a mount point.",
            error_code="OFFDEVICE_DESTINATION_NOT_MOUNT",
        )
    try:
        local_dev = probe.device_id(local_backup_root)
        dest_dev = probe.device_id(root)
    except OSError as exc:
        raise OffdeviceError(
            "Off-device destination is unavailable.",
            error_code="OFFDEVICE_DESTINATION_UNAVAILABLE",
        ) from exc
    if local_dev == dest_dev:
        raise OffdeviceError(
            "Off-device destination shares the local backup filesystem.",
            error_code="OFFDEVICE_DESTINATION_SAME_DEVICE",
        )

    marker = _accepted_marker(root)
    if marker is None or marker.is_symlink() or not marker.is_file():
        raise OffdeviceError(
            "Off-device destination marker is missing or unsafe.",
            error_code="OFFDEVICE_MARKER_INVALID",
        )
    marker_stat = marker.lstat()
    if not probe.marker_uid_allowed(marker_stat.st_uid):
        raise OffdeviceError(
            "Off-device destination marker ownership is unsafe.",
            error_code="OFFDEVICE_MARKER_UNSAFE",
        )
    if marker_stat.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise OffdeviceError(
            "Off-device destination marker mode is unsafe.",
            error_code="OFFDEVICE_MARKER_UNSAFE",
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise OffdeviceError(
            "Off-device destination marker is malformed.",
            error_code="OFFDEVICE_MARKER_MALFORMED",
        ) from exc
    if not isinstance(payload, dict):
        raise OffdeviceError(
            "Off-device destination marker is malformed.",
            error_code="OFFDEVICE_MARKER_MALFORMED",
        )
    if set(payload.keys()) != {"schema_version", "purpose", "destination_id"}:
        raise OffdeviceError(
            "Off-device destination marker is malformed.",
            error_code="OFFDEVICE_MARKER_MALFORMED",
        )
    if payload.get("schema_version") != MARKER_SCHEMA_VERSION:
        raise OffdeviceError(
            "Off-device destination marker is unsupported.",
            error_code="OFFDEVICE_MARKER_UNSUPPORTED",
        )
    if payload.get("purpose") not in ACCEPTED_MARKER_PURPOSES:
        raise OffdeviceError(
            "Off-device destination marker purpose mismatch.",
            error_code="OFFDEVICE_MARKER_PURPOSE_MISMATCH",
        )
    destination_id = payload.get("destination_id")
    if not isinstance(destination_id, str) or not DESTINATION_ID_PATTERN.fullmatch(destination_id):
        raise OffdeviceError(
            "Off-device destination marker identity is invalid.",
            error_code="OFFDEVICE_MARKER_ID_INVALID",
        )
    if configured_destination_id is not None and destination_id != configured_destination_id:
        raise OffdeviceError(
            "Off-device destination identity mismatch.",
            error_code="OFFDEVICE_DESTINATION_ID_MISMATCH",
        )

    bundles = root / BUNDLES_DIRNAME
    if bundles.is_symlink() or not bundles.is_dir():
        raise OffdeviceError(
            "Off-device bundles directory is missing or unsafe.",
            error_code="OFFDEVICE_BUNDLES_UNSAFE",
        )
    try:
        bundles.resolve(strict=True).relative_to(root.resolve(strict=True))
    except (OSError, ValueError) as exc:
        raise OffdeviceError(
            "Off-device bundles directory escaped destination root.",
            error_code="OFFDEVICE_BUNDLES_UNSAFE",
        ) from exc
    bundles_stat = bundles.lstat()
    if not probe.bundles_uid_allowed(bundles_stat.st_uid):
        raise OffdeviceError(
            "Off-device bundles directory ownership is unsafe.",
            error_code="OFFDEVICE_BUNDLES_UNSAFE",
        )
    if bundles_stat.st_mode & (stat.S_IWOTH | stat.S_ISVTX) == stat.S_IWOTH:
        raise OffdeviceError(
            "Off-device bundles directory mode is unsafe.",
            error_code="OFFDEVICE_BUNDLES_UNSAFE",
        )
    if bundles_stat.st_mode & stat.S_IWOTH:
        raise OffdeviceError(
            "Off-device bundles directory mode is unsafe.",
            error_code="OFFDEVICE_BUNDLES_UNSAFE",
        )
    return ValidatedOffdeviceDestination(
        root=root,
        bundles_dir=bundles,
        destination_id=destination_id,
    )


def publish_or_reuse_offdevice_bundle(
    *,
    source_bundle: Path,
    source_identity: BundleIdentity,
    destination: ValidatedOffdeviceDestination,
    hooks: OffdeviceOsHooks | None = None,
) -> tuple[Path, bool]:
    """Copy and atomically publish, or idempotently reuse an exact final bundle."""
    probe = hooks or OffdeviceOsHooks()
    rename = probe.rename_noreplace or rename_noreplace
    final_path = destination.bundles_dir / source_identity.bundle_id
    _assert_contained(final_path, destination.bundles_dir)

    if final_path.exists() or final_path.is_symlink():
        if final_path.is_symlink() or not final_path.is_dir():
            raise OffdeviceError(
                "Off-device destination bundle conflicts.",
                error_code="OFFDEVICE_COPY_CONFLICT",
            )
        try:
            existing = capture_bundle_identity(
                final_path,
                bundle_id=source_identity.bundle_id,
            )
        except BackupError as exc:
            raise OffdeviceError(
                "Off-device destination bundle conflicts.",
                error_code="OFFDEVICE_COPY_CONFLICT",
            ) from exc
        if not identities_match(existing, source_identity):
            raise OffdeviceError(
                "Off-device destination bundle conflicts.",
                error_code="OFFDEVICE_COPY_CONFLICT",
            )
        return final_path, True

    stage = destination.bundles_dir / (
        f"{STAGE_PREFIX}{source_identity.bundle_id}.{secrets.token_hex(8)}"
    )
    _assert_contained(stage, destination.bundles_dir)
    try:
        _cleanup_owned_stage(stage, destination.bundles_dir)
        stage.mkdir(mode=0o700)
        _chmod_private_dir(stage)
        _copy_regular_file(
            source_bundle / MANIFEST_NAME,
            stage / MANIFEST_NAME,
            expected_sha256=source_identity.manifest_sha256,
            expected_size=source_identity.manifest_size_bytes,
            hooks=probe,
        )
        _copy_regular_file(
            source_bundle / CATALOG_NAME,
            stage / CATALOG_NAME,
            expected_sha256=source_identity.catalog_sha256,
            expected_size=source_identity.catalog_size_bytes,
            hooks=probe,
        )
        staged_identity = capture_bundle_identity(
            stage,
            bundle_id=source_identity.bundle_id,
        )
        if not identities_match(staged_identity, source_identity):
            raise OffdeviceError(
                "Staged off-device bundle identity mismatch.",
                error_code="OFFDEVICE_STAGE_IDENTITY_MISMATCH",
            )
        fsync_directory(stage, fsync=probe.fsync)
        fsync_directory(destination.bundles_dir, fsync=probe.fsync)
        try:
            rename(stage, final_path)
        except OffdeviceError:
            raise
        except TransferError as exc:
            raise OffdeviceError(
                "Atomic off-device publication is unsupported.",
                error_code="OFFDEVICE_ATOMIC_PUBLISH_UNSUPPORTED",
            ) from exc
        except FileExistsError as exc:
            raise OffdeviceError(
                "Off-device destination bundle conflicts.",
                error_code="OFFDEVICE_COPY_CONFLICT",
            ) from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise OffdeviceError(
                    "Off-device destination bundle conflicts.",
                    error_code="OFFDEVICE_COPY_CONFLICT",
                ) from exc
            if exc.errno in {errno.ENOSYS, errno.EINVAL}:
                raise OffdeviceError(
                    "Atomic off-device publication is unsupported.",
                    error_code="OFFDEVICE_ATOMIC_PUBLISH_UNSUPPORTED",
                ) from exc
            if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise OffdeviceError(
                    "Off-device destination has insufficient space.",
                    error_code="OFFDEVICE_DESTINATION_FULL",
                ) from exc
            raise OffdeviceError(
                "Off-device bundle could not be published.",
                error_code="OFFDEVICE_PUBLISH_FAILED",
            ) from exc
        stage = Path()  # published; do not remove final
        fsync_directory(destination.bundles_dir, fsync=probe.fsync)
        final_identity = capture_bundle_identity(
            final_path,
            bundle_id=source_identity.bundle_id,
        )
        if not identities_match(final_identity, source_identity):
            raise OffdeviceError(
                "Published off-device bundle identity mismatch.",
                error_code="OFFDEVICE_FINAL_IDENTITY_MISMATCH",
            )
        return final_path, False
    finally:
        if stage != Path() and stage.exists():
            _cleanup_owned_stage(stage, destination.bundles_dir)


def rename_noreplace(source: Path, destination: Path) -> None:
    """Atomically rename with Linux RENAME_NOREPLACE semantics."""
    try:
        transfer_rename_noreplace(source, destination)
    except TransferError as exc:
        raise OffdeviceError(
            "Atomic off-device publication is unsupported.",
            error_code="OFFDEVICE_ATOMIC_PUBLISH_UNSUPPORTED",
        ) from exc


def derive_offdevice_readiness(
    *,
    configured: bool,
    status: Mapping[str, Any],
    destination_health: str,
    now: datetime | None = None,
    lock_held_elsewhere: bool = False,
) -> OffdeviceReadiness:
    """Derive sanitized off-device readiness for operator status."""
    if not configured:
        return "disabled"
    if lock_held_elsewhere or status.get("current_operation") == "run-offdevice":
        return "busy"
    if status.get("current_operation"):
        return "busy"
    if destination_health != "ok":
        return "unavailable"
    success = status.get("last_successful_offdevice_copy_and_restore")
    if not isinstance(success, dict) or not success.get("completed_at_utc"):
        attempt = status.get("last_offdevice_attempt")
        if isinstance(attempt, dict) and attempt.get("state") == "failed":
            return "failed"
        return "never_verified"
    completed_at = _parse_utc(str(success["completed_at_utc"]))
    clock = now or datetime.now(UTC)
    readiness: OffdeviceReadiness = (
        "stale" if clock - completed_at > OFFDEVICE_STALE_AFTER else "ready"
    )
    attempt = status.get("last_offdevice_attempt")
    if isinstance(attempt, dict) and attempt.get("state") == "failed":
        attempt_at = attempt.get("completed_at_utc") or attempt.get("started_at_utc")
        success_at = success.get("completed_at_utc")
        if isinstance(attempt_at, str) and isinstance(success_at, str) and attempt_at > success_at:
            return "failed"
        attempt_seq = attempt.get("attempt_seq")
        success_seq = success.get("attempt_seq")
        if (
            isinstance(attempt_seq, int)
            and isinstance(success_seq, int)
            and attempt_seq > success_seq
        ):
            return "failed"
    return readiness


def build_sanitized_offdevice_status(
    *,
    configured: bool,
    readiness: OffdeviceReadiness,
    destination_health: str,
    status: Mapping[str, Any],
    local_recovery_point: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Build the additive sanitized off_device status block."""
    success = status.get("last_successful_offdevice_copy_and_restore")
    attempt = status.get("last_offdevice_attempt")
    pending = status.get("offdevice_pending_cleanup")
    payload = {
        "configured": configured,
        "readiness": readiness,
        "destination_health": destination_health,
        "local_recovery_point": _sanitize_recovery_point(local_recovery_point),
        "last_attempt": _sanitize_attempt(attempt if isinstance(attempt, dict) else None),
        "last_successful_copy_and_restore": _sanitize_success(
            success if isinstance(success, dict) else None
        ),
        "pending_cleanup": _sanitize_pending(pending if isinstance(pending, dict) else None),
    }
    _assert_no_sensitive_leak(payload)
    return payload


def inspect_destination_health(
    *,
    configured: bool,
    configured_destination_id: str | None,
    local_backup_root: Path,
    hooks: OffdeviceOsHooks | None = None,
    destination_root: Path = DEFAULT_OFFDEVICE_ROOT,
) -> str:
    """Return sanitized destination health without leaking host identifiers."""
    if not configured:
        return "unconfigured"
    try:
        validate_offdevice_destination(
            destination_root=destination_root,
            configured_destination_id=configured_destination_id,
            local_backup_root=local_backup_root,
            hooks=hooks,
        )
    except OffdeviceError as exc:
        if exc.error_code in {
            "OFFDEVICE_DESTINATION_UNAVAILABLE",
            "OFFDEVICE_DESTINATION_NOT_MOUNT",
            "OFFDEVICE_DESTINATION_SAME_DEVICE",
            "OFFDEVICE_MARKER_INVALID",
            "OFFDEVICE_MARKER_MALFORMED",
            "OFFDEVICE_MARKER_UNSUPPORTED",
            "OFFDEVICE_MARKER_PURPOSE_MISMATCH",
            "OFFDEVICE_MARKER_ID_INVALID",
            "OFFDEVICE_DESTINATION_ID_MISMATCH",
            "OFFDEVICE_MARKER_UNSAFE",
            "OFFDEVICE_BUNDLES_UNSAFE",
            "OFFDEVICE_DESTINATION_INVALID",
        }:
            if exc.error_code in {
                "OFFDEVICE_DESTINATION_UNAVAILABLE",
                "OFFDEVICE_DESTINATION_NOT_MOUNT",
            }:
                return "missing"
            return "unsafe"
        return "unsafe"
    except Exception:
        return "unsafe"
    return "ok"


def _copy_regular_file(
    source: Path,
    destination: Path,
    *,
    expected_sha256: str,
    expected_size: int,
    hooks: OffdeviceOsHooks,
) -> None:
    source_file = _require_regular_file(source, description="source file")
    if destination.exists() or destination.is_symlink():
        raise OffdeviceError(
            "Off-device staging path already exists.",
            error_code="OFFDEVICE_STAGE_EXISTS",
        )
    try:
        with source_file.open("rb") as src, open(
            destination,
            "xb",
            buffering=0,
        ) as dst:
            shutil.copyfileobj(src, dst, length=1024 * 1024)
            dst.flush()
            hooks.fsync(dst.fileno())
        os.chmod(destination, 0o600)
    except FileExistsError as exc:
        raise OffdeviceError(
            "Off-device staging path already exists.",
            error_code="OFFDEVICE_STAGE_EXISTS",
        ) from exc
    except OSError as exc:
        if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
            raise OffdeviceError(
                "Off-device destination has insufficient space.",
                error_code="OFFDEVICE_DESTINATION_FULL",
            ) from exc
        raise OffdeviceError(
            "Off-device file copy failed.",
            error_code="OFFDEVICE_COPY_FAILED",
        ) from exc
    if destination.stat().st_size != expected_size:
        raise OffdeviceError(
            "Copied off-device file size mismatch.",
            error_code="OFFDEVICE_COPY_SIZE_MISMATCH",
        )
    if sha256_file(destination) != expected_sha256:
        raise OffdeviceError(
            "Copied off-device file checksum mismatch.",
            error_code="OFFDEVICE_COPY_CHECKSUM_MISMATCH",
        )


def _cleanup_owned_stage(stage: Path, bundles_dir: Path) -> None:
    """Remove only an exact FrameNest-owned staging directory after validation."""
    if not stage.exists() and not stage.is_symlink():
        return
    _assert_contained(stage, bundles_dir)
    if not stage.name.startswith(ACCEPTED_STAGE_PREFIXES):
        raise OffdeviceError(
            "Unexpected off-device staging object.",
            error_code="OFFDEVICE_STAGE_UNSAFE",
        )
    if stage.is_symlink() or not stage.is_dir():
        raise OffdeviceError(
            "Unexpected off-device staging object.",
            error_code="OFFDEVICE_STAGE_UNSAFE",
        )
    children = list(stage.iterdir())
    for child in children:
        if child.is_symlink() or not child.is_file():
            raise OffdeviceError(
                "Unexpected off-device staging object.",
                error_code="OFFDEVICE_STAGE_UNSAFE",
            )
        if child.name not in {MANIFEST_NAME, CATALOG_NAME}:
            raise OffdeviceError(
                "Unexpected off-device staging object.",
                error_code="OFFDEVICE_STAGE_UNSAFE",
            )
    for child in children:
        child.unlink()
    stage.rmdir()


def _require_regular_file(path: Path, *, description: str) -> Path:
    if path.is_symlink() or not path.is_file():
        raise OffdeviceError(
            f"Invalid {description}.",
            error_code="OFFDEVICE_PATH_INVALID",
        )
    return path


def _assert_contained(path: Path, root: Path) -> None:
    try:
        resolved = path.resolve(strict=False)
        root_resolved = root.resolve(strict=False)
        resolved.relative_to(root_resolved)
    except (OSError, ValueError) as exc:
        raise OffdeviceError(
            "Off-device path escaped trusted layout.",
            error_code="OFFDEVICE_PATH_ESCAPE",
        ) from exc
    if path.is_absolute() and ".." in path.parts:
        raise OffdeviceError(
            "Off-device path escaped trusted layout.",
            error_code="OFFDEVICE_PATH_ESCAPE",
        )


def _chmod_private_dir(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise OffdeviceError(
            "Off-device permissions could not be restricted.",
            error_code="OFFDEVICE_PERMISSION_FAILED",
        ) from exc


def _sanitize_recovery_point(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "bundle_id": value.get("bundle_id"),
        "catalog_sha256": value.get("catalog_sha256"),
        "alembic_revision": value.get("alembic_revision"),
        "catalog_size_bytes": value.get("catalog_size_bytes"),
        "completed_at_utc": value.get("completed_at_utc"),
    }


def _sanitize_attempt(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "started_at_utc": value.get("started_at_utc"),
        "completed_at_utc": value.get("completed_at_utc"),
        "state": value.get("state"),
        "operation": value.get("operation"),
        "bundle_id": value.get("bundle_id"),
        "error_code": value.get("error_code"),
        "attempt_seq": value.get("attempt_seq"),
    }


def _sanitize_success(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "bundle_id": value.get("bundle_id"),
        "completed_at_utc": value.get("completed_at_utc"),
        "catalog_sha256": value.get("catalog_sha256"),
        "alembic_revision": value.get("alembic_revision"),
        "catalog_size_bytes": value.get("catalog_size_bytes"),
        "reused_existing": value.get("reused_existing"),
        "attempt_seq": value.get("attempt_seq"),
        "semantic": value.get("semantic"),
    }


def _sanitize_pending(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        "path_kind": value.get("path_kind"),
        "bundle_id": value.get("bundle_id"),
        "at_utc": value.get("at_utc"),
    }


def _assert_no_sensitive_leak(payload: Mapping[str, Any]) -> None:
    encoded = json.dumps(payload, sort_keys=True)
    if str(DEFAULT_OFFDEVICE_ROOT) in encoded:
        raise OffdeviceError(
            "Off-device status sanitization failed.",
            error_code="OFFDEVICE_STATUS_SANITIZATION_FAILED",
        )

    forbidden_keys = {"destination_id", "st_dev", "device_id", "mount_uuid", "device_number"}

    def _walk(node: object) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                if key in forbidden_keys:
                    raise OffdeviceError(
                        "Off-device status sanitization failed.",
                        error_code="OFFDEVICE_STATUS_SANITIZATION_FAILED",
                    )
                _walk(value)
        elif isinstance(node, list):
            for item in node:
                _walk(item)

    _walk(payload)


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
