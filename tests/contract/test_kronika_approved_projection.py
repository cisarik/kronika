"""Withdrawal keeps the first Timeline timestamp and the approved snapshot."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.application.records import RecordService
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from kronika.domain.records import CompletedDocument, DocumentId, RecordId, RecordKind
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine, run_in_transaction
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import (
    SqliteRecordRepository,
    note_successful_analysis,
)
from tests.support.record_access import install_synthetic_caller, synthetic_identity

_MEDIA_ID = "11111111-1111-4111-8111-111111111111"
_APPROVED_LOCATION_ID = "22222222-2222-4222-8222-222222222222"
_NEW_LOCATION_ID = "61111111-1111-4111-8111-111111111111"
_APPROVED_RUN_ID = "33333333-3333-4333-8333-333333333333"
_WORKING_RUN_ID = "44444444-4444-4444-8444-444444444444"
_DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
_LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def test_withdrawal_keeps_timeline_position_and_snapshot(tmp_path: Path) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice", role=ROLE_USER)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-timeline",
        kind=RecordKind.SEARCH,
        question_text="Timeline question",
        answer_text="Timeline answer",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=5,
        completed_at_ms=6,
    )
    try:
        created = service.create_completed_document(alice, document)
        candidate = service.prepare_approval(admin, created.summary.record_id)
        approved = service.approve(admin, candidate, approved_at_ms=9)
        first_timeline = service.list_timeline(alice).items[0]
        assert first_timeline.timeline_entered_at_ms == 9
        withdrawn = service.withdraw(
            admin,
            record_id=created.summary.record_id,
            expected_version=approved.version,
        )
        assert withdrawn.changed is True
        history = service.list_own_history(alice)
        assert history.items[0].record_id == created.summary.record_id
        assert service.list_timeline(alice).items == ()
        detail = service.read_detail(alice, created.summary.record_id)
        assert detail.summary.timeline_entered_at_ms == 9
        again = service.withdraw(
            admin,
            record_id=created.summary.record_id,
            expected_version=withdrawn.version,
        )
        assert again.changed is False
    finally:
        dispose_engine(engine)


def test_household_keeps_approved_answer_until_reapproval(tmp_path: Path) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice", role=ROLE_USER)
    household = synthetic_identity("bob", role=ROLE_USER)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-stability",
        kind=RecordKind.SEARCH,
        question_text="Approved question",
        answer_text="Answer A",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=5,
        completed_at_ms=6,
    )
    try:
        created = service.create_completed_document(alice, document)
        candidate = service.prepare_approval(admin, created.summary.record_id)
        approved = service.approve(admin, candidate, approved_at_ms=9)
        connection = sqlite3.connect(settings.database_path)
        try:
            connection.execute(
                "UPDATE kronika_documents SET answer_text = ? WHERE operation_id = ?",
                ("Answer B", document.operation_id),
            )
            connection.execute(
                "UPDATE kronika_records SET version = version + 1 WHERE id = ?",
                (created.summary.record_id,),
            )
            connection.commit()
        finally:
            connection.close()
        frozen = service.read_detail(household, created.summary.record_id)
        assert frozen.document is not None
        assert frozen.document.answer_text == "Answer A"
        assert service.list_timeline(household).items[0].timeline_entered_at_ms == 9
        current = service.read_detail(alice, created.summary.record_id)
        assert current.document is not None
        assert current.document.answer_text == "Answer B"
        refreshed = service.prepare_approval(admin, created.summary.record_id)
        again = service.approve(admin, refreshed, approved_at_ms=11)
        assert again.changed is True
        assert again.version != approved.version
        updated = service.read_detail(household, created.summary.record_id)
        assert updated.document is not None
        assert updated.document.answer_text == "Answer B"
        assert updated.summary.timeline_entered_at_ms == 9
        assert service.list_timeline(household).items[0].timeline_entered_at_ms == 9
    finally:
        dispose_engine(engine)


def _suggestion_json(*, title: str, description: str) -> str:
    return json.dumps(
        {
            "title": title,
            "description": description,
            "collection": "inbox",
            "tags": ["clip"],
            "suggested_filename": "clip.mp4",
            "confidence": 0.5,
            "evidence": ["frame"],
            "uncertainties": ["none"],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def test_household_http_reads_keep_the_approved_media_projection(tmp_path: Path) -> None:
    """Approve TitleA, then change working state; Bob still reads the snapshot."""
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        identity_map={"alice": "user", "bob": "user", "ada": "admin"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    service = RecordService(repository)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    try:
        connection = sqlite3.connect(settings.database_path)
        try:
            connection.execute(
                "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic')",
                (_DEVICE_ID,),
            )
            connection.execute(
                "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
                "VALUES (?, ?, 'Synthetic', 'posix', ?)",
                (_LIBRARY_ID, _DEVICE_ID, str(tmp_path / "library")),
            )
            connection.execute(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, 'video', 10, 10)",
                (_MEDIA_ID,),
            )
            connection.execute(
                "INSERT INTO physical_media_locations ("
                "id, media_id, library_id, relative_path, availability, "
                "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
                ") VALUES (?, ?, ?, 'approved.mp4', 'available', 8, 8, 10, 10)",
                (_APPROVED_LOCATION_ID, _MEDIA_ID, _LIBRARY_ID),
            )
            connection.execute(
                "INSERT INTO media_metadata ("
                "media_id, display_title, description, content_category, acquisition_source, "
                "created_at_ms, updated_at_ms"
                ") VALUES (?, 'TitleA', 'Approved description', 'general', 'manual_upload', 10, 10)",
                (_MEDIA_ID,),
            )
            connection.execute(
                "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
                "VALUES ('clip', 'Clip', 10, 10)"
            )
            connection.execute(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, 'clip', 0)",
                (_MEDIA_ID,),
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
                    _APPROVED_RUN_ID,
                    _MEDIA_ID,
                    _APPROVED_LOCATION_ID,
                    _suggestion_json(title="SnapshotTitle", description="Snapshot description"),
                ),
            )
            connection.commit()
        finally:
            connection.close()

        record_id = RecordId.new()

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=record_id,
                media_id=_MEDIA_ID,
                owner_login_key="alice",
                created_at_ms=10,
            )
            note_successful_analysis(connection, _MEDIA_ID, _APPROVED_RUN_ID)

        run_in_transaction(engine, bind)
        candidate = service.prepare_approval(admin, record_id.to_string())
        service.approve(admin, candidate, approved_at_ms=20)
    finally:
        dispose_engine(engine)

    connection = sqlite3.connect(settings.database_path)
    try:
        connection.execute(
            "UPDATE media_metadata SET display_title = 'TitleB', description = 'Working description', "
            "content_category = 'meme', collection_key = 'processed', processed_at_ms = 30, "
            "updated_at_ms = 30 WHERE media_id = ?",
            (_MEDIA_ID,),
        )
        connection.execute(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (?, ?, ?, 'working.mp4', 'available', 9, 9, 30, 30)",
            (_NEW_LOCATION_ID, _MEDIA_ID, _LIBRARY_ID),
        )
        connection.execute(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "analysis_profile, created_at_ms, started_at_ms, completed_at_ms, version"
            ") VALUES (?, ?, ?, 'automatic_post_catalog', 'analyzed', 1, "
            "'synthetic', 'synthetic-model', 'suggestion-v1', "
            "'framenest-media-suggestion-result-v1', ?, 'generic_media', 30, 30, 30, 1)",
            (
                _WORKING_RUN_ID,
                _MEDIA_ID,
                _NEW_LOCATION_ID,
                _suggestion_json(title="WorkingTitle", description="Working analysis description"),
            ),
        )
        connection.execute(
            "UPDATE kronika_records SET latest_successful_analysis_run_id = ? WHERE media_id = ?",
            (_WORKING_RUN_ID, _MEDIA_ID),
        )
        publications = connection.execute(
            "SELECT COUNT(*) FROM media_content_publications"
        ).fetchone()[0]
        connection.commit()
    finally:
        connection.close()
    assert publications == 0

    bob = TestClient(
        install_synthetic_caller(create_app(settings=settings), "bob", role=ROLE_USER)
    )
    alice_client = TestClient(
        install_synthetic_caller(create_app(settings=settings), "alice", role=ROLE_USER)
    )
    detail = bob.get(f"/api/media/{_MEDIA_ID}")
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["display_title"] == "TitleA"
    assert detail_body["content_category"] == "general"
    assert detail_body["description"] == "Approved description"
    assert [item["location_id"] for item in detail_body["locations"]] == [_APPROVED_LOCATION_ID]
    assert detail_body["collection_key"] is None
    metadata = bob.get(f"/api/media/{_MEDIA_ID}/metadata")
    assert metadata.status_code == 200
    metadata_body = metadata.json()
    assert metadata_body["persisted"] is True
    assert metadata_body["display_title"] == "TitleA"
    assert metadata_body["content_category"] == "general"
    assert metadata_body["description"] == "Approved description"
    assert metadata_body["collection_key"] is None
    assert metadata_body["processed_at_ms"] is None
    assert metadata_body["genres"] == []
    listing = bob.get("/api/media")
    assert listing.status_code == 200
    listed_ids = [item["media_id"] for item in listing.json()["items"]]
    assert _MEDIA_ID in listed_ids
    assert listing.json()["total"] == len(listing.json()["items"])
    processed = bob.get("/api/media", params={"collection": "processed"})
    assert _MEDIA_ID not in [item["media_id"] for item in processed.json()["items"]]
    general = bob.get("/api/media", params={"content_category": "general"})
    meme = bob.get("/api/media", params={"content_category": "meme"})
    assert _MEDIA_ID in [item["media_id"] for item in general.json()["items"]]
    assert _MEDIA_ID not in [item["media_id"] for item in meme.json()["items"]]
    content = bob.get(f"/api/media/{_MEDIA_ID}/locations/{_NEW_LOCATION_ID}/content")
    download = bob.get(f"/api/media/{_MEDIA_ID}/locations/{_NEW_LOCATION_ID}/download")
    assert content.status_code == 404
    assert content.json()["error"]["code"] == "MEDIA_CONTENT_NOT_FOUND"
    assert download.status_code == 404
    assert download.json()["error"]["code"] == "MEDIA_CONTENT_NOT_FOUND"
    suggestions = bob.get(f"/api/media/{_MEDIA_ID}/ai-suggestions")
    assert suggestions.status_code == 200
    suggestion_titles = [item["title"] for item in suggestions.json()["suggestions"]]
    assert suggestion_titles == ["SnapshotTitle"]
    assert suggestions.json()["next_cursor"] is None
    analysis = bob.get(f"/api/media/{_MEDIA_ID}/automatic-analysis")
    assert analysis.status_code == 200
    assert "WorkingTitle" not in analysis.text
    movie = bob.get(f"/api/media/{_MEDIA_ID}/movie-identification")
    assert movie.status_code == 200
    assert "WorkingTitle" not in movie.text
    owner = alice_client.get(f"/api/media/{_MEDIA_ID}")
    assert owner.status_code == 200
    assert owner.json()["display_title"] == "TitleB"
    assert owner.json()["content_category"] == "meme"
