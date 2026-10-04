"""Contract tests for the SQLite media catalog repository adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from kronika.application.ports.media_repository import (
    FrameNestMediaRepositoryError,
    MediaAlreadyExistsError,
    MediaLocationAlreadyExistsError,
    MediaLocationNotUniqueError,
    MediaLocationReferenceNotFoundError,
)
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.identities import MediaId, MediaLocationId

CANONICAL_MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
SECOND_MEDIA_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
CANONICAL_LOCATION_ID = "11111111-2222-4333-8444-555555555555"
SECOND_LOCATION_ID = "22222222-3333-4444-8555-666666666666"


def _migrated_engine(tmp_path: Path) -> sa.Engine:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "media-repository.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repositories(tmp_path: Path):
    from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
    from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
    from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository

    engine = _migrated_engine(tmp_path)
    return (
        SqliteMediaRepository(engine),
        SqliteLibraryRepository(engine),
        SqliteDeviceRepository(engine),
        engine,
    )


def _register_library(
    device_repository,
    library_repository,
    *,
    path: str = "/media/main",
    library_id: LibraryId | None = None,
) -> Library:
    device = Device(id=DeviceId.new(), display_name=f"Device {path}")
    device_repository.add(device)
    library = Library(
        id=library_id or LibraryId.new(),
        device_id=device.id,
        display_name=f"Library {path}",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path),
    )
    library_repository.add(library)
    return library


def _logical_media(
    *,
    media_id: MediaId | None = None,
    kind: MediaKind = MediaKind.VIDEO,
    created_at_ms: int = 10,
    updated_at_ms: int = 20,
) -> LogicalMedia:
    return LogicalMedia(
        id=media_id or MediaId.new(),
        kind=kind,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
    )


def _location(
    *,
    location_id: MediaLocationId | None = None,
    media_id: MediaId,
    library_id: LibraryId,
    relative_path: str = "clips/a.mp4",
    availability: MediaLocationAvailability = MediaLocationAvailability.AVAILABLE,
    observed_size_bytes: int | None = 123,
    observed_mtime_ns: int | None = 456,
    created_at_ms: int = 30,
    updated_at_ms: int = 40,
) -> MediaLocation:
    return MediaLocation(
        id=location_id or MediaLocationId.new(),
        media_id=media_id,
        library_id=library_id,
        relative_path=MediaRelativePath(relative_path),
        availability=availability,
        observed_size_bytes=observed_size_bytes,
        observed_mtime_ns=observed_mtime_ns,
        created_at_ms=created_at_ms,
        updated_at_ms=updated_at_ms,
    )


def test_create_and_get_logical_media(tmp_path: Path) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    media = _logical_media(media_id=MediaId.from_string(CANONICAL_MEDIA_ID))
    try:
        repository.add_media(media)
        assert repository.get_media(media.id) == media
    finally:
        engine.dispose()


def test_missing_media_and_location_return_none(tmp_path: Path) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    try:
        assert repository.get_media(MediaId.new()) is None
        assert repository.get_location(MediaLocationId.new()) is None
    finally:
        engine.dispose()


def test_logical_media_listing_is_deterministic(tmp_path: Path) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    first = _logical_media(
        media_id=MediaId.from_string(CANONICAL_MEDIA_ID),
        kind=MediaKind.ANIMATED_IMAGE,
        created_at_ms=2,
    )
    second = _logical_media(
        media_id=MediaId.from_string(SECOND_MEDIA_ID),
        kind=MediaKind.VIDEO,
        created_at_ms=1,
    )
    try:
        repository.add_media(first)
        repository.add_media(second)
        assert repository.list_media() == (second, first)
    finally:
        engine.dispose()


def test_duplicate_media_identity_fails_safely(tmp_path: Path) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    media_id = MediaId.new()
    try:
        repository.add_media(_logical_media(media_id=media_id, kind=MediaKind.VIDEO))
        with pytest.raises(MediaAlreadyExistsError):
            repository.add_media(_logical_media(media_id=media_id, kind=MediaKind.ANIMATED_IMAGE))
    finally:
        engine.dispose()


def test_create_get_and_lookup_physical_location(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media(media_id=MediaId.from_string(CANONICAL_MEDIA_ID))
    location = _location(
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        media_id=media.id,
        library_id=library.id,
        relative_path="clips\\a.mp4",
    )
    try:
        repository.add_media(media)
        repository.add_location(location)

        assert repository.get_location(location.id) == location
        assert (
            repository.get_location_by_library_path(library.id, MediaRelativePath("clips/a.mp4"))
            == location
        )
        assert repository.list_locations_for_media(media.id) == (location,)
    finally:
        engine.dispose()


def test_create_media_with_location_is_atomic(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media(media_id=MediaId.from_string(CANONICAL_MEDIA_ID))
    location = _location(
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        media_id=media.id,
        library_id=library.id,
        relative_path="clips/a.mp4",
    )
    try:
        repository.add_media_with_location(media, location)

        assert repository.get_media(media.id) == media
        assert repository.get_location(location.id) == location
    finally:
        engine.dispose()


def test_create_media_with_location_rolls_back_when_location_reference_fails(
    tmp_path: Path,
) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    media = _logical_media(media_id=MediaId.from_string(CANONICAL_MEDIA_ID))
    location = _location(
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        media_id=media.id,
        library_id=LibraryId.new(),
        relative_path="clips/a.mp4",
    )
    try:
        with pytest.raises(MediaLocationReferenceNotFoundError):
            repository.add_media_with_location(media, location)

        assert repository.get_media(media.id) is None
        assert repository.get_location(location.id) is None
    finally:
        engine.dispose()


def test_create_media_with_location_rejects_mismatched_media_reference(
    tmp_path: Path,
) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media(media_id=MediaId.from_string(CANONICAL_MEDIA_ID))
    location = _location(
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        media_id=MediaId.from_string(SECOND_MEDIA_ID),
        library_id=library.id,
        relative_path="clips/a.mp4",
    )
    try:
        with pytest.raises(MediaLocationReferenceNotFoundError):
            repository.add_media_with_location(media, location)

        assert repository.get_media(media.id) is None
        assert repository.get_location(location.id) is None
    finally:
        engine.dispose()


def test_multiple_locations_for_one_logical_media_and_deterministic_order(
    tmp_path: Path,
) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    first_library = _register_library(
        device_repository,
        library_repository,
        path="/media/first",
        library_id=LibraryId.from_string(CANONICAL_MEDIA_ID),
    )
    second_library = _register_library(
        device_repository,
        library_repository,
        path="/media/second",
        library_id=LibraryId.from_string(SECOND_MEDIA_ID),
    )
    media = _logical_media()
    first = _location(
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        media_id=media.id,
        library_id=second_library.id,
        relative_path="b.mp4",
    )
    second = _location(
        location_id=MediaLocationId.from_string(SECOND_LOCATION_ID),
        media_id=media.id,
        library_id=first_library.id,
        relative_path="a.mp4",
        availability=MediaLocationAvailability.ARCHIVED,
        observed_size_bytes=None,
        observed_mtime_ns=None,
    )
    try:
        repository.add_media(media)
        repository.add_location(first)
        repository.add_location(second)
        assert repository.list_locations_for_media(media.id) == (second, first)
    finally:
        engine.dispose()


def test_location_reference_failures_are_sanitized(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media()
    try:
        with pytest.raises(MediaLocationReferenceNotFoundError):
            repository.add_location(
                _location(media_id=media.id, library_id=library.id, relative_path="missing.mp4")
            )
        repository.add_media(media)
        with pytest.raises(MediaLocationReferenceNotFoundError):
            repository.add_location(
                _location(media_id=media.id, library_id=LibraryId.new(), relative_path="missing.mp4")
            )
    finally:
        engine.dispose()


def test_duplicate_location_identity_and_library_path_fail_safely(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media()
    location_id = MediaLocationId.new()
    try:
        repository.add_media(media)
        repository.add_location(
            _location(location_id=location_id, media_id=media.id, library_id=library.id)
        )
        with pytest.raises(MediaLocationAlreadyExistsError):
            repository.add_location(
                _location(
                    location_id=location_id,
                    media_id=media.id,
                    library_id=library.id,
                    relative_path="clips/b.mp4",
                )
            )
        with pytest.raises(MediaLocationNotUniqueError):
            repository.add_location(
                _location(media_id=media.id, library_id=library.id, relative_path="clips/a.mp4")
            )
    finally:
        engine.dispose()


def test_same_relative_path_in_different_libraries_is_allowed(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    first_library = _register_library(device_repository, library_repository, path="/media/first")
    second_library = _register_library(device_repository, library_repository, path="/media/second")
    media = _logical_media()
    try:
        repository.add_media(media)
        repository.add_location(_location(media_id=media.id, library_id=first_library.id))
        repository.add_location(_location(media_id=media.id, library_id=second_library.id))
        assert len(repository.list_locations_for_media(media.id)) == 2
    finally:
        engine.dispose()


def test_enum_path_and_optional_observations_round_trip(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media(kind=MediaKind.ANIMATED_IMAGE)
    location = _location(
        media_id=media.id,
        library_id=library.id,
        relative_path="gifs/reaction.gif",
        availability=MediaLocationAvailability.UNVERIFIED,
        observed_size_bytes=None,
        observed_mtime_ns=None,
    )
    try:
        repository.add_media(media)
        repository.add_location(location)
        assert repository.get_media(media.id) == media
        assert repository.get_location(location.id) == location
    finally:
        engine.dispose()


def test_malformed_stored_row_raises_sanitized_repository_error(tmp_path: Path) -> None:
    repository, _, _, engine = _repositories(tmp_path)
    private_value = "/Users/agile/private/catalog.sqlite3"
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                    "VALUES (:id, :media_kind, :created_at_ms, :updated_at_ms)"
                ),
                {
                    "id": "12345678-1234-4234-9234-123456789ABC",
                    "media_kind": "video",
                    "created_at_ms": 0,
                    "updated_at_ms": 0,
                },
            )
            connection.commit()
        with pytest.raises(FrameNestMediaRepositoryError) as exc_info:
            repository.list_media()
        rendered = str(exc_info.value)
        assert rendered == "Media catalog operation failed."
        assert private_value not in rendered
        assert "12345678-1234-4234-9234-123456789ABC" not in rendered
        assert "INSERT INTO" not in rendered
        assert "sqlite" not in rendered.lower()
    finally:
        engine.dispose()


def test_repository_does_not_access_filesystem_or_apply_migrations(tmp_path: Path) -> None:
    repository, library_repository, device_repository, engine = _repositories(tmp_path)
    library = _register_library(device_repository, library_repository)
    media = _logical_media()
    location = _location(media_id=media.id, library_id=library.id)
    try:
        with (
            patch("pathlib.Path.exists", side_effect=AssertionError("filesystem access forbidden")),
            patch(
                "kronika.infrastructure.persistence.migrations.upgrade_database_to_head",
                side_effect=AssertionError("repository must not migrate"),
            ),
        ):
            repository.add_media(media)
            repository.add_location(location)
            assert repository.get_location(location.id) == location
    finally:
        engine.dispose()
