"""Operator CLI for durable accepted-cover artifacts and cover thumbnails."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from typing import NoReturn

from kronika.application.media_cover import (
    CoverFailedError,
    CoverService,
    CoverSourceChangedError,
    CoverSourceUnavailableError,
    CoverTimestampInvalidError,
)
from kronika.application.ports.cover_storage import (
    CoverStorageError,
    CoverThumbnailUnavailableError,
)
from kronika.configuration import (
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.domain import FrameNestIdentityError, LibraryId
from kronika.infrastructure.filesystem.cover_storage import (
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
    PillowCoverEncoder,
)
from kronika.infrastructure.media_analysis.cover_frame import LocalCoverSourceAdapter
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_cover_repository import (
    SqliteMediaCoverRepository,
)
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import inspect_database_migration_status

INVALID_INPUT_MESSAGE = "Invalid covers command."
NOT_READY_MESSAGE = "Catalog database is not ready. Run poetry run framenest-db migrate."
NOT_FOUND_MESSAGE = "Library or media was not found."
UNAVAILABLE_MESSAGE = "Cover operation is not available."
COMMAND_FAILED_MESSAGE = "Cover command failed."


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
    """Dispatch durable accepted-cover operations."""
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
    except _DeclinedError:
        print("No durable changes made.")
        return 0
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(str(exc))
        return exc.exit_status
    except (FrameNestPersistenceError, CoverFailedError, Exception):
        _write_error(COMMAND_FAILED_MESSAGE)
        return 1


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-covers")
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
        return _with_service(
            settings,
            lambda service, media_repository: _status(
                service, media_repository, args, settings
            ),
        )
    if args.operation == "generate":
        return _with_service(
            settings,
            lambda service, media_repository: _generate(
                service, media_repository, args, settings
            ),
        )
    raise _UsageError(INVALID_INPUT_MESSAGE)


def _status(
    service: CoverService,
    media_repository: SqliteMediaRepository,
    args: argparse.Namespace,
    settings: KronikaSettings,
) -> int:
    library_id = _parse_optional_library_id(args.library_id)
    media_ids = _selected_media_ids(service, media_repository, library_id)
    states = [service.admin_state(media_id) for media_id in media_ids]
    print("Durable accepted cover status")
    print(f"Cover storage root: {settings.cover_storage_root}")
    print(f"Cover thumbnail cache: {settings.cover_thumbnail_cache_path}")
    print(f"Accepted covers: {len(states)}")
    ready = sum(1 for state in states if state.thumbnail_state == "ready")
    missing = sum(1 for state in states if state.thumbnail_state == "missing")
    print(f"Thumbnail ready: {ready}")
    print(f"Thumbnail missing: {missing}")
    for state in states:
        print(
            f"  {state.media_id}  kind={state.source_kind or 'none'}  "
            f"revision={state.revision}  timestamp_ms={state.timestamp_ms}  "
            f"thumbnail={state.thumbnail_state}  artifact={state.artifact_state}"
        )
    return 0


def _generate(
    service: CoverService,
    media_repository: SqliteMediaRepository,
    args: argparse.Namespace,
    settings: KronikaSettings,
) -> int:
    library_id = _parse_optional_library_id(args.library_id)
    max_items = _parse_positive_int(args.max_items)
    media_ids = _selected_media_ids(service, media_repository, library_id)
    pending: list[str] = []
    for media_id in media_ids:
        state = service.admin_state(media_id)
        if state.thumbnail_state != "ready":
            pending.append(media_id.to_string())
    pending = pending[:max_items]
    print("Durable cover thumbnail regeneration plan")
    print(f"Accepted covers considered: {len(media_ids)}")
    print(f"To regenerate: {len(pending)}")
    if not _confirmed(args.confirmed):
        raise _DeclinedError()
    generated = 0
    failed = 0
    for media_id_text in pending:
        try:
            result = service.regenerate_thumbnail(_parse_media_id(media_id_text))
        except (CoverStorageError, CoverThumbnailUnavailableError):
            failed += 1
            continue
        if result == "ready":
            generated += 1
        else:
            failed += 1
    print("Regeneration complete")
    print(f"Regenerated: {generated}")
    print(f"Failed: {failed}")
    return 0 if failed == 0 else 1


def _selected_media_ids(
    service: CoverService,
    media_repository: SqliteMediaRepository,
    library_id: LibraryId | None,
) -> tuple:
    media_ids = service.list_cover_media_ids()
    if library_id is None:
        return media_ids
    selected: list[object] = []
    for media_id in media_ids:
        state = service.admin_state(media_id)
        if state.source_reference is None:
            continue
        source_location_id = state.source_reference.removeprefix("location:")
        try:
            from kronika.domain.identities import MediaLocationId

            location = media_repository.get_location(
                MediaLocationId.from_string(source_location_id)
            )
        except FrameNestIdentityError:
            location = None
        if location is not None and location.library_id == library_id:
            selected.append(media_id)
    return tuple(selected)


def _parse_optional_library_id(value: str | None) -> LibraryId | None:
    if value is None:
        return None
    try:
        return LibraryId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_media_id(value: str):
    from kronika.domain.identities import MediaId

    try:
        return MediaId.from_string(value)
    except FrameNestIdentityError:
        raise _InvalidInputError() from None


def _parse_positive_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise _InvalidInputError()
    return value


def _confirmed(already_confirmed: bool) -> bool:
    if already_confirmed:
        return True
    answer = input("Regenerate these cover thumbnails now? Type yes to continue: ")
    return answer == "yes"


def _with_service(
    settings: KronikaSettings,
    callback: Callable[[CoverService, SqliteMediaRepository], int],
) -> int:
    status = inspect_database_migration_status(settings)
    if status.state != "at_head":
        raise _NotReadyError()
    engine = create_sqlite_engine(settings.database_path)
    try:
        media_repository = SqliteMediaRepository(engine)
        service = CoverService(
            media_repository,
            SqliteLibraryRepository(engine),
            LocalCoverSourceAdapter(),
            PillowCoverEncoder(),
            FilesystemDurableCoverStorage(settings.cover_storage_root),
            FilesystemCoverThumbnailCache(settings.cover_thumbnail_cache_path),
            SqliteMediaCoverRepository(engine),
        )
        return callback(service, media_repository)
    finally:
        dispose_engine(engine)


def _write_error(message: str) -> None:
    print(message, file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
