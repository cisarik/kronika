"""Application services for durable analysis proposals.

These use-cases never call a provider, enqueue analysis, or read the automatic
analysis flag. Submissions are bounded by a per-user hourly submit rate limit
that mirrors the YouTube/X requester admission pattern: once a user reaches the
limit inside the rolling one-hour window, further submissions are rejected
until the window resets.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import uuid

from kronika.application.ports.analysis_proposal import (
    ANALYSIS_PROPOSAL_STATUS_OPEN,
    AdminAnalysisProposalPage,
    AnalysisProposal,
    AnalysisProposalMediaNotFoundError,
    AnalysisProposalRepository,
    FrameNestAnalysisProposalRepositoryError,
)
from kronika.application.upload_transport import default_now_ms
from kronika.domain.identities import MediaId
from kronika.domain import FrameNestIdentityError

DEFAULT_ANALYSIS_PROPOSAL_LIMIT = 24
MAX_ANALYSIS_PROPOSAL_LIMIT = 100
DEFAULT_ANALYSIS_PROPOSAL_MAX_SUBMITS_PER_HOUR = 6
MS_PER_HOUR = 3_600_000
ANALYSIS_PROPOSAL_RATE_LIMIT_CODE = "ANALYSIS_PROPOSAL_RATE_LIMIT"
ANALYSIS_PROPOSAL_RATE_LIMIT_MESSAGE = "Too many analysis proposals this hour."


class AnalysisProposalLimitError(Exception):
    """Raised when one user exceeds the bounded proposal submit rate."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class AnalysisProposalValidationError(ValueError):
    """Raised for invalid proposal list input."""


def _new_proposal_id() -> str:
    return str(uuid.uuid4())


@dataclass(frozen=True, slots=True)
class ProposeAnalysis:
    """Create one durable administrator-visible analysis proposal."""

    repository: AnalysisProposalRepository
    now_ms: Callable[[], int] = default_now_ms
    new_id: Callable[[], str] = _new_proposal_id
    max_submits_per_hour: int = DEFAULT_ANALYSIS_PROPOSAL_MAX_SUBMITS_PER_HOUR

    def execute(self, *, media_id: str, login_key: str) -> AnalysisProposal:
        if not isinstance(login_key, str) or not login_key.strip():
            raise AnalysisProposalValidationError()
        try:
            parsed_media_id = MediaId.from_string(media_id)
        except FrameNestIdentityError as exc:
            raise AnalysisProposalMediaNotFoundError() from exc
        self._enforce_submit_rate_limit(login_key)
        return self.repository.create_proposal(
            proposal_id=self.new_id(),
            media_id=parsed_media_id,
            login_key=login_key,
            created_at_ms=self.now_ms(),
            status=ANALYSIS_PROPOSAL_STATUS_OPEN,
        )

    def _enforce_submit_rate_limit(self, login_key: str) -> None:
        if self.max_submits_per_hour < 0:
            return
        since = self.now_ms() - MS_PER_HOUR
        submits = self.repository.count_created_since(
            login_key=login_key,
            since_ms=since,
        )
        if submits >= self.max_submits_per_hour:
            raise AnalysisProposalLimitError(
                ANALYSIS_PROPOSAL_RATE_LIMIT_CODE,
                ANALYSIS_PROPOSAL_RATE_LIMIT_MESSAGE,
            )


@dataclass(frozen=True, slots=True)
class ListAnalysisProposals:
    """Normalize and execute the administrator open-proposal query."""

    repository: AnalysisProposalRepository

    def execute(
        self,
        *,
        limit: int = DEFAULT_ANALYSIS_PROPOSAL_LIMIT,
        offset: int = 0,
    ) -> AdminAnalysisProposalPage:
        bounded_limit = _bounded_int(
            limit,
            minimum=1,
            maximum=MAX_ANALYSIS_PROPOSAL_LIMIT,
        )
        bounded_offset = _bounded_int(offset, minimum=0, maximum=None)
        try:
            return self.repository.list_open_proposals(
                limit=bounded_limit,
                offset=bounded_offset,
            )
        except FrameNestAnalysisProposalRepositoryError:
            raise


def _bounded_int(value: int, *, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise AnalysisProposalValidationError()
    if maximum is not None and value > maximum:
        raise AnalysisProposalValidationError()
    return value
