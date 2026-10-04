"""Application port for durable YouTube manual-acquisition claims."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.identities import MediaId, YouTubeAcquisitionClaimId
from kronika.domain.youtube_acquisition import (
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
)
from kronika.domain.uploads import UploadSessionId


class FrameNestYouTubeClaimRepositoryError(RuntimeError):
    """Sanitized base error for durable claim persistence."""


class YouTubeClaimAlreadyExistsError(FrameNestYouTubeClaimRepositoryError):
    """Raised when a claim UUID already exists."""


class YouTubeClaimNotFoundError(FrameNestYouTubeClaimRepositoryError):
    """Raised when a claim does not exist."""


class YouTubeClaimConcurrencyConflictError(FrameNestYouTubeClaimRepositoryError):
    """Raised when an optimistic state/version guard is stale."""


class YouTubeClaimSourceIdentityConflictError(FrameNestYouTubeClaimRepositoryError):
    """Raised when another active claim owns the source identity."""


class YouTubeAcquisitionClaimRepository(Protocol):
    """Persistence-independent durable YouTube claim contract."""

    def create_or_get_active(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> tuple[YouTubeAcquisitionClaim, bool]:
        """Create a claim or return the transactionally selected active winner."""

    def create(self, claim: YouTubeAcquisitionClaim) -> None:
        """Persist one new claim, rejecting identity conflicts."""

    def get(
        self,
        claim_id: YouTubeAcquisitionClaimId,
    ) -> YouTubeAcquisitionClaim | None:
        """Return one claim by identity, or None."""

    def find_active_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
        created_by_login_key: str | None = None,
    ) -> YouTubeAcquisitionClaim | None:
        """Return the scoped active source-identity owner, if present."""

    def find_latest_cataloged_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
    ) -> YouTubeAcquisitionClaim | None:
        """Return the newest successful source claim available for reuse."""

    def find_owned_live_successful_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
        created_by_login_key: str,
    ) -> YouTubeAcquisitionClaim | None:
        """Return the requester's own live successful claim for one source."""

    def find_published_successful_by_source_identity(
        self,
        *,
        extractor_key: str,
        youtube_video_id: str,
    ) -> YouTubeAcquisitionClaim | None:
        """Return a successful claim whose linked media is published."""

    def find_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> YouTubeAcquisitionClaim | None:
        """Return the unique claim linked to an upload session, if present."""

    def get_owned(
        self,
        claim_id: YouTubeAcquisitionClaimId,
        *,
        created_by_login_key: str,
    ) -> YouTubeAcquisitionClaim | None:
        """Return one owned claim, or None for missing/foreign/legacy rows."""

    def list_owned(
        self,
        *,
        created_by_login_key: str,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        """Return owned claims newest-first with stable cursor ordering."""

    def count_active_for_requester(self, *, created_by_login_key: str) -> int:
        """Count active ordinary claims for one requester."""

    def count_global_active_ordinary(self) -> int:
        """Count active claims that carry non-NULL requester ownership."""

    def count_submits_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        """Count requester submissions created at or after since_ms."""

    def count_failed_transitions_since(
        self,
        *,
        created_by_login_key: str,
        since_ms: int,
    ) -> int:
        """Count owned rows that entered failed at or after since_ms."""

    def private_successful_quota(
        self,
        *,
        created_by_login_key: str,
    ) -> tuple[int, int]:
        """Return (item_count, aggregate_bytes) for live unpublished owned media."""

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        """Return True when a live successful owned claim links the media."""

    def list_recovery_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        """Return active claims in deterministic recovery order."""

    def list_cleanup_candidates(
        self,
        *,
        limit: int,
    ) -> tuple[YouTubeAcquisitionClaim, ...]:
        """Return terminal claims whose exact staging cleanup remains pending."""

    def save(
        self,
        claim: YouTubeAcquisitionClaim,
        *,
        expected_state: YouTubeAcquisitionState,
        expected_version: int,
    ) -> YouTubeAcquisitionClaim:
        """Persist one already-domain-validated optimistic snapshot idempotently."""
