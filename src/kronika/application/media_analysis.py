"""Application boundary for deterministic read-only local media analysis preparation."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING

from kronika.application.library_scan import (
    LibraryScanCandidateKind,
    classify_candidate_extension,
)
from kronika.application.ports.library_repository import LibraryRepository
from kronika.domain import LibraryId

if TYPE_CHECKING:
    from kronika.application.ports.media_analysis import LocalMediaAnalysisPreparer

INVALID_MEDIA_PATH_MESSAGE = "Invalid media relative path."
INVALID_TECHNICAL_METADATA_MESSAGE = "Invalid technical metadata."
INVALID_REPRESENTATIVE_FRAME_MESSAGE = "Invalid representative frame."
INVALID_PREPARED_RESULT_MESSAGE = "Invalid prepared analysis result."
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
PREPARATION_UNAVAILABLE_MESSAGE = "Local media analysis preparation is not available."
PREPARATION_FAILED_MESSAGE = "Local media analysis preparation failed."

REQUESTED_FRAME_COUNT = 3
FFPROBE_TIMEOUT_SECONDS = 15
FFMPEG_FRAME_TIMEOUT_SECONDS = 30
FFPROBE_STDOUT_MAX_BYTES = 1_048_576
SUBPROCESS_STDERR_MAX_BYTES = 65_536
PNG_PAYLOAD_MAX_BYTES = 5_242_880
AGGREGATE_PNG_PAYLOAD_MAX_BYTES = 15_728_640
MAX_OUTPUT_DIMENSION = 1024
MAX_REPRESENTATIVE_FRAMES = 3

MAX_BOUNDED_NAME_LENGTH = 128
MAX_WARNING_COUNT = 32
MAX_WARNING_LENGTH = 256
MAX_TOOL_VERSION_LENGTH = 256

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CONTROL_CHAR_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_WINDOWS_DRIVE_PATTERN = re.compile(r"^[A-Za-z]:[/\\]")
_UNC_PATTERN = re.compile(r"^\\\\")


class FrameNestMediaAnalysisError(ValueError):
    """Sanitized error raised when media analysis inputs are invalid."""


class MediaAnalysisNotFoundError(RuntimeError):
    """Raised when the requested library is not registered."""


class MediaAnalysisUnavailableError(RuntimeError):
    """Raised when preparation cannot run for the registered library or candidate."""


class MediaAnalysisFailedError(RuntimeError):
    """Raised when an unexpected preparation implementation failure occurs."""


def _validate_bounded_name(value: object, *, message: str) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_BOUNDED_NAME_LENGTH:
        raise FrameNestMediaAnalysisError(message)
    if _CONTROL_CHAR_PATTERN.search(value):
        raise FrameNestMediaAnalysisError(message)
    return value


def _validate_non_negative_int(value: object, *, message: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise FrameNestMediaAnalysisError(message)
    if value < 0:
        raise FrameNestMediaAnalysisError(message)
    return value


def _validate_positive_int(value: object, *, message: str) -> int:
    validated = _validate_non_negative_int(value, message=message)
    if validated <= 0:
        raise FrameNestMediaAnalysisError(message)
    return validated


def _validate_warning(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > MAX_WARNING_LENGTH:
        raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
    if _CONTROL_CHAR_PATTERN.search(value):
        raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
    return value


@dataclass(frozen=True, slots=True)
class MediaRelativePath:
    """Canonical forward-slash relative path for one explicit scan candidate."""

    value: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "value", _validate_media_relative_path(self.value))

    def __str__(self) -> str:
        return self.value


def _validate_media_relative_path(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    if value.startswith("/") or "\\" in value:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    if _WINDOWS_DRIVE_PATTERN.match(value) or _UNC_PATTERN.match(value):
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    if _CONTROL_CHAR_PATTERN.search(value):
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    parts = value.split("/")
    if any(not part or part in (".", "..") for part in parts):
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    if any(part.startswith(".") for part in parts):
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    return value


def candidate_kind_for_relative_path(relative_path: MediaRelativePath) -> LibraryScanCandidateKind:
    """Return the candidate kind for a validated relative path extension."""
    extension = ""
    if "." in relative_path.value:
        extension = "." + relative_path.value.rsplit(".", 1)[-1].lower()
    kind = classify_candidate_extension(extension)
    if kind is None:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    return kind


def compute_target_timestamps_ms(duration_ms: int | None) -> tuple[int, ...]:
    """Return deterministic target timestamps for representative-frame extraction."""
    if duration_ms is None or duration_ms <= 0:
        return (0,)
    raw_targets = (
        duration_ms * 10 // 100,
        duration_ms * 50 // 100,
        duration_ms * 90 // 100,
    )
    seen: set[int] = set()
    ordered: list[int] = []
    for target in raw_targets:
        clamped = target
        if clamped >= duration_ms:
            clamped = duration_ms - 1
        if clamped < 0:
            clamped = 0
        if clamped not in seen:
            seen.add(clamped)
            ordered.append(clamped)
    return tuple(ordered)


def parse_duration_seconds_to_ms(value: str) -> int | None:
    """Convert a bounded ffprobe duration string to integer milliseconds."""
    try:
        seconds = Decimal(value)
    except (InvalidOperation, ValueError):
        return None
    if seconds < 0:
        return None
    millis = int(seconds * 1000)
    return millis


@dataclass(frozen=True, slots=True)
class TechnicalMetadata:
    """Bounded technical metadata for one analyzed candidate."""

    duration_ms: int | None
    width: int
    height: int
    video_codec: str
    container_formats: tuple[str, ...]
    has_audio: bool

    def __post_init__(self) -> None:
        duration = self.duration_ms
        if duration is not None:
            object.__setattr__(
                self,
                "duration_ms",
                _validate_non_negative_int(
                    duration,
                    message=INVALID_TECHNICAL_METADATA_MESSAGE,
                ),
            )
        object.__setattr__(
            self,
            "width",
            _validate_positive_int(self.width, message=INVALID_TECHNICAL_METADATA_MESSAGE),
        )
        object.__setattr__(
            self,
            "height",
            _validate_positive_int(self.height, message=INVALID_TECHNICAL_METADATA_MESSAGE),
        )
        object.__setattr__(
            self,
            "video_codec",
            _validate_bounded_name(self.video_codec, message=INVALID_TECHNICAL_METADATA_MESSAGE),
        )
        if not isinstance(self.container_formats, tuple):
            raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)
        normalized_formats: list[str] = []
        for item in self.container_formats:
            normalized_formats.append(
                _validate_bounded_name(item, message=INVALID_TECHNICAL_METADATA_MESSAGE)
            )
        object.__setattr__(self, "container_formats", tuple(normalized_formats))
        if not isinstance(self.has_audio, bool):
            raise FrameNestMediaAnalysisError(INVALID_TECHNICAL_METADATA_MESSAGE)


@dataclass(frozen=True, slots=True)
class RepresentativeFrame:
    """One in-memory representative PNG frame."""

    timestamp_ms: int
    mime_type: str
    sha256: str
    byte_size: int
    payload: bytes = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "timestamp_ms",
            _validate_non_negative_int(
                self.timestamp_ms,
                message=INVALID_REPRESENTATIVE_FRAME_MESSAGE,
            ),
        )
        if self.mime_type != "image/png":
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        if not isinstance(self.sha256, str) or not _SHA256_PATTERN.fullmatch(self.sha256):
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        if not isinstance(self.payload, bytes) or not self.payload:
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        if len(self.payload) > PNG_PAYLOAD_MAX_BYTES:
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        if not self.payload.startswith(PNG_SIGNATURE):
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        digest = hashlib.sha256(self.payload).hexdigest()
        if digest != self.sha256:
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)
        object.__setattr__(
            self,
            "byte_size",
            _validate_positive_int(self.byte_size, message=INVALID_REPRESENTATIVE_FRAME_MESSAGE),
        )
        if self.byte_size != len(self.payload):
            raise FrameNestMediaAnalysisError(INVALID_REPRESENTATIVE_FRAME_MESSAGE)


def build_representative_frame(*, timestamp_ms: int, payload: bytes) -> RepresentativeFrame:
    """Construct one validated representative frame from PNG payload bytes."""
    return RepresentativeFrame(
        timestamp_ms=timestamp_ms,
        mime_type="image/png",
        sha256=hashlib.sha256(payload).hexdigest(),
        byte_size=len(payload),
        payload=payload,
    )


def deduplicate_representative_frames(
    frames: tuple[RepresentativeFrame, ...],
) -> tuple[RepresentativeFrame, ...]:
    """Remove exact PNG digest duplicates while preserving first occurrence order."""
    seen: set[str] = set()
    unique: list[RepresentativeFrame] = []
    for frame in frames:
        if frame.sha256 in seen:
            continue
        seen.add(frame.sha256)
        unique.append(frame)
    return tuple(unique)


@dataclass(frozen=True, slots=True)
class PreparedAnalysisResult:
    """Complete typed preparation result for one registered library candidate."""

    relative_path: MediaRelativePath
    candidate_kind: LibraryScanCandidateKind
    technical_metadata: TechnicalMetadata
    representative_frames: tuple[RepresentativeFrame, ...]
    requested_frame_count: int
    warnings: tuple[str, ...]
    ffprobe_version: str
    ffmpeg_version: str

    def __post_init__(self) -> None:
        if not isinstance(self.relative_path, MediaRelativePath):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if not isinstance(self.candidate_kind, LibraryScanCandidateKind):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if not isinstance(self.technical_metadata, TechnicalMetadata):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if not isinstance(self.representative_frames, tuple):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if len(self.representative_frames) > MAX_REPRESENTATIVE_FRAMES:
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        digests = [frame.sha256 for frame in self.representative_frames]
        if len(digests) != len(set(digests)):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        total_payload = sum(frame.byte_size for frame in self.representative_frames)
        if total_payload > AGGREGATE_PNG_PAYLOAD_MAX_BYTES:
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        object.__setattr__(
            self,
            "requested_frame_count",
            _validate_positive_int(
                self.requested_frame_count,
                message=INVALID_PREPARED_RESULT_MESSAGE,
            ),
        )
        if self.requested_frame_count != REQUESTED_FRAME_COUNT:
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if not self.representative_frames:
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if not isinstance(self.warnings, tuple):
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        if len(self.warnings) > MAX_WARNING_COUNT:
            raise FrameNestMediaAnalysisError(INVALID_PREPARED_RESULT_MESSAGE)
        object.__setattr__(
            self,
            "warnings",
            tuple(_validate_warning(item) for item in self.warnings),
        )
        object.__setattr__(
            self,
            "ffprobe_version",
            _validate_bounded_name(self.ffprobe_version, message=INVALID_PREPARED_RESULT_MESSAGE),
        )
        object.__setattr__(
            self,
            "ffmpeg_version",
            _validate_bounded_name(self.ffmpeg_version, message=INVALID_PREPARED_RESULT_MESSAGE),
        )


class PrepareLocalMediaAnalysis:
    """Prepare one registered library candidate through the preparation port."""

    def __init__(
        self,
        repository: LibraryRepository,
        preparer: LocalMediaAnalysisPreparer,
    ) -> None:
        self._repository = repository
        self._preparer = preparer

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        library = self._repository.get(library_id)
        if library is None:
            raise MediaAnalysisNotFoundError(LIBRARY_NOT_FOUND_MESSAGE)
        return self._preparer.prepare(library.root, relative_path)
