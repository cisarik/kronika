"""HTTP caller matrix for a private completed document is not exposed by gallery."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import ROLE_USER
from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordKind,
)
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import SqliteRecordRepository
from tests.support.record_access import install_synthetic_caller, synthetic_identity


def test_gallery_does_not_list_a_private_search_record(tmp_path: Path) -> None:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        identity_map={"alice": "user", "bob": "user"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-http",
        kind=RecordKind.SEARCH,
        question_text="Hidden question",
        answer_text="Hidden answer",
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
    try:
        SqliteRecordRepository(engine).create_completed_document(
            document,
            owner_login_key="alice",
            record_id=__import__(
                "kronika.domain.records", fromlist=["RecordId"]
            ).RecordId.new(),
        )
    finally:
        dispose_engine(engine)
    app = install_synthetic_caller(
        create_app(settings=settings),
        "bob",
        role=ROLE_USER,
    )
    client = TestClient(app)
    listing = client.get("/api/media")
    assert listing.status_code == 200
    assert "Hidden question" not in listing.text
    assert "Hidden answer" not in listing.text
    anonymous = TestClient(create_app(settings=settings))
    anonymous_listing = anonymous.get("/api/media")
    assert anonymous_listing.status_code == 200
    assert anonymous_listing.json()["items"] == []
    assert "Hidden answer" not in anonymous_listing.text
    public_settings = KronikaSettings(
        database_path=settings.database_path,
        ingress_mode="public_published_uds",
        uds_path=tmp_path / "public.sock",
        _env_file=None,
    )
    public_listing = TestClient(create_app(settings=public_settings)).get("/api/media")
    assert public_listing.status_code == 200
    assert "Hidden question" not in public_listing.text
    assert "Hidden answer" not in public_listing.text
    assert synthetic_identity("alice").login_key == "alice"
