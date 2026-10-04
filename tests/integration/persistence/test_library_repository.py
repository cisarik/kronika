"""Integration tests for the SQLite library registry adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, text
from sqlalchemy.exc import SQLAlchemyError

from kronika.application.ports.library_repository import (
    FrameNestLibraryRepositoryError,
    LibraryAlreadyExistsError,
    LibraryDeviceNotFoundError,
    LibraryRootAlreadyRegisteredError,
)
from kronika.domain import (
    Device,
    DeviceId,
    Library,
    LibraryId,
    LibraryPathFlavor,
    LibraryRoot,
)
from kronika.infrastructure.persistence.catalog_schema import libraries

CANONICAL_UUID4_TEXT = "12345678-1234-4234-9234-123456789abc"
SECOND_CANONICAL_UUID4_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
PRIVATE_ROOT_PATH = "/Users/agile/private/library"
PRIVATE_DISPLAY_NAME = "secret-library-name"


def _migrated_engine(tmp_path: Path) -> sa.Engine:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "library-registry.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repository(tmp_path: Path):
    from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
    from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository

    engine = _migrated_engine(tmp_path)
    return SqliteLibraryRepository(engine), SqliteDeviceRepository(engine), engine


def _register_device(
    device_repository,
    *,
    device_id: DeviceId | None = None,
    display_name: str = "Studio Mac",
) -> Device:
    device = Device(id=device_id or DeviceId.new(), display_name=display_name)
    device_repository.add(device)
    return device


def _library(
    *,
    library_id: LibraryId | None = None,
    device_id: DeviceId,
    display_name: str = "Main Library",
    flavor: LibraryPathFlavor = LibraryPathFlavor.POSIX,
    path: str = "/media/main",
) -> Library:
    return Library(
        id=library_id or LibraryId.new(),
        device_id=device_id,
        display_name=display_name,
        root=LibraryRoot(flavor=flavor, path=path),
    )


def test_add_and_get_roundtrip_for_posix_root(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    library = _library(device_id=device.id, path="/media/video")
    try:
        repository.add(library)
        loaded = repository.get(library.id)
        assert loaded == library
    finally:
        engine.dispose()


def test_add_and_get_roundtrip_for_windows_root(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    library = _library(
        device_id=device.id,
        flavor=LibraryPathFlavor.WINDOWS,
        path="D:\\Media\\Library",
    )
    try:
        repository.add(library)
        assert repository.get(library.id) == library
    finally:
        engine.dispose()


def test_get_missing_library_returns_none(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    _register_device(device_repository)
    try:
        assert repository.get(LibraryId.new()) is None
    finally:
        engine.dispose()


def test_list_on_empty_registry_returns_empty_tuple(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    _register_device(device_repository)
    try:
        assert repository.list_all() == ()
    finally:
        engine.dispose()


def test_list_returns_libraries_in_deterministic_order(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    first_device = _register_device(device_repository, display_name="Alpha Device")
    second_device = _register_device(device_repository, display_name="Beta Device")
    alpha = _library(
        library_id=LibraryId.from_string(CANONICAL_UUID4_TEXT),
        device_id=first_device.id,
        display_name="alpha",
        path="/media/alpha",
    )
    beta = _library(
        library_id=LibraryId.from_string(SECOND_CANONICAL_UUID4_TEXT),
        device_id=second_device.id,
        display_name="Beta",
        path="/media/beta",
    )
    zebra = _library(device_id=first_device.id, display_name="zebra", path="/media/zebra")
    try:
        for library in (beta, zebra, alpha):
            repository.add(library)
        assert repository.list_all() == (alpha, beta, zebra)
    finally:
        engine.dispose()


def test_missing_device_raises_library_device_not_found_error(tmp_path: Path) -> None:
    repository, _, engine = _repository(tmp_path)
    library = _library(device_id=DeviceId.new(), path="/media/missing-device")
    try:
        with pytest.raises(LibraryDeviceNotFoundError):
            repository.add(library)
    finally:
        engine.dispose()


def test_duplicate_library_id_raises_library_already_exists_error(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    library_id = LibraryId.new()
    first = _library(library_id=library_id, device_id=device.id, path="/media/first")
    second = _library(library_id=library_id, device_id=device.id, path="/media/second")
    try:
        repository.add(first)
        with pytest.raises(LibraryAlreadyExistsError):
            repository.add(second)
    finally:
        engine.dispose()


def test_same_root_on_same_device_is_rejected(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    first = _library(device_id=device.id, display_name="First", path="/media/shared")
    second = _library(device_id=device.id, display_name="Second", path="/media/shared")
    try:
        repository.add(first)
        with pytest.raises(LibraryRootAlreadyRegisteredError):
            repository.add(second)
    finally:
        engine.dispose()


def test_same_root_text_on_different_devices_is_allowed(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    first_device = _register_device(device_repository, display_name="First Device")
    second_device = _register_device(device_repository, display_name="Second Device")
    first = _library(device_id=first_device.id, display_name="First", path="/media/shared")
    second = _library(device_id=second_device.id, display_name="Second", path="/media/shared")
    try:
        repository.add(first)
        repository.add(second)
        assert len(repository.list_all()) == 2
    finally:
        engine.dispose()


def test_duplicate_display_names_are_allowed(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    first = _library(device_id=device.id, display_name="Shared Name", path="/media/a")
    second = _library(device_id=device.id, display_name="Shared Name", path="/media/b")
    try:
        repository.add(first)
        repository.add(second)
        assert len(repository.list_all()) == 2
    finally:
        engine.dispose()


def test_malformed_stored_record_raises_sanitized_repository_error(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device_id, :display_name, :path_flavor, :root_path)"
                ),
                {
                    "id": LibraryId.new().to_string(),
                    "device_id": device.id.to_string(),
                    "display_name": "Broken",
                    "path_flavor": "posix",
                    "root_path": "relative/path",
                },
            )
            connection.commit()
        with pytest.raises(FrameNestLibraryRepositoryError) as exc_info:
            repository.list_all()
        assert str(exc_info.value) == "Library registry operation failed."
        assert PRIVATE_ROOT_PATH not in str(exc_info.value)
    finally:
        engine.dispose()


def test_transaction_failure_rolls_back(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import run_in_transaction

    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    library = _library(device_id=device.id, path="/media/rollback")
    try:
        repository.add(library)

        def failing_operation(connection: sa.Connection) -> None:
            connection.execute(
                insert(libraries).values(
                    id=LibraryId.new().to_string(),
                    device_id=device.id.to_string(),
                    display_name="Rollback Probe",
                    path_flavor="posix",
                    root_path="/media/probe",
                )
            )
            raise SQLAlchemyError("forced rollback")

        with pytest.raises(SQLAlchemyError):
            run_in_transaction(engine, failing_operation)

        assert repository.get(library.id) == library
        assert repository.list_all() == (library,)
    finally:
        engine.dispose()


def test_repository_errors_do_not_leak_record_values_or_sql(tmp_path: Path) -> None:
    repository, device_repository, engine = _repository(tmp_path)
    device = _register_device(device_repository)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device_id, :display_name, :path_flavor, :root_path)"
                ),
                {
                    "id": LibraryId.new().to_string(),
                    "device_id": device.id.to_string(),
                    "display_name": PRIVATE_DISPLAY_NAME,
                    "path_flavor": "posix",
                    "root_path": "relative/path",
                },
            )
            connection.commit()
        with pytest.raises(FrameNestLibraryRepositoryError) as exc_info:
            repository.list_all()
        rendered = str(exc_info.value)
        assert PRIVATE_ROOT_PATH not in rendered
        assert PRIVATE_DISPLAY_NAME not in rendered
        assert "INSERT INTO" not in rendered
        assert "sqlite" not in rendered.lower()
    finally:
        engine.dispose()
