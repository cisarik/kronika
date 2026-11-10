"""Timeline HTTP contract: audience parity, filters, payload and no generation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.public_published_application import create_public_published_app
from kronika.application.records import RecordService
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from kronika.domain.records import CompletedDocument, DocumentId, RecordId, RecordKind
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_immediate_transaction,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import SqliteRecordRepository
from tests.support.record_access import install_synthetic_caller

_KEYS = {
    "item_id",
    "kind",
    "media_kind",
    "record_id",
    "media_id",
    "operation_id",
    "display_title",
    "content_category",
    "created_at_ms",
    "status",
    "visibility",
    "publication_state",
    "read_decision",
    "preview_location_id",
}


def _settings(tmp_path: Path) -> KronikaSettings:
    return KronikaSettings(
        database_path=tmp_path / "timeline-api.sqlite3",
        identity_map={
            "alice@example.com": "user",
            "bob@example.com": "user",
            "ada@example.com": "admin",
        },
        _env_file=None,
    )


def _client(app: FastAPI, login: str | None, role: str = ROLE_USER) -> TestClient:
    if login is not None:
        install_synthetic_caller(app, login, role=role)
    return TestClient(app, client=("127.0.0.1", 9))


def _document(operation_id: str, question: str) -> CompletedDocument:
    return CompletedDocument(
        document_id=DocumentId.new(),
        operation_id=operation_id,
        kind=RecordKind.SEARCH,
        question_text=question,
        answer_text="SECRET-ANSWER",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=10,
        completed_at_ms=11,
    )


def test_timeline_visibility_matches_direct_reads(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    service = RecordService(SqliteRecordRepository(engine))
    try:
        from tests.support.record_access import synthetic_identity

        alice = synthetic_identity("alice@example.com")
        created = service.create_completed_document(
            alice, _document("op-private", "Private question")
        )
        app = create_app(settings=settings)
        alice_client = _client(app, "alice@example.com")
        bob = _client(create_app(settings=settings), "bob@example.com")
        ada = _client(create_app(settings=settings), "ada@example.com", ROLE_ADMIN)
        assert alice_client.get(f"/api/records/{created.summary.record_id}").status_code == 200
        assert bob.get(f"/api/records/{created.summary.record_id}").status_code == 404
        assert ada.get(f"/api/records/{created.summary.record_id}").status_code == 200
        assert alice_client.get("/api/timeline").json()["total"] == 1
        assert bob.get("/api/timeline").json()["total"] == 0
        assert ada.get("/api/timeline").json()["total"] == 1
    finally:
        dispose_engine(engine)


def test_timeline_contract_is_bounded_sanitized_and_no_store(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        from tests.support.record_access import synthetic_identity

        RecordService(SqliteRecordRepository(engine)).create_completed_document(
            synthetic_identity("alice@example.com"),
            _document("op-shape", "Visible question"),
        )
        response = _client(create_app(settings=settings), "alice@example.com").get(
            "/api/timeline"
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "private, no-store"
        assert "etag" not in response.headers
        item = response.json()["items"][0]
        assert set(item) == _KEYS
        assert "SECRET-ANSWER" not in response.text
        assert "owner_login_key" not in item
        assert item["kind"] == "search"
        assert item["status"] == "saved"
    finally:
        dispose_engine(engine)


def test_timeline_filters_cannot_widen_identity_scope(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        from tests.support.record_access import synthetic_identity

        RecordService(SqliteRecordRepository(engine)).create_completed_document(
            synthetic_identity("alice@example.com"),
            _document("op-filter", "Alice private words"),
        )
        bob = _client(create_app(settings=settings), "bob@example.com")
        assert bob.get("/api/timeline", params={"q": "Alice"}).json()["total"] == 0
        assert bob.get("/api/timeline", params={"visibility": "private"}).json()["total"] == 0
        alice = _client(create_app(settings=settings), "alice@example.com")
        assert alice.get("/api/timeline", params={"q": "private"}).json()["total"] == 1
        assert alice.get("/api/timeline", params={"status": "nope"}).status_code == 422
        denied = alice.get("/api/timeline", params={"content_category": "meme"})
        assert denied.status_code == 422
        assert denied.headers["cache-control"] == "private, no-store"
    finally:
        dispose_engine(engine)


def test_timeline_denies_anonymous_and_is_absent_from_public_composition(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    anonymous = _client(create_app(settings=settings), None)
    denied = anonymous.get("/api/timeline")
    assert denied.status_code == 401
    assert denied.json()["error"]["code"] == "IDENTITY_REQUIRED"
    assert denied.headers["cache-control"] == "private, no-store"
    public_settings = KronikaSettings(
        database_path=tmp_path / "public.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="public_published_uds",
        uds_path=tmp_path / "public.sock",
        _env_file=None,
    )
    upgrade_database_to_head(public_settings)
    public = TestClient(create_public_published_app(public_settings))
    assert public.get("/api/timeline").status_code == 404


def test_timeline_request_lifecycle_and_saved_transition(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        connection = sqlite3.connect(settings.database_path)
        try:
            connection.execute(
                """
                INSERT INTO research_requests (
                    operation_id, owner_login_key, client_request_id, request_fingerprint,
                    kind, prompt_text, prompt_utf8_bytes, lifecycle_state,
                    provider_id, model_id, configuration_version, reasoning_effort,
                    max_tool_calls, max_output_tokens, deadline_seconds,
                    reservation_micro_usd, tool_allowlist_json, background,
                    prompt_max_utf8_bytes, answer_max_utf8_bytes, citation_count_max,
                    cleanup_state, accounting_state,
                    created_at_ms, admitted_at_ms, updated_at_ms
                ) VALUES (
                    'op-live', 'alice@example.com', 'client-live', ?,
                    'research', 'Live question', 13, 'running',
                    'openai-responses', 'gpt-5.5-2026-04-23', '3', 'low',
                    3, 4096, 180, 500000, '["web_search"]', 1,
                    16384, 2097152, 200,
                    'not_required', 'reserved', 20, 20, 20
                )
                """,
                ("cd" * 32,),
            )
            connection.commit()
        finally:
            connection.close()
        alice = _client(create_app(settings=settings), "alice@example.com")
        bob = _client(create_app(settings=settings), "bob@example.com")
        running = alice.get("/api/timeline").json()["items"][0]
        assert running["status"] == "running"
        assert running["operation_id"] == "op-live"
        assert running["record_id"] is None
        assert bob.get("/api/timeline").json()["total"] == 0
        from tests.support.record_access import synthetic_identity

        detail = RecordService(SqliteRecordRepository(engine)).create_completed_document(
            synthetic_identity("alice@example.com"),
            _document("op-live", "Live question"),
        )
        connection = sqlite3.connect(settings.database_path)
        try:
            connection.execute(
                "UPDATE research_requests SET record_id = ?, lifecycle_state = 'saved' "
                "WHERE operation_id = 'op-live'",
                (detail.summary.record_id,),
            )
            connection.commit()
        finally:
            connection.close()
        saved = alice.get("/api/timeline").json()
        assert saved["total"] == 1
        assert saved["items"][0]["status"] == "saved"
        assert saved["items"][0]["item_id"] == "question:op-live"
        assert saved["items"][0]["record_id"] == detail.summary.record_id
    finally:
        dispose_engine(engine)


def test_timeline_loading_never_calls_research_or_preview_generation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)

    class _Runtime:
        calls = 0

        def submit_pending(self):
            self.calls += 1

        def poll_once(self):
            self.calls += 1

        def release_remote_pending(self):
            self.calls += 1

    runtime = _Runtime()
    app.state.research_runtime = runtime
    response = _client(app, "alice@example.com").get("/api/timeline")
    assert response.status_code == 200
    assert runtime.calls == 0
    assert "gallery-preview" not in response.text


def test_timeline_preview_locator_round_trip_and_withdrawal(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteRecordRepository(engine)
    media_id = "66000000-0000-4000-8000-000000000099"
    location_id = "67000000-0000-4000-8000-000000000099"
    record_id = "69000000-0000-4000-8000-000000000099"
    try:
        connection = sqlite3.connect(settings.database_path)
        try:
            connection.execute(
                "INSERT INTO devices (id, display_name) VALUES "
                "('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Synthetic')"
            )
            connection.execute(
                "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) "
                "VALUES ('bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', "
                "'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', 'Synthetic', 'posix', ?)",
                (str(tmp_path / "library"),),
            )
            connection.execute(
                "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
                "VALUES (?, 'video', 10, 10)",
                (media_id,),
            )
            connection.execute(
                "INSERT INTO physical_media_locations (id, media_id, library_id, relative_path, "
                "availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
                "VALUES (?, ?, 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb', 'clip.mp4', 'available', "
                "8, 8, 10, 10)",
                (location_id, media_id),
            )
            connection.execute(
                "INSERT INTO media_metadata (media_id, display_title, description, content_category, "
                "acquisition_source, created_at_ms, updated_at_ms) "
                "VALUES (?, 'Clip', 'Synthetic description', 'general', 'manual_upload', 10, 10)",
                (media_id,),
            )
            connection.commit()
        finally:
            connection.close()

        def bind(connection) -> None:
            repository.bind_media_record(
                connection,
                record_id=RecordId.from_string(record_id),
                media_id=media_id,
                owner_login_key="alice@example.com",
                created_at_ms=10,
            )

        run_in_immediate_transaction(engine, bind)
        alice = _client(create_app(settings=settings), "alice@example.com")
        bob = _client(create_app(settings=settings), "bob@example.com")
        owner = alice.get("/api/timeline").json()["items"][0]
        assert owner["preview_location_id"] == location_id
        assert bob.get("/api/timeline").json()["total"] == 0
        direct = alice.get(f"/api/records/{record_id}")
        assert direct.status_code == 200
        assert bob.get(f"/api/records/{record_id}").status_code == 404
    finally:
        dispose_engine(engine)
