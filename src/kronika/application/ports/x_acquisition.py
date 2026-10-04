"""Application port for durable requester-private X post claims and assets."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.identities import MediaId, XAssetId, XPostClaimId
from kronika.domain.media_user_alias import MediaUserAliasContent, PendingMediaUserAlias
from kronika.domain.uploads import UploadSessionId
from kronika.domain.x_acquisition import (
    XAcquisitionState,
    XAsset,
    XAssetState,
    XPostClaim,
)


class FrameNestXClaimRepositoryError(RuntimeError):
    """Sanitized base error for durable X claim persistence."""


class XClaimAlreadyExistsError(FrameNestXClaimRepositoryError):
    """Raised when a claim UUID already exists."""


class XClaimNotFoundError(FrameNestXClaimRepositoryError):
    """Raised when a claim or asset does not exist."""


class XClaimConcurrencyConflictError(FrameNestXClaimRepositoryError):
    """Raised when an optimistic state/version guard is stale."""


class XClaimSourceIdentityConflictError(FrameNestXClaimRepositoryError):
    """Raised when another active claim owns the source post identity."""


class XAcquisitionClaimRepository(Protocol):
    """Persistence-independent durable X post and asset contract."""

    def create_or_get_active(
        self,
        claim: XPostClaim,
    ) -> tuple[XPostClaim, bool]:
        """Create a claim or return the transactionally selected active winner."""

    def create_post(self, claim: XPostClaim) -> None:
        """Persist one new post claim, rejecting identity conflicts."""

    def get_post(
        self,
        post_id: XPostClaimId,
    ) -> XPostClaim | None:
        """Return one post claim by identity, or None."""

    def find_active_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str | None = None,
    ) -> XPostClaim | None:
        """Return the scoped active source-post owner, if present."""

    def find_owned_live_successful_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        """Return the requester's own live successful claim for one source."""

    def find_post_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> XPostClaim | None:
        """Return the post claim whose asset is linked to an upload session."""

    def find_asset_by_upload_id(
        self,
        upload_id: UploadSessionId,
    ) -> XAsset | None:
        """Return the asset linked to one upload session, if present."""

    def find_owned_successful_by_post_id(
        self,
        *,
        post_id_key: str,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        """Return the requester's own successful claim for one source."""

    def get_owned_post(
        self,
        post_id: XPostClaimId,
        *,
        created_by_login_key: str,
    ) -> XPostClaim | None:
        """Return one owned claim, or None for missing/foreign rows."""

    def list_owned(
        self,
        *,
        created_by_login_key: str,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[XPostClaim, ...]:
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
        """Return (item_count, aggregate_bytes) for live unpublished X media."""

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        """Return True when a live successful owned X asset links the media."""

    def list_recovery_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[XPostClaim, ...]:
        """Return active claims in deterministic recovery order."""

    def list_cleanup_candidates(
        self,
        *,
        limit: int,
    ) -> tuple[XPostClaim, ...]:
        """Return terminal claims whose asset cleanup remains pending."""

    def save_post(
        self,
        claim: XPostClaim,
        *,
        expected_state: XAcquisitionState,
        expected_version: int,
    ) -> XPostClaim:
        """Persist one already-domain-validated optimistic post snapshot."""

    def create_assets(self, assets: tuple[XAsset, ...]) -> None:
        """Persist one claim's discovered assets atomically."""

    def get_asset(
        self,
        asset_id: XAssetId,
    ) -> XAsset | None:
        """Return one asset by identity, or None."""

    def list_assets_for_post(
        self,
        post_id: XPostClaimId,
    ) -> tuple[XAsset, ...]:
        """Return all assets for one claim, ordered by ordinal."""

    def save_asset(
        self,
        asset: XAsset,
        *,
        expected_state: XAssetState,
        expected_version: int,
    ) -> XAsset:
        """Persist one already-domain-validated optimistic asset snapshot."""

    def get_pending_alias(self, claim_id: XPostClaimId) -> PendingMediaUserAlias | None:
        """Return the pending alias for one claim, or None."""

    def upsert_pending_alias(
        self,
        claim_id: XPostClaimId,
        login_key: str,
        content: MediaUserAliasContent,
        now_ms: int,
    ) -> PendingMediaUserAlias | None:
        """Replace pending alias content. Empty content deletes the pending row."""

    def delete_pending_alias(self, claim_id: XPostClaimId) -> None:
        """Delete pending alias content for one claim if present."""
