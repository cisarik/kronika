"""Local browser-development runtime controller for FrameNest."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import errno
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Iterator, Literal
from urllib.error import URLError
from urllib.request import urlopen
import webbrowser

from kronika.configuration import KronikaSettings
from kronika.identity_env import (
    IdentityEnvironmentConflictError,
    IdentityEnvironmentConflictFailure,
    drop_identity_environment_spellings,
    lookup_env,
)
from kronika.infrastructure.persistence.migrations import (
    inspect_database_migration_status,
    upgrade_database_to_head,
)

STATE_SCHEMA = 1
STATE_FILENAME = "server-state.json"
LOCK_FILENAME = "operation.lock"
LAUNCH_MODULE = "kronika.server"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
DEFAULT_HEALTH_TIMEOUT_SECONDS = 15.0
DEFAULT_STOP_TIMEOUT_SECONDS = 10.0
DEFAULT_LOCK_TIMEOUT_SECONDS = 5.0
DEFAULT_LOG_LINES = 80

DATABASE_ENV = "FRAMENEST_DATABASE_PATH"
PORT_ENV = "FRAMENEST_PORT"
HOST_ENV = "FRAMENEST_HOST"
RUNTIME_DIR_ENV = "FRAMENEST_DEVELOPMENT_RUNTIME_DIR"
LOG_DIR_ENV = "FRAMENEST_DEVELOPMENT_LOG_DIR"

#: Setting-name suffixes for the same five development runtime variables. The
#: constants above stay the exact names of the variables; the first three are the
#: ones this module writes into the spawned server environment, and all five are
#: read through :func:`_resolve_override`.
DATABASE_ENV_SUFFIX = "DATABASE_PATH"
PORT_ENV_SUFFIX = "PORT"
RUNTIME_DIR_ENV_SUFFIX = "DEVELOPMENT_RUNTIME_DIR"
LOG_DIR_ENV_SUFFIX = "DEVELOPMENT_LOG_DIR"
HOST_ENV_SUFFIX = "HOST"

#: Every setting-name suffix whose resolved value the launcher writes into the
#: spawned server environment. The launcher is authoritative for exactly these
#: three settings, so the alternate spelling of each one is dropped from the
#: child environment first. Without that, an inherited ``KRONIKA_*`` spelling
#: would reach the child beside the value written here, and the child would exit
#: on a conflict the launcher created itself.
SPAWNED_SETTING_SUFFIXES = (
    HOST_ENV_SUFFIX,
    PORT_ENV_SUFFIX,
    DATABASE_ENV_SUFFIX,
)

StatusKind = Literal["running", "stopped", "stale", "unhealthy", "conflict"]


class DevelopmentRuntimeError(Exception):
    """Expected sanitized runtime failure."""


class RuntimeLockError(DevelopmentRuntimeError):
    """Raised when another runtime operation is already in progress."""


class IdentityEnvironmentDevelopmentError(
    IdentityEnvironmentConflictFailure,
    DevelopmentRuntimeError,
):
    """Launcher failure caused by one setting name set under both prefixes.

    The development runtime resolves its own overrides and builds settings
    through the same identity resolver as every other command, so a conflicting
    pair is translated into this sanitized launcher failure instead of escaping
    as an unhandled resolver error. The message names the two variable suffixes
    only.
    """


@dataclass(frozen=True)
class DevelopmentPaths:
    database_path: Path
    runtime_dir: Path
    state_path: Path
    lock_path: Path
    log_path: Path


@dataclass(frozen=True)
class ProcessSnapshot:
    pid: int
    start_identity: str
    command: str


@dataclass(frozen=True)
class ManagedState:
    schema: int
    pid: int
    process_start_identity: str
    executable: str
    launch_module: str
    host: str
    port: int
    database_path: Path
    log_path: Path
    created_at: float


@dataclass(frozen=True)
class RuntimeStatus:
    kind: StatusKind
    url: str | None
    pid: int | None
    database_state: str
    log_available: bool
    message: str


@dataclass(frozen=True)
class RuntimeResult:
    ok: bool
    status: RuntimeStatus
    message: str


def resolve_development_paths(
    *,
    environ: dict[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> DevelopmentPaths:
    env = os.environ if environ is None else environ
    resolved_home = Path.home() if home is None else home
    platform = sys.platform if platform_name is None else platform_name

    database_path = _database_path(env, platform, resolved_home)
    runtime_dir = _runtime_dir(env, platform, resolved_home)
    log_path = _log_path(env, platform, resolved_home)
    return DevelopmentPaths(
        database_path=database_path,
        runtime_dir=runtime_dir,
        state_path=runtime_dir / STATE_FILENAME,
        lock_path=runtime_dir / LOCK_FILENAME,
        log_path=log_path,
    )


def selected_development_port(environ: dict[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    raw_port = _resolve_override(env, PORT_ENV_SUFFIX)
    if raw_port is None or raw_port == "":
        return DEFAULT_PORT
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise DevelopmentRuntimeError("FRAMENEST_PORT must be an integer.") from exc
    if port < 1 or port > 65535:
        raise DevelopmentRuntimeError("FRAMENEST_PORT must be between 1 and 65535.")
    return port


class DevelopmentRuntime:
    """Owns the safe lifecycle of the managed local development server."""

    def __init__(
        self,
        *,
        environ: dict[str, str] | None = None,
        platform_name: str | None = None,
        home: Path | None = None,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
        open_browser: Callable[[str], bool] = webbrowser.open,
        spawn_process: Callable[..., subprocess.Popen[bytes]] | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self._paths = resolve_development_paths(
            environ=self._environ,
            platform_name=platform_name,
            home=home,
        )
        self._port = selected_development_port(self._environ)
        self._now = now
        self._sleep = sleep
        self._open_browser = open_browser
        self._spawn_process = subprocess.Popen if spawn_process is None else spawn_process

    @property
    def paths(self) -> DevelopmentPaths:
        return self._paths

    @property
    def url(self) -> str:
        return _url(self._port)

    def start(self, *, open_after_start: bool = True) -> RuntimeResult:
        with self._operation_lock():
            status = self.status()
            if status.kind == "running":
                if open_after_start:
                    self._open_healthy_url(status.url or self.url)
                return RuntimeResult(True, status, f"Kronika is already running at {self.url}")
            if status.kind == "conflict":
                return RuntimeResult(False, status, status.message)
            if status.kind == "unhealthy":
                return RuntimeResult(False, status, status.message)
            if status.kind == "stale":
                self._remove_state_file()

            if self._port_is_occupied():
                conflict = RuntimeStatus(
                    kind="conflict",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Port is occupied by an unmanaged process.",
                )
                return RuntimeResult(False, conflict, conflict.message)

            try:
                migration_result = self._migrate_database()
            except Exception:
                status = RuntimeStatus(
                    kind="unhealthy",
                    url=None,
                    pid=None,
                    database_state="unknown",
                    log_available=self._paths.log_path.exists(),
                    message="Development database migration failed.",
                )
                return RuntimeResult(False, status, status.message)
            process: subprocess.Popen[bytes] | None = None
            state_written = False
            try:
                process = self._spawn_server_process()
                snapshot = self._wait_for_process_snapshot(process.pid)
                state = ManagedState(
                    schema=STATE_SCHEMA,
                    pid=process.pid,
                    process_start_identity=snapshot.start_identity,
                    executable=sys.executable,
                    launch_module=LAUNCH_MODULE,
                    host=LOOPBACK_HOST,
                    port=self._port,
                    database_path=self._paths.database_path,
                    log_path=self._paths.log_path,
                    created_at=self._now(),
                )
                self._write_state(state)
                state_written = True
                if not self._wait_for_health():
                    raise DevelopmentRuntimeError("Kronika did not become healthy in time.")
            except Exception:
                if process is not None and self._is_pid_live(process.pid):
                    self._terminate_pid(process.pid)
                    self._wait_for_pid_exit(process.pid, timeout_seconds=2.0)
                if state_written:
                    self._remove_state_file()
                status = RuntimeStatus(
                    kind="unhealthy",
                    url=None,
                    pid=process.pid if process is not None else None,
                    database_state=migration_result.state,
                    log_available=self._paths.log_path.exists(),
                    message="Kronika startup failed. Check logs for details.",
                )
                return RuntimeResult(False, status, status.message)

            running = RuntimeStatus(
                kind="running",
                url=self.url,
                pid=process.pid,
                database_state=migration_result.state,
                log_available=self._paths.log_path.exists(),
                message=f"Kronika is running at {self.url}",
            )
            if open_after_start:
                self._open_healthy_url(self.url)
            return RuntimeResult(True, running, running.message)

    def stop(self) -> RuntimeResult:
        with self._operation_lock():
            status, state = self._status_with_state()
            if status.kind in {"stopped", "stale"}:
                if status.kind == "stale":
                    self._remove_state_file()
                stopped = RuntimeStatus(
                    kind="stopped",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Kronika is stopped.",
                )
                return RuntimeResult(True, stopped, "Kronika is stopped.")
            if status.kind == "conflict" or state is None:
                return RuntimeResult(False, status, status.message)

            self._terminate_pid(state.pid)
            if not self._wait_for_pid_exit(state.pid, DEFAULT_STOP_TIMEOUT_SECONDS):
                timeout = RuntimeStatus(
                    kind="unhealthy",
                    url=self.url,
                    pid=state.pid,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Managed process did not stop after graceful termination.",
                )
                return RuntimeResult(False, timeout, timeout.message)
            self._remove_state_file()
            stopped = RuntimeStatus(
                kind="stopped",
                url=None,
                pid=None,
                database_state=self._database_state(),
                log_available=self._paths.log_path.exists(),
                message="Kronika stopped.",
            )
            return RuntimeResult(True, stopped, stopped.message)

    def restart(self, *, open_after_start: bool = True) -> RuntimeResult:
        stopped = self.stop()
        if not stopped.ok:
            return stopped
        return self.start(open_after_start=open_after_start)

    def status(self) -> RuntimeStatus:
        status, _state = self._status_with_state()
        return status

    def open(self) -> RuntimeResult:
        status = self.status()
        if status.kind != "running" or status.url is None:
            return RuntimeResult(False, status, "Kronika is not running.")
        try:
            self._open_healthy_url(status.url)
        except DevelopmentRuntimeError as exc:
            failed = RuntimeStatus(
                kind="unhealthy",
                url=status.url,
                pid=status.pid,
                database_state=status.database_state,
                log_available=status.log_available,
                message=str(exc),
            )
            return RuntimeResult(False, failed, failed.message)
        return RuntimeResult(True, status, f"Opened {status.url}")

    def read_log_tail(self, *, lines: int = DEFAULT_LOG_LINES) -> list[str]:
        if not self._paths.log_path.exists():
            return []
        with self._paths.log_path.open("r", encoding="utf-8", errors="replace") as log_file:
            return log_file.readlines()[-lines:]

    def follow_log(self, *, poll_seconds: float = 0.5) -> Iterator[str]:
        self._paths.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self._paths.log_path.open("a+", encoding="utf-8", errors="replace") as log_file:
            log_file.seek(0, os.SEEK_END)
            while True:
                line = log_file.readline()
                if line:
                    yield line
                    continue
                self._sleep(poll_seconds)

    def _status_with_state(self) -> tuple[RuntimeStatus, ManagedState | None]:
        try:
            state = self._read_state()
        except DevelopmentRuntimeError as exc:
            status = RuntimeStatus(
                kind="conflict",
                url=None,
                pid=None,
                database_state=self._database_state(),
                log_available=self._paths.log_path.exists(),
                message=str(exc),
            )
            return status, None
        if state is None:
            if self._port_is_occupied():
                status = RuntimeStatus(
                    kind="conflict",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Port is occupied by an unmanaged process.",
                )
                return status, None
            return (
                RuntimeStatus(
                    kind="stopped",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Kronika is stopped.",
                ),
                None,
            )

        snapshot = self._process_snapshot(state.pid)
        if snapshot is None:
            return (
                RuntimeStatus(
                    kind="stale",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Managed state is stale; recorded process is not running.",
                ),
                state,
            )
        if not self._snapshot_matches_state(snapshot, state):
            return (
                RuntimeStatus(
                    kind="conflict",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Managed state does not match the live process.",
                ),
                None,
            )
        if state.host != LOOPBACK_HOST:
            return (
                RuntimeStatus(
                    kind="conflict",
                    url=None,
                    pid=None,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message="Managed state is not bound to loopback.",
                ),
                None,
            )
        if self._health_is_ok(state.port):
            return (
                RuntimeStatus(
                    kind="running",
                    url=_url(state.port),
                    pid=state.pid,
                    database_state=self._database_state(),
                    log_available=self._paths.log_path.exists(),
                    message=f"Kronika is running at {_url(state.port)}",
                ),
                state,
            )
        return (
            RuntimeStatus(
                kind="unhealthy",
                url=_url(state.port),
                pid=state.pid,
                database_state=self._database_state(),
                log_available=self._paths.log_path.exists(),
                message="Managed Kronika process is running but health is not ready.",
            ),
            state,
        )

    def _migrate_database(self) -> Any:
        try:
            settings = KronikaSettings(
                host=LOOPBACK_HOST,
                port=self._port,
                database_path=self._paths.database_path,
                _env_file=None,
            )
        except IdentityEnvironmentConflictError as exc:
            raise IdentityEnvironmentDevelopmentError(str(exc)) from exc
        return upgrade_database_to_head(settings)

    def _database_state(self) -> str:
        try:
            settings = KronikaSettings(
                host=LOOPBACK_HOST,
                port=self._port,
                database_path=self._paths.database_path,
                _env_file=None,
            )
            return inspect_database_migration_status(settings).state
        except Exception:
            return "unknown"

    def _spawn_server_process(self) -> subprocess.Popen[bytes]:
        self._paths.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        self._paths.log_path.parent.mkdir(parents=True, exist_ok=True)
        _private_directory(self._paths.runtime_dir)
        env = dict(self._environ)
        drop_identity_environment_spellings(env, SPAWNED_SETTING_SUFFIXES)
        env[HOST_ENV] = LOOPBACK_HOST
        env[PORT_ENV] = str(self._port)
        env[DATABASE_ENV] = str(self._paths.database_path)
        log_file = self._paths.log_path.open("ab")
        try:
            return self._spawn_process(
                [sys.executable, "-m", LAUNCH_MODULE],
                cwd=str(self._paths.runtime_dir),
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
                start_new_session=True,
            )
        finally:
            log_file.close()

    def _wait_for_health(self) -> bool:
        deadline = self._now() + DEFAULT_HEALTH_TIMEOUT_SECONDS
        while self._now() < deadline:
            if self._health_is_ok(self._port):
                return True
            self._sleep(0.2)
        return False

    def _health_is_ok(self, port: int) -> bool:
        try:
            with urlopen(f"{_url(port)}health", timeout=1.0) as response:
                if response.status != 200:
                    return False
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, URLError, ValueError, json.JSONDecodeError):
            return False
        return payload == {"status": "ok"}

    def _open_healthy_url(self, url: str) -> None:
        try:
            opened = self._open_browser(url)
        except Exception as exc:
            raise DevelopmentRuntimeError("Browser opening failed.") from exc
        if opened is False:
            raise DevelopmentRuntimeError("Browser opening failed.")

    def _read_state(self) -> ManagedState | None:
        if not self._paths.state_path.exists():
            return None
        try:
            payload = json.loads(self._paths.state_path.read_text(encoding="utf-8"))
            return _state_from_payload(payload)
        except Exception:
            raise DevelopmentRuntimeError("Managed state is malformed.")

    def _write_state(self, state: ManagedState) -> None:
        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        _private_directory(self._paths.runtime_dir)
        payload = {
            "schema": state.schema,
            "pid": state.pid,
            "process_start_identity": state.process_start_identity,
            "executable": state.executable,
            "launch_module": state.launch_module,
            "host": state.host,
            "port": state.port,
            "database_path": str(state.database_path),
            "log_path": str(state.log_path),
            "created_at": state.created_at,
        }
        fd, temporary_name = tempfile.mkstemp(
            prefix=".server-state-",
            suffix=".tmp",
            dir=self._paths.runtime_dir,
            text=True,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as file:
                json.dump(payload, file, sort_keys=True)
                file.write("\n")
            os.chmod(temporary_path, 0o600)
            os.replace(temporary_path, self._paths.state_path)
        finally:
            if temporary_path.exists():
                temporary_path.unlink()

    def _remove_state_file(self) -> None:
        try:
            self._paths.state_path.unlink()
        except FileNotFoundError:
            return

    @contextmanager
    def _operation_lock(self) -> Iterator[None]:
        self._paths.runtime_dir.mkdir(parents=True, exist_ok=True)
        _private_directory(self._paths.runtime_dir)
        with self._paths.lock_path.open("a+", encoding="utf-8") as lock_file:
            deadline = self._now() + DEFAULT_LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError as exc:
                    if self._now() >= deadline:
                        raise RuntimeLockError(
                            "Another Kronika runtime operation is in progress."
                        ) from exc
                    self._sleep(0.1)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    def _wait_for_process_snapshot(self, pid: int) -> ProcessSnapshot:
        deadline = self._now() + 2.0
        while self._now() < deadline:
            snapshot = self._process_snapshot(pid)
            if snapshot is not None:
                return snapshot
            self._sleep(0.05)
        raise DevelopmentRuntimeError("Started process identity is unavailable.")

    def _process_snapshot(self, pid: int) -> ProcessSnapshot | None:
        if not self._is_pid_live(pid):
            return None
        try:
            start = subprocess.run(
                ["ps", "-p", str(pid), "-o", "lstart="],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
            args = subprocess.run(
                ["ps", "-p", str(pid), "-o", "args="],
                check=False,
                capture_output=True,
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        start_identity = start.stdout.strip()
        command = args.stdout.strip()
        if start.returncode != 0 or args.returncode != 0 or not start_identity or not command:
            return None
        return ProcessSnapshot(pid=pid, start_identity=start_identity, command=command)

    def _snapshot_matches_state(self, snapshot: ProcessSnapshot, state: ManagedState) -> bool:
        return (
            snapshot.pid == state.pid
            and snapshot.start_identity == state.process_start_identity
            and state.launch_module == LAUNCH_MODULE
            and f"-m {LAUNCH_MODULE}" in snapshot.command
        )

    def _is_pid_live(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _terminate_pid(self, pid: int) -> None:
        os.kill(pid, signal.SIGTERM)

    def _wait_for_pid_exit(self, pid: int, timeout_seconds: float) -> bool:
        deadline = self._now() + timeout_seconds
        while self._now() < deadline:
            if not self._is_pid_live(pid):
                return True
            self._sleep(0.1)
        return not self._is_pid_live(pid)

    def _port_is_occupied(self) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((LOOPBACK_HOST, self._port))
            except OSError as exc:
                return exc.errno in {errno.EADDRINUSE, errno.EACCES}
        return False


def _resolve_override(
    env: os._Environ[str] | dict[str, str],
    suffix: str,
) -> str | None:
    """Resolve one development override through the identity resolver."""
    try:
        return lookup_env(suffix, environ=env)
    except IdentityEnvironmentConflictError as exc:
        raise IdentityEnvironmentDevelopmentError(str(exc)) from exc


def _database_path(env: os._Environ[str] | dict[str, str], platform: str, home: Path) -> Path:
    override = _resolve_override(env, DATABASE_ENV_SUFFIX)
    if override:
        return _absolute_override(DATABASE_ENV, override)
    if platform == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "FrameNest"
            / "development"
            / "catalog.sqlite3"
        ).resolve(strict=False)
    return (
        _xdg_directory(env, "XDG_DATA_HOME", home / ".local" / "share")
        / "FrameNest"
        / "development"
        / "catalog.sqlite3"
    ).resolve(strict=False)


def _runtime_dir(env: os._Environ[str] | dict[str, str], platform: str, home: Path) -> Path:
    override = _resolve_override(env, RUNTIME_DIR_ENV_SUFFIX)
    if override:
        return _absolute_override(RUNTIME_DIR_ENV, override)
    if platform == "darwin":
        return (
            home
            / "Library"
            / "Application Support"
            / "FrameNest"
            / "development"
            / "runtime"
        ).resolve(strict=False)
    return (
        _xdg_directory(env, "XDG_STATE_HOME", home / ".local" / "state")
        / "FrameNest"
        / "development"
        / "runtime"
    ).resolve(strict=False)


def _log_path(env: os._Environ[str] | dict[str, str], platform: str, home: Path) -> Path:
    override = _resolve_override(env, LOG_DIR_ENV_SUFFIX)
    if override:
        return _absolute_override(LOG_DIR_ENV, override) / "server.log"
    if platform == "darwin":
        return (home / "Library" / "Logs" / "FrameNest" / "development" / "server.log").resolve(
            strict=False
        )
    return (
        _xdg_directory(env, "XDG_STATE_HOME", home / ".local" / "state")
        / "FrameNest"
        / "development"
        / "logs"
        / "server.log"
    ).resolve(strict=False)


def _absolute_override(name: str, value: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        raise DevelopmentRuntimeError(f"{name} must be an absolute path.")
    return path.resolve(strict=False)


def _xdg_directory(env: os._Environ[str] | dict[str, str], name: str, default: Path) -> Path:
    value = env.get(name)
    if not value:
        return default.resolve(strict=False)
    path = Path(value)
    if not path.is_absolute():
        raise DevelopmentRuntimeError(f"{name} must be an absolute path.")
    return path.resolve(strict=False)


def _url(port: int) -> str:
    return f"http://{LOOPBACK_HOST}:{port}/"


def _private_directory(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError:
        return


def _state_from_payload(payload: Any) -> ManagedState:
    if not isinstance(payload, dict):
        raise ValueError("state payload must be an object")
    schema = int(payload["schema"])
    if schema != STATE_SCHEMA:
        raise ValueError("unsupported state schema")
    return ManagedState(
        schema=schema,
        pid=int(payload["pid"]),
        process_start_identity=str(payload["process_start_identity"]),
        executable=str(payload["executable"]),
        launch_module=str(payload["launch_module"]),
        host=str(payload["host"]),
        port=int(payload["port"]),
        database_path=Path(str(payload["database_path"])),
        log_path=Path(str(payload["log_path"])),
        created_at=float(payload["created_at"]),
    )
