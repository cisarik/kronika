"""Contract tests for the live Uvicorn loopback runtime and UDS provenance."""

from __future__ import annotations

import contextlib
import errno
import json
import os
import socket
import stat
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

REPRESENTATIVE_SECRET = "contract-runtime-api-key-secret"
STARTUP_TIMEOUT_SECONDS = 5.0
SHUTDOWN_TIMEOUT_SECONDS = 5.0
FAIL_CLOSED_STARTUP_TIMEOUT_SECONDS = 15.0


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


def _child_pids() -> set[int]:
    if os.name != "posix":
        return set()
    result = subprocess.run(
        ["pgrep", "-P", str(os.getpid())],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return set()
    return {int(pid) for pid in result.stdout.splitlines() if pid.strip()}


def test_live_loopback_runtime_health_shutdown_and_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import create_server

    monkeypatch.delenv("UVICORN_HOST", raising=False)
    monkeypatch.delenv("UVICORN_PORT", raising=False)
    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    settings = KronikaSettings(
        host="127.0.0.1",
        port=0,
        api_key=SecretStr(REPRESENTATIVE_SECRET),
        _env_file=None,
    )
    server = create_server(settings=settings)
    assert server.config.host == "127.0.0.1"
    assert server.config.host not in {"0.0.0.0", "::"}

    runtime_thread: threading.Thread | None = None
    bound_port: int | None = None
    baseline_children = _child_pids()

    try:
        runtime_thread = threading.Thread(target=server.run, name="uvicorn-runtime-contract")
        runtime_thread.start()

        deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
        while time.monotonic() < deadline:
            if server.started and server.servers:
                sockets = server.servers[0].sockets
                if sockets:
                    bound_port = int(sockets[0].getsockname()[1])
                    break
            time.sleep(0.05)
        if bound_port is None:
            raise TimeoutError("Timed out waiting for Uvicorn to bind an ephemeral loopback port")

        _wait_until_port_listening("127.0.0.1", bound_port, STARTUP_TIMEOUT_SECONDS)

        status, body = _http_get_json(f"http://127.0.0.1:{bound_port}/health")
        assert status == 200
        assert body == {"status": "ok"}

        server.should_exit = True
        runtime_thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        assert not runtime_thread.is_alive()

        _wait_until_port_closed("127.0.0.1", bound_port, SHUTDOWN_TIMEOUT_SECONDS)
        assert _child_pids() - baseline_children == set()
    finally:
        server.should_exit = True
        if runtime_thread is not None and runtime_thread.is_alive():
            runtime_thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
        if bound_port is not None:
            try:
                _wait_until_port_closed("127.0.0.1", bound_port, SHUTDOWN_TIMEOUT_SECONDS)
            except TimeoutError:
                pytest.fail("Listener remained open after contract test cleanup")


# --- UDS socket provenance tightening and fail-closed verification --------


@contextlib.contextmanager
def _restored_umask(mask: int):
    previous = os.umask(mask)
    try:
        yield
    finally:
        os.umask(previous)


def _precreate_unix_socket_file(path: Path, mode: int) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
        os.chmod(str(path), mode)
    finally:
        sock.close()


def _build_uds_settings(tmp_path: Path, filename: str) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / filename,
        external_origin="https://nuc-1.example.ts.net",
        identity_map={"admin@example.com": "admin"},
        _env_file=None,
    )


def _record_server_emissions(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    from kronika.server import LOGGER

    emissions: list[dict[str, Any]] = []
    original_emit = LOGGER.emit

    def recording_emit(**kwargs: Any) -> None:
        emissions.append(kwargs)
        original_emit(**kwargs)

    monkeypatch.setattr(LOGGER, "emit", recording_emit)
    return emissions


def _record_verification_sightings(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    import kronika.server as server_module

    original_verify = server_module._verify_uds_socket_provenance
    sightings: list[dict[str, Any]] = []

    def recording_verify(uds_path: Path) -> None:
        stat_result = os.stat(uds_path)
        sightings.append(
            {
                "is_socket": stat.S_ISSOCK(stat_result.st_mode),
                "mode": stat.S_IMODE(stat_result.st_mode),
                "uid": stat_result.st_uid,
            }
        )
        original_verify(uds_path)

    monkeypatch.setattr(server_module, "_verify_uds_socket_provenance", recording_verify)
    return sightings


def _run_server_and_capture(server: Any, timeout: float) -> list[BaseException]:
    captured: list[BaseException] = []

    def target() -> None:
        try:
            server.run()
        except BaseException as exc:
            captured.append(exc)

    thread = threading.Thread(target=target, name="uvicorn-uds-provenance")
    thread.start()
    thread.join(timeout=timeout)
    assert not thread.is_alive()
    return captured


def _assert_listening_servers_closed(server: Any) -> None:
    for asyncio_server in getattr(server, "servers", None) or []:
        assert asyncio_server.sockets == ()


def _assert_single_fail_closed_emission(
    emissions: list[dict[str, Any]],
    reason: str,
    forbidden_path: str,
) -> None:
    assert len(emissions) == 1
    emission = emissions[0]
    assert emission["level"] == "CRITICAL"
    assert emission["event"] == "uds_socket_provenance_failure"
    assert emission["operation"] == "startup"
    assert emission["error_code"] == "UDS_SOCKET_PROVENANCE_FAILURE"
    assert emission["retryable"] is False
    assert emission["context"] == {"reason": reason}
    assert forbidden_path not in repr(emission)


def _cleanup_uds_file(uds_path: Path) -> None:
    uds_path.unlink(missing_ok=True)


def _http_get_over_uds(uds_path: Path, target: str) -> tuple[bytes, bytes]:
    request = (
        f"GET {target} HTTP/1.1\r\n"
        "Host: framenest\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii")
    chunks: list[bytes] = []
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(STARTUP_TIMEOUT_SECONDS)
        sock.connect(str(uds_path))
        sock.sendall(request)
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
    raw = b"".join(chunks)
    header, _, body = raw.partition(b"\r\n\r\n")
    return header.split(b"\r\n", 1)[0], body


def test_uds_startup_tightens_bound_socket_to_owner_only_and_serves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import UdsProvenanceVerifyingServer, create_server

    uds_path = tmp_path / "framenest.sock"
    settings = _build_uds_settings(tmp_path, "framenest.sock")
    upgrade_database_to_head(settings)
    server = create_server(settings=settings)
    assert isinstance(server, UdsProvenanceVerifyingServer)
    emissions = _record_server_emissions(monkeypatch)

    runtime_thread: threading.Thread | None = None
    try:
        with _restored_umask(0o077):
            runtime_thread = threading.Thread(
                target=server.run,
                name="uvicorn-uds-owner-only",
            )
            runtime_thread.start()
            deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
            while not (server.started and getattr(server, "servers", None)):
                if time.monotonic() > deadline:
                    raise TimeoutError("Timed out waiting for UDS startup")
                time.sleep(0.05)
            assert stat.S_ISSOCK(uds_path.stat().st_mode)
            assert stat.S_IMODE(uds_path.stat().st_mode) == 0o600
            status_line, body = _http_get_over_uds(uds_path, "/health")
            assert b"200" in status_line
            assert json.loads(body) == {"status": "ok"}
        assert emissions == []
    finally:
        server.should_exit = True
        if runtime_thread is not None:
            runtime_thread.join(timeout=SHUTDOWN_TIMEOUT_SECONDS)
            assert not runtime_thread.is_alive()
        _cleanup_uds_file(uds_path)


def test_uds_startup_fails_closed_when_tightening_is_neutralized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import UdsSocketProvenanceError, create_server

    uds_path = tmp_path / "guard.sock"
    _precreate_unix_socket_file(uds_path, mode=0o755)
    settings = _build_uds_settings(tmp_path, "guard.sock")
    server = create_server(settings=settings)
    emissions = _record_server_emissions(monkeypatch)
    sightings = _record_verification_sightings(monkeypatch)
    monkeypatch.setattr(
        "kronika.server._tighten_uds_socket_permissions",
        lambda path: None,
    )

    captured = _run_server_and_capture(server, FAIL_CLOSED_STARTUP_TIMEOUT_SECONDS)

    assert len(captured) == 1
    assert isinstance(captured[0], UdsSocketProvenanceError)
    assert captured[0].reason == "permission_bits_not_owner_only"
    assert server.started is True
    assert sightings == [
        {
            "is_socket": True,
            "mode": 0o755,
            "uid": os.geteuid(),
        }
    ]
    _assert_listening_servers_closed(server)
    _assert_single_fail_closed_emission(
        emissions,
        "permission_bits_not_owner_only",
        str(tmp_path),
    )
    _cleanup_uds_file(uds_path)


def test_uds_startup_fails_closed_when_tightening_chmod_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import UdsSocketProvenanceError, create_server

    uds_path = tmp_path / "chmod-fail.sock"
    settings = _build_uds_settings(tmp_path, "chmod-fail.sock")
    server = create_server(settings=settings)
    emissions = _record_server_emissions(monkeypatch)
    sightings = _record_verification_sightings(monkeypatch)
    real_chmod = os.chmod

    def chmod_failing_for_owner_only_mode(
        path: Any,
        mode: int,
        *args: Any,
        **kwargs: Any,
    ) -> None:
        if mode == 0o600:
            raise PermissionError(
                errno.EACCES,
                "simulated owner-only tightening failure",
            )
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", chmod_failing_for_owner_only_mode)

    captured = _run_server_and_capture(server, FAIL_CLOSED_STARTUP_TIMEOUT_SECONDS)

    assert len(captured) == 1
    assert isinstance(captured[0], UdsSocketProvenanceError)
    assert captured[0].reason == "chmod_failed"
    assert server.started is True
    assert sightings == []
    _assert_listening_servers_closed(server)
    _assert_single_fail_closed_emission(emissions, "chmod_failed", str(tmp_path))
    _cleanup_uds_file(uds_path)


def test_uds_startup_fails_closed_when_socket_owner_is_foreign(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import UdsSocketProvenanceError, create_server

    uds_path = tmp_path / "foreign-owner.sock"
    settings = _build_uds_settings(tmp_path, "foreign-owner.sock")
    server = create_server(settings=settings)
    emissions = _record_server_emissions(monkeypatch)
    sightings = _record_verification_sightings(monkeypatch)
    real_euid = os.geteuid()
    monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)

    captured = _run_server_and_capture(server, FAIL_CLOSED_STARTUP_TIMEOUT_SECONDS)

    assert len(captured) == 1
    assert isinstance(captured[0], UdsSocketProvenanceError)
    assert captured[0].reason == "foreign_owner"
    assert server.started is True
    assert sightings == [
        {
            "is_socket": True,
            "mode": 0o600,
            "uid": real_euid,
        }
    ]
    _assert_listening_servers_closed(server)
    _assert_single_fail_closed_emission(emissions, "foreign_owner", str(tmp_path))
    _cleanup_uds_file(uds_path)
