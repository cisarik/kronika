"""Operator-workstation pull-based catalog snapshot store and recovery.

Workstation-initiated OpenSSH pull of the NUC export protocol-v1 stream into a
fail-closed local snapshot store with disposable restore verification and
no-replace atomic publication. Does not select NUC sources and does not grant
the NUC any workstation write authority.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import errno
import json
import os
from pathlib import Path
import re
import secrets
import signal
import sqlite3
import subprocess
import threading
import time
from typing import Any

from kronika.infrastructure.persistence.catalog_backup import (
    CATALOG_NAME,
    MANIFEST_NAME,
    BackupError,
    restore_catalog_backup,
    verify_catalog_backup,
)
from kronika.infrastructure.persistence.catalog_backup_ops import (
    capture_semantic_snapshot,
)
from kronika.infrastructure.persistence.catalog_backup_transfer import (
    BundleIdentity,
    TransferError,
    assert_stream_eof,
    capture_bundle_identity,
    fsync_directory,
    read_protocol_v1_preamble,
    receive_file_bytes,
    rename_noreplace,
)

MARKER_NAME = ".kronika-workstation-snapshot-store.json"
#: The marker filename written before the durable-writer cut. Readers keep
#: accepting it, and the accepted tuple must name it explicitly.
COMPATIBLE_MARKER_NAME = ".framenest-workstation-snapshot-store.json"
#: Accepted marker filenames, current writer spelling first.
ACCEPTED_MARKER_NAMES = (MARKER_NAME, COMPATIBLE_MARKER_NAME)
MARKER_PURPOSE = "kronika-workstation-snapshot-store"
#: The purpose written before the durable-writer cut. Readers keep accepting it.
COMPATIBLE_MARKER_PURPOSE = "framenest-workstation-snapshot-store"
ACCEPTED_MARKER_PURPOSES = frozenset({MARKER_PURPOSE, COMPATIBLE_MARKER_PURPOSE})
MARKER_SCHEMA_VERSION = 1
SNAPSHOTS_DIRNAME = "snapshots"
RESTORE_VERIFY_DIRNAME = ".restore-verify"
STAGE_PREFIX = ".kronika-pull-stage-"
#: The staging prefix written before the durable-writer cut. The cleanup
#: recognizer keeps accepting it so an abandoned former staging directory is
#: still identified as owned.
COMPATIBLE_STAGE_PREFIX = ".framenest-pull-stage-"
ACCEPTED_STAGE_PREFIXES = (STAGE_PREFIX, COMPATIBLE_STAGE_PREFIX)
SNAPSHOT_NAME = "snapshot.json"
SNAPSHOT_PURPOSE = "kronika-workstation-catalog-snapshot"
#: The snapshot purpose written before the durable-writer cut. Readers keep
#: accepting it.
COMPATIBLE_SNAPSHOT_PURPOSE = "framenest-workstation-catalog-snapshot"
ACCEPTED_SNAPSHOT_PURPOSES = frozenset({SNAPSHOT_PURPOSE, COMPATIBLE_SNAPSHOT_PURPOSE})
BUNDLE_DIRNAME = "bundle"
STORE_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
PRIVATE_DIR_MODE = 0o700
PRIVATE_FILE_MODE = 0o600
GROUP_OTHER_BITS = 0o077
STDERR_CAP_BYTES = 64 * 1024
FIXED_REMOTE_EXPORT_COMMAND = (
    "sudo",
    "-n",
    "-u",
    "framenest",
    "--",
    "/usr/local/libexec/framenest-catalog-export-v1",
)
#: Temporary explicit workstation compatibility selection. Each key selects one
#: fixed remote command tuple; the caller selects a key and can never supply an
#: arbitrary remote command. ``old`` is the default while the former layout is
#: still served; the authorized operator invocation switches to ``new`` during
#: the identity migration window and canonical operation becomes the default
#: when this selection is removed.
REMOTE_LAYOUT_COMMANDS: Mapping[str, tuple[str, ...]] = {
    "old": FIXED_REMOTE_EXPORT_COMMAND,
    "new": (
        "sudo",
        "-n",
        "-u",
        "kronika",
        "--",
        "/usr/local/libexec/kronika-catalog-export-v1",
    ),
}
DEFAULT_REMOTE_LAYOUT = "old"
DEFAULT_SSH_EXECUTABLE = "ssh"
SNAPSHOT_SCHEMA_VERSION = 1
TRANSFER_PROTOCOL_NAME = "kronika-catalog-backup-export"
#: The transfer-protocol name written before the durable-writer cut. The envelope
#: reader accepts it, so historical snapshots stay verifiable.
COMPATIBLE_TRANSFER_PROTOCOL_NAME = "framenest-catalog-backup-export"
ACCEPTED_TRANSFER_PROTOCOL_NAMES = (
    TRANSFER_PROTOCOL_NAME,
    COMPATIBLE_TRANSFER_PROTOCOL_NAME,
)
CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
UNSAFE_TARGET_CHARS = frozenset(' \t\r\n;|&$`<>(){}[]!#"\\\'*?')


class WorkstationError(BackupError):
    """Sanitized workstation snapshot-store failure."""


@dataclass(frozen=True, slots=True)
class ValidatedWorkstationStore:
    """Trusted workstation snapshot store after fail-closed validation."""

    mount_root: Path
    store_root: Path
    snapshots_dir: Path
    store_id: str
    mount_device: int


@dataclass(frozen=True, slots=True)
class WorkstationOsHooks:
    """Injectable OS inspection boundary for deterministic tests."""

    is_mountpoint: Callable[[Path], bool] = os.path.ismount
    device_id: Callable[[Path], int] = lambda path: path.stat().st_dev
    rename_noreplace: Callable[[Path, Path], None] | None = None
    fsync: Callable[[int], None] = os.fsync
    geteuid: Callable[[], int] = os.geteuid


@dataclass(frozen=True, slots=True)
class PullResult:
    """Successful workstation pull and atomic acceptance."""

    bundle_id: str
    catalog_sha256: str
    alembic_revision: str
    catalog_size_bytes: int
    reused_existing: bool
    semantic: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class InitStoreResult:
    """Result of workstation snapshot-store initialization."""

    store_id: str
    created: bool


def init_workstation_store(
    *,
    store_root: Path,
    mount_root: Path,
    hooks: WorkstationOsHooks | None = None,
) -> InitStoreResult:
    """Initialize or idempotently verify a trusted workstation snapshot store."""
    probe = hooks or WorkstationOsHooks()
    mount = _require_absolute(mount_root, description="mount root")
    store = _require_absolute(store_root, description="store root")
    _validate_mount_root(mount, hooks=probe)

    if store.exists() or store.is_symlink():
        if store.is_symlink() or not store.is_dir():
            raise WorkstationError(
                "Workstation snapshot store is unsafe.",
                error_code="WORKSTATION_STORE_UNSAFE",
            )
        _assert_store_under_mount(store, mount)
        _assert_same_device(store, mount, hooks=probe)
        store_stat = store.lstat()
        if store_stat.st_uid != probe.geteuid():
            raise WorkstationError(
                "Workstation snapshot store ownership is unsafe.",
                error_code="WORKSTATION_STORE_OWNER",
            )
        if store_stat.st_mode & GROUP_OTHER_BITS:
            raise WorkstationError(
                "Workstation snapshot store mode is unsafe; tighten permissions before init.",
                error_code="WORKSTATION_STORE_MODE_UNSAFE",
            )
        children = [child.name for child in store.iterdir()]
        if not children:
            store_id = secrets.token_hex(16)
            _write_new_marker(store, store_id=store_id, hooks=probe)
            snapshots = store / SNAPSHOTS_DIRNAME
            snapshots.mkdir(mode=PRIVATE_DIR_MODE)
            os.chmod(snapshots, PRIVATE_DIR_MODE)
            restore_root = store / RESTORE_VERIFY_DIRNAME
            restore_root.mkdir(mode=PRIVATE_DIR_MODE)
            os.chmod(restore_root, PRIVATE_DIR_MODE)
            fsync_directory(store, fsync=probe.fsync)
            return InitStoreResult(store_id=store_id, created=True)
        if any(name in children for name in ACCEPTED_MARKER_NAMES):
            validated = validate_workstation_store(
                store_root=store,
                mount_root=mount,
                expected_store_id=None,
                hooks=probe,
            )
            return InitStoreResult(store_id=validated.store_id, created=False)
        raise WorkstationError(
            "Workstation snapshot store is non-empty without a valid marker.",
            error_code="WORKSTATION_STORE_NONEMPTY",
        )

    # Target absent: create privately on verified mount.
    _assert_store_under_mount(store, mount)
    parent = store.parent
    if parent.is_symlink() or not parent.is_dir():
        raise WorkstationError(
            "Workstation snapshot store parent is unsafe.",
            error_code="WORKSTATION_STORE_PARENT_UNSAFE",
        )
    _assert_same_device(parent, mount, hooks=probe)
    store_id = secrets.token_hex(16)
    store.mkdir(mode=PRIVATE_DIR_MODE)
    os.chmod(store, PRIVATE_DIR_MODE)
    if store.lstat().st_uid != probe.geteuid():
        raise WorkstationError(
            "Workstation snapshot store ownership is unsafe.",
            error_code="WORKSTATION_STORE_OWNER",
        )
    _write_new_marker(store, store_id=store_id, hooks=probe)
    snapshots = store / SNAPSHOTS_DIRNAME
    snapshots.mkdir(mode=PRIVATE_DIR_MODE)
    os.chmod(snapshots, PRIVATE_DIR_MODE)
    restore_root = store / RESTORE_VERIFY_DIRNAME
    restore_root.mkdir(mode=PRIVATE_DIR_MODE)
    os.chmod(restore_root, PRIVATE_DIR_MODE)
    fsync_directory(store, fsync=probe.fsync)
    fsync_directory(parent, fsync=probe.fsync)
    return InitStoreResult(store_id=store_id, created=True)


def _accepted_marker(store: Path) -> Path | None:
    """Return the accepted store marker path, or None when absent.

    The current writer spelling wins when both spellings exist. An accepted name
    that exists but is unsafe is returned as-is so the caller fails closed on
    that spelling instead of silently reading the other one.
    """
    for name in ACCEPTED_MARKER_NAMES:
        candidate = store / name
        if candidate.is_symlink() or candidate.exists():
            return candidate
    return None


def validate_workstation_store(
    *,
    store_root: Path,
    mount_root: Path,
    expected_store_id: str | None,
    hooks: WorkstationOsHooks | None = None,
) -> ValidatedWorkstationStore:
    """Fail closed unless the workstation snapshot-store trust boundary holds."""
    probe = hooks or WorkstationOsHooks()
    mount = _require_absolute(mount_root, description="mount root")
    store = _require_absolute(store_root, description="store root")
    mount_device = _validate_mount_root(mount, hooks=probe)
    if store.is_symlink() or not store.exists() or not store.is_dir():
        raise WorkstationError(
            "Workstation snapshot store is unavailable.",
            error_code="WORKSTATION_STORE_UNAVAILABLE",
        )
    _assert_store_under_mount(store, mount)
    _assert_same_device(store, mount, hooks=probe)
    store_stat = store.lstat()
    if store_stat.st_uid != probe.geteuid():
        raise WorkstationError(
            "Workstation snapshot store ownership is unsafe.",
            error_code="WORKSTATION_STORE_OWNER",
        )
    if store_stat.st_mode & GROUP_OTHER_BITS:
        raise WorkstationError(
            "Workstation snapshot store mode is unsafe.",
            error_code="WORKSTATION_STORE_MODE_UNSAFE",
        )
    marker = _accepted_marker(store)
    if marker is None or marker.is_symlink() or not marker.is_file():
        raise WorkstationError(
            "Workstation snapshot store marker is missing or unsafe.",
            error_code="WORKSTATION_MARKER_INVALID",
        )
    marker_stat = marker.lstat()
    if marker_stat.st_uid != probe.geteuid():
        raise WorkstationError(
            "Workstation snapshot store marker ownership is unsafe.",
            error_code="WORKSTATION_MARKER_UNSAFE",
        )
    if marker_stat.st_mode & GROUP_OTHER_BITS:
        raise WorkstationError(
            "Workstation snapshot store marker mode is unsafe.",
            error_code="WORKSTATION_MARKER_UNSAFE",
        )
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkstationError(
            "Workstation snapshot store marker is malformed.",
            error_code="WORKSTATION_MARKER_MALFORMED",
        ) from exc
    if not isinstance(payload, dict):
        raise WorkstationError(
            "Workstation snapshot store marker is malformed.",
            error_code="WORKSTATION_MARKER_MALFORMED",
        )
    if set(payload.keys()) != {"schema_version", "purpose", "store_id"}:
        raise WorkstationError(
            "Workstation snapshot store marker is malformed.",
            error_code="WORKSTATION_MARKER_MALFORMED",
        )
    if payload.get("schema_version") != MARKER_SCHEMA_VERSION:
        raise WorkstationError(
            "Workstation snapshot store marker is unsupported.",
            error_code="WORKSTATION_MARKER_UNSUPPORTED",
        )
    if payload.get("purpose") not in ACCEPTED_MARKER_PURPOSES:
        raise WorkstationError(
            "Workstation snapshot store marker purpose mismatch.",
            error_code="WORKSTATION_MARKER_PURPOSE_MISMATCH",
        )
    store_id = payload.get("store_id")
    if not isinstance(store_id, str) or not STORE_ID_PATTERN.fullmatch(store_id):
        raise WorkstationError(
            "Workstation snapshot store marker identity is invalid.",
            error_code="WORKSTATION_MARKER_ID_INVALID",
        )
    if expected_store_id is not None:
        if not STORE_ID_PATTERN.fullmatch(expected_store_id):
            raise WorkstationError(
                "Expected workstation store ID is invalid.",
                error_code="WORKSTATION_EXPECTED_STORE_ID_INVALID",
            )
        if store_id != expected_store_id:
            raise WorkstationError(
                "Workstation snapshot store identity mismatch.",
                error_code="WORKSTATION_STORE_ID_MISMATCH",
            )
    snapshots = store / SNAPSHOTS_DIRNAME
    if snapshots.is_symlink() or not snapshots.is_dir():
        raise WorkstationError(
            "Workstation snapshots directory is missing or unsafe.",
            error_code="WORKSTATION_SNAPSHOTS_UNSAFE",
        )
    _assert_contained(snapshots, store)
    _assert_same_device(snapshots, mount, hooks=probe)
    snapshots_stat = snapshots.lstat()
    if snapshots_stat.st_uid != probe.geteuid():
        raise WorkstationError(
            "Workstation snapshots directory ownership is unsafe.",
            error_code="WORKSTATION_SNAPSHOTS_UNSAFE",
        )
    if snapshots_stat.st_mode & GROUP_OTHER_BITS:
        raise WorkstationError(
            "Workstation snapshots directory mode is unsafe.",
            error_code="WORKSTATION_SNAPSHOTS_UNSAFE",
        )
    return ValidatedWorkstationStore(
        mount_root=mount,
        store_root=store,
        snapshots_dir=snapshots,
        store_id=store_id,
        mount_device=mount_device,
    )


def pull_workstation_snapshot(
    *,
    store_root: Path,
    mount_root: Path,
    expected_store_id: str,
    ssh_target: str,
    ssh_port: int | None = None,
    ssh_executable: str = DEFAULT_SSH_EXECUTABLE,
    connect_timeout_seconds: int = 30,
    transfer_timeout_seconds: int = 30 * 60,
    remote_layout: str = DEFAULT_REMOTE_LAYOUT,
    hooks: WorkstationOsHooks | None = None,
    popen: Callable[..., subprocess.Popen[bytes]] | None = None,
    now: datetime | None = None,
) -> PullResult:
    """Pull, verify, restore-verify, and atomically publish one workstation snapshot."""
    probe = hooks or WorkstationOsHooks()
    clock = now or datetime.now(UTC)
    target = validate_ssh_target(ssh_target)
    store = validate_workstation_store(
        store_root=store_root,
        mount_root=mount_root,
        expected_store_id=expected_store_id,
        hooks=probe,
    )
    argv = build_ssh_argv(
        target=target,
        ssh_executable=ssh_executable,
        ssh_port=ssh_port,
        connect_timeout_seconds=connect_timeout_seconds,
        remote_layout=remote_layout,
    )
    launcher = popen or subprocess.Popen
    process: subprocess.Popen[bytes] | None = None
    stage: Path | None = None
    disposable: Path | None = None
    snapshots_dir = store.snapshots_dir
    stderr_state = {"truncated": False, "bytes": 0, "prefix": bytearray()}
    stderr_thread: threading.Thread | None = None
    try:
        try:
            process = launcher(
                argv,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                bufsize=0,
            )
        except FileNotFoundError as exc:
            raise WorkstationError(
                "OpenSSH executable is unavailable.",
                error_code="WORKSTATION_SSH_MISSING",
            ) from exc
        except OSError as exc:
            raise WorkstationError(
                "OpenSSH process could not be started.",
                error_code="WORKSTATION_SSH_SPAWN_FAILED",
            ) from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stderr_thread = threading.Thread(
            target=_drain_stderr,
            args=(process.stderr, stderr_state),
            name="framenest-ssh-stderr-drain",
            daemon=True,
        )
        stderr_thread.start()
        deadline = time.monotonic() + max(1, transfer_timeout_seconds)
        header = _read_preamble_with_timeout(process, deadline=deadline)
        bundle_id = str(header["bundle_id"])
        final_path = snapshots_dir / bundle_id
        _assert_contained(final_path, snapshots_dir)

        stage = snapshots_dir / f"{STAGE_PREFIX}{bundle_id}.{secrets.token_hex(8)}"
        _assert_contained(stage, snapshots_dir)
        _cleanup_owned_stage(stage, snapshots_dir)
        stage.mkdir(mode=PRIVATE_DIR_MODE)
        os.chmod(stage, PRIVATE_DIR_MODE)
        bundle_stage = stage / BUNDLE_DIRNAME
        bundle_stage.mkdir(mode=PRIVATE_DIR_MODE)
        os.chmod(bundle_stage, PRIVATE_DIR_MODE)

        try:
            receive_file_bytes(
                process.stdout,
                bundle_stage / MANIFEST_NAME,
                expected_size=int(header["manifest_size_bytes"]),
                expected_sha256=str(header["manifest_sha256"]),
                fsync=probe.fsync,
                deadline=deadline,
            )
            receive_file_bytes(
                process.stdout,
                bundle_stage / CATALOG_NAME,
                expected_size=int(header["catalog_size_bytes"]),
                expected_sha256=str(header["catalog_sha256"]),
                fsync=probe.fsync,
                deadline=deadline,
            )
            assert_stream_eof(process.stdout, deadline=deadline)
        except TransferError as exc:
            if exc.error_code == "TRANSFER_TIMEOUT":
                raise WorkstationError(
                    "Workstation pull timed out.",
                    error_code="WORKSTATION_TRANSFER_TIMEOUT",
                ) from exc
            raise
        _wait_process(process, deadline=deadline)
        if process.returncode != 0:
            raise _classify_remote_exit(process.returncode)

        identity = capture_bundle_identity(bundle_stage, bundle_id=bundle_id)
        _assert_identity_matches_header(identity, header)
        verified = verify_catalog_backup(bundle_stage)
        disposable = _disposable_restore_path(store.store_root, operation_id=secrets.token_hex(8))
        semantic = _restore_verify_bundle(
            bundle_stage,
            disposable=disposable,
            expected_revision=verified.alembic_revision,
            expected_semantic=header["semantic"],
            hooks=probe,
        )
        snapshot_payload = _build_snapshot_envelope(
            identity=identity,
            semantic=semantic,
            accepted_at=clock,
        )
        snapshot_path = stage / SNAPSHOT_NAME
        _atomic_write_json(snapshot_path, snapshot_payload, hooks=probe)
        fsync_directory(bundle_stage, fsync=probe.fsync)
        fsync_directory(stage, fsync=probe.fsync)
        validate_workstation_store(
            store_root=store.store_root,
            mount_root=store.mount_root,
            expected_store_id=store.store_id,
            hooks=probe,
        )

        if final_path.exists() or final_path.is_symlink():
            result = _verify_existing_final(
                final_path=final_path,
                expected_header=header,
                store=store,
                hooks=probe,
            )
            _cleanup_owned_stage(stage, snapshots_dir)
            stage = None
            _cleanup_disposable(disposable)
            disposable = None
            return result

        rename = probe.rename_noreplace or rename_noreplace
        try:
            rename(stage, final_path)
        except TransferError as exc:
            raise WorkstationError(
                "Atomic workstation snapshot publication is unsupported.",
                error_code="WORKSTATION_ATOMIC_PUBLISH_UNSUPPORTED",
            ) from exc
        except FileExistsError as exc:
            raise WorkstationError(
                "Workstation snapshot conflicts with an existing final directory.",
                error_code="WORKSTATION_SNAPSHOT_CONFLICT",
            ) from exc
        except OSError as exc:
            if exc.errno == errno.EEXIST:
                raise WorkstationError(
                    "Workstation snapshot conflicts with an existing final directory.",
                    error_code="WORKSTATION_SNAPSHOT_CONFLICT",
                ) from exc
            if exc.errno in {errno.ENOSPC, errno.EDQUOT}:
                raise WorkstationError(
                    "Workstation snapshot store has insufficient space.",
                    error_code="WORKSTATION_STORE_FULL",
                ) from exc
            raise WorkstationError(
                "Workstation snapshot could not be published.",
                error_code="WORKSTATION_PUBLISH_FAILED",
            ) from exc
        stage = None
        fsync_directory(snapshots_dir, fsync=probe.fsync)
        validate_workstation_store(
            store_root=store.store_root,
            mount_root=store.mount_root,
            expected_store_id=store.store_id,
            hooks=probe,
        )
        final = verify_workstation_snapshot(
            store_root=store.store_root,
            mount_root=store.mount_root,
            expected_store_id=store.store_id,
            bundle_id=bundle_id,
            hooks=probe,
        )
        _cleanup_disposable(disposable)
        disposable = None
        return PullResult(
            bundle_id=final["bundle_id"],
            catalog_sha256=final["catalog_sha256"],
            alembic_revision=final["alembic_revision"],
            catalog_size_bytes=final["catalog_size_bytes"],
            reused_existing=False,
            semantic=final["semantic"],
        )
    except TransferError as exc:
        if process is not None:
            try:
                _wait_process(process, deadline=time.monotonic() + 2)
            except WorkstationError:
                pass
            if process.returncode == 255:
                raise WorkstationError(
                    "SSH transport or authentication failed.",
                    error_code="WORKSTATION_SSH_TRANSPORT",
                ) from exc
        if exc.error_code == "TRANSFER_TIMEOUT":
            raise WorkstationError(
                "Workstation pull timed out.",
                error_code="WORKSTATION_TRANSFER_TIMEOUT",
            ) from exc
        raise WorkstationError(str(exc), error_code=exc.error_code) from exc
    finally:
        if process is not None and process.poll() is None:
            _terminate_and_reap(process, deadline=time.monotonic() + 5)
        if stderr_thread is not None:
            stderr_thread.join(timeout=2)
        if stage is not None:
            try:
                _cleanup_owned_stage(stage, snapshots_dir)
            except WorkstationError:
                pass
        if disposable is not None:
            try:
                _cleanup_disposable(disposable)
            except WorkstationError:
                pass


def list_workstation_snapshots(
    *,
    store_root: Path,
    mount_root: Path,
    expected_store_id: str,
    hooks: WorkstationOsHooks | None = None,
) -> list[dict[str, Any]]:
    """Offline enumeration of recognized final snapshots only."""
    store = validate_workstation_store(
        store_root=store_root,
        mount_root=mount_root,
        expected_store_id=expected_store_id,
        hooks=hooks,
    )
    results: list[dict[str, Any]] = []
    for child in sorted(store.snapshots_dir.iterdir(), key=lambda path: path.name):
        if child.name.startswith("."):
            continue
        if child.is_symlink() or not child.is_dir():
            continue
        try:
            payload = verify_workstation_snapshot(
                store_root=store.store_root,
                mount_root=store.mount_root,
                expected_store_id=store.store_id,
                bundle_id=child.name,
                hooks=hooks,
                restore_verify=False,
            )
        except WorkstationError:
            continue
        results.append(
            {
                "bundle_id": payload["bundle_id"],
                "accepted_at_utc": payload["accepted_at_utc"],
                "alembic_revision": payload["alembic_revision"],
                "catalog_sha256": payload["catalog_sha256"],
                "catalog_size_bytes": payload["catalog_size_bytes"],
            }
        )
    return results


def verify_workstation_snapshot(
    *,
    store_root: Path,
    mount_root: Path,
    expected_store_id: str,
    bundle_id: str,
    hooks: WorkstationOsHooks | None = None,
    restore_verify: bool = True,
) -> dict[str, Any]:
    """Offline strict verification of one local workstation snapshot."""
    probe = hooks or WorkstationOsHooks()
    store = validate_workstation_store(
        store_root=store_root,
        mount_root=mount_root,
        expected_store_id=expected_store_id,
        hooks=probe,
    )
    if not isinstance(bundle_id, str) or not bundle_id or "/" in bundle_id or bundle_id.startswith("."):
        raise WorkstationError(
            "Snapshot identity is invalid.",
            error_code="WORKSTATION_SNAPSHOT_ID_INVALID",
        )
    snapshot_dir = store.snapshots_dir / bundle_id
    if snapshot_dir.is_symlink() or not snapshot_dir.is_dir():
        raise WorkstationError(
            "Workstation snapshot is missing or unsafe.",
            error_code="WORKSTATION_SNAPSHOT_MISSING",
        )
    _assert_contained(snapshot_dir, store.snapshots_dir)
    envelope_path = snapshot_dir / SNAPSHOT_NAME
    bundle_dir = snapshot_dir / BUNDLE_DIRNAME
    if envelope_path.is_symlink() or not envelope_path.is_file():
        raise WorkstationError(
            "Workstation snapshot envelope is missing or unsafe.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_INVALID",
        )
    if bundle_dir.is_symlink() or not bundle_dir.is_dir():
        raise WorkstationError(
            "Workstation snapshot bundle is missing or unsafe.",
            error_code="WORKSTATION_SNAPSHOT_BUNDLE_INVALID",
        )
    envelope = _load_snapshot_envelope(envelope_path)
    if envelope["original_bundle_id"] != bundle_id:
        raise WorkstationError(
            "Workstation snapshot identity mismatch.",
            error_code="WORKSTATION_SNAPSHOT_IDENTITY_MISMATCH",
        )
    identity = capture_bundle_identity(bundle_dir, bundle_id=bundle_id)
    if (
        identity.manifest_sha256 != envelope["manifest_sha256"]
        or identity.manifest_size_bytes != envelope["manifest_size_bytes"]
        or identity.catalog_sha256 != envelope["catalog_sha256"]
        or identity.catalog_size_bytes != envelope["catalog_size_bytes"]
        or identity.alembic_revision != envelope["alembic_revision"]
    ):
        raise WorkstationError(
            "Workstation snapshot envelope no longer matches bundle bytes.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_MISMATCH",
        )
    verified = verify_catalog_backup(bundle_dir)
    semantic: Mapping[str, Any] = envelope["semantic"]
    if restore_verify:
        disposable = _disposable_restore_path(
            store.store_root,
            operation_id=f"verify-{secrets.token_hex(6)}",
        )
        try:
            semantic = _restore_verify_bundle(
                bundle_dir,
                disposable=disposable,
                expected_revision=verified.alembic_revision,
                expected_semantic=envelope["semantic"],
                hooks=probe,
            )
        finally:
            _cleanup_disposable(disposable)
    return {
        "bundle_id": bundle_id,
        "accepted_at_utc": envelope["accepted_at_utc"],
        "alembic_revision": verified.alembic_revision,
        "catalog_sha256": verified.catalog_sha256,
        "catalog_size_bytes": verified.catalog_size_bytes,
        "manifest_sha256": identity.manifest_sha256,
        "manifest_size_bytes": identity.manifest_size_bytes,
        "bundle_verification": "verified",
        "disposable_restore_verification": "verified" if restore_verify else "skipped",
        "semantic": dict(semantic),
    }


def validate_ssh_target(raw: str) -> str:
    """Validate caller-controlled SSH destination conservatively."""
    if not isinstance(raw, str) or not raw or len(raw) > 255:
        raise WorkstationError(
            "SSH target is invalid.",
            error_code="WORKSTATION_SSH_TARGET_INVALID",
        )
    if raw.startswith("-"):
        raise WorkstationError(
            "SSH target must not look like an OpenSSH option.",
            error_code="WORKSTATION_SSH_TARGET_INVALID",
        )
    if CONTROL_CHAR_PATTERN.search(raw):
        raise WorkstationError(
            "SSH target contains unsafe characters.",
            error_code="WORKSTATION_SSH_TARGET_INVALID",
        )
    if any(ch in UNSAFE_TARGET_CHARS for ch in raw):
        raise WorkstationError(
            "SSH target contains unsafe characters.",
            error_code="WORKSTATION_SSH_TARGET_INVALID",
        )
    if raw.count("@") > 1:
        raise WorkstationError(
            "SSH target is invalid.",
            error_code="WORKSTATION_SSH_TARGET_INVALID",
        )
    return raw


def resolve_remote_layout_command(remote_layout: str) -> tuple[str, ...]:
    """Select one fixed remote export command tuple by layout key.

    An unknown key is refused before any process is started, so the temporary
    workstation selection can never execute an arbitrary command.
    """
    command = REMOTE_LAYOUT_COMMANDS.get(remote_layout)
    if command is None:
        raise WorkstationError(
            "Workstation remote layout is invalid.",
            error_code="WORKSTATION_REMOTE_LAYOUT_INVALID",
        )
    return command


def build_ssh_argv(
    *,
    target: str,
    ssh_executable: str = DEFAULT_SSH_EXECUTABLE,
    ssh_port: int | None = None,
    connect_timeout_seconds: int = 30,
    remote_layout: str = DEFAULT_REMOTE_LAYOUT,
) -> list[str]:
    """Build argv for system OpenSSH with a fixed remote export command."""
    if not isinstance(ssh_executable, str) or not ssh_executable or ssh_executable.startswith("-"):
        raise WorkstationError(
            "SSH executable is invalid.",
            error_code="WORKSTATION_SSH_EXECUTABLE_INVALID",
        )
    if "/" in ssh_executable and not Path(ssh_executable).is_absolute():
        raise WorkstationError(
            "SSH executable is invalid.",
            error_code="WORKSTATION_SSH_EXECUTABLE_INVALID",
        )
    if ssh_port is not None and (not isinstance(ssh_port, int) or isinstance(ssh_port, bool)):
        raise WorkstationError(
            "SSH port is invalid.",
            error_code="WORKSTATION_SSH_PORT_INVALID",
        )
    if ssh_port is not None and (ssh_port < 1 or ssh_port > 65535):
        raise WorkstationError(
            "SSH port is invalid.",
            error_code="WORKSTATION_SSH_PORT_INVALID",
        )
    timeout = max(1, int(connect_timeout_seconds))
    argv = [
        ssh_executable,
        "-o",
        "BatchMode=yes",
        "-o",
        "RequestTTY=no",
        "-o",
        "ForwardAgent=no",
        "-o",
        "ForwardX11=no",
        "-o",
        "ClearAllForwardings=yes",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        f"ConnectTimeout={timeout}",
        "-o",
        "ServerAliveInterval=15",
        "-o",
        "ServerAliveCountMax=3",
        "-o",
        "StrictHostKeyChecking=yes",
    ]
    if ssh_port is not None:
        argv.extend(["-p", str(ssh_port)])
    argv.append(target)
    argv.extend(resolve_remote_layout_command(remote_layout))
    return argv


def _validate_mount_root(mount: Path, *, hooks: WorkstationOsHooks) -> int:
    if mount.is_symlink() or not mount.exists() or not mount.is_dir():
        raise WorkstationError(
            "Workstation mount root is unavailable.",
            error_code="WORKSTATION_MOUNT_UNAVAILABLE",
        )
    if not hooks.is_mountpoint(mount):
        raise WorkstationError(
            "Workstation mount root is not a mount point.",
            error_code="WORKSTATION_MOUNT_NOT_MOUNT",
        )
    try:
        mount_dev = hooks.device_id(mount)
        parent_dev = hooks.device_id(mount.parent)
    except OSError as exc:
        raise WorkstationError(
            "Workstation mount root is unavailable.",
            error_code="WORKSTATION_MOUNT_UNAVAILABLE",
        ) from exc
    if mount_dev == parent_dev:
        raise WorkstationError(
            "Workstation mount root shares its parent filesystem.",
            error_code="WORKSTATION_MOUNT_SAME_DEVICE",
        )
    return mount_dev


def _write_new_marker(store: Path, *, store_id: str, hooks: WorkstationOsHooks) -> None:
    marker = store / MARKER_NAME
    if marker.exists() or marker.is_symlink():
        raise WorkstationError(
            "Workstation snapshot store marker already exists.",
            error_code="WORKSTATION_MARKER_EXISTS",
        )
    payload = {
        "schema_version": MARKER_SCHEMA_VERSION,
        "purpose": MARKER_PURPOSE,
        "store_id": store_id,
    }
    _atomic_write_json(marker, payload, hooks=hooks)


def _atomic_write_json(path: Path, payload: Mapping[str, Any], *, hooks: WorkstationOsHooks) -> None:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(tmp, "xb", buffering=0) as handle:
            handle.write(encoded)
            handle.flush()
            hooks.fsync(handle.fileno())
        os.chmod(tmp, PRIVATE_FILE_MODE)
        os.rename(tmp, path)
        os.chmod(path, PRIVATE_FILE_MODE)
    except Exception:
        if tmp.exists() and tmp.is_file() and not tmp.is_symlink():
            tmp.unlink()
        raise


def _build_snapshot_envelope(
    *,
    identity: BundleIdentity,
    semantic: Mapping[str, Any],
    accepted_at: datetime,
) -> dict[str, Any]:
    return {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "purpose": SNAPSHOT_PURPOSE,
        "accepted_at_utc": accepted_at.astimezone(UTC).replace(microsecond=0).isoformat().replace(
            "+00:00", "Z"
        ),
        "transfer_protocol": TRANSFER_PROTOCOL_NAME,
        "transfer_protocol_version": 1,
        "original_bundle_id": identity.bundle_id,
        "manifest_sha256": identity.manifest_sha256,
        "manifest_size_bytes": identity.manifest_size_bytes,
        "catalog_sha256": identity.catalog_sha256,
        "catalog_size_bytes": identity.catalog_size_bytes,
        "alembic_revision": identity.alembic_revision,
        "bundle_verification": "verified",
        "disposable_restore_verification": "verified",
        "semantic": dict(semantic),
    }


def _load_snapshot_envelope(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise WorkstationError(
            "Workstation snapshot envelope is malformed.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_MALFORMED",
        ) from exc
    if not isinstance(payload, dict):
        raise WorkstationError(
            "Workstation snapshot envelope is malformed.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_MALFORMED",
        )
    required = {
        "schema_version",
        "purpose",
        "accepted_at_utc",
        "transfer_protocol",
        "transfer_protocol_version",
        "original_bundle_id",
        "manifest_sha256",
        "manifest_size_bytes",
        "catalog_sha256",
        "catalog_size_bytes",
        "alembic_revision",
        "bundle_verification",
        "disposable_restore_verification",
        "semantic",
    }
    if set(payload.keys()) != required:
        raise WorkstationError(
            "Workstation snapshot envelope schema is invalid.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_INVALID",
        )
    if payload.get("schema_version") != SNAPSHOT_SCHEMA_VERSION:
        raise WorkstationError(
            "Workstation snapshot envelope schema is unsupported.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_UNSUPPORTED",
        )
    if payload.get("purpose") not in ACCEPTED_SNAPSHOT_PURPOSES:
        raise WorkstationError(
            "Workstation snapshot envelope purpose mismatch.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_PURPOSE_MISMATCH",
        )
    if payload.get("transfer_protocol") not in ACCEPTED_TRANSFER_PROTOCOL_NAMES:
        raise WorkstationError(
            "Workstation snapshot envelope protocol mismatch.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_PROTOCOL_MISMATCH",
        )
    if payload.get("transfer_protocol_version") != 1:
        raise WorkstationError(
            "Workstation snapshot envelope protocol mismatch.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_PROTOCOL_MISMATCH",
        )
    if payload.get("bundle_verification") != "verified":
        raise WorkstationError(
            "Workstation snapshot envelope verification state is invalid.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_INVALID",
        )
    if payload.get("disposable_restore_verification") != "verified":
        raise WorkstationError(
            "Workstation snapshot envelope verification state is invalid.",
            error_code="WORKSTATION_SNAPSHOT_ENVELOPE_INVALID",
        )
    return payload


def _verify_existing_final(
    *,
    final_path: Path,
    expected_header: Mapping[str, Any],
    store: ValidatedWorkstationStore,
    hooks: WorkstationOsHooks,
) -> PullResult:
    if final_path.is_symlink() or not final_path.is_dir():
        raise WorkstationError(
            "Workstation snapshot conflicts with an existing final directory.",
            error_code="WORKSTATION_SNAPSHOT_CONFLICT",
        )
    try:
        payload = verify_workstation_snapshot(
            store_root=store.store_root,
            mount_root=store.mount_root,
            expected_store_id=store.store_id,
            bundle_id=final_path.name,
            hooks=hooks,
            restore_verify=True,
        )
    except WorkstationError as exc:
        raise WorkstationError(
            "Workstation snapshot conflicts with an existing final directory.",
            error_code="WORKSTATION_SNAPSHOT_CONFLICT",
        ) from exc
    if (
        payload["catalog_sha256"] != expected_header["catalog_sha256"]
        or payload["catalog_size_bytes"] != expected_header["catalog_size_bytes"]
        or payload["alembic_revision"] != expected_header["alembic_revision"]
        or payload["manifest_sha256"] != expected_header["manifest_sha256"]
        or payload["manifest_size_bytes"] != expected_header["manifest_size_bytes"]
    ):
        raise WorkstationError(
            "Workstation snapshot conflicts with an existing final directory.",
            error_code="WORKSTATION_SNAPSHOT_CONFLICT",
        )
    return PullResult(
        bundle_id=payload["bundle_id"],
        catalog_sha256=payload["catalog_sha256"],
        alembic_revision=payload["alembic_revision"],
        catalog_size_bytes=payload["catalog_size_bytes"],
        reused_existing=True,
        semantic=payload["semantic"],
    )


def _restore_verify_bundle(
    bundle: Path,
    *,
    disposable: Path,
    expected_revision: str,
    expected_semantic: Mapping[str, Any],
    hooks: WorkstationOsHooks,
) -> dict[str, Any]:
    if disposable.exists() or disposable.is_symlink():
        raise WorkstationError(
            "Disposable restore destination already exists.",
            error_code="WORKSTATION_RESTORE_DEST_EXISTS",
        )
    disposable.parent.mkdir(mode=PRIVATE_DIR_MODE, exist_ok=True)
    os.chmod(disposable.parent, PRIVATE_DIR_MODE)
    restore_catalog_backup(bundle, disposable)
    _verify_sqlite(disposable)
    semantic = capture_semantic_snapshot(disposable)
    payload = {
        "alembic_revision": semantic.alembic_revision,
        "counts": dict(semantic.counts),
    }
    if semantic.alembic_revision != expected_revision:
        raise WorkstationError(
            "Disposable restore revision mismatch.",
            error_code="WORKSTATION_RESTORE_REVISION_MISMATCH",
        )
    if dict(payload) != dict(expected_semantic):
        raise WorkstationError(
            "Disposable restore semantic mismatch.",
            error_code="WORKSTATION_RESTORE_SEMANTIC_MISMATCH",
        )
    return payload


def _verify_sqlite(database: Path) -> None:
    try:
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                raise WorkstationError(
                    "Restored catalog failed integrity check.",
                    error_code="WORKSTATION_RESTORE_INTEGRITY_FAILED",
                )
            fk_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if fk_rows:
                raise WorkstationError(
                    "Restored catalog failed foreign-key check.",
                    error_code="WORKSTATION_RESTORE_FK_FAILED",
                )
    except WorkstationError:
        raise
    except sqlite3.Error as exc:
        raise WorkstationError(
            "Restored catalog could not be verified.",
            error_code="WORKSTATION_RESTORE_VERIFY_FAILED",
        ) from exc


def _disposable_restore_path(store_root: Path, *, operation_id: str) -> Path:
    restore_root = store_root / RESTORE_VERIFY_DIRNAME
    path = restore_root / f"{operation_id}.sqlite3"
    _assert_contained(path, restore_root)
    return path


def _cleanup_disposable(path: Path | None) -> None:
    if path is None:
        return
    if not path.exists() and not path.is_symlink():
        return
    if path.is_symlink() or not path.is_file():
        raise WorkstationError(
            "Unexpected disposable restore artifact.",
            error_code="WORKSTATION_RESTORE_CLEANUP_UNSAFE",
        )
    if not path.name.endswith(".sqlite3"):
        raise WorkstationError(
            "Unexpected disposable restore artifact.",
            error_code="WORKSTATION_RESTORE_CLEANUP_UNSAFE",
        )
    path.unlink()


def _cleanup_owned_stage(stage: Path, snapshots_dir: Path) -> None:
    if not stage.exists() and not stage.is_symlink():
        return
    _assert_contained(stage, snapshots_dir)
    if not stage.name.startswith(ACCEPTED_STAGE_PREFIXES):
        raise WorkstationError(
            "Unexpected workstation staging object.",
            error_code="WORKSTATION_STAGE_UNSAFE",
        )
    if stage.is_symlink() or not stage.is_dir():
        raise WorkstationError(
            "Unexpected workstation staging object.",
            error_code="WORKSTATION_STAGE_UNSAFE",
        )
    for child in list(stage.iterdir()):
        if child.is_symlink():
            raise WorkstationError(
                "Unexpected workstation staging object.",
                error_code="WORKSTATION_STAGE_UNSAFE",
            )
        if child.name == SNAPSHOT_NAME and child.is_file():
            child.unlink()
            continue
        if child.name == BUNDLE_DIRNAME and child.is_dir():
            for nested in list(child.iterdir()):
                if nested.is_symlink() or not nested.is_file():
                    raise WorkstationError(
                        "Unexpected workstation staging object.",
                        error_code="WORKSTATION_STAGE_UNSAFE",
                    )
                if nested.name not in {MANIFEST_NAME, CATALOG_NAME}:
                    raise WorkstationError(
                        "Unexpected workstation staging object.",
                        error_code="WORKSTATION_STAGE_UNSAFE",
                    )
                nested.unlink()
            child.rmdir()
            continue
        raise WorkstationError(
            "Unexpected workstation staging object.",
            error_code="WORKSTATION_STAGE_UNSAFE",
        )
    stage.rmdir()


def _assert_identity_matches_header(identity: BundleIdentity, header: Mapping[str, Any]) -> None:
    if (
        identity.bundle_id != header["bundle_id"]
        or identity.manifest_sha256 != header["manifest_sha256"]
        or identity.manifest_size_bytes != header["manifest_size_bytes"]
        or identity.catalog_sha256 != header["catalog_sha256"]
        or identity.catalog_size_bytes != header["catalog_size_bytes"]
        or identity.alembic_revision != header["alembic_revision"]
    ):
        raise WorkstationError(
            "Received bundle identity does not match protocol header.",
            error_code="WORKSTATION_HEADER_IDENTITY_MISMATCH",
        )


def _read_preamble_with_timeout(
    process: subprocess.Popen[bytes],
    *,
    deadline: float,
) -> dict[str, Any]:
    assert process.stdout is not None
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WorkstationError(
            "Workstation pull timed out.",
            error_code="WORKSTATION_TRANSFER_TIMEOUT",
        )
    stdout = process.stdout
    try:
        return read_protocol_v1_preamble(stdout, deadline=deadline)
    except TransferError as exc:
        if exc.error_code == "TRANSFER_TIMEOUT":
            raise WorkstationError(
                "Workstation pull timed out.",
                error_code="WORKSTATION_TRANSFER_TIMEOUT",
            ) from exc
        raise
    except BrokenPipeError as exc:
        raise WorkstationError(
            "Workstation pull pipe broke.",
            error_code="WORKSTATION_BROKEN_PIPE",
        ) from exc


def _wait_process(process: subprocess.Popen[bytes], *, deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise WorkstationError(
            "Workstation pull timed out.",
            error_code="WORKSTATION_TRANSFER_TIMEOUT",
        )
    try:
        process.wait(timeout=remaining)
    except subprocess.TimeoutExpired as exc:
        _terminate_and_reap(process, deadline=time.monotonic() + 5)
        raise WorkstationError(
            "Workstation pull timed out.",
            error_code="WORKSTATION_TRANSFER_TIMEOUT",
        ) from exc


def _terminate_and_reap(process: subprocess.Popen[bytes], *, deadline: float) -> None:
    if process.poll() is not None:
        return
    process.send_signal(signal.SIGTERM)
    remaining = max(0.1, deadline - time.monotonic())
    try:
        process.wait(timeout=remaining)
        return
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _drain_stderr(stream: Any, state: dict[str, Any]) -> None:
    try:
        while True:
            chunk = stream.read(4096)
            if not chunk:
                break
            state["bytes"] = int(state["bytes"]) + len(chunk)
            prefix: bytearray = state["prefix"]
            if len(prefix) < STDERR_CAP_BYTES:
                room = STDERR_CAP_BYTES - len(prefix)
                prefix.extend(chunk[:room])
                if len(chunk) > room:
                    state["truncated"] = True
            else:
                state["truncated"] = True
    except Exception:
        return


def _classify_remote_exit(code: int | None) -> WorkstationError:
    if code == 255:
        return WorkstationError(
            "SSH transport or authentication failed.",
            error_code="WORKSTATION_SSH_TRANSPORT",
        )
    return WorkstationError(
        "Remote catalog export failed.",
        error_code="WORKSTATION_REMOTE_EXPORT_FAILED",
    )


def _require_absolute(path: Path, *, description: str) -> Path:
    if not path.is_absolute():
        raise WorkstationError(
            f"{description.title()} must be absolute.",
            error_code="WORKSTATION_PATH_INVALID",
        )
    return path


def _assert_store_under_mount(store: Path, mount: Path) -> None:
    try:
        store.resolve(strict=False).relative_to(mount.resolve(strict=False))
    except ValueError as exc:
        raise WorkstationError(
            "Workstation snapshot store is outside the mount root.",
            error_code="WORKSTATION_STORE_OUTSIDE_MOUNT",
        ) from exc
    if store == mount:
        raise WorkstationError(
            "Workstation snapshot store must be strictly beneath the mount root.",
            error_code="WORKSTATION_STORE_OUTSIDE_MOUNT",
        )


def _assert_same_device(path: Path, mount: Path, *, hooks: WorkstationOsHooks) -> None:
    try:
        if hooks.device_id(path) != hooks.device_id(mount):
            raise WorkstationError(
                "Workstation path is not on the mounted filesystem.",
                error_code="WORKSTATION_STORE_DEVICE_MISMATCH",
            )
    except OSError as exc:
        raise WorkstationError(
            "Workstation path is unavailable.",
            error_code="WORKSTATION_STORE_UNAVAILABLE",
        ) from exc


def _assert_contained(path: Path, root: Path) -> None:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except (OSError, ValueError) as exc:
        raise WorkstationError(
            "Workstation path escaped trusted layout.",
            error_code="WORKSTATION_PATH_ESCAPE",
        ) from exc
    if path.is_absolute() and ".." in path.parts:
        raise WorkstationError(
            "Workstation path escaped trusted layout.",
            error_code="WORKSTATION_PATH_ESCAPE",
        )
