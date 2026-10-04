"""Read-side contribution attribution used by workspace list and audience reads."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from kronika.domain.identities import MediaId

CONTRIBUTION_SOURCE_UPLOAD = "upload"
CONTRIBUTION_SOURCE_YOUTUBE = "youtube"
CONTRIBUTION_SOURCE_X = "x"
CONTRIBUTION_SOURCE_ORDER = (
    CONTRIBUTION_SOURCE_UPLOAD,
    CONTRIBUTION_SOURCE_YOUTUBE,
    CONTRIBUTION_SOURCE_X,
)


class FrameNestMediaAttributionRepositoryError(RuntimeError):
    """Sanitized contribution-attribution persistence failure."""


@dataclass(frozen=True, slots=True)
class MediaContributionAttribution:
    """One contributor's stamps on one logical medium."""

    login_key: str
    sources: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceMediaItem:
    """Caller-scoped contribution row with honest publication and readiness."""

    media_id: str
    media_kind: str
    created_at_ms: int
    updated_at_ms: int
    display_title: str | None
    description: str | None
    content_category: str
    acquisition_source: str
    contribution_sources: tuple[str, ...]
    content_publication_state: str
    publication_ready: bool
    missing_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WorkspaceMediaPage:
    """Deterministic bounded page of the caller's attributed media."""

    items: tuple[WorkspaceMediaItem, ...]
    total: int
    limit: int
    offset: int


class MediaAttributionRepository(Protocol):
    """Read-only contribution stamps: upload, YouTube, and X."""

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        """Return True when an upload session stamps this caller on the media."""

    def list_workspace_media(
        self,
        *,
        login_key: str,
        limit: int,
        offset: int,
    ) -> WorkspaceMediaPage:
        """Return the caller's attributed catalog media, newest first."""

    def list_contributions_for_media_ids(
        self,
        media_ids: tuple[str, ...],
    ) -> dict[str, tuple[MediaContributionAttribution, ...]]:
        """Return contribution stamps grouped by media id."""
