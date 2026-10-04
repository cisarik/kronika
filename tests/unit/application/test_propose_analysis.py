"""Unit evidence for analysis-proposal use-case bounds."""

from __future__ import annotations

import pytest

from kronika.application.analysis_proposal import (
    DEFAULT_ANALYSIS_PROPOSAL_MAX_SUBMITS_PER_HOUR,
    MS_PER_HOUR,
    ListAnalysisProposals,
    ProposeAnalysis,
    AnalysisProposalLimitError,
    AnalysisProposalValidationError,
)
from kronika.application.ports.analysis_proposal import (
    AdminAnalysisProposalPage,
    AnalysisProposal,
    AnalysisProposalMediaNotFoundError,
)
from kronika.domain.identities import MediaId

MEDIA_A = "11111111-1111-4111-8111-111111111111"


class _Repo:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []
        self.count_calls: list[dict[str, object]] = []

    def create_proposal(self, **kwargs: object) -> AnalysisProposal:
        self.created.append(kwargs)
        media_id = kwargs["media_id"]
        assert isinstance(media_id, MediaId)
        created_at_ms = kwargs["created_at_ms"]
        assert isinstance(created_at_ms, int)
        return AnalysisProposal(
            proposal_id=str(kwargs["proposal_id"]),
            media_id=media_id.to_string(),
            proposed_by_login_key=str(kwargs["login_key"]),
            created_at_ms=created_at_ms,
            status=str(kwargs["status"]),
        )

    def list_open_proposals(self, *, limit: int, offset: int) -> AdminAnalysisProposalPage:
        return AdminAnalysisProposalPage(items=(), total=0, limit=limit, offset=offset)

    def count_created_since(self, *, login_key: str, since_ms: int) -> int:
        self.count_calls.append({"login_key": login_key, "since_ms": since_ms})
        return sum(
            1
            for entry in self.created
            if entry["login_key"] == login_key
            and int(entry["created_at_ms"]) >= since_ms
        )


def test_propose_rejects_empty_login_and_unknown_identity_shape() -> None:
    repo = _Repo()
    service = ProposeAnalysis(repo, now_ms=lambda: 42, new_id=lambda: "id-1")
    with pytest.raises(AnalysisProposalValidationError):
        service.execute(media_id=MEDIA_A, login_key="")
    with pytest.raises(AnalysisProposalMediaNotFoundError):
        service.execute(media_id="not-a-uuid", login_key="alice@example.com")
    created = service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    assert created.created_at_ms == 42
    assert created.proposal_id == "id-1"
    assert created.status == "open"
    assert len(repo.created) == 1


def test_list_rejects_invalid_bounds() -> None:
    service = ListAnalysisProposals(_Repo())
    with pytest.raises(AnalysisProposalValidationError):
        service.execute(limit=0)
    with pytest.raises(AnalysisProposalValidationError):
        service.execute(offset=-1)
    page = service.execute()
    assert page.limit == 24
    assert page.total == 0


def test_default_submit_limit_mirrors_requester_pattern() -> None:
    assert DEFAULT_ANALYSIS_PROPOSAL_MAX_SUBMITS_PER_HOUR == 6
    assert MS_PER_HOUR == 3_600_000


def test_submit_rate_limit_enforcement() -> None:
    repo = _Repo()
    service = ProposeAnalysis(
        repo,
        now_ms=lambda: 10_000,
        new_id=lambda: f"id-{len(repo.created)}",
        max_submits_per_hour=2,
    )
    first = service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    second = service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    assert first.proposal_id != second.proposal_id
    with pytest.raises(AnalysisProposalLimitError) as raised:
        service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    assert raised.value.code == "ANALYSIS_PROPOSAL_RATE_LIMIT"
    assert str(raised.value) == "Too many analysis proposals this hour."
    assert len(repo.created) == 2


def test_submit_rate_limit_window_resets_after_one_hour() -> None:
    clock = [10_000]
    repo = _Repo()
    service = ProposeAnalysis(
        repo,
        now_ms=lambda: clock[0],
        new_id=lambda: f"id-{len(repo.created)}-{clock[0]}",
        max_submits_per_hour=1,
    )
    service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    with pytest.raises(AnalysisProposalLimitError):
        service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    clock[0] += MS_PER_HOUR + 1
    reset = service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    assert reset.status == "open"


def test_submit_rate_limit_is_isolated_per_user() -> None:
    repo = _Repo()
    service = ProposeAnalysis(
        repo,
        now_ms=lambda: 5_000,
        new_id=lambda: f"id-{len(repo.created)}",
        max_submits_per_hour=1,
    )
    service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    with pytest.raises(AnalysisProposalLimitError):
        service.execute(media_id=MEDIA_A, login_key="alice@example.com")
    other = service.execute(media_id=MEDIA_A, login_key="bob@example.com")
    assert other.status == "open"
    assert [call["login_key"] for call in repo.count_calls[-1:]] == [
        "bob@example.com"
    ]


def test_negative_submit_limit_disables_rate_limiting() -> None:
    repo = _Repo()
    service = ProposeAnalysis(
        repo,
        now_ms=lambda: 7_000,
        new_id=lambda: f"id-{len(repo.created)}",
        max_submits_per_hour=-1,
    )
    for _ in range(4):
        created = service.execute(media_id=MEDIA_A, login_key="alice@example.com")
        assert created.status == "open"
    assert repo.count_calls == []
