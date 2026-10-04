"""Operator CLI for server-local library onboarding and refresh."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import NoReturn

from kronika.adapters.cli.library_root import native_library_path_flavor
from kronika.application.library_scan import (
    FrameNestLibraryScanError,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanUnavailableError,
    default_scan_limits,
)
from kronika.application.library_workflow import (
    LibraryAddPlan,
    LibraryImportSummary,
    LibraryRefreshPlan,
    LibraryWorkflowDeviceNotFoundError,
    LibraryWorkflowDeviceSelectionRequiredError,
    LibraryWorkflowInvalidInputError,
    LibraryWorkflowLibraryNotFoundError,
    LibraryWorkflowLibrarySelectionRequiredError,
    LibraryWorkflowNoLibraryError,
    LibraryWorkflowReservedRootConflictError,
    ServerLibraryWorkflow,
)
from kronika.configuration import (
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.domain import (
    DeviceId,
    FrameNestIdentityError,
    FrameNestLibraryError,
    FrameNestLibraryRootError,
    LibraryId,
    LibraryRoot,
)
from kronika.infrastructure.filesystem.library_scanner import LocalLibraryScanner
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

INVALID_INPUT_MESSAGE = "Invalid library command."
NOT_READY_MESSAGE = "Catalog database is not ready. Run poetry run framenest-db migrate."
DEVICE_SELECTION_REQUIRED_MESSAGE = "Multiple devices are registered; pass --device-id."
DEVICE_NOT_FOUND_MESSAGE = "Device not found."
LIBRARY_SELECTION_REQUIRED_MESSAGE = "Multiple libraries are registered; pass --library-id."
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
NO_LIBRARY_MESSAGE = "No library is registered."
SCAN_UNAVAILABLE_MESSAGE = "Library scan preview is not available."
COMMAND_FAILED_MESSAGE = "Library command failed."


class _UsageError(Exception):
    pass


class _InvalidInputError(Exception):
    pass


class _NotReadyError(Exception):
    pass


class _DeclinedError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError(INVALID_INPUT_MESSAGE)


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch server-operator library operations."""
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        settings = load_settings()
        return _dispatch(args, settings)
    except (_UsageError, _InvalidInputError, LibraryWorkflowInvalidInputError):
        _write_error(INVALID_INPUT_MESSAGE)
        return 2
    except _NotReadyError:
        _write_error(NOT_READY_MESSAGE)
        return 4
    except LibraryWorkflowDeviceSelectionRequiredError:
        _write_error(DEVICE_SELECTION_REQUIRED_MESSAGE)
        return 2
    except LibraryWorkflowDeviceNotFoundError:
        _write_error(DEVICE_NOT_FOUND_MESSAGE)
        return 3
    except LibraryWorkflowLibrarySelectionRequiredError:
        _write_error(LIBRARY_SELECTION_REQUIRED_MESSAGE)
        return 2
    except LibraryWorkflowLibraryNotFoundError:
        _write_error(LIBRARY_NOT_FOUND_MESSAGE)
        return 3
    except LibraryWorkflowNoLibraryError:
        _write_error(NO_LIBRARY_MESSAGE)
        return 3
    except LibraryWorkflowReservedRootConflictError:
        _write_error(INVALID_INPUT_MESSAGE)
        return 2
    except LibraryScanUnavailableError:
        _write_error(SCAN_UNAVAILABLE_MESSAGE)
        return 6
    except _DeclinedError:
        print("No durable changes made.")
        return 0
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(str(exc))
        return exc.exit_status
    except (
        FrameNestPersistenceError,
        LibraryScanFailedError,
        Exception,
    ):
        _write_error(COMMAND_FAILED_MESSAGE)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-library")
    commands = parser.add_subparsers(dest="operation", required=True)

    commands.add_parser("status")

    add = commands.add_parser("add")
    add.add_argument("--root", dest="root_path")
    add.add_argument("--display-name", dest="display_name")
    add.add_argument("--device-id", dest="device_id")
    add.add_argument("--yes", action="store_true", dest="confirmed")
    _add_scan_limit_arguments(add)

    refresh = commands.add_parser("refresh")
    refresh.add_argument("--library-id", dest="library_id")
    refresh.add_argument("--yes", action="store_true", dest="confirmed")
    _add_scan_limit_arguments(refresh)

    return parser


def _add_scan_limit_arguments(parser: argparse.ArgumentParser) -> None:
    defaults = default_scan_limits()
    parser.add_argument("--max-entries", type=int, default=defaults.max_entries, dest="max_entries")
    parser.add_argument(
        "--max-candidates",
        type=int,
        default=defaults.max_candidates,
        dest="max_candidates",
    )


def _dispatch(args: argparse.Namespace, settings: KronikaSettings) -> int:
    if args.operation == "status":
        return _with_workflow(settings, lambda workflow: _status(workflow))
    if args.operation == "add":
        return _with_workflow(settings, lambda workflow: _add(workflow, args))
    if args.operation == "refresh":
        return _with_workflow(settings, lambda workflow: _refresh(workflow, args))
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _status(workflow: ServerLibraryWorkflow) -> int:
    status = workflow.status()
    print(f"Registered devices: {status.device_count}")
    print(f"Registered libraries: {status.library_count}")
    if status.libraries:
        print("Libraries:")
        for library in status.libraries:
            print(f"  {library.id.to_string()}  {library.display_name}")
            print(f"    root: {library.root.path}")
    else:
        print("Libraries: none")
    print(f"Imported physical locations: {status.total_location_count}")
    print(f"Available imported physical locations: {status.available_location_count}")
    return 0


def _add(workflow: ServerLibraryWorkflow, args: argparse.Namespace) -> int:
    root = _parse_absolute_existing_directory(args.root_path or _prompt_required("Library root"))
    display_name = args.display_name
    if display_name is None:
        default_name = _default_display_name(root.path)
        display_name = _prompt_with_default("Display name", default_name)
    _validate_display_name(display_name)
    device_id = _parse_optional_device_id(args.device_id)
    plan = workflow.plan_add(
        root=root,
        display_name=display_name,
        limits=_parse_scan_limits(args.max_entries, args.max_candidates),
        device_id=device_id,
    )
    _print_add_plan(plan)
    if not _confirmed(args.confirmed):
        raise _DeclinedError()
    _print_import_summary(workflow.confirm_add(plan))
    return 0


def _refresh(workflow: ServerLibraryWorkflow, args: argparse.Namespace) -> int:
    plan = workflow.plan_refresh(
        limits=_parse_scan_limits(args.max_entries, args.max_candidates),
        library_id=_parse_optional_library_id(args.library_id),
    )
    _print_refresh_plan(plan)
    if not _confirmed(args.confirmed):
        raise _DeclinedError()
    _print_import_summary(workflow.confirm_refresh(plan))
    return 0


def _parse_absolute_existing_directory(raw_path: str) -> LibraryRoot:
    if not isinstance(raw_path, str) or not raw_path:
        raise _InvalidInputError()
    try:
        expanded = os.path.expanduser(raw_path)
        if not os.path.isabs(expanded):
            raise _InvalidInputError()
        normalized = os.path.normpath(expanded)
        if not os.path.exists(normalized) or not os.path.isdir(normalized):
            raise _InvalidInputError()
        return LibraryRoot(flavor=native_library_path_flavor(), path=normalized)
    except (_InvalidInputError, FrameNestLibraryRootError, OSError, TypeError, ValueError):
        raise _InvalidInputError() from None


def _default_display_name(root_path: str) -> str:
    name = Path(root_path).name.strip()
    return name or "Library"


def _validate_display_name(value: str) -> None:
    try:
        from kronika.domain import Library

        Library(
            id=LibraryId.new(),
            device_id=DeviceId.new(),
            display_name=value,
            root=LibraryRoot(flavor=native_library_path_flavor(), path="/" if os.name != "nt" else "C:\\"),
        )
    except (FrameNestLibraryError, FrameNestLibraryRootError):
        raise _InvalidInputError() from None


def _parse_optional_device_id(value: str | None) -> DeviceId | None:
    if value is None:
        return None
    try:
        return DeviceId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_optional_library_id(value: str | None) -> LibraryId | None:
    if value is None:
        return None
    try:
        return LibraryId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_scan_limits(max_entries: object, max_candidates: object) -> LibraryScanLimits:
    try:
        return LibraryScanLimits(max_entries=max_entries, max_candidates=max_candidates)
    except FrameNestLibraryScanError:
        raise _InvalidInputError() from None


def _confirmed(already_confirmed: bool) -> bool:
    if already_confirmed:
        return True
    answer = input("Import these scan candidates now? Type yes to continue: ")
    return answer == "yes"


def _prompt_required(label: str) -> str:
    value = input(f"{label}: ").strip()
    if not value:
        raise _InvalidInputError()
    return value


def _prompt_with_default(label: str, default: str) -> str:
    value = input(f"{label} [{default}]: ").strip()
    return value or default


def _print_add_plan(plan: LibraryAddPlan) -> None:
    print("Library add preview")
    if plan.existing_root:
        print("Existing registered root detected; the existing library will be refreshed.")
    elif plan.device_to_create is not None:
        print(f"Device to create: {plan.device_to_create.display_name}")
    print(f"Library ID: {plan.library.id.to_string()}")
    print(f"Display name: {plan.library.display_name}")
    print(f"Root: {plan.library.root.path}")
    _print_scan_summary(plan.preview)


def _print_refresh_plan(plan: LibraryRefreshPlan) -> None:
    print("Library refresh preview")
    print(f"Library ID: {plan.library.id.to_string()}")
    print(f"Display name: {plan.library.display_name}")
    print(f"Root: {plan.library.root.path}")
    _print_scan_summary(plan.preview)


def _print_scan_summary(preview: object) -> None:
    summary = preview.summary
    print("Scan summary:")
    print(f"  entries seen: {summary.entries_seen}")
    print(f"  candidate files seen: {summary.candidate_files_seen}")
    print(f"  candidates shown: {len(preview.candidates)}")
    print(f"  candidate bytes seen: {summary.candidate_bytes_seen}")
    print(f"  truncated: {str(summary.truncated).lower()}")
    print(f"  candidates truncated: {str(summary.candidates_truncated).lower()}")


def _print_import_summary(summary: LibraryImportSummary) -> None:
    print("Import complete")
    print(f"Library ID: {summary.library.id.to_string()}")
    print(f"Scanned candidates: {summary.scanned_candidate_count}")
    print(f"New imports: {summary.imported_candidate_count}")
    print(f"Already imported: {summary.existing_candidate_count}")


def _with_workflow(
    settings: KronikaSettings,
    callback: Callable[[ServerLibraryWorkflow], int],
) -> int:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        workflow = ServerLibraryWorkflow(
            SqliteDeviceRepository(engine),
            SqliteLibraryRepository(engine),
            SqliteMediaRepository(engine),
            LocalLibraryScanner(),
            reserved_roots=(
                ()
                if settings.upload_quarantine_root is None
                else (settings.upload_quarantine_root,)
            ),
        )
        return callback(workflow)
    finally:
        dispose_engine(engine)


def _write_error(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
