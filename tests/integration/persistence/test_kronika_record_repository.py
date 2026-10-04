"""Repository transactions for documents: rollback and invalid persisted JSON."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path

import pytest

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


def _engine(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
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
