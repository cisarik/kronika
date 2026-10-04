"""Opt-in live NVIDIA NIM smoke test."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kronika.application.media_suggestion import PreviewMediaSuggestion, PROMPT_VERSION
from kronika.application.media_analysis import MediaRelativePath
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.ai import NvidiaNimMediaSuggestionProvider
from kronika.infrastructure.ai.credentials import NvidiaApiCredential
from kronika.infrastructure.media_analysis import LocalMediaAnalysisAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAMENEST_RUN_NVIDIA_NIM_SMOKE") != "1"
    or not os.environ.get("NVIDIA_API_KEY", "").strip(),
    reason="Set FRAMENEST_RUN_NVIDIA_NIM_SMOKE=1 and NVIDIA_API_KEY to run live NVIDIA smoke test.",
)


class _FakeLibraryRepository:
    def __init__(self, library: Library) -> None:
        self._library = library

    def add(self, library: Library) -> None:
        raise AssertionError("database write forbidden")

    def get(self, library_id: LibraryId) -> Library | None:
        return self._library if self._library.id == library_id else None

    def list_all(self) -> tuple[Library, ...]:
        return (self._library,)


def _generate_tiny_mp4(path: Path) -> None:
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.fail("ffmpeg is required for live NVIDIA smoke test")
    subprocess.run(
        [
            ffmpeg,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=blue:s=64x48:d=1",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ],
        check=True,
        timeout=30,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX live smoke fixture")
def test_live_nvidia_nim_returns_valid_suggestion(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = root / "sample.mp4"
    _generate_tiny_mp4(media)

    library = Library(
        id=LibraryId.new(),
        device_id=DeviceId.new(),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root)),
    )
    credential = NvidiaApiCredential(os.environ["NVIDIA_API_KEY"])
    provider = NvidiaNimMediaSuggestionProvider(credential)
    service = PreviewMediaSuggestion(
        _FakeLibraryRepository(library),
        LocalMediaAnalysisAdapter(),
        provider,
    )

    result = service.execute(library.id, MediaRelativePath("sample.mp4"))

    assert result.suggestion.prompt_version == PROMPT_VERSION
    assert result.suggestion.provider_id == "nvidia-nim"
    assert result.suggestion.suggested_filename.endswith(".mp4")
    assert 1 <= result.sent_frame_count <= 3
    assert media.read_bytes()
