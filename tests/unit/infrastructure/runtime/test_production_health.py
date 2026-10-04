"""Unit tests for the production check-health runtime command."""

from __future__ import annotations

import http.server
import json
import socketserver
import threading
from pathlib import Path

import pytest

from tests.support.kronika_identity import expected

from framenest.infrastructure.runtime import production


class _HealthHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path != "/health":
            self.send_error(404)
            return
        body = b'{"status":"ok"}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return


def _payload(output: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


@pytest.fixture
def tcp_health_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def uds_health_server(tmp_path: Path):
    socket_path = tmp_path / "framenest.sock"
    server = socketserver.ThreadingUnixStreamServer(
        str(socket_path), _HealthHandler
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield socket_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_check_health_succeeds_over_tcp_loopback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tcp_health_server: int,
) -> None:
    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.1")
    monkeypatch.setenv("FRAMENEST_PORT", str(tcp_health_server))

    assert production.main(["check-health"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert _payload(output.out) == {"operation": "check-health", "state": "ready"}


def test_check_health_succeeds_over_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    uds_health_server: Path,
) -> None:
    monkeypatch.setenv("FRAMENEST_INGRESS_MODE", "tailscale_uds")
    monkeypatch.setenv("FRAMENEST_UDS_PATH", str(uds_health_server))
    monkeypatch.setenv("FRAMENEST_EXTERNAL_ORIGIN", "https://nuc-1.example.ts.net")
    monkeypatch.setenv("FRAMENEST_IDENTITY_MAP", '{"admin@example.com": "admin"}')

    assert production.main(["check-health"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert _payload(output.out) == {"operation": "check-health", "state": "ready"}


def test_check_health_fails_closed_without_listener(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tcp_health_server: int,
) -> None:
    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.1")
    monkeypatch.setenv("FRAMENEST_PORT", str(tcp_health_server + 1))

    assert production.main(["check-health"]) == 5

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["operation"] == "check-health"
    assert payload["state"] == "error"
    assert payload["error_code"] == "FRAMENEST_HEALTH_CHECK_FAILED"
    assert payload["message"] == expected("{brand} health check failed.")
    assert "Traceback" not in output.err


def test_check_health_fails_closed_for_missing_unix_socket(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("FRAMENEST_INGRESS_MODE", "tailscale_uds")
    monkeypatch.setenv("FRAMENEST_UDS_PATH", str(tmp_path / "absent.sock"))
    monkeypatch.setenv("FRAMENEST_EXTERNAL_ORIGIN", "https://nuc-1.example.ts.net")
    monkeypatch.setenv("FRAMENEST_IDENTITY_MAP", '{"admin@example.com": "admin"}')

    assert production.main(["check-health"]) == 5

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["error_code"] == "FRAMENEST_HEALTH_CHECK_FAILED"
    assert "Traceback" not in output.err
    assert str(tmp_path) not in output.err


def test_check_health_rejects_non_ok_payload(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tcp_health_server: int,
) -> None:
    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.1")
    monkeypatch.setenv("FRAMENEST_PORT", str(tcp_health_server))

    class _BrokenHandler(_HealthHandler):
        def do_GET(self) -> None:
            body = b'{"status":"degraded"}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _BrokenHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("FRAMENEST_PORT", str(server.server_address[1]))
        assert production.main(["check-health"]) == 5
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    output = capsys.readouterr()
    assert _payload(output.err)["error_code"] == "FRAMENEST_HEALTH_CHECK_FAILED"
