"""Process-level SIGTERM evidence for the injected in-process lifecycle envelope."""

from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

WORKTREE_ROOT = Path(__file__).resolve().parents[2]
CANONICAL_PYTHON = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
CHILD_SCRIPT = r'''
from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

import uvicorn
from sqlalchemy import insert

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.catalog_schema import devices, libraries
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DESTINATION_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


class FakeChildDownloader:
    def __init__(self, pid_path: Path) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        pid_path.write_text(str(self.proc.pid), encoding="utf-8")

    def bind_shutdown_deadline(self, deadline: object) -> None:
        del deadline

    def request_interrupt(self) -> None:
        if self.proc.poll() is not None:
            return
        try:
            os.killpg(self.proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return
        try:
            self.proc.wait(timeout=0.2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.proc.wait(timeout=0.2)

    def attest_version(self) -> str:
        return "fake-test"


def main() -> None:
    workspace = Path(sys.argv[1])
    port = int(sys.argv[2])
    database_path = workspace / "db" / "catalog.sqlite3"
    quarantine = workspace / "quarantine"
    published = workspace / "published"
    youtube_root = workspace / "youtube-acquisition"
    pid_path = workspace / "fake-child.pid"
    database_path.parent.mkdir()
    quarantine.mkdir()
    published.mkdir()
    youtube_root.mkdir(mode=0o700)
    youtube_root.chmod(0o700)
    settings = KronikaSettings(
        host="127.0.0.1",
        port=port,
        database_path=database_path,
        gallery_preview_cache_path=workspace / "previews",
        upload_quarantine_root=quarantine,
        upload_publication_library_id=DESTINATION_ID,
        youtube_acquisition_root=youtube_root,
        upload_min_free_space_reserve_bytes=0,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(devices).values(
                    id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    display_name="Synthetic device",
                )
            )
            connection.execute(
                insert(libraries).values(
                    id=DESTINATION_ID,
                    device_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    display_name="Published originals",
                    path_flavor="posix",
                    root_path=str(published),
                )
            )
    finally:
        dispose_engine(engine)
    app = create_app(
        settings=settings,
        youtube_downloader=FakeChildDownloader(pid_path),
        lifespan_shutdown_budget_seconds=0.4,
    )
    config = uvicorn.Config(
        app=app,
        host="127.0.0.1",
        port=port,
        log_config=None,
        access_log=False,
        timeout_graceful_shutdown=0.05,
    )
    uvicorn.Server(config).run()


if __name__ == "__main__":
    main()
'''


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
    raise AssertionError(f"server did not listen: {last_error}")


def _http_get_json(url: str) -> tuple[int, dict[str, object]]:
    with urllib.request.urlopen(url, timeout=2) as response:
        payload = json.loads(response.read().decode("utf-8"))
        return int(response.status), payload


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _child_pids(parent_pid: int) -> set[int]:
    result = subprocess.run(
        ["pgrep", "-P", str(parent_pid)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {int(pid) for pid in result.stdout.splitlines() if pid.strip()}


def test_sigterm_exits_within_injected_envelope_and_reaps_fake_child(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("POSIX SIGTERM lifecycle evidence is required")
    script_path = tmp_path / "lifecycle_child.py"
    script_path.write_text(CHILD_SCRIPT, encoding="utf-8")
    port = _find_free_loopback_port()
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WORKTREE_ROOT / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("LD_LIBRARY_PATH", None)
    stdout_path = tmp_path / "child.stdout"
    stderr_path = tmp_path / "child.stderr"
    stdout_file = stdout_path.open("w", encoding="utf-8")
    stderr_file = stderr_path.open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(CANONICAL_PYTHON), str(script_path), str(tmp_path), str(port)],
        cwd=str(WORKTREE_ROOT),
        env=env,
        stdout=stdout_file,
        stderr=stderr_file,
    )
    fake_pid: int | None = None
    try:
        try:
            _wait_until_port_listening("127.0.0.1", port, 8.0)
        except AssertionError:
            stdout_file.flush()
            stderr_file.flush()
            raise AssertionError(
                "server did not listen\n"
                f"stdout={stdout_path.read_text(encoding='utf-8')[-4000:]}\n"
                f"stderr={stderr_path.read_text(encoding='utf-8')[-4000:]}"
            ) from None
        status, body = _http_get_json(f"http://127.0.0.1:{port}/health")
        assert status == 200
        assert body == {"status": "ok"}
        pid_path = tmp_path / "fake-child.pid"
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not pid_path.exists():
            time.sleep(0.02)
        fake_pid = int(pid_path.read_text(encoding="utf-8"))
        assert _pid_alive(fake_pid)
        started = time.monotonic()
        os.kill(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate(timeout=3.0)
            raise AssertionError("process did not exit within the injected envelope")
        elapsed = time.monotonic() - started
        assert elapsed < 2.0
        # Uvicorn 0.49 restores the default SIGTERM handler after graceful
        # shutdown and re-raises the captured signal, so the process may exit
        # with -SIGTERM rather than 0. Forced self-termination is forbidden.
        assert proc.returncode in {0, -signal.SIGTERM}
        assert not _pid_alive(fake_pid)
        assert _child_pids(proc.pid) == set()
        with sqlite3.connect(tmp_path / "db" / "catalog.sqlite3") as connection:
            row = connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()
        assert row == ("0035",)
        connection_retry = sqlite3.connect(tmp_path / "db" / "catalog.sqlite3")
        try:
            connection_retry.execute("SELECT COUNT(*) FROM alembic_version")
        finally:
            connection_retry.close()
    finally:
        stdout_file.close()
        stderr_file.close()
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=3.0)
        if fake_pid is not None and _pid_alive(fake_pid):
            try:
                os.killpg(fake_pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                try:
                    os.kill(fake_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
