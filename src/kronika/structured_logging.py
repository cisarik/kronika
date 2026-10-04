"""FrameNest structured logging infrastructure."""

from __future__ import annotations

import datetime
import json
import logging
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import SecretStr

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

REDACTED = "[REDACTED]"
MAX_SANITIZE_DEPTH = 8
STRUCTURED_PAYLOAD_ATTR = "kronika_structured_payload"
SANITIZED_PAYLOAD_ATTR = "kronika_sanitized_payload"

_LEVEL_TO_NAME: dict[int, LogLevel] = {
    logging.DEBUG: "DEBUG",
    logging.INFO: "INFO",
    logging.WARNING: "WARNING",
    logging.ERROR: "ERROR",
    logging.CRITICAL: "CRITICAL",
}

_LEVEL_FROM_NAME: dict[LogLevel, int] = {
    "DEBUG": logging.DEBUG,
    "INFO": logging.INFO,
    "WARNING": logging.WARNING,
    "ERROR": logging.ERROR,
    "CRITICAL": logging.CRITICAL,
}

_OUTPUT_KEY_ORDER = (
    "timestamp",
    "level",
    "event",
    "component",
    "operation",
    "error_code",
    "retryable",
    "exception",
    "context",
)

_SENSITIVE_NORMALIZED_KEYS = frozenset(
    {
        "secret",
        "apikey",
        "token",
        "accesstoken",
        "refreshtoken",
        "password",
        "passwd",
        "authorization",
        "cookie",
        "setcookie",
        "credential",
        "credentials",
        "privatekey",
        "headers",
        "request",
        "path",
        "file",
        "filename",
        "mediafilename",
        "url",
        "query",
        "querystring",
    }
)

_BEARER_PATTERN = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(api_key|token|password|passwd|secret)\s*=\s*\S+",
)
_URL_PATTERN = re.compile(r"https?://\S+")
_POSIX_PATH_PATTERN = re.compile(r"(?<![A-Za-z0-9_])/(?:[^\s\"']+)")
_WINDOWS_PATH_PATTERN = re.compile(r"[A-Za-z]:\\(?:[^\s\"']+)")
_MEDIA_FILENAME_PATTERN = re.compile(
    r"[^\s\"']+\.(?:mkv|mp4|webm|mov|gif|jpg|jpeg|png)\b",
    re.IGNORECASE,
)


def get_logger(component: str) -> FrameNestLogger:
    return FrameNestLogger(component)


def build_uvicorn_log_config() -> dict[str, object]:
    formatter_path = "kronika.structured_logging.FrameNestJsonFormatter"
    filter_path = "kronika.structured_logging.FrameNestRedactionFilter"
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "filters": {
            "kronika_redaction": {
                "()": filter_path,
            },
        },
        "formatters": {
            "kronika_json": {
                "()": formatter_path,
            },
        },
        "handlers": {
            "kronika_stderr": {
                "class": "logging.StreamHandler",
                "formatter": "kronika_json",
                "filters": ["kronika_redaction"],
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "": {
                "handlers": ["kronika_stderr"],
                "level": "INFO",
            },
            "uvicorn": {
                "handlers": ["kronika_stderr"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["kronika_stderr"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": [],
                "level": "INFO",
                "propagate": False,
            },
            "kronika": {
                "handlers": ["kronika_stderr"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


class FrameNestLogger:
    """FrameNest-owned structured logging facade."""

    def __init__(self, component: str) -> None:
        self._component = _validate_machine_name(component, "component")
        self._logger = logging.getLogger(f"kronika.{self._component}")

    def emit(
        self,
        *,
        level: LogLevel,
        event: str,
        operation: str,
        error_code: str | None = None,
        retryable: bool | None = None,
        exception: BaseException | None = None,
        context: Mapping[str, object] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "event": _validate_machine_name(event, "event"),
            "component": self._component,
            "operation": _validate_machine_name(operation, "operation"),
            "error_code": error_code,
            "retryable": retryable,
        }
        if exception is not None:
            payload["exception_input"] = exception
        if context is not None:
            payload["context"] = dict(context)
        self._logger.log(
            _LEVEL_FROM_NAME[level],
            "",
            extra={STRUCTURED_PAYLOAD_ATTR: payload},
        )


class FrameNestRedactionFilter(logging.Filter):
    """Prepare sanitized structured payloads and neutralize unsafe record fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            raw_payload = record.__dict__.get(STRUCTURED_PAYLOAD_ATTR)
            if isinstance(raw_payload, dict):
                sanitized = _build_structured_payload(raw_payload, record)
            else:
                sanitized = _build_foreign_payload(record)
            record.__dict__[SANITIZED_PAYLOAD_ATTR] = sanitized
        except Exception:
            record.__dict__[SANITIZED_PAYLOAD_ATTR] = _fallback_payload(record)
        record.msg = ""
        record.args = ()
        record.exc_info = None
        record.exc_text = None
        record.stack_info = None
        return True


class FrameNestJsonFormatter(logging.Formatter):
    """Render sanitized structured payloads as compact JSON lines."""

    def format(self, record: logging.LogRecord) -> str:
        try:
            sanitized = record.__dict__.get(SANITIZED_PAYLOAD_ATTR)
            if not isinstance(sanitized, dict):
                sanitized = _build_foreign_payload(record)
            output = _compose_output(sanitized, record)
            return self._encode_json(output)
        except Exception:
            ordered = _order_output(_fallback_payload(record))
            return json.dumps(
                ordered,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )

    def _encode_json(self, payload: dict[str, Any]) -> str:
        ordered = _order_output(payload)
        return json.dumps(ordered, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _validate_machine_name(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Invalid {field_name}")
    return value.strip()


def _normalize_key(key: str) -> str:
    return re.sub(r"[\s_\-]+", "", key.lower())


def _is_sensitive_key(key: str) -> bool:
    return _normalize_key(key) in _SENSITIVE_NORMALIZED_KEYS


def _is_kronika_settings(value: object) -> bool:
    cls = type(value)
    return cls.__name__ == "KronikaSettings" and cls.__module__ == "kronika.configuration"


def _sanitize_string(value: str) -> str:
    sanitized = value
    sanitized = _BEARER_PATTERN.sub(REDACTED, sanitized)
    sanitized = _ASSIGNMENT_PATTERN.sub(REDACTED, sanitized)
    sanitized = _URL_PATTERN.sub(REDACTED, sanitized)
    sanitized = _POSIX_PATH_PATTERN.sub(REDACTED, sanitized)
    sanitized = _WINDOWS_PATH_PATTERN.sub(REDACTED, sanitized)
    sanitized = _MEDIA_FILENAME_PATTERN.sub(REDACTED, sanitized)
    return sanitized


def _sanitize_exception(value: BaseException) -> dict[str, str]:
    return {"type": type(value).__name__}


def _sanitize_value(
    value: object,
    *,
    depth: int = 0,
    seen: set[int] | None = None,
    key: str | None = None,
) -> object:
    if key is not None and _is_sensitive_key(key):
        return REDACTED
    if isinstance(value, SecretStr):
        return REDACTED
    if _is_kronika_settings(value):
        return REDACTED
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, BaseException):
        return _sanitize_exception(value)
    if depth >= MAX_SANITIZE_DEPTH:
        return "__max_depth__"
    if isinstance(value, Mapping):
        object_id = id(value)
        if seen is None:
            seen = set()
        if object_id in seen:
            return "__cycle__"
        seen.add(object_id)
        return {
            str(item_key): _sanitize_value(
                item_value,
                depth=depth + 1,
                seen=seen,
                key=str(item_key),
            )
            for item_key, item_value in value.items()
        }
    if isinstance(value, (list, tuple)):
        object_id = id(value)
        if seen is None:
            seen = set()
        if object_id in seen:
            return "__cycle__"
        seen.add(object_id)
        return [
            _sanitize_value(item, depth=depth + 1, seen=seen)
            for item in value
        ]
    if isinstance(value, (set, frozenset)):
        object_id = id(value)
        if seen is None:
            seen = set()
        if object_id in seen:
            return "__cycle__"
        seen.add(object_id)
        sanitized_items = [
            _sanitize_value(item, depth=depth + 1, seen=seen) for item in value
        ]
        return sorted(
            sanitized_items,
            key=lambda item: json.dumps(item, sort_keys=True, default=_json_fallback),
        )
    return {"__type__": type(value).__name__}


def _json_fallback(value: object) -> str:
    return type(value).__name__


def _normalize_level(record: logging.LogRecord) -> LogLevel:
    return _LEVEL_TO_NAME.get(record.levelno, "INFO")


def _format_timestamp(created: float) -> str:
    instant = datetime.datetime.fromtimestamp(created, tz=datetime.timezone.utc)
    milliseconds = instant.microsecond // 1000
    return instant.strftime("%Y-%m-%dT%H:%M:%S.") + f"{milliseconds:03d}Z"


def _build_structured_payload(
    payload: Mapping[str, Any],
    record: logging.LogRecord,
) -> dict[str, Any]:
    sanitized: dict[str, Any] = {
        "level": _normalize_level(record),
        "event": _sanitize_string(str(payload["event"])),
        "component": _sanitize_string(str(payload["component"])),
        "operation": _sanitize_string(str(payload["operation"])),
        "error_code": payload.get("error_code"),
        "retryable": payload.get("retryable"),
    }
    exception_input = payload.get("exception_input")
    if isinstance(exception_input, BaseException):
        sanitized["exception"] = _sanitize_exception(exception_input)
    context = payload.get("context")
    if context is not None:
        sanitized_context = _sanitize_value(context)
        if isinstance(sanitized_context, dict) and sanitized_context:
            sanitized["context"] = sanitized_context
    return sanitized


def _build_foreign_payload(record: logging.LogRecord) -> dict[str, Any]:
    raw_message = record.msg if isinstance(record.msg, str) else ""
    if record.args:
        sanitized_message = ""
    else:
        sanitized_message = _sanitize_string(raw_message) if raw_message else ""
    payload: dict[str, Any] = {
        "level": _normalize_level(record),
        "event": "external_log",
        "component": _safe_component_from_logger_name(record.name),
        "operation": "emit",
        "error_code": None,
        "retryable": None,
    }
    if sanitized_message:
        payload["context"] = {"message": sanitized_message}
    if record.exc_info:
        exc_type = record.exc_info[0]
        if exc_type is not None:
            payload["exception"] = {"type": exc_type.__name__}
    return payload


def _safe_component_from_logger_name(logger_name: str) -> str:
    if not logger_name:
        return "logging"
    if logger_name.startswith("uvicorn"):
        return "uvicorn"
    if logger_name.startswith("kronika"):
        return logger_name.removeprefix("kronika.").split(".", maxsplit=1)[0] or "kronika"
    return logger_name.split(".", maxsplit=1)[0]


def _compose_output(sanitized: Mapping[str, Any], record: logging.LogRecord) -> dict[str, Any]:
    output: dict[str, Any] = {
        "timestamp": _format_timestamp(record.created),
        "level": sanitized.get("level", _normalize_level(record)),
        "event": sanitized["event"],
        "component": sanitized["component"],
        "operation": sanitized["operation"],
        "error_code": sanitized.get("error_code"),
        "retryable": sanitized.get("retryable"),
    }
    if "exception" in sanitized:
        output["exception"] = sanitized["exception"]
    if sanitized.get("context"):
        output["context"] = sanitized["context"]
    return output


def _order_output(payload: Mapping[str, Any]) -> dict[str, Any]:
    ordered: dict[str, Any] = {}
    for key in _OUTPUT_KEY_ORDER:
        if key in payload:
            ordered[key] = payload[key]
    return ordered


def _fallback_payload(record: logging.LogRecord) -> dict[str, Any]:
    return {
        "timestamp": _format_timestamp(record.created),
        "level": "ERROR",
        "event": "logging_format_failure",
        "component": "logging",
        "operation": "format",
        "error_code": "LOG_FORMAT_FAILURE",
        "retryable": False,
    }
