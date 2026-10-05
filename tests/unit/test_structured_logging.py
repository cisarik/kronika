"""Unit tests for the FrameNest structured logging infrastructure."""

from __future__ import annotations

import ast
import json
import logging
import logging.config
import math
import re
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from kronika.configuration import KronikaSettings

REPRESENTATIVE_SECRET = "structured-logging-unit-secret"
FORBIDDEN_LOGGING_PACKAGES = frozenset({"structlog", "pythonjsonlogger"})
ALLOWED_LOGGING_MODULE = Path("src/kronika/structured_logging.py")
SOURCE_ROOT = Path("src/kronika")


class _ReprRaises:
    def __repr__(self) -> str:
        raise AssertionError("repr must not be invoked")

    def __str__(self) -> str:
        raise AssertionError("str must not be invoked")


def _configure_capture() -> StringIO:
    from kronika.structured_logging import build_uvicorn_log_config

    logging.config.dictConfig(build_uvicorn_log_config())
    stream = StringIO()
    logger = logging.getLogger("kronika")
    logger.setLevel(logging.DEBUG)
    handler = logger.handlers[0]
    handler.setLevel(logging.DEBUG)
    handler.stream = stream  # type: ignore[attr-defined]
    return stream


@pytest.fixture(autouse=True)
def restore_logging() -> Any:
    import logging.config

    original = logging.root.manager.loggerDict.copy()
    original_level = logging.root.level
    original_handlers = logging.root.handlers[:]
    yield
    logging.root.handlers = original_handlers
    logging.root.level = original_level
    logging.root.manager.loggerDict = original


def _parse_lines(stream: StringIO) -> list[dict[str, Any]]:
    lines = [line for line in stream.getvalue().splitlines() if line.strip()]
    return [json.loads(line) for line in lines]


def test_required_fields_are_emitted() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="health_checked",
        operation="verify",
        error_code="OK",
        retryable=False,
    )
    payload = _parse_lines(stream)[0]
    assert set(payload) >= {
        "timestamp",
        "level",
        "event",
        "component",
        "operation",
        "error_code",
        "retryable",
    }


def test_key_ordering_is_deterministic() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="ordered_event",
        operation="verify",
        context={"z": 1, "a": 2},
    )
    raw_line = stream.getvalue().strip()
    keys = list(json.loads(raw_line).keys())
    assert keys[:7] == [
        "timestamp",
        "level",
        "event",
        "component",
        "operation",
        "error_code",
        "retryable",
    ]


def test_timestamp_is_utc_with_millisecond_precision_and_z_suffix() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(level="INFO", event="time_event", operation="verify")
    timestamp = _parse_lines(stream)[0]["timestamp"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp)


def test_all_supported_levels_normalize_correctly() -> None:
    from kronika.structured_logging import get_logger

    for level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        stream = _configure_capture()
        get_logger("test").emit(level=level, event="level_event", operation="verify")
        assert _parse_lines(stream)[0]["level"] == level


def test_error_code_and_retryable_preserve_values_and_null() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="nullable_fields",
        operation="verify",
        error_code=None,
        retryable=None,
    )
    payload = _parse_lines(stream)[0]
    assert payload["error_code"] is None
    assert payload["retryable"] is None


def test_exception_metadata_is_sanitized_without_raw_message_or_traceback() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    secret_message = "raw-exception-secret-message"
    get_logger("test").emit(
        level="ERROR",
        event="failed",
        operation="verify",
        exception=RuntimeError(secret_message),
    )
    line = stream.getvalue()
    payload = _parse_lines(stream)[0]
    assert secret_message not in line
    assert "traceback" not in line.lower()
    assert payload["exception"] == {"type": "RuntimeError"}


def test_secret_str_redacted_at_top_level_and_nested_levels() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="secret_event",
        operation="verify",
        context={"outer": {"api_key": SecretStr(REPRESENTATIVE_SECRET)}},
    )
    line = stream.getvalue()
    payload = _parse_lines(stream)[0]
    assert REPRESENTATIVE_SECRET not in line
    assert payload["context"]["outer"]["api_key"] == "[REDACTED]"


@pytest.mark.parametrize(
    "key",
    [
        "secret",
        "API_KEY",
        "access-token",
        "Set Cookie",
        "media_filename",
        "query_string",
    ],
)
def test_sensitive_keys_redacted_case_insensitively(key: str) -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="key_redaction",
        operation="verify",
        context={key: REPRESENTATIVE_SECRET},
    )
    payload = _parse_lines(stream)[0]
    assert REPRESENTATIVE_SECRET not in stream.getvalue()
    assert payload["context"][key] == "[REDACTED]"


def test_nested_mappings_and_sequences_are_sanitized() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="nested",
        operation="verify",
        context={"items": [{"token": "nested-secret"}], "count": 2},
    )
    payload = _parse_lines(stream)[0]
    assert payload["context"]["items"][0]["token"] == "[REDACTED]"


def test_set_output_is_deterministic() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="set_event",
        operation="verify",
        context={"tags": {"b", "a"}},
    )
    payload = _parse_lines(stream)[0]
    assert payload["context"]["tags"] == ["a", "b"]


def test_cycles_and_excessive_depth_terminate_safely() -> None:
    from kronika.structured_logging import get_logger

    cyclic: dict[str, Any] = {"name": "root"}
    cyclic["self"] = cyclic
    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="cycle_event",
        operation="verify",
        context=cyclic,
    )
    line = stream.getvalue()
    assert json.loads(line)
    assert "[REDACTED]" in line or "__cycle__" in line or "__max_depth__" in line


def test_non_finite_floats_do_not_produce_invalid_json() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="float_event",
        operation="verify",
        context={"value": math.nan},
    )
    payload = _parse_lines(stream)[0]
    assert payload["context"]["value"] is None or payload["context"]["value"] == "non_finite"


def test_arbitrary_object_repr_and_str_are_never_invoked() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="object_event",
        operation="verify",
        context={"obj": _ReprRaises()},
    )
    payload = _parse_lines(stream)[0]
    assert "__type__" in str(payload["context"]["obj"])


def test_complete_framenest_settings_objects_are_not_traversed_or_exposed() -> None:
    from kronika.structured_logging import get_logger

    settings = KronikaSettings(
        host="127.0.0.1",
        api_key=SecretStr(REPRESENTATIVE_SECRET),
        _env_file=None,
    )
    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="settings_event",
        operation="verify",
        context={"settings": settings},
    )
    line = stream.getvalue()
    assert REPRESENTATIVE_SECRET not in line
    assert "127.0.0.1" not in line
    assert _parse_lines(stream)[0]["context"]["settings"] == "[REDACTED]"


def test_bearer_tokens_and_secret_assignments_in_strings_are_redacted() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="string_redaction",
        operation="verify",
        context={
            "note": "Bearer abcdefghijklmnop",
            "pair": "api_key=super-secret-value",
        },
    )
    line = stream.getvalue()
    assert "abcdefghijklmnop" not in line
    assert "super-secret-value" not in line


def test_urls_absolute_paths_and_media_filenames_are_redacted() -> None:
    from kronika.structured_logging import get_logger

    stream = _configure_capture()
    get_logger("test").emit(
        level="INFO",
        event="path_redaction",
        operation="verify",
        context={
            "url": "https://example.invalid/movie.mkv",
            "path": "/Users/agile/private/video.mp4",
            "file": "C:\\media\\clip.webm",
        },
    )
    line = stream.getvalue()
    assert "example.invalid" not in line
    assert "/Users/agile" not in line
    assert "video.mp4" not in line
    assert "clip.webm" not in line


def test_malformed_context_does_not_crash_formatting() -> None:
    from kronika.structured_logging import FrameNestJsonFormatter, FrameNestRedactionFilter

    record = logging.LogRecord(
        name="framenest.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="",
        args=(),
        exc_info=None,
    )
    record.__dict__["framenest_structured_payload"] = {
        "event": "bad_context",
        "component": "test",
        "operation": "verify",
        "error_code": None,
        "retryable": None,
        "context": {"broken": _ReprRaises()},
    }
    FrameNestRedactionFilter().filter(record)
    output = FrameNestJsonFormatter().format(record)
    json.loads(output)


def test_foreign_stdlib_records_receive_external_log_schema() -> None:
    from kronika.structured_logging import FrameNestJsonFormatter, FrameNestRedactionFilter

    stream = _configure_capture()
    logging.getLogger("foreign.module").info("foreign message")
    payload = _parse_lines(stream)[0]
    assert payload["event"] == "external_log"
    assert payload["operation"] == "emit"
    assert payload["error_code"] is None
    assert payload["retryable"] is None


def test_raw_foreign_record_args_do_not_appear_in_output() -> None:
    from kronika.structured_logging import FrameNestJsonFormatter, FrameNestRedactionFilter

    stream = _configure_capture()
    logging.getLogger("foreign.module").info("hello %s", REPRESENTATIVE_SECRET)
    line = stream.getvalue()
    assert REPRESENTATIVE_SECRET not in line
    assert "%s" not in line


def test_formatter_failure_emits_safe_fallback_without_rejected_data() -> None:
    from kronika.structured_logging import FrameNestJsonFormatter, FrameNestRedactionFilter

    record = logging.LogRecord(
        name="framenest.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="must-not-appear",
        args=(),
        exc_info=None,
    )
    record.__dict__["framenest_sanitized_payload"] = {"broken": object()}
    formatter = FrameNestJsonFormatter()
    with patch.object(formatter, "_encode_json", side_effect=RuntimeError("boom")):
        output = formatter.format(record)
    payload = json.loads(output)
    assert payload["event"] == "logging_format_failure"
    assert payload["error_code"] == "LOG_FORMAT_FAILURE"
    assert "must-not-appear" not in output


def test_invalid_facade_fields_raise_sanitized_errors() -> None:
    from kronika.structured_logging import get_logger

    logger = get_logger("test")
    with pytest.raises(ValueError) as exc_info:
        logger.emit(level="INFO", event="   ", operation="verify")
    assert "   " not in str(exc_info.value)


def test_no_third_party_structured_logging_module_imported() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    for path in (repository_root / SOURCE_ROOT).rglob("*.py"):
        if path.name == "structured_logging.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                root = node.names[0].name.split(".")[0]
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".")[0]
            else:
                continue
            assert root not in FORBIDDEN_LOGGING_PACKAGES


def test_production_logging_imports_confined_to_structured_logging_module() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        relative = path.relative_to(repository_root)
        if relative == ALLOWED_LOGGING_MODULE:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imports_logging = any(
            isinstance(node, ast.Import) and node.names[0].name.split(".")[0] == "logging"
            or isinstance(node, ast.ImportFrom)
            and node.module is not None
            and node.module.split(".")[0] == "logging"
            for node in ast.walk(tree)
        )
        if imports_logging:
            violations.append(str(relative))
    assert violations == []
