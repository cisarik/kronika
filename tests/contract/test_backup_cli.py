"""Contract tests for the FrameNest catalog backup command boundary."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-backup"
PYTHON_EXECUTABLE = REPOSITORY_ROOT / ".venv" / "bin" / "python"


def _require_backup_console_script() -> Path:
    if not BACKUP_CONSOLE_SCRIPT.is_file():
        pytest.fail(f"Expected installed console script at {BACKUP_CONSOLE_SCRIPT}")
    return BACKUP_CONSOLE_SCRIPT


def _ops_env(tmp_path: Path, database: Path | None = None) -> dict[str, str]:
    db = database if database is not None else tmp_path / "catalog.sqlite3"
    return {
        "FRAMENEST_DATABASE_PATH": str(db),
        "FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "catalog-backups"),
        "FRAMENEST_CATALOG_RESTORE_VERIFY_ROOT": str(tmp_path / "catalog-restore-verify"),
        "FRAMENEST_CATALOG_BACKUP_OPS_ROOT": str(tmp_path / "catalog-backup-ops"),
        "FRAMENEST_CATALOG_BACKUP_KEEP_AUTO": "30",
    }


def _run_backup_command(
    *args: str,
    cwd: Path,
    env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("FRAMENEST_API_KEY", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [str(_require_backup_console_script()), *args],
        cwd=cwd,
        env=merged,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _payload(output: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


def _migrated_database(path: Path) -> Path:
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def test_backup_console_script_is_installed() -> None:
    assert BACKUP_CONSOLE_SCRIPT.is_file()


def test_importing_backup_cli_has_no_execution_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(PYTHON_EXECUTABLE), "-c", "import kronika.adapters.cli.backup"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_backup_help_succeeds(tmp_path: Path) -> None:
    result = _run_backup_command("--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "create" in result.stdout
    assert "verify" in result.stdout
    assert "restore" in result.stdout


def test_create_verify_restore_cli_round_trip_with_sanitized_output(tmp_path: Path) -> None:
    database_path = _migrated_database(tmp_path / "private" / "catalog.sqlite3")
    bundle = tmp_path / "bundle"
    restored = tmp_path / "restored" / "catalog.sqlite3"
    env = _ops_env(tmp_path, database_path)

    create = _run_backup_command(
        "create",
        "--source",
        str(database_path),
        "--output",
        str(bundle),
        cwd=tmp_path,
        env=env,
    )
    verify = _run_backup_command("verify", "--bundle", str(bundle), cwd=tmp_path, env=env)
    restore = _run_backup_command(
        "restore",
        "--bundle",
        str(bundle),
        "--destination",
        str(restored),
        cwd=tmp_path,
        env=env,
    )

    assert create.returncode == 0
    assert verify.returncode == 0
    assert restore.returncode == 0
    assert create.stderr == verify.stderr == restore.stderr == ""
    assert _payload(create.stdout)["state"] == "created"
    assert _payload(verify.stdout)["state"] == "verified"
    assert _payload(restore.stdout)["state"] == "restored"
    combined = create.stdout + verify.stdout + restore.stdout
    assert str(database_path) not in combined
    assert str(bundle) not in combined
    assert str(restored) not in combined
    assert "private" not in combined
    siblings = [
        path
        for path in restored.parent.iterdir()
        if path.name.startswith(f".{restored.name}.") and path.name.endswith(".tmp")
    ]
    assert siblings == []


def test_failure_output_is_sanitized_and_stable(tmp_path: Path) -> None:
    supplied_path = tmp_path / "private" / "missing.sqlite3"
    env = _ops_env(tmp_path)

    result = _run_backup_command(
        "create",
        "--source",
        str(supplied_path),
        "--output",
        str(tmp_path / "bundle"),
        cwd=tmp_path,
        env=env,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    payload = _payload(result.stderr)
    assert payload == {
        "operation": "create",
        "state": "error",
        "error_code": "FRAMENEST_BACKUP_COMMAND_FAILED",
        "message": "Backup command failed.",
    }
    assert str(supplied_path) not in result.stderr
    assert "private" not in result.stderr
    assert "Traceback" not in result.stderr


def test_invalid_usage_returns_exit_2_with_sanitized_json(tmp_path: Path) -> None:
    result = _run_backup_command("create", "--source", "relative.sqlite3", cwd=tmp_path)

    assert result.returncode == 2
    assert result.stdout == ""
    payload = _payload(result.stderr)
    assert payload["operation"] == "unknown"
    assert payload["error_code"] == "FRAMENEST_BACKUP_INVALID_INPUT"
    assert "relative.sqlite3" not in result.stderr


@pytest.mark.parametrize(
    ("command", "extra"),
    [
        ("create", ()),
        ("verify", ()),
        ("restore", ()),
        ("run-scheduled", ()),
        ("verify-restore", ()),
        ("expire", ("--apply",)),
    ],
)
def test_held_lock_blocks_protected_cli_commands(
    tmp_path: Path,
    command: str,
    extra: tuple[str, ...],
) -> None:
    database = _migrated_database(tmp_path / "catalog.sqlite3")
    env = _ops_env(tmp_path, database)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir(parents=True, exist_ok=True)
    bundle = backup_root / "seed-bundle"
    seed = _run_backup_command(
        "create",
        "--source",
        str(database),
        "--output",
        str(bundle),
        cwd=tmp_path,
        env=env,
    )
    assert seed.returncode == 0, seed.stderr

    lock_path = tmp_path / "catalog-backup-ops" / "catalog-backup.lock"
    assert lock_path.is_file()
    before_names = {path.name for path in backup_root.iterdir()}
    before_bundle = {path.name for path in bundle.iterdir()}
    destination = tmp_path / "blocked-restore.sqlite3"

    holder = subprocess.Popen(
        [
            str(PYTHON_EXECUTABLE),
            "-c",
            "import fcntl,sys,time; p=sys.argv[1]; f=open(p,'a+'); "
            "fcntl.flock(f.fileno(), fcntl.LOCK_EX); print('HELD', flush=True); time.sleep(20)",
            str(lock_path),
        ],
        cwd=tmp_path,
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert holder.stdout is not None
    assert "HELD" in holder.stdout.readline()

    args: list[str]
    if command == "create":
        args = ["create", "--source", str(database), "--output", str(tmp_path / "blocked-out")]
    elif command == "verify":
        args = ["verify", "--bundle", str(bundle)]
    elif command == "restore":
        args = ["restore", "--bundle", str(bundle), "--destination", str(destination)]
    elif command == "run-scheduled":
        args = ["run-scheduled"]
    elif command == "verify-restore":
        args = ["verify-restore", "--bundle", str(bundle)]
    else:
        args = ["expire", *extra]

    result = _run_backup_command(*args, cwd=tmp_path, env=env, timeout=10.0)
    holder.kill()
    holder.wait(timeout=5)

    assert result.returncode == 1
    payload = _payload(result.stderr)
    assert payload["error_code"] == "BACKUP_OPS_BUSY"
    assert payload["operation"] == command
    assert not destination.exists()
    assert not (tmp_path / "blocked-out").exists()
    after_names = {path.name for path in backup_root.iterdir()}
    assert after_names == before_names
    assert {path.name for path in bundle.iterdir()} == before_bundle


def test_lock_release_allows_later_create(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path / "catalog.sqlite3")
    env = _ops_env(tmp_path, database)
    first = _run_backup_command(
        "create",
        "--source",
        str(database),
        "--output",
        str(tmp_path / "one"),
        cwd=tmp_path,
        env=env,
    )
    second = _run_backup_command(
        "create",
        "--source",
        str(database),
        "--output",
        str(tmp_path / "two"),
        cwd=tmp_path,
        env=env,
    )
    assert first.returncode == second.returncode == 0
    assert (tmp_path / "one" / "manifest.json").is_file()
    assert (tmp_path / "two" / "manifest.json").is_file()


def test_readonly_commands_remain_available_while_lock_held(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path / "catalog.sqlite3")
    env = _ops_env(tmp_path, database)
    scheduled = _run_backup_command("run-scheduled", cwd=tmp_path, env=env)
    assert scheduled.returncode == 0, scheduled.stderr
    lock_path = tmp_path / "catalog-backup-ops" / "catalog-backup.lock"
    holder = subprocess.Popen(
        [
            str(PYTHON_EXECUTABLE),
            "-c",
            "import fcntl,sys,time; p=sys.argv[1]; f=open(p,'a+'); "
            "fcntl.flock(f.fileno(), fcntl.LOCK_EX); print('HELD', flush=True); time.sleep(20)",
            str(lock_path),
        ],
        cwd=tmp_path,
        env={**os.environ, **env},
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    assert holder.stdout is not None
    assert "HELD" in holder.stdout.readline()
    status = _run_backup_command("status", cwd=tmp_path, env=env)
    listed = _run_backup_command("list", cwd=tmp_path, env=env)
    plan = _run_backup_command("retain-plan", cwd=tmp_path, env=env)
    holder.kill()
    holder.wait(timeout=5)
    assert status.returncode == listed.returncode == plan.returncode == 0
    assert _payload(status.stdout)["restore_readiness"] in {"ready", "busy", "stale"}
    assert _payload(listed.stdout)["operation"] == "list"
    assert _payload(plan.stdout)["operation"] == "retain-plan"
