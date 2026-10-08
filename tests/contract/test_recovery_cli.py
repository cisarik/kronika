"""Contract tests for recovery CLI, export-latest, and export launcher source."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import stat

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-backup"
RECOVERY_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-recovery"
PYTHON_EXECUTABLE = REPOSITORY_ROOT / ".venv" / "bin" / "python"
LAUNCHER_SOURCE = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest-catalog-export-v1"
BACKUP_DOC = REPOSITORY_ROOT / "docs" / "BACKUP_AND_RECOVERY.md"


def _ops_env(tmp_path: Path) -> dict[str, str]:
    db = tmp_path / "catalog.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=db, _env_file=None))
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "PYTHONNOUSERSITE": "1",
        "FRAMENEST_DATABASE_PATH": str(db),
        "FRAMENEST_CATALOG_BACKUP_ROOT": str(tmp_path / "catalog-backups"),
        "FRAMENEST_CATALOG_RESTORE_VERIFY_ROOT": str(tmp_path / "catalog-restore-verify"),
        "FRAMENEST_CATALOG_BACKUP_OPS_ROOT": str(tmp_path / "catalog-backup-ops"),
        "FRAMENEST_CATALOG_BACKUP_KEEP_AUTO": "30",
    }


def _run(script: Path, *args: str, cwd: Path, env: dict[str, str], timeout: float = 60.0):
    merged = os.environ.copy()
    merged.pop("FRAMENEST_API_KEY", None)
    merged.update(env)
    return subprocess.run(
        [str(script), *args],
        cwd=cwd,
        env=merged,
        check=False,
        capture_output=True,
        timeout=timeout,
    )


def test_recovery_and_backup_console_scripts_installed() -> None:
    assert BACKUP_CONSOLE_SCRIPT.is_file()
    assert RECOVERY_CONSOLE_SCRIPT.is_file()
    assert "framenest-recovery" in (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")


def test_export_latest_rejects_arguments(tmp_path: Path) -> None:
    env = _ops_env(tmp_path)
    result = _run(
        BACKUP_CONSOLE_SCRIPT,
        "export-latest",
        "--bundle",
        "x",
        cwd=tmp_path,
        env=env,
    )
    assert result.returncode == 2
    payload = json.loads(result.stderr.decode("utf-8"))
    assert payload["error_code"] == "FRAMENEST_BACKUP_INVALID_INPUT"


def test_export_latest_streams_protocol_after_scheduled(tmp_path: Path) -> None:
    env = _ops_env(tmp_path)
    scheduled = _run(BACKUP_CONSOLE_SCRIPT, "run-scheduled", cwd=tmp_path, env=env)
    assert scheduled.returncode == 0, scheduled.stderr
    exported = _run(BACKUP_CONSOLE_SCRIPT, "export-latest", cwd=tmp_path, env=env)
    assert exported.returncode == 0, exported.stderr
    assert exported.stdout.startswith(b"FNCBE01\0")
    # stdout must not be JSON success
    assert not exported.stdout.lstrip().startswith(b"{")


def test_launcher_source_contract() -> None:
    text = LAUNCHER_SOURCE.read_text(encoding="utf-8")
    mode = LAUNCHER_SOURCE.stat().st_mode
    assert mode & stat.S_IXUSR
    assert "export-latest" in text
    assert "/opt/framenest/current/.venv/bin/framenest-backup" in text
    assert 'if [ "$#" -ne 0 ]' in text
    assert "exec </dev/null" in text
    assert "umask 077" in text
    assert "FRAMENEST_CATALOG_BACKUP_ROOT" in text
    assert "ProxyCommand" not in text
    assert "ssh" not in text.lower() or "OpenSSH" in text  # comments only ok
    assert "/mnt/umbrel-data" not in text
    assert "password" not in text.lower()
    assert "BEGIN OPENSSH" not in text
    # no caller command interpolation
    assert "$@" not in text
    assert "$*" not in text
    result = subprocess.run(
        ["bash", str(LAUNCHER_SOURCE), "extra"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "rejects all arguments" in result.stderr


def test_sudoers_no_argument_matcher_documented() -> None:
    text = BACKUP_DOC.read_text(encoding="utf-8")
    assert (
        '<FRAME_NEST_OPERATOR> ALL=(framenest) NOPASSWD:NOSETENV: '
        '/usr/local/libexec/framenest-catalog-export-v1 ""'
    ) in text
    assert "No wildcard `framenest-backup *` rule." in text
    assert 'explicit final `""`' in text or "explicit final `\"\"`" in text


def test_recovery_cli_surface_limited(tmp_path: Path) -> None:
    help_result = _run(RECOVERY_CONSOLE_SCRIPT, "--help", cwd=tmp_path, env=_ops_env(tmp_path))
    assert help_result.returncode == 0
    out = help_result.stdout.decode("utf-8")
    assert "init-store" in out
    assert "pull" in out
    assert "list" in out
    assert "verify" in out
    assert "prune" not in out
    assert "restore-production" not in out
    assert "apply" not in out


def test_root_launcher_exposes_recovery_route() -> None:
    launcher = (REPOSITORY_ROOT / "kronika").read_text(encoding="utf-8")
    assert "recovery_controller" in launcher
    assert "case recovery" in launcher
    assert "export-latest" in launcher


def test_pull_remote_layout_selection_is_fixed_and_explicit() -> None:
    """The temporary selection chooses one fixed tuple and nothing else."""
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        FIXED_REMOTE_EXPORT_COMMAND,
        REMOTE_LAYOUT_COMMANDS,
        WorkstationError,
        build_ssh_argv,
        resolve_remote_layout_command,
    )

    old_argv = build_ssh_argv(target="nuc-alias")
    assert old_argv[-len(FIXED_REMOTE_EXPORT_COMMAND):] == list(
        FIXED_REMOTE_EXPORT_COMMAND
    )
    new_command = REMOTE_LAYOUT_COMMANDS["new"]
    new_argv = build_ssh_argv(target="nuc-alias", remote_layout="new")
    assert new_argv[-len(new_command):] == list(new_command)
    assert new_argv[-1] == "/usr/local/libexec/kronika-catalog-export-v1"
    assert "kronika" in new_command
    assert "framenest" not in " ".join(new_command)

    assert resolve_remote_layout_command("old") == FIXED_REMOTE_EXPORT_COMMAND
    for rejected in ("anything", "", "; rm -rf /", "old new", "OLD"):
        with pytest.raises(WorkstationError) as exc:
            resolve_remote_layout_command(rejected)
        assert exc.value.error_code == "WORKSTATION_REMOTE_LAYOUT_INVALID"


def test_pull_parser_accepts_only_the_two_fixed_layout_choices() -> None:
    from kronika.adapters.cli import recovery

    parser = recovery._build_parser()
    base = [
        "pull",
        "--store-root",
        "/store",
        "--mount-root",
        "/mnt",
        "--expected-store-id",
        "0" * 32,
        "--ssh-target",
        "nuc",
    ]
    assert parser.parse_args(base).remote_layout == "old"
    assert parser.parse_args([*base, "--remote-layout", "new"]).remote_layout == "new"
    with pytest.raises(recovery._UsageError):
        parser.parse_args([*base, "--remote-layout", "anything"])


def test_recovery_pull_passes_the_selected_fixed_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from kronika.adapters.cli import recovery

    captured: dict[str, object] = {}

    def fake_pull(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            bundle_id="bundle",
            catalog_size_bytes=1,
            catalog_sha256="a" * 64,
            alembic_revision="0035",
            reused_existing=False,
            semantic={},
        )

    monkeypatch.setattr(recovery, "pull_workstation_snapshot", fake_pull)
    result = recovery.main(
        [
            "pull",
            "--store-root",
            "/store",
            "--mount-root",
            "/mnt",
            "--expected-store-id",
            "0" * 32,
            "--ssh-target",
            "nuc",
            "--remote-layout",
            "new",
        ]
    )
    assert result == 0
    assert captured["remote_layout"] == "new"
