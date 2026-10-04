"""Application port for requester-private YouTube media access lookup."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.identities import MediaId


class YouTubeRequesterPrivateAccess(Protocol):
    """Narrow existence lookup used only by the shared audience policy."""

    def has_live_requester_media_access(
        self,
        *,
        media_id: MediaId,
        login_key: str,
    ) -> bool:
        """Return True when a live successful owned claim still links the media."""
