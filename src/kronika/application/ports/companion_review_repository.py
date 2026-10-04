"""Persistence port for administrator companion review inbox reads and mutations."""

from __future__ import annotations

from typing import Protocol

from kronika.application.companion_review import (
    CompanionReviewApplyResult,
    CompanionReviewDetail,
    CompanionReviewInboxPage,
    CompanionReviewOpenedResult,
)
from kronika.domain.identities import MediaId


class FrameNestCompanionReviewRepositoryError(RuntimeError):
    """Sanitized companion-review persistence failure."""


class CompanionReviewMediaNotFoundError(FrameNestCompanionReviewRepositoryError):
    """Raised when the target logical medium is absent."""


class CompanionReviewMovieExcludedError(FrameNestCompanionReviewRepositoryError):
    """Raised when the target medium is in the movie category."""


class CompanionReviewStoredResultError(FrameNestCompanionReviewRepositoryError):
    """Raised when a stored suggestion payload cannot be decoded."""


class CompanionReviewAnalysisRunNotFoundError(FrameNestCompanionReviewRepositoryError):
    """Raised when the named analysis run does not exist."""


class CompanionReviewRunNotEligibleError(FrameNestCompanionReviewRepositoryError):
    """Raised when the named run is not an eligible generic success for the media."""


class CompanionReviewStaleMappingError(FrameNestCompanionReviewRepositoryError):
    """Raised when submitted tag keys are not an ordered subsequence of mapped keys."""


class CompanionReviewTagLimitConflictError(FrameNestCompanionReviewRepositoryError):
    """Raised when preserve-and-append would exceed the canonical tag maximum."""


class CompanionReviewRepository(Protocol):
    """Persistence contract for companion review inbox, history, opened, and apply."""

    def list_inbox(
        self,
        *,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, bool, str] | None,
    ) -> CompanionReviewInboxPage:
        """Return one mixed keyset page of analyzed rows and owned X Saves."""

    def get_detail(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        limit: int,
        cursor: tuple[int, str] | None,
    ) -> CompanionReviewDetail:
        """Return canonical state and one history page for one medium."""

    def mark_opened(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        analysis_run_id: MediaId,
        now_ms: int,
    ) -> CompanionReviewOpenedResult:
        """Record a monotonic opened marker for one actor and medium."""

    def apply_review(
        self,
        *,
        media_id: MediaId,
        actor_login_key: str,
        analysis_run_id: MediaId,
        fields: tuple[str, ...],
        tag_keys: tuple[str, ...],
        now_ms: int,
    ) -> CompanionReviewApplyResult:
        """Apply selected suggestion fields, upsert receipts, and publish when ready."""
