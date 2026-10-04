"""Infrastructure adapter for movie-identification contact-sheet preparation."""

from __future__ import annotations

from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PREPARATION_FAILED_MESSAGE,
    PREPARATION_UNAVAILABLE_MESSAGE,
)
from kronika.application.movie_identification import LocalMovieHints
from kronika.application.ports.movie_identification import (
    MovieIdentificationPreparer,
    PreparedMovieIdentification,
)
from kronika.domain import LibraryRoot
from kronika.infrastructure.media_analysis.contact_sheet import (
    build_local_movie_hints,
    extract_and_compose_contact_sheet,
)
from kronika.infrastructure.media_analysis.ffprobe import probe_media_metadata
from kronika.infrastructure.media_analysis.filesystem import resolve_safe_candidate_path
from kronika.infrastructure.media_analysis.process import ProcessRunner, SubprocessRunner
from kronika.infrastructure.media_analysis.tools import resolve_ffmpeg, resolve_ffprobe


class LocalMovieIdentificationAdapter:
    """Read-only local preparation for movie-identification contact sheets."""

    def __init__(self, runner: ProcessRunner | None = None) -> None:
        self._runner = runner or SubprocessRunner()

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedMovieIdentification:
        try:
            candidate_path, _extension = resolve_safe_candidate_path(root, relative_path)
            ffprobe_executable, _ffprobe_version = resolve_ffprobe(self._runner)
            ffmpeg_executable, _ffmpeg_version = resolve_ffmpeg(self._runner)
            metadata = probe_media_metadata(
                self._runner,
                ffprobe_executable=ffprobe_executable,
                media_path=str(candidate_path),
            )
            sheet, warnings = extract_and_compose_contact_sheet(
                self._runner,
                ffmpeg_executable=ffmpeg_executable,
                media_path=str(candidate_path),
                duration_ms=metadata.duration_ms,
            )
            basename = relative_path.value.rsplit("/", maxsplit=1)[-1]
            hints = build_local_movie_hints(
                basename=basename,
                technical_metadata=metadata,
            )
            return PreparedMovieIdentification(
                basename=basename,
                contact_sheet=sheet,
                hints=hints,
                warnings=warnings,
            )
        except (MediaAnalysisUnavailableError, MediaAnalysisFailedError):
            raise
        except FrameNestMediaAnalysisError as exc:
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE) from exc
        except Exception as exc:
            raise MediaAnalysisFailedError(PREPARATION_FAILED_MESSAGE) from exc
