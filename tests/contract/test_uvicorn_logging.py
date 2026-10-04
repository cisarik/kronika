"""Contract tests for Uvicorn logging integration."""

from __future__ import annotations

import json
import logging
import logging.config
import socket
from io import StringIO
from typing import Any
from unittest.mock import patch

import pytest
import uvicorn
from pydantic import SecretStr

from kronika.configuration import KronikaSettings
from kronika.server import create_server
from kronika.structured_logging import (
    FrameNestJsonFormatter,
    FrameNestRedactionFilter,
    build_uvicorn_log_config,
)

REPRESENTATIVE_SECRET = "uvicorn-logging-contract-secret"
FORBIDDEN_HANDLER_CLASSES = (
    "FileHandler",
    "RotatingFileHandler",
    "TimedRotatingFileHandler",
    "SocketHandler",
    "DatagramHandler",
    "SysLogHandler",
    "HTTPHandler",
    "SMTPHandler",
)


@pytest.fixture(autouse=True)
def restore_logging() -> Any:
    original_handlers = logging.root.handlers[:]
    original_level = logging.root.level
    original_loggers = logging.root.manager.loggerDict.copy()
    yield
    logging.root.handlers = original_handlers
    logging.root.level = original_level
    logging.root.manager.loggerDict = original_loggers


def _handler_classes(config: dict[str, Any]) -> set[str]:
    classes: set[str] = set()
    for handler in config.get("handlers", {}).values():
        if isinstance(handler, dict):
            class_name = handler.get("class", "")
            classes.add(str(class_name).rsplit(".", maxsplit=1)[-1])
    return classes


def _capture_logger_output(logger_name: str) -> tuple[StringIO, logging.Handler]:
    logging.config.dictConfig(build_uvicorn_log_config())
    stream = StringIO()
    logger = logging.getLogger(logger_name)
    handler = logger.handlers[0]
    handler.stream = stream  # type: ignore[attr-defined]
    return stream, handler


def test_build_uvicorn_log_config_returns_fresh_independent_dict() -> None:
    first = build_uvicorn_log_config()
    second = build_uvicorn_log_config()
    assert first is not second
    second["version"] = 99
    assert first["version"] == 1


def test_config_uses_framenest_formatter_and_redaction_filter() -> None:
    config = build_uvicorn_log_config()
    assert config["formatters"]["framenest_json"]["()"].endswith("FrameNestJsonFormatter")
    assert config["filters"]["framenest_redaction"]["()"].endswith("FrameNestRedactionFilter")


def test_config_has_no_file_rotation_socket_syslog_or_network_handlers() -> None:
    config = build_uvicorn_log_config()
    classes = _handler_classes(config)
    assert classes.isdisjoint(set(FORBIDDEN_HANDLER_CLASSES))


def test_configured_output_uses_stderr() -> None:
    config = build_uvicorn_log_config()
    handler = config["handlers"]["framenest_stderr"]
    assert handler["stream"] == "ext://sys.stderr"


def test_uvicorn_and_error_emit_one_valid_json_line_per_record() -> None:
    for logger_name in ("uvicorn", "uvicorn.error"):
        stream, _ = _capture_logger_output(logger_name)
        logging.getLogger(logger_name).error("server startup")
        lines = [line for line in stream.getvalue().splitlines() if line.strip()]
        assert len(lines) == 1
        json.loads(lines[0])


def test_emitted_uvicorn_records_contain_stable_required_fields() -> None:
    stream, _ = _capture_logger_output("uvicorn.error")
    logging.getLogger("uvicorn.error").error("startup complete")
    payload = json.loads(stream.getvalue().strip())
    for field in (
        "timestamp",
        "level",
        "event",
        "component",
        "operation",
        "error_code",
        "retryable",
    ):
        assert field in payload


def test_representative_secret_absent_from_uvicorn_message() -> None:
    stream, _ = _capture_logger_output("uvicorn.error")
    logging.getLogger("uvicorn.error").error("failed token=%s", REPRESENTATIVE_SECRET)
    assert REPRESENTATIVE_SECRET not in stream.getvalue()


def test_representative_url_and_path_absent_from_foreign_message() -> None:
    stream, _ = _capture_logger_output("uvicorn")
    logging.getLogger("uvicorn").info(
        "GET https://example.invalid/private/video.mkv from /Users/agile/secret"
    )
    line = stream.getvalue()
    assert "example.invalid" not in line
    assert "/Users/agile" not in line
    assert "video.mkv" not in line


def test_uvicorn_access_emits_no_line() -> None:
    logging.config.dictConfig(build_uvicorn_log_config())
    stream = StringIO()
    logger = logging.getLogger("uvicorn.access")
    assert logger.handlers == []
    logger.info('127.0.0.1:12345 - "GET /health HTTP/1.1" 200')
    assert stream.getvalue() == ""


def test_create_server_passes_framenest_log_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = KronikaSettings(host="127.0.0.1", port=8000, _env_file=None)
    server = create_server(settings=settings)
    assert isinstance(server.config.log_config, dict)
    assert server.config.log_config["formatters"]["framenest_json"]["()"].endswith(
        "FrameNestJsonFormatter"
    )


def test_server_config_access_log_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = KronikaSettings(host="127.0.0.1", port=8000, _env_file=None)
    server = create_server(settings=settings)
    assert server.config.access_log is False


def test_existing_runtime_invariants_remain_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.1")
    monkeypatch.setenv("FRAMENEST_PORT", "8000")
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    server = create_server()
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 8000
    assert server.config.reload is False
    assert server.config.workers == 1
    assert server.config.proxy_headers is False
    assert server.config.forwarded_allow_ips == ""


def test_constructing_config_and_server_does_not_bind_listener(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_attempts: list[tuple[Any, ...]] = []
    original_bind = socket.socket.bind

    def tracked_bind(self, address: tuple[Any, ...]) -> None:
        bind_attempts.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", tracked_bind)
    build_uvicorn_log_config()
    create_server(settings=KronikaSettings(host="127.0.0.1", port=8000, _env_file=None))
    assert bind_attempts == []
