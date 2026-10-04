"""Application port for the durable accepted-cover persistence boundary."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media_cover import CoverSourceKind, MediaCover


class FrameNestMediaCoverRepositoryError(RuntimeError):
    """Raised when accepted-cover persistence fails unexpectedly."""


class MediaCoverMediaNotFoundError(RuntimeError):
    """Raised when the target logical medium is absent."""


class MediaCoverConflictError(RuntimeError):
    """Raised when a stale expected revision can never match current state."""


@dataclass(frozen=True, slots=True)
class MediaCoverDraft:
    """Validated durable facts for one accepted-cover mutation, minus revision.

    The caller has already extracted, encoded, and durably published the
    immutable artifact; this draft carries only sanitized facts and content
    identities, never paths or browser-provided pixel claims.
    """

    media_id: MediaId
    source_location_id: MediaLocationId | None
    source_reference: str
    source_kind: CoverSourceKind
    source_timestamp_ms: int
    source_size_bytes: int
    source_mtime_ns: int | None
    source_duration_ms: int | None
    source_observation_version: str
    source_observation_digest: str
    artifact_profile: str
    artifact_media_type: str
    artifact_digest: str
    artifact_width: int
    artifact_height: int
    artifact_byte_size: int
    accepted_at_ms: int

    def same_payload(self, current: MediaCover) -> bool:
        """Return whether the draft duplicates the current accepted cover.

        This implements exact-idempotency detection: an identical selection
        must not increment the revision or republish authoritative state.
        """
        return (
            current.media_id == self.media_id
            and current.source_location_id == self.source_location_id
            and current.source_reference == self.source_reference
            and current.source_kind == self.source_kind
            and current.source_timestamp_ms == self.source_timestamp_ms
            and current.source_size_bytes == self.source_size_bytes
            and current.source_mtime_ns == self.source_mtime_ns
            and current.source_duration_ms == self.source_duration_ms
            and current.source_observation_version == self.source_observation_version
            and current.source_observation_digest == self.source_observation_digest
            and current.artifact_profile == self.artifact_profile
            and current.artifact_media_type == self.artifact_media_type
            and current.artifact_digest == self.artifact_digest
            and current.artifact_width == self.artifact_width
            and current.artifact_height == self.artifact_height
            and current.artifact_byte_size == self.artifact_byte_size
        )


@dataclass(frozen=True, slots=True)
class MediaCoverSetResult:
    """Atomic outcome of one compare-and-swap accepted-cover mutation."""

    outcome: Literal["created", "replaced", "unchanged", "conflict"]
    cover: MediaCover | None


class MediaCoverRepository(Protocol):
    """Persistence contract for the sparse accepted-cover relation."""

    def get(self, media_id: MediaId) -> MediaCover | None:
        """Return the accepted cover for one logical medium, or None."""

    def list_by_media(self, media_ids: tuple[MediaId, ...]) -> tuple[MediaCover, ...]:
        """Return accepted covers for a bounded set of logical media."""

    def list_all(self) -> tuple[MediaCover, ...]:
        """Return all accepted covers in deterministic order."""

    def set_cover(
        self,
        draft: MediaCoverDraft,
        expected_revision: int,
    ) -> MediaCoverSetResult:
        """Atomically create, replace, or idempotently confirm one cover.

        ``expected_revision`` 0 means the caller expects no cover yet. The
        mutation acquires the writer lock and compares against the current
        revision so a stale browser tab can never silently overwrite a newer
        accepted cover.
        """
