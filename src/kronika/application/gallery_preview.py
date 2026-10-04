"""Application boundary for persistent gallery preview derivatives."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from kronika.application.media_analysis import (
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath as AnalysisRelativePath,
)
from kronika.application.media_content import (
    MEDIA_CONTENT_FAILED_MESSAGE,
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MEDIA_NOT_FOUND_MESSAGE,
    MediaContentFailedError,
    MediaContentNotFoundError,
    MediaContentUnavailableError,
    supported_media_type,
)
from kronika.application.ports.gallery_preview import (
    GalleryPreviewCache,
    GalleryPreviewEncoder,
    OpenedGalleryPreview,
)
from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_analysis import LocalMediaAnalysisPreparer
from kronika.application.ports.media_content import MediaContentReader
from kronika.application.ports.media_repository import MediaRepository
from kronika.domain import Library, LibraryId, MediaId, MediaLocationId
from kronika.domain.media import LogicalMedia, MediaKind, MediaLocation, MediaLocationAvailability

GALLERY_PREVIEW_ALGORITHM_VERSION = "gallery-preview-jpeg-v1"
GALLERY_PREVIEW_MEDIA_TYPE = "image/jpeg"
GALLERY_PREVIEW_CACHE_FAILED_MESSAGE = "Gallery preview cache operation failed."


class GalleryPreviewState(StrEnum):
    """Application-level gallery preview derivative states."""

    READY = "ready"
    MISSING = "missing"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    UNSUPPORTED = "unsupported"
    GENERATION_UNAVAILABLE = "generation-unavailable"


class GalleryPreviewNotFoundError(RuntimeError):
    """Raised when the media/location relationship is absent or mismatched."""


class GalleryPreviewUnavailableError(RuntimeError):
    """Raised when a preview cannot be read or generated for current source state."""


class GalleryPreviewFailedError(RuntimeError):
    """Raised when preview handling fails unexpectedly."""


@dataclass(frozen=True, slots=True)
class GalleryPreviewSourceIdentity:
    """Current source identity observed behind the registered-root boundary."""

    size_bytes: int
    mtime_ns: int | None


@dataclass(frozen=True, slots=True)
class GalleryPreviewIdentity:
    """Versioned deterministic derivative identity."""

    algorithm_version: str
    source_identity: GalleryPreviewSourceIdentity
    cache_key: str


@dataclass(frozen=True, slots=True)
class GalleryPreviewItemStatus:
    """Preview state for one imported physical media location."""

    media_id: MediaId
    location_id: MediaLocationId
    library_id: LibraryId
    state: GalleryPreviewState
    cache_key: str | None


@dataclass(frozen=True, slots=True)
class GalleryPreviewLibrarySummary:
    """Per-library status summary."""

    library_id: LibraryId
    display_name: str
    total_count: int
    ready_count: int
    missing_count: int
    stale_count: int
    unavailable_count: int
    unsupported_count: int
    generation_unavailable_count: int


@dataclass(frozen=True, slots=True)
class GalleryPreviewStatus:
    """Read-only preview status for operator reporting."""

    total_count: int
    ready_count: int
    missing_count: int
    stale_count: int
    unavailable_count: int
    unsupported_count: int
    generation_unavailable_count: int
    libraries: tuple[GalleryPreviewLibrarySummary, ...]


@dataclass(frozen=True, slots=True)
class GalleryPreviewGenerationPlan:
    """Bounded explicit generation plan."""

    selected_library_ids: tuple[LibraryId, ...]
    total_considered: int
    ready_count: int
    to_generate: tuple[GalleryPreviewItemStatus, ...]
    max_items: int


@dataclass(frozen=True, slots=True)
class GalleryPreviewGenerationSummary:
    """Generation execution summary."""

    considered_count: int
    ready_count: int
    generated_count: int
    failed_count: int
    skipped_count: int


class GalleryPreviewService:
    """Resolve, inspect, generate, and read persistent gallery preview derivatives."""

    def __init__(
        self,
        media_repository: MediaRepository,
        library_repository: LibraryRepository,
        content_reader: MediaContentReader,
        preparer: LocalMediaAnalysisPreparer,
        encoder: GalleryPreviewEncoder,
        cache: GalleryPreviewCache,
    ) -> None:
        self._media_repository = media_repository
        self._library_repository = library_repository
        self._content_reader = content_reader
        self._preparer = preparer
        self._encoder = encoder
        self._cache = cache

    def status(self, *, library_id: LibraryId | None = None) -> GalleryPreviewStatus:
        libraries = self._selected_libraries(library_id)
        library_ids = {library.id for library in libraries}
        items = [
            self._status_for(media, location)
            for media, location in self._iter_media_locations()
            if location.library_id in library_ids
        ]
        return _status_from_items(items, libraries)

    def plan_generate(
        self,
        *,
        library_id: LibraryId | None,
        include_all: bool,
        max_items: int,
    ) -> GalleryPreviewGenerationPlan:
        if (library_id is None and not include_all) or (
            library_id is not None and include_all
        ):
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        if isinstance(max_items, bool) or max_items < 1:
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        libraries = self._selected_libraries(None if include_all else library_id)
        library_ids = {library.id for library in libraries}
        statuses = [
            self._status_for(media, location)
            for media, location in self._iter_media_locations()
            if location.library_id in library_ids
        ]
        to_generate = tuple(
            status
            for status in statuses
            if status.state in {GalleryPreviewState.MISSING, GalleryPreviewState.STALE}
        )[:max_items]
        return GalleryPreviewGenerationPlan(
            selected_library_ids=tuple(library.id for library in libraries),
            total_considered=len(statuses),
            ready_count=sum(1 for status in statuses if status.state == GalleryPreviewState.READY),
            to_generate=to_generate,
            max_items=max_items,
        )

    def generate(self, plan: GalleryPreviewGenerationPlan) -> GalleryPreviewGenerationSummary:
        generated = 0
        failed = 0
        ready = plan.ready_count
        for item in plan.to_generate:
            try:
                status = self.generate_one(item.media_id, item.location_id)
            except (GalleryPreviewUnavailableError, GalleryPreviewFailedError):
                failed += 1
                continue
            if status.state == GalleryPreviewState.READY:
                generated += 1
            else:
                failed += 1
        return GalleryPreviewGenerationSummary(
            considered_count=plan.total_considered,
            ready_count=ready,
            generated_count=generated,
            failed_count=failed,
            skipped_count=max(0, plan.total_considered - ready - len(plan.to_generate)),
        )

    def generate_one(self, media_id: MediaId, location_id: MediaLocationId) -> GalleryPreviewItemStatus:
        media, location, library = self._resolve_media_location(media_id, location_id)
        self._ensure_cache_root_outside_source_roots()
        status = self._status_for(media, location)
        if status.state == GalleryPreviewState.READY:
            return status
        if status.state not in {GalleryPreviewState.MISSING, GalleryPreviewState.STALE}:
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        assert status.cache_key is not None
        try:
            prepared = self._preparer.prepare(
                library.root,
                AnalysisRelativePath(location.relative_path.value),
            )
            frame = prepared.representative_frames[0]
            image = self._encoder.encode_frame(frame)
            if image.media_type != GALLERY_PREVIEW_MEDIA_TYPE:
                raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE)
            self._cache.publish(status.cache_key, image)
        except (MediaAnalysisUnavailableError, MediaAnalysisFailedError):
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE) from None
        except GalleryPreviewUnavailableError:
            raise
        except Exception:
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None
        return self._status_for(media, location)

    def open_ready(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> OpenedGalleryPreview:
        media, location, _library = self._resolve_media_location(media_id, location_id)
        status = self._status_for(media, location)
        if status.state != GalleryPreviewState.READY or status.cache_key is None:
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        try:
            return self._cache.open(status.cache_key)
        except GalleryPreviewUnavailableError:
            raise
        except Exception:
            raise GalleryPreviewFailedError(GALLERY_PREVIEW_CACHE_FAILED_MESSAGE) from None

    def _status_for(
        self,
        media: LogicalMedia,
        location: MediaLocation,
    ) -> GalleryPreviewItemStatus:
        if location.availability != MediaLocationAvailability.AVAILABLE:
            return _item_status(media, location, GalleryPreviewState.UNAVAILABLE, None)
        if not _is_supported(media.kind, location):
            return _item_status(media, location, GalleryPreviewState.UNSUPPORTED, None)
        try:
            identity = self._compute_identity(media, location)
        except MediaContentUnavailableError:
            return _item_status(media, location, GalleryPreviewState.UNAVAILABLE, None)
        except Exception:
            return _item_status(media, location, GalleryPreviewState.GENERATION_UNAVAILABLE, None)
        if self._cache.contains_current(identity.cache_key):
            return _item_status(media, location, GalleryPreviewState.READY, identity.cache_key)
        if self._cache.contains_any_for_location(
            GALLERY_PREVIEW_ALGORITHM_VERSION,
            location.id.to_string(),
        ):
            return _item_status(media, location, GalleryPreviewState.STALE, identity.cache_key)
        return _item_status(media, location, GalleryPreviewState.MISSING, identity.cache_key)

    def _compute_identity(
        self,
        media: LogicalMedia,
        location: MediaLocation,
    ) -> GalleryPreviewIdentity:
        library = self._library_repository.get(location.library_id)
        if library is None:
            raise MediaContentUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        opened = self._content_reader.open(library.root, location.relative_path, media.kind)
        opened.close()
        source_identity = GalleryPreviewSourceIdentity(
            size_bytes=opened.byte_size,
            mtime_ns=opened.mtime_ns,
        )
        payload = {
            "algorithm_version": GALLERY_PREVIEW_ALGORITHM_VERSION,
            "media_id": media.id.to_string(),
            "location_id": location.id.to_string(),
            "media_kind": media.kind.value,
            "source_size_bytes": source_identity.size_bytes,
            "source_mtime_ns": source_identity.mtime_ns,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        cache_key = f"{GALLERY_PREVIEW_ALGORITHM_VERSION}/{location.id.to_string()}/{digest}.jpg"
        return GalleryPreviewIdentity(
            algorithm_version=GALLERY_PREVIEW_ALGORITHM_VERSION,
            source_identity=source_identity,
            cache_key=cache_key,
        )

    def _resolve_media_location(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> tuple[LogicalMedia, MediaLocation, Library]:
        media = self._media_repository.get_media(media_id)
        if media is None:
            raise GalleryPreviewNotFoundError(MEDIA_NOT_FOUND_MESSAGE)
        location = self._media_repository.get_location(location_id)
        if location is None or location.media_id != media_id:
            raise GalleryPreviewNotFoundError(MEDIA_NOT_FOUND_MESSAGE)
        library = self._library_repository.get(location.library_id)
        if library is None:
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        return media, location, library

    def _iter_media_locations(self) -> tuple[tuple[LogicalMedia, MediaLocation], ...]:
        media_by_id = {media.id: media for media in self._media_repository.list_media()}
        items: list[tuple[LogicalMedia, MediaLocation]] = []
        for location in self._media_repository.list_all_locations():
            media = media_by_id.get(location.media_id)
            if media is not None:
                items.append((media, location))
        return tuple(items)

    def _selected_libraries(self, library_id: LibraryId | None) -> tuple[Library, ...]:
        if library_id is not None:
            library = self._library_repository.get(library_id)
            if library is None:
                raise GalleryPreviewNotFoundError(MEDIA_NOT_FOUND_MESSAGE)
            return (library,)
        return self._library_repository.list_all()

    def _ensure_cache_root_outside_source_roots(self) -> None:
        cache_root = getattr(self._cache, "root", None)
        if cache_root is None:
            return
        try:
            resolved_cache_root = Path(cache_root).resolve(strict=False)
            for library in self._library_repository.list_all():
                source_root = Path(library.root.path).resolve(strict=False)
                if resolved_cache_root == source_root or source_root in resolved_cache_root.parents:
                    raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE)
        except GalleryPreviewUnavailableError:
            raise
        except OSError:
            raise GalleryPreviewUnavailableError(MEDIA_CONTENT_UNAVAILABLE_MESSAGE) from None


def _item_status(
    media: LogicalMedia,
    location: MediaLocation,
    state: GalleryPreviewState,
    cache_key: str | None,
) -> GalleryPreviewItemStatus:
    return GalleryPreviewItemStatus(
        media_id=media.id,
        location_id=location.id,
        library_id=location.library_id,
        state=state,
        cache_key=cache_key,
    )


def _is_supported(kind: MediaKind, location: MediaLocation) -> bool:
    # Still images use identity-only original content in Gallery/Details.
    # The ffmpeg-backed gallery-preview derivative pipeline stays video/GIF-only.
    if kind is MediaKind.IMAGE:
        return False
    extension = ""
    filename = location.relative_path.filename
    if "." in filename:
        extension = "." + filename.rsplit(".", 1)[-1].lower()
    return supported_media_type(kind, extension) is not None


def _status_from_items(
    items: list[GalleryPreviewItemStatus],
    libraries: tuple[Library, ...],
) -> GalleryPreviewStatus:
    counts = Counter(item.state for item in items)
    summaries = []
    for library in libraries:
        library_items = [item for item in items if item.library_id == library.id]
        library_counts = Counter(item.state for item in library_items)
        summaries.append(
            GalleryPreviewLibrarySummary(
                library_id=library.id,
                display_name=library.display_name,
                total_count=len(library_items),
                ready_count=library_counts[GalleryPreviewState.READY],
                missing_count=library_counts[GalleryPreviewState.MISSING],
                stale_count=library_counts[GalleryPreviewState.STALE],
                unavailable_count=library_counts[GalleryPreviewState.UNAVAILABLE],
                unsupported_count=library_counts[GalleryPreviewState.UNSUPPORTED],
                generation_unavailable_count=library_counts[
                    GalleryPreviewState.GENERATION_UNAVAILABLE
                ],
            )
        )
    return GalleryPreviewStatus(
        total_count=len(items),
        ready_count=counts[GalleryPreviewState.READY],
        missing_count=counts[GalleryPreviewState.MISSING],
        stale_count=counts[GalleryPreviewState.STALE],
        unavailable_count=counts[GalleryPreviewState.UNAVAILABLE],
        unsupported_count=counts[GalleryPreviewState.UNSUPPORTED],
        generation_unavailable_count=counts[GalleryPreviewState.GENERATION_UNAVAILABLE],
        libraries=tuple(summaries),
    )
