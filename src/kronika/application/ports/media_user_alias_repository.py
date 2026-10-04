"""Application port for per-user media alias overlay persistence."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.identities import MediaId
from kronika.domain.media_metadata import CanonicalTagKey
from kronika.domain.media_user_alias import MediaUserAlias, MediaUserAliasContent
from kronika.domain.x_acquisition import XPostClaimId


class FrameNestMediaUserAliasRepositoryError(RuntimeError):
    """Sanitized error raised when alias overlay persistence fails."""


class MediaUserAliasMediaNotFoundError(RuntimeError):
    """Raised when a logical media item is absent."""


class AliasTagNotFoundError(RuntimeError):
    """Raised when an alias references an absent canonical tag."""


class MediaUserAliasRepository(Protocol):
    """Persistence-independent caller-private overlay contract."""

    def get_alias(self, media_id: MediaId, login_key: str) -> MediaUserAlias | None:
        """Return the caller's overlay row, or None when absent."""

    def list_aliases_for_login(
        self, login_key: str, media_ids: tuple[MediaId, ...]
    ) -> dict[str, MediaUserAlias]:
        """Return the caller's overlays for the named media ids.

        Missing ids are omitted. This method is read-only.
        """

    def canonical_tag_display_names(self, tag_keys: tuple[str, ...]) -> dict[str, str]:
        """Return display names for existing canonical tag keys."""

    def list_aliases_for_media(self, media_id: MediaId) -> tuple[MediaUserAlias, ...]:
        """Return every overlay for one media item, ordered by login key.

        Raises MediaUserAliasMediaNotFoundError when the logical media row is
        absent. This method is read-only.
        """

    def upsert_alias(
        self,
        media_id: MediaId,
        login_key: str,
        content: MediaUserAliasContent,
        now_ms: int,
    ) -> MediaUserAlias | None:
        """Replace the caller overlay. Empty content deletes the row and returns None."""

    def delete_alias(self, media_id: MediaId, login_key: str) -> None:
        """Delete the caller overlay and its tags if present."""

    def canonical_tag_keys_exist(self, tag_keys: tuple[CanonicalTagKey, ...]) -> bool:
        """Return True when every tag key exists in the canonical tag catalog."""
