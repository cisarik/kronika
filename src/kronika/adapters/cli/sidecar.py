"""Thin operator CLI for portable media sidecar export, validate, and compare."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Sequence
from typing import Any, NoReturn

from kronika.application.media_sidecar import (
    FrameNestMediaSidecarApplicationError,
    MediaSidecarService,
    SidecarCompareResult,
    SidecarExportResult,
)
from kronika.configuration import IdentityEnvironmentConfigurationError, load_settings
from kronika.domain.identities import FrameNestIdentityError, MediaId, MediaLocationId
from kronika.infrastructure.filesystem.media_sidecar import FilesystemMediaSidecarStore
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_metadata_repository import SqliteMediaMetadataRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

INVALID_INPUT_CODE = "SIDECAR_INVALID_INPUT"
INVALID_INPUT_MESSAGE = "Invalid sidecar command."
CATALOG_NOT_READY_CODE = "SIDECAR_CATALOG_NOT_READY"
CATALOG_NOT_READY_MESSAGE = "Catalog database is not ready."
COMMAND_FAILED_CODE = "SIDECAR_COMMAND_FAILED"
COMMAND_FAILED_MESSAGE = "Media sidecar command failed."
UNAVAILABLE_CODE = "SIDECAR_UNAVAILABLE"
UNAVAILABLE_MESSAGE = "Media sidecar is not available."
VALIDATE_RESULT_CODE = "SIDECAR_VALIDATE_VALID"
_OPERATIONS = frozenset({"export", "validate", "compare"})
_EXPORT_CODES = {
    "created": "SIDECAR_EXPORT_CREATED",
    "replaced": "SIDECAR_EXPORT_REPLACED",
    "unchanged": "SIDECAR_EXPORT_UNCHANGED",
}


class _UsageError(Exception):
    pass


class _CatalogNotReadyError(Exception):
    pass


class _UnavailableError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        del message
        raise _UsageError()


class _UnusedCatalogBound:
    """Stand-in catalog dependency that validate must never invoke."""

    def __getattr__(self, name: str) -> Any:
        del name
        raise RuntimeError("catalog access is not available for validate")


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch sidecar operator commands and return a process exit code."""
    operation = _peek_operation(argv)
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        operation = str(args.operation)
        if operation == "validate":
            _run_validate(args.path)
            return 0
        if operation == "export":
            media_id = _parse_media_id(args.media_id)
            location_id = _parse_location_id(args.location_id)
            result = _with_catalog_service(lambda service: service.export(media_id, location_id))
            _write_export(result)
            return 0
        if operation == "compare":
            media_id = _parse_media_id(args.media_id)
            location_id = _parse_location_id(args.location_id)
            result = _with_catalog_service(lambda service: service.compare(media_id, location_id))
            _write_compare(result)
            return 0
        raise _UsageError()
    except _UsageError:
        _write_error(operation=operation, error_code=INVALID_INPUT_CODE, message=INVALID_INPUT_MESSAGE)
        return 1
    except FrameNestIdentityError:
        _write_error(operation=operation, error_code=INVALID_INPUT_CODE, message=INVALID_INPUT_MESSAGE)
        return 1
    except _CatalogNotReadyError:
        _write_error(operation=operation, error_code=CATALOG_NOT_READY_CODE, message=CATALOG_NOT_READY_MESSAGE)
        return 1
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(operation=operation, error_code=UNAVAILABLE_CODE, message=str(exc))
        return exc.exit_status
    except _UnavailableError:
        _write_error(operation=operation, error_code=UNAVAILABLE_CODE, message=UNAVAILABLE_MESSAGE)
        return 1
    except FrameNestMediaSidecarApplicationError as exc:
        _write_error(operation=operation, error_code=exc.error_code, message=str(exc))
        return 1
    except Exception:
        _write_error(operation=operation, error_code=COMMAND_FAILED_CODE, message=COMMAND_FAILED_MESSAGE)
        return 1


def _peek_operation(argv: Sequence[str] | None) -> str:
    tokens = list(sys.argv[1:] if argv is None else argv)
    for token in tokens:
        if token in _OPERATIONS:
            return token
        if token.startswith("-"):
            continue
        return "unknown"
    return "unknown"


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-sidecar", add_help=True)
    subcommands = parser.add_subparsers(dest="operation", required=True)

    export_parser = subcommands.add_parser("export", help="Export one catalog location to an adjacent sidecar.")
    export_parser.add_argument("--media-id", required=True, help="Canonical media UUID.")
    export_parser.add_argument("--location-id", required=True, help="Canonical location UUID.")

    validate_parser = subcommands.add_parser("validate", help="Validate one sidecar file without catalog access.")
    validate_parser.add_argument("--path", required=True, help="Sidecar filesystem path.")

    compare_parser = subcommands.add_parser("compare", help="Compare one catalog location with its adjacent sidecar.")
    compare_parser.add_argument("--media-id", required=True, help="Canonical media UUID.")
    compare_parser.add_argument("--location-id", required=True, help="Canonical location UUID.")
    return parser


def _parse_media_id(value: str) -> MediaId:
    try:
        return MediaId.from_string(value)
    except FrameNestIdentityError:
        raise _UsageError() from None


def _parse_location_id(value: str) -> MediaLocationId:
    try:
        return MediaLocationId.from_string(value)
    except FrameNestIdentityError:
        raise _UsageError() from None


def _run_validate(path: str) -> None:
    service = MediaSidecarService(
        _UnusedCatalogBound(),
        _UnusedCatalogBound(),
        _UnusedCatalogBound(),
        FilesystemMediaSidecarStore(),
    )
    service.validate_path(path)
    _write_success(
        {
            "operation": "validate",
            "result": "valid",
            "result_code": VALIDATE_RESULT_CODE,
        }
    )


def _with_catalog_service(callback: Callable[[MediaSidecarService], Any]) -> Any:
    try:
        settings = load_settings()
        status = inspect_database_migration_status(settings)
    except IdentityEnvironmentConfigurationError:
        raise
    except Exception:
        raise _UnavailableError() from None
    if status.state != "at_head":
        raise _CatalogNotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        service = MediaSidecarService(
            SqliteMediaRepository(engine),
            SqliteLibraryRepository(engine),
            SqliteMediaMetadataRepository(engine),
            FilesystemMediaSidecarStore(),
        )
        return callback(service)
    finally:
        dispose_engine(engine)


def _write_export(result: SidecarExportResult) -> None:
    _write_success(
        {
            "operation": "export",
            "result": result.status,
            "result_code": _EXPORT_CODES[result.status],
        }
    )


def _write_compare(result: SidecarCompareResult) -> None:
    _write_success(
        {
            "operation": "compare",
            "result": result.status,
            "result_code": result.error_code,
        }
    )


def _write_success(payload: dict[str, Any]) -> None:
    _write_json(payload, sys.stdout)


def _write_error(*, operation: str, error_code: str, message: str) -> None:
    _write_json(
        {
            "operation": operation,
            "error_code": error_code,
            "message": message,
        },
        sys.stderr,
    )


def _write_json(payload: dict[str, Any], stream: Any) -> None:
    stream.write(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
