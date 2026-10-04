"""SQLAlchemy Core adapter for the durable accepted-cover relation."""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_cover_repository import (
    FrameNestMediaCoverRepositoryError,
    MediaCoverConflictError,
    MediaCoverDraft,
    MediaCoverMediaNotFoundError,
    MediaCoverRepository,
    MediaCoverSetResult,
)
from kronika.domain.identities import FrameNestIdentityError, MediaId, MediaLocationId
from kronika.domain.media import (
    FrameNestMediaLocationError,
    FrameNestMediaRelativePathError,
)
from kronika.domain.media_cover import (
    FrameNestMediaCoverError,
    CoverSourceKind,
    MediaCover,
)
from kronika.infrastructure.persistence.catalog_schema import (
    logical_media,
    media_covers,
)
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "Accepted cover operation failed."

_COVER_COLUMNS = (
    media_covers.c.media_id,
    media_covers.c.source_location_id,
    media_covers.c.source_reference,
    media_covers.c.source_kind,
    media_covers.c.source_timestamp_ms,
    media_covers.c.source_size_bytes,
    media_covers.c.source_mtime_ns,
    media_covers.c.source_duration_ms,
    media_covers.c.source_observation_version,
    media_covers.c.source_observation_digest,
    media_covers.c.artifact_profile,
    media_covers.c.artifact_media_type,
    media_covers.c.artifact_digest,
    media_covers.c.artifact_width,
    media_covers.c.artifact_height,
    media_covers.c.artifact_byte_size,
    media_covers.c.revision,
    media_covers.c.accepted_at_ms,
)


class SqliteMediaCoverRepository:
    """Synchronous SQLite adapter for the sparse accepted-cover relation."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get(self, media_id: MediaId) -> MediaCover | None:
        def operation(connection: Connection) -> MediaCover | None:
            return _get_cover(connection, media_id.to_string())

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaCoverRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaCoverRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_by_media(self, media_ids: tuple[MediaId, ...]) -> tuple[MediaCover, ...]:
        def operation(connection: Connection) -> tuple[MediaCover, ...]:
            if not media_ids:
                return ()
            rows = connection.execute(
                sa.select(*_COVER_COLUMNS).where(
                    media_covers.c.media_id.in_(
                        [media_id.to_string() for media_id in media_ids]
                    )
                )
            ).mappings()
            covers = tuple(_cover_from_row(row) for row in rows)
            return tuple(
                sorted(
                    covers,
                    key=lambda cover: (
                        cover.media_id.to_string(),
                        cover.accepted_at_ms,
                    ),
                )
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaCoverRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaCoverRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_all(self) -> tuple[MediaCover, ...]:
        def operation(connection: Connection) -> tuple[MediaCover, ...]:
            rows = connection.execute(sa.select(*_COVER_COLUMNS)).mappings()
            covers = tuple(_cover_from_row(row) for row in rows)
            return tuple(
                sorted(
                    covers,
                    key=lambda cover: (
                        cover.media_id.to_string(),
                        cover.accepted_at_ms,
                    ),
                )
            )

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaCoverRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaCoverRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def set_cover(
        self,
        draft: MediaCoverDraft,
        expected_revision: int,
    ) -> MediaCoverSetResult:
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int):
            raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        if expected_revision < 0:
            raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE)

        def operation(connection: Connection) -> MediaCoverSetResult:
            media_id_text = draft.media_id.to_string()
            if (
                connection.execute(
                    sa.select(logical_media.c.id).where(
                        logical_media.c.id == media_id_text
                    )
                ).first()
                is None
            ):
                raise MediaCoverMediaNotFoundError(_REPOSITORY_FAILURE_MESSAGE)
            current = _get_cover(connection, media_id_text)
            if current is None:
                if expected_revision != 0:
                    raise MediaCoverConflictError(_REPOSITORY_FAILURE_MESSAGE)
                _insert_cover(connection, draft, revision=1)
                created = _get_cover(connection, media_id_text)
                if created is None:
                    raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
                from kronika.infrastructure.persistence.record_repository import (
                    bump_bound_record_version,
                )

                bump_bound_record_version(connection, media_id_text)
                return MediaCoverSetResult(outcome="created", cover=created)
            if expected_revision != current.revision:
                raise MediaCoverConflictError(_REPOSITORY_FAILURE_MESSAGE)
            if draft.same_payload(current):
                return MediaCoverSetResult(outcome="unchanged", cover=current)
            _update_cover(connection, draft, revision=current.revision + 1)
            replaced = _get_cover(connection, media_id_text)
            if replaced is None:
                raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
            from kronika.infrastructure.persistence.record_repository import (
                bump_bound_record_version,
            )

            bump_bound_record_version(connection, media_id_text)
            return MediaCoverSetResult(outcome="replaced", cover=replaced)

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except (MediaCoverMediaNotFoundError, MediaCoverConflictError):
            raise
        except FrameNestMediaCoverRepositoryError:
            raise
        except (IntegrityError, SQLAlchemyError) as exc:
            raise FrameNestMediaCoverRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _values_from_draft(draft: MediaCoverDraft, *, revision: int) -> dict[str, object]:
    return {
        "media_id": draft.media_id.to_string(),
        "source_location_id": (
            None
            if draft.source_location_id is None
            else draft.source_location_id.to_string()
        ),
        "source_reference": draft.source_reference,
        "source_kind": draft.source_kind.value,
        "source_timestamp_ms": draft.source_timestamp_ms,
        "source_size_bytes": draft.source_size_bytes,
        "source_mtime_ns": draft.source_mtime_ns,
        "source_duration_ms": draft.source_duration_ms,
        "source_observation_version": draft.source_observation_version,
        "source_observation_digest": draft.source_observation_digest,
        "artifact_profile": draft.artifact_profile,
        "artifact_media_type": draft.artifact_media_type,
        "artifact_digest": draft.artifact_digest,
        "artifact_width": draft.artifact_width,
        "artifact_height": draft.artifact_height,
        "artifact_byte_size": draft.artifact_byte_size,
        "revision": revision,
        "accepted_at_ms": draft.accepted_at_ms,
    }


def _insert_cover(connection: Connection, draft: MediaCoverDraft, *, revision: int) -> None:
    connection.execute(
        sa.insert(media_covers).values(_values_from_draft(draft, revision=revision))
    )


def _update_cover(connection: Connection, draft: MediaCoverDraft, *, revision: int) -> None:
    connection.execute(
        sa.update(media_covers)
        .where(media_covers.c.media_id == draft.media_id.to_string())
        .values(_values_from_draft(draft, revision=revision))
    )


def _get_cover(connection: Connection, media_id_text: str) -> MediaCover | None:
    row = connection.execute(
        sa.select(*_COVER_COLUMNS).where(media_covers.c.media_id == media_id_text)
    ).mappings().first()
    return None if row is None else _cover_from_row(row)


def _cover_from_row(row: object) -> MediaCover:
    try:
        mapping = dict(row)  # type: ignore[arg-type]
        media_id_text = mapping["media_id"]
        source_location_id_text = mapping["source_location_id"]
        source_reference = mapping["source_reference"]
        source_kind = mapping["source_kind"]
        source_observation_version = mapping["source_observation_version"]
        source_observation_digest = mapping["source_observation_digest"]
        artifact_profile = mapping["artifact_profile"]
        artifact_media_type = mapping["artifact_media_type"]
        artifact_digest = mapping["artifact_digest"]
        if not all(
            isinstance(value, str)
            for value in (
                media_id_text,
                source_reference,
                source_kind,
                source_observation_version,
                source_observation_digest,
                artifact_profile,
                artifact_media_type,
                artifact_digest,
            )
        ):
            raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        return MediaCover(
            media_id=MediaId.from_string(media_id_text),
            source_location_id=(
                None
                if source_location_id_text is None
                else MediaLocationId.from_string(source_location_id_text)
            ),
            source_reference=source_reference,
            source_kind=CoverSourceKind(source_kind),
            source_timestamp_ms=int(mapping["source_timestamp_ms"]),
            source_size_bytes=int(mapping["source_size_bytes"]),
            source_mtime_ns=(
                None if mapping["source_mtime_ns"] is None else int(mapping["source_mtime_ns"])
            ),
            source_duration_ms=(
                None
                if mapping["source_duration_ms"] is None
                else int(mapping["source_duration_ms"])
            ),
            source_observation_version=source_observation_version,
            source_observation_digest=source_observation_digest,
            artifact_profile=artifact_profile,
            artifact_media_type=artifact_media_type,
            artifact_digest=artifact_digest,
            artifact_width=int(mapping["artifact_width"]),
            artifact_height=int(mapping["artifact_height"]),
            artifact_byte_size=int(mapping["artifact_byte_size"]),
            revision=int(mapping["revision"]),
            accepted_at_ms=int(mapping["accepted_at_ms"]),
        )
    except (
        FrameNestIdentityError,
        FrameNestMediaLocationError,
        FrameNestMediaRelativePathError,
        FrameNestMediaCoverError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestMediaCoverRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
