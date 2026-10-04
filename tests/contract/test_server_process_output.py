"""Contract tests for direct FrameNest server process output and shutdown."""

from __future__ import annotations

import json
import os
import re
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PYTHON = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
REPRESENTATIVE_SECRET = "process-output-contract-api-key-secret"
STARTUP_TIMEOUT_SECONDS = 8.0
SHUTDOWN_TIMEOUT_SECONDS = 8.0
REQUIRED_JSON_FIELDS = (
    "timestamp",
    "level",
    "event",
    "component",
    "operation",
    "error_code",
    "retryable",
)
ANSI_ESCAPE_PATTERN = re.compile(r"\x1b\[")
FORBIDDEN_OUTPUT_FRAGMENTS = (
    REPRESENTATIVE_SECRET,
    "http://",
    "https://",
    "/Users/",
    "video.mkv",
    "Traceback (most recent call last)",
    "KeyboardInterrupt",
)


def _require_console_script() -> Path:
    if not CANONICAL_PYTHON.is_file():
        pytest.fail(f"Expected canonical interpreter at {CANONICAL_PYTHON}")
    return CANONICAL_PYTHON


def _server_command() -> list[str]:
    return [str(_require_console_script()), "-m", "kronika.server"]


def _server_env(base: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy() if base is None else dict(base)
    env["PYTHONPATH"] = str(REPOSITORY_ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("LD_LIBRARY_PATH", None)
    return env


def _find_free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


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
    raise TimeoutError(f"Timed out waiting for {host}:{port} to accept connections") from last_error


def _wait_until_port_closed(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                time.sleep(0.05)
        except OSError:
            return
    raise TimeoutError(f"Timed out waiting for {host}:{port} to close")


def _http_get_json(url: str) -> tuple[int, dict[str, Any]]:
    with urllib.request.urlopen(url, timeout=2.0) as response:
        status = response.status
        body = json.loads(response.read().decode("utf-8"))
        return status, body


def _assert_privacy_clean(combined_output: str) -> None:
    assert not ANSI_ESCAPE_PATTERN.search(combined_output)
    for fragment in FORBIDDEN_OUTPUT_FRAGMENTS:
        assert fragment not in combined_output


def _parse_stderr_json_lines(stderr: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for line in stderr.splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        assert isinstance(payload, dict)
        for field in REQUIRED_JSON_FIELDS:
            assert field in payload
        payloads.append(payload)
    return payloads


def _run_direct_process_shutdown(
    termination_signal: signal.Signals,
) -> dict[str, Any]:
    port = _find_free_loopback_port()
    env = _server_env()
    env["FRAMENEST_HOST"] = "127.0.0.1"
    env["FRAMENEST_PORT"] = str(port)
    env["FRAMENEST_API_KEY"] = REPRESENTATIVE_SECRET
    env.pop("UVICORN_HOST", None)
    env.pop("UVICORN_PORT", None)

    popen_kwargs: dict[str, Any] = {
        "cwd": str(REPOSITORY_ROOT),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
    }
    if os.name == "posix":
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(_server_command(), **popen_kwargs)
    result: dict[str, Any] = {
        "termination_signal": termination_signal.name,
        "parent_pid": proc.pid,
        "port": port,
    }

    try:
        _wait_until_port_listening("127.0.0.1", port, STARTUP_TIMEOUT_SECONDS)
        status, body = _http_get_json(f"http://127.0.0.1:{port}/health")
        result["health_status"] = status
        result["health_body"] = body

        if os.name != "posix":
            pytest.skip("POSIX process-group termination is required for this contract")

        os.killpg(os.getpgid(proc.pid), termination_signal)
        try:
            stdout, stderr = proc.communicate(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            stdout, stderr = proc.communicate(timeout=3.0)
            pytest.fail("Direct server process did not shut down within the bounded timeout")

        result["exit_code"] = proc.returncode
        result["stdout"] = stdout
        result["stderr"] = stderr
        result["stdout_lines"] = [line for line in stdout.splitlines() if line.strip()]
        result["stderr_lines"] = [line for line in stderr.splitlines() if line.strip()]
        _wait_until_port_closed("127.0.0.1", port, SHUTDOWN_TIMEOUT_SECONDS)
        result["remaining_children"] = _child_pids(proc.pid)
        return result
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
            proc.communicate(timeout=3.0)


@pytest.fixture
def direct_shutdown_result(request: pytest.FixtureRequest) -> dict[str, Any]:
    termination_signal = request.param
    return _run_direct_process_shutdown(termination_signal)


@pytest.mark.parametrize(
    "direct_shutdown_result",
    [signal.SIGINT, signal.SIGTERM],
    indirect=True,
)
def test_direct_process_starts_health_and_shuts_down_cleanly(
    direct_shutdown_result: dict[str, Any],
) -> None:
    result = direct_shutdown_result
    assert result["health_status"] == 200
    assert result["health_body"] == {"status": "ok"}
    assert result["remaining_children"] == set()


@pytest.mark.parametrize(
    "direct_shutdown_result",
    [signal.SIGINT, signal.SIGTERM],
    indirect=True,
)
def test_direct_process_stderr_is_structured_json_only(
    direct_shutdown_result: dict[str, Any],
) -> None:
    stderr = direct_shutdown_result["stderr"]
    assert stderr.strip()
    payloads = _parse_stderr_json_lines(stderr)
    assert payloads
    assert len(payloads) == len(direct_shutdown_result["stderr_lines"])


@pytest.mark.parametrize(
    "direct_shutdown_result",
    [signal.SIGINT, signal.SIGTERM],
    indirect=True,
)
def test_direct_process_has_no_traceback_or_privacy_leaks(
    direct_shutdown_result: dict[str, Any],
) -> None:
    combined = direct_shutdown_result["stdout"] + direct_shutdown_result["stderr"]
    _assert_privacy_clean(combined)


@pytest.mark.parametrize(
    "direct_shutdown_result",
    [signal.SIGINT, signal.SIGTERM],
    indirect=True,
)
def test_direct_process_stdout_is_empty(
    direct_shutdown_result: dict[str, Any],
) -> None:
    assert direct_shutdown_result["stdout_lines"] == []


def test_direct_process_sigint_shutdown_has_no_traceback() -> None:
    result = _run_direct_process_shutdown(signal.SIGINT)
    combined = result["stdout"] + result["stderr"]
    assert "Traceback (most recent call last)" not in combined
    assert "KeyboardInterrupt" not in combined


def test_direct_process_sigterm_shutdown_has_no_traceback() -> None:
    result = _run_direct_process_shutdown(signal.SIGTERM)
    combined = result["stdout"] + result["stderr"]
    assert "Traceback (most recent call last)" not in combined
    assert "KeyboardInterrupt" not in combined


def test_direct_process_emergency_cleanup_prevents_listener_leak() -> None:
    port = _find_free_loopback_port()
    env = _server_env()
    env["FRAMENEST_HOST"] = "127.0.0.1"
    env["FRAMENEST_PORT"] = str(port)
    env["FRAMENEST_API_KEY"] = REPRESENTATIVE_SECRET

    popen_kwargs: dict[str, Any] = {
        "cwd": str(REPOSITORY_ROOT),
        "env": env,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "text": True,
        "start_new_session": True,
    }
    proc = subprocess.Popen(_server_command(), **popen_kwargs)
    try:
        _wait_until_port_listening("127.0.0.1", port, STARTUP_TIMEOUT_SECONDS)
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        proc.communicate(timeout=3.0)
    finally:
        if proc.poll() is None:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except ProcessLookupError:
                proc.kill()
            proc.communicate(timeout=3.0)
        _wait_until_port_closed("127.0.0.1", port, SHUTDOWN_TIMEOUT_SECONDS)
        assert _child_pids(proc.pid) == set()
