"""Contract tests for the explicit FrameNest database command boundary."""

from __future__ import annotations

import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

INTERPRETER_BIN = Path(sys.executable).parent
DB_CONSOLE_SCRIPT = INTERPRETER_BIN / "framenest-db"
SERVER_CONSOLE_SCRIPT = INTERPRETER_BIN / "framenest-server"
STARTUP_TIMEOUT_SECONDS = 8.0
SHUTDOWN_TIMEOUT_SECONDS = 8.0


def _require_db_console_script() -> Path:
    if not DB_CONSOLE_SCRIPT.is_file():
        pytest.fail(f"Expected installed console script at {DB_CONSOLE_SCRIPT}")
    return DB_CONSOLE_SCRIPT


def _require_server_console_script() -> Path:
    if not SERVER_CONSOLE_SCRIPT.is_file():
        pytest.fail(f"Expected installed console script at {SERVER_CONSOLE_SCRIPT}")
    return SERVER_CONSOLE_SCRIPT


def _run_db_command(
    subcommand: str,
    *,
    cwd: Path,
    database_path: str,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["FRAMENEST_DATABASE_PATH"] = database_path
    env.pop("FRAMENEST_API_KEY", None)
    return subprocess.run(
        [str(_require_db_console_script()), subcommand],
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


def _find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_until_port_listening(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.05)
    raise TimeoutError(f"Timed out waiting for {host}:{port}") from last_error


def _wait_until_port_closed(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                time.sleep(0.05)
        except OSError:
            return
    raise TimeoutError(f"Timed out waiting for {host}:{port} to close")


def _child_pids(parent_pid: int) -> set[int]:
    if os.name != "posix":
        return set()
    result = subprocess.run(
        ["pgrep", "-P", str(parent_pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {int(pid) for pid in result.stdout.splitlines() if pid.strip()}


def test_framenest_db_status_reports_uninitialized_without_creating_database(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "status" / "catalog.sqlite3"

    result = _run_db_command("status", cwd=tmp_path, database_path=str(database_path))

    assert result.returncode == 0
    assert result.stderr == ""
    payload = _parse_single_json_line(result.stdout)
    assert payload == {
        "operation": "status",
        "state": "uninitialized",
        "current_revision": None,
        "head_revision": "0035",
    }
    assert str(database_path) not in result.stdout
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_framenest_db_migrate_upgrades_to_head_with_deterministic_output(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "migrate" / "catalog.sqlite3"

    migrate = _run_db_command("migrate", cwd=tmp_path, database_path=str(database_path))
    status = _run_db_command("status", cwd=tmp_path, database_path=str(database_path))

    assert migrate.returncode == 0
    assert migrate.stderr == ""
    assert _parse_single_json_line(migrate.stdout) == {
        "operation": "migrate",
        "state": "at_head",
        "current_revision": "0035",
        "head_revision": "0035",
    }
    assert status.returncode == 0
    assert _parse_single_json_line(status.stdout)["state"] == "at_head"
    assert str(database_path) not in migrate.stdout
    assert str(database_path) not in status.stdout


def test_framenest_db_failure_output_is_sanitized_and_stable(
    tmp_path: Path,
) -> None:
    supplied_path = "relative/private/catalog.sqlite3"

    result = _run_db_command("status", cwd=tmp_path, database_path=supplied_path)

    assert result.returncode == 1
    assert result.stdout == ""
    payload = _parse_single_json_line(result.stderr)
    assert payload["operation"] == "status"
    assert payload["state"] == "error"
    assert payload["error_code"] == "FRAMENEST_DB_COMMAND_FAILED"
    combined = result.stdout + result.stderr
    assert supplied_path not in combined
    assert "relative/private" not in combined
    assert "sqlite" not in payload.get("message", "").lower()


def test_framenest_db_status_starts_no_listener_or_child_process(tmp_path: Path) -> None:
    database_path = tmp_path / "no-listener.sqlite3"
    baseline_children = _child_pids(os.getpid())

    result = _run_db_command("status", cwd=tmp_path, database_path=str(database_path))

    assert result.returncode == 0
    assert _child_pids(os.getpid()) - baseline_children == set()


def test_framenest_db_status_dispatch_does_not_bind_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from kronika.infrastructure.persistence.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(tmp_path / "no-bind.sqlite3"))

    with patch("socket.socket.bind", side_effect=AssertionError("socket bind must not run")):
        assert main(["status"]) == 0

    output = capsys.readouterr()
    assert json.loads(output.out)["state"] == "uninitialized"
    assert output.err == ""


def test_importing_cli_module_has_no_execution_side_effects(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import kronika.infrastructure.persistence.cli",
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


def test_framenest_server_startup_does_not_apply_migrations(tmp_path: Path) -> None:
    server_script = _require_server_console_script()
    database_path = tmp_path / "server" / "catalog.sqlite3"
    port = _find_free_loopback_port()
    env = os.environ.copy()
    env["FRAMENEST_HOST"] = "127.0.0.1"
    env["FRAMENEST_PORT"] = str(port)
    env["FRAMENEST_DATABASE_PATH"] = str(database_path)

    proc = subprocess.Popen(
        [str(server_script)],
        cwd=tmp_path,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        _wait_until_port_listening("127.0.0.1", port, STARTUP_TIMEOUT_SECONDS)
        assert not database_path.exists()

        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.communicate(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        _wait_until_port_closed("127.0.0.1", port, SHUTDOWN_TIMEOUT_SECONDS)
    finally:
        if proc.poll() is None:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            proc.communicate(timeout=3.0)

    assert not database_path.exists()
