"""Contract tests for off-device catalog backup systemd assets and CLI surface."""

from __future__ import annotations

import configparser
import os
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
BACKUP_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-backup"
OFFDEVICE_SERVICE_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-catalog-offdevice.service"
)
OFFDEVICE_TIMER_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-catalog-offdevice.timer"
)
LOCAL_BACKUP_SERVICE_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-catalog-backup.service"
)
LOCAL_BACKUP_TIMER_PATH = (
    REPOSITORY_ROOT / "deploy" / "systemd" / "framenest-catalog-backup.timer"
)
ENV_TEMPLATE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "framenest.env.example"
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


def _read_unit(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(interpolation=None, strict=False)
    parser.optionxform = str
    assert parser.read(path, encoding="utf-8") == [str(path)]
    return parser


def test_exactly_one_offdevice_timer_pair_exists() -> None:
    services = sorted(SYSTEMD_DIR.glob("framenest-catalog-offdevice*.service"))
    timers = sorted(SYSTEMD_DIR.glob("framenest-catalog-offdevice*.timer"))
    assert services == [OFFDEVICE_SERVICE_PATH]
    assert timers == [OFFDEVICE_TIMER_PATH]


def test_offdevice_service_contract() -> None:
    parser = _read_unit(OFFDEVICE_SERVICE_PATH)
    service = parser["Service"]
    assert service["Type"] == "oneshot"
    assert service["User"] == "framenest"
    assert service["Group"] == "framenest"
    assert service["WorkingDirectory"] == "/opt/framenest/current"
    assert service["EnvironmentFile"] == "/etc/framenest/framenest.env"
    assert (
        service["ExecStart"]
        == "/opt/framenest/current/.venv/bin/framenest-backup run-offdevice"
    )
    assert service["TimeoutStartSec"] == "30min"
    assert service["ProtectSystem"] == "strict"
    assert service["StateDirectory"] == "framenest"
    assert service["StateDirectoryMode"] == "0700"
    assert service["RestrictAddressFamilies"] == "AF_UNIX"
    assert service["IPAddressDeny"] == "any"
    assert service["InaccessiblePaths"] == "/srv/media"
    assert "ListenStream" not in service

    unit = parser["Unit"]
    requires_mounts = unit["RequiresMountsFor"]
    assert "/mnt/framenest-catalog-offdevice" in requires_mounts
    assert "/var/lib/framenest" in requires_mounts

    text = OFFDEVICE_SERVICE_PATH.read_text(encoding="utf-8")
    assert "ListenStream" not in text
    assert "/mnt/umbrel-data" not in text
    # Destination is the dedicated off-device mount, never original media storage.
    assert "ReadWritePaths=" in text
    assert "/mnt/framenest-catalog-offdevice/bundles" in text
    assert "InaccessiblePaths=/srv/media" in text
    assert "run-scheduled" not in service["ExecStart"]


def test_offdevice_timer_schedule_contract() -> None:
    parser = _read_unit(OFFDEVICE_TIMER_PATH)
    timer = parser["Timer"]
    assert timer["OnCalendar"] == "*-*-* 04:17:00 UTC"
    assert timer["Persistent"] == "yes"
    assert timer["RandomizedDelaySec"] == "900"
    assert timer["AccuracySec"] == "1min"
    assert timer["Unit"] == "framenest-catalog-offdevice.service"


def test_local_catalog_backup_timer_unchanged() -> None:
    """Existing local scheduled backup timer remains present at 03:17 UTC."""
    assert LOCAL_BACKUP_SERVICE_PATH.is_file()
    assert LOCAL_BACKUP_TIMER_PATH.is_file()
    parser = _read_unit(LOCAL_BACKUP_TIMER_PATH)
    timer = parser["Timer"]
    assert timer["OnCalendar"] == "*-*-* 03:17:00 UTC"
    assert timer["Persistent"] == "yes"
    assert timer["RandomizedDelaySec"] == "900"
    assert timer["AccuracySec"] == "1min"
    assert timer["Unit"] == "framenest-catalog-backup.service"

    local_service = _read_unit(LOCAL_BACKUP_SERVICE_PATH)["Service"]
    assert (
        local_service["ExecStart"]
        == "/opt/framenest/current/.venv/bin/framenest-backup run-scheduled"
    )


def test_env_template_mentions_offdevice_destination_id() -> None:
    text = ENV_TEMPLATE_PATH.read_text(encoding="utf-8")
    assert "FRAMENEST_CATALOG_OFFDEVICE_DESTINATION_ID" in text
    assert "/mnt/framenest-catalog-offdevice" in text
    # Existing local backup settings remain documented.
    assert "FRAMENEST_CATALOG_BACKUP_ROOT=/var/lib/framenest/catalog-backups" in text
    assert "FRAMENEST_CATALOG_BACKUP_KEEP_AUTO=30" in text


def test_cli_help_includes_run_offdevice(tmp_path: Path) -> None:
    help_result = _run_backup_command("--help", cwd=tmp_path)
    assert help_result.returncode == 0
    assert "run-offdevice" in help_result.stdout
    for name in ("run-scheduled", "verify-restore", "status", "list", "retain-plan", "expire"):
        assert name in help_result.stdout
