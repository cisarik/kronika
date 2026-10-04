"""Opt-in integration tests using real local ffprobe and ffmpeg executables."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from kronika.application.media_analysis import MediaRelativePath, PrepareLocalMediaAnalysis
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.media_analysis import LocalMediaAnalysisAdapter

pytestmark = pytest.mark.skipif(
    os.environ.get("FRAMENEST_RUN_REAL_MEDIA_TOOLS") != "1",
    reason="Set FRAMENEST_RUN_REAL_MEDIA_TOOLS=1 to run real-tool media analysis tests.",
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


def _require_ffmpeg() -> str:
    executable = shutil.which("ffmpeg")
    if executable is None:
        pytest.fail("ffmpeg is required for real-tool media analysis tests")
    return executable


def _generate_tiny_mp4(path: Path) -> None:
    ffmpeg = _require_ffmpeg()
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


def _generate_tiny_gif(path: Path) -> None:
    ffmpeg = _require_ffmpeg()
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
            "color=c=red:s=32x32:d=1",
            "-frames:v",
            "1",
            str(path),
        ],
        check=True,
        timeout=30,
    )


@pytest.mark.skipif(os.name == "nt", reason="POSIX real-tool fixture")
@pytest.mark.parametrize(
    ("filename", "generator"),
    [
        ("sample.mp4", _generate_tiny_mp4),
        ("sample.gif", _generate_tiny_gif),
    ],
)
def test_real_tools_prepare_bounded_metadata_and_frames(
    tmp_path: Path,
    filename: str,
    generator: object,
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = root / filename
    generator(media)
    original = media.read_bytes()

    library = Library(
        id=LibraryId.new(),
        device_id=DeviceId.new(),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root)),
    )
    service = PrepareLocalMediaAnalysis(_FakeLibraryRepository(library), LocalMediaAnalysisAdapter())

    result = service.execute(library.id, MediaRelativePath(filename))

    assert result.technical_metadata.width > 0
    assert result.technical_metadata.height > 0
    assert 1 <= len(result.representative_frames) <= 3
    digests = [frame.sha256 for frame in result.representative_frames]
    assert len(digests) == len(set(digests))
    for frame in result.representative_frames:
        assert frame.payload.startswith(b"\x89PNG\r\n\x1a\n")
        assert frame.sha256 == hashlib.sha256(frame.payload).hexdigest()
        assert frame.byte_size == len(frame.payload)
    assert media.read_bytes() == original
    assert not any(path.suffix == ".png" for path in root.iterdir())
