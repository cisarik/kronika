"""Application record service: owner, denial, and administrator approval."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.application.records import RecordService
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordId,
    RecordKind,
    RecordNotFoundError,
    RecordVisibility,
)
from kronika.domain.research import CompletionEvidence
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.record_repository import SqliteRecordRepository
from tests.support.record_access import synthetic_identity
from kronika.configuration import KronikaSettings


def _evidence() -> CompletionEvidence:
    return CompletionEvidence(
        provider_terminal=True,
        answer_complete=True,
        web_search_executed=True,
        refusal_marker=False,
        incomplete_marker=False,
    )


def _document() -> CompletedDocument:
    return CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-service",
        kind=RecordKind.SEARCH,
        question_text="What is the private record?",
        answer_text="A complete answer.",
        citations=(),
        evidence=_evidence(),
        created_at_ms=10,
        completed_at_ms=20,
    )


def _service(tmp_path: Path) -> tuple[RecordService, object]:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    return RecordService(SqliteRecordRepository(engine)), engine


def test_owner_reads_private_document_and_stranger_is_not_found(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    try:
        alice = synthetic_identity("alice", role=ROLE_USER)
        bob = synthetic_identity("bob", role=ROLE_USER)
        created = service.create_completed_document(alice, _document())
        assert created.summary.visibility is RecordVisibility.PRIVATE
        loaded = service.read_detail(alice, created.summary.record_id)
        assert loaded.document is not None
        assert loaded.document.answer_text == "A complete answer."
        with pytest.raises(RecordNotFoundError):
            service.read_detail(bob, created.summary.record_id)
        with pytest.raises(RecordNotFoundError):
            service.prepare_approval(alice, created.summary.record_id)
    finally:
        dispose_engine(engine)


def test_administrator_approval_and_exact_replay(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    try:
        alice = synthetic_identity("alice", role=ROLE_USER)
        admin = synthetic_identity("ada", role=ROLE_ADMIN)
        created = service.create_completed_document(
            alice, _document(), record_id=RecordId.new()
        )
        candidate = service.prepare_approval(admin, created.summary.record_id)
        first = service.approve(admin, candidate, approved_at_ms=30)
        assert first.changed is True
        replay = service.prepare_approval(admin, created.summary.record_id)
        second = service.approve(admin, replay, approved_at_ms=40)
        assert second.changed is False
        assert second.version == first.version
        household = synthetic_identity("bob", role=ROLE_USER)
        visible = service.read_detail(household, created.summary.record_id)
        assert visible.summary.read_decision == "approved"
        timeline = service.list_timeline(household)
        assert [item.record_id for item in timeline.items] == [created.summary.record_id]
    finally:
        dispose_engine(engine)


def test_caller_matrix_sql_matches_owner_stranger_and_administrator(tmp_path: Path) -> None:
    service, engine = _service(tmp_path)
    try:
        alice = synthetic_identity("alice", role=ROLE_USER)
        bob = synthetic_identity("bob", role=ROLE_USER)
        admin = synthetic_identity("ada", role=ROLE_ADMIN)
        created = service.create_completed_document(alice, _document())
        record_id = created.summary.record_id
        assert created.summary.owner_login_key == "alice"
        assert [item.record_id for item in service.list_own_history(alice).items] == [
            record_id
        ]
        assert service.list_own_history(bob).items == ()
        with pytest.raises(RecordNotFoundError):
            service.read_detail(bob, record_id)
        assert [
            item.record_id for item in service.list_admin_inventory(admin).items
        ] == [record_id]
        unfinished = service.read_detail(admin, record_id)
        assert unfinished.summary.visibility is RecordVisibility.PRIVATE
        with pytest.raises(RecordNotFoundError):
            service.read_detail(object(), record_id)
        with pytest.raises(RecordNotFoundError):
            service.list_admin_inventory(bob)
        with pytest.raises(TypeError):
            service.create_completed_document(
                alice,
                _document(),
                owner_login_key="bob",
            )
    finally:
        dispose_engine(engine)
