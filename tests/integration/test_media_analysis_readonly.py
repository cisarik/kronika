"""Read-only integration tests for local media analysis preparation."""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    MediaRelativePath,
    PreparedAnalysisResult,
    PrepareLocalMediaAnalysis,
    REQUESTED_FRAME_COUNT,
    TechnicalMetadata,
    build_representative_frame,
    PNG_SIGNATURE,
)
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.media_analysis.adapter import LocalMediaAnalysisAdapter
from kronika.infrastructure.media_analysis.process import ProcessRunResult

_VALID_PNG = PNG_SIGNATURE + b"png"


class _FakeLibraryRepository:
    def __init__(self, library: Library) -> None:
        self._library = library
        self.write_calls = 0

    def add(self, library: Library) -> None:
        self.write_calls += 1

    def get(self, library_id: LibraryId) -> Library | None:
        return self._library if self._library.id == library_id else None

    def list_all(self) -> tuple[Library, ...]:
        return (self._library,)


class _CannedRunner:
    def __init__(self, results: list[ProcessRunResult]) -> None:
        self._results = list(results)

    def run(
        self,
        *,
        executable: str,
        argv: Sequence[str],
        timeout_seconds: float,
        stdout_max_bytes: int,
        stderr_max_bytes: int,
    ) -> ProcessRunResult:
        if tuple(argv) == ("-version",):
            if "ffprobe" in executable:
                return ProcessRunResult(returncode=0, stdout=b"ffprobe version 8.1.2\n", stderr=b"")
            if "ffmpeg" in executable:
                return ProcessRunResult(returncode=0, stdout=b"ffmpeg version 8.1.2\n", stderr=b"")
        return self._results.pop(0)


def _ffprobe_json() -> bytes:
    payload = {
        "streams": [
            {
                "codec_type": "video",
                "codec_name": "h264",
                "width": 64,
                "height": 48,
                "duration": "1.0",
                "disposition": {"attached_pic": 0},
            },
            {"codec_type": "audio", "codec_name": "aac"},
        ],
        "format": {"duration": "1.0", "format_name": "mov,mp4,m4a,3gp,3g2,mj2"},
    }
    return json.dumps(payload).encode("utf-8")


def _prepared_result(root: LibraryRoot, relative: str) -> PreparedAnalysisResult:
    frame = build_representative_frame(timestamp_ms=100, payload=_VALID_PNG)
    return PreparedAnalysisResult(
        relative_path=MediaRelativePath(relative),
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=1000,
            width=64,
            height=48,
            video_codec="h264",
            container_formats=("mp4",),
            has_audio=True,
        ),
        representative_frames=(frame,),
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=(),
        ffprobe_version="ffprobe version 8.1.2",
        ffmpeg_version="ffmpeg version 8.1.2",
    )


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX integration fixture")
def test_readonly_preparation_does_not_mutate_filesystem_or_database(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = root / "clip.mp4"
    original = b"original-bytes"
    media.write_bytes(original)

    library = Library(
        id=LibraryId.new(),
        device_id=DeviceId.new(),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root)),
    )
    repository = _FakeLibraryRepository(library)

    png = _VALID_PNG
    runner = _CannedRunner(
        [
            ProcessRunResult(returncode=0, stdout=_ffprobe_json(), stderr=b""),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
            ProcessRunResult(returncode=0, stdout=png, stderr=b""),
        ]
    )
    adapter = LocalMediaAnalysisAdapter(runner=runner)
    service = PrepareLocalMediaAnalysis(repository, adapter)

    before = set(root.iterdir())
    result = service.execute(library.id, MediaRelativePath("clip.mp4"))
    after = set(root.iterdir())

    assert repository.write_calls == 0
    assert before == after
    assert media.read_bytes() == original
    assert result.relative_path.value == "clip.mp4"
    assert result.representative_frames
