"""Operator CLI for persistent gallery preview derivatives."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from kronika.application.gallery_preview import (
    GalleryPreviewFailedError,
    GalleryPreviewGenerationPlan,
    GalleryPreviewNotFoundError,
    GalleryPreviewService,
    GalleryPreviewUnavailableError,
)
from kronika.configuration import (
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.domain import FrameNestIdentityError, LibraryId
from kronika.infrastructure.filesystem.media_content import LocalMediaContentReader
from kronika.infrastructure.media_analysis import LocalMediaAnalysisAdapter
from kronika.infrastructure.media_analysis.gallery_preview import (
    FilesystemGalleryPreviewCache,
    PillowGalleryPreviewEncoder,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

INVALID_INPUT_MESSAGE = "Invalid previews command."
NOT_READY_MESSAGE = "Catalog database is not ready. Run poetry run framenest-db migrate."
NOT_FOUND_MESSAGE = "Library or media was not found."
UNAVAILABLE_MESSAGE = "Gallery preview operation is not available."
COMMAND_FAILED_MESSAGE = "Gallery preview command failed."


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
    """Dispatch persistent gallery preview operations."""
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        settings = load_settings()
        return _dispatch(args, settings)
    except (_UsageError, _InvalidInputError):
        _write_error(INVALID_INPUT_MESSAGE)
        return 2
    except _NotReadyError:
        _write_error(NOT_READY_MESSAGE)
        return 4
    except GalleryPreviewNotFoundError:
        _write_error(NOT_FOUND_MESSAGE)
        return 3
    except GalleryPreviewUnavailableError:
        _write_error(UNAVAILABLE_MESSAGE)
        return 6
    except _DeclinedError:
        print("No durable changes made.")
        return 0
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(str(exc))
        return exc.exit_status
    except (FrameNestPersistenceError, GalleryPreviewFailedError, Exception):
        _write_error(COMMAND_FAILED_MESSAGE)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-previews")
    commands = parser.add_subparsers(dest="operation", required=True)

    status = commands.add_parser("status")
    status.add_argument("--library-id", dest="library_id")

    generate = commands.add_parser("generate")
    selection = generate.add_mutually_exclusive_group(required=True)
    selection.add_argument("--library-id", dest="library_id")
    selection.add_argument("--all", action="store_true", dest="include_all")
    generate.add_argument("--yes", action="store_true", dest="confirmed")
    generate.add_argument("--max-items", type=int, default=100, dest="max_items")

    return parser


def _dispatch(args: argparse.Namespace, settings: KronikaSettings) -> int:
    if args.operation == "status":
        return _with_service(settings, lambda service: _status(service, args, settings))
    if args.operation == "generate":
        return _with_service(settings, lambda service: _generate(service, args, settings))
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _status(
    service: GalleryPreviewService,
    args: argparse.Namespace,
    settings: KronikaSettings,
) -> int:
    status = service.status(library_id=_parse_optional_library_id(args.library_id))
    print("Persistent gallery preview status")
    print(f"Cache root: {settings.gallery_preview_cache_path}")
    print(f"Imported physical locations considered: {status.total_count}")
    _print_counts(
        ready=status.ready_count,
        missing=status.missing_count,
        stale=status.stale_count,
        unavailable=status.unavailable_count,
        unsupported=status.unsupported_count,
        generation_unavailable=status.generation_unavailable_count,
    )
    print("Libraries:")
    for library in status.libraries:
        print(f"  {library.library_id.to_string()}  {library.display_name}")
        print(f"    considered: {library.total_count}")
        _print_counts(
            ready=library.ready_count,
            missing=library.missing_count,
            stale=library.stale_count,
            unavailable=library.unavailable_count,
            unsupported=library.unsupported_count,
            generation_unavailable=library.generation_unavailable_count,
            indent="    ",
        )
    return 0


def _generate(
    service: GalleryPreviewService,
    args: argparse.Namespace,
    settings: KronikaSettings,
) -> int:
    library_id = _parse_optional_library_id(args.library_id)
    max_items = _parse_positive_int(args.max_items)
    plan = service.plan_generate(
        library_id=library_id,
        include_all=bool(args.include_all),
        max_items=max_items,
    )
    _print_plan(plan, settings)
    if not _confirmed(args.confirmed):
        raise _DeclinedError()
    summary = service.generate(plan)
    print("Generation complete")
    print(f"Considered: {summary.considered_count}")
    print(f"Already ready: {summary.ready_count}")
    print(f"Generated: {summary.generated_count}")
    print(f"Failed: {summary.failed_count}")
    print(f"Skipped: {summary.skipped_count}")
    return 0 if summary.failed_count == 0 else 1


def _print_counts(
    *,
    ready: int,
    missing: int,
    stale: int,
    unavailable: int,
    unsupported: int,
    generation_unavailable: int,
    indent: str = "",
) -> None:
    print(f"{indent}ready: {ready}")
    print(f"{indent}missing: {missing}")
    print(f"{indent}stale: {stale}")
    print(f"{indent}unavailable: {unavailable}")
    print(f"{indent}unsupported: {unsupported}")
    print(f"{indent}generation-unavailable: {generation_unavailable}")


def _print_plan(plan: GalleryPreviewGenerationPlan, settings: KronikaSettings) -> None:
    print("Persistent gallery preview generation plan")
    print(f"Cache root: {settings.gallery_preview_cache_path}")
    print(f"Selected libraries: {len(plan.selected_library_ids)}")
    for library_id in plan.selected_library_ids:
        print(f"  {library_id.to_string()}")
    print(f"Imported physical locations considered: {plan.total_considered}")
    print(f"Already ready: {plan.ready_count}")
    print(f"To generate: {len(plan.to_generate)}")
    print(f"Max items: {plan.max_items}")


def _parse_optional_library_id(value: str | None) -> LibraryId | None:
    if value is None:
        return None
    try:
        return LibraryId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _InvalidInputError()
    return value


def _confirmed(already_confirmed: bool) -> bool:
    if already_confirmed:
        return True
    answer = input("Generate these gallery preview derivatives now? Type yes to continue: ")
    return answer == "yes"


def _with_service(
    settings: KronikaSettings,
    callback: Callable[[GalleryPreviewService], int],
) -> int:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        service = GalleryPreviewService(
            SqliteMediaRepository(engine),
            SqliteLibraryRepository(engine),
            LocalMediaContentReader(),
            LocalMediaAnalysisAdapter(),
            PillowGalleryPreviewEncoder(),
            FilesystemGalleryPreviewCache(settings.gallery_preview_cache_path),
        )
        return callback(service)
    finally:
        dispose_engine(engine)


def _write_error(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
