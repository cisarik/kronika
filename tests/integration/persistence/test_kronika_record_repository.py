"""Repository transactions for documents: rollback and invalid persisted JSON."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest
from sqlalchemy import event

from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_USER
from kronika.application.records import RecordService
from kronika.domain.identity_access import ROLE_ADMIN
from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordConflictError,
    RecordId,
    RecordKind,
    RecordStorageIntegrityError,
    RecordValueError,
)
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_immediate_transaction,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import (
    SqliteRecordRepository,
    note_successful_analysis,
)
from tests.support.record_access import synthetic_identity


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )


def _engine(tmp_path: Path):
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    return create_sqlite_engine(settings.database_path)


def _document(operation_id: str) -> CompletedDocument:
    return CompletedDocument(
        document_id=DocumentId.new(),
        operation_id=operation_id,
        kind=RecordKind.SEARCH,
        question_text="Question",
        answer_text="Answer",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=1,
        completed_at_ms=2,
    )


def test_document_insert_rolls_back_with_the_caller_transaction(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteRecordRepository(engine)
    document = _document("search-op-rollback")
    try:
        def operation(connection) -> None:
            connection.exec_driver_sql(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'video', 1, 1)"
            )
            repository.bind_media_record(
                connection,
                record_id=RecordId.new(),
                media_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                owner_login_key="alice",
                created_at_ms=1,
            )
            raise RuntimeError("injected failure")

        with pytest.raises(RuntimeError, match="injected failure"):
            run_in_immediate_transaction(engine, operation)
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            count = connection.execute("SELECT COUNT(*) FROM kronika_records").fetchone()[0]
        finally:
            connection.close()
        assert count == 0
        created = repository.create_completed_document(
            document,
            owner_login_key="alice",
            record_id=RecordId.new(),
        )
        assert created.document is not None
    finally:
        dispose_engine(engine)


def test_invalid_persisted_document_fails_closed(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteRecordRepository(engine)
    document = _document("search-op-invalid")
    try:
        created = repository.create_completed_document(
            document,
            owner_login_key="alice",
            record_id=RecordId.new(),
        )
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE kronika_documents SET citations_json = ? WHERE operation_id = ?",
                ("not-json", document.operation_id),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RecordStorageIntegrityError):
            repository.read_detail(
                synthetic_identity("alice", role=ROLE_USER),
                created.summary.record_id,
            )
    finally:
        dispose_engine(engine)


def test_stale_version_and_digest_leave_no_partial_approval(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice", role=ROLE_USER)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    try:
        created = service.create_completed_document(
            alice, _document("search-op-stale")
        )
        candidate = service.prepare_approval(admin, created.summary.record_id)
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE kronika_documents SET answer_text = ? WHERE operation_id = ?",
                ("Changed before approval", "search-op-stale"),
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(RecordConflictError):
            service.approve(admin, candidate, approved_at_ms=8)
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            stored = connection.execute(
                "SELECT approved_projection_json, version FROM kronika_records"
            ).fetchone()
        finally:
            connection.close()
        assert stored[0] is None
        fresh = service.prepare_approval(admin, created.summary.record_id)
        approved = service.approve(admin, fresh, approved_at_ms=9)
        with pytest.raises(RecordConflictError):
            service.withdraw(
                admin,
                record_id=created.summary.record_id,
                expected_version=approved.version - 1,
            )
        assert service.list_timeline(alice).items[0].timeline_entered_at_ms == 9
    finally:
        dispose_engine(engine)


def test_racing_approve_and_withdraw_keep_one_consistent_state(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice", role=ROLE_USER)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    try:
        created = service.create_completed_document(
            alice, _document("search-op-race")
        )
        candidate = service.prepare_approval(admin, created.summary.record_id)
        approved = service.approve(admin, candidate, approved_at_ms=9)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def approve_again() -> None:
            barrier.wait()
            try:
                service.approve(admin, candidate, approved_at_ms=12)
                outcomes.append("approve")
            except RecordConflictError:
                outcomes.append("approve-conflict")

        def withdraw() -> None:
            barrier.wait()
            try:
                service.withdraw(
                    admin,
                    record_id=created.summary.record_id,
                    expected_version=approved.version,
                )
                outcomes.append("withdraw")
            except RecordConflictError:
                outcomes.append("withdraw-conflict")

        threads = (
            threading.Thread(target=approve_again),
            threading.Thread(target=withdraw),
        )
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert sorted(outcomes) in (
            ["approve-conflict", "withdraw"],
            ["approve", "withdraw-conflict"],
        )
        detail = service.read_detail(alice, created.summary.record_id)
        timeline = service.list_timeline(alice).items
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            row = connection.execute(
                "SELECT version, approved_projection_json, timeline_entered_at_ms "
                "FROM kronika_records"
            ).fetchone()
        finally:
            connection.close()
        assert row[1] is not None
        assert row[2] == 9
        assert detail.summary.timeline_entered_at_ms == 9
        assert len(timeline) in (0, 1)
    finally:
        dispose_engine(engine)


def _evidence() -> CompletionEvidence:
    return CompletionEvidence(
        provider_terminal=True,
        answer_complete=True,
        web_search_executed=True,
        refusal_marker=False,
        incomplete_marker=False,
    )


def _save_document(
    service: RecordService,
    owner,
    *,
    operation_id: str,
    kind: RecordKind,
    question: str,
    answer: str,
    created_at_ms: int,
    record_id: str,
) -> str:
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id=operation_id,
        kind=kind,
        question_text=question,
        answer_text=answer,
        citations=(),
        evidence=_evidence(),
        created_at_ms=created_at_ms,
        completed_at_ms=created_at_ms + 1,
    )
    created = service.create_completed_document(
        owner,
        document,
        record_id=RecordId.from_string(record_id),
    )
    return created.summary.record_id


def test_list_filters_apply_before_count_and_keep_order(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice")
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    try:
        for index in range(25):
            _save_document(
                service,
                alice,
                operation_id=f"search-page-{index:02d}",
                kind=RecordKind.SEARCH,
                question="Question " + ("Q" * 280 if index == 0 else str(index)),
                answer="SECRET-ANSWER-TEXT",
                created_at_ms=1_000 + index,
                record_id=f"10000000-0000-4000-8000-{index:012d}",
            )
        _save_document(
            service,
            alice,
            operation_id="research-page-extra",
            kind=RecordKind.RESEARCH,
            question="hello   <script>alert(1)</script>",
            answer="SECRET-ANSWER-TEXT",
            created_at_ms=9_000,
            record_id="20000000-0000-4000-8000-000000000001",
        )
        _save_document(
            service,
            ada,
            operation_id="search-admin-own",
            kind=RecordKind.SEARCH,
            question="Admin private question",
            answer="SECRET-ANSWER-TEXT",
            created_at_ms=8_000,
            record_id="20000000-0000-4000-8000-000000000002",
        )
        with pytest.raises(RecordValueError):
            service.list_timeline(alice, content_category="meme")
        page = service.list_own_history(alice, kind="search", limit=24, offset=0)
        assert page.total == 25
        assert len(page.items) == 24
        assert page.items[0].record_id.endswith("000000000024")
        assert all(item.kind is RecordKind.SEARCH for item in page.items)
        assert all("SECRET-ANSWER-TEXT" not in (item.display_title or "") for item in page.items)
        oldest = service.list_own_history(alice, kind="search", limit=24, offset=24)
        assert oldest.total == 25
        assert len(oldest.items) == 1
        assert oldest.items[0].record_id.endswith("000000000000")
        assert oldest.items[0].display_title is not None
        assert len(oldest.items[0].display_title) == 240
        assert oldest.items[0].display_title.endswith("…")
        hostile = service.list_own_history(alice, kind="research")
        assert hostile.total == 1
        assert hostile.items[0].display_title == "hello <script>alert(1)</script>"
        assert service.list_own_history(ada).total == 1
        assert service.list_admin_inventory(ada, visibility="private").total == 27
        assert service.list_timeline(alice).total == 0
        assert service.list_timeline(ada).total == 0
    finally:
        dispose_engine(engine)


def test_timeline_titles_stay_on_the_approved_projection(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    alice = synthetic_identity("alice")
    bob = synthetic_identity("bob")
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    media_id = "11111111-1111-4111-8111-111111111111"
    location_id = "22222222-2222-4222-8222-222222222222"
    run_id = "33333333-3333-4333-8333-333333333333"
    device_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    library_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    record_id = "44444444-4444-4444-8444-444444444444"
    try:
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            connection.execute(
                "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic')",
                (device_id,),
            )
            connection.execute(
                "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
                "VALUES (?, ?, 'Synthetic', 'posix', ?)",
                (library_id, device_id, str(tmp_path / "library")),
            )
            connection.execute(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, 'video', 10, 10)",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO physical_media_locations ("
                "id, media_id, library_id, relative_path, availability, "
                "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, ?, 'approved.mp4', 'available', 8, 8, 10, 10)",
                (location_id, media_id, library_id),
            )
            connection.execute(
                "INSERT INTO media_metadata ("
                "media_id, display_title, description, content_category, acquisition_source, "
                "created_at_ms, updated_at_ms"
                ") VALUES (?, 'TitleA', 'Approved description', 'general', 'manual_upload', 10, 10)",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
                "VALUES ('clip', 'Clip', 10, 10)"
            )
            connection.execute(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, 'clip', 0)",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO media_analysis_runs ("
                "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
                "provider_id, model_id, prompt_version, result_schema_version, result_json, "
                "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
                ") VALUES (?, ?, ?, 'automatic_post_catalog', 'analyzed', 1, "
                "'synthetic', 'synthetic-model', 'suggestion-v1', "
                "'framenest-media-suggestion-result-v1', ?, 'generic_media', 10, 10, 10, 1)",
                (
                    run_id,
                    media_id,
                    location_id,
                    json.dumps(
                        {
                            "title": "SnapshotTitle",
                            "description": "Snapshot description",
                            "collection": "inbox",
                            "tags": ["clip"],
                            "suggested_filename": "clip.mp4",
                            "confidence": 0.5,
                            "evidence": ["frame"],
                            "uncertainties": ["none"],
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string(record_id),
                media_id=media_id,
                owner_login_key="alice",
                created_at_ms=10,
            )
            note_successful_analysis(connection, media_id, run_id)

        run_in_immediate_transaction(engine, bind)
        candidate = service.prepare_approval(ada, record_id)
        approved = service.approve(ada, candidate, approved_at_ms=20)
        connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            connection.execute(
                "UPDATE media_metadata SET display_title = 'TitleB', content_category = 'meme', "
                "updated_at_ms = 30 WHERE media_id = ?",
                (media_id,),
            )
            connection.commit()
        finally:
            connection.close()
        for caller in (alice, bob, ada):
            timeline = service.list_timeline(caller)
            assert timeline.total == 1
            assert timeline.items[0].display_title == "TitleA"
            assert timeline.items[0].content_category == "general"
            assert timeline.items[0].read_decision == "approved"
            assert service.list_timeline(caller, kind="media", content_category="general").total == 1
            assert service.list_timeline(caller, kind="media", content_category="meme").total == 0
        own = service.list_own_history(alice)
        assert own.items[0].display_title == "TitleB"
        assert own.items[0].content_category == "meme"
        withdrawn = service.withdraw(
            ada,
            record_id=record_id,
            expected_version=approved.version,
        )
        assert withdrawn.changed is True
        for caller in (alice, bob, ada):
            assert service.list_timeline(caller).total == 0
    finally:
        dispose_engine(engine)


_PREVIEW_DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_PREVIEW_LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
_PREVIEW_TAG_KEY = "clip"


def _catalog_many(
    settings: KronikaSettings,
    entries: list[tuple[str, str, tuple[tuple[str, str], ...], str]],
) -> None:
    """Insert synthetic catalog rows for preview-locator tests."""
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT OR IGNORE INTO devices (id, display_name) VALUES (?, 'Synthetic')",
            (_PREVIEW_DEVICE_ID,),
        )
        connection.execute(
            "INSERT OR IGNORE INTO libraries ("
            "id, device_id, display_name, path_flavor, root_path"
            ") VALUES (?, ?, 'Synthetic', 'posix', ?)",
            (_PREVIEW_LIBRARY_ID, _PREVIEW_DEVICE_ID, "/tmp/synthetic-library"),
        )
        connection.execute(
            "INSERT OR IGNORE INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES (?, 'Clip', 10, 10)",
            (_PREVIEW_TAG_KEY,),
        )
        for media_id, media_kind, locations, title in entries:
            connection.execute(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, 10, 10)",
                (media_id, media_kind),
            )
            for index, (location_id, availability) in enumerate(locations):
                connection.execute(
                    "INSERT INTO physical_media_locations ("
                    "id, media_id, library_id, relative_path, availability, "
                    "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                    ") VALUES (?, ?, ?, ?, ?, 8, 8, 10, 10)",
                    (
                        location_id,
                        media_id,
                        _PREVIEW_LIBRARY_ID,
                        f"{media_id}-{index}.mp4",
                        availability,
                    ),
                )
            connection.execute(
                "INSERT INTO media_metadata ("
                "media_id, display_title, description, content_category, acquisition_source, "
                "created_at_ms, updated_at_ms"
                ") VALUES (?, ?, 'Synthetic description', 'general', 'manual_upload', 10, 10)",
                (media_id, title),
            )
            connection.execute(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                "VALUES (?, ?, 0)",
                (media_id, _PREVIEW_TAG_KEY),
            )
        connection.commit()
    finally:
        connection.close()


def _catalog_media(
    settings: KronikaSettings,
    *,
    media_id: str,
    media_kind: str,
    locations: tuple[tuple[str, str], ...],
    title: str,
) -> None:
    _catalog_many(settings, [(media_id, media_kind, locations, title)])


def _insert_location(
    settings: KronikaSettings,
    *,
    media_id: str,
    location_id: str,
    availability: str = "available",
    index: int = 90,
) -> None:
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, ?, ?, 8, 8, 10, 10)",
            (
                location_id,
                media_id,
                _PREVIEW_LIBRARY_ID,
                f"{media_id}-{index}.mp4",
                availability,
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _bind_media_record(
    engine,
    repository: SqliteRecordRepository,
    *,
    record_id: str,
    media_id: str,
    owner_login_key: str = "alice",
    created_at_ms: int = 10,
) -> None:
    def bind(connection) -> None:
        repository.bind_media_record(
            connection,
            record_id=RecordId.from_string(record_id),
            media_id=media_id,
            owner_login_key=owner_login_key,
            created_at_ms=created_at_ms,
        )

    run_in_immediate_transaction(engine, bind)


def _insert_analysis_run(
    settings: KronikaSettings,
    *,
    run_id: str,
    media_id: str,
    location_id: str,
    title: str,
) -> None:
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
            ") VALUES (?, ?, ?, 'automatic_post_catalog', 'analyzed', 1, "
            "'synthetic', 'synthetic-model', 'suggestion-v1', "
            "'framenest-media-suggestion-result-v1', ?, 'generic_media', 10, 10, 10, 1)",
            (
                run_id,
                media_id,
                location_id,
                json.dumps(
                    {
                        "title": title,
                        "description": "Synthetic description",
                        "collection": "inbox",
                        "tags": [_PREVIEW_TAG_KEY],
                        "suggested_filename": "clip.mp4",
                        "confidence": 0.5,
                        "evidence": ["frame"],
                        "uncertainties": ["none"],
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        connection.commit()
    finally:
        connection.close()


def _insert_media_record_row(
    settings: KronikaSettings,
    *,
    record_id: str,
    media_id: str,
    owner_login_key: str = "alice",
    created_at_ms: int = 10,
) -> None:
    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "INSERT INTO kronika_records ("
            "id, kind, owner_login_key, visibility, media_id, created_at_ms, version"
            ") VALUES (?, 'media', ?, 'private', ?, ?, 1)",
            (record_id, owner_login_key, media_id, created_at_ms),
        )
        connection.commit()
    finally:
        connection.close()


def test_history_preview_locator_selects_one_current_available_location(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    alice = synthetic_identity("alice")
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    media_id = "61000000-0000-4000-8000-000000000001"
    record_id = "62000000-0000-4000-8000-000000000001"
    first_inserted = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    lexically_first = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
    unavailable_earlier = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    try:
        _catalog_media(
            settings,
            media_id=media_id,
            media_kind="video",
            locations=(
                (first_inserted, "available"),
                (lexically_first, "available"),
                (unavailable_earlier, "missing"),
            ),
            title="Synthetic current title",
        )
        _insert_media_record_row(settings, record_id=record_id, media_id=media_id)
        owned = service.list_own_history(alice)
        assert owned.items[0].preview_location_id == lexically_first
        detail = service.read_detail(alice, record_id)
        assert detail.summary.preview_location_id is None
        inventory = service.list_admin_inventory(ada)
        assert inventory.items[0].preview_location_id is None
    finally:
        dispose_engine(engine)


def test_timeline_preview_locator_uses_only_approved_locations_for_every_caller(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    alice = synthetic_identity("alice")
    bob = synthetic_identity("bob")
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    media_id = "61000000-0000-4000-8000-000000000002"
    record_id = "62000000-0000-4000-8000-000000000002"
    run_id = "63000000-0000-4000-8000-000000000002"
    approved_location = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
    working_only = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    try:
        _catalog_media(
            settings,
            media_id=media_id,
            media_kind="video",
            locations=((approved_location, "available"),),
            title="Synthetic approved title",
        )
        _bind_media_record(
            engine,
            repository,
            record_id=record_id,
            media_id=media_id,
        )

        def analyse(connection) -> None:
            note_successful_analysis(connection, media_id, run_id)

        _insert_analysis_run(
            settings,
            run_id=run_id,
            media_id=media_id,
            location_id=approved_location,
            title="Synthetic approved title",
        )
        run_in_immediate_transaction(engine, analyse)
        candidate = service.prepare_approval(ada, record_id)
        service.approve(ada, candidate, approved_at_ms=20)
        _insert_location(
            settings,
            media_id=media_id,
            location_id=working_only,
            availability="available",
        )
        for caller in (alice, bob, ada):
            timeline = service.list_timeline(caller)
            assert timeline.total == 1
            assert timeline.items[0].preview_location_id == approved_location
        current = service.list_own_history(alice)
        assert current.items[0].preview_location_id == working_only
    finally:
        dispose_engine(engine)


def test_preview_locators_are_null_for_nonmedia_still_images_and_unavailable_locations(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    alice = synthetic_identity("alice")
    still_media = "61000000-0000-4000-8000-000000000003"
    still_record = "62000000-0000-4000-8000-000000000003"
    unavailable_media = "61000000-0000-4000-8000-000000000004"
    unavailable_record = "62000000-0000-4000-8000-000000000004"
    try:
        _catalog_media(
            settings,
            media_id=still_media,
            media_kind="image",
            locations=(("dddddddd-dddd-4ddd-8ddd-dddddddddddd", "available"),),
            title="Synthetic still",
        )
        _insert_media_record_row(
            settings,
            record_id=still_record,
            media_id=still_media,
        )
        _catalog_media(
            settings,
            media_id=unavailable_media,
            media_kind="animated_image",
            locations=(("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee", "offline"),),
            title="Synthetic offline",
        )
        _insert_media_record_row(
            settings,
            record_id=unavailable_record,
            media_id=unavailable_media,
        )
        _save_document(
            service,
            alice,
            operation_id="search-null-preview",
            kind=RecordKind.SEARCH,
            question="Synthetic question",
            answer="Synthetic answer",
            created_at_ms=5,
            record_id="62000000-0000-4000-8000-000000000005",
        )
        history = service.list_own_history(alice)
        by_record = {item.record_id: item for item in history.items}
        assert by_record[still_record].preview_location_id is None
        assert by_record[unavailable_record].preview_location_id is None
        assert by_record["62000000-0000-4000-8000-000000000005"].kind is RecordKind.SEARCH
        assert by_record["62000000-0000-4000-8000-000000000005"].preview_location_id is None
        assert all(item.preview_location_id is None for item in history.items)
    finally:
        dispose_engine(engine)


def test_record_preview_locator_queries_are_constant_per_page(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    alice = synthetic_identity("alice")
    entries = []
    for index in range(100):
        media_id = f"61000000-0000-4000-8000-{index:012d}"
        location_id = f"63000000-0000-4000-8000-{index:012d}"
        entries.append((media_id, "video", ((location_id, "available"),), f"Title {index:03d}"))
    try:
        _catalog_many(settings, entries)
        for index in range(100):
            media_id = f"61000000-0000-4000-8000-{index:012d}"
            record_id = f"62000000-0000-4000-8000-{index:012d}"
            _insert_media_record_row(
                settings,
                record_id=record_id,
                media_id=media_id,
                created_at_ms=1_000 + index,
            )
        _save_document(
            service,
            alice,
            operation_id="search-query-count",
            kind=RecordKind.SEARCH,
            question="Synthetic question",
            answer="Synthetic answer",
            created_at_ms=5,
            record_id="62000000-0000-4000-8000-000000000100",
        )

        def statements_for(call):
            statements: list[str] = []

            def before(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(engine, "before_cursor_execute", before)
            try:
                page = call()
            finally:
                event.remove(engine, "before_cursor_execute", before)
            return page, statements

        baseline_page, baseline = statements_for(
            lambda: service.list_own_history(alice, kind="search")
        )
        assert baseline_page.items[0].preview_location_id is None
        one_page, one = statements_for(
            lambda: service.list_own_history(alice, kind="media", limit=1)
        )
        page_24, page_24_statements = statements_for(
            lambda: service.list_own_history(alice, kind="media", limit=24)
        )
        page_100, page_100_statements = statements_for(
            lambda: service.list_own_history(alice, kind="media", limit=100)
        )
        assert len(one_page.items) == 1
        assert len(page_24.items) == 24
        assert len(page_100.items) == 100
        assert all(item.preview_location_id for item in page_100.items)
        assert len(one) == len(page_24_statements) == len(page_100_statements)
        assert len(one) == len(baseline) + 1
    finally:
        dispose_engine(engine)
