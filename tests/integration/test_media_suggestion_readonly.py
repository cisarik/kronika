"""Read-only integration tests for media suggestion preview composition."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    PNG_SIGNATURE,
    PreparedAnalysisResult,
    RepresentativeFrame,
    TechnicalMetadata,
    build_representative_frame,
    REQUESTED_FRAME_COUNT,
    MediaRelativePath,
)
from kronika.application.media_suggestion import (
    MediaSuggestion,
    MediaSuggestionRequest,
    PreviewMediaSuggestion,
    PROMPT_VERSION,
)
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot

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


class _FakePreparer:
    def prepare(self, root: LibraryRoot, relative_path: MediaRelativePath) -> PreparedAnalysisResult:
        frame = build_representative_frame(timestamp_ms=0, payload=_VALID_PNG)
        return PreparedAnalysisResult(
            relative_path=relative_path,
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=TechnicalMetadata(
                duration_ms=1000,
                width=64,
                height=48,
                video_codec="h264",
                container_formats=("mp4",),
                has_audio=False,
            ),
            representative_frames=(frame,),
            requested_frame_count=REQUESTED_FRAME_COUNT,
            warnings=(),
            ffprobe_version="ffprobe version 8.1.2",
            ffmpeg_version="ffmpeg version 8.1.2",
        )


class _FakeProvider:
    def __init__(self) -> None:
        self.calls = 0

    def suggest(self, request: MediaSuggestionRequest) -> MediaSuggestion:
        self.calls += 1
        return MediaSuggestion(
            title="Evening clip",
            description="A short evening scene with warm light.",
            collection="Home",
            tags=("evening", "clip"),
            suggested_filename="evening-clip.mp4",
            confidence=0.72,
            evidence=("Warm light is visible in the frame.",),
            uncertainties=("Exact location is unknown.",),
            provider_id="nvidia-nim",
            model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
            prompt_version=PROMPT_VERSION,
        )


@pytest.mark.skipif(__import__("os").name == "nt", reason="POSIX integration fixture")
def test_readonly_suggestion_preview_does_not_mutate_filesystem_or_database(tmp_path: Path) -> None:
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
    provider = _FakeProvider()
    service = PreviewMediaSuggestion(repository, _FakePreparer(), provider)

    before = set(root.iterdir())
    result = service.execute(library.id, MediaRelativePath("clip.mp4"))
    after = set(root.iterdir())

    assert repository.write_calls == 0
    assert provider.calls == 1
    assert before == after
    assert media.read_bytes() == original
    assert result.suggestion.title == "Evening clip"
