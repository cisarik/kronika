"""Contract tests for the SQLite media metadata repository adapter."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from kronika.application.ports.media_metadata_repository import (
    OMITTED,
    CanonicalTagDefinitionConflictError,
    CanonicalTagNotFoundError,
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataMediaNotFoundError,
    SourceDerivedMetadataImmutableError,
)
from kronika.domain.media import LogicalMedia, MediaKind
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.media_metadata import (
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
)
from kronika.application.companion_review import canonical_field_digest
from kronika.domain.identities import MediaId

CANONICAL_MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
SECOND_MEDIA_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"


def _migrated_engine(tmp_path: Path) -> sa.Engine:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "media-metadata-repository.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repository(tmp_path: Path):
    from kronika.infrastructure.persistence.media_metadata_repository import (
        SqliteMediaMetadataRepository,
    )

    engine = _migrated_engine(tmp_path)
    return SqliteMediaMetadataRepository(engine), engine


def _insert_media(engine: sa.Engine, media_id: str = CANONICAL_MEDIA_ID) -> MediaId:
    parsed = MediaId.from_string(media_id)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (:id, 'video', 1, 1)"
            ),
            {"id": parsed.to_string()},
        )
    return parsed


def test_create_canonical_tag_and_idempotent_repeat(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        created = repository.create_canonical_tag(
            CanonicalTagKey("mathematics"),
            CanonicalTagDisplayName("Math"),
            now_ms=10,
        )
        repeated = repository.create_canonical_tag(
            CanonicalTagKey("mathematics"),
            CanonicalTagDisplayName("Math"),
            now_ms=99,
        )

        assert created.status == "created"
        assert repeated.status == "already_exists"
        assert repeated.tag == created.tag
    finally:
        engine.dispose()


def test_same_key_different_display_name_conflicts(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        repository.create_canonical_tag(
            CanonicalTagKey("mathematics"),
            CanonicalTagDisplayName("Math"),
            now_ms=10,
        )
        with pytest.raises(CanonicalTagDefinitionConflictError):
            repository.create_canonical_tag(
                CanonicalTagKey("mathematics"),
                CanonicalTagDisplayName("Mathematics"),
                now_ms=11,
            )
    finally:
        engine.dispose()


def test_tag_listing_is_deterministic(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        repository.create_canonical_tag(CanonicalTagKey("meme"), CanonicalTagDisplayName("Meme"), 1)
        repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 1)
        repository.create_canonical_tag(CanonicalTagKey("math"), CanonicalTagDisplayName("Compression"), 1)

        assert [(tag.display_name.value, tag.key.value) for tag in repository.list_canonical_tags()] == [
            ("Compression", "compression"),
            ("Compression", "math"),
            ("Meme", "meme"),
        ]
    finally:
        engine.dispose()


def test_missing_and_unsaved_media_are_distinguished(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        unsaved = repository.get_media_metadata(media_id)
        assert unsaved.persisted is False
        assert unsaved.display_title is None
        assert unsaved.tag_keys == ()
        assert unsaved.created_at_ms is None
        with pytest.raises(MediaMetadataMediaNotFoundError):
            repository.get_media_metadata(MediaId.new())
    finally:
        engine.dispose()


def test_metadata_save_create_update_clear_empty_and_unchanged(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 1)

        created = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Reinventing Entropy"),
            None,
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=20,
        )
        updated = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            None,
            (CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
            now_ms=30,
        )
        unchanged = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            None,
            (CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
            now_ms=40,
        )
        cleared = repository.save_media_metadata(media_id, None, None, (), now_ms=50)

        assert created.status == "created"
        assert updated.status == "updated"
        assert updated.metadata.tag_keys == (
            CanonicalTagKey("compression"),
            CanonicalTagKey("mathematics"),
        )
        assert unchanged.status == "unchanged"
        assert unchanged.metadata.updated_at_ms == updated.metadata.updated_at_ms
        assert cleared.status == "updated"
        assert cleared.metadata.display_title is None
        assert cleared.metadata.tag_keys == ()
    finally:
        engine.dispose()


def test_missing_tag_duplicate_keys_and_too_many_tags_fail_safely(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        with pytest.raises(CanonicalTagNotFoundError):
            repository.save_media_metadata(media_id, None, None, (CanonicalTagKey("missing"),), now_ms=2)
        with pytest.raises(ValueError):
            repository.save_media_metadata(
                media_id,
                None,
                None,
                (CanonicalTagKey("mathematics"), CanonicalTagKey("mathematics")),
                now_ms=2,
            )
        with pytest.raises(ValueError):
            repository.save_media_metadata(
                media_id,
                None,
                None,
                tuple(CanonicalTagKey(f"tag-{index}") for index in range(33)),
                now_ms=2,
            )
    finally:
        engine.dispose()


def test_assignment_replacement_failure_rolls_back_previous_state(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 1)
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Original"),
            None,
            (CanonicalTagKey("mathematics"),),
            now_ms=10,
        )
        with patch(
            "kronika.infrastructure.persistence.media_metadata_repository._insert_assignments",
            side_effect=FrameNestMediaMetadataRepositoryError("Media metadata operation failed."),
        ):
            with pytest.raises(FrameNestMediaMetadataRepositoryError):
                repository.save_media_metadata(
                    media_id,
                    MediaDisplayTitle("Changed"),
                    None,
                    (CanonicalTagKey("compression"),),
                    now_ms=20,
                )

        preserved = repository.get_media_metadata(media_id)
        assert preserved.display_title == MediaDisplayTitle("Original")
        assert preserved.tag_keys == (CanonicalTagKey("mathematics"),)
        assert preserved.updated_at_ms == 10
    finally:
        engine.dispose()


def test_malformed_persisted_rows_raise_sanitized_error(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_metadata (media_id, display_title, created_at_ms, updated_at_ms) "
                    "VALUES (:media_id, :display_title, 1, 1)"
                ),
                {"media_id": media_id.to_string(), "display_title": "Bad\nTitle"},
            )
        with pytest.raises(FrameNestMediaMetadataRepositoryError) as exc_info:
            repository.get_media_metadata(media_id)
        rendered = str(exc_info.value)
        assert rendered == "Media metadata operation failed."
        assert "Bad" not in rendered
        assert "SELECT" not in rendered
        assert "sqlite" not in rendered.lower()
    finally:
        engine.dispose()


def test_importing_media_does_not_create_metadata_rows(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository

    repository, engine = _repository(tmp_path)
    media_repository = SqliteMediaRepository(engine)
    try:
        media = LogicalMedia(
            id=MediaId.from_string(CANONICAL_MEDIA_ID),
            kind=MediaKind.VIDEO,
            created_at_ms=1,
            updated_at_ms=1,
        )
        media_repository.add_media(media)
        assert repository.get_media_metadata(media.id).persisted is False
        with engine.connect() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM media_metadata")).scalar_one() == 0
            assert connection.execute(text("SELECT COUNT(*) FROM canonical_tags")).scalar_one() == 0
    finally:
        engine.dispose()


def _row(connection: sa.engine.Connection, media_id: MediaId) -> dict[str, object]:
    return dict(
        connection.execute(
            text(
                "SELECT display_title, description, collection_key, processed_at_ms, "
                "created_at_ms, updated_at_ms FROM media_metadata WHERE media_id = :media_id"
            ),
            {"media_id": media_id.to_string()},
        ).mappings().one()
    )


def _tag_rows(connection: sa.engine.Connection, media_id: MediaId) -> list[str]:
    return [
        row[0]
        for row in connection.execute(
            text(
                "SELECT tag_key FROM media_canonical_tags WHERE media_id = :media_id "
                "ORDER BY position"
            ),
            {"media_id": media_id.to_string()},
        )
    ]


def test_processed_collection_lifecycle_through_real_sqlite_repository(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 1)

        first = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Reinventing Entropy"),
            None,
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=1000,
        )
        assert first.status == "created"
        assert first.metadata.collection_key is not None
        assert first.metadata.collection_key.value == "processed"
        assert first.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["collection_key"] == "processed"
            assert row["processed_at_ms"] == 1000
            assert _tag_rows(connection, media_id) == ["mathematics", "compression"]

        # 8.2 exact no-op preserves everything
        noop = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Reinventing Entropy"),
            None,
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=2000,
        )
        assert noop.status == "unchanged"
        assert noop.metadata.updated_at_ms == 1000
        assert noop.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["updated_at_ms"] == 1000
            assert row["processed_at_ms"] == 1000

        # 8.3 title-only update preserves processed_at_ms
        title_update = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            None,
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=3000,
        )
        assert title_update.status == "updated"
        assert title_update.metadata.updated_at_ms == 3000
        assert title_update.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["display_title"] == "Entropy"
            assert row["processed_at_ms"] == 1000
            assert row["updated_at_ms"] == 3000

        # 8.4 description-only update preserves processed_at_ms
        desc_update = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            MediaDescription("A description."),
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=4000,
        )
        assert desc_update.status == "updated"
        assert desc_update.metadata.updated_at_ms == 4000
        assert desc_update.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["description"] == "A description."
            assert row["processed_at_ms"] == 1000

        # 8.5 reorder preserves processed_at_ms
        reorder = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            MediaDescription("A description."),
            (CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
            now_ms=5000,
        )
        assert reorder.status == "updated"
        assert reorder.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            assert _tag_rows(connection, media_id) == ["compression", "mathematics"]
            assert _row(connection, media_id)["processed_at_ms"] == 1000

        # 8.5 replacement preserves processed_at_ms
        replacement = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            MediaDescription("A description."),
            (CanonicalTagKey("compression"),),
            now_ms=6000,
        )
        assert replacement.status == "updated"
        assert replacement.metadata.processed_at_ms == 1000
        with engine.connect() as connection:
            assert _tag_rows(connection, media_id) == ["compression"]
            assert _row(connection, media_id)["processed_at_ms"] == 1000

        # 8.6 all-tag removal clears collection state
        cleared = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            MediaDescription("A description."),
            (),
            now_ms=7000,
        )
        assert cleared.status == "updated"
        assert cleared.metadata.collection_key is None
        assert cleared.metadata.processed_at_ms is None
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["collection_key"] is None
            assert row["processed_at_ms"] is None
            assert _tag_rows(connection, media_id) == []
            assert row["display_title"] == "Entropy"
            assert row["description"] == "A description."

        # 8.7 re-entry assigns new timestamp
        reentry = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Entropy"),
            MediaDescription("A description."),
            (CanonicalTagKey("mathematics"),),
            now_ms=9000,
        )
        assert reentry.status == "updated"
        assert reentry.metadata.collection_key is not None
        assert reentry.metadata.processed_at_ms == 9000
        assert reentry.metadata.processed_at_ms != 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["collection_key"] == "processed"
            assert row["processed_at_ms"] == 9000
            assert _tag_rows(connection, media_id) == ["mathematics"]

        # GET roundtrip returns the same persisted state
        loaded = repository.get_media_metadata(media_id)
        assert loaded.persisted is True
        assert loaded.collection_key is not None
        assert loaded.collection_key.value == "processed"
        assert loaded.processed_at_ms == 9000
        assert loaded.tag_keys == (CanonicalTagKey("mathematics"),)
    finally:
        engine.dispose()


def test_processed_collection_first_entry_with_processed_at_ms_zero(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        result = repository.save_media_metadata(
            media_id,
            None,
            None,
            (CanonicalTagKey("mathematics"),),
            now_ms=0,
        )
        assert result.metadata.collection_key is not None
        assert result.metadata.collection_key.value == "processed"
        assert result.metadata.processed_at_ms == 0
        assert isinstance(result.metadata.processed_at_ms, int)
        assert not isinstance(result.metadata.processed_at_ms, bool)
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["collection_key"] == "processed"
            assert row["processed_at_ms"] == 0
    finally:
        engine.dispose()


def test_processed_collection_rollback_preserves_previous_collection_state(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1)
        repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 1)
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Original"),
            None,
            (CanonicalTagKey("mathematics"),),
            now_ms=1000,
        )
        with patch(
            "kronika.infrastructure.persistence.media_metadata_repository._insert_assignments",
            side_effect=FrameNestMediaMetadataRepositoryError("Media metadata operation failed."),
        ):
            with pytest.raises(FrameNestMediaMetadataRepositoryError):
                repository.save_media_metadata(
                    media_id,
                    MediaDisplayTitle("Changed"),
                    None,
                    (CanonicalTagKey("compression"),),
                    now_ms=2000,
                )

        preserved = repository.get_media_metadata(media_id)
        assert preserved.display_title == MediaDisplayTitle("Original")
        assert preserved.tag_keys == (CanonicalTagKey("mathematics"),)
        assert preserved.collection_key is not None
        assert preserved.collection_key.value == "processed"
        assert preserved.processed_at_ms == 1000
        assert preserved.updated_at_ms == 1000
        with engine.connect() as connection:
            row = _row(connection, media_id)
            assert row["collection_key"] == "processed"
            assert row["processed_at_ms"] == 1000
            assert row["display_title"] == "Original"
            assert _tag_rows(connection, media_id) == ["mathematics"]
    finally:
        engine.dispose()


def _seed_x_media(repository, engine, media_id=CANONICAL_MEDIA_ID):
    repository.create_canonical_tag(CanonicalTagKey("funny"), CanonicalTagDisplayName("Funny"), 1)
    repository.create_canonical_tag(CanonicalTagKey("art"), CanonicalTagDisplayName("Art"), 1)
    parsed = _insert_media(engine, media_id)
    created = repository.save_media_metadata(
        parsed,
        MediaDisplayTitle("X Meme"),
        None,
        (),
        now_ms=10,
        content_category=ContentCategory.MEME,
        acquisition_source=AcquisitionSource.X_MANUAL_CLAIM,
        creator_attribution_kind=CreatorAttributionKind.X_AUTHOR,
        creator_stable_id="12345",
        creator_handle="michal",
        creator_display_name="Michal",
    )
    assert created.status == "created"
    return parsed


def test_x_omitted_category_preserves_meme(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id, MediaDisplayTitle("Renamed"), None, (), now_ms=20
        )
        assert result.status == "updated"
        assert result.metadata.content_category is ContentCategory.MEME
        loaded = repository.get_media_metadata(media_id)
        assert loaded.content_category is ContentCategory.MEME
    finally:
        engine.dispose()


def test_x_identical_meme_category_is_a_noop(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("funny"), CanonicalTagDisplayName("Funny"), 1)
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Renamed"),
            None,
            (CanonicalTagKey("funny"),),
            now_ms=20,
            content_category=ContentCategory.MEME,
        )
        assert result.status == "updated"
        assert result.metadata.content_category is ContentCategory.MEME
        assert result.metadata.tag_keys == (CanonicalTagKey("funny"),)
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "category",
    [
        ContentCategory.GENERAL,
        ContentCategory.MOVIE,
        ContentCategory.YOUTUBE,
    ],
)
def test_x_changing_category_is_allowed(tmp_path: Path, category) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Renamed"),
            None,
            (),
            now_ms=20,
            content_category=category,
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.content_category is category
        assert loaded.display_title == MediaDisplayTitle("Renamed")
        assert loaded.acquisition_source is AcquisitionSource.X_MANUAL_CLAIM
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_handle == "michal"
    finally:
        engine.dispose()


def test_x_omitted_or_none_category_preserves_meme(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id, MediaDisplayTitle("Renamed"), None, (), now_ms=20, content_category=None
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.content_category is ContentCategory.MEME
        assert loaded.display_title == MediaDisplayTitle("Renamed")
        assert loaded.creator_handle == "michal"
    finally:
        engine.dispose()


def test_x_omitted_creator_fields_preserve_attribution(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id, MediaDisplayTitle("Renamed"), None, (), now_ms=20
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_stable_id == "12345"
        assert loaded.creator_handle == "michal"
        assert loaded.creator_display_name == "Michal"
    finally:
        engine.dispose()


def test_x_identical_creator_tuple_is_a_noop(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Renamed"),
            None,
            (),
            now_ms=20,
            creator_attribution_kind=CreatorAttributionKind.X_AUTHOR,
            creator_stable_id="12345",
            creator_handle="michal",
            creator_display_name="Michal",
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_display_name == "Michal"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "field",
    ["kind", "stable_id", "handle", "display_name"],
)
def test_x_clearing_creator_field_rejects(tmp_path: Path, field: str) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        call_kwargs = {}
        if field == "kind":
            call_kwargs["creator_attribution_kind"] = None
        elif field == "stable_id":
            call_kwargs["creator_stable_id"] = None
        elif field == "handle":
            call_kwargs["creator_handle"] = None
        else:
            call_kwargs["creator_display_name"] = None
        with pytest.raises(SourceDerivedMetadataImmutableError):
            repository.save_media_metadata(
                media_id, MediaDisplayTitle("Renamed"), None, (), now_ms=20, **call_kwargs
            )
        loaded = repository.get_media_metadata(media_id)
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_stable_id == "12345"
        assert loaded.creator_display_name == "Michal"
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    "call_kwargs",
    [
        {
            "creator_attribution_kind": CreatorAttributionKind.YOUTUBE_CHANNEL,
            "creator_stable_id": "12345",
            "creator_handle": "michal",
            "creator_display_name": "Michal",
        },
        {
            "creator_attribution_kind": CreatorAttributionKind.X_AUTHOR,
            "creator_stable_id": "99999",
            "creator_handle": "michal",
            "creator_display_name": "Michal",
        },
        {
            "creator_attribution_kind": CreatorAttributionKind.X_AUTHOR,
            "creator_stable_id": "12345",
            "creator_handle": "other",
            "creator_display_name": "Michal",
        },
        {
            "creator_attribution_kind": CreatorAttributionKind.X_AUTHOR,
            "creator_stable_id": "12345",
            "creator_handle": "michal",
            "creator_display_name": "Other",
        },
    ],
)
def test_x_changing_creator_field_rejects(tmp_path: Path, call_kwargs) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        with pytest.raises(SourceDerivedMetadataImmutableError):
            repository.save_media_metadata(
                media_id, MediaDisplayTitle("Renamed"), None, (), now_ms=20, **call_kwargs
            )
        loaded = repository.get_media_metadata(media_id)
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_stable_id == "12345"
        assert loaded.creator_handle == "michal"
        assert loaded.creator_display_name == "Michal"
    finally:
        engine.dispose()


def test_x_atomic_rejection_discards_allowed_edits(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        with pytest.raises(SourceDerivedMetadataImmutableError):
            repository.save_media_metadata(
                media_id,
                MediaDisplayTitle("Renamed"),
                MediaDescription("New description."),
                (CanonicalTagKey("funny"), CanonicalTagKey("art")),
                now_ms=20,
                content_category=ContentCategory.GENERAL,
                creator_attribution_kind=CreatorAttributionKind.X_AUTHOR,
                creator_stable_id="12345",
                creator_handle="other",
                creator_display_name="Michal",
            )
        loaded = repository.get_media_metadata(media_id)
        assert loaded.display_title == MediaDisplayTitle("X Meme")
        assert loaded.description is None
        assert loaded.tag_keys == ()
        assert loaded.content_category is ContentCategory.MEME
        assert loaded.creator_handle == "michal"
    finally:
        engine.dispose()


def test_x_allowed_metadata_edit_preserves_protected_values(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_x_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Renamed"),
            MediaDescription("A description."),
            (CanonicalTagKey("funny"),),
            now_ms=20,
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.display_title == MediaDisplayTitle("Renamed")
        assert loaded.description == MediaDescription("A description.")
        assert loaded.tag_keys == (CanonicalTagKey("funny"),)
        assert loaded.content_category is ContentCategory.MEME
        assert loaded.acquisition_source is AcquisitionSource.X_MANUAL_CLAIM
        assert loaded.creator_attribution_kind is CreatorAttributionKind.X_AUTHOR
        assert loaded.creator_stable_id == "12345"
        assert loaded.creator_handle == "michal"
        assert loaded.creator_display_name == "Michal"
    finally:
        engine.dispose()


def _seed_manual_media(repository, engine):
    parsed = _insert_media(engine)
    created = repository.save_media_metadata(
        parsed,
        MediaDisplayTitle("Manual"),
        None,
        (),
        now_ms=10,
        content_category=ContentCategory.GENERAL,
        acquisition_source=AcquisitionSource.MANUAL_UPLOAD,
    )
    assert created.status == "created"
    return parsed


def test_non_x_general_media_metadata_edit_works(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_manual_media(repository, engine)
    try:
        repository.create_canonical_tag(CanonicalTagKey("funny"), CanonicalTagDisplayName("Funny"), 1)
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Renamed"),
            MediaDescription("A description."),
            (CanonicalTagKey("funny"),),
            now_ms=20,
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.display_title == MediaDisplayTitle("Renamed")
        assert loaded.tag_keys == (CanonicalTagKey("funny"),)
        assert loaded.acquisition_source is AcquisitionSource.MANUAL_UPLOAD
    finally:
        engine.dispose()


def _seed_youtube_media(repository, engine):
    repository.create_canonical_tag(CanonicalTagKey("funny"), CanonicalTagDisplayName("Funny"), 1)
    parsed = _insert_media(engine)
    created = repository.save_media_metadata(
        parsed,
        MediaDisplayTitle("YT"),
        None,
        (),
        now_ms=10,
        content_category=ContentCategory.YOUTUBE,
        acquisition_source=AcquisitionSource.YOUTUBE_MANUAL_CLAIM,
        creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
        creator_stable_id="UC123",
        creator_handle=None,
        creator_display_name="Channel",
    )
    assert created.status == "created"
    return parsed


def test_non_x_youtube_admin_category_and_creator_edits_work(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_youtube_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("YT ren"),
            None,
            (CanonicalTagKey("funny"),),
            now_ms=20,
            content_category=ContentCategory.MEME,
            creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
            creator_stable_id="UC999",
            creator_handle=None,
            creator_display_name="Renamed",
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.content_category is ContentCategory.MEME
        assert loaded.creator_stable_id == "UC999"
        assert loaded.creator_display_name == "Renamed"
    finally:
        engine.dispose()


def _seed_movie_media(repository, engine):
    parsed = _insert_media(engine)
    created = repository.save_media_metadata(
        parsed,
        MediaDisplayTitle("Film"),
        None,
        (),
        now_ms=10,
        content_category=ContentCategory.MOVIE,
        acquisition_source=AcquisitionSource.MANUAL_UPLOAD,
    )
    assert created.status == "created"
    return parsed


def test_website_save_drops_stale_companion_receipts_only(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    device_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    library_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    try:
        repository.create_canonical_tag(
            CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1
        )
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Original title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("mathematics"),),
            now_ms=10,
        )
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO devices (id, display_name) VALUES (:id, 'Dev')"),
                {"id": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device, 'Lib', 'posix', '/tmp/synthetic')"
                ),
                {"id": library_id, "device": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations ("
                    "id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                    ") VALUES (:id, :media, :library, 'clip.mp4', 'available', 8, NULL, 1, 1)"
                ),
                {
                    "id": location_id,
                    "media": media_id.to_string(),
                    "library": library_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_analysis_runs ("
                    "id, media_id, media_location_id, analysis_definition, state, "
                    "attempt_count, provider_id, model_id, prompt_version, "
                    "result_schema_version, result_json, error_code, error_message, "
                    "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
                    ") VALUES ("
                    ":id, :media, :location, 'automatic_post_catalog', 'analyzed', 1, "
                    "'nvidia-nim', 'test-model', 'framenest-media-suggestion-v4', "
                    "'framenest-media-suggestion-result-v1', '{}', NULL, NULL, "
                    "'generic_media', 1, 1, 1, 2)"
                ),
                {
                    "id": run_id,
                    "media": media_id.to_string(),
                    "location": location_id,
                },
            )
            title_digest = canonical_field_digest("display_title", "Original title")
            description_digest = canonical_field_digest(
                "description", "Original description"
            )
            tags_digest = canonical_field_digest("tags", ("mathematics",))
            connection.execute(
                text(
                    "INSERT INTO companion_review_field_sources ("
                    "media_id, field_name, analysis_run_id, applied_by_login_key, "
                    "applied_at_ms, value_digest"
                    ") VALUES "
                    "(:media, 'display_title', :run, 'admin@example.com', 5, :title), "
                    "(:media, 'description', :run, 'admin@example.com', 5, :description), "
                    "(:media, 'tags', :run, 'admin@example.com', 5, :tags)"
                ),
                {
                    "media": media_id.to_string(),
                    "run": run_id,
                    "title": title_digest,
                    "description": description_digest,
                    "tags": tags_digest,
                },
            )
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Changed title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("mathematics"),),
            now_ms=20,
        )
        with engine.connect() as connection:
            remaining = {
                str(row[0]): str(row[1])
                for row in connection.execute(
                    text(
                        "SELECT field_name, value_digest FROM companion_review_field_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": media_id.to_string()},
                )
            }
        assert "display_title" not in remaining
        assert remaining["description"] == description_digest
        assert remaining["tags"] == tags_digest
        unchanged = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Changed title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("mathematics"),),
            now_ms=30,
        )
        assert unchanged.status == "unchanged"
        with engine.connect() as connection:
            after_noop = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT field_name FROM companion_review_field_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": media_id.to_string()},
                )
            }
        assert after_noop == {"description", "tags"}
    finally:
        engine.dispose()


def test_website_save_drops_removed_tag_sources_only(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _insert_media(engine)
    run_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    device_id = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    library_id = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    try:
        repository.create_canonical_tag(
            CanonicalTagKey("mathematics"), CanonicalTagDisplayName("Math"), 1
        )
        repository.create_canonical_tag(
            CanonicalTagKey("compression"), CanonicalTagDisplayName("Comp"), 1
        )
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Original title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("mathematics"), CanonicalTagKey("compression")),
            now_ms=10,
        )
        with engine.begin() as connection:
            connection.execute(
                text("INSERT INTO devices (id, display_name) VALUES (:id, 'Dev')"),
                {"id": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO libraries "
                    "(id, device_id, display_name, path_flavor, root_path) "
                    "VALUES (:id, :device, 'Lib', 'posix', '/tmp/synthetic')"
                ),
                {"id": library_id, "device": device_id},
            )
            connection.execute(
                text(
                    "INSERT INTO physical_media_locations ("
                    "id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                    ") VALUES (:id, :media, :library, 'clip.mp4', 'available', 8, NULL, 1, 1)"
                ),
                {
                    "id": location_id,
                    "media": media_id.to_string(),
                    "library": library_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO media_analysis_runs ("
                    "id, media_id, media_location_id, analysis_definition, state, "
                    "attempt_count, provider_id, model_id, prompt_version, "
                    "result_schema_version, result_json, error_code, error_message, "
                    "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
                    ") VALUES ("
                    ":id, :media, :location, 'automatic_post_catalog', 'analyzed', 1, "
                    "'nvidia-nim', 'test-model', 'framenest-media-suggestion-v4', "
                    "'framenest-media-suggestion-result-v1', '{}', NULL, NULL, "
                    "'generic_media', 1, 1, 1, 2)"
                ),
                {
                    "id": run_id,
                    "media": media_id.to_string(),
                    "location": location_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO companion_review_tag_sources ("
                    "media_id, tag_key, analysis_run_id, applied_by_login_key, applied_at_ms"
                    ") VALUES "
                    "(:media, 'mathematics', :run, 'admin@example.com', 5), "
                    "(:media, 'compression', :run, 'admin@example.com', 5)"
                ),
                {"media": media_id.to_string(), "run": run_id},
            )
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Changed title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
            now_ms=20,
        )
        with engine.connect() as connection:
            reordered = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT tag_key FROM companion_review_tag_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": media_id.to_string()},
                )
            }
        assert reordered == {"mathematics", "compression"}
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Changed title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("compression"),),
            now_ms=30,
        )
        with engine.connect() as connection:
            remaining = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT tag_key FROM companion_review_tag_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": media_id.to_string()},
                )
            }
        assert remaining == {"compression"}
        repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Changed title"),
            MediaDescription("Original description"),
            (CanonicalTagKey("compression"), CanonicalTagKey("mathematics")),
            now_ms=40,
        )
        with engine.connect() as connection:
            after_readd = {
                str(row[0])
                for row in connection.execute(
                    text(
                        "SELECT tag_key FROM companion_review_tag_sources "
                        "WHERE media_id = :media"
                    ),
                    {"media": media_id.to_string()},
                )
            }
        assert after_readd == {"compression"}
    finally:
        engine.dispose()


def test_non_x_movie_metadata_edit_works(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    media_id = _seed_movie_media(repository, engine)
    try:
        result = repository.save_media_metadata(
            media_id,
            MediaDisplayTitle("Film 2"),
            None,
            (),
            now_ms=20,
            content_category=ContentCategory.MOVIE,
        )
        assert result.status == "updated"
        loaded = repository.get_media_metadata(media_id)
        assert loaded.display_title == MediaDisplayTitle("Film 2")
        assert loaded.content_category is ContentCategory.MOVIE
    finally:
        engine.dispose()
