"""Standard-library CLI for explicit FrameNest database operations."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from typing import NoReturn

from framenest.configuration import (
    FrameNestConfigurationError,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from framenest.infrastructure.persistence.errors import FrameNestPersistenceError
from framenest.infrastructure.persistence.migrations import (
    MigrationStatus,
    inspect_database_migration_status,
    upgrade_database_to_head,
)

COMMAND_ERROR_CODE = "FRAMENEST_DB_COMMAND_FAILED"
CONFIGURATION_ERROR_CODE = "FRAMENEST_DB_CONFIGURATION_FAILED"


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError("Invalid database command.")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch explicit database operations and return a process exit code."""
    operation = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        operation = args.operation
        settings = load_settings()
        if operation == "status":
            status = inspect_database_migration_status(settings)
        elif operation == "migrate":
            status = upgrade_database_to_head(settings)
        else:
            raise _UsageError("Invalid database command.")
        _write_success(operation, status)
        return 0
    except _UsageError:
        _write_error(
            operation=operation,
            error_code=COMMAND_ERROR_CODE,
            message="Database command failed.",
        )
        return 2
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(
            operation=operation,
            error_code=CONFIGURATION_ERROR_CODE,
            message=str(exc),
        )
        return exc.exit_status
    except FrameNestConfigurationError:
        _write_error(
            operation=operation,
            error_code=CONFIGURATION_ERROR_CODE,
            message="Kronika configuration could not be loaded.",
        )
        return 1
    except (FrameNestPersistenceError, Exception):
        _write_error(
            operation=operation,
            error_code=COMMAND_ERROR_CODE,
            message="Database command failed.",
        )
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-db", add_help=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)
    subcommands.add_parser("migrate", help="Upgrade the Kronika database to head.")
    subcommands.add_parser("status", help="Inspect the Kronika database revision.")
    return parser


def _write_success(operation: str, status: MigrationStatus) -> None:
    payload = {
        "operation": operation,
        "state": status.state,
        "current_revision": status.current_revision,
        "head_revision": status.head_revision,
    }
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
