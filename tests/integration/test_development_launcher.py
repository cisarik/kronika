from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import time
from urllib.request import urlopen

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-dev"


def _free_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


def _run(command: list[str], *, env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(CONSOLE_SCRIPT), *command],
        cwd=REPOSITORY_ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
        timeout=40,
    )


def _health(port: int) -> dict[str, str]:
    with urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as response:
        return json.loads(response.read().decode("utf-8"))


def test_development_launcher_real_process_lifecycle(tmp_path: Path) -> None:
    port = _free_loopback_port()
    assert port != 8000
    env = os.environ.copy()
    env["FRAMENEST_DATABASE_PATH"] = str(tmp_path / "data" / "catalog.sqlite3")
    env["FRAMENEST_DEVELOPMENT_RUNTIME_DIR"] = str(tmp_path / "runtime")
    env["FRAMENEST_DEVELOPMENT_LOG_DIR"] = str(tmp_path / "logs")
    env["FRAMENEST_PORT"] = str(port)

    first_pid: int | None = None
    try:
        started = _run(["start", "--no-open"], env=env)
        assert started.returncode == 0, started.stderr
        assert "FrameNest is running" in started.stdout
        assert env["FRAMENEST_DATABASE_PATH"] not in started.stdout
        assert _health(port) == {"status": "ok"}

        status = _run(["status"], env=env)
        assert status.returncode == 0
        assert "Status: running" in status.stdout
        first_pid = _pid_from_status(status.stdout)

        with urlopen(f"http://127.0.0.1:{port}/", timeout=3) as response:
            assert response.status == 200
            served_root = response.read()
        assert b"Kronika" in served_root
        assert b"FrameNest" not in served_root

        restarted = _run(["restart", "--no-open"], env=env)
        assert restarted.returncode == 0, restarted.stderr
        assert _health(port) == {"status": "ok"}

        restarted_status = _run(["status"], env=env)
        assert restarted_status.returncode == 0
        restarted_pid = _pid_from_status(restarted_status.stdout)
        assert restarted_pid != first_pid

        logs = _run(["logs"], env=env)
        assert logs.returncode == 0

        stopped = _run(["stop"], env=env)
        assert stopped.returncode == 0, stopped.stderr
        assert not (tmp_path / "runtime" / "server-state.json").exists()
        assert first_pid is None or restarted_pid != first_pid

        stopped_status = _run(["status"], env=env)
        assert stopped_status.returncode == 3
        assert "Status: stopped" in stopped_status.stdout

        stopped_again = _run(["stop"], env=env)
        assert stopped_again.returncode == 0
    finally:
        _run(["stop"], env=env)
        _wait_until_port_closed(port)


def _pid_from_status(output: str) -> int:
    for line in output.splitlines():
        if line.startswith("PID: "):
            return int(line.removeprefix("PID: "))
    raise AssertionError(f"status output did not include a PID: {output}")


def _wait_until_port_closed(port: int) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", port)) != 0:
                return
        time.sleep(0.1)
