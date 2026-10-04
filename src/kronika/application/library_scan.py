"""Application boundary for bounded read-only library scan preview."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from kronika.application.ports.library_repository import LibraryRepository
from kronika.domain import LibraryId

if TYPE_CHECKING:
    from kronika.application.ports.library_scanner import LibraryScanner

INVALID_SCAN_LIMITS_MESSAGE = "Invalid library scan limits."
INVALID_SCAN_CANDIDATE_MESSAGE = "Invalid library scan candidate."
INVALID_SCAN_SUMMARY_MESSAGE = "Invalid library scan summary."
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
SCAN_UNAVAILABLE_MESSAGE = "Library scan preview is not available."
SCAN_FAILED_MESSAGE = "Library scan failed."

DEFAULT_MAX_ENTRIES = 100_000
DEFAULT_MAX_CANDIDATES = 1000
MIN_MAX_ENTRIES = 1
MAX_MAX_ENTRIES = 1_000_000
MIN_MAX_CANDIDATES = 1
MAX_MAX_CANDIDATES = 10_000

VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".avi",
        ".flv",
        ".m2ts",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ogv",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
)
GIF_EXTENSIONS = frozenset({".gif"})
IMAGE_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})


class FrameNestLibraryScanError(ValueError):
    """Sanitized error raised when scan preview inputs are invalid."""


class LibraryScanNotFoundError(RuntimeError):
    """Raised when the requested library is not registered."""


class LibraryScanUnavailableError(RuntimeError):
    """Raised when the registered library root cannot be scanned."""


class LibraryScanFailedError(RuntimeError):
    """Raised when an unexpected scan implementation failure occurs."""


class LibraryScanCandidateKind(StrEnum):
    """Extension-hint candidate classification."""

    VIDEO = "video"
    GIF = "gif"
    IMAGE = "image"


def classify_candidate_extension(extension: str) -> LibraryScanCandidateKind | None:
    """Return the candidate kind for a lowercase extension, or None."""
    if extension in VIDEO_EXTENSIONS:
        return LibraryScanCandidateKind.VIDEO
    if extension in GIF_EXTENSIONS:
        return LibraryScanCandidateKind.GIF
    if extension in IMAGE_EXTENSIONS:
        return LibraryScanCandidateKind.IMAGE
    return None


def _validate_non_negative_int(value: object, *, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameNestLibraryScanError(message)
    if value < 0:
        raise FrameNestLibraryScanError(message)
    return value


def _validate_positive_bounded_int(
    value: object,
    *,
    minimum: int,
    maximum: int,
    message: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameNestLibraryScanError(message)
    if value < minimum or value > maximum:
        raise FrameNestLibraryScanError(message)
    return value


def _validate_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise FrameNestLibraryScanError(INVALID_SCAN_CANDIDATE_MESSAGE)
    if value.startswith("/") or "\\" in value:
        raise FrameNestLibraryScanError(INVALID_SCAN_CANDIDATE_MESSAGE)
    parts = value.split("/")
    if any(part in (".", "..") or not part for part in parts):
        raise FrameNestLibraryScanError(INVALID_SCAN_CANDIDATE_MESSAGE)
    return value


def _validate_extension(value: object) -> str:
    if not isinstance(value, str) or not value.startswith(".") or value != value.lower():
        raise FrameNestLibraryScanError(INVALID_SCAN_CANDIDATE_MESSAGE)
    return value


@dataclass(frozen=True, slots=True)
class LibraryScanLimits:
    """Bounded work limits for one scan preview operation."""

    max_entries: int
    max_candidates: int

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "max_entries",
            _validate_positive_bounded_int(
                self.max_entries,
                minimum=MIN_MAX_ENTRIES,
                maximum=MAX_MAX_ENTRIES,
                message=INVALID_SCAN_LIMITS_MESSAGE,
            ),
        )
        object.__setattr__(
            self,
            "max_candidates",
            _validate_positive_bounded_int(
                self.max_candidates,
                minimum=MIN_MAX_CANDIDATES,
                maximum=MAX_MAX_CANDIDATES,
                message=INVALID_SCAN_LIMITS_MESSAGE,
            ),
        )


def default_scan_limits() -> LibraryScanLimits:
    """Return the default accepted scan preview limits."""
    return LibraryScanLimits(
        max_entries=DEFAULT_MAX_ENTRIES,
        max_candidates=DEFAULT_MAX_CANDIDATES,
    )


@dataclass(frozen=True, slots=True)
class LibraryScanCandidate:
    """One extension-classified media candidate within a scan preview."""

    relative_path: str
    kind: LibraryScanCandidateKind
    extension: str
    size_bytes: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "relative_path", _validate_relative_path(self.relative_path))
        if not isinstance(self.kind, LibraryScanCandidateKind):
            raise FrameNestLibraryScanError(INVALID_SCAN_CANDIDATE_MESSAGE)
        object.__setattr__(self, "extension", _validate_extension(self.extension))
        object.__setattr__(
            self,
            "size_bytes",
            _validate_non_negative_int(
                self.size_bytes,
                message=INVALID_SCAN_CANDIDATE_MESSAGE,
            ),
        )


@dataclass(frozen=True, slots=True)
class LibraryScanSummary:
    """Aggregate counters for one scan preview."""

    entries_seen: int
    directories_seen: int
    regular_files_seen: int
    candidate_files_seen: int
    candidate_bytes_seen: int
    skipped_hidden_entries: int
    skipped_symlink_entries: int
    skipped_other_entries: int
    inaccessible_entries: int
    truncated: bool
    candidates_truncated: bool

    def __post_init__(self) -> None:
        for field_name in (
            "entries_seen",
            "directories_seen",
            "regular_files_seen",
            "candidate_files_seen",
            "candidate_bytes_seen",
            "skipped_hidden_entries",
            "skipped_symlink_entries",
            "skipped_other_entries",
            "inaccessible_entries",
        ):
            value = getattr(self, field_name)
            object.__setattr__(
                self,
                field_name,
                _validate_non_negative_int(
                    value,
                    message=INVALID_SCAN_SUMMARY_MESSAGE,
                ),
            )
        if not isinstance(self.truncated, bool):
            raise FrameNestLibraryScanError(INVALID_SCAN_SUMMARY_MESSAGE)
        if not isinstance(self.candidates_truncated, bool):
            raise FrameNestLibraryScanError(INVALID_SCAN_SUMMARY_MESSAGE)


@dataclass(frozen=True, slots=True)
class LibraryFilesystemScanResult:
    """Filesystem-level scan preview without catalog identity."""

    summary: LibraryScanSummary
    candidates: tuple[LibraryScanCandidate, ...]


@dataclass(frozen=True, slots=True)
class LibraryScanPreviewResult:
    """Complete typed scan preview for one registered library."""

    library_id: LibraryId
    limits: LibraryScanLimits
    summary: LibraryScanSummary
    candidates: tuple[LibraryScanCandidate, ...]


class PreviewLibraryScan:
    """Preview one registered library through the scanner port."""

    def __init__(
        self,
        repository: LibraryRepository,
        scanner: LibraryScanner,
    ) -> None:
        self._repository = repository
        self._scanner = scanner

    def execute(
        self,
        library_id: LibraryId,
        limits: LibraryScanLimits,
    ) -> LibraryScanPreviewResult:
        library = self._repository.get(library_id)
        if library is None:
            raise LibraryScanNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        filesystem_result = self._scanner.preview(library.root, limits)
        return LibraryScanPreviewResult(
            library_id=library_id,
            limits=limits,
            summary=filesystem_result.summary,
            candidates=filesystem_result.candidates,
        )
