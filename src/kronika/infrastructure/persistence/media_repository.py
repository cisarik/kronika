"""SQLAlchemy Core adapter for the persistent media catalog foundation."""

from __future__ import annotations

from sqlalchemy import insert, select
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_repository import (
    FrameNestMediaRepositoryError,
    MediaAlreadyExistsError,
    MediaLocationAlreadyExistsError,
    MediaLocationNotUniqueError,
    MediaLocationReferenceNotFoundError,
)
from kronika.domain import (
    FrameNestIdentityError,
    FrameNestMediaError,
    FrameNestMediaLocationError,
    FrameNestMediaRelativePathError,
    LibraryId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.infrastructure.persistence.catalog_schema import (
    libraries,
    logical_media,
    physical_media_locations,
)
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Media catalog operation failed."


class SqliteMediaRepository:
    """Synchronous SQLite media catalog backed by SQLAlchemy Core."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def add_media(self, media: LogicalMedia) -> None:
        def operation(connection: Connection) -> None:
            connection.execute(
                insert(logical_media).values(
                    id=media.id.to_string(),
                    media_kind=media.kind.value,
                    created_at_ms=media.created_at_ms,
                    updated_at_ms=media.updated_at_ms,
                )
            )

        try:
            run_in_transaction(self._engine, operation)
        except IntegrityError as exc:
            raise MediaAlreadyExistsError() from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        def operation(connection: Connection) -> LogicalMedia | None:
            row = (
                connection.execute(
                    select(
                        logical_media.c.id,
                        logical_media.c.media_kind,
                        logical_media.c.created_at_ms,
                        logical_media.c.updated_at_ms,
                    ).where(logical_media.c.id == media_id.to_string())
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return _media_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_media(self) -> tuple[LogicalMedia, ...]:
        def operation(connection: Connection) -> tuple[LogicalMedia, ...]:
            rows = connection.execute(
                select(
                    logical_media.c.id,
                    logical_media.c.media_kind,
                    logical_media.c.created_at_ms,
                    logical_media.c.updated_at_ms,
                )
            ).mappings()
            loaded = tuple(_media_from_row(row) for row in rows)
            return _sort_media(loaded)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def add_location(self, location: MediaLocation) -> None:
        def operation(connection: Connection) -> None:
            location_id_text = location.id.to_string()
            media_id_text = location.media_id.to_string()
            library_id_text = location.library_id.to_string()
            relative_path = location.relative_path.value
            if not _media_exists(connection, media_id_text):
                raise MediaLocationReferenceNotFoundError()
            if not _library_exists(connection, library_id_text):
                raise MediaLocationReferenceNotFoundError()
            if _location_exists(connection, location_id_text):
                raise MediaLocationAlreadyExistsError()
            if _library_path_exists(connection, library_id_text, relative_path):
                raise MediaLocationNotUniqueError()
            connection.execute(
                insert(physical_media_locations).values(
                    id=location_id_text,
                    media_id=media_id_text,
                    library_id=library_id_text,
                    relative_path=relative_path,
                    availability=location.availability.value,
                    observed_size_bytes=location.observed_size_bytes,
                    observed_mtime_ns=location.observed_mtime_ns,
                    created_at_ms=location.created_at_ms,
                    updated_at_ms=location.updated_at_ms,
                )
            )

        try:
            run_in_transaction(self._engine, operation)
        except (
            MediaLocationAlreadyExistsError,
            MediaLocationNotUniqueError,
            MediaLocationReferenceNotFoundError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        if location.media_id != media.id:
            raise MediaLocationReferenceNotFoundError()

        def operation(connection: Connection) -> None:
            _insert_media(connection, media)
            _insert_location(connection, location)

        try:
            run_in_transaction(self._engine, operation)
        except (
            MediaAlreadyExistsError,
            MediaLocationAlreadyExistsError,
            MediaLocationNotUniqueError,
            MediaLocationReferenceNotFoundError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        def operation(connection: Connection) -> MediaLocation | None:
            row = (
                connection.execute(
                    _location_select().where(
                        physical_media_locations.c.id == location_id.to_string()
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return _location_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_location_by_library_path(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaLocation | None:
        def operation(connection: Connection) -> MediaLocation | None:
            row = (
                connection.execute(
                    _location_select().where(
                        physical_media_locations.c.library_id == library_id.to_string(),
                        physical_media_locations.c.relative_path == relative_path.value,
                    )
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            return _location_from_row(row)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_locations_for_media(self, media_id: MediaId) -> tuple[MediaLocation, ...]:
        def operation(connection: Connection) -> tuple[MediaLocation, ...]:
            rows = connection.execute(
                _location_select().where(
                    physical_media_locations.c.media_id == media_id.to_string()
                )
            ).mappings()
            loaded = tuple(_location_from_row(row) for row in rows)
            return _sort_locations(loaded)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_all_locations(self) -> tuple[MediaLocation, ...]:
        def operation(connection: Connection) -> tuple[MediaLocation, ...]:
            rows = connection.execute(_location_select()).mappings()
            loaded = tuple(_location_from_row(row) for row in rows)
            return _sort_locations(loaded)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _media_exists(connection: Connection, media_id_text: str) -> bool:
    return (
        connection.execute(
            select(logical_media.c.id).where(logical_media.c.id == media_id_text)
        ).first()
        is not None
    )


def _insert_media(connection: Connection, media: LogicalMedia) -> None:
    media_id_text = media.id.to_string()
    if _media_exists(connection, media_id_text):
        raise MediaAlreadyExistsError()
    connection.execute(
        insert(logical_media).values(
            id=media_id_text,
            media_kind=media.kind.value,
            created_at_ms=media.created_at_ms,
            updated_at_ms=media.updated_at_ms,
        )
    )


def _insert_location(connection: Connection, location: MediaLocation) -> None:
    location_id_text = location.id.to_string()
    media_id_text = location.media_id.to_string()
    library_id_text = location.library_id.to_string()
    relative_path = location.relative_path.value
    if not _media_exists(connection, media_id_text):
        raise MediaLocationReferenceNotFoundError()
    if not _library_exists(connection, library_id_text):
        raise MediaLocationReferenceNotFoundError()
    if _location_exists(connection, location_id_text):
        raise MediaLocationAlreadyExistsError()
    if _library_path_exists(connection, library_id_text, relative_path):
        raise MediaLocationNotUniqueError()
    connection.execute(
        insert(physical_media_locations).values(
            id=location_id_text,
            media_id=media_id_text,
            library_id=library_id_text,
            relative_path=relative_path,
            availability=location.availability.value,
            observed_size_bytes=location.observed_size_bytes,
            observed_mtime_ns=location.observed_mtime_ns,
            created_at_ms=location.created_at_ms,
            updated_at_ms=location.updated_at_ms,
        )
    )


def _library_exists(connection: Connection, library_id_text: str) -> bool:
    return (
        connection.execute(select(libraries.c.id).where(libraries.c.id == library_id_text)).first()
        is not None
    )


def _location_exists(connection: Connection, location_id_text: str) -> bool:
    return (
        connection.execute(
            select(physical_media_locations.c.id).where(
                physical_media_locations.c.id == location_id_text
            )
        ).first()
        is not None
    )


def _library_path_exists(connection: Connection, library_id_text: str, relative_path: str) -> bool:
    return (
        connection.execute(
            select(physical_media_locations.c.id).where(
                physical_media_locations.c.library_id == library_id_text,
                physical_media_locations.c.relative_path == relative_path,
            )
        ).first()
        is not None
    )


def _location_select():
    return select(
        physical_media_locations.c.id,
        physical_media_locations.c.media_id,
        physical_media_locations.c.library_id,
        physical_media_locations.c.relative_path,
        physical_media_locations.c.availability,
        physical_media_locations.c.observed_size_bytes,
        physical_media_locations.c.observed_mtime_ns,
        physical_media_locations.c.created_at_ms,
        physical_media_locations.c.updated_at_ms,
    )


def _media_from_row(row: object) -> LogicalMedia:
    try:
        mapping = dict(row)  # type: ignore[arg-type]
        media_id_text = mapping["id"]
        media_kind = mapping["media_kind"]
        created_at_ms = mapping["created_at_ms"]
        updated_at_ms = mapping["updated_at_ms"]
        if not isinstance(media_id_text, str) or not isinstance(media_kind, str):
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        return LogicalMedia(
            id=MediaId.from_string(media_id_text),
            kind=MediaKind(media_kind),
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
        )
    except (
        FrameNestIdentityError,
        FrameNestMediaError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _location_from_row(row: object) -> MediaLocation:
    try:
        mapping = dict(row)  # type: ignore[arg-type]
        location_id_text = mapping["id"]
        media_id_text = mapping["media_id"]
        library_id_text = mapping["library_id"]
        relative_path = mapping["relative_path"]
        availability = mapping["availability"]
        if not all(
            isinstance(value, str)
            for value in (
                location_id_text,
                media_id_text,
                library_id_text,
                relative_path,
                availability,
            )
        ):
            raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        return MediaLocation(
            id=MediaLocationId.from_string(location_id_text),
            media_id=MediaId.from_string(media_id_text),
            library_id=LibraryId.from_string(library_id_text),
            relative_path=MediaRelativePath(relative_path),
            availability=MediaLocationAvailability(availability),
            observed_size_bytes=mapping["observed_size_bytes"],
            observed_mtime_ns=mapping["observed_mtime_ns"],
            created_at_ms=mapping["created_at_ms"],
            updated_at_ms=mapping["updated_at_ms"],
        )
    except (
        FrameNestIdentityError,
        FrameNestMediaLocationError,
        FrameNestMediaRelativePathError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestMediaRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _sort_media(media_to_sort: tuple[LogicalMedia, ...]) -> tuple[LogicalMedia, ...]:
    return tuple(
        sorted(
            media_to_sort,
            key=lambda media: (
                media.created_at_ms,
                media.kind.value,
                media.id.to_string(),
            ),
        )
    )


def _sort_locations(locations_to_sort: tuple[MediaLocation, ...]) -> tuple[MediaLocation, ...]:
    return tuple(
        sorted(
            locations_to_sort,
            key=lambda location: (
                location.library_id.to_string(),
                location.relative_path.value,
                location.id.to_string(),
            ),
        )
    )
