"""Application boundary for the first durable manual cover workflow."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass

from kronika.application.media_analysis import (
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath as AnalysisRelativePath,
)
from kronika.application.media_content import MEDIA_CONTENT_UNAVAILABLE_MESSAGE
from kronika.application.ports.cover_source_analysis import (
    CoverSourceAnalyzer,
    CoverSourceProbe,
)
from kronika.application.ports.cover_storage import (
    CoverEncoder,
    CoverStorageError,
    CoverThumbnailCache,
    CoverThumbnailUnavailableError,
    DurableCoverStorage,
    OpenedCoverThumbnail,
)
from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_cover_repository import (
    MediaCoverConflictError,
    MediaCoverDraft,
    MediaCoverMediaNotFoundError,
    MediaCoverRepository,
)
from kronika.application.ports.media_repository import MediaRepository
from kronika.application.upload_transport import default_now_ms
from kronika.domain import Library, LibraryId, MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
)
from kronika.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    SOURCE_OBSERVATION_ALGORITHM,
    TIMELESS_IMAGE_TIMESTAMP_MS,
    CoverSourceKind,
    CoverSourceObservation,
    MediaCover,
    source_reference_for_location,
)

COVER_PREVIEW_MEDIA_TYPE = "image/png"

FALLBACK_PREVIEW_SOURCE_VERSION = (
    "0000000000000000000000000000000000000000000000000000000000000000"
)


class CoverMediaNotFoundError(RuntimeError):
    """Raised when the media/location relationship is absent or mismatched."""


class CoverSourceUnavailableError(RuntimeError):
    """Raised when the requested source is unavailable or unsupported."""


class CoverSourceChangedError(RuntimeError):
    """Raised when the observed source changed between requests or extraction."""


class CoverTimestampInvalidError(RuntimeError):
    """Raised when the requested timestamp is outside the valid range."""


class CoverConflictError(RuntimeError):
    """Raised when a stale expected revision must not overwrite a newer cover."""


class CoverFailedError(RuntimeError):
    """Raised when cover handling fails unexpectedly."""


@dataclass(frozen=True, slots=True)
class CoverTimeline:
    """Server-authoritative timeline facts for one supported cover source."""

    media_id: MediaId
    location_id: MediaLocationId
    media_kind: MediaKind
    duration_ms: int
    source_version: str


@dataclass(frozen=True, slots=True)
class CoverPreview:
    """One ephemeral in-memory preview frame, never persisted."""

    media_type: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class CoverState:
    """Admin-facing accepted-cover state for the authoring surface."""

    media_id: str
    has_cover: bool
    revision: int | None
    timestamp_ms: int | None
    artifact_digest: str | None
    source_reference: str | None
    source_kind: str | None
    accepted_at_ms: int | None
    thumbnail_state: str
    artifact_state: str


@dataclass(frozen=True, slots=True)
class CoverAcceptResult:
    """Truthful accepted-cover mutation result and thumbnail state."""

    status: str
    revision: int
    timestamp_ms: int
    artifact_digest: str
    thumbnail_state: str


class CoverService:
    """Orchestrate timeline, preview, acceptance, state, and thumbnail delivery.

    The server is authoritative for source observation, frame extraction,
    artifact encoding, durable publication, compare-and-swap database mutation,
    and thumbnail generation. The browser only ever supplies identities and a
    timestamp; it never uploads pixels.
    """

    def __init__(
        self,
        media_repository: MediaRepository,
        library_repository: LibraryRepository,
        analyzer: CoverSourceAnalyzer,
        encoder: CoverEncoder,
        storage: DurableCoverStorage,
        thumbnail_cache: CoverThumbnailCache,
        cover_repository: MediaCoverRepository,
        now_ms: Callable[[], int] = default_now_ms,
    ) -> None:
        self._media_repository = media_repository
        self._library_repository = library_repository
        self._analyzer = analyzer
        self._encoder = encoder
        self._storage = storage
        self._thumbnail_cache = thumbnail_cache
        self._cover_repository = cover_repository
        self._now_ms = now_ms

    def timeline(self, media_id: MediaId, location_id: MediaLocationId) -> CoverTimeline:
        media, location, library = self._resolve_supported(media_id, location_id)
        probe = self._probe(media, location, library)
        source_kind = _required_source_kind(media, location)
        if _is_timeless_kind(source_kind):
            duration_ms = 0
        else:
            duration_ms = _require_positive_duration(probe.duration_ms)
        return CoverTimeline(
            media_id=media.id,
            location_id=location.id,
            media_kind=media.kind,
            duration_ms=duration_ms,
            source_version=_build_source_version(media, location, probe),
        )

    def preview(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
        timestamp_ms: int,
        expected_source_version: str,
    ) -> CoverPreview:
        media, location, library = self._resolve_supported(media_id, location_id)
        probe = self._probe(media, location, library)
        _ensure_source_version(media, location, probe, expected_source_version)
        requested_timestamp_ms = _validated_timestamp_ms(
            media=media,
            location=location,
            probe=probe,
            requested_timestamp_ms=timestamp_ms,
        )
        try:
            frame = self._analyzer.extract_frame(
                library.root,
                AnalysisRelativePath(location.relative_path.value),
                media.kind,
                requested_timestamp_ms,
            )
        except (MediaAnalysisUnavailableError, MediaAnalysisFailedError):
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE) from None
        return CoverPreview(media_type=frame.mime_type, payload=frame.payload)

    def accept(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
        *,
        timestamp_ms: int,
        expected_revision: int,
        expected_source_version: str,
    ) -> CoverAcceptResult:
        media, location, library = self._resolve_supported(media_id, location_id)
        probe = self._probe(media, location, library)
        _ensure_source_version(media, location, probe, expected_source_version)
        requested_timestamp_ms = _validated_timestamp_ms(
            media=media,
            location=location,
            probe=probe,
            requested_timestamp_ms=timestamp_ms,
        )
        observation = _observation(media, location, probe)
        try:
            frame = self._analyzer.extract_frame(
                library.root,
                AnalysisRelativePath(location.relative_path.value),
                media.kind,
                requested_timestamp_ms,
            )
        except (MediaAnalysisUnavailableError, MediaAnalysisFailedError):
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE) from None
        after_extraction = self._probe(media, location, library)
        if _build_source_version(media, location, after_extraction) != _build_source_version(
            media, location, probe
        ):
            raise CoverSourceChangedError(
                "The cover source changed during extraction."
            )
        try:
            artifact = self._encoder.encode_artifact_frame(frame)
            self._storage.publish(media_id=media.id, artifact=artifact)
        except Exception as exc:
            if isinstance(
                exc,
                (CoverSourceUnavailableError, CoverSourceChangedError, CoverConflictError),
            ):
                raise
            raise CoverFailedError("Accepted cover delivery failed.") from None
        draft = _build_draft(
            media=media,
            location=location,
            observation=observation,
            timestamp_ms=requested_timestamp_ms,
            artifact_digest=artifact.digest,
            artifact_width=artifact.width,
            artifact_height=artifact.height,
            artifact_byte_size=artifact.byte_size,
            now_ms=self._now_ms(),
        )
        try:
            result = self._cover_repository.set_cover(draft, expected_revision)
        except MediaCoverConflictError:
            raise CoverConflictError("The accepted cover changed concurrently.") from None
        except MediaCoverMediaNotFoundError:
            raise CoverMediaNotFoundError("Media not found.") from None
        assert result.cover is not None
        thumbnail_state = self._generate_thumbnail(media.id, artifact.digest)
        return CoverAcceptResult(
            status=result.outcome,
            revision=result.cover.revision,
            timestamp_ms=result.cover.source_timestamp_ms,
            artifact_digest=result.cover.artifact_digest,
            thumbnail_state=thumbnail_state,
        )

    def admin_state(self, media_id: MediaId) -> CoverState:
        cover = self._cover_repository.get(media_id)
        if cover is None:
            return CoverState(
                media_id=media_id.to_string(),
                has_cover=False,
                revision=None,
                timestamp_ms=None,
                artifact_digest=None,
                source_reference=None,
                source_kind=None,
                accepted_at_ms=None,
                thumbnail_state="none",
                artifact_state="none",
            )
        key = self._thumbnail_cache.key_for(
            media_id=media_id,
            artifact_digest=cover.artifact_digest,
        )
        thumbnail_state = "ready" if self._thumbnail_cache.contains(key) else "missing"
        artifact_state = (
            "available"
            if self._storage.artifact_valid(media_id=media_id, digest=cover.artifact_digest)
            else "missing"
        )
        return CoverState(
            media_id=media_id.to_string(),
            has_cover=True,
            revision=cover.revision,
            timestamp_ms=cover.source_timestamp_ms,
            artifact_digest=cover.artifact_digest,
            source_reference=cover.source_reference,
            source_kind=cover.source_kind.value,
            accepted_at_ms=cover.accepted_at_ms,
            thumbnail_state=thumbnail_state,
            artifact_state=artifact_state,
        )

    def thumbnail_etag(self, media_id: MediaId) -> str | None:
        """Return a versioned representation ETag for the cover thumbnail.

        The ETag binds the cover-thumbnail algorithm identity to the accepted
        artifact digest, so a thumbnail-algorithm change can never produce a
        stale 304 for the previous representation.
        """
        cover = self._cover_repository.get(media_id)
        if cover is None:
            return None
        body = f"{self._thumbnail_cache.algorithm}|{cover.artifact_digest}"
        return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'

    def thumbnail_etag_for_digest(self, media_id: MediaId, artifact_digest: str) -> str | None:
        """ETag for one approved digest. A missing artifact is absence, not the current cover."""
        if not isinstance(artifact_digest, str) or not artifact_digest:
            return None
        key = self._thumbnail_cache.key_for(
            media_id=media_id,
            artifact_digest=artifact_digest,
        )
        if not self._thumbnail_cache.contains(key):
            return None
        body = f"{self._thumbnail_cache.algorithm}|{artifact_digest}"
        return '"' + hashlib.sha256(body.encode("utf-8")).hexdigest() + '"'

    def open_thumbnail_for_digest(
        self,
        media_id: MediaId,
        artifact_digest: str,
    ) -> OpenedCoverThumbnail:
        """Open the thumbnail for one approved digest, never the current cover."""
        if not isinstance(artifact_digest, str) or not artifact_digest:
            raise CoverMediaNotFoundError("Media not found.")
        key = self._thumbnail_cache.key_for(
            media_id=media_id,
            artifact_digest=artifact_digest,
        )
        if not self._thumbnail_cache.contains(key):
            raise CoverMediaNotFoundError("Media not found.")
        try:
            return self._thumbnail_cache.open(key)
        except CoverThumbnailUnavailableError:
            raise CoverMediaNotFoundError("Media not found.") from None
        except Exception:
            raise CoverFailedError("Accepted cover delivery failed.") from None

    def open_thumbnail(self, media_id: MediaId) -> OpenedCoverThumbnail:
        cover = self._cover_repository.get(media_id)
        if cover is None:
            raise CoverMediaNotFoundError("Media not found.")
        key = self._thumbnail_cache.key_for(
            media_id=media_id,
            artifact_digest=cover.artifact_digest,
        )
        if not self._thumbnail_cache.contains(key):
            raise CoverMediaNotFoundError("Media not found.")
        try:
            return self._thumbnail_cache.open(key)
        except CoverThumbnailUnavailableError:
            raise CoverMediaNotFoundError("Media not found.") from None
        except Exception:
            raise CoverFailedError("Accepted cover delivery failed.") from None

    def cover_ready_map(self, media_ids: tuple[str, ...]) -> dict[str, bool]:
        parsed: list[MediaId] = []
        for media_id_text in media_ids:
            try:
                parsed.append(MediaId.from_string(media_id_text))
            except Exception:
                continue
        covers = self._cover_repository.list_by_media(tuple(parsed))
        keys_by_media: dict[str, str] = {}
        for cover in covers:
            keys_by_media[cover.media_id.to_string()] = self._thumbnail_cache.key_for(
                media_id=cover.media_id,
                artifact_digest=cover.artifact_digest,
            )
        present = self._thumbnail_cache.contains_many(tuple(keys_by_media.values()))
        return {
            media_id_text: keys_by_media.get(media_id_text) in present
            for media_id_text in media_ids
        }

    def list_cover_media_ids(self) -> tuple[MediaId, ...]:
        """Return logical media ids that hold an accepted cover."""
        return tuple(cover.media_id for cover in self._cover_repository.list_all())

    def regenerate_thumbnail(self, media_id: MediaId) -> str:
        """Explicitly regenerate the cover thumbnail from the durable artifact."""
        cover = self._cover_repository.get(media_id)
        if cover is None:
            return "none"
        return self._generate_thumbnail(media_id, cover.artifact_digest)

    def _generate_thumbnail(self, media_id: MediaId, artifact_digest: str) -> str:
        key = self._thumbnail_cache.key_for(
            media_id=media_id,
            artifact_digest=artifact_digest,
        )
        if self._thumbnail_cache.contains(key):
            return "ready"
        try:
            payload = self._storage.read_bytes(
                media_id=media_id,
                digest=artifact_digest,
            )
            thumbnail = self._encoder.encode_thumbnail(payload)
            self._thumbnail_cache.publish(key, thumbnail)
            return "ready"
        except (CoverStorageError, CoverThumbnailUnavailableError):
            return "missing"
        except Exception:
            return "missing"

    def _probe(
        self,
        media: LogicalMedia,
        location: MediaLocation,
        library: Library,
    ) -> CoverSourceProbe:
        try:
            return self._analyzer.probe(
                library.root,
                AnalysisRelativePath(location.relative_path.value),
                media.kind,
            )
        except (MediaAnalysisUnavailableError, MediaAnalysisFailedError):
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE) from None

    def _resolve_supported(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> tuple[LogicalMedia, MediaLocation, Library]:
        media = self._media_repository.get_media(media_id)
        if media is None:
            raise CoverMediaNotFoundError("Media not found.")
        location = self._media_repository.get_location(location_id)
        if location is None or location.media_id != media_id:
            raise CoverMediaNotFoundError("Media not found.")
        if location.availability != MediaLocationAvailability.AVAILABLE:
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        if _source_kind_for(media, location) is None:
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        library = self._library_repository.get(location.library_id)
        if library is None:
            raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        return media, location, library


def _source_kind_for(media: LogicalMedia, location: MediaLocation) -> CoverSourceKind | None:
    filename = location.relative_path.filename
    if "." not in filename:
        return None
    extension = "." + filename.rsplit(".", 1)[-1].lower()
    if media.kind is MediaKind.VIDEO and extension == ".mp4":
        return CoverSourceKind.MP4
    if media.kind is MediaKind.ANIMATED_IMAGE and extension == ".gif":
        return CoverSourceKind.GIF
    if (
        media.kind is MediaKind.IMAGE
        and extension in (".jpg", ".jpeg", ".png")
    ):
        return CoverSourceKind.IMAGE
    return None


def _required_source_kind(media: LogicalMedia, location: MediaLocation) -> CoverSourceKind:
    source_kind = _source_kind_for(media, location)
    if source_kind is None:
        raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
    return source_kind


def _is_timeless_kind(source_kind: CoverSourceKind) -> bool:
    """Return whether the source kind is a timeless still image.

    A timeless still image has no video duration and no selectable frame time;
    its canonical source timestamp is ``0`` and must never be presented as a
    chosen temporal frame.
    """
    return source_kind is CoverSourceKind.IMAGE


def _validated_timestamp_ms(
    *,
    media: LogicalMedia,
    location: MediaLocation,
    probe: CoverSourceProbe,
    requested_timestamp_ms: int,
) -> int:
    """Return the validated extraction timestamp for the requested source.

    Temporal GIF/MP4 sources require an integer millisecond within the probe
    duration. Timeless still-image sources accept only the canonical ``0``.
    """
    source_kind = _required_source_kind(media, location)
    if _is_timeless_kind(source_kind):
        return _ensure_timeless_timestamp(requested_timestamp_ms)
    duration_ms = _require_positive_duration(probe.duration_ms)
    _ensure_timestamp(requested_timestamp_ms, duration_ms)
    return requested_timestamp_ms


def _ensure_timeless_timestamp(timestamp_ms: int) -> int:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise CoverTimestampInvalidError("The selected timestamp is invalid.")
    if timestamp_ms != TIMELESS_IMAGE_TIMESTAMP_MS:
        raise CoverTimestampInvalidError("The selected timestamp is invalid.")
    return TIMELESS_IMAGE_TIMESTAMP_MS


def _observation(
    media: LogicalMedia,
    location: MediaLocation,
    probe: CoverSourceProbe,
) -> CoverSourceObservation:
    kind = _source_kind_for(media, location)
    if kind is None:
        raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
    return CoverSourceObservation(
        source_location_id=location.id,
        source_kind=kind,
        source_size_bytes=probe.source_size_bytes,
        source_mtime_ns=probe.source_mtime_ns,
        source_duration_ms=probe.duration_ms,
    )


def _build_source_version(
    media: LogicalMedia,
    location: MediaLocation,
    probe: CoverSourceProbe,
) -> str:
    payload = {
        "algorithm": SOURCE_OBSERVATION_ALGORITHM,
        "media_id": media.id.to_string(),
        "location_id": location.id.to_string(),
        "media_kind": media.kind.value,
        "source_size_bytes": probe.source_size_bytes,
        "source_mtime_ns": probe.source_mtime_ns,
        "duration_ms": probe.duration_ms,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _ensure_source_version(
    media: LogicalMedia,
    location: MediaLocation,
    probe: CoverSourceProbe,
    expected_source_version: str,
) -> None:
    if not isinstance(expected_source_version, str) or not _is_sha256(
        expected_source_version
    ):
        raise CoverSourceChangedError("The cover source changed.")
    if _build_source_version(media, location, probe) != expected_source_version:
        raise CoverSourceChangedError("The cover source changed.")


def _require_positive_duration(duration_ms: int | None) -> int:
    if duration_ms is None or duration_ms <= 0:
        raise CoverSourceUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
    return duration_ms


def _ensure_timestamp(timestamp_ms: int, duration_ms: int) -> None:
    if isinstance(timestamp_ms, bool) or not isinstance(timestamp_ms, int):
        raise CoverTimestampInvalidError("The selected timestamp is invalid.")
    if timestamp_ms < 0 or timestamp_ms >= duration_ms:
        raise CoverTimestampInvalidError("The selected timestamp is invalid.")


def _build_draft(
    *,
    media: LogicalMedia,
    location: MediaLocation,
    observation: CoverSourceObservation,
    timestamp_ms: int,
    artifact_digest: str,
    artifact_width: int,
    artifact_height: int,
    artifact_byte_size: int,
    now_ms: int,
) -> MediaCoverDraft:
    return MediaCoverDraft(
        media_id=media.id,
        source_location_id=location.id,
        source_reference=source_reference_for_location(location.id),
        source_kind=observation.source_kind,
        source_timestamp_ms=timestamp_ms,
        source_size_bytes=observation.source_size_bytes,
        source_mtime_ns=observation.source_mtime_ns,
        source_duration_ms=observation.source_duration_ms,
        source_observation_version=SOURCE_OBSERVATION_ALGORITHM,
        source_observation_digest=_build_source_version(
            media, location, _probe_from_observation(observation)
        ),
        artifact_profile=COVER_ARTIFACT_PROFILE,
        artifact_media_type=COVER_ARTIFACT_MEDIA_TYPE,
        artifact_digest=artifact_digest,
        artifact_width=artifact_width,
        artifact_height=artifact_height,
        artifact_byte_size=artifact_byte_size,
        accepted_at_ms=now_ms,
    )


def _probe_from_observation(observation: CoverSourceObservation) -> CoverSourceProbe:
    return CoverSourceProbe(
        duration_ms=observation.source_duration_ms,
        source_size_bytes=observation.source_size_bytes,
        source_mtime_ns=observation.source_mtime_ns,
    )


def _is_sha256(value: str) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )
