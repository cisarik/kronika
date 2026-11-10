"""Timeline union, authorization parity, ordering, snapshot and query budget."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event

from kronika.application.ports.timeline import TimelinePageQuery, TimelineUnavailableError
from kronika.application.records import RecordService
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN, IdentityContext
from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordId,
    RecordKind,
)
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_immediate_transaction,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import SqliteRecordRepository
from kronika.infrastructure.persistence.timeline_repository import SqliteTimelineReader
from tests.support.record_access import synthetic_identity

_MEDIA = "11111111-1111-4111-8111-111111111111"
_OTHER = "11111111-1111-4111-8111-111111111112"
_LOCATION = "22222222-2222-4222-8222-222222222221"
_LOCATION_B = "22222222-2222-4222-8222-222222222222"


def _engine(tmp_path: Path):
    settings = KronikaSettings(database_path=tmp_path / "catalog.sqlite3", _env_file=None)
    upgrade_database_to_head(settings)
    return create_sqlite_engine(settings.database_path)


def _connect(tmp_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(tmp_path / "catalog.sqlite3")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _library(connection: sqlite3.Connection) -> None:
    connection.execute(
        "INSERT INTO devices (id, display_name) VALUES "
        "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Synthetic')"
    )
    connection.execute(
        "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
        "VALUES ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', "
        "'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Synthetic', 'posix', '/tmp/synthetic')"
    )


def _media(
    connection: sqlite3.Connection,
    media_id: str,
    *,
    created_at_ms: int,
    kind: str = "video",
    title: str | None = "Title",
) -> None:
    connection.execute(
        "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, ?, ?)",
        (media_id, kind, created_at_ms, created_at_ms),
    )
    if title is not None:
        connection.execute(
            "INSERT INTO media_metadata (media_id, display_title, description, "
            "content_category, acquisition_source, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, 'Synthetic description', 'general', 'manual_upload', ?, ?)",
            (media_id, title, created_at_ms, created_at_ms),
        )


def _location(connection: sqlite3.Connection, location_id: str, media_id: str) -> None:
    connection.execute(
        "INSERT INTO physical_media_locations ("
        "id, media_id, library_id, relative_path, availability, observed_size_bytes, "
        "observed_mtime_ns, created_at_ms, updated_at_ms) VALUES "
        "(?, ?, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', ?, 'available', 8, 8, 1, 1)",
        (location_id, media_id, f"{location_id}.mp4"),
    )


def _request(
    connection: sqlite3.Connection,
    operation_id: str,
    *,
    owner: str = "alice",
    state: str = "admitted",
    prompt: str = "Question?",
    admitted_at_ms: int = 50,
    record_id: str | None = None,
) -> None:
    connection.execute(
        """
        INSERT INTO research_requests (
            operation_id, owner_login_key, client_request_id, request_fingerprint,
            kind, prompt_text, prompt_utf8_bytes, lifecycle_state,
            provider_id, model_id, configuration_version, reasoning_effort,
            max_tool_calls, max_output_tokens, deadline_seconds,
            reservation_micro_usd, tool_allowlist_json, background,
            prompt_max_utf8_bytes, answer_max_utf8_bytes, citation_count_max,
            record_id, cleanup_state, accounting_state,
            created_at_ms, admitted_at_ms, updated_at_ms
        ) VALUES (
            ?, ?, ?, ?, 'search', ?, ?, ?,
            'openai-responses', 'gpt-5.5-2026-04-23', '3', 'low',
            3, 4096, 180, 500000, '["web_search"]', 1,
            16384, 2097152, 200,
            ?, 'not_required', 'reserved', ?, ?, ?
        )
        """,
        (
            operation_id,
            owner,
            operation_id,
            operation_id.encode("utf-8").hex().ljust(64, "a")[:64],
            prompt,
            len(prompt.encode("utf-8")),
            state,
            record_id,
            admitted_at_ms,
            admitted_at_ms,
            admitted_at_ms,
        ),
    )


def _document(operation_id: str, created_at_ms: int = 70) -> CompletedDocument:
    return CompletedDocument(
        document_id=DocumentId.new(),
        operation_id=operation_id,
        kind=RecordKind.SEARCH,
        question_text="Before save",
        answer_text="Complete answer",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=created_at_ms,
        completed_at_ms=created_at_ms + 1,
    )


def _page(reader: SqliteTimelineReader, identity: IdentityContext, **filters: object):
    return reader.read_page(identity, TimelinePageQuery(limit=24, offset=0, **filters))  # type: ignore[arg-type]


def _select_count(engine, call) -> tuple[object, int, str]:
    statements: list[str] = []

    def before(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        text = " ".join(statement.split())
        if text[:6].upper() == "SELECT":
            statements.append(text)

    event.listen(engine, "before_cursor_execute", before)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", before)
    joined = "\n".join(statements)
    return result, len(statements), joined


def test_timeline_union_deduplicates_media_and_saved_requests(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    alice = synthetic_identity("alice")
    try:
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _media(connection, _MEDIA, created_at_ms=10, title=None)
            _location(connection, _LOCATION, _MEDIA)
            _location(connection, _LOCATION_B, _MEDIA)
            _request(connection, "op-open", state="running", admitted_at_ms=30)
            connection.commit()
        finally:
            connection.close()
        repository = SqliteRecordRepository(engine)

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333331"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=10,
            )

        run_in_immediate_transaction(engine, bind)
        detail = RecordService(repository).create_completed_document(
            alice, _document("op-saved")
        )
        connection = _connect(tmp_path)
        try:
            _request(
                connection,
                "op-saved",
                state="validating",
                admitted_at_ms=40,
                record_id=detail.summary.record_id,
                prompt="Saved question",
            )
            connection.commit()
        finally:
            connection.close()
        page = _page(reader, alice)
        identities = [item.item_id for item in page.items]
        assert identities.count(f"media:{_MEDIA}") == 1
        assert identities.count("question:op-saved") == 1
        assert identities.count("question:op-open") == 1
        assert page.total == 3
        media = next(item for item in page.items if item.media_id == _MEDIA)
        assert media.status == "not_requested"
        assert media.display_title == "Untitled media"
        assert media.record_id == "33333333-3333-4333-8333-333333333331"
        saved = next(item for item in page.items if item.operation_id == "op-saved")
        assert saved.status == "saved"
        assert saved.record_id == detail.summary.record_id
    finally:
        dispose_engine(engine)


def test_timeline_item_identity_survives_binding_and_save(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    repository = SqliteRecordRepository(engine)
    reader = SqliteTimelineReader(engine)
    alice = synthetic_identity("alice")
    try:
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _media(connection, _MEDIA, created_at_ms=15, title="Clip")
            _request(connection, "op-bind", admitted_at_ms=80, prompt="Before save")
            connection.commit()
        finally:
            connection.close()
        def bind_first(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333333"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=15,
            )

        run_in_immediate_transaction(engine, bind_first)
        before = _page(reader, alice)
        assert {item.item_id for item in before.items} == {
            f"media:{_MEDIA}",
            "question:op-bind",
        }
        question = next(item for item in before.items if item.operation_id == "op-bind")
        assert question.created_at_ms == 80
        detail = RecordService(repository).create_completed_document(
            alice, _document("op-bind")
        )
        connection = _connect(tmp_path)
        try:
            connection.execute(
                "UPDATE research_requests SET record_id = ?, lifecycle_state = 'saved' "
                "WHERE operation_id = 'op-bind'",
                (detail.summary.record_id,),
            )
            connection.commit()
        finally:
            connection.close()
        after = _page(reader, alice)
        assert [item.item_id for item in after.items].count(f"media:{_MEDIA}") == 1
        assert [item.item_id for item in after.items].count("question:op-bind") == 1
        saved = next(item for item in after.items if item.operation_id == "op-bind")
        assert saved.created_at_ms == 80
        assert saved.status == "saved"
    finally:
        dispose_engine(engine)


def test_timeline_sql_matches_existing_audience_policy(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    repository = SqliteRecordRepository(engine)
    alice = synthetic_identity("alice")
    bob = synthetic_identity("bob")
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    try:
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _media(connection, _MEDIA, created_at_ms=5, title="Private clip")
            _media(connection, _OTHER, created_at_ms=6, title="Legacy clip")
            connection.execute(
                "INSERT INTO media_content_publications "
                "(media_id, published_at_ms, publication_origin) "
                "VALUES (?, 6, 'legacy_backfill')",
                (_OTHER,),
            )
            _request(connection, "op-alice", owner="alice", prompt="Alice only")
            connection.commit()
        finally:
            connection.close()

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("44444444-4444-4444-8444-444444444444"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=5,
            )

        run_in_immediate_transaction(engine, bind)
        assert {item.media_id for item in _page(reader, alice).items if item.media_id} == {
            _MEDIA,
            _OTHER,
        }
        assert {item.media_id for item in _page(reader, bob).items if item.media_id} == {_OTHER}
        assert _page(reader, bob).items[0].read_decision == "legacy"
        assert {item.item_id for item in _page(reader, ada).items} >= {
            f"media:{_MEDIA}",
            f"media:{_OTHER}",
            "question:op-alice",
        }
        assert all(
            item.operation_id != "op-alice" for item in _page(reader, bob).items
        )
    finally:
        dispose_engine(engine)


def test_timeline_binding_precedes_legacy_publication_and_contributions(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    repository = SqliteRecordRepository(engine)
    bob = synthetic_identity("bob")
    try:
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _media(connection, _MEDIA, created_at_ms=5, title="Bound private")
            connection.execute(
                "INSERT INTO media_content_publications "
                "(media_id, published_at_ms, publication_origin) "
                "VALUES (?, 5, 'admin_explicit')",
                (_MEDIA,),
            )
            connection.commit()
        finally:
            connection.close()
        assert _page(reader, bob).total == 1

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("44444444-4444-4444-8444-444444444445"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=5,
            )

        run_in_immediate_transaction(engine, bind)
        assert _page(reader, bob).total == 0
        assert _page(reader, synthetic_identity("alice")).total == 1
    finally:
        dispose_engine(engine)


def test_timeline_household_uses_only_approved_labels_and_status(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice")
    bob = synthetic_identity("bob")
    try:
        created = service.create_completed_document(alice, _document("op-house"))
        candidate = service.prepare_approval(
            synthetic_identity("ada", role=ROLE_ADMIN), created.summary.record_id
        )
        service.approve(
            synthetic_identity("ada", role=ROLE_ADMIN),
            candidate,
            approved_at_ms=90,
        )
        connection = _connect(tmp_path)
        try:
            connection.execute(
                "UPDATE kronika_documents SET question_text = ? WHERE operation_id = ?",
                ("Working question that must stay hidden", "op-house"),
            )
            connection.commit()
        finally:
            connection.close()
        household = service.list_timeline(bob)
        assert household.total == 1
        assert household.items[0].display_title == "Before save"
        assert household.items[0].read_decision == "approved"
        assert household.items[0].status == "saved"
        hidden = service.list_timeline(bob, q="Working question")
        assert hidden.total == 0
        visible = service.list_timeline(bob, q="Before")
        assert visible.total == 1
        owner = service.list_timeline(alice, q="Working")
        assert owner.total == 1
        assert owner.items[0].display_title.startswith("Working question")
    finally:
        dispose_engine(engine)


def test_timeline_filters_counts_and_tie_order_use_authorized_projection(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    repository = SqliteRecordRepository(engine)
    alice = synthetic_identity("alice")
    try:
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _media(connection, _MEDIA, created_at_ms=10, title="Alpha", kind="video")
            _media(connection, _OTHER, created_at_ms=10, title="Beta", kind="image")
            _request(connection, "op-z", admitted_at_ms=10, prompt="Zed")
            _request(connection, "op-a", admitted_at_ms=10, prompt="Aye")
            connection.commit()
        finally:
            connection.close()

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333334"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=10,
            )
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333335"),
                media_id=_OTHER,
                owner_login_key="alice",
                created_at_ms=10,
            )

        run_in_immediate_transaction(engine, bind)
        page = _page(reader, alice)
        assert [item.item_id for item in page.items] == sorted(
            [item.item_id for item in page.items]
        )
        assert page.items[0].created_at_ms == 10
        assert page.total == 4
        media_only = _page(reader, alice, kind="media")
        assert media_only.total == 2
        assert _page(reader, alice, q="Aye").total == 1
        assert _page(reader, alice, status="unanalysed").total == 2
        assert _page(reader, alice, visibility="private").total == 4
        assert _page(reader, synthetic_identity("bob"), q="Aye").total == 0
    finally:
        dispose_engine(engine)


def test_timeline_count_page_and_locators_share_one_snapshot(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    ada = synthetic_identity("ada", role=ROLE_ADMIN)
    connection = _connect(tmp_path)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        _library(connection)
        _media(connection, _MEDIA, created_at_ms=10, title="Stable")
        connection.commit()
    finally:
        connection.close()
    inserted = {"done": False}

    def before(_connection, _cursor, statement, _parameters, _context, _executemany) -> None:
        text = " ".join(statement.split())
        if text[:6].upper() != "SELECT" or inserted["done"]:
            return
        if "timeline_page" not in text:
            return
        other = sqlite3.connect(tmp_path / "catalog.sqlite3")
        try:
            other.execute("PRAGMA foreign_keys=ON")
            _media(other, _OTHER, created_at_ms=99, title="Late")
            other.commit()
        finally:
            other.close()
        inserted["done"] = True

    event.listen(engine, "before_cursor_execute", before)
    try:
        page = _page(reader, ada)
    finally:
        event.remove(engine, "before_cursor_execute", before)
        dispose_engine(engine)
    assert inserted["done"] is True
    assert page.total == 1
    assert [item.media_id for item in page.items] == [_MEDIA]


def test_timeline_page_query_count_is_constant(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    alice = synthetic_identity("alice")
    try:
        _empty, empty_count, _empty_sql = _select_count(
            engine, lambda: _page(reader, alice)
        )
        assert empty_count == 2
        connection = _connect(tmp_path)
        try:
            _library(connection)
            _request(connection, "op-only", prompt="Only question")
            _media(connection, _MEDIA, created_at_ms=4, title="Video", kind="video")
            _location(connection, _LOCATION, _MEDIA)
            _media(connection, _OTHER, created_at_ms=3, title="Still", kind="image")
            connection.commit()
        finally:
            connection.close()
        repository = SqliteRecordRepository(engine)

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333336"),
                media_id=_MEDIA,
                owner_login_key="alice",
                created_at_ms=4,
            )
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string("33333333-3333-4333-8333-333333333337"),
                media_id=_OTHER,
                owner_login_key="alice",
                created_at_ms=3,
            )

        run_in_immediate_transaction(engine, bind)
        questions, question_count, question_sql = _select_count(
            engine, lambda: _page(reader, alice, kind="search")
        )
        assert question_count == 2
        assert "answer_text" not in question_sql
        assert "relative_path" not in question_sql
        assert questions.total == 1
        mixed, mixed_count, mixed_sql = _select_count(
            engine, lambda: _page(reader, alice, kind="media")
        )
        assert mixed_count == 3
        assert mixed.total == 2
        assert next(item.preview_location_id for item in mixed.items if item.media_kind == "video") == _LOCATION
        assert next(item.preview_location_id for item in mixed.items if item.media_kind == "image") is None
        assert "answer_text" not in mixed_sql
    finally:
        dispose_engine(engine)


def test_timeline_read_does_not_probe_files_or_load_answer_payloads(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    alice = synthetic_identity("alice")
    try:
        detail = RecordService(SqliteRecordRepository(engine)).create_completed_document(
            alice, _document("op-secret")
        )
        _page_result, _count, sql = _select_count(engine, lambda: _page(reader, alice))
        assert "answer_text" not in sql
        assert "Complete answer" not in sql
        assert detail.summary.record_id
        item = _page(reader, alice).items[0]
        assert item.display_title == "Before save"
        assert "Complete" not in (item.display_title or "")
    finally:
        dispose_engine(engine)


def test_saved_request_without_a_record_is_an_integrity_error(tmp_path: Path) -> None:
    engine = _engine(tmp_path)
    reader = SqliteTimelineReader(engine)
    try:
        connection = _connect(tmp_path)
        try:
            _request(connection, "op-bad", state="saved", prompt="Missing record")
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(TimelineUnavailableError):
            _page(reader, synthetic_identity("alice"))
    finally:
        dispose_engine(engine)
