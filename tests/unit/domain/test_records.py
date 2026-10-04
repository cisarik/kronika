"""Domain checks for Kronika documents and operation tokens."""

from __future__ import annotations

import uuid

import pytest

from kronika.domain.records import (
    CompletedDocument,
    DocumentId,
    RecordKind,
    RecordValueError,
    parse_operation_token,
)
from kronika.domain.research import CompletionEvidence


def _evidence() -> CompletionEvidence:
    return CompletionEvidence(
        provider_terminal=True,
        answer_complete=True,
        web_search_executed=True,
        refusal_marker=False,
        incomplete_marker=False,
    )


def test_operation_token_rejects_uuid_text() -> None:
    with pytest.raises(RecordValueError):
        parse_operation_token(str(uuid.uuid4()))


def test_operation_token_accepts_bounded_non_uuid() -> None:
    assert parse_operation_token("search-op-1") == "search-op-1"


def test_completed_document_keeps_full_answer() -> None:
    answer = "answer " * 20
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-1",
        kind=RecordKind.SEARCH,
        question_text="What happened?",
        answer_text=answer,
        citations=(),
        evidence=_evidence(),
        created_at_ms=1,
        completed_at_ms=2,
    )
    assert document.answer_text == answer


def test_incomplete_evidence_cannot_become_a_document() -> None:
    with pytest.raises(RecordValueError):
        CompletedDocument(
            document_id=DocumentId.new(),
            operation_id="search-op-2",
            kind=RecordKind.SEARCH,
            question_text="What happened?",
            answer_text="An answer.",
            citations=(),
            evidence=CompletionEvidence(
                provider_terminal=True,
                answer_complete=False,
                web_search_executed=True,
                refusal_marker=False,
                incomplete_marker=True,
            ),
            created_at_ms=1,
            completed_at_ms=2,
        )
