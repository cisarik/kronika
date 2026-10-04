"""Local media analysis preparation adapter."""

from __future__ import annotations

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    INVALID_MEDIA_PATH_MESSAGE,
    PREPARATION_FAILED_MESSAGE,
    PREPARATION_UNAVAILABLE_MESSAGE,
    REQUESTED_FRAME_COUNT,
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    candidate_kind_for_relative_path,
)
from kronika.domain import LibraryRoot
from kronika.infrastructure.media_analysis.ffmpeg import extract_representative_frames
from kronika.infrastructure.media_analysis.ffprobe import probe_media_metadata
from kronika.infrastructure.media_analysis.filesystem import resolve_safe_candidate_path
from kronika.infrastructure.media_analysis.process import ProcessRunner, SubprocessRunner
from kronika.infrastructure.media_analysis.still_image import prepare_still_image_analysis
from kronika.infrastructure.media_analysis.tools import resolve_ffmpeg, resolve_ffprobe

_UNAVAILABLE = MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
_FAILED = MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)


class LocalMediaAnalysisAdapter:
    """Read-only preparation adapter for cataloged library media."""

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        self._runner = runner or SubprocessRunner()

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        try:
            candidate_path, _extension = resolve_safe_candidate_path(root, relative_path)
            kind = candidate_kind_for_relative_path(relative_path)
            if kind is LibraryScanCandidateKind.IMAGE:
                return prepare_still_image_analysis(candidate_path, relative_path)
            ffprobe_executable, ffprobe_version = resolve_ffprobe(self._runner)
            ffmpeg_executable, ffmpeg_version = resolve_ffmpeg(self._runner)
            absolute_media_path = str(candidate_path)
            metadata = probe_media_metadata(
                self._runner,
                ffprobe_executable=ffprobe_executable,
                media_path=absolute_media_path,
            )
            frames, warnings = extract_representative_frames(
                self._runner,
                ffmpeg_executable=ffmpeg_executable,
                media_path=absolute_media_path,
                duration_ms=metadata.duration_ms,
            )
            return PreparedAnalysisResult(
                relative_path=relative_path,
                candidate_kind=kind,
                technical_metadata=metadata,
                representative_frames=frames,
                requested_frame_count=REQUESTED_FRAME_COUNT,
                warnings=warnings,
                ffprobe_version=ffprobe_version,
                ffmpeg_version=ffmpeg_version,
            )
        except MediaAnalysisUnavailableError:
            raise
        except MediaAnalysisFailedError:
            raise
        except FrameNestMediaAnalysisError as exc:
            message = str(exc)
            if message in {
                PREPARATION_UNAVAILABLE_MESSAGE,
                INVALID_MEDIA_PATH_MESSAGE,
            }:
                raise _UNAVAILABLE from None
            raise _UNAVAILABLE from None
        except Exception as exc:
            if isinstance(exc, (MediaAnalysisUnavailableError, MediaAnalysisFailedError)):
                raise
            raise _FAILED from None
