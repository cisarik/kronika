"""Integration tests for the SQLite device registry adapter."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import insert, text
from sqlalchemy.exc import SQLAlchemyError

from kronika.application.ports.device_repository import (
    DeviceAlreadyExistsError,
    FrameNestDeviceRepositoryError,
)
from kronika.domain import Device, DeviceId
from kronika.infrastructure.persistence.catalog_schema import devices

INVALID_STORED_ID_TEXT = "12345678-1234-4234-9234-123456789ABC"
CANONICAL_UUID4_TEXT = "12345678-1234-4234-9234-123456789abc"
SECOND_CANONICAL_UUID4_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
PRIVATE_DATABASE_PATH = "/Users/agile/private/catalog.sqlite3"


def _migrated_engine(tmp_path: Path) -> sa.Engine:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "device-registry.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repository(tmp_path: Path):
    from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository

    engine = _migrated_engine(tmp_path)
    return SqliteDeviceRepository(engine), engine


def _device(
    *,
    device_id: DeviceId | None = None,
    display_name: str = "Studio Mac",
) -> Device:
    return Device(id=device_id or DeviceId.new(), display_name=display_name)


def test_add_and_get_roundtrip(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    device = _device(display_name="Studio Mac")
    try:
        repository.add(device)
        loaded = repository.get(device.id)
        assert loaded == device
    finally:
        engine.dispose()


def test_get_missing_id_returns_none(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        assert repository.get(DeviceId.new()) is None
    finally:
        engine.dispose()


def test_list_on_empty_registry_returns_empty_tuple(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        assert repository.list_all() == ()
    finally:
        engine.dispose()


def test_list_returns_all_devices_in_deterministic_order(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    alpha = _device(
        device_id=DeviceId.from_string(CANONICAL_UUID4_TEXT),
        display_name="alpha",
    )
    beta = _device(
        device_id=DeviceId.from_string(SECOND_CANONICAL_UUID4_TEXT),
        display_name="Beta",
    )
    zebra = _device(display_name="zebra")
    try:
        for device in (beta, zebra, alpha):
            repository.add(device)
        assert repository.list_all() == (alpha, beta, zebra)
    finally:
        engine.dispose()


def test_duplicate_id_raises_device_already_exists_error(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    device_id = DeviceId.new()
    first = _device(device_id=device_id, display_name="First")
    second = _device(device_id=device_id, display_name="Second")
    try:
        repository.add(first)
        with pytest.raises(DeviceAlreadyExistsError):
            repository.add(second)
    finally:
        engine.dispose()


def test_duplicate_display_names_are_allowed(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    first = _device(display_name="Shared Name")
    second = _device(display_name="Shared Name")
    try:
        repository.add(first)
        repository.add(second)
        loaded = repository.list_all()
        assert len(loaded) == 2
        assert {device.display_name for device in loaded} == {"Shared Name"}
    finally:
        engine.dispose()


def test_transaction_failure_rolls_back(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.engine import run_in_transaction

    repository, engine = _repository(tmp_path)
    device = _device()
    try:
        repository.add(device)

        def failing_operation(connection: sa.Connection) -> None:
            connection.execute(
                insert(devices).values(
                    id=DeviceId.new().to_string(),
                    display_name="Rollback Probe",
                )
            )
            raise SQLAlchemyError("forced rollback")

        with pytest.raises(SQLAlchemyError):
            run_in_transaction(engine, failing_operation)

        assert repository.get(device.id) == device
        assert repository.list_all() == (device,)
    finally:
        engine.dispose()


def test_invalid_stored_id_raises_sanitized_repository_error(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (id, display_name) VALUES (:id, :display_name)"
                ),
                {"id": INVALID_STORED_ID_TEXT, "display_name": "Broken"},
            )
            connection.commit()
        with pytest.raises(FrameNestDeviceRepositoryError) as exc_info:
            repository.list_all()
        assert str(exc_info.value) == "Device registry operation failed."
        assert INVALID_STORED_ID_TEXT not in str(exc_info.value)
    finally:
        engine.dispose()


def test_invalid_stored_display_name_raises_sanitized_repository_error(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (id, display_name) VALUES (:id, :display_name)"
                ),
                {
                    "id": DeviceId.new().to_string(),
                    "display_name": "invalid\u0000name",
                },
            )
            connection.commit()
        with pytest.raises(FrameNestDeviceRepositoryError) as exc_info:
            repository.list_all()
        rendered = str(exc_info.value)
        assert rendered == "Device registry operation failed."
        assert "invalid" not in rendered
        assert "\u0000" not in rendered
    finally:
        engine.dispose()


def test_repository_errors_do_not_leak_sqlalchemy_or_sqlite_details(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.connect() as connection:
            connection.execute(
                text(
                    "INSERT INTO devices (id, display_name) VALUES (:id, :display_name)"
                ),
                {"id": INVALID_STORED_ID_TEXT, "display_name": "Broken"},
            )
            connection.commit()
        with pytest.raises(FrameNestDeviceRepositoryError) as exc_info:
            repository.list_all()
        rendered = str(exc_info.value)
        assert PRIVATE_DATABASE_PATH not in rendered
        assert "INSERT INTO" not in rendered
        assert "sqlite" not in rendered.lower()
        assert "sqlalchemy" not in rendered.lower()
    finally:
        engine.dispose()


def test_engine_disposal_remains_explicit(tmp_path: Path) -> None:
    from unittest.mock import patch

    from kronika.infrastructure.persistence.engine import dispose_engine

    repository, engine = _repository(tmp_path)
    repository.add(_device())
    with patch.object(engine, "dispose", wraps=engine.dispose) as dispose:
        dispose_engine(engine)
    dispose.assert_called_once_with()
