"""Scheduled catalog backup orchestration, retention, and operator state."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import fcntl
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import tempfile
import threading
from collections.abc import Iterator, Mapping
from typing import Any, Literal

from kronika.infrastructure.persistence.catalog_backup import (
    CATALOG_NAME,
    MANIFEST_NAME,
    BackupError,
    BackupResult,
    create_catalog_backup,
    restore_catalog_backup,
    sha256_file,
    verify_catalog_backup,
)
from kronika.infrastructure.persistence.catalog_backup_offdevice import (
    DEFAULT_OFFDEVICE_ROOT,
    OffdeviceCopyResult,
    OffdeviceError,
    OffdeviceOsHooks,
    build_sanitized_offdevice_status,
    derive_offdevice_readiness,
    inspect_destination_health,
    parse_configured_destination_id,
    publish_or_reuse_offdevice_bundle,
    validate_offdevice_destination,
)
from kronika.infrastructure.persistence.catalog_backup_transfer import (
    BundleIdentity,
    capture_bundle_identity,
    identities_match,
    write_protocol_v1_stream,
)
from kronika.identity_env import (
    IdentityEnvironmentConflictError,
    IdentityEnvironmentConflictFailure,
    lookup_env,
)

DEFAULT_BACKUP_ROOT = Path("/var/lib/framenest/catalog-backups")
DEFAULT_RESTORE_VERIFY_ROOT = Path("/var/lib/framenest/catalog-restore-verify")
DEFAULT_OPS_ROOT = Path("/var/lib/framenest/catalog-backup-ops")
DEFAULT_DATABASE_PATH = Path("/var/lib/framenest/catalog.sqlite3")
DEFAULT_KEEP_AUTO = 30
MIN_KEEP_AUTO = 3
MAX_KEEP_AUTO = 3650
STATUS_SCHEMA_VERSION = 1
STALE_AFTER = timedelta(hours=48)
AUTO_NAME_PATTERN = re.compile(
    r"^auto-(\d{8}T\d{6}Z)-([0-9a-f]{8})$",
)
EVENTS_MAX_BYTES = 512 * 1024
EVENTS_KEEP_LINES = 200
SEMANTIC_OPTIONAL_TABLES = (
    ("logical_media", "logical_media_count"),
    ("physical_media_locations", "physical_media_locations_count"),
    ("libraries", "libraries_count"),
    ("media_byte_identities", "media_byte_identities_count"),
    ("media_catalog_removal_receipts", "media_catalog_removal_receipts_count"),
)

RestoreReadiness = Literal["busy", "never_verified", "failed", "stale", "ready"]
BundleClass = Literal["automatic", "pinned"]

_LOCK_STATE = threading.local()


class CatalogBackupOpsError(BackupError):
    """Sanitized catalog backup operations failure."""


class CatalogBackupIdentityEnvironmentConflictError(
    IdentityEnvironmentConflictFailure,
    CatalogBackupOpsError,
):
    """Backup-operations failure caused by one setting name set under both prefixes.

    The scheduled operations configuration resolves its own overrides through
    the same identity resolver as the settings boundary, so a conflicting pair
    is translated into this sanitized operations failure instead of escaping as
    an unhandled resolver error. The message names the two variable suffixes
    only.
    """


@dataclass(frozen=True, slots=True)
class CatalogBackupOpsConfig:
    """Validated operator configuration for automated catalog backups."""

    database_path: Path
    backup_root: Path
    restore_verify_root: Path
    ops_root: Path
    keep_auto: int


@dataclass(frozen=True, slots=True)
class SemanticSnapshot:
    """Bounded semantic facts read from a restored catalog."""

    alembic_revision: str
    counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class BundleSummary:
    """Operator-visible bundle classification summary."""

    bundle_id: str
    classification: BundleClass
    complete: bool
    verification_eligible: bool
    retention_eligible: bool
    created_at_utc: str | None
    alembic_revision: str | None


@dataclass(frozen=True, slots=True)
class RetentionPlan:
    """Deterministic retain and expire sets for automatic bundles."""

    keep_auto: int
    retain: tuple[str, ...]
    expire: tuple[str, ...]
    pinned_count: int
    automatic_count: int


@dataclass(frozen=True, slots=True)
class ScheduledPipelineResult:
    """Result of one scheduled backup-and-restore pipeline."""

    bundle_id: str
    catalog_sha256: str
    alembic_revision: str
    catalog_size_bytes: int
    semantic: SemanticSnapshot
    retention: RetentionPlan
    expired: tuple[str, ...]
    pending_cleanup: bool


def run_offdevice_catalog_copy(
    config: CatalogBackupOpsConfig,
    *,
    now: datetime | None = None,
    hooks: OffdeviceOsHooks | None = None,
    destination_root: Path | None = None,
    environ: Mapping[str, str] | None = None,
) -> OffdeviceCopyResult:
    """Copy the latest verified scheduled recovery point to the off-device mount.

    ``destination_root`` defaults to the fixed scheduled mount. Tests may inject
    a disposable root with matching OS hooks. Operator CLI never accepts an
    arbitrary destination argument.
    """
    clock = now or datetime.now(UTC)
    configured_id = parse_configured_destination_id(environ)
    if configured_id is None:
        raise OffdeviceError(
            "Off-device catalog copy is disabled.",
            error_code="OFFDEVICE_DISABLED",
        )
    root = destination_root if destination_root is not None else DEFAULT_OFFDEVICE_ROOT
    with operation_lock(config) as held:
        if not held:
            raise CatalogBackupOpsError(
                "Another catalog backup operation is in progress.",
                error_code="BACKUP_OPS_BUSY",
            )
        _ensure_ops_layout(config)
        status = _load_status(config)
        attempt_seq = _allocate_offdevice_attempt_seq(status)
        status["current_operation"] = "run-offdevice"
        status["last_offdevice_attempt"] = {
            "started_at_utc": _format_utc(clock),
            "state": "running",
            "operation": "run-offdevice",
            "attempt_seq": attempt_seq,
        }
        _write_status(config, status)
        _append_event(
            config,
            {
                "event": "offdevice_attempt_started",
                "at_utc": _format_utc(clock),
                "attempt_seq": attempt_seq,
            },
        )
        try:
            result = _run_offdevice_body(
                config,
                clock=clock,
                attempt_seq=attempt_seq,
                configured_destination_id=configured_id,
                destination_root=root,
                hooks=hooks,
            )
            if result.pending_cleanup:
                raise CatalogBackupOpsError(
                    "Disposable restore cleanup is pending.",
                    error_code="PENDING_CLEANUP",
                )
            return result
        except OffdeviceError as exc:
            _record_offdevice_failure(
                config,
                error_code=exc.error_code,
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise
        except CatalogBackupOpsError as exc:
            if exc.error_code == "PENDING_CLEANUP":
                raise
            _record_offdevice_failure(
                config,
                error_code=exc.error_code,
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise
        except BackupError as exc:
            _record_offdevice_failure(
                config,
                error_code=getattr(exc, "error_code", "BACKUP_FAILED"),
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise
        except Exception:
            _record_offdevice_failure(
                config,
                error_code="OFFDEVICE_FAILED",
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise OffdeviceError(
                "Off-device catalog copy failed.",
                error_code="OFFDEVICE_FAILED",
            )
        finally:
            status = _load_status(config)
            if status.get("current_operation") == "run-offdevice":
                status["current_operation"] = None
                _write_status(config, status)


def load_catalog_backup_ops_config(
    environ: Mapping[str, str] | None = None,
) -> CatalogBackupOpsConfig:
    """Load and validate operator configuration from process environment."""
    env = os.environ if environ is None else environ
    try:
        database_path = _absolute_path_from_env(
            lookup_env("DATABASE_PATH", environ=env),
            default=DEFAULT_DATABASE_PATH,
            description="database path",
        )
        backup_root = _absolute_path_from_env(
            lookup_env("CATALOG_BACKUP_ROOT", environ=env),
            default=DEFAULT_BACKUP_ROOT,
            description="catalog backup root",
        )
        restore_verify_root = _absolute_path_from_env(
            lookup_env("CATALOG_RESTORE_VERIFY_ROOT", environ=env),
            default=DEFAULT_RESTORE_VERIFY_ROOT,
            description="catalog restore verification root",
        )
        ops_root = _absolute_path_from_env(
            lookup_env("CATALOG_BACKUP_OPS_ROOT", environ=env),
            default=DEFAULT_OPS_ROOT,
            description="catalog backup operator-state root",
        )
        keep_auto = _parse_keep_auto(lookup_env("CATALOG_BACKUP_KEEP_AUTO", environ=env))
    except IdentityEnvironmentConflictError as exc:
        raise CatalogBackupIdentityEnvironmentConflictError(str(exc)) from exc
    config = CatalogBackupOpsConfig(
        database_path=database_path,
        backup_root=backup_root,
        restore_verify_root=restore_verify_root,
        ops_root=ops_root,
        keep_auto=keep_auto,
    )
    _validate_config_roots(config)
    return config


def parse_keep_auto(raw: object) -> int:
    """Parse KEEP_AUTO with fail-closed validation."""
    return _parse_keep_auto(raw)


def is_automatic_bundle_name(name: str) -> bool:
    """Return True when the name matches the accepted auto- bundle form."""
    return AUTO_NAME_PATTERN.fullmatch(name) is not None


@dataclass(frozen=True, slots=True)
class SelectedScheduledRecoveryPoint:
    """Authoritative latest successful scheduled recovery point."""

    bundle_path: Path
    identity: BundleIdentity
    semantic: Mapping[str, Any]
    recorded_success: Mapping[str, Any]


def select_latest_successful_scheduled_recovery_point(
    config: CatalogBackupOpsConfig,
) -> SelectedScheduledRecoveryPoint:
    """Select and strictly verify the ledgered successful scheduled recovery point.

    This is the single shared definition of "latest successful scheduled backup"
    used by mounted off-device copy and workstation export.
    """
    status = _load_status(config)
    success = status.get("last_successful_scheduled_backup_and_restore")
    if not isinstance(success, dict):
        raise CatalogBackupOpsError(
            "No verified scheduled catalog recovery point is available.",
            error_code="SCHEDULED_SOURCE_UNAVAILABLE",
        )
    bundle_id = success.get("bundle_id")
    if not isinstance(bundle_id, str) or not is_automatic_bundle_name(bundle_id):
        raise CatalogBackupOpsError(
            "Scheduled recovery point identity is invalid.",
            error_code="SCHEDULED_SOURCE_INVALID",
        )
    ledger_ids = {
        entry["bundle_id"]
        for entry in _ledger_entries(status)
        if isinstance(entry.get("bundle_id"), str)
    }
    if bundle_id not in ledger_ids:
        raise CatalogBackupOpsError(
            "Scheduled recovery point is not ledgered.",
            error_code="SCHEDULED_SOURCE_NOT_LEDGERED",
        )
    source_bundle = config.backup_root / bundle_id
    if source_bundle.is_symlink() or not source_bundle.is_dir():
        raise CatalogBackupOpsError(
            "Scheduled recovery point is missing.",
            error_code="SCHEDULED_SOURCE_MISSING",
        )
    verified = verify_catalog_backup(source_bundle)
    expected_sha = success.get("catalog_sha256")
    expected_revision = success.get("alembic_revision")
    expected_size = success.get("catalog_size_bytes")
    if (
        verified.catalog_sha256 != expected_sha
        or verified.alembic_revision != expected_revision
        or verified.catalog_size_bytes != expected_size
    ):
        raise CatalogBackupOpsError(
            "Scheduled recovery point no longer matches recorded evidence.",
            error_code="SCHEDULED_SOURCE_EVIDENCE_MISMATCH",
        )
    identity = capture_bundle_identity(source_bundle, bundle_id=bundle_id)
    catalog_path = source_bundle / CATALOG_NAME
    live_semantic = capture_semantic_snapshot(catalog_path)
    semantic_payload = _semantic_payload(live_semantic)
    recorded_semantic = success.get("semantic")
    if isinstance(recorded_semantic, dict):
        recorded_counts = recorded_semantic.get("counts")
        if isinstance(recorded_counts, dict) and dict(recorded_counts) != dict(
            semantic_payload["counts"]
        ):
            raise CatalogBackupOpsError(
                "Scheduled recovery point no longer matches recorded evidence.",
                error_code="SCHEDULED_SOURCE_EVIDENCE_MISMATCH",
            )
        recorded_revision = recorded_semantic.get("alembic_revision")
        if (
            isinstance(recorded_revision, str)
            and recorded_revision != semantic_payload["alembic_revision"]
        ):
            raise CatalogBackupOpsError(
                "Scheduled recovery point no longer matches recorded evidence.",
                error_code="SCHEDULED_SOURCE_EVIDENCE_MISMATCH",
            )
    return SelectedScheduledRecoveryPoint(
        bundle_path=source_bundle,
        identity=identity,
        semantic=semantic_payload,
        recorded_success=success,
    )


def export_latest_scheduled_recovery_point(
    config: CatalogBackupOpsConfig,
    stdout: Any,
) -> BundleIdentity:
    """Stream protocol-v1 bytes for the authoritative scheduled recovery point.

    Acquires the shared backup operation lock for selection, verification,
    streaming, and terminal source re-verification. Does not mutate scheduled
    success status, retention, or backup roots. Writes protocol bytes only to
    ``stdout``.
    """
    with operation_lock(config) as held:
        if not held:
            raise CatalogBackupOpsError(
                "Another catalog backup operation is in progress.",
                error_code="BACKUP_OPS_BUSY",
            )
        _ensure_ops_layout(config)
        selected = select_latest_successful_scheduled_recovery_point(config)
        write_protocol_v1_stream(
            stdout,
            identity=selected.identity,
            semantic=selected.semantic,
            manifest_path=selected.bundle_path / MANIFEST_NAME,
            catalog_path=selected.bundle_path / CATALOG_NAME,
        )
        revalidated = select_latest_successful_scheduled_recovery_point(config)
        if not identities_match(selected.identity, revalidated.identity):
            raise CatalogBackupOpsError(
                "Scheduled recovery point changed during export.",
                error_code="SCHEDULED_SOURCE_CHANGED",
            )
        if dict(selected.semantic) != dict(revalidated.semantic):
            raise CatalogBackupOpsError(
                "Scheduled recovery point changed during export.",
                error_code="SCHEDULED_SOURCE_CHANGED",
            )
        return selected.identity


def derive_restore_readiness(
    *,
    status: Mapping[str, Any],
    now: datetime | None = None,
    lock_held_elsewhere: bool = False,
) -> RestoreReadiness:
    """Derive restore-readiness from durable operator status."""
    if lock_held_elsewhere or status.get("current_operation"):
        return "busy"
    success = status.get("last_successful_scheduled_backup_and_restore")
    if not isinstance(success, dict) or not success.get("completed_at_utc"):
        attempt = status.get("last_scheduled_attempt")
        if isinstance(attempt, dict) and attempt.get("state") == "failed":
            return "failed"
        return "never_verified"
    completed_at = _parse_utc(str(success["completed_at_utc"]))
    clock = now or datetime.now(UTC)
    if clock - completed_at > STALE_AFTER:
        readiness: RestoreReadiness = "stale"
    else:
        readiness = "ready"
    attempt = status.get("last_scheduled_attempt")
    if (
        isinstance(attempt, dict)
        and attempt.get("state") == "failed"
        and _attempt_after_success(attempt, success)
    ):
        return "failed"
    return readiness


def run_scheduled_catalog_backup(
    config: CatalogBackupOpsConfig,
    *,
    now: datetime | None = None,
) -> ScheduledPipelineResult:
    """Execute the full daily create, verify, restore, and retention pipeline."""
    clock = now or datetime.now(UTC)
    with operation_lock(config) as held:
        if not held:
            raise CatalogBackupOpsError(
                "Another catalog backup operation is in progress.",
                error_code="BACKUP_OPS_BUSY",
            )
        _ensure_ops_layout(config)
        status = _load_status(config)
        attempt_seq = _allocate_scheduled_attempt_seq(status)
        status["scheduled_attempt_seq"] = attempt_seq
        status["current_operation"] = "run-scheduled"
        status["last_scheduled_attempt"] = {
            "started_at_utc": _format_utc(clock),
            "state": "running",
            "operation": "run-scheduled",
            "attempt_seq": attempt_seq,
        }
        _write_status(config, status)
        _append_event(
            config,
            {
                "event": "scheduled_attempt_started",
                "at_utc": _format_utc(clock),
                "attempt_seq": attempt_seq,
            },
        )
        pending_cleanup = False
        try:
            result = _run_scheduled_body(config, clock=clock, attempt_seq=attempt_seq)
            pending_cleanup = result.pending_cleanup
            if pending_cleanup:
                raise CatalogBackupOpsError(
                    "Disposable restore cleanup is pending.",
                    error_code="PENDING_CLEANUP",
                )
            return result
        except CatalogBackupOpsError as exc:
            if exc.error_code == "PENDING_CLEANUP":
                raise
            _record_scheduled_failure(
                config,
                error_code=exc.error_code,
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise
        except BackupError as exc:
            _record_scheduled_failure(
                config,
                error_code=getattr(exc, "error_code", "BACKUP_FAILED"),
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise
        except Exception:
            _record_scheduled_failure(
                config,
                error_code="BACKUP_OPS_FAILED",
                clock=clock,
                attempt_seq=attempt_seq,
            )
            raise CatalogBackupOpsError(
                "Scheduled catalog backup failed.",
                error_code="BACKUP_OPS_FAILED",
            )
        finally:
            status = _load_status(config)
            if status.get("current_operation") == "run-scheduled":
                status["current_operation"] = None
                _write_status(config, status)


def verify_restore_bundle(
    config: CatalogBackupOpsConfig,
    bundle: Path | str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Manually restore-verify one selected bundle into a disposable destination."""
    clock = now or datetime.now(UTC)
    with operation_lock(config) as held:
        if not held:
            raise CatalogBackupOpsError(
                "Another catalog backup operation is in progress.",
                error_code="BACKUP_OPS_BUSY",
            )
        _ensure_ops_layout(config)
        status = _load_status(config)
        status["current_operation"] = "verify-restore"
        _write_status(config, status)
        try:
            bundle_path = _existing_bundle_under_root(config.backup_root, bundle)
            verified = verify_catalog_backup(bundle_path)
            semantic, disposable, pending_cleanup = _restore_and_semantic(
                config,
                bundle_path,
                verified,
                clock=clock,
            )
            evidence = {
                "operation": "verify-restore",
                "bundle_id": bundle_path.name,
                "completed_at_utc": _format_utc(clock),
                "catalog_sha256": verified.catalog_sha256,
                "alembic_revision": verified.alembic_revision,
                "catalog_size_bytes": verified.catalog_size_bytes,
                "semantic": _semantic_payload(semantic),
                "pending_cleanup": pending_cleanup,
            }
            status = _load_status(config)
            status["last_manual_verify_restore"] = evidence
            if pending_cleanup:
                status["pending_cleanup"] = {
                    "path_kind": "disposable_restore",
                    "bundle_id": bundle_path.name,
                    "at_utc": _format_utc(clock),
                }
            else:
                if status.get("pending_cleanup"):
                    pending = status["pending_cleanup"]
                    if (
                        isinstance(pending, dict)
                        and pending.get("bundle_id") == bundle_path.name
                    ):
                        status["pending_cleanup"] = None
            status["current_operation"] = None
            _write_status(config, status)
            _append_event(
                config,
                {
                    "event": "manual_verify_restore_succeeded",
                    "at_utc": _format_utc(clock),
                    "bundle_id": bundle_path.name,
                },
            )
            if pending_cleanup:
                raise CatalogBackupOpsError(
                    "Disposable restore cleanup is pending.",
                    error_code="PENDING_CLEANUP",
                )
            return evidence
        except Exception:
            status = _load_status(config)
            status["current_operation"] = None
            _write_status(config, status)
            raise
        finally:
            status = _load_status(config)
            if status.get("current_operation") == "verify-restore":
                status["current_operation"] = None
                _write_status(config, status)


def build_retention_plan(
    config: CatalogBackupOpsConfig,
    *,
    verify: bool = True,
) -> RetentionPlan:
    """Compute deterministic retain and expire sets without deleting."""
    _ensure_ops_layout(config)
    _validate_safe_directory_root(config.backup_root, description="catalog backup root")
    status = _load_status(config)
    ledger_ids = {
        entry["bundle_id"]
        for entry in _ledger_entries(status)
        if isinstance(entry.get("bundle_id"), str)
    }
    summaries = list_bundle_summaries(config, verify=verify, ledger_ids=ledger_ids)
    automatic_verified = [
        summary
        for summary in summaries
        if summary.classification == "automatic"
        and summary.verification_eligible
        and summary.bundle_id in ledger_ids
    ]
    automatic_verified.sort(key=_bundle_sort_key, reverse=True)
    pinned_count = sum(1 for summary in summaries if summary.classification == "pinned")
    retain_list = [summary.bundle_id for summary in automatic_verified[: config.keep_auto]]
    retain_set = set(retain_list)
    floor_protected = {
        summary.bundle_id for summary in automatic_verified[:MIN_KEEP_AUTO]
    }
    newest = automatic_verified[0].bundle_id if automatic_verified else None
    expire: list[str] = []
    for summary in automatic_verified:
        bundle_id = summary.bundle_id
        if bundle_id in retain_set:
            continue
        if newest is not None and bundle_id == newest:
            continue
        if bundle_id in floor_protected:
            continue
        if len(automatic_verified) - len(expire) <= MIN_KEEP_AUTO:
            break
        if not summary.retention_eligible:
            continue
        expire.append(bundle_id)
    return RetentionPlan(
        keep_auto=config.keep_auto,
        retain=tuple(retain_list),
        expire=tuple(expire),
        pinned_count=pinned_count,
        automatic_count=len(automatic_verified),
    )


def expire_automatic_backups(
    config: CatalogBackupOpsConfig,
    *,
    apply: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Dry-run or apply safe expiration of eligible automatic bundles."""
    clock = now or datetime.now(UTC)
    with operation_lock(config) as held:
        if not held:
            raise CatalogBackupOpsError(
                "Another catalog backup operation is in progress.",
                error_code="BACKUP_OPS_BUSY",
            )
        plan = build_retention_plan(config, verify=True)
        payload: dict[str, Any] = {
            "operation": "expire",
            "mode": "apply" if apply else "dry-run",
            "keep_auto": plan.keep_auto,
            "retain": list(plan.retain),
            "expire": list(plan.expire),
            "deleted": [],
            "failed": None,
        }
        if not apply:
            _append_event(
                config,
                {
                    "event": "expire_dry_run",
                    "at_utc": _format_utc(clock),
                    "expire_count": len(plan.expire),
                },
            )
            return payload
        deleted: list[str] = []
        for bundle_id in plan.expire:
            revalidated = build_retention_plan(config, verify=True)
            if bundle_id not in revalidated.expire:
                continue
            try:
                _delete_eligible_bundle(config, bundle_id)
            except CatalogBackupOpsError as exc:
                payload["failed"] = {
                    "bundle_id": bundle_id,
                    "error_code": exc.error_code,
                }
                status = _load_status(config)
                status["recent_failure"] = {
                    "at_utc": _format_utc(clock),
                    "error_code": exc.error_code,
                    "operation": "expire",
                }
                _write_status(config, status)
                _append_event(
                    config,
                    {
                        "event": "expire_partial_failure",
                        "at_utc": _format_utc(clock),
                        "bundle_id": bundle_id,
                        "error_code": exc.error_code,
                    },
                )
                payload["deleted"] = deleted
                raise
            deleted.append(bundle_id)
            _remove_ledger_entry(config, bundle_id)
        payload["deleted"] = deleted
        _append_event(
            config,
            {
                "event": "expire_applied",
                "at_utc": _format_utc(clock),
                "deleted_count": len(deleted),
            },
        )
        return payload


def list_bundle_summaries(
    config: CatalogBackupOpsConfig,
    *,
    verify: bool = False,
    ledger_ids: set[str] | None = None,
) -> list[BundleSummary]:
    """List bundle summaries under the configured backup root."""
    _validate_safe_directory_root(config.backup_root, description="catalog backup root")
    if not config.backup_root.exists():
        return []
    status = _load_status(config)
    known_ledger = ledger_ids
    if known_ledger is None:
        known_ledger = {
            entry["bundle_id"]
            for entry in _ledger_entries(status)
            if isinstance(entry.get("bundle_id"), str)
        }
    summaries: list[BundleSummary] = []
    for child in sorted(config.backup_root.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        if child.is_symlink() or not child.is_dir():
            summaries.append(
                BundleSummary(
                    bundle_id=child.name,
                    classification=(
                        "automatic" if is_automatic_bundle_name(child.name) else "pinned"
                    ),
                    complete=False,
                    verification_eligible=False,
                    retention_eligible=False,
                    created_at_utc=None,
                    alembic_revision=None,
                )
            )
            continue
        classification: BundleClass = (
            "automatic" if is_automatic_bundle_name(child.name) else "pinned"
        )
        complete = _bundle_looks_complete(child)
        verification_eligible = False
        created_at_utc: str | None = None
        alembic_revision: str | None = None
        if complete and verify:
            try:
                verified = verify_catalog_backup(child)
                verification_eligible = True
                alembic_revision = verified.alembic_revision
                manifest = json.loads((child / MANIFEST_NAME).read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    value = manifest.get("created_at_utc")
                    if isinstance(value, str):
                        created_at_utc = value
            except BackupError:
                verification_eligible = False
        elif complete:
            try:
                manifest = json.loads((child / MANIFEST_NAME).read_text(encoding="utf-8"))
                if isinstance(manifest, dict):
                    value = manifest.get("created_at_utc")
                    if isinstance(value, str):
                        created_at_utc = value
                    catalog = manifest.get("catalog")
                    if isinstance(catalog, dict):
                        revision = catalog.get("alembic_revision")
                        if isinstance(revision, str):
                            alembic_revision = revision
            except (OSError, json.JSONDecodeError):
                complete = False
        retention_eligible = (
            classification == "automatic"
            and verification_eligible
            and child.name in known_ledger
        )
        summaries.append(
            BundleSummary(
                bundle_id=child.name,
                classification=classification,
                complete=complete,
                verification_eligible=verification_eligible if verify else complete,
                retention_eligible=retention_eligible if verify else False,
                created_at_utc=created_at_utc,
                alembic_revision=alembic_revision,
            )
        )
    return summaries


def read_operator_status(
    config: CatalogBackupOpsConfig,
    *,
    now: datetime | None = None,
    offdevice_hooks: OffdeviceOsHooks | None = None,
    offdevice_destination_root: Path | None = None,
    offdevice_environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Return sanitized operator status including derived readiness."""
    _ensure_ops_layout(config)
    status = _load_status(config)
    lock_busy = _lock_is_held(config)
    readiness = derive_restore_readiness(
        status=status,
        now=now,
        lock_held_elsewhere=lock_busy and not status.get("current_operation"),
    )
    if status.get("current_operation"):
        readiness = "busy"
    plan = build_retention_plan(config, verify=False)
    summaries = list_bundle_summaries(config, verify=False)
    automatic_count = sum(1 for item in summaries if item.classification == "automatic")
    pinned_count = sum(1 for item in summaries if item.classification == "pinned")
    newest_auto = None
    automatic_ids = [
        item.bundle_id for item in summaries if item.classification == "automatic" and item.complete
    ]
    if automatic_ids:
        automatic_ids.sort(reverse=True)
        newest_auto = automatic_ids[0]
    destination_health = "ok"
    try:
        _validate_safe_directory_root(config.backup_root, description="catalog backup root")
        if not config.backup_root.exists():
            destination_health = "missing"
    except CatalogBackupOpsError:
        destination_health = "unsafe"

    configured = False
    configured_destination_id: str | None = None
    offdevice_destination_health = "unconfigured"
    destination_root = (
        offdevice_destination_root
        if offdevice_destination_root is not None
        else DEFAULT_OFFDEVICE_ROOT
    )
    try:
        configured_destination_id = parse_configured_destination_id(offdevice_environ)
        configured = configured_destination_id is not None
        offdevice_destination_health = inspect_destination_health(
            configured=configured,
            configured_destination_id=configured_destination_id,
            local_backup_root=config.backup_root,
            hooks=offdevice_hooks,
            destination_root=destination_root,
        )
    except OffdeviceError:
        configured = True
        offdevice_destination_health = "unsafe"

    local_recovery = status.get("last_successful_scheduled_backup_and_restore")
    if not isinstance(local_recovery, dict):
        local_recovery = None
    off_device_readiness = derive_offdevice_readiness(
        configured=configured,
        status=status,
        destination_health=offdevice_destination_health if configured else "unconfigured",
        now=now,
        lock_held_elsewhere=lock_busy and not status.get("current_operation"),
    )
    off_device = build_sanitized_offdevice_status(
        configured=configured,
        readiness=off_device_readiness,
        destination_health=offdevice_destination_health,
        status=status,
        local_recovery_point=local_recovery,
    )
    return {
        "operation": "status",
        "restore_readiness": readiness,
        "current_operation": status.get("current_operation"),
        "busy": readiness == "busy",
        "last_scheduled_attempt": status.get("last_scheduled_attempt"),
        "last_successful_scheduled_backup": status.get("last_successful_scheduled_backup"),
        "last_successful_scheduled_restore_verification": status.get(
            "last_successful_scheduled_restore_verification"
        ),
        "last_successful_scheduled_backup_and_restore": status.get(
            "last_successful_scheduled_backup_and_restore"
        ),
        "last_manual_verify_restore": status.get("last_manual_verify_restore"),
        "newest_retained_automatic_recovery_point": newest_auto,
        "destination_health": destination_health,
        "retention": {
            "keep_auto": config.keep_auto,
            "automatic_bundle_count": automatic_count,
            "pinned_bundle_count": pinned_count,
            "planned_expire_count": len(plan.expire),
        },
        "pending_cleanup": status.get("pending_cleanup"),
        "recent_failure": status.get("recent_failure"),
        "next_scheduled_execution": None,
        "off_device": off_device,
    }


@contextmanager
def operation_lock(config: CatalogBackupOpsConfig) -> Iterator[bool]:
    """Acquire the shared exclusive non-blocking backup operations lock.

    The lock is process-aware and re-entrant within the same thread so that a
    CLI-held lock does not self-deadlock when orchestration later enters an
    already-protected ops function.
    """
    depth = getattr(_LOCK_STATE, "depth", 0)
    if depth > 0:
        _LOCK_STATE.depth = depth + 1
        try:
            yield True
        finally:
            _LOCK_STATE.depth = depth
        return

    _ensure_ops_layout(config)
    lock_path = config.ops_root / "catalog-backup.lock"
    _private_file(lock_path)
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        _LOCK_STATE.depth = 1
        try:
            yield True
        finally:
            _LOCK_STATE.depth = 0
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def allocate_automatic_bundle_name(clock: datetime | None = None) -> str:
    """Allocate a unique automatic bundle directory name."""
    moment = (clock or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    stamp = moment.strftime("%Y%m%dT%H%M%SZ")
    return f"auto-{stamp}-{secrets.token_hex(4)}"


def _verify_restored_database(database: Path) -> None:
    try:
        with sqlite3.connect(_sqlite_readonly_uri(database), uri=True) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                raise CatalogBackupOpsError(
                    "SQLite integrity check failed.",
                    error_code="SQLITE_INTEGRITY_FAILED",
                )
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_rows:
                raise CatalogBackupOpsError(
                    "SQLite foreign-key check failed.",
                    error_code="SQLITE_FOREIGN_KEY_FAILED",
                )
    except CatalogBackupOpsError:
        raise
    except sqlite3.Error as exc:
        raise CatalogBackupOpsError(
            "SQLite integrity check failed.",
            error_code="SQLITE_INTEGRITY_FAILED",
        ) from exc


def capture_semantic_snapshot(database: Path) -> SemanticSnapshot:
    """Capture bounded semantic facts from a catalog database."""
    try:
        with sqlite3.connect(_sqlite_readonly_uri(database), uri=True) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
            if row is None or not isinstance(row[0], str) or not row[0]:
                raise CatalogBackupOpsError(
                    "Catalog revision is unavailable.",
                    error_code="CATALOG_REVISION_UNAVAILABLE",
                )
            counts: dict[str, int] = {}
            for table_name, key in SEMANTIC_OPTIONAL_TABLES:
                if _table_exists(connection, table_name):
                    count_row = connection.execute(
                        f"SELECT COUNT(*) FROM {table_name}"  # noqa: S608 — fixed identifiers
                    ).fetchone()
                    counts[key] = int(count_row[0]) if count_row is not None else 0
    except CatalogBackupOpsError:
        raise
    except sqlite3.Error as exc:
        raise CatalogBackupOpsError(
            "Semantic catalog readback failed.",
            error_code="SEMANTIC_READBACK_FAILED",
        ) from exc
    return SemanticSnapshot(alembic_revision=row[0], counts=counts)


def _run_offdevice_body(
    config: CatalogBackupOpsConfig,
    *,
    clock: datetime,
    attempt_seq: int,
    configured_destination_id: str,
    destination_root: Path,
    hooks: OffdeviceOsHooks | None,
) -> OffdeviceCopyResult:
    try:
        selected = select_latest_successful_scheduled_recovery_point(config)
    except CatalogBackupOpsError as exc:
        raise _map_scheduled_source_to_offdevice(exc) from exc
    bundle_id = selected.identity.bundle_id
    source_bundle = selected.bundle_path
    source_identity = selected.identity
    destination = validate_offdevice_destination(
        destination_root=destination_root,
        configured_destination_id=configured_destination_id,
        local_backup_root=config.backup_root,
        hooks=hooks,
    )
    # Re-validate destination immediately before publication boundary.
    destination = validate_offdevice_destination(
        destination_root=destination_root,
        configured_destination_id=configured_destination_id,
        local_backup_root=config.backup_root,
        hooks=hooks,
    )
    published, reused = publish_or_reuse_offdevice_bundle(
        source_bundle=source_bundle,
        source_identity=source_identity,
        destination=destination,
        hooks=hooks,
    )
    # Destination identity must still hold after publication.
    validate_offdevice_destination(
        destination_root=destination_root,
        configured_destination_id=configured_destination_id,
        local_backup_root=config.backup_root,
        hooks=hooks,
    )
    final_verified = verify_catalog_backup(published)
    if (
        final_verified.catalog_sha256 != source_identity.catalog_sha256
        or final_verified.catalog_size_bytes != source_identity.catalog_size_bytes
        or final_verified.alembic_revision != source_identity.alembic_revision
    ):
        raise OffdeviceError(
            "Published off-device bundle identity mismatch.",
            error_code="OFFDEVICE_FINAL_IDENTITY_MISMATCH",
        )
    semantic, _disposable, pending_cleanup = _restore_and_semantic(
        config,
        published,
        final_verified,
        clock=clock,
    )
    completed_at = _format_utc(clock)
    success_payload = {
        "bundle_id": bundle_id,
        "completed_at_utc": completed_at,
        "catalog_sha256": final_verified.catalog_sha256,
        "alembic_revision": final_verified.alembic_revision,
        "catalog_size_bytes": final_verified.catalog_size_bytes,
        "reused_existing": reused,
        "semantic": _semantic_payload(semantic),
        "attempt_seq": attempt_seq,
    }
    status = _load_status(config)
    status["last_offdevice_attempt"] = {
        "started_at_utc": status.get("last_offdevice_attempt", {}).get("started_at_utc")
        if isinstance(status.get("last_offdevice_attempt"), dict)
        else None,
        "completed_at_utc": completed_at,
        "state": "succeeded",
        "operation": "run-offdevice",
        "bundle_id": bundle_id,
        "attempt_seq": attempt_seq,
    }
    status["last_successful_offdevice_copy_and_restore"] = success_payload
    status["offdevice_attempt_seq"] = attempt_seq
    if pending_cleanup:
        status["offdevice_pending_cleanup"] = {
            "path_kind": "disposable_restore",
            "bundle_id": bundle_id,
            "at_utc": completed_at,
        }
    else:
        status["offdevice_pending_cleanup"] = None
    status["current_operation"] = None
    _write_status(config, status)
    _append_event(
        config,
        {
            "event": "offdevice_copy_and_restore_succeeded",
            "at_utc": completed_at,
            "bundle_id": bundle_id,
            "attempt_seq": attempt_seq,
            "reused_existing": reused,
        },
    )
    return OffdeviceCopyResult(
        bundle_id=bundle_id,
        catalog_sha256=final_verified.catalog_sha256,
        alembic_revision=final_verified.alembic_revision,
        catalog_size_bytes=final_verified.catalog_size_bytes,
        reused_existing=reused,
        pending_cleanup=pending_cleanup,
        semantic=_semantic_payload(semantic),
    )


def _map_scheduled_source_to_offdevice(exc: CatalogBackupOpsError) -> OffdeviceError:
    mapping = {
        "SCHEDULED_SOURCE_UNAVAILABLE": "OFFDEVICE_SOURCE_UNAVAILABLE",
        "SCHEDULED_SOURCE_INVALID": "OFFDEVICE_SOURCE_INVALID",
        "SCHEDULED_SOURCE_NOT_LEDGERED": "OFFDEVICE_SOURCE_NOT_LEDGERED",
        "SCHEDULED_SOURCE_MISSING": "OFFDEVICE_SOURCE_MISSING",
        "SCHEDULED_SOURCE_EVIDENCE_MISMATCH": "OFFDEVICE_SOURCE_EVIDENCE_MISMATCH",
    }
    return OffdeviceError(
        str(exc),
        error_code=mapping.get(exc.error_code, "OFFDEVICE_SOURCE_UNAVAILABLE"),
    )


def _record_offdevice_failure(
    config: CatalogBackupOpsConfig,
    *,
    error_code: str,
    clock: datetime,
    attempt_seq: int | None = None,
) -> None:
    status = _load_status(config)
    previous = status.get("last_offdevice_attempt")
    previous_seq = None
    if isinstance(previous, dict):
        previous_seq = previous.get("attempt_seq")
    seq = attempt_seq if isinstance(attempt_seq, int) else previous_seq
    status["last_offdevice_attempt"] = {
        "started_at_utc": previous.get("started_at_utc") if isinstance(previous, dict) else None,
        "completed_at_utc": _format_utc(clock),
        "state": "failed",
        "operation": "run-offdevice",
        "error_code": error_code,
        "attempt_seq": seq,
    }
    if isinstance(seq, int) and seq > 0:
        status["offdevice_attempt_seq"] = max(
            _coerce_attempt_seq(status.get("offdevice_attempt_seq")) or 0,
            seq,
        )
    status["current_operation"] = None
    _write_status(config, status)
    _append_event(
        config,
        {
            "event": "offdevice_attempt_failed",
            "at_utc": _format_utc(clock),
            "error_code": error_code,
            "attempt_seq": seq,
        },
    )


def _allocate_offdevice_attempt_seq(status: Mapping[str, Any]) -> int:
    current = _coerce_attempt_seq(status.get("offdevice_attempt_seq")) or 0
    last = status.get("last_offdevice_attempt")
    if isinstance(last, dict):
        last_seq = _coerce_attempt_seq(last.get("attempt_seq"))
        if last_seq is not None:
            current = max(current, last_seq)
    success = status.get("last_successful_offdevice_copy_and_restore")
    if isinstance(success, dict):
        success_seq = _coerce_attempt_seq(success.get("attempt_seq"))
        if success_seq is not None:
            current = max(current, success_seq)
    return current + 1


def _run_scheduled_body(
    config: CatalogBackupOpsConfig,
    *,
    clock: datetime,
    attempt_seq: int,
) -> ScheduledPipelineResult:
    _validate_safe_directory_root(config.backup_root, description="catalog backup root")
    config.backup_root.mkdir(mode=0o750, parents=True, exist_ok=True)
    _private_directory(config.backup_root, mode=0o750)
    if config.database_path.is_symlink() or not config.database_path.is_file():
        raise CatalogBackupOpsError("Invalid source database.", error_code="INVALID_PATH")
    source_sha_before = sha256_file(config.database_path)

    bundle_id = allocate_automatic_bundle_name(clock)
    bundle_path = config.backup_root / bundle_id
    if bundle_path.exists() or bundle_path.is_symlink():
        bundle_id = allocate_automatic_bundle_name(clock)
        bundle_path = config.backup_root / bundle_id
        if bundle_path.exists() or bundle_path.is_symlink():
            raise CatalogBackupOpsError(
                "Automatic backup name collided.",
                error_code="OUTPUT_EXISTS",
            )

    created = create_catalog_backup(config.database_path, bundle_path)
    verified = verify_catalog_backup(bundle_path)
    if verified.catalog_sha256 != created.catalog_sha256:
        raise CatalogBackupOpsError(
            "Backup catalog checksum mismatch.",
            error_code="CATALOG_CHECKSUM_MISMATCH",
        )
    if not config.database_path.is_file():
        raise CatalogBackupOpsError("Source database missing after backup.", error_code="INVALID_PATH")
    if sha256_file(config.database_path) != source_sha_before:
        # Concurrent catalog writes may change the live file; the online backup
        # snapshot remains the recovery point. Require only that the source path
        # remains a regular file.
        pass

    semantic, _disposable, pending_cleanup = _restore_and_semantic(
        config,
        bundle_path,
        verified,
        clock=clock,
    )
    if semantic.alembic_revision != verified.alembic_revision:
        raise CatalogBackupOpsError(
            "Restored catalog revision mismatch.",
            error_code="RESTORE_REVISION_MISMATCH",
        )

    completed_at = _format_utc(clock)
    success_payload = {
        "bundle_id": bundle_id,
        "completed_at_utc": completed_at,
        "catalog_sha256": verified.catalog_sha256,
        "alembic_revision": verified.alembic_revision,
        "catalog_size_bytes": verified.catalog_size_bytes,
        "semantic": _semantic_payload(semantic),
        "attempt_seq": attempt_seq,
    }
    status = _load_status(config)
    status["last_scheduled_attempt"] = {
        "started_at_utc": status.get("last_scheduled_attempt", {}).get("started_at_utc"),
        "completed_at_utc": completed_at,
        "state": "succeeded",
        "operation": "run-scheduled",
        "bundle_id": bundle_id,
        "attempt_seq": attempt_seq,
    }
    status["last_successful_scheduled_backup"] = {
        "bundle_id": bundle_id,
        "completed_at_utc": completed_at,
        "catalog_sha256": verified.catalog_sha256,
        "alembic_revision": verified.alembic_revision,
        "catalog_size_bytes": verified.catalog_size_bytes,
        "attempt_seq": attempt_seq,
    }
    status["last_successful_scheduled_restore_verification"] = {
        "bundle_id": bundle_id,
        "completed_at_utc": completed_at,
        "semantic": _semantic_payload(semantic),
        "attempt_seq": attempt_seq,
    }
    status["last_successful_scheduled_backup_and_restore"] = success_payload
    status["recent_failure"] = None
    if pending_cleanup:
        status["pending_cleanup"] = {
            "path_kind": "disposable_restore",
            "bundle_id": bundle_id,
            "at_utc": completed_at,
        }
    else:
        status["pending_cleanup"] = None
    ledger = _ledger_entries(status)
    ledger.append(
        {
            "bundle_id": bundle_id,
            "created_at_utc": completed_at,
            "catalog_sha256": verified.catalog_sha256,
            "alembic_revision": verified.alembic_revision,
        }
    )
    status["auto_ledger"] = ledger
    status["scheduled_attempt_seq"] = attempt_seq
    _write_status(config, status)
    _append_event(
        config,
        {
            "event": "scheduled_backup_and_restore_succeeded",
            "at_utc": completed_at,
            "bundle_id": bundle_id,
            "attempt_seq": attempt_seq,
        },
    )

    plan = build_retention_plan(config, verify=True)
    expired: list[str] = []
    for expire_id in plan.expire:
        revalidated = build_retention_plan(config, verify=True)
        if expire_id not in revalidated.expire:
            continue
        _delete_eligible_bundle(config, expire_id)
        expired.append(expire_id)
        _remove_ledger_entry(config, expire_id)
    if expired:
        _append_event(
            config,
            {
                "event": "scheduled_expire_applied",
                "at_utc": _format_utc(datetime.now(UTC)),
                "deleted_count": len(expired),
            },
        )

    status = _load_status(config)
    status["current_operation"] = None
    _write_status(config, status)
    return ScheduledPipelineResult(
        bundle_id=bundle_id,
        catalog_sha256=verified.catalog_sha256,
        alembic_revision=verified.alembic_revision,
        catalog_size_bytes=verified.catalog_size_bytes,
        semantic=semantic,
        retention=plan,
        expired=tuple(expired),
        pending_cleanup=pending_cleanup,
    )


def _restore_and_semantic(
    config: CatalogBackupOpsConfig,
    bundle_path: Path,
    verified: BackupResult,
    *,
    clock: datetime,
) -> tuple[SemanticSnapshot, Path, bool]:
    _validate_safe_directory_root(
        config.restore_verify_root,
        description="catalog restore verification root",
        create=True,
    )
    disposable = (
        config.restore_verify_root
        / f"restore-{bundle_path.name}-{clock.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(4)}.sqlite3"
    )
    if disposable.exists() or disposable.is_symlink():
        raise CatalogBackupOpsError(
            "Restore destination already exists.",
            error_code="DESTINATION_EXISTS",
        )
    restore_catalog_backup(bundle_path, disposable)
    _verify_restored_database(disposable)
    semantic = capture_semantic_snapshot(disposable)
    if semantic.alembic_revision != verified.alembic_revision:
        raise CatalogBackupOpsError(
            "Restored catalog revision mismatch.",
            error_code="RESTORE_REVISION_MISMATCH",
        )
    pending_cleanup = False
    try:
        disposable.unlink()
    except OSError:
        pending_cleanup = True
    # Remove any operation-owned temporary siblings for this disposable path only.
    # Never touch unrelated restore-root entries.
    for leftover in _operation_temp_siblings(config.restore_verify_root, disposable):
        try:
            if leftover.is_symlink() or not leftover.is_file():
                pending_cleanup = True
                continue
            leftover.unlink()
        except OSError:
            pending_cleanup = True
    if disposable.exists() or any(
        path.exists() for path in _operation_temp_siblings(config.restore_verify_root, disposable)
    ):
        pending_cleanup = True
    return semantic, disposable, pending_cleanup


def _operation_temp_siblings(restore_root: Path, disposable: Path) -> list[Path]:
    """Return private temp siblings created for one disposable restore destination."""
    prefix = f".{disposable.name}."
    matches: list[Path] = []
    try:
        children = list(restore_root.iterdir())
    except OSError:
        return matches
    for child in children:
        if not child.name.startswith(prefix):
            continue
        if not child.name.endswith(".tmp"):
            continue
        matches.append(child)
    return matches


def _record_scheduled_failure(
    config: CatalogBackupOpsConfig,
    *,
    error_code: str,
    clock: datetime,
    attempt_seq: int | None = None,
) -> None:
    status = _load_status(config)
    previous = status.get("last_scheduled_attempt")
    previous_seq = None
    if isinstance(previous, dict):
        previous_seq = previous.get("attempt_seq")
    seq = attempt_seq if isinstance(attempt_seq, int) else previous_seq
    status["last_scheduled_attempt"] = {
        "started_at_utc": previous.get("started_at_utc") if isinstance(previous, dict) else None,
        "completed_at_utc": _format_utc(clock),
        "state": "failed",
        "operation": "run-scheduled",
        "error_code": error_code,
        "attempt_seq": seq,
    }
    if isinstance(seq, int) and seq > 0:
        status["scheduled_attempt_seq"] = max(
            _coerce_attempt_seq(status.get("scheduled_attempt_seq")) or 0,
            seq,
        )
    status["recent_failure"] = {
        "at_utc": _format_utc(clock),
        "error_code": error_code,
        "operation": "run-scheduled",
        "attempt_seq": seq,
    }
    status["current_operation"] = None
    _write_status(config, status)
    _append_event(
        config,
        {
            "event": "scheduled_attempt_failed",
            "at_utc": _format_utc(clock),
            "error_code": error_code,
            "attempt_seq": seq,
        },
    )


def _delete_eligible_bundle(config: CatalogBackupOpsConfig, bundle_id: str) -> None:
    if not is_automatic_bundle_name(bundle_id):
        raise CatalogBackupOpsError("Pinned backup is not eligible.", error_code="NOT_ELIGIBLE")
    if "/" in bundle_id or bundle_id in {".", ".."} or "\\" in bundle_id:
        raise CatalogBackupOpsError("Unsafe backup identity.", error_code="UNSAFE_PATH")
    root = config.backup_root.resolve(strict=False)
    target = (config.backup_root / bundle_id).resolve(strict=False)
    if root not in target.parents and target != root:
        raise CatalogBackupOpsError("Backup path escaped root.", error_code="UNSAFE_PATH")
    if not target.exists():
        return
    if target.is_symlink() or not target.is_dir():
        raise CatalogBackupOpsError("Unsafe backup bundle.", error_code="UNSAFE_PATH")
    if any(child.is_symlink() for child in target.iterdir()):
        raise CatalogBackupOpsError("Unsafe backup bundle.", error_code="UNSAFE_PATH")
    observed = {child.name for child in target.iterdir()}
    if not observed.issubset({MANIFEST_NAME, CATALOG_NAME}):
        raise CatalogBackupOpsError("Unexpected backup bundle state.", error_code="UNEXPECTED_BUNDLE_STATE")
    for name in (MANIFEST_NAME, CATALOG_NAME):
        child = target / name
        if child.exists():
            child.unlink()
    try:
        target.rmdir()
    except OSError as exc:
        raise CatalogBackupOpsError(
            "Eligible backup could not be deleted.",
            error_code="EXPIRE_FAILED",
        ) from exc


def _existing_bundle_under_root(root: Path, bundle: Path | str) -> Path:
    path = Path(bundle).expanduser()
    if not path.is_absolute():
        path = root / path
    if path.is_symlink():
        raise CatalogBackupOpsError("Unsafe backup bundle.", error_code="UNSAFE_PATH")
    resolved_root = root.resolve(strict=False)
    resolved = path.resolve(strict=False)
    if resolved_root not in resolved.parents and resolved != resolved_root:
        raise CatalogBackupOpsError("Backup path escaped root.", error_code="UNSAFE_PATH")
    if not resolved.exists() or not resolved.is_dir():
        raise CatalogBackupOpsError("Invalid backup bundle.", error_code="INVALID_PATH")
    return resolved


def _bundle_looks_complete(bundle: Path) -> bool:
    try:
        names = {child.name for child in bundle.iterdir()}
    except OSError:
        return False
    if any(name.startswith(".framenest-backup-") or name.endswith(".tmp") for name in names):
        return False
    return names == {MANIFEST_NAME, CATALOG_NAME}


def _ensure_ops_layout(config: CatalogBackupOpsConfig) -> None:
    _validate_safe_directory_root(
        config.ops_root,
        description="catalog backup operator-state root",
        create=True,
    )
    status_path = config.ops_root / "status.json"
    events_path = config.ops_root / "events.jsonl"
    lock_path = config.ops_root / "catalog-backup.lock"
    if not status_path.exists():
        _write_status(config, _empty_status())
    if not events_path.exists():
        events_path.touch(mode=0o600)
        _private_file(events_path)
    if not lock_path.exists():
        lock_path.touch(mode=0o600)
        _private_file(lock_path)


def _empty_status() -> dict[str, Any]:
    return {
        "schema_version": STATUS_SCHEMA_VERSION,
        "updated_at_utc": _format_utc(datetime.now(UTC)),
        "current_operation": None,
        "scheduled_attempt_seq": 0,
        "last_scheduled_attempt": None,
        "last_successful_scheduled_backup": None,
        "last_successful_scheduled_restore_verification": None,
        "last_successful_scheduled_backup_and_restore": None,
        "last_manual_verify_restore": None,
        "pending_cleanup": None,
        "recent_failure": None,
        "auto_ledger": [],
        "offdevice_attempt_seq": 0,
        "last_offdevice_attempt": None,
        "last_successful_offdevice_copy_and_restore": None,
        "offdevice_pending_cleanup": None,
    }


def _load_status(config: CatalogBackupOpsConfig) -> dict[str, Any]:
    path = config.ops_root / "status.json"
    if not path.exists():
        return _empty_status()
    if path.is_symlink():
        raise CatalogBackupOpsError("Unsafe operator status.", error_code="UNSAFE_PATH")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CatalogBackupOpsError(
            "Operator status is malformed.",
            error_code="STATUS_MALFORMED",
        ) from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != STATUS_SCHEMA_VERSION:
        raise CatalogBackupOpsError(
            "Operator status is unsupported.",
            error_code="STATUS_UNSUPPORTED",
        )
    return payload


def _write_status(config: CatalogBackupOpsConfig, payload: dict[str, Any]) -> None:
    path = config.ops_root / "status.json"
    body = dict(payload)
    body["schema_version"] = STATUS_SCHEMA_VERSION
    body["updated_at_utc"] = _format_utc(datetime.now(UTC))
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".status.json.",
            suffix=".tmp",
            dir=str(config.ops_root),
        )
        temp_path = Path(temp_name)
        _private_file(temp_path)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        temp_path = None
        _private_file(path)
    except OSError as exc:
        raise CatalogBackupOpsError(
            "Operator status could not be written.",
            error_code="STATUS_WRITE_FAILED",
        ) from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _append_event(config: CatalogBackupOpsConfig, event: Mapping[str, Any]) -> None:
    path = config.ops_root / "events.jsonl"
    line = json.dumps(dict(event), sort_keys=True, separators=(",", ":")) + "\n"
    try:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        _private_file(path)
        if path.stat().st_size > EVENTS_MAX_BYTES:
            _truncate_events(path)
    except OSError as exc:
        raise CatalogBackupOpsError(
            "Operator event could not be written.",
            error_code="EVENT_WRITE_FAILED",
        ) from exc


def _truncate_events(path: Path) -> None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        kept = lines[-EVENTS_KEEP_LINES:]
        encoded = ("\n".join(kept) + ("\n" if kept else "")).encode("utf-8")
        fd, temp_name = tempfile.mkstemp(prefix=".events.jsonl.", suffix=".tmp", dir=str(path.parent))
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, path)
            _private_file(path)
        finally:
            if temp_path.exists():
                temp_path.unlink(missing_ok=True)
    except OSError:
        return


def _ledger_entries(status: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw = status.get("auto_ledger", [])
    if not isinstance(raw, list):
        return []
    entries: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, dict) and isinstance(item.get("bundle_id"), str):
            entries.append(dict(item))
    return entries


def _remove_ledger_entry(config: CatalogBackupOpsConfig, bundle_id: str) -> None:
    status = _load_status(config)
    status["auto_ledger"] = [
        entry for entry in _ledger_entries(status) if entry.get("bundle_id") != bundle_id
    ]
    _write_status(config, status)


def _validate_config_roots(config: CatalogBackupOpsConfig) -> None:
    for path, description in (
        (config.backup_root, "catalog backup root"),
        (config.restore_verify_root, "catalog restore verification root"),
        (config.ops_root, "catalog backup operator-state root"),
    ):
        if path.is_symlink():
            raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
        resolved = path.resolve(strict=False)
        if resolved in {
            Path("/"),
            Path("/etc"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/boot"),
            Path("/dev"),
            Path("/proc"),
            Path("/sys"),
            Path("/var"),
            Path("/var/lib"),
            Path("/mnt"),
            Path("/home"),
            Path("/srv"),
            Path("/srv/media"),
        }:
            raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
        for blocked in (Path("/mnt"), Path("/srv/media"), Path("/home"), Path("/etc")):
            try:
                resolved.relative_to(blocked)
            except ValueError:
                continue
            raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    if len({config.backup_root, config.restore_verify_root, config.ops_root}) != 3:
        raise CatalogBackupOpsError(
            "Catalog backup roots must be distinct.",
            error_code="INVALID_PATH",
        )


def _validate_safe_directory_root(
    path: Path,
    *,
    description: str,
    create: bool = False,
) -> None:
    if path.is_symlink():
        raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    if create:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if path.exists() and path.is_symlink():
        raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    if path.exists() and not path.is_dir():
        raise CatalogBackupOpsError(f"Invalid {description}.", error_code="INVALID_PATH")
    if path.exists():
        _private_directory(path)


def _parse_keep_auto(raw: object) -> int:
    if raw is None or raw == "":
        return DEFAULT_KEEP_AUTO
    if isinstance(raw, bool):
        raise CatalogBackupOpsError(
            "Catalog backup retention configuration is invalid.",
            error_code="INVALID_KEEP_AUTO",
        )
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        if not re.fullmatch(r"[0-9]+", raw.strip()):
            raise CatalogBackupOpsError(
                "Catalog backup retention configuration is invalid.",
                error_code="INVALID_KEEP_AUTO",
            )
        value = int(raw.strip())
    else:
        raise CatalogBackupOpsError(
            "Catalog backup retention configuration is invalid.",
            error_code="INVALID_KEEP_AUTO",
        )
    if value < MIN_KEEP_AUTO or value > MAX_KEEP_AUTO:
        raise CatalogBackupOpsError(
            "Catalog backup retention configuration is invalid.",
            error_code="INVALID_KEEP_AUTO",
        )
    return value


def _absolute_path_from_env(
    raw: str | None,
    *,
    default: Path,
    description: str,
) -> Path:
    value = default if raw is None or raw == "" else Path(raw).expanduser()
    if not value.is_absolute():
        raise CatalogBackupOpsError(
            f"{description.title()} must be absolute.",
            error_code="INVALID_PATH",
        )
    if value.is_symlink():
        raise CatalogBackupOpsError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    return value.resolve(strict=False)


def _bundle_sort_key(summary: BundleSummary) -> tuple[str, str]:
    created = summary.created_at_utc or ""
    return (created, summary.bundle_id)


def _attempt_after_success(attempt: Mapping[str, Any], success: Mapping[str, Any]) -> bool:
    """Return True when the failed attempt is ordered after the recorded success.

    Prefer durable monotonic attempt_seq. Timestamps alone are not authoritative
    because multiple attempts can share one UTC second.
    """
    attempt_seq = _coerce_attempt_seq(attempt.get("attempt_seq"))
    success_seq = _coerce_attempt_seq(success.get("attempt_seq"))
    if attempt_seq is not None and success_seq is not None:
        return attempt_seq > success_seq
    if attempt_seq is not None and success_seq is None:
        # Newer durable state after legacy success: treat sequenced failure as later.
        return True
    if attempt_seq is None and success_seq is not None:
        # Malformed/missing ordering on the failure side fails closed to "not after"
        # only when we cannot prove order; readiness then keeps success/stale rather
        # than inventing a failure order. Callers still see failed when there is no
        # success payload.
        return False
    attempt_at = attempt.get("completed_at_utc") or attempt.get("started_at_utc")
    success_at = success.get("completed_at_utc")
    if not isinstance(attempt_at, str) or not isinstance(success_at, str):
        return True
    return attempt_at > success_at


def _allocate_scheduled_attempt_seq(status: Mapping[str, Any]) -> int:
    current = _coerce_attempt_seq(status.get("scheduled_attempt_seq")) or 0
    last = status.get("last_scheduled_attempt")
    if isinstance(last, dict):
        last_seq = _coerce_attempt_seq(last.get("attempt_seq"))
        if last_seq is not None:
            current = max(current, last_seq)
    success = status.get("last_successful_scheduled_backup_and_restore")
    if isinstance(success, dict):
        success_seq = _coerce_attempt_seq(success.get("attempt_seq"))
        if success_seq is not None:
            current = max(current, success_seq)
    return current + 1


def _coerce_attempt_seq(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    return None


def _semantic_payload(semantic: SemanticSnapshot) -> dict[str, Any]:
    return {
        "alembic_revision": semantic.alembic_revision,
        "counts": dict(semantic.counts),
    }


def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def _sqlite_readonly_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def _format_utc(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: str) -> datetime:
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _private_directory(path: Path, mode: int = 0o700) -> None:
    try:
        os.chmod(path, mode)
    except OSError as exc:
        if os.name != "nt":
            raise CatalogBackupOpsError(
                "Backup permissions could not be restricted.",
                error_code="PERMISSION_FAILED",
            ) from exc


def _private_file(path: Path) -> None:
    try:
        if not path.exists():
            path.touch(mode=0o600)
        os.chmod(path, 0o600)
    except OSError as exc:
        if os.name != "nt":
            raise CatalogBackupOpsError(
                "Backup permissions could not be restricted.",
                error_code="PERMISSION_FAILED",
            ) from exc


def _lock_is_held(config: CatalogBackupOpsConfig) -> bool:
    lock_path = config.ops_root / "catalog-backup.lock"
    if not lock_path.exists():
        return False
    with lock_path.open("a+", encoding="utf-8") as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return False
