"""SQLAlchemy Core adapter for the local library registry."""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.library_repository import (
    FrameNestLibraryRepositoryError,
    LibraryAlreadyExistsError,
    LibraryDeviceNotFoundError,
    LibraryRootAlreadyRegisteredError,
)
from kronika.domain import (
    DeviceId,
    FrameNestIdentityError,
    FrameNestLibraryError,
    FrameNestLibraryRootError,
    Library,
    LibraryId,
    LibraryPathFlavor,
    LibraryRoot,
)
from kronika.infrastructure.persistence.catalog_schema import devices, libraries
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Library registry operation failed."


class SqliteLibraryRepository:
    """Synchronous SQLite library registry backed by SQLAlchemy Core."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add(self, library: Library) -> None:
        def operation(connection: Connection) -> None:
            device_id_text = library.device_id.to_string()
            library_id_text = library.id.to_string()
            if not _device_exists(connection, device_id_text):
                raise LibraryDeviceNotFoundError()
            if _library_exists(connection, library_id_text):
                raise LibraryAlreadyExistsError()
            if _root_exists(
                connection,
                device_id_text,
                library.root.flavor.value,
                library.root.path,
            ):
                raise LibraryRootAlreadyRegisteredError()
            connection.execute(
                insert(libraries).values(
                    id=library_id_text,
                    device_id=device_id_text,
                    display_name=library.display_name,
                    path_flavor=library.root.flavor.value,
                    root_path=library.root.path,
                )
            )

        try:
            run_in_transaction(self._engine, operation)
        except (
            LibraryAlreadyExistsError,
            LibraryDeviceNotFoundError,
            LibraryRootAlreadyRegisteredError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get(self, library_id: LibraryId) -> Library | None:
        def operation(connection: Connection) -> Library | None:
            row = (
                connection.execute(
                    select(
                        libraries.c.id,
                        libraries.c.device_id,
                        libraries.c.display_name,
                        libraries.c.path_flavor,
                        libraries.c.root_path,
                    ).where(libraries.c.id == library_id.to_string())
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return _library_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestLibraryRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_all(self) -> tuple[Library, ...]:
        def operation(connection: Connection) -> tuple[Library, ...]:
            rows = connection.execute(
                select(
                    libraries.c.id,
                    libraries.c.device_id,
                    libraries.c.display_name,
                    libraries.c.path_flavor,
                    libraries.c.root_path,
                )
            ).mappings()
            loaded = tuple(_library_from_row(row) for row in rows)
            return _sort_libraries(loaded)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestLibraryRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _device_exists(connection: Connection, device_id_text: str) -> bool:
    return (
        connection.execute(
            select(devices.c.id).where(devices.c.id == device_id_text)
        ).first()
        is not None
    )


def _library_exists(connection: Connection, library_id_text: str) -> bool:
    return (
        connection.execute(
            select(libraries.c.id).where(libraries.c.id == library_id_text)
        ).first()
        is not None
    )


def _root_exists(
    connection: Connection,
    device_id_text: str,
    path_flavor: str,
    root_path: str,
) -> bool:
    return (
        connection.execute(
            select(libraries.c.id).where(
                libraries.c.device_id == device_id_text,
                libraries.c.path_flavor == path_flavor,
                libraries.c.root_path == root_path,
            )
        ).first()
        is not None
    )


def _library_from_row(row: object) -> Library:
    try:
        if not isinstance(row, dict):
            mapping = dict(row)  # type: ignore[arg-type]
        else:
            mapping = row
        library_id_text = mapping["id"]
        device_id_text = mapping["device_id"]
        display_name = mapping["display_name"]
        path_flavor = mapping["path_flavor"]
        root_path = mapping["root_path"]
        if not all(
            isinstance(value, str)
            for value in (
                library_id_text,
                device_id_text,
                display_name,
                path_flavor,
                root_path,
            )
        ):
            raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        flavor = LibraryPathFlavor(path_flavor)
        return Library(
            id=LibraryId.from_string(library_id_text),
            device_id=DeviceId.from_string(device_id_text),
            display_name=display_name,
            root=LibraryRoot(flavor=flavor, path=root_path),
        )
    except (
        FrameNestIdentityError,
        FrameNestLibraryError,
        FrameNestLibraryRootError,
        ValueError,
    ) as exc:
        raise FrameNestLibraryRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _sort_libraries(libraries_to_sort: tuple[Library, ...]) -> tuple[Library, ...]:
    return tuple(
        sorted(
            libraries_to_sort,
            key=lambda library: (
                library.display_name.casefold(),
                library.display_name,
                library.device_id.to_string(),
                library.root.flavor.value,
                library.root.path,
                library.id.to_string(),
            ),
        )
    )
