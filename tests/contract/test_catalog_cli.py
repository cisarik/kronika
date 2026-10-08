"""Contract tests for the FrameNest catalog command boundary."""

from __future__ import annotations

import importlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from tests.support.tooling import resolve_tool

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOG_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-catalog"
DB_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-db"
PYTHON_EXECUTABLE = REPOSITORY_ROOT / ".venv" / "bin" / "python"
PRIVATE_DATABASE_PATH = "/Users/agile/private/catalog.sqlite3"
CANONICAL_UUID4_TEXT = "12345678-1234-4234-9234-123456789abc"
SECOND_CANONICAL_UUID4_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"


def _require_catalog_console_script() -> Path:
    if not CATALOG_CONSOLE_SCRIPT.is_file():
        pytest.fail(f"Expected installed console script at {CATALOG_CONSOLE_SCRIPT}")
    return CATALOG_CONSOLE_SCRIPT


def _run_db_migrate(*, cwd: Path, database_path: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FRAMENEST_DATABASE_PATH"] = database_path
    env.pop("FRAMENEST_API_KEY", None)
    return subprocess.run(
        [str(DB_CONSOLE_SCRIPT), "migrate"],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )


def _run_catalog_command(
    *args: str,
    cwd: Path,
    database_path: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FRAMENEST_DATABASE_PATH"] = database_path
    env.pop("FRAMENEST_API_KEY", None)
    return subprocess.run(
        [str(_require_catalog_console_script()), *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )


def _parse_single_json_line(output: str) -> dict[str, Any]:
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return payload


def _upgrade_database_to_revision(database_path: Path, revision: str) -> None:
    from alembic import command

    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    settings = KronikaSettings(database_path=database_path, _env_file=None)
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def test_catalog_console_script_is_installed() -> None:
    assert CATALOG_CONSOLE_SCRIPT.is_file()


def test_importing_catalog_module_has_no_execution_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [
            str(PYTHON_EXECUTABLE),
            "-c",
            "import kronika.adapters.cli.catalog",
        ],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_catalog_help_succeeds(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "device" in result.stdout


def test_catalog_help_lists_the_identity_labels_resource(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "identity-labels" in result.stdout


def test_invalid_usage_returns_exit_2_with_sanitized_json(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    rejected_id = "NOT-A-VALID-ID"

    result = _run_catalog_command(
        "device",
        "get",
        "--id",
        rejected_id,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    assert result.stdout == ""
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "device.get",
        "state": "error",
        "error_code": "FRAMENEST_CATALOG_INVALID_INPUT",
        "message": "Invalid catalog command.",
    }
    combined = result.stdout + result.stderr
    assert rejected_id not in combined
    assert "argument" not in combined.lower()


def test_nonexistent_database_returns_exit_4_without_creating_file(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"

    result = _run_catalog_command(
        "device",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 4
    assert result.stdout == ""
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "device.list",
        "state": "error",
        "error_code": "FRAMENEST_CATALOG_NOT_READY",
        "message": "Catalog database is not ready. Run framenest-db migrate.",
    }
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert str(database_path) not in result.stderr


def test_database_at_revision_0001_returns_exit_4(tmp_path: Path) -> None:
    database_path = tmp_path / "behind.sqlite3"
    _upgrade_database_to_revision(database_path, "0001")

    result = _run_catalog_command(
        "device",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 4
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_NOT_READY"
    assert "0001" not in result.stderr
    assert "0002" not in result.stderr


def test_catalog_cli_does_not_execute_migrations(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.adapters.cli import catalog

    database_path = tmp_path / "no-migrate.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    def fail_upgrade(*args: object, **kwargs: object) -> object:
        raise AssertionError("catalog CLI must not execute migrations")

    monkeypatch.setattr(
        "kronika.infrastructure.persistence.migrations.upgrade_database_to_head",
        fail_upgrade,
    )

    assert catalog.main(["device", "list"]) == 4
    assert not database_path.exists()


@pytest.mark.parametrize(
    ("device_id",),
    [
        ("12345678-1234-4234-9234-123456789ABC",),
        (" 12345678-1234-4234-9234-123456789abc",),
        ("{12345678-1234-4234-9234-123456789abc}",),
        ("urn:uuid:12345678-1234-4234-9234-123456789abc",),
        ("12345678123442349234123456789abc",),
        ("a8098c1a-f86e-11da-bd1a-00112444be1e",),
    ],
)
def test_get_rejects_malformed_device_ids(tmp_path: Path, device_id: str) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "device",
        "get",
        "--id",
        device_id,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_INVALID_INPUT"
    assert device_id not in result.stdout + result.stderr


def test_register_valid_unicode_display_name(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    display_name = "Café 🎬"

    result = _run_catalog_command(
        "device",
        "register",
        "--display-name",
        display_name,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = _parse_single_json_line(result.stdout)
    assert payload["operation"] == "device.register"
    assert payload["state"] == "ok"
    device = payload["device"]
    assert device["display_name"] == display_name
    assert device["id"] == device["id"].lower()
    assert len(device["id"]) == 36


def test_register_invalid_display_name_returns_exit_2(tmp_path: Path) -> None:
    from kronika.adapters.cli import catalog

    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    exit_code = catalog.main(["device", "register", "--display-name", " leading"])
    assert exit_code == 2


def test_register_invalid_display_name_via_subprocess_returns_exit_2(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "device",
        "register",
        "--display-name",
        "",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_INVALID_INPUT"
    assert "leading" not in result.stdout + result.stderr


def test_register_persists_and_get_returns_device(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    register = _run_catalog_command(
        "device",
        "register",
        "--display-name",
        "Studio Mac",
        cwd=tmp_path,
        database_path=str(database_path),
    )
    device_id = _parse_single_json_line(register.stdout)["device"]["id"]

    get_result = _run_catalog_command(
        "device",
        "get",
        "--id",
        device_id,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert get_result.returncode == 0
    payload = _parse_single_json_line(get_result.stdout)
    assert payload == {
        "operation": "device.get",
        "state": "ok",
        "device": {"id": device_id, "display_name": "Studio Mac"},
    }


def test_get_missing_device_returns_exit_3(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "device",
        "get",
        "--id",
        CANONICAL_UUID4_TEXT,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 3
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "device.get",
        "state": "error",
        "error_code": "FRAMENEST_DEVICE_NOT_FOUND",
        "message": "Device not found.",
    }
    assert CANONICAL_UUID4_TEXT not in result.stderr


def test_list_empty_registry_returns_empty_array(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "device",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    payload = _parse_single_json_line(result.stdout)
    assert payload == {
        "operation": "device.list",
        "state": "ok",
        "devices": [],
    }


def test_list_preserves_repository_ordering(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    from kronika.application.ports.device_repository import DeviceRepository
    from kronika.domain import Device, DeviceId
    from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine

    engine = create_sqlite_engine(database_path)
    try:
        repository = SqliteDeviceRepository(engine)
        repository.add(Device(id=DeviceId.from_string(CANONICAL_UUID4_TEXT), display_name="alpha"))
        repository.add(
            Device(id=DeviceId.from_string(SECOND_CANONICAL_UUID4_TEXT), display_name="Beta")
        )
        repository.add(Device(id=DeviceId.new(), display_name="zebra"))
        expected = [
            {"id": device.id.to_string(), "display_name": device.display_name}
            for device in repository.list_all()
        ]
    finally:
        engine.dispose()

    result = _run_catalog_command(
        "device",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    payload = _parse_single_json_line(result.stdout)
    assert payload["devices"] == expected


def test_direct_process_emits_single_json_line_without_traceback(tmp_path: Path) -> None:
    database_path = tmp_path / "process.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "device",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert len([line for line in result.stdout.splitlines() if line.strip()]) == 1
    assert "Traceback" not in result.stdout + result.stderr
    assert PRIVATE_DATABASE_PATH not in result.stdout + result.stderr
    assert "INSERT INTO" not in result.stdout + result.stderr
    assert "sqlite" not in (result.stdout + result.stderr).lower()


def test_direct_process_starts_no_listener(tmp_path: Path) -> None:
    database_path = tmp_path / "no-listener.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    with patch("socket.socket.bind", side_effect=AssertionError("socket bind must not run")):
        result = _run_catalog_command(
            "device",
            "list",
            cwd=tmp_path,
            database_path=str(database_path),
        )

    assert result.returncode == 0


def test_repository_failure_returns_exit_1(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.adapters.cli import catalog
    from kronika.application.ports.device_repository import FrameNestDeviceRepositoryError

    database_path = tmp_path / "failure.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    def fail_add(self: object, device: object) -> None:
        raise FrameNestDeviceRepositoryError("Device registry operation failed.")

    monkeypatch.setattr(
        "kronika.infrastructure.persistence.device_repository.SqliteDeviceRepository.add",
        fail_add,
    )

    exit_code = catalog.main(["device", "register", "--display-name", "Studio Mac"])
    assert exit_code == 1


def test_catalog_works_from_working_directory_outside_repository(tmp_path: Path) -> None:
    database_path = tmp_path / "outside" / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    outside_cwd = tmp_path / "outside-cwd"
    outside_cwd.mkdir()

    result = _run_catalog_command(
        "device",
        "register",
        "--display-name",
        "Remote CWD",
        cwd=outside_cwd,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert _parse_single_json_line(result.stdout)["device"]["display_name"] == "Remote CWD"


def _register_device_for_library_tests(
    *,
    cwd: Path,
    database_path: str,
    display_name: str = "Studio Mac",
) -> str:
    result = _run_catalog_command(
        "device",
        "register",
        "--display-name",
        display_name,
        cwd=cwd,
        database_path=database_path,
    )
    assert result.returncode == 0
    return _parse_single_json_line(result.stdout)["device"]["id"]


def _native_root_flavor() -> str:
    import os

    return "windows" if os.name == "nt" else "posix"


def test_library_help_lists_library_commands(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "library", "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "register" in result.stdout
    assert "list" in result.stdout


def test_library_list_on_empty_registry(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert _parse_single_json_line(result.stdout) == {
        "operation": "library.list",
        "state": "ok",
        "libraries": [],
    }


def test_library_register_and_get_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    device_id = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
    )

    register = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        device_id,
        "--display-name",
        "Videos",
        "--root",
        str(library_dir),
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert register.returncode == 0
    payload = _parse_single_json_line(register.stdout)
    assert payload["operation"] == "library.register"
    library = payload["library"]
    assert library["device_id"] == device_id
    assert library["display_name"] == "Videos"
    assert library["root"]["flavor"] == _native_root_flavor()
    assert library["root"]["path"] == os.path.normpath(str(library_dir))

    get_result = _run_catalog_command(
        "library",
        "get",
        "--id",
        library["id"],
        cwd=tmp_path,
        database_path=str(database_path),
    )
    assert get_result.returncode == 0
    assert _parse_single_json_line(get_result.stdout)["library"] == library


def test_library_register_relative_root_from_cwd(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    workdir = tmp_path / "work"
    library_dir = workdir / "nested" / "library"
    library_dir.mkdir(parents=True)
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    device_id = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
    )

    result = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        device_id,
        "--display-name",
        "Nested",
        "--root",
        "nested/library",
        cwd=workdir,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    library = _parse_single_json_line(result.stdout)["library"]
    assert library["root"]["path"] == os.path.normpath(str(library_dir))


def test_library_register_missing_device_returns_exit_3(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        CANONICAL_UUID4_TEXT,
        "--display-name",
        "Videos",
        "--root",
        str(library_dir),
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 3
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "library.register",
        "state": "error",
        "error_code": "FRAMENEST_LIBRARY_DEVICE_NOT_FOUND",
        "message": "Owning device not found.",
    }
    assert CANONICAL_UUID4_TEXT not in result.stderr


def test_library_register_duplicate_root_on_same_device_returns_exit_5(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "shared-root"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    device_id = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
    )
    common_args = (
        "library",
        "register",
        "--device-id",
        device_id,
        "--root",
        str(library_dir),
    )

    first = _run_catalog_command(
        *common_args,
        "--display-name",
        "First",
        cwd=tmp_path,
        database_path=str(database_path),
    )
    assert first.returncode == 0

    second = _run_catalog_command(
        *common_args,
        "--display-name",
        "Second",
        cwd=tmp_path,
        database_path=str(database_path),
    )
    assert second.returncode == 5
    payload = _parse_single_json_line(second.stderr)
    assert payload["error_code"] == "FRAMENEST_LIBRARY_ROOT_ALREADY_REGISTERED"
    assert str(library_dir) not in second.stderr


def test_library_register_same_root_on_different_devices_succeeds(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "shared-root"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    first_device = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        display_name="First Device",
    )
    second_device = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        display_name="Second Device",
    )

    first = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        first_device,
        "--display-name",
        "First",
        "--root",
        str(library_dir),
        cwd=tmp_path,
        database_path=str(database_path),
    )
    second = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        second_device,
        "--display-name",
        "Second",
        "--root",
        str(library_dir),
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert first.returncode == 0
    assert second.returncode == 0


def test_library_register_missing_root_returns_exit_2_without_path_leak(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    missing_root = tmp_path / "missing-library-root"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    device_id = _register_device_for_library_tests(
        cwd=tmp_path,
        database_path=str(database_path),
    )

    result = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        device_id,
        "--display-name",
        "Videos",
        "--root",
        str(missing_root),
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_LIBRARY_ROOT_NOT_USABLE"
    assert str(missing_root) not in result.stderr


def test_library_get_missing_returns_exit_3(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "get",
        "--id",
        CANONICAL_UUID4_TEXT,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 3
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_LIBRARY_NOT_FOUND"
    assert CANONICAL_UUID4_TEXT not in result.stderr


def test_library_get_rejects_malformed_ids(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    rejected = "12345678-1234-4234-9234-123456789ABC"

    result = _run_catalog_command(
        "library",
        "get",
        "--id",
        rejected,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    assert rejected not in result.stdout + result.stderr


def test_library_list_at_database_revision_0002_returns_not_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "behind-0002.sqlite3"
    _upgrade_database_to_revision(database_path, "0002")

    result = _run_catalog_command(
        "library",
        "list",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 4
    assert _parse_single_json_line(result.stderr)["error_code"] == "FRAMENEST_CATALOG_NOT_READY"


def _register_library_for_scan_tests(
    *,
    cwd: Path,
    database_path: str,
    library_dir: Path,
    display_name: str = "Videos",
) -> str:
    device_id = _register_device_for_library_tests(cwd=cwd, database_path=database_path)
    register = _run_catalog_command(
        "library",
        "register",
        "--device-id",
        device_id,
        "--display-name",
        display_name,
        "--root",
        str(library_dir),
        cwd=cwd,
        database_path=database_path,
    )
    assert register.returncode == 0
    return _parse_single_json_line(register.stdout)["library"]["id"]


def test_library_scan_preview_help_lists_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "library", "scan-preview", "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "--id" in result.stdout
    assert "--max-entries" in result.stdout


def test_library_scan_preview_returns_one_json_object(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    (library_dir / "clip.mkv").write_bytes(b"x" * 3)
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        library_id,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = _parse_single_json_line(result.stdout)
    assert payload["operation"] == "library.scan_preview"
    assert payload["state"] == "ok"
    assert payload["library_id"] == library_id
    assert payload["limits"] == {"max_entries": 100000, "max_candidates": 1000}
    assert payload["candidates"] == [
        {
            "relative_path": "clip.mkv",
            "kind": "video",
            "extension": ".mkv",
            "size_bytes": 3,
        }
    ]
    combined = result.stdout + result.stderr
    assert str(library_dir) not in combined
    assert str(database_path) not in combined


def test_library_scan_preview_custom_limits(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        library_id,
        "--max-entries",
        "10",
        "--max-candidates",
        "5",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    payload = _parse_single_json_line(result.stdout)
    assert payload["limits"] == {"max_entries": 10, "max_candidates": 5}


def test_library_scan_preview_invalid_limits_return_exit_2(tmp_path: Path) -> None:
    from kronika.adapters.cli import catalog

    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=tmp_path / "Videos",
    )

    exit_code = catalog.main(
        [
            "library",
            "scan-preview",
            "--id",
            library_id,
            "--max-entries",
            "0",
        ]
    )
    assert exit_code == 2


def test_library_scan_preview_missing_library_returns_exit_3(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 3
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "library.scan_preview",
        "state": "error",
        "error_code": "FRAMENEST_LIBRARY_NOT_FOUND",
        "message": "Library not found.",
    }


def test_library_scan_preview_unavailable_root_returns_exit_6(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )
    library_dir.rmdir()

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        library_id,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 6
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "library.scan_preview",
        "state": "error",
        "error_code": "FRAMENEST_LIBRARY_SCAN_UNAVAILABLE",
        "message": "Library scan preview is not available.",
    }
    assert str(library_dir) not in result.stderr


def test_library_scan_preview_at_database_revision_0002_returns_not_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "behind-0002.sqlite3"
    _upgrade_database_to_revision(database_path, "0002")

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 4
    assert _parse_single_json_line(result.stderr)["error_code"] == "FRAMENEST_CATALOG_NOT_READY"


def test_library_scan_preview_does_not_execute_migrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.adapters.cli import catalog

    database_path = tmp_path / "no-migrate.sqlite3"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    def fail_upgrade(*args: object, **kwargs: object) -> object:
        raise AssertionError("catalog CLI must not execute migrations")

    monkeypatch.setattr(
        "kronika.infrastructure.persistence.migrations.upgrade_database_to_head",
        fail_upgrade,
    )

    assert catalog.main(["library", "scan-preview", "--id", CANONICAL_UUID4_TEXT]) == 4
    assert not database_path.exists()


def test_library_scan_preview_direct_process_has_no_traceback_or_leaks(tmp_path: Path) -> None:
    database_path = tmp_path / "process.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )
    outside_cwd = tmp_path / "outside"
    outside_cwd.mkdir()

    result = _run_catalog_command(
        "library",
        "scan-preview",
        "--id",
        library_id,
        cwd=outside_cwd,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert "Traceback" not in result.stdout + result.stderr
    assert PRIVATE_DATABASE_PATH not in result.stdout + result.stderr
    assert "INSERT INTO" not in result.stdout + result.stderr
    assert str(library_dir) not in result.stdout + result.stderr


def _generate_tiny_mp4(path: Path) -> None:
    try:
        ffmpeg = resolve_tool("ffmpeg")
    except FileNotFoundError:
        pytest.fail("ffmpeg is required for analyze-preview contract tests")
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:d=1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )


def test_library_analyze_preview_help_lists_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "library", "analyze-preview", "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "--id" in result.stdout
    assert "--path" in result.stdout


def test_library_analyze_preview_returns_one_json_object(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    _generate_tiny_mp4(library_dir / "clip.mp4")
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )

    result = _run_catalog_command(
        "library",
        "analyze-preview",
        "--id",
        library_id,
        "--path",
        "clip.mp4",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 0
    assert result.stderr == ""
    payload = _parse_single_json_line(result.stdout)
    assert payload["operation"] == "library.analyze_preview"
    assert payload["state"] == "ok"
    assert payload["library_id"] == library_id
    assert payload["relative_path"] == "clip.mp4"
    assert payload["candidate_kind"] == "video"
    assert "technical_metadata" in payload
    assert payload["requested_frame_count"] == 3
    assert 1 <= payload["produced_frame_count"] <= 3
    assert payload["representative_frames"]
    frame = payload["representative_frames"][0]
    assert set(frame) == {"ordinal", "timestamp_ms", "mime_type", "byte_size", "sha256"}
    assert "tools" in payload
    combined = result.stdout + result.stderr
    assert str(library_dir) not in combined
    assert str(database_path) not in combined
    assert "base64" not in combined.lower()
    dumped = json.dumps(payload)
    assert "iVBOR" not in dumped


def test_library_analyze_preview_invalid_relative_path_returns_exit_2(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "analyze-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "/abs/clip.mp4",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_INVALID_INPUT"


def test_library_analyze_preview_missing_library_returns_exit_3(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "analyze-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "clip.mp4",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 3
    payload = _parse_single_json_line(result.stderr)
    assert payload == {
        "operation": "library.analyze_preview",
        "state": "error",
        "error_code": "FRAMENEST_LIBRARY_NOT_FOUND",
        "message": "Library not found.",
    }


def test_library_analyze_preview_unsupported_extension_returns_exit_6(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_dir = tmp_path / "Videos"
    library_dir.mkdir()
    (library_dir / "readme.txt").write_bytes(b"x")
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    library_id = _register_library_for_scan_tests(
        cwd=tmp_path,
        database_path=str(database_path),
        library_dir=library_dir,
    )

    result = _run_catalog_command(
        "library",
        "analyze-preview",
        "--id",
        library_id,
        "--path",
        "readme.txt",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 6
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_LIBRARY_ANALYSIS_UNAVAILABLE"


def test_library_analyze_preview_at_database_revision_0002_returns_not_ready(tmp_path: Path) -> None:
    database_path = tmp_path / "behind-0002.sqlite3"
    _upgrade_database_to_revision(database_path, "0002")

    result = _run_catalog_command(
        "library",
        "analyze-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "clip.mp4",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 4
    assert _parse_single_json_line(result.stderr)["error_code"] == "FRAMENEST_CATALOG_NOT_READY"


def test_library_analyze_preview_does_not_execute_migrations(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.adapters.cli import catalog

    database_path = tmp_path / "missing.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert catalog.main(
        [
            "library",
            "analyze-preview",
            "--id",
            CANONICAL_UUID4_TEXT,
            "--path",
            "clip.mp4",
        ]
    ) == 4
    assert not database_path.exists()


def test_library_suggest_preview_help_lists_command(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(_require_catalog_console_script()), "library", "suggest-preview", "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )
    assert result.returncode == 0
    assert "--confirm-cloud-upload" in result.stdout
    assert "--provider" in result.stdout
    assert "--model" in result.stdout


def test_library_suggest_preview_missing_confirmation_returns_exit_2(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))

    result = _run_catalog_command(
        "library",
        "suggest-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "clip.mp4",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_CLOUD_CONFIRMATION_REQUIRED"


def test_library_suggest_preview_missing_confirmation_skips_local_preparation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.adapters.cli import catalog

    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    calls: list[bool] = []

    class _SpyAdapter:
        def prepare(self, *args: object, **kwargs: object) -> object:
            calls.append(True)
            raise AssertionError("local preparation should not run")

    monkeypatch.setattr(catalog, "LocalMediaAnalysisAdapter", _SpyAdapter)

    assert catalog.main(
        [
            "library",
            "suggest-preview",
            "--id",
            CANONICAL_UUID4_TEXT,
            "--path",
            "clip.mp4",
        ]
    ) == 2
    assert calls == []


def test_library_suggest_preview_missing_credential_returns_exit_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)

    result = _run_catalog_command(
        "library",
        "suggest-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "clip.mp4",
        "--confirm-cloud-upload",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_NVIDIA_CREDENTIAL_MISSING"


def test_library_suggest_preview_unsupported_provider_returns_exit_2(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    monkeypatch.setenv("NVIDIA_API_KEY", "test-secret-not-real")

    result = _run_catalog_command(
        "library",
        "suggest-preview",
        "--id",
        CANONICAL_UUID4_TEXT,
        "--path",
        "clip.mp4",
        "--provider",
        "other-provider",
        "--confirm-cloud-upload",
        cwd=tmp_path,
        database_path=str(database_path),
    )

    assert result.returncode == 2
    payload = _parse_single_json_line(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_UNSUPPORTED_PROVIDER"


def test_library_suggest_preview_success_returns_deterministic_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kronika.adapters.cli import catalog

    monkeypatch.setenv("NVIDIA_API_KEY", "test-secret-not-real")

    def _fake_with_suggest_preview(
        settings: object,
        library_id: object,
        relative_path: object,
        *,
        provider_id: str,
        model_id: str,
    ) -> dict[str, object]:
        return {
            "library_id": str(library_id),
            "relative_path": "clip.mp4",
            "provider_id": provider_id,
            "model_id": model_id,
            "prompt_version": "framenest-media-suggestion-v1",
            "sent_frame_count": 1,
            "technical_metadata": {
                "duration_ms": 1000,
                "width": 64,
                "height": 48,
                "video_codec": "h264",
                "container_formats": ["mp4"],
                "has_audio": False,
            },
            "suggestion": {
                "title": "Evening clip",
                "description": "A short evening scene with warm light.",
                "collection": "Home",
                "tags": ["evening", "clip"],
                "suggested_filename": "evening-clip.mp4",
                "confidence": 0.72,
                "evidence": ["Warm light is visible in the frame."],
                "uncertainties": ["Exact location is unknown."],
            },
        }

    monkeypatch.setattr(catalog, "_with_suggest_preview", _fake_with_suggest_preview)

    exit_code = catalog.main(
        [
            "library",
            "suggest-preview",
            "--id",
            CANONICAL_UUID4_TEXT,
            "--path",
            "clip.mp4",
            "--confirm-cloud-upload",
        ]
    )
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = _parse_single_json_line(captured.out)
    assert payload["operation"] == "library.suggest_preview"
    assert payload["state"] == "ok"
    assert payload["prompt_version"] == "framenest-media-suggestion-v1"
    assert "test-secret-not-real" not in captured.out
    assert "base64" not in json.dumps(payload)
