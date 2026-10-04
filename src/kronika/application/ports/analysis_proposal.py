"""Ports for durable administrator-visible analysis proposals."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.identities import MediaId

ANALYSIS_PROPOSAL_STATUS_OPEN = "open"


class FrameNestAnalysisProposalRepositoryError(RuntimeError):
    """Sanitized analysis-proposal persistence failure."""


class AnalysisProposalMediaNotFoundError(LookupError):
    """Raised when the proposed media id is absent from the catalog."""


@dataclass(frozen=True, slots=True)
class AnalysisProposal:
    """One durable proposal row."""

    proposal_id: str
    media_id: str
    proposed_by_login_key: str
    created_at_ms: int
    status: str


@dataclass(frozen=True, slots=True)
class AdminAnalysisProposalItem:
    """Administrator list row with live title, publication, and readiness."""

    proposal_id: str
    media_id: str
    proposer_login: str
    created_at_ms: int
    status: str
    display_title: str | None
    content_publication_state: str
    publication_ready: bool
    missing_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AdminAnalysisProposalPage:
    """Newest-first page of open analysis proposals."""

    items: tuple[AdminAnalysisProposalItem, ...]
    total: int
    limit: int
    offset: int


class AnalysisProposalRepository(Protocol):
    """Durable proposal writes and administrator reads. Never runs analysis."""

    def create_proposal(
        self,
        *,
        proposal_id: str,
        media_id: MediaId,
        login_key: str,
        created_at_ms: int,
        status: str = ANALYSIS_PROPOSAL_STATUS_OPEN,
    ) -> AnalysisProposal:
        """Insert one proposal for an existing catalog medium."""

    def list_open_proposals(
        self,
        *,
        limit: int,
        offset: int,
    ) -> AdminAnalysisProposalPage:
        """Return open proposals newest first."""

    def count_created_since(
        self,
        *,
        login_key: str,
        since_ms: int,
    ) -> int:
        """Count proposals created by one user at or after the window start."""
