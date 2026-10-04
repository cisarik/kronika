"""Acquisition surfaces do not treat a bound private record as published."""

from __future__ import annotations

from pathlib import Path

from kronika.configuration import KronikaSettings
from kronika.domain.identities import MediaId
from kronika.domain.records import RecordId
from kronika.infrastructure.persistence.content_publication_repository import (
    SqliteContentPublicationRepository,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_immediate_transaction,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import SqliteRecordRepository


def test_bound_media_is_not_legacy_published_even_with_a_publication_row(
    tmp_path: Path,
) -> None:
    media_id = MediaId.new()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        def seed(connection) -> None:
            connection.exec_driver_sql(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, 'video', 1, 1)",
                (media_id.to_string(),),
            )
            connection.exec_driver_sql(
                "INSERT INTO media_content_publications "
                "(media_id, published_at_ms, publication_origin) "
                "VALUES (?, 1, 'admin_explicit')",
                (media_id.to_string(),),
            )
            SqliteRecordRepository(engine).bind_media_record(
                connection,
                record_id=RecordId.new(),
                media_id=media_id.to_string(),
                owner_login_key="alice",
                created_at_ms=1,
            )

        run_in_immediate_transaction(engine, seed)
        assert SqliteContentPublicationRepository(engine).is_published(media_id) is False
        assert (
            SqliteContentPublicationRepository(engine).bound_record_owner(media_id)
            == "alice"
        )
    finally:
        dispose_engine(engine)


def test_bound_removal_fails_before_receipt_or_cleanup(tmp_path: Path) -> None:
    import sqlite3

    from kronika.application.catalog_removal import (
        CatalogMediaRemovalService,
        CatalogRemovalBoundRecordError,
    )
    from kronika.infrastructure.persistence.catalog_removal_repository import (
        SqliteCatalogRemovalRepository,
    )

    media_id = MediaId.new()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    calls: list[str] = []

    class _Cleanup:
        def cleanup_cover(self, *, media_id: str, artifact_digest: str | None) -> str:
            calls.append("cover")
            return "complete"

        def cleanup_previews(self, *, location_ids_json: str | None) -> str:
            calls.append("preview")
            return "complete"

    try:
        def seed(connection) -> None:
            connection.exec_driver_sql(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, 'video', 1, 1)",
                (media_id.to_string(),),
            )
            SqliteRecordRepository(engine).bind_media_record(
                connection,
                record_id=RecordId.new(),
                media_id=media_id.to_string(),
                owner_login_key="alice",
                created_at_ms=1,
            )

        run_in_immediate_transaction(engine, seed)
        service = CatalogMediaRemovalService(
            SqliteCatalogRemovalRepository(engine),
            _Cleanup(),
            lambda: 1,
        )
        try:
            service.execute(
                media_id=media_id.to_string(),
                acknowledge_consequences=True,
                consequence_fingerprint="a" * 64,
                request_id="request-bound",
                actor_key="ada",
            )
        except CatalogRemovalBoundRecordError:
            pass
        else:
            raise AssertionError("bound removal was accepted")
        connection = sqlite3.connect(settings.database_path)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM media_catalog_removal_receipts"
            ).fetchone()[0]
        finally:
            connection.close()
        assert count == 0
        assert calls == []
    finally:
        dispose_engine(engine)


def test_x_requester_snapshot_hides_a_foreign_bound_media_id() -> None:
    from types import SimpleNamespace

    from kronika.application.x_acquisition import _requester_snapshot
    from kronika.domain.x_acquisition import XPostClaim

    claim = XPostClaim.new(
        submitted_url="https://x.com/a/status/123",
        now_ms=10,
        created_by_login_key="alice@example.com",
    )

    class _MediaId:
        def to_string(self) -> str:
            return "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"

    class _Value:
        def __init__(self, value: str) -> None:
            self.value = value

    asset = SimpleNamespace(
        id=_MediaId(),
        ordinal=1,
        media_type=_Value("photo"),
        state=_Value("cataloged"),
        acquired_bytes=4,
        media_id=_MediaId(),
        failure_stage=None,
        failure_code=None,
        created_at_ms=1,
        updated_at_ms=1,
    )

    class _Repository:
        def list_assets_for_post(self, post_id: object) -> tuple[object, ...]:
            return (asset,)

        def media_ids_hidden_from_requester(
            self, *, login_key: str, media_ids: tuple[str, ...]
        ) -> frozenset[str]:
            assert login_key == "alice@example.com"
            return frozenset(media_ids)

    snapshot = _requester_snapshot(claim, _Repository())
    assert snapshot.assets[0].media_id is None
    assert snapshot.x_post_id == claim.x_post_id
