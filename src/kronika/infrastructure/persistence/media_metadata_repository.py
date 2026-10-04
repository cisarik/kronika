"""SQLAlchemy Core adapter for persistent media metadata and canonical tags."""

from __future__ import annotations

from sqlalchemy import delete, exists, insert, not_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_metadata_repository import (
    OMITTED,
    AcquisitionSourceImmutableError,
    CanonicalTagCreateResult,
    CanonicalTagDefinitionConflictError,
    CanonicalTagNotFoundError,
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataMediaNotFoundError,
    MediaMetadataSaveResult,
    MediaMetadataSnapshot,
    SourceDerivedMetadataImmutableError,
)
from kronika.domain import FrameNestIdentityError, MediaId
from kronika.domain.media_classification import (
    DEFAULT_ACQUISITION_SOURCE,
    DEFAULT_CONTENT_CATEGORY,
    MAX_MEDIA_GENRES,
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
    MovieGenre,
)
from kronika.application.companion_review import canonical_field_digest
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    derive_collection_state,
    FrameNestMediaMetadataError,
    MediaCollectionKey,
    MediaDescription,
    MediaDisplayTitle,
    normalize_genres_for_category,
    validate_creator_attribution_fields,
)
from kronika.infrastructure.persistence.catalog_schema import (
    canonical_tags,
    kronika_records,
    companion_review_field_sources,
    companion_review_tag_sources,
    logical_media,
    media_canonical_tags,
    media_content_publications,
    media_genres,
    media_metadata,
)
from kronika.infrastructure.persistence.engine import run_in_transaction

_REPOSITORY_FAILURE_MESSAGE = "Media metadata operation failed."
_X_SOURCE_DERIVED_MESSAGE = (
    "X source-derived values are immutable provenance and cannot be changed."
)


class SqliteMediaMetadataRepository:
    """Synchronous SQLite metadata repository backed by SQLAlchemy Core."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        def operation(connection: Connection) -> CanonicalTagCreateResult:
            existing = _get_tag(connection, key)
            if existing is not None:
                if existing.display_name != display_name:
                    raise CanonicalTagDefinitionConflictError()
                return CanonicalTagCreateResult(status="already_exists", tag=existing)
            tag = CanonicalTag(
                key=key,
                display_name=display_name,
                created_at_ms=now_ms,
                updated_at_ms=now_ms,
            )
            connection.execute(
                insert(canonical_tags).values(
                    key=tag.key.value,
                    display_name=tag.display_name.value,
                    created_at_ms=tag.created_at_ms,
                    updated_at_ms=tag.updated_at_ms,
                )
            )
            return CanonicalTagCreateResult(status="created", tag=tag)

        try:
            return run_in_transaction(self._engine, operation)
        except CanonicalTagDefinitionConflictError:
            raise
        except IntegrityError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_canonical_tags(self) -> tuple[CanonicalTag, ...]:
        def operation(connection: Connection) -> tuple[CanonicalTag, ...]:
            rows = connection.execute(
                select(
                    canonical_tags.c.key,
                    canonical_tags.c.display_name,
                    canonical_tags.c.created_at_ms,
                    canonical_tags.c.updated_at_ms,
                ).order_by(canonical_tags.c.display_name, canonical_tags.c.key)
            ).mappings()
            return tuple(_tag_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaMetadataRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def list_canonical_tags_for_published_media(self) -> tuple[CanonicalTag, ...]:
        def operation(connection: Connection) -> tuple[CanonicalTag, ...]:
            rows = connection.execute(
                select(
                    canonical_tags.c.key,
                    canonical_tags.c.display_name,
                    canonical_tags.c.created_at_ms,
                    canonical_tags.c.updated_at_ms,
                )
                .select_from(
                    canonical_tags.join(
                        media_canonical_tags,
                        media_canonical_tags.c.tag_key == canonical_tags.c.key,
                    ).join(
                        media_content_publications,
                        media_content_publications.c.media_id
                        == media_canonical_tags.c.media_id,
                    )
                )
                .where(
                    not_(
                        exists(
                            select(1)
                            .select_from(kronika_records)
                            .where(
                                kronika_records.c.media_id
                                == media_canonical_tags.c.media_id
                            )
                        )
                    )
                )
                .distinct()
                .order_by(canonical_tags.c.display_name, canonical_tags.c.key)
            ).mappings()
            return tuple(_tag_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaMetadataRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        def operation(connection: Connection) -> CanonicalTag | None:
            return _get_tag(connection, key)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaMetadataRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def get_media_metadata(self, media_id: MediaId) -> MediaMetadataSnapshot:
        def operation(connection: Connection) -> MediaMetadataSnapshot:
            if not _media_exists(connection, media_id):
                raise MediaMetadataMediaNotFoundError()
            return _load_metadata_snapshot(connection, media_id)

        try:
            return run_in_transaction(self._engine, operation)
        except MediaMetadataMediaNotFoundError:
            raise
        except FrameNestMediaMetadataRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc

    def save_media_metadata(
        self,
        media_id: MediaId,
        display_title: MediaDisplayTitle | None,
        description: MediaDescription | None,
        tag_keys: tuple[CanonicalTagKey, ...],
        now_ms: int,
        *,
        content_category: ContentCategory | None | object = OMITTED,
        acquisition_source: AcquisitionSource | None = None,
        genre_keys: tuple[MovieGenre, ...] = (),
        creator_attribution_kind: CreatorAttributionKind | None | object = OMITTED,
        creator_stable_id: str | None | object = OMITTED,
        creator_handle: str | None | object = OMITTED,
        creator_display_name: str | None | object = OMITTED,
    ) -> MediaMetadataSaveResult:
        if len(tag_keys) > 32 or len(set(tag_keys)) != len(tag_keys):
            raise ValueError(_REPOSITORY_FAILURE_MESSAGE)
        if content_category is not OMITTED and (
            content_category is not None and not isinstance(content_category, ContentCategory)
        ):
            raise ValueError(_REPOSITORY_FAILURE_MESSAGE)
        if acquisition_source is not None and not isinstance(
            acquisition_source,
            AcquisitionSource,
        ):
            raise ValueError(_REPOSITORY_FAILURE_MESSAGE)
        _validate_provided_creator_attribution(
            creator_attribution_kind,
            creator_stable_id,
            creator_handle,
            creator_display_name,
        )
        if len(genre_keys) > MAX_MEDIA_GENRES or len(set(genre_keys)) != len(genre_keys):
            raise ValueError(_REPOSITORY_FAILURE_MESSAGE)

        def operation(connection: Connection) -> MediaMetadataSaveResult:
            if not _media_exists(connection, media_id):
                raise MediaMetadataMediaNotFoundError()
            for key in tag_keys:
                if _get_tag(connection, key) is None:
                    raise CanonicalTagNotFoundError()
            current = _load_metadata_snapshot(connection, media_id)
            if current.persisted:
                if acquisition_source is None:
                    resolved_source = current.acquisition_source
                elif acquisition_source == current.acquisition_source:
                    resolved_source = current.acquisition_source
                else:
                    raise AcquisitionSourceImmutableError(
                        "Acquisition source is immutable provenance and cannot be changed."
                    )
                if current.acquisition_source is AcquisitionSource.X_MANUAL_CLAIM:
                    resolved_category = (
                        current.content_category
                        if content_category in (None, OMITTED)
                        else content_category
                    )
                    resolved_creator_kind = _resolve_x_source_field(
                        "creator attribution kind",
                        current.creator_attribution_kind,
                        creator_attribution_kind,
                    )
                    resolved_stable_id = _resolve_x_source_field(
                        "creator stable id",
                        current.creator_stable_id,
                        creator_stable_id,
                    )
                    resolved_handle = _resolve_x_source_field(
                        "creator handle",
                        current.creator_handle,
                        creator_handle,
                    )
                    resolved_display_name = _resolve_x_source_field(
                        "creator display name",
                        current.creator_display_name,
                        creator_display_name,
                    )
                else:
                    resolved_category = (
                        current.content_category
                        if content_category in (None, OMITTED)
                        else content_category
                    )
                    resolved_creator_kind = (
                        current.creator_attribution_kind
                        if creator_attribution_kind in (None, OMITTED)
                        else creator_attribution_kind
                    )
                    resolved_stable_id = (
                        current.creator_stable_id
                        if creator_stable_id in (None, OMITTED)
                        else creator_stable_id
                    )
                    resolved_handle = (
                        current.creator_handle if creator_handle in (None, OMITTED) else creator_handle
                    )
                    resolved_display_name = (
                        current.creator_display_name
                        if creator_display_name in (None, OMITTED)
                        else creator_display_name
                    )
            else:
                resolved_source = (
                    DEFAULT_ACQUISITION_SOURCE
                    if acquisition_source is None
                    else acquisition_source
                )
                resolved_category = (
                    DEFAULT_CONTENT_CATEGORY if content_category in (None, OMITTED) else content_category
                )
                resolved_creator_kind = (
                    None if creator_attribution_kind in (None, OMITTED) else creator_attribution_kind
                )
                resolved_stable_id = (
                    None if creator_stable_id in (None, OMITTED) else creator_stable_id
                )
                resolved_handle = None if creator_handle in (None, OMITTED) else creator_handle
                resolved_display_name = (
                    None if creator_display_name in (None, OMITTED) else creator_display_name
                )
            normalized_genres = normalize_genres_for_category(resolved_category, genre_keys)
            derived = derive_collection_state(
                current.collection_key,
                current.processed_at_ms,
                tag_keys,
                now_ms,
            )
            if (
                current.persisted
                and current.display_title == display_title
                and current.description == description
                and current.tag_keys == tag_keys
                and current.collection_key == derived.collection_key
                and current.processed_at_ms == derived.processed_at_ms
                and current.content_category == resolved_category
                and current.acquisition_source == resolved_source
                and current.genre_keys == normalized_genres
                and current.creator_attribution_kind == resolved_creator_kind
                and current.creator_stable_id == resolved_stable_id
                and current.creator_handle == resolved_handle
                and current.creator_display_name == resolved_display_name
            ):
                return MediaMetadataSaveResult(status="unchanged", metadata=current)

            if current.persisted:
                created_at_ms = current.created_at_ms
                assert created_at_ms is not None
                connection.execute(
                    update(media_metadata)
                    .where(media_metadata.c.media_id == media_id.to_string())
                    .values(
                        display_title=None if display_title is None else display_title.value,
                        description=None if description is None else description.value,
                        content_category=resolved_category.value,
                        acquisition_source=resolved_source.value,
                        creator_attribution_kind=(
                            None
                            if resolved_creator_kind is None
                            else resolved_creator_kind.value
                        ),
                        creator_stable_id=resolved_stable_id,
                        creator_handle=resolved_handle,
                        creator_display_name=resolved_display_name,
                        collection_key=None
                        if derived.collection_key is None
                        else derived.collection_key.value,
                        processed_at_ms=derived.processed_at_ms,
                        updated_at_ms=now_ms,
                    )
                )
                status = "updated"
            else:
                created_at_ms = now_ms
                connection.execute(
                    insert(media_metadata).values(
                        media_id=media_id.to_string(),
                        display_title=None if display_title is None else display_title.value,
                        description=None if description is None else description.value,
                        content_category=resolved_category.value,
                        acquisition_source=resolved_source.value,
                        creator_attribution_kind=(
                            None
                            if resolved_creator_kind is None
                            else resolved_creator_kind.value
                        ),
                        creator_stable_id=resolved_stable_id,
                        creator_handle=resolved_handle,
                        creator_display_name=resolved_display_name,
                        collection_key=None
                        if derived.collection_key is None
                        else derived.collection_key.value,
                        processed_at_ms=derived.processed_at_ms,
                        created_at_ms=now_ms,
                        updated_at_ms=now_ms,
                    )
                )
                status = "created"
            connection.execute(
                delete(media_canonical_tags).where(
                    media_canonical_tags.c.media_id == media_id.to_string()
                )
            )
            connection.execute(
                delete(media_genres).where(media_genres.c.media_id == media_id.to_string())
            )
            _insert_assignments(connection, media_id, tag_keys)
            _insert_genres(connection, media_id, normalized_genres)
            _delete_stale_companion_receipts(
                connection,
                media_id=media_id.to_string(),
                display_title=None if display_title is None else display_title.value,
                description=None if description is None else description.value,
                tag_keys=tuple(key.value for key in tag_keys),
            )
            _delete_removed_companion_tag_sources(
                connection,
                media_id=media_id.to_string(),
                tag_keys=tuple(key.value for key in tag_keys),
            )
            snapshot = MediaMetadataSnapshot(
                media_id=media_id,
                persisted=True,
                display_title=display_title,
                description=description,
                tag_keys=tag_keys,
                collection_key=derived.collection_key,
                processed_at_ms=derived.processed_at_ms,
                created_at_ms=created_at_ms,
                updated_at_ms=now_ms,
                content_category=resolved_category,
                acquisition_source=resolved_source,
                genre_keys=normalized_genres,
                creator_attribution_kind=resolved_creator_kind,
                creator_stable_id=resolved_stable_id,
                creator_handle=resolved_handle,
                creator_display_name=resolved_display_name,
            )
            from kronika.infrastructure.persistence.record_repository import (
                bump_bound_record_version,
            )

            bump_bound_record_version(connection, media_id.to_string())
            return MediaMetadataSaveResult(status=status, metadata=snapshot)

        try:
            return run_in_transaction(self._engine, operation)
        except (
            AcquisitionSourceImmutableError,
            CanonicalTagNotFoundError,
            MediaMetadataMediaNotFoundError,
            FrameNestMediaMetadataRepositoryError,
            SourceDerivedMetadataImmutableError,
        ):
            raise
        except IntegrityError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _delete_stale_companion_receipts(
    connection: Connection,
    *,
    media_id: str,
    display_title: str | None,
    description: str | None,
    tag_keys: tuple[str, ...],
) -> None:
    """Drop field-source receipts whose digest no longer matches canonical values."""
    current_digests = {
        "display_title": (
            None
            if display_title is None
            else canonical_field_digest("display_title", display_title)
        ),
        "description": (
            None
            if description is None
            else canonical_field_digest("description", description)
        ),
        "tags": canonical_field_digest("tags", tag_keys),
    }
    rows = connection.execute(
        select(
            companion_review_field_sources.c.field_name,
            companion_review_field_sources.c.value_digest,
        ).where(companion_review_field_sources.c.media_id == media_id)
    ).mappings().all()
    for row in rows:
        field_name = str(row["field_name"])
        expected = current_digests.get(field_name)
        if expected is not None and str(row["value_digest"]) == expected:
            continue
        connection.execute(
            delete(companion_review_field_sources).where(
                companion_review_field_sources.c.media_id == media_id,
                companion_review_field_sources.c.field_name == field_name,
            )
        )


def _delete_removed_companion_tag_sources(
    connection: Connection,
    *,
    media_id: str,
    tag_keys: tuple[str, ...],
) -> None:
    """Drop per-tag AI sources only for keys removed from the submitted vector."""
    if not tag_keys:
        connection.execute(
            delete(companion_review_tag_sources).where(
                companion_review_tag_sources.c.media_id == media_id
            )
        )
        return
    connection.execute(
        delete(companion_review_tag_sources).where(
            companion_review_tag_sources.c.media_id == media_id,
            companion_review_tag_sources.c.tag_key.notin_(tag_keys),
        )
    )


def _media_exists(connection: Connection, media_id: MediaId) -> bool:
    return (
        connection.execute(
            select(logical_media.c.id).where(logical_media.c.id == media_id.to_string())
        ).first()
        is not None
    )


def _resolve_x_source_field(field: str, current: object, provided: object) -> object:
    """Resolve one protected X source-derived value for a metadata Save.

    ``OMITTED`` preserves the existing value; an identical value is a compatible
    no-op; a different supplied value or an explicit clear of a present value is
    rejected.
    """
    if provided is OMITTED:
        return current
    if provided == current:
        return current
    if provided is None:
        if current is None:
            return None
        raise SourceDerivedMetadataImmutableError(
            _X_SOURCE_DERIVED_MESSAGE + f" ({field})"
        )
    raise SourceDerivedMetadataImmutableError(
        _X_SOURCE_DERIVED_MESSAGE + f" ({field})"
    )


def _validate_provided_creator_attribution(
    kind: object,
    stable_id: object,
    handle: object,
    display_name: object,
) -> None:
    """Validate the provided (non-omitted) creator attribution fields."""
    resolved_kind = None if kind is OMITTED else kind
    resolved_stable = None if stable_id is OMITTED else stable_id
    resolved_handle = None if handle is OMITTED else handle
    resolved_display = None if display_name is OMITTED else display_name
    validate_creator_attribution_fields(
        resolved_kind,
        resolved_stable,
        resolved_handle,
        resolved_display,
    )


def _get_tag(connection: Connection, key: CanonicalTagKey) -> CanonicalTag | None:
    row = (
        connection.execute(
            select(
                canonical_tags.c.key,
                canonical_tags.c.display_name,
                canonical_tags.c.created_at_ms,
                canonical_tags.c.updated_at_ms,
            ).where(canonical_tags.c.key == key.value)
        )
        .mappings()
        .first()
    )
    if row is None:
        return None
    return _tag_from_row(row)


def _load_metadata_snapshot(connection: Connection, media_id: MediaId) -> MediaMetadataSnapshot:
    metadata_row = (
        connection.execute(
            select(
                media_metadata.c.media_id,
                media_metadata.c.display_title,
                media_metadata.c.description,
                media_metadata.c.content_category,
                media_metadata.c.acquisition_source,
                media_metadata.c.creator_attribution_kind,
                media_metadata.c.creator_stable_id,
                media_metadata.c.creator_handle,
                media_metadata.c.creator_display_name,
                media_metadata.c.collection_key,
                media_metadata.c.processed_at_ms,
                media_metadata.c.created_at_ms,
                media_metadata.c.updated_at_ms,
            ).where(media_metadata.c.media_id == media_id.to_string())
        )
        .mappings()
        .first()
    )
    if metadata_row is None:
        return MediaMetadataSnapshot(
            media_id=media_id,
            persisted=False,
            display_title=None,
            description=None,
            tag_keys=(),
            collection_key=None,
            processed_at_ms=None,
            created_at_ms=None,
            updated_at_ms=None,
            content_category=DEFAULT_CONTENT_CATEGORY,
            acquisition_source=DEFAULT_ACQUISITION_SOURCE,
            genre_keys=(),
        )
    assignment_rows = connection.execute(
        select(media_canonical_tags.c.tag_key)
        .where(media_canonical_tags.c.media_id == media_id.to_string())
        .order_by(media_canonical_tags.c.position)
    ).mappings()
    genre_rows = connection.execute(
        select(media_genres.c.genre_key)
        .where(media_genres.c.media_id == media_id.to_string())
        .order_by(media_genres.c.position)
    ).mappings()
    try:
        mapping = dict(metadata_row)
        title = mapping["display_title"]
        if title is not None and not isinstance(title, str):
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        raw_description = mapping["description"]
        if raw_description is not None and not isinstance(raw_description, str):
            raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE)
        raw_collection_key = mapping["collection_key"]
        raw_processed_at_ms = mapping["processed_at_ms"]
        raw_kind = mapping["creator_attribution_kind"]
        creator_kind = None if raw_kind is None else CreatorAttributionKind(raw_kind)
        (
            creator_kind,
            creator_stable_id,
            creator_handle,
            creator_display_name,
        ) = validate_creator_attribution_fields(
            creator_kind,
            mapping["creator_stable_id"],
            mapping["creator_handle"],
            mapping["creator_display_name"],
        )
        return MediaMetadataSnapshot(
            media_id=MediaId.from_string(mapping["media_id"]),
            persisted=True,
            display_title=None if title is None else MediaDisplayTitle(title),
            description=None if raw_description is None else MediaDescription(raw_description),
            tag_keys=tuple(CanonicalTagKey(dict(row)["tag_key"]) for row in assignment_rows),
            collection_key=None
            if raw_collection_key is None
            else MediaCollectionKey(raw_collection_key),
            processed_at_ms=raw_processed_at_ms,
            created_at_ms=mapping["created_at_ms"],
            updated_at_ms=mapping["updated_at_ms"],
            content_category=ContentCategory(mapping["content_category"]),
            acquisition_source=AcquisitionSource(mapping["acquisition_source"]),
            genre_keys=tuple(MovieGenre(dict(row)["genre_key"]) for row in genre_rows),
            creator_attribution_kind=creator_kind,
            creator_stable_id=creator_stable_id,
            creator_handle=creator_handle,
            creator_display_name=creator_display_name,
        )
    except (
        FrameNestIdentityError,
        FrameNestMediaMetadataError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc


def _insert_assignments(
    connection: Connection,
    media_id: MediaId,
    tag_keys: tuple[CanonicalTagKey, ...],
) -> None:
    for position, key in enumerate(tag_keys):
        connection.execute(
            insert(media_canonical_tags).values(
                media_id=media_id.to_string(),
                tag_key=key.value,
                position=position,
            )
        )


def _insert_genres(
    connection: Connection,
    media_id: MediaId,
    genre_keys: tuple[MovieGenre, ...],
) -> None:
    for position, genre in enumerate(genre_keys):
        connection.execute(
            insert(media_genres).values(
                media_id=media_id.to_string(),
                genre_key=genre.value,
                position=position,
            )
        )


def _tag_from_row(row: object) -> CanonicalTag:
    try:
        mapping = dict(row)  # type: ignore[arg-type]
        return CanonicalTag(
            key=CanonicalTagKey(mapping["key"]),
            display_name=CanonicalTagDisplayName(mapping["display_name"]),
            created_at_ms=mapping["created_at_ms"],
            updated_at_ms=mapping["updated_at_ms"],
        )
    except (
        FrameNestMediaMetadataError,
        KeyError,
        TypeError,
        ValueError,
    ) as exc:
        raise FrameNestMediaMetadataRepositoryError(_REPOSITORY_FAILURE_MESSAGE) from exc
