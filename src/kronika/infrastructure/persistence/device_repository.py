"""SQLAlchemy Core adapter for the local device registry."""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.device_repository import (
    DeviceAlreadyExistsError,
    FrameNestDeviceRepositoryError,
)
from kronika.domain import Device, DeviceId, FrameNestDeviceError, FrameNestIdentityError
from kronika.infrastructure.persistence.catalog_schema import devices
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Device registry operation failed."


class SqliteDeviceRepository:
    """Synchronous SQLite device registry backed by SQLAlchemy Core."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, device: Device) -> None:
        def operation(connection: Connection) -> None:
            connection.execute(
                insert(devices).values(
                    id=device.id.to_string(),
                    display_name=device.display_name,
                )
            )

        try:
            run_in_transaction(self._engine, operation)
        except IntegrityError as exc:
            raise DeviceAlreadyExistsError() from exc
        except SQLAlchemyError as exc:
            raise FrameNestDeviceRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get(self, device_id: DeviceId) -> Device | None:
        def operation(connection: Connection) -> Device | None:
            row = (
                connection.execute(
                    select(devices.c.id, devices.c.display_name).where(
                        devices.c.id == device_id.to_string()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return _device_from_row(row["id"], row["display_name"])

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestDeviceRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestDeviceRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_all(self) -> tuple[Device, ...]:
        def operation(connection: Connection) -> tuple[Device, ...]:
            rows = connection.execute(
                select(devices.c.id, devices.c.display_name)
            ).mappings()
            loaded = tuple(_device_from_row(row["id"], row["display_name"]) for row in rows)
            return _sort_devices(loaded)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestDeviceRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestDeviceRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _device_from_row(device_id_text: object, display_name: object) -> Device:
    try:
        if not isinstance(device_id_text, str) or not isinstance(display_name, str):
            raise FrameNestDeviceRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        return Device(id=DeviceId.from_string(device_id_text), display_name=display_name)
    except (FrameNestDeviceError, FrameNestIdentityError) as exc:
        raise FrameNestDeviceRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _sort_devices(devices_to_sort: tuple[Device, ...]) -> tuple[Device, ...]:
    return tuple(
        sorted(
            devices_to_sort,
            key=lambda device: (
                device.display_name.casefold(),
                device.display_name,
                device.id.to_string(),
            ),
        )
    )
