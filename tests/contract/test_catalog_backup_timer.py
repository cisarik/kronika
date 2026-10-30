"""Contract tests for automated catalog backup systemd assets and CLI extensions."""

from __future__ import annotations

import configparser
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "kronika-backup"
SERVICE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "kronika-catalog-backup.service"
TIMER_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "kronika-catalog-backup.timer"
ENV_TEMPLATE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "kronika.env.example"
SYSTEMD_DIR = REPOSITORY_ROOT / "deploy" / "systemd"


def _require_backup_console_script() -> Path:
    if not BACKUP_CONSOLE_SCRIPT.is_file():
        pytest.fail(f"Expected installed console script at {BACKUP_CONSOLE_SCRIPT}")
    return BACKUP_CONSOLE_SCRIPT


def _run_backup_command(*args: str, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    merged.pop("KRONIKA_API_KEY", None)
    if env:
        merged.update(env)
    return subprocess.run(
        [str(_require_backup_console_script()), *args],
        cwd=cwd,
        env=merged,
        check=False,
        capture_output=True,
        text=True,
        timeout=60.0,
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


def _ops_env(tmp_path: Path, database: Path) -> dict[str, str]:
    return {
        "KRONIKA_DATABASE_PATH": str(database),
        "KRONIKA_CATALOG_BACKUP_ROOT": str(tmp_path / "catalog-backups"),
        "KRONIKA_CATALOG_RESTORE_VERIFY_ROOT": str(tmp_path / "catalog-restore-verify"),
        "KRONIKA_CATALOG_BACKUP_OPS_ROOT": str(tmp_path / "catalog-backup-ops"),
        "KRONIKA_CATALOG_BACKUP_KEEP_AUTO": "30",
    }


def test_exactly_one_catalog_backup_timer_pair_exists() -> None:
    services = sorted(SYSTEMD_DIR.glob("kronika-catalog-backup*.service"))
    timers = sorted(SYSTEMD_DIR.glob("kronika-catalog-backup*.timer"))
    assert services == [SERVICE_PATH]
    assert timers == [TIMER_PATH]


def test_catalog_backup_service_contract() -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    assert parser.read(SERVICE_PATH, encoding="utf-8") == [str(SERVICE_PATH)]
    service = parser["Service"]
    assert service["Type"] == "oneshot"
    assert service["User"] == "kronika"
    assert service["Group"] == "kronika"
    assert service["WorkingDirectory"] == "/opt/kronika/current"
    assert service["EnvironmentFile"] == "/etc/kronika/kronika.env"
    assert (
        service["ExecStart"]
        == "/opt/kronika/current/.venv/bin/kronika-backup run-scheduled"
    )
    assert service["TimeoutStartSec"] == "30min"
    assert service["ProtectSystem"] == "strict"
    assert service["StateDirectory"] == "kronika"
    assert service["StateDirectoryMode"] == "0700"
    assert "ListenStream" not in service
    text = SERVICE_PATH.read_text(encoding="utf-8")
    assert "ListenStream" not in text
    assert "/mnt/umbrel-data" not in text
    assert service["InaccessiblePaths"] == "/srv/media"
    assert service["RestrictAddressFamilies"] == "AF_UNIX"
    assert service["IPAddressDeny"] == "any"
    for name in ("PrivateDevices", "ProtectHostname", "ProtectClock",
                 "ProtectKernelLogs", "RestrictNamespaces", "RestrictRealtime",
                 "NoNewPrivileges", "PrivateTmp", "ProtectControlGroups",
                 "ProtectKernelModules", "ProtectKernelTunables", "RestrictSUIDSGID",
                 "LockPersonality"):
        assert service[name] == "true"
    assert service["SystemCallArchitectures"] == "native"
    assert service["SystemCallFilter"] == "~@clock @module @mount @raw-io @reboot @swap"
    assert service["SystemCallErrorNumber"] == "EPERM"
    assert service["ProtectHome"] == "read-only"
    assert service["UMask"] == "0077"
    assert service["CapabilityBoundingSet"] == service["AmbientCapabilities"] == ""
    assert service["Nice"] == "10"
    assert service["KillMode"] == "mixed"
    assert service["KillSignal"] == "SIGTERM"
    assert service["StandardOutput"] == service["StandardError"] == "journal"
    assert "ReadWritePaths" not in service


def test_catalog_backup_timer_schedule_contract() -> None:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    assert parser.read(TIMER_PATH, encoding="utf-8") == [str(TIMER_PATH)]
    timer = parser["Timer"]
    assert timer["OnCalendar"] == "*-*-* 03:17:00 UTC"
    assert timer["Persistent"] == "yes"
    assert timer["RandomizedDelaySec"] == "900"
    assert timer["AccuracySec"] == "1min"
    assert timer["Unit"] == "kronika-catalog-backup.service"


def test_env_template_includes_catalog_backup_settings() -> None:
    from kronika.infrastructure.persistence import catalog_backup_ops as ops

    assert ops.DEFAULT_DATABASE_PATH == Path("/var/lib/kronika/catalog.sqlite3")
    assert ops.DEFAULT_BACKUP_ROOT == Path("/var/lib/kronika/catalog-backups")
    assert ops.DEFAULT_RESTORE_VERIFY_ROOT == Path("/var/lib/kronika/catalog-restore-verify")
    assert ops.DEFAULT_OPS_ROOT == Path("/var/lib/kronika/catalog-backup-ops")
    text = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "KRONIKA_CATALOG_BACKUP_ROOT=/var/lib/kronika/catalog-backups" in text
    assert "KRONIKA_CATALOG_BACKUP_KEEP_AUTO=30" in text
    assert "KRONIKA_CATALOG_RESTORE_VERIFY_ROOT=/var/lib/kronika/catalog-restore-verify" in text
    assert "KRONIKA_CATALOG_BACKUP_OPS_ROOT=/var/lib/kronika/catalog-backup-ops" in text


def test_new_cli_commands_and_scheduled_pipeline(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path / "catalog.sqlite3")
    env = _ops_env(tmp_path, database)

    help_result = _run_backup_command("--help", cwd=tmp_path, env=env)
    assert help_result.returncode == 0
    for name in ("run-scheduled", "verify-restore", "status", "list", "retain-plan", "expire"):
        assert name in help_result.stdout

    scheduled = _run_backup_command("run-scheduled", cwd=tmp_path, env=env)
    assert scheduled.returncode == 0, scheduled.stderr
    payload = _payload(scheduled.stdout)
    assert payload["state"] == "succeeded"
    assert payload["bundle_id"].startswith("auto-")
    assert str(database) not in scheduled.stdout

    status = _run_backup_command("status", cwd=tmp_path, env=env)
    assert status.returncode == 0
    status_payload = _payload(status.stdout)
    assert status_payload["restore_readiness"] in {"ready", "stale"}
    assert status_payload["last_successful_scheduled_backup"]["bundle_id"] == payload["bundle_id"]

    listed = _run_backup_command("list", cwd=tmp_path, env=env)
    assert listed.returncode == 0
    list_payload = _payload(listed.stdout)
    assert list_payload["bundles"][0]["classification"] == "automatic"

    plan = _run_backup_command("retain-plan", cwd=tmp_path, env=env)
    assert plan.returncode == 0
    assert _payload(plan.stdout)["expire"] == []

    dry = _run_backup_command("expire", cwd=tmp_path, env=env)
    assert dry.returncode == 0
    assert _payload(dry.stdout)["mode"] == "dry-run"

    verify_restore = _run_backup_command(
        "verify-restore",
        "--bundle",
        payload["bundle_id"],
        cwd=tmp_path,
        env=env,
    )
    assert verify_restore.returncode == 0, verify_restore.stderr
    assert _payload(verify_restore.stdout)["operation"] == "verify-restore"


def test_create_verify_restore_compatibility_preserved(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path / "private" / "catalog.sqlite3")
    bundle = tmp_path / "bundle"
    restored = tmp_path / "restored" / "catalog.sqlite3"
    env = _ops_env(tmp_path, database)
    create = _run_backup_command(
        "create",
        "--source",
        str(database),
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
    assert create.returncode == verify.returncode == restore.returncode == 0
    assert _payload(create.stdout)["state"] == "created"
    siblings = [
        path
        for path in restored.parent.iterdir()
        if path.name.startswith(f".{restored.name}.") and path.name.endswith(".tmp")
    ]
    assert siblings == []


def test_invalid_keep_auto_fails_closed(tmp_path: Path) -> None:
    database = _migrated_database(tmp_path / "catalog.sqlite3")
    env = _ops_env(tmp_path, database)
    env["KRONIKA_CATALOG_BACKUP_KEEP_AUTO"] = "2"
    result = _run_backup_command("status", cwd=tmp_path, env=env)
    assert result.returncode == 1
    payload = _payload(result.stderr)
    assert payload["error_code"] == "INVALID_KEEP_AUTO"
