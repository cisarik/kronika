"""Contract tests for the SQLite searchable media catalog read adapter."""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from kronika.application.ports.media_catalog_repository import MediaCatalogQuery
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.media_metadata import CanonicalTagKey
from kronika.domain.record_access import AccessScopeKind, RecordAccessScope

MEDIA_A = "11111111-1111-4111-8111-111111111111"
MEDIA_B = "22222222-2222-4222-8222-222222222222"
MEDIA_C = "33333333-3333-4333-8333-333333333333"
MEDIA_D = "44444444-4444-4444-8444-444444444444"
LOCATION_A = "aaaaaaaa-1111-4111-8111-111111111111"
LOCATION_B = "bbbbbbbb-2222-4222-8222-222222222222"
LOCATION_C = "cccccccc-3333-4333-8333-333333333333"
LOCATION_D = "dddddddd-4444-4444-8444-444444444444"
LOCATION_E = "eeeeeeee-5555-4555-8555-555555555555"


def _migrated_engine(tmp_path: Path) -> sa.Engine:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.engine import create_sqlite_engine
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    database_path = tmp_path / "media-catalog-repository.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    return create_sqlite_engine(database_path)


def _repository(tmp_path: Path):
    from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
    from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
    from kronika.infrastructure.persistence.media_catalog_repository import SqliteMediaCatalogRepository

    engine = _migrated_engine(tmp_path)
    device_repository = SqliteDeviceRepository(engine)
    library_repository = SqliteLibraryRepository(engine)
    first_library = _register_library(
        device_repository,
        library_repository,
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "/catalog/a",
    )
    second_library = _register_library(
        device_repository,
        library_repository,
        "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        "/catalog/b",
    )
    return SqliteMediaCatalogRepository(engine), engine, first_library, second_library


def _register_library(device_repository, library_repository, library_id: str, path: str) -> LibraryId:
    device = Device(id=DeviceId.new(), display_name=f"Device {path}")
    device_repository.add(device)
    library = Library(
        id=LibraryId.from_string(library_id),
        device_id=device.id,
        display_name=f"Library {path}",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path),
    )
    library_repository.add(library)
    return library.id


def _seed_catalog(engine: sa.Engine, first_library: LibraryId, second_library: LibraryId) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) VALUES "
                "(:a, 'video', 100, 101), "
                "(:b, 'animated_image', 300, 301), "
                "(:c, 'video', 300, 302), "
                "(:d, 'video', 50, 51)"
            ),
            {"a": MEDIA_A, "b": MEDIA_B, "c": MEDIA_C, "d": MEDIA_D},
        )
        connection.execute(
            text(
                "INSERT INTO physical_media_locations "
                "(id, media_id, library_id, relative_path, availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
                "VALUES "
                "(:la, :a, :library_a, 'clips/zeta.mp4', 'available', 1000, 10000, 1, 1), "
                "(:lb, :b, :library_a, 'gifs/reaction.gif', 'offline', 2000, 20000, 1, 1), "
                "(:lc, :c, :library_b, 'clips/alpha.mp4', 'available', 3000, 30000, 1, 1), "
                "(:ld, :c, :library_a, 'clips/beta.mp4', 'missing', NULL, NULL, 1, 1), "
                "(:le, :d, :library_a, 'clips/percent%_literal.mp4', 'available', 4000, 40000, 1, 1)"
            ),
            {
                "la": LOCATION_A,
                "lb": LOCATION_B,
                "lc": LOCATION_C,
                "ld": LOCATION_D,
                "le": LOCATION_E,
                "a": MEDIA_A,
                "b": MEDIA_B,
                "c": MEDIA_C,
                "d": MEDIA_D,
                "library_a": first_library.to_string(),
                "library_b": second_library.to_string(),
            },
        )
        connection.execute(
            text(
                "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) VALUES "
                "('mathematics', 'Math', 1, 1), "
                "('compression', 'Compression', 1, 1), "
                "('meme', 'Meme', 1, 1), "
                "('reaction', 'Reaction', 1, 1)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO media_metadata (media_id, display_title, created_at_ms, updated_at_ms) VALUES "
                "(:a, 'Reinventing 100%_ Entropy', 10, 11), "
                "(:b, 'reaction meme', 10, 12), "
                "(:d, NULL, 10, 13)"
            ),
            {"a": MEDIA_A, "b": MEDIA_B, "d": MEDIA_D},
        )
        connection.execute(
            text(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES "
                "(:a, 'compression', 0), "
                "(:a, 'mathematics', 1), "
                "(:b, 'meme', 0), "
                "(:b, 'reaction', 1), "
                "(:d, 'compression', 0)"
            ),
            {"a": MEDIA_A, "b": MEDIA_B, "d": MEDIA_D},
        )


def _query(
    *,
    q: str | None = None,
    tags: tuple[str, ...] = (),
    limit: int = 24,
    offset: int = 0,
) -> MediaCatalogQuery:
    return MediaCatalogQuery(
        q=q,
        tag_keys=tuple(CanonicalTagKey(tag) for tag in tags),
        limit=limit,
        offset=offset,
        collection_key=None,
        access_scope=RecordAccessScope(
            kind=AccessScopeKind.ADMINISTRATOR,
            owner_login_key="admin",
        ),
    )


def test_unfiltered_listing_order_metadata_tags_locations_and_pagination(tmp_path: Path) -> None:
    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    try:
        page = repository.list_media(_query(limit=3, offset=0))
    finally:
        engine.dispose()

    assert page.total == 4
    assert [item.media_id for item in page.items] == [MEDIA_B, MEDIA_C, MEDIA_A]
    assert page.items[1].display_title is None
    assert page.items[1].tags == ()
    assert len(page.items[1].locations) == 2
    assert [
        (location.library_id, location.relative_path, location.location_id)
        for location in page.items[1].locations
    ] == [
        (first_library.to_string(), "clips/beta.mp4", LOCATION_D),
        (second_library.to_string(), "clips/alpha.mp4", LOCATION_C),
    ]
    assert [(tag.key, tag.display_name, tag.position) for tag in page.items[2].tags] == [
        ("compression", "Compression", 0),
        ("mathematics", "Math", 1),
    ]
    assert page.items[0].media_kind == "animated_image"
    assert page.items[0].locations[0].availability == "offline"
    assert page.items[0].locations[0].observed_size_bytes == 2000
    assert page.items[0].locations[0].observed_mtime_ns == 20000


def test_stable_media_id_tie_breaker_for_equal_created_time(tmp_path: Path) -> None:
    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    try:
        page = repository.list_media(_query(limit=2, offset=0))
    finally:
        engine.dispose()

    assert [item.media_id for item in page.items] == [MEDIA_B, MEDIA_C]


def test_literal_percent_underscore_and_case_insensitive_title_search(tmp_path: Path) -> None:
    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    try:
        literal = repository.list_media(_query(q="100%_"))
        case_insensitive = repository.list_media(_query(q="REACTION"))
        wildcard = repository.list_media(_query(q="%"))
    finally:
        engine.dispose()

    assert [item.media_id for item in literal.items] == [MEDIA_A]
    assert [item.media_id for item in case_insensitive.items] == [MEDIA_B]
    assert [item.media_id for item in wildcard.items] == [MEDIA_A]
    assert all(item.display_title is not None for item in literal.items + case_insensitive.items)


def test_tag_filtering_uses_and_semantics_and_duplicate_keys_do_not_change_results(
    tmp_path: Path,
) -> None:
    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    try:
        one_tag = repository.list_media(_query(tags=("compression",)))
        two_tags = repository.list_media(_query(tags=("compression", "mathematics")))
        duplicate_tags = repository.list_media(
            MediaCatalogQuery(
                q=None,
                tag_keys=(CanonicalTagKey("compression"), CanonicalTagKey("compression")),
                limit=24,
                offset=0,
                collection_key=None,
                access_scope=RecordAccessScope(
                    kind=AccessScopeKind.ADMINISTRATOR,
                    owner_login_key="admin",
                ),
            )
        )
        unknown = repository.list_media(_query(tags=("unknown-tag",)))
    finally:
        engine.dispose()

    assert [item.media_id for item in one_tag.items] == [MEDIA_A, MEDIA_D]
    assert [item.media_id for item in two_tags.items] == [MEDIA_A]
    assert MEDIA_D not in [item.media_id for item in two_tags.items]
    assert [item.media_id for item in duplicate_tags.items] == [MEDIA_A, MEDIA_D]
    assert unknown.total == 0
    assert unknown.items == ()


def test_combined_title_tag_filter_total_before_pagination_and_no_join_duplicates(
    tmp_path: Path,
) -> None:
    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    try:
        first_page = repository.list_media(_query(tags=("compression",), limit=1, offset=0))
        second_page = repository.list_media(_query(tags=("compression",), limit=1, offset=1))
        combined = repository.list_media(_query(q="entropy", tags=("compression",), limit=10, offset=0))
    finally:
        engine.dispose()

    assert first_page.total == 2
    assert second_page.total == 2
    assert [item.media_id for item in first_page.items] == [MEDIA_A]
    assert [item.media_id for item in second_page.items] == [MEDIA_D]
    assert [item.media_id for item in combined.items] == [MEDIA_A]
    assert len({item.media_id for item in first_page.items + second_page.items}) == 2


def test_companion_audience_query_plan_executes_without_a_new_index(
    tmp_path: Path,
) -> None:
    from sqlalchemy import text as sql_text

    from kronika.infrastructure.persistence.media_catalog_repository import (
        _filtered_media_select,
    )

    repository, engine, first_library, second_library = _repository(tmp_path)
    _seed_catalog(engine, first_library, second_library)
    query = MediaCatalogQuery(
        q=None,
        tag_keys=(),
        limit=24,
        offset=0,
        collection_key=None,
        content_category="meme",
        companion_audience_login_key="owner@example.com",
        companion_kinds=("image", "animated_image", "video"),
        access_scope=RecordAccessScope(
            kind=AccessScopeKind.MEMBER,
            owner_login_key="owner@example.com",
        ),
    )
    try:
        compiled = _filtered_media_select(query, ()).compile(
            compile_kwargs={"literal_binds": True}
        )
        with engine.connect() as connection:
            plan_rows = connection.execute(
                sql_text(f"EXPLAIN QUERY PLAN {compiled}")
            ).fetchall()
        page = repository.list_media(query)
    finally:
        engine.dispose()

    plan = " ".join(str(column) for row in plan_rows for column in row).lower()
    assert plan_rows
    assert "scan" in plan or "search" in plan
    assert page.total == 0
    assert page.items == ()

