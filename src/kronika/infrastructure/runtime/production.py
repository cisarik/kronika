"""Production runtime checks for deployment supervisors."""

from __future__ import annotations

import argparse
import http.client
import json
import socket
import sys
from collections.abc import Sequence
from typing import NoReturn

from kronika.configuration import (
    INGRESS_MODE_TAILSCALE_UDS,
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status
from kronika.server import run_server

COMMAND_ERROR_CODE = "FRAMENEST_PRODUCTION_COMMAND_FAILED"
DATABASE_NOT_READY_CODE = "FRAMENEST_DATABASE_NOT_READY"
HEALTH_CHECK_FAILED_CODE = "FRAMENEST_HEALTH_CHECK_FAILED"


class _UsageError(Exception):
    pass


class _DatabaseNotReadyError(Exception):
    pass


class _HealthCheckFailedError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError("Invalid production command.")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch production runtime checks and return a process exit code."""
    operation = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        operation = args.operation
        settings = load_settings(env_file=None)
        if operation == "check-database-ready":
            status = inspect_database_migration_status(settings)
            if status.state != "at_head":
                raise _DatabaseNotReadyError()
            _write_success(operation, current_revision=status.current_revision)
        elif operation == "check-health":
            _check_health(settings)
            _write_success(operation, current_revision=None)
        elif operation == "serve":
            run_server(settings=settings)
        else:
            raise _UsageError("Invalid production command.")
        return 0
    except _UsageError:
        _write_error(
            operation=operation,
            error_code=COMMAND_ERROR_CODE,
            message="Production command failed.",
        )
        return 2
    except KeyboardInterrupt:
        return 0
    except _DatabaseNotReadyError:
        _write_error(
            operation=operation,
            error_code=DATABASE_NOT_READY_CODE,
            message="Catalog database is not ready. Run framenest-db migrate first.",
        )
        return 4
    except _HealthCheckFailedError:
        _write_error(
            operation=operation,
            error_code=HEALTH_CHECK_FAILED_CODE,
            message="Kronika health check failed.",
        )
        return 5
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(
            operation=operation,
            error_code=COMMAND_ERROR_CODE,
            message=str(exc),
        )
        return exc.exit_status
    except (FrameNestPersistenceError, Exception):
        _write_error(
            operation=operation,
            error_code=COMMAND_ERROR_CODE,
            message="Production command failed.",
        )
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-production", add_help=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser(
        "check-database-ready",
        help="Verify the configured database is already migrated to head.",
    )
    subcommands.add_parser(
        "check-health",
        help="Verify the Kronika listener answers a local /health request.",
    )
    subcommands.add_parser(
        "serve",
        help="Run the production Kronika server in the foreground.",
    )
    return parser


def _check_health(settings: KronikaSettings) -> None:
    try:
        status_code, payload = _request_health(settings)
    except OSError as exc:
        raise _HealthCheckFailedError() from exc
    if status_code != 200 or payload.get("status") != "ok":
        raise _HealthCheckFailedError()


def _request_health(settings: KronikaSettings) -> tuple[int, dict[str, object]]:
    connection: http.client.HTTPConnection
    if settings.ingress_mode == INGRESS_MODE_TAILSCALE_UDS:
        assert settings.uds_path is not None
        connection = _UnixHTTPConnection(str(settings.uds_path))
    else:
        connection = http.client.HTTPConnection(
            settings.host, settings.port, timeout=5.0
        )
    try:
        connection.request("GET", "/health", headers={"Accept": "application/json"})
        response = connection.getresponse()
        body = response.read()
    finally:
        connection.close()
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        decoded = None
    payload = decoded if isinstance(decoded, dict) else {}
    return response.status, payload


class _UnixHTTPConnection(http.client.HTTPConnection):
    """Minimal HTTP client bound to the permission-restricted Unix socket."""

    def __init__(self, uds_path: str, timeout: float = 5.0) -> None:
        super().__init__("localhost", timeout=timeout)
        self._uds_path = uds_path

    def connect(self) -> None:
        uds_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        uds_socket.settimeout(self.timeout)
        uds_socket.connect(self._uds_path)
        self.sock = uds_socket


def _write_success(operation: str, *, current_revision: str | None) -> None:
    payload = {
        "operation": operation,
        "state": "ready",
    }
    if current_revision is not None:
        payload["current_revision"] = current_revision
    print(json.dumps(payload, separators=(",", ":")), file=sys.stdout)


def _write_error(*, operation: str, error_code: str, message: str) -> None:
    payload = {
        "operation": operation,
        "state": "error",
        "error_code": error_code,
        "message": message,
    }
    print(json.dumps(payload, separators=(",", ":")), file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
