"""ffprobe/ffmpeg cover source probing and arbitrary-timestamp frame extraction."""

from __future__ import annotations

from kronika.application.media_analysis import (
    FFMPEG_FRAME_TIMEOUT_SECONDS,
    FFPROBE_STDOUT_MAX_BYTES,
    FFPROBE_TIMEOUT_SECONDS,
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PNG_PAYLOAD_MAX_BYTES,
    PREPARATION_FAILED_MESSAGE,
    PREPARATION_UNAVAILABLE_MESSAGE,
    SUBPROCESS_STDERR_MAX_BYTES,
    RepresentativeFrame,
    build_representative_frame,
)
from kronika.application.ports.cover_source_analysis import CoverSourceProbe
from kronika.domain import LibraryRoot
from kronika.domain.media import MediaKind
from kronika.infrastructure.media_analysis.ffmpeg import build_ffmpeg_frame_argv
from kronika.infrastructure.media_analysis.ffprobe import probe_media_metadata
from kronika.infrastructure.media_analysis.filesystem import resolve_safe_candidate_path
from kronika.infrastructure.media_analysis.process import (
    ProcessExecutionError,
    ProcessRunner,
    SubprocessRunner,
)
from kronika.infrastructure.media_analysis.still_image import prepare_still_image_analysis
from kronika.infrastructure.media_analysis.tools import (
    sanitize_retained_stderr,
    resolve_ffmpeg,
    resolve_ffprobe,
)
from kronika.infrastructure.filesystem.media_content import LocalMediaContentReader

_UNAVAILABLE = MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
_FAILED = MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE)


class LocalCoverSourceAdapter:
    """Read-only authoritative cover source probing and extraction adapter.

    Reuses the existing ffprobe/ffmpeg process boundary, registered-root
    containment resolution, and in-memory PNG extraction without persisting any
    frame or exposing any source path.
    """

    def __init__(
        self,
        runner: ProcessRunner | None = None,
        content_reader: LocalMediaContentReader | None = None,
    ) -> None:
        self._runner = runner or SubprocessRunner()
        self._content_reader = content_reader or LocalMediaContentReader()

    def probe(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
        kind: MediaKind,
    ) -> CoverSourceProbe:
        try:
            candidate_path, _extension = resolve_safe_candidate_path(root, relative_path)
            if kind is MediaKind.IMAGE:
                opened = self._content_reader.open(root, relative_path, kind)
                try:
                    byte_size = opened.byte_size
                    mtime_ns = opened.mtime_ns
                finally:
                    opened.close()
                return CoverSourceProbe(
                    duration_ms=None,
                    source_size_bytes=byte_size,
                    source_mtime_ns=mtime_ns,
                )
            absolute_media_path = str(candidate_path)
            ffprobe_executable, _version = resolve_ffprobe(self._runner)
            metadata = probe_media_metadata(
                self._runner,
                ffprobe_executable=ffprobe_executable,
                media_path=absolute_media_path,
            )
            opened = self._content_reader.open(root, relative_path, kind)
            byte_size = opened.byte_size
            mtime_ns = opened.mtime_ns
            opened.close()
            return CoverSourceProbe(
                duration_ms=metadata.duration_ms,
                source_size_bytes=byte_size,
                source_mtime_ns=mtime_ns,
            )
        except MediaAnalysisUnavailableError:
            raise
        except MediaAnalysisFailedError:
            raise
        except FrameNestMediaAnalysisError:
            raise _UNAVAILABLE from None
        except Exception as exc:
            if isinstance(exc, (MediaAnalysisUnavailableError, MediaAnalysisFailedError)):
                raise
            raise _FAILED from None

    def extract_frame(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
        kind: MediaKind,
        timestamp_ms: int,
    ) -> RepresentativeFrame:
        try:
            candidate_path, _extension = resolve_safe_candidate_path(root, relative_path)
            if kind is MediaKind.IMAGE:
                prepared = prepare_still_image_analysis(candidate_path, relative_path)
                frames = prepared.representative_frames
                if len(frames) != 1 or frames[0].timestamp_ms != 0:
                    raise _FAILED from None
                return frames[0]
            absolute_media_path = str(candidate_path)
            ffmpeg_executable, _version = resolve_ffmpeg(self._runner)
            result = self._runner.run(
                executable=ffmpeg_executable,
                argv=build_ffmpeg_frame_argv(
                    media_path=absolute_media_path,
                    timestamp_ms=timestamp_ms,
                ),
                timeout_seconds=FFMPEG_FRAME_TIMEOUT_SECONDS,
                stdout_max_bytes=PNG_PAYLOAD_MAX_BYTES,
                stderr_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
            )
        except ProcessExecutionError:
            raise _FAILED from None
        if result.returncode != 0:
            sanitize_retained_stderr(result.stderr)
            raise _FAILED from None
        try:
            return build_representative_frame(
                timestamp_ms=timestamp_ms,
                payload=result.stdout,
            )
        except FrameNestMediaAnalysisError:
            raise _FAILED from None
