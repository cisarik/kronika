"""Unit tests for local media analysis preparation application values and service."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisNotFoundError,
    MediaRelativePath,
    PreparedAnalysisResult,
    PrepareLocalMediaAnalysis,
    RepresentativeFrame,
    TechnicalMetadata,
    build_representative_frame,
    compute_target_timestamps_ms,
    deduplicate_representative_frames,
    parse_duration_seconds_to_ms,
    candidate_kind_for_relative_path,
    PNG_PAYLOAD_MAX_BYTES,
    REQUESTED_FRAME_COUNT,
    PNG_SIGNATURE,
)
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_MEDIA_ANALYSIS = (
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_analysis.py"
)

_VALID_PNG = PNG_SIGNATURE + b"rest-of-png-bytes"


class _FakeLibraryRepository:
    def __init__(self, library: Library | None) -> None:
        self._library = library
        self.write_calls = 0

    def add(self, library: Library) -> None:
        self.write_calls += 1

    def get(self, library_id: LibraryId) -> Library | None:
        if self._library is None or self._library.id != library_id:
            return None
        return self._library

    def list_all(self) -> tuple[Library, ...]:
        return () if self._library is None else (self._library,)


class _FakePreparer:
    def __init__(self, result: PreparedAnalysisResult) -> None:
        self.result = result
        self.last_root: LibraryRoot | None = None
        self.last_path: MediaRelativePath | None = None

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        self.last_root = root
        self.last_path = relative_path
        return self.result


def _sample_metadata() -> TechnicalMetadata:
    return TechnicalMetadata(
        duration_ms=10_000,
        width=640,
        height=360,
        video_codec="h264",
        container_formats=("mov", "mp4"),
        has_audio=True,
    )


def _sample_frame(timestamp_ms: int = 100, payload: bytes = _VALID_PNG) -> RepresentativeFrame:
    return build_representative_frame(timestamp_ms=timestamp_ms, payload=payload)


def _sample_result() -> PreparedAnalysisResult:
    return PreparedAnalysisResult(
        relative_path=MediaRelativePath("clips/sample.mp4"),
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=_sample_metadata(),
        representative_frames=(_sample_frame(),),
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=(),
        ffprobe_version="ffprobe version 8.1.2",
        ffmpeg_version="ffmpeg version 8.1.2",
    )


def _sample_library() -> Library:
    return Library(
        id=LibraryId.from_string("12345678-1234-4234-9234-123456789abc"),
        device_id=DeviceId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef"),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


@pytest.mark.parametrize(
    "path",
    [
        "media/clip.mp4",
        "nested/dir/sample.gif",
    ],
)
def test_media_relative_path_accepts_valid_paths(path: str) -> None:
    assert MediaRelativePath(path).value == path


@pytest.mark.parametrize(
    "path",
    [
        "",
        "/abs.mp4",
        "a\\b.mp4",
        "C:/Videos/a.mp4",
        "C:\\Videos\\a.mp4",
        "\\\\server\\share\\a.mp4",
        "a/../b.mp4",
        "a/./b.mp4",
        "a//b.mp4",
        ".hidden/clip.mp4",
        "dir/.secret.mp4",
        "a\x00b.mp4",
    ],
)
def test_media_relative_path_rejects_invalid_paths(path: str) -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        MediaRelativePath(path)


def test_media_relative_path_errors_do_not_echo_root() -> None:
    rejected = "/private/root/clip.mp4"
    with pytest.raises(FrameNestMediaAnalysisError) as exc_info:
        MediaRelativePath(rejected)
    assert rejected not in str(exc_info.value)


def test_candidate_kind_reuses_scan_classifier() -> None:
    assert (
        candidate_kind_for_relative_path(MediaRelativePath("a.mp4"))
        == LibraryScanCandidateKind.VIDEO
    )
    assert (
        candidate_kind_for_relative_path(MediaRelativePath("a.gif"))
        == LibraryScanCandidateKind.GIF
    )
    with pytest.raises(FrameNestMediaAnalysisError):
        candidate_kind_for_relative_path(MediaRelativePath("readme.txt"))


def test_compute_target_timestamps_for_known_duration() -> None:
    assert compute_target_timestamps_ms(10_000) == (1_000, 5_000, 9_000)


def test_compute_target_timestamps_clamps_and_dedupes() -> None:
    assert compute_target_timestamps_ms(1) == (0,)


def test_compute_target_timestamps_unknown_duration_fallback() -> None:
    assert compute_target_timestamps_ms(None) == (0,)
    assert compute_target_timestamps_ms(0) == (0,)
    assert compute_target_timestamps_ms(-1) == (0,)


def test_parse_duration_seconds_to_ms() -> None:
    assert parse_duration_seconds_to_ms("1.234") == 1234
    assert parse_duration_seconds_to_ms("invalid") is None


def test_technical_metadata_validation() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        TechnicalMetadata(
            duration_ms=-1,
            width=1,
            height=1,
            video_codec="h264",
            container_formats=("mp4",),
            has_audio=False,
        )


def test_representative_frame_validates_png_and_digest() -> None:
    frame = build_representative_frame(timestamp_ms=0, payload=_VALID_PNG)
    assert frame.mime_type == "image/png"
    assert frame.byte_size == len(_VALID_PNG)
    assert "payload" not in repr(frame)


def test_representative_frame_rejects_invalid_payload() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        build_representative_frame(timestamp_ms=0, payload=b"not-a-png")


def test_deduplicate_representative_frames_preserves_first() -> None:
    payload_a = _VALID_PNG
    payload_b = PNG_SIGNATURE + b"other"
    first = build_representative_frame(timestamp_ms=1, payload=payload_a)
    duplicate = build_representative_frame(timestamp_ms=2, payload=payload_a)
    unique = build_representative_frame(timestamp_ms=3, payload=payload_b)
    result = deduplicate_representative_frames((first, duplicate, unique))
    assert len(result) == 2
    assert result[0].timestamp_ms == 1
    assert result[1].timestamp_ms == 3


def test_prepared_result_requires_unique_frames_and_bounds() -> None:
    frame = _sample_frame()
    duplicate = build_representative_frame(timestamp_ms=200, payload=_VALID_PNG)
    with pytest.raises(FrameNestMediaAnalysisError):
        PreparedAnalysisResult(
            relative_path=MediaRelativePath("a.mp4"),
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=_sample_metadata(),
            representative_frames=(frame, duplicate),
            requested_frame_count=REQUESTED_FRAME_COUNT,
            warnings=(),
            ffprobe_version="ffprobe version 8.1.2",
            ffmpeg_version="ffmpeg version 8.1.2",
        )


def test_prepared_result_rejects_empty_frames() -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        PreparedAnalysisResult(
            relative_path=MediaRelativePath("a.mp4"),
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=_sample_metadata(),
            representative_frames=(),
            requested_frame_count=REQUESTED_FRAME_COUNT,
            warnings=(),
            ffprobe_version="ffprobe version 8.1.2",
            ffmpeg_version="ffmpeg version 8.1.2",
        )


def test_representative_frame_rejects_oversized_payload() -> None:
    huge = PNG_SIGNATURE + (b"x" * PNG_PAYLOAD_MAX_BYTES)
    with pytest.raises(FrameNestMediaAnalysisError):
        build_representative_frame(timestamp_ms=0, payload=huge)


def test_prepare_service_delegates_to_port() -> None:
    library = _sample_library()
    result = _sample_result()
    preparer = _FakePreparer(result)
    repository = _FakeLibraryRepository(library)
    service = PrepareLocalMediaAnalysis(repository, preparer)
    path = MediaRelativePath("clips/sample.mp4")

    got = service.execute(library.id, path)

    assert got == result
    assert preparer.last_root == library.root
    assert preparer.last_path == path


def test_prepare_service_raises_not_found_for_missing_library() -> None:
    service = PrepareLocalMediaAnalysis(
        _FakeLibraryRepository(None),
        _FakePreparer(_sample_result()),
    )
    with pytest.raises(MediaAnalysisNotFoundError):
        service.execute(LibraryId.new(), MediaRelativePath("a.mp4"))


def test_prepare_service_requests_no_database_write() -> None:
    library = _sample_library()
    repository = _FakeLibraryRepository(library)
    service = PrepareLocalMediaAnalysis(repository, _FakePreparer(_sample_result()))
    service.execute(library.id, MediaRelativePath("a.mp4"))
    assert repository.write_calls == 0


def test_application_media_analysis_module_imports_no_infrastructure() -> None:
    tree = ast.parse(
        APPLICATION_MEDIA_ANALYSIS.read_text(encoding="utf-8"),
        filename=str(APPLICATION_MEDIA_ANALYSIS),
    )
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        if module.startswith("kronika.infrastructure"):
            violations.append(module)
    assert violations == []
