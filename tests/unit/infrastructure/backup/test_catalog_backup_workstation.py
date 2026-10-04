"""Unit tests for workstation snapshot store trust, pull, and offline verify."""

from __future__ import annotations

from datetime import UTC, datetime
import errno
import io
import json
import os
from pathlib import Path
import threading

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

STORE_ID = "0123456789abcdef0123456789abcdef"


def _migrated_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def _ops_config(tmp_path: Path):
    from kronika.infrastructure.persistence.catalog_backup_ops import CatalogBackupOpsConfig

    return CatalogBackupOpsConfig(
        database_path=_migrated_database(tmp_path / "catalog.sqlite3"),
        backup_root=tmp_path / "catalog-backups",
        restore_verify_root=tmp_path / "catalog-restore-verify",
        ops_root=tmp_path / "catalog-backup-ops",
        keep_auto=30,
    )


def _test_rename_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
    os.rename(source, destination)


def _hooks(mount_root: Path, *, is_mountpoint: bool = True, same_as_parent: bool = False):
    from kronika.infrastructure.persistence.catalog_backup_workstation import WorkstationOsHooks

    mount_resolved = mount_root.resolve()

    def _device_id(path: Path) -> int:
        resolved = path.resolve()
        if same_as_parent:
            return 1
        if resolved == mount_resolved or mount_resolved in resolved.parents:
            return 42
        if resolved == mount_resolved.parent:
            return 7
        return 7

    return WorkstationOsHooks(
        is_mountpoint=lambda path: is_mountpoint and path.resolve() == mount_resolved,
        device_id=_device_id,
        rename_noreplace=_test_rename_noreplace,
        geteuid=os.geteuid,
    )


def _prepare_mount(tmp_path: Path) -> tuple[Path, Path]:
    mount = tmp_path / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    store = mount / "framenest_backups"
    return mount, store


def test_init_store_creates_marker_and_is_idempotent(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        init_workstation_store,
        validate_workstation_store,
    )

    mount, store = _prepare_mount(tmp_path)
    hooks = _hooks(mount)
    created = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)
    assert created.created is True
    assert len(created.store_id) == 32
    again = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)
    assert again.created is False
    assert again.store_id == created.store_id
    validated = validate_workstation_store(
        store_root=store,
        mount_root=mount,
        expected_store_id=created.store_id,
        hooks=hooks,
    )
    assert validated.store_id == created.store_id


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permission bits")
def test_init_store_rejects_unsafe_mode(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
    )

    mount, store = _prepare_mount(tmp_path)
    store.mkdir(mode=0o755)
    os.chmod(store, 0o755)
    with pytest.raises(WorkstationError) as exc:
        init_workstation_store(store_root=store, mount_root=mount, hooks=_hooks(mount))
    assert exc.value.error_code == "WORKSTATION_STORE_MODE_UNSAFE"


def test_init_store_rejects_nonempty_without_marker(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
    )

    mount, store = _prepare_mount(tmp_path)
    store.mkdir(mode=0o700)
    (store / "noise.txt").write_text("x", encoding="utf-8")
    with pytest.raises(WorkstationError) as exc:
        init_workstation_store(store_root=store, mount_root=mount, hooks=_hooks(mount))
    assert exc.value.error_code == "WORKSTATION_STORE_NONEMPTY"


def test_store_trust_missing_mount_and_symlink(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
        validate_workstation_store,
    )

    mount, store = _prepare_mount(tmp_path)
    with pytest.raises(WorkstationError) as exc:
        init_workstation_store(
            store_root=store,
            mount_root=mount,
            hooks=_hooks(mount, is_mountpoint=False),
        )
    assert exc.value.error_code == "WORKSTATION_MOUNT_NOT_MOUNT"

    with pytest.raises(WorkstationError) as exc:
        init_workstation_store(
            store_root=store,
            mount_root=mount,
            hooks=_hooks(mount, same_as_parent=True),
        )
    assert exc.value.error_code == "WORKSTATION_MOUNT_SAME_DEVICE"

    created = init_workstation_store(store_root=store, mount_root=mount, hooks=_hooks(mount))
    outside = tmp_path / "outside-store"
    outside.mkdir()
    with pytest.raises(WorkstationError) as exc:
        validate_workstation_store(
            store_root=outside,
            mount_root=mount,
            expected_store_id=created.store_id,
            hooks=_hooks(mount),
        )
    assert exc.value.error_code == "WORKSTATION_STORE_OUTSIDE_MOUNT"


def test_store_id_mismatch_and_invalid_marker(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        MARKER_NAME,
        WorkstationError,
        init_workstation_store,
        validate_workstation_store,
    )

    mount, store = _prepare_mount(tmp_path)
    created = init_workstation_store(store_root=store, mount_root=mount, hooks=_hooks(mount))
    with pytest.raises(WorkstationError) as exc:
        validate_workstation_store(
            store_root=store,
            mount_root=mount,
            expected_store_id=STORE_ID,
            hooks=_hooks(mount),
        )
    assert exc.value.error_code == "WORKSTATION_STORE_ID_MISMATCH"
    assert created.store_id != STORE_ID

    marker = store / MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "wrong-purpose",
                "store_id": created.store_id,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(WorkstationError) as exc:
        validate_workstation_store(
            store_root=store,
            mount_root=mount,
            expected_store_id=created.store_id,
            hooks=_hooks(mount),
        )
    assert exc.value.error_code == "WORKSTATION_MARKER_PURPOSE_MISMATCH"


def test_ssh_target_injection_and_fixed_remote_command() -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        FIXED_REMOTE_EXPORT_COMMAND,
        WorkstationError,
        build_ssh_argv,
        validate_ssh_target,
    )

    with pytest.raises(WorkstationError):
        validate_ssh_target("-oProxyCommand=evil")
    with pytest.raises(WorkstationError):
        validate_ssh_target("host;rm")
    with pytest.raises(WorkstationError):
        validate_ssh_target("host\n")
    argv = build_ssh_argv(target="nuc-alias", ssh_port=22)
    assert argv[0] == "ssh"
    assert "shell=True" not in argv
    assert argv[-len(FIXED_REMOTE_EXPORT_COMMAND) :] == list(FIXED_REMOTE_EXPORT_COMMAND)
    assert "nuc-alias" in argv
    assert "-p" in argv and "22" in argv


def test_export_latest_source_selection_and_lock(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        export_latest_scheduled_recovery_point,
        operation_lock,
        run_scheduled_catalog_backup,
        select_latest_successful_scheduled_recovery_point,
    )

    config = _ops_config(tmp_path)
    with pytest.raises(CatalogBackupOpsError) as exc:
        select_latest_successful_scheduled_recovery_point(config)
    assert exc.value.error_code == "SCHEDULED_SOURCE_UNAVAILABLE"

    scheduled = run_scheduled_catalog_backup(
        config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC)
    )
    selected = select_latest_successful_scheduled_recovery_point(config)
    assert selected.identity.bundle_id == scheduled.bundle_id

    # Non-automatic ID rejected.
    status_path = config.ops_root / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["last_successful_scheduled_backup_and_restore"]["bundle_id"] = "pinned-bundle"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(CatalogBackupOpsError) as exc:
        select_latest_successful_scheduled_recovery_point(config)
    assert exc.value.error_code == "SCHEDULED_SOURCE_INVALID"

    # Restore valid success and stream export.
    status["last_successful_scheduled_backup_and_restore"]["bundle_id"] = scheduled.bundle_id
    status_path.write_text(json.dumps(status), encoding="utf-8")
    buffer = io.BytesIO()
    identity = export_latest_scheduled_recovery_point(config, buffer)
    assert identity.bundle_id == scheduled.bundle_id
    assert buffer.getvalue().startswith(b"FNCBE01\0")

    # Lock contention.
    with operation_lock(config) as held:
        assert held is True

        def _contender() -> None:
            with operation_lock(config) as inner:
                assert inner is False

        thread = threading.Thread(target=_contender)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()


def test_export_rejects_symlink_and_evidence_mismatch(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        CatalogBackupOpsError,
        run_scheduled_catalog_backup,
        select_latest_successful_scheduled_recovery_point,
    )

    config = _ops_config(tmp_path)
    scheduled = run_scheduled_catalog_backup(
        config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC)
    )
    bundle = config.backup_root / scheduled.bundle_id
    replaced = tmp_path / "replaced"
    replaced.mkdir()
    os.rename(bundle, replaced)
    bundle.symlink_to(replaced)
    with pytest.raises(CatalogBackupOpsError) as exc:
        select_latest_successful_scheduled_recovery_point(config)
    assert exc.value.error_code == "SCHEDULED_SOURCE_MISSING"

    os.unlink(bundle)
    os.rename(replaced, bundle)
    status_path = config.ops_root / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8"))
    status["last_successful_scheduled_backup_and_restore"]["catalog_sha256"] = "0" * 64
    status_path.write_text(json.dumps(status), encoding="utf-8")
    with pytest.raises(CatalogBackupOpsError) as exc:
        select_latest_successful_scheduled_recovery_point(config)
    assert exc.value.error_code == "SCHEDULED_SOURCE_EVIDENCE_MISMATCH"


def test_pull_success_idempotent_conflict_and_remote_nonzero(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        export_latest_scheduled_recovery_point,
        run_scheduled_catalog_backup,
    )
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
        list_workstation_snapshots,
        pull_workstation_snapshot,
        verify_workstation_snapshot,
    )

    config = _ops_config(tmp_path / "nuc")
    scheduled = run_scheduled_catalog_backup(
        config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC)
    )
    payload = io.BytesIO()
    export_latest_scheduled_recovery_point(config, payload)
    stream_bytes = payload.getvalue()

    mount, store = _prepare_mount(tmp_path / "ws")
    hooks = _hooks(mount)
    init = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)

    class _FakeProc:
        def __init__(self, data: bytes, returncode: int = 0) -> None:
            self.stdout = io.BytesIO(data)
            self.stderr = io.BytesIO(b"")
            self.returncode = returncode
            self._alive = True

        def poll(self):
            return None if self._alive else self.returncode

        def wait(self, timeout=None):
            self._alive = False
            return self.returncode

        def send_signal(self, _sig):
            self._alive = False

        def kill(self):
            self._alive = False

    def _popen_ok(*_a, **_k):
        return _FakeProc(stream_bytes, returncode=0)

    result = pull_workstation_snapshot(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        ssh_target="nuc",
        hooks=hooks,
        popen=_popen_ok,
    )
    assert result.bundle_id == scheduled.bundle_id
    assert result.reused_existing is False
    listed = list_workstation_snapshots(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        hooks=hooks,
    )
    assert listed[0]["bundle_id"] == scheduled.bundle_id
    verified = verify_workstation_snapshot(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        bundle_id=scheduled.bundle_id,
        hooks=hooks,
    )
    assert verified["bundle_verification"] == "verified"

    reused = pull_workstation_snapshot(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        ssh_target="nuc",
        hooks=hooks,
        popen=_popen_ok,
    )
    assert reused.reused_existing is True

    # Conflicting final: corrupt catalog digest in envelope path by rewriting snapshot.
    snapshot_dir = store / "snapshots" / scheduled.bundle_id
    conflict_payload = json.loads((snapshot_dir / "snapshot.json").read_text(encoding="utf-8"))
    conflict_payload["catalog_sha256"] = "f" * 64
    (snapshot_dir / "snapshot.json").write_text(
        json.dumps(conflict_payload, sort_keys=True), encoding="utf-8"
    )
    with pytest.raises(WorkstationError) as exc:
        pull_workstation_snapshot(
            store_root=store,
            mount_root=mount,
            expected_store_id=init.store_id,
            ssh_target="nuc",
            hooks=hooks,
            popen=_popen_ok,
        )
    assert exc.value.error_code == "WORKSTATION_SNAPSHOT_CONFLICT"

    def _popen_fail(*_a, **_k):
        return _FakeProc(stream_bytes, returncode=1)

    # Remove conflict by deleting only after restoring envelope for remote-fail path on fresh store.
    mount2, store2 = _prepare_mount(tmp_path / "ws2")
    hooks2 = _hooks(mount2)
    init2 = init_workstation_store(store_root=store2, mount_root=mount2, hooks=hooks2)
    with pytest.raises(WorkstationError) as exc:
        pull_workstation_snapshot(
            store_root=store2,
            mount_root=mount2,
            expected_store_id=init2.store_id,
            ssh_target="nuc",
            hooks=hooks2,
            popen=_popen_fail,
        )
    assert exc.value.error_code == "WORKSTATION_REMOTE_EXPORT_FAILED"

    def _popen_ssh255(*_a, **_k):
        return _FakeProc(b"", returncode=255)

    with pytest.raises(WorkstationError) as exc:
        pull_workstation_snapshot(
            store_root=store2,
            mount_root=mount2,
            expected_store_id=init2.store_id,
            ssh_target="nuc",
            hooks=hooks2,
            popen=_popen_ssh255,
        )
    assert exc.value.error_code == "WORKSTATION_SSH_TRANSPORT"


def test_stderr_flood_does_not_deadlock(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        export_latest_scheduled_recovery_point,
        run_scheduled_catalog_backup,
    )
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        STDERR_CAP_BYTES,
        init_workstation_store,
        pull_workstation_snapshot,
    )

    config = _ops_config(tmp_path / "nuc")
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC))
    payload = io.BytesIO()
    export_latest_scheduled_recovery_point(config, payload)
    stream_bytes = payload.getvalue()

    mount, store = _prepare_mount(tmp_path / "ws")
    hooks = _hooks(mount)
    init = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)

    class _FloodStderr(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(b"x" * (STDERR_CAP_BYTES * 4))

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(stream_bytes)
            self.stderr = _FloodStderr()
            self.returncode = 0
            self._alive = True

        def poll(self):
            return None if self._alive else self.returncode

        def wait(self, timeout=None):
            self._alive = False
            return self.returncode

        def send_signal(self, _sig):
            self._alive = False

        def kill(self):
            self._alive = False

    result = pull_workstation_snapshot(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        ssh_target="nuc",
        hooks=hooks,
        popen=lambda *_a, **_k: _FakeProc(),
    )
    assert result.bundle_id


def test_spawn_failure_and_missing_executable(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
        pull_workstation_snapshot,
    )

    mount, store = _prepare_mount(tmp_path)
    hooks = _hooks(mount)
    init = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)

    def _missing(*_a, **_k):
        raise FileNotFoundError("ssh")

    with pytest.raises(WorkstationError) as exc:
        pull_workstation_snapshot(
            store_root=store,
            mount_root=mount,
            expected_store_id=init.store_id,
            ssh_target="nuc",
            hooks=hooks,
            popen=_missing,
        )
    assert exc.value.error_code == "WORKSTATION_SSH_MISSING"


def test_offline_verify_rejects_corrupt_envelope(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        export_latest_scheduled_recovery_point,
        run_scheduled_catalog_backup,
    )
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
        pull_workstation_snapshot,
        verify_workstation_snapshot,
    )

    config = _ops_config(tmp_path / "nuc")
    scheduled = run_scheduled_catalog_backup(
        config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC)
    )
    payload = io.BytesIO()
    export_latest_scheduled_recovery_point(config, payload)

    mount, store = _prepare_mount(tmp_path / "ws")
    hooks = _hooks(mount)
    init = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = io.BytesIO(payload.getvalue())
            self.stderr = io.BytesIO(b"")
            self.returncode = 0
            self._alive = True

        def poll(self):
            return None if self._alive else self.returncode

        def wait(self, timeout=None):
            self._alive = False
            return self.returncode

        def send_signal(self, _sig):
            self._alive = False

        def kill(self):
            self._alive = False

    pull_workstation_snapshot(
        store_root=store,
        mount_root=mount,
        expected_store_id=init.store_id,
        ssh_target="nuc",
        hooks=hooks,
        popen=lambda *_a, **_k: _FakeProc(),
    )
    envelope = store / "snapshots" / scheduled.bundle_id / "snapshot.json"
    envelope.write_text("{not-json", encoding="utf-8")
    with pytest.raises(WorkstationError) as exc:
        verify_workstation_snapshot(
            store_root=store,
            mount_root=mount,
            expected_store_id=init.store_id,
            bundle_id=scheduled.bundle_id,
            hooks=hooks,
        )
    assert exc.value.error_code == "WORKSTATION_SNAPSHOT_ENVELOPE_MALFORMED"


def test_real_pipe_body_stall_times_out_and_reaps_child(tmp_path: Path) -> None:
    """A3-F01: valid preamble + stalled body must fire transfer timeout on a real pipe."""
    import subprocess
    import sys
    import time

    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        init_workstation_store,
        pull_workstation_snapshot,
    )

    mount, store = _prepare_mount(tmp_path)
    hooks = _hooks(mount)
    init = init_workstation_store(store_root=store, mount_root=mount, hooks=hooks)

    helper = tmp_path / "stall_after_preamble_helper.py"
    helper.write_text(
        "\n".join(
            [
                "import json, sys, time",
                "header = {",
                '  "protocol_version": 1,',
                '  "bundle_id": "auto-20260808T031700Z-abcd1234",',
                '  "manifest_filename": "manifest.json",',
                '  "catalog_filename": "catalog.sqlite3",',
                '  "manifest_size_bytes": 1024,',
                '  "manifest_sha256": "a" * 64,',
                '  "catalog_size_bytes": 4096,',
                '  "catalog_sha256": "b" * 64,',
                '  "alembic_revision": "0028",',
                '  "semantic": {"alembic_revision": "0028", "counts": {}},',
                "}",
                'encoded = json.dumps(header, sort_keys=True, separators=(",", ":")).encode()',
                'sys.stdout.buffer.write(b"FNCBE01\\0")',
                "sys.stdout.buffer.write(len(encoded).to_bytes(4, 'big'))",
                "sys.stdout.buffer.write(encoded)",
                "sys.stdout.buffer.flush()",
                "while True:",
                "    time.sleep(3600)",
                "",
            ]
        ),
        encoding="utf-8",
    )

    child_holder: dict[str, subprocess.Popen[bytes] | None] = {"proc": None}

    def _popen(argv, **kwargs):  # noqa: ANN001
        proc = subprocess.Popen(
            [sys.executable, str(helper)],
            stdin=kwargs.get("stdin"),
            stdout=kwargs.get("stdout"),
            stderr=kwargs.get("stderr"),
            shell=False,
            bufsize=0,
        )
        child_holder["proc"] = proc
        return proc

    transfer_timeout = 2
    outer_watchdog = 12.0
    t0 = time.monotonic()
    with pytest.raises(WorkstationError) as exc:
        pull_workstation_snapshot(
            store_root=store,
            mount_root=mount,
            expected_store_id=init.store_id,
            ssh_target="nuc-alias",
            transfer_timeout_seconds=transfer_timeout,
            hooks=hooks,
            popen=_popen,
        )
    elapsed = time.monotonic() - t0
    assert exc.value.error_code == "WORKSTATION_TRANSFER_TIMEOUT"
    assert elapsed < outer_watchdog
    assert elapsed >= transfer_timeout * 0.5

    child = child_holder["proc"]
    assert child is not None
    assert child.poll() is not None
    snapshots = store / "snapshots"
    finals = [
        path
        for path in snapshots.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    ]
    assert finals == []
    stages = [path for path in snapshots.iterdir() if path.name.startswith(".framenest-pull-stage-")]
    assert stages == []


def test_exporter_stalled_stdout_releases_operation_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A3-F01: non-draining stdout must fail export and release the shared lock."""
    import os
    import threading
    import time

    from kronika.infrastructure.persistence import catalog_backup_transfer as transfer_mod
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        export_latest_scheduled_recovery_point,
        operation_lock,
        run_scheduled_catalog_backup,
    )
    from kronika.infrastructure.persistence.catalog_backup_transfer import TransferError

    monkeypatch.setattr(transfer_mod, "EXPORT_STDOUT_NO_PROGRESS_SECONDS", 2)

    config = _ops_config(tmp_path)
    run_scheduled_catalog_backup(config, now=datetime(2026, 8, 8, 3, 17, 0, tzinfo=UTC))

    r_fd, w_fd = os.pipe()
    errors: list[BaseException] = []
    done = threading.Event()

    def _run_export() -> None:
        try:
            with os.fdopen(w_fd, "wb", buffering=0) as stdout:
                export_latest_scheduled_recovery_point(config, stdout)
        except BaseException as exc:  # noqa: BLE001 - capture for assertion
            errors.append(exc)
        finally:
            done.set()

    thread = threading.Thread(target=_run_export, name="export-stall", daemon=True)
    t0 = time.monotonic()
    thread.start()

    assert done.wait(timeout=12.0) is True
    elapsed = time.monotonic() - t0
    assert elapsed < 10.0
    assert len(errors) == 1
    assert isinstance(errors[0], TransferError)
    assert errors[0].error_code == "TRANSFER_WRITE_NO_PROGRESS"
    assert thread.is_alive() is False

    with operation_lock(config) as held:
        assert held is True

    os.close(r_fd)
    # No scheduled success mutation / deletion side effects from stalled export.
    status = (config.ops_root / "status.json").read_text(encoding="utf-8")
    assert "last_successful_scheduled_backup_and_restore" in status
    assert list(config.backup_root.iterdir())
