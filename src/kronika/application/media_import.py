"""Application boundary for explicit persistent media import."""

from __future__ import annotations

from dataclasses import dataclass
import time
from typing import TYPE_CHECKING, Protocol

from kronika.application.library_scan import (
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LIBRARY_NOT_FOUND_MESSAGE,
)
from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_repository import (
    FrameNestMediaRepositoryError,
    MediaLocationNotUniqueError,
    MediaRepository,
)
from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)

if TYPE_CHECKING:
    from kronika.application.ports.library_scanner import LibraryScanner

INVALID_MEDIA_IMPORT_MESSAGE = "Invalid media import result."
MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_MESSAGE = "Media import candidate is not available."
MEDIA_IMPORT_FAILED_MESSAGE = "Media import failed."


class ClockMs(Protocol):
    """Callable source of non-negative millisecond timestamps."""

    def __call__(self) -> int:
        """Return the current timestamp in milliseconds."""


class MediaIdFactory(Protocol):
    """Callable source of logical media identities."""

    def __call__(self) -> MediaId:
        """Return a new media identity."""


class MediaLocationIdFactory(Protocol):
    """Callable source of media-location identities."""

    def __call__(self) -> MediaLocationId:
        """Return a new media-location identity."""


class MediaImportCandidateUnavailableError(RuntimeError):
    """Raised when the selected candidate is unavailable in the fresh scan."""


class MediaImportFailedError(RuntimeError):
    """Raised when persistent media import fails after sanitized recovery."""


@dataclass(frozen=True, slots=True)
class MediaImportResult:
    """Persistent import result for one selected scan candidate."""

    library_id: LibraryId
    media: LogicalMedia
    location: MediaLocation
    created: bool

    def __post_init__(self) -> None:
        if not isinstance(self.library_id, LibraryId):
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)
        if not isinstance(self.media, LogicalMedia):
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)
        if not isinstance(self.location, MediaLocation):
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)
        if self.location.library_id != self.library_id:
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)
        if self.location.media_id != self.media.id:
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)
        if not isinstance(self.created, bool):
            raise MediaImportFailedError(INVALID_MEDIA_IMPORT_MESSAGE)


class ImportMediaFromScanCandidate:
    """Persist one explicitly selected candidate from a bounded library scan."""

    def __init__(
        self,
        library_repository: LibraryRepository,
        media_repository: MediaRepository,
        scanner: LibraryScanner,
        *,
        media_id_factory: MediaIdFactory | None = None,
        location_id_factory: MediaLocationIdFactory | None = None,
        clock_ms: ClockMs | None = None,
    ) -> None:
        self._library_repository = library_repository
        self._media_repository = media_repository
        self._scanner = scanner
        self._media_id_factory = media_id_factory if media_id_factory is not None else MediaId.new
        self._location_id_factory = (
            location_id_factory if location_id_factory is not None else MediaLocationId.new
        )
        self._clock_ms = clock_ms if clock_ms is not None else _utc_now_ms

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
        limits: LibraryScanLimits,
    ) -> MediaImportResult:
        library = self._library_repository.get(library_id)
        if library is None:
            raise LibraryScanNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        filesystem_result = self._scanner.preview(library.root, limits)
        candidate = _find_candidate(filesystem_result.candidates, relative_path)
        if candidate is None:
            raise MediaImportCandidateUnavailableError(MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_MESSAGE)

        existing = self._media_repository.get_location_by_library_path(library_id, relative_path)
        if existing is not None:
            return self._existing_import_result(library_id, existing)

        media_kind = _media_kind_for_candidate(candidate)
        now_ms = _call_clock_ms(self._clock_ms)
        media = LogicalMedia(
            id=_call_media_id_factory(self._media_id_factory),
            kind=media_kind,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        location = MediaLocation(
            id=_call_location_id_factory(self._location_id_factory),
            media_id=media.id,
            library_id=library_id,
            relative_path=relative_path,
            availability=MediaLocationAvailability.AVAILABLE,
            observed_size_bytes=candidate.size_bytes,
            observed_mtime_ns=None,
            created_at_ms=now_ms,
            updated_at_ms=now_ms,
        )
        try:
            self._media_repository.add_media_with_location(media, location)
        except MediaLocationNotUniqueError:
            recovered = self._media_repository.get_location_by_library_path(
                library_id,
                relative_path,
            )
            if recovered is None:
                raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE) from None
            return self._existing_import_result(library_id, recovered)
        except FrameNestMediaRepositoryError as exc:
            raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE) from exc

        return MediaImportResult(
            library_id=library_id,
            media=media,
            location=location,
            created=True,
        )

    def _existing_import_result(
        self,
        library_id: LibraryId,
        location: MediaLocation,
    ) -> MediaImportResult:
        media = self._media_repository.get_media(location.media_id)
        if media is None:
            raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE)
        return MediaImportResult(
            library_id=library_id,
            media=media,
            location=location,
            created=False,
        )


def _find_candidate(
    candidates: tuple[LibraryScanCandidate, ...],
    relative_path: MediaRelativePath,
) -> LibraryScanCandidate | None:
    for candidate in candidates:
        if candidate.relative_path == relative_path.value:
            return candidate
    return None


def _media_kind_for_candidate(candidate: LibraryScanCandidate) -> MediaKind:
    if candidate.kind == LibraryScanCandidateKind.GIF:
        return MediaKind.ANIMATED_IMAGE
    if candidate.kind == LibraryScanCandidateKind.IMAGE:
        return MediaKind.IMAGE
    return MediaKind.VIDEO


def _utc_now_ms() -> int:
    return time.time_ns() // 1_000_000


def _call_clock_ms(clock_ms: ClockMs) -> int:
    value = clock_ms()
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE)
    return value


def _call_media_id_factory(factory: MediaIdFactory) -> MediaId:
    media_id = factory()
    if not isinstance(media_id, MediaId):
        raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE)
    return media_id


def _call_location_id_factory(factory: MediaLocationIdFactory) -> MediaLocationId:
    location_id = factory()
    if not isinstance(location_id, MediaLocationId):
        raise MediaImportFailedError(MEDIA_IMPORT_FAILED_MESSAGE)
    return location_id
