"""Cover source probing and arbitrary-timestamp extraction adapter tests."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from PIL import Image

from kronika.application.media_analysis import (
    MediaAnalysisFailedError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
)
from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
from kronika.domain.media import MediaKind
from kronika.infrastructure.media_analysis.cover_frame import LocalCoverSourceAdapter
from kronika.infrastructure.media_analysis.process import (
    EXECUTABLE_NOT_FOUND_MESSAGE,
    PROCESS_TIMEOUT_MESSAGE,
    ProcessExecutionError,
    ProcessRunResult,
)

VERSION_STDOUT = b"ffprobe version 7.1\n"


class _FakeContentReader:
    def __init__(self, *, byte_size: int, mtime_ns: int | None) -> None:
        self._byte_size = byte_size
        self._mtime_ns = mtime_ns
        self.closed = False

    def open(self, root, relative_path, kind):
        return _Opened(byte_size=self._byte_size, mtime_ns=self._mtime_ns)


class _Opened:
    def __init__(self, *, byte_size: int, mtime_ns: int | None) -> None:
        self.byte_size = byte_size
        self.mtime_ns = mtime_ns
        self.media_type = "video/mp4"

    def stream(self, start, length):
        raise AssertionError("streaming not expected")

    def close(self):
        self.closed = True


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 20, 30)).save(buffer, format="PNG")
    return buffer.getvalue()


class _FakeRunner:
    def __init__(
        self,
        *,
        probe_json: dict,
        frame_payload: bytes,
        frame_rc: int = 0,
        frame_error: Exception | None = None,
        version_error: Exception | None = None,
    ) -> None:
        self._probe_json = probe_json
        self._frame_payload = frame_payload
        self._frame_rc = frame_rc
        self._frame_error = frame_error
        self._version_error = version_error
        self.calls: list[tuple[str, list[str]]] = []

    def run(self, *, executable, argv, timeout_seconds, stdout_max_bytes, stderr_max_bytes, pass_fds=()):
        self.calls.append((executable, list(argv)))
        if "-version" in argv:
            if self._version_error is not None:
                raise self._version_error
            name = b"ffprobe" if "ffprobe" in executable else b"ffmpeg"
            return ProcessRunResult(returncode=0, stdout=name + b" version 7.1\n", stderr=b"")
        if "-show_format" in argv:
            return ProcessRunResult(
                returncode=0,
                stdout=json.dumps(self._probe_json).encode("utf-8"),
                stderr=b"",
            )
        if self._frame_error is not None:
            raise self._frame_error
        return ProcessRunResult(
            returncode=self._frame_rc,
            stdout=self._frame_payload,
            stderr=b"decode error" if self._frame_rc != 0 else b"",
        )


def _fixtures(tmp_path: Path, filename: str = "clip.mp4") -> tuple[LibraryRoot, MediaRelativePath]:
    root = tmp_path / "root"
    root.mkdir()
    (root / filename).write_bytes(b"\x00" * 1024)
    library_root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root))
    return library_root, MediaRelativePath(filename)


def test_probe_returns_sanitized_observations(tmp_path: Path) -> None:
    library_root, relative_path = _fixtures(tmp_path)
    runner = _FakeRunner(
        probe_json={
            "streams": [
                {
                    "codec_type": "video",
                    "codec_name": "h264",
                    "width": 320,
                    "height": 240,
                    "duration": "2.5",
                }
            ],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "2.5"},
        },
        frame_payload=b"",
    )
    content_reader = _FakeContentReader(byte_size=1024, mtime_ns=123)
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=content_reader)
    probe = adapter.probe(library_root, relative_path, MediaKind.VIDEO)
    assert probe.duration_ms == 2500
    assert probe.source_size_bytes == 1024
    assert probe.source_mtime_ns == 123


def test_extract_frame_returns_validated_frame(tmp_path: Path) -> None:
    library_root, relative_path = _fixtures(tmp_path)
    runner = _FakeRunner(
        probe_json=json.loads(
            '{"streams":[]}'
        ),
        frame_payload=_png_bytes(),
    )
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=_FakeContentReader(byte_size=1, mtime_ns=None))
    frame = adapter.extract_frame(library_root, relative_path, MediaKind.VIDEO, 500)
    assert frame.timestamp_ms == 500
    assert frame.mime_type == "image/png"
    assert frame.payload == _png_bytes()


def test_extract_frame_sanitizes_subprocess_failure(tmp_path: Path) -> None:
    library_root, relative_path = _fixtures(tmp_path)
    runner = _FakeRunner(probe_json={"streams": []}, frame_payload=b"", frame_rc=1)
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=_FakeContentReader(byte_size=1, mtime_ns=None))
    with pytest.raises(MediaAnalysisFailedError):
        adapter.extract_frame(library_root, relative_path, MediaKind.VIDEO, 500)


def test_extract_frame_sanitizes_timeout(tmp_path: Path) -> None:
    library_root, relative_path = _fixtures(tmp_path)
    runner = _FakeRunner(
        probe_json={"streams": []},
        frame_payload=b"",
        frame_error=ProcessExecutionError(PROCESS_TIMEOUT_MESSAGE),
    )
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=_FakeContentReader(byte_size=1, mtime_ns=None))
    with pytest.raises(MediaAnalysisFailedError):
        adapter.extract_frame(library_root, relative_path, MediaKind.VIDEO, 500)


def test_extract_frame_rejects_unresolvable_tool(tmp_path: Path) -> None:
    library_root, relative_path = _fixtures(tmp_path)
    runner = _FakeRunner(
        probe_json={"streams": []},
        frame_payload=b"",
        version_error=ProcessExecutionError(EXECUTABLE_NOT_FOUND_MESSAGE),
    )
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=_FakeContentReader(byte_size=1, mtime_ns=None))
    with pytest.raises(MediaAnalysisFailedError):
        adapter.extract_frame(library_root, relative_path, MediaKind.VIDEO, 500)


def test_probe_rejects_escaping_and_missing_paths(tmp_path: Path) -> None:
    from kronika.application.media_analysis import FrameNestMediaAnalysisError

    root = tmp_path / "root"
    root.mkdir()
    library_root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root))
    adapter = LocalCoverSourceAdapter(
        runner=_FakeRunner(probe_json={"streams": []}, frame_payload=b""),
        content_reader=_FakeContentReader(byte_size=1, mtime_ns=None),
    )
    with pytest.raises(FrameNestMediaAnalysisError):
        MediaRelativePath("../escape.mp4")
    with pytest.raises(MediaAnalysisUnavailableError):
        adapter.probe(
            library_root,
            MediaRelativePath("missing.mp4"),
            MediaKind.VIDEO,
        )


def _jpeg_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (120, 80), (5, 15, 25)).save(buffer, format="JPEG", quality=90)
    return buffer.getvalue()


def _image_bytes() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (96, 64), (5, 15, 25)).save(buffer, format="PNG")
    return buffer.getvalue()


def _image_fixtures(tmp_path: Path, filename: str, payload: bytes = _jpeg_bytes()) -> tuple[LibraryRoot, MediaRelativePath]:
    root = tmp_path / "root"
    root.mkdir()
    (root / filename).write_bytes(payload)
    library_root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(root))
    return library_root, MediaRelativePath(filename)


def test_still_image_probe_is_timeless_without_ffprobe(tmp_path: Path) -> None:
    library_root, relative_path = _image_fixtures(tmp_path, "still.jpg")
    runner = _FakeRunner(
        probe_json={"streams": []},
        frame_payload=b"",
    )
    content_reader = _FakeContentReader(byte_size=len(_jpeg_bytes()), mtime_ns=404)
    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=content_reader)
    probe = adapter.probe(library_root, relative_path, MediaKind.IMAGE)
    assert probe.duration_ms is None
    assert probe.source_size_bytes == len(_jpeg_bytes())
    assert probe.source_mtime_ns == 404
    assert runner.calls == []


def test_still_image_extract_returns_bounded_png_frame_without_ffmpeg(tmp_path: Path) -> None:
    library_root, relative_path = _image_fixtures(tmp_path, "still.png", payload=_image_bytes())
    runner = _FakeRunner(
        probe_json={"streams": []},
        frame_payload=b"",
    )
    adapter = LocalCoverSourceAdapter(
        runner=runner,
        content_reader=_FakeContentReader(byte_size=1, mtime_ns=None),
    )
    frame = adapter.extract_frame(library_root, relative_path, MediaKind.IMAGE, 0)
    assert frame.timestamp_ms == 0
    assert frame.mime_type == "image/png"
    assert frame.payload.startswith(b"\x89PNG")
    assert runner.calls == []


def test_still_image_extract_rejects_malformed_content(tmp_path: Path) -> None:
    library_root, relative_path = _image_fixtures(
        tmp_path,
        "broken.jpg",
        payload=b"\x00" * 64,
    )
    runner = _FakeRunner(probe_json={"streams": []}, frame_payload=b"")
    adapter = LocalCoverSourceAdapter(
        runner=runner,
        content_reader=_FakeContentReader(byte_size=64, mtime_ns=None),
    )
    with pytest.raises(MediaAnalysisFailedError):
        adapter.extract_frame(library_root, relative_path, MediaKind.IMAGE, 0)


def test_still_image_probe_rejects_mismatched_extension_kind(tmp_path: Path) -> None:
    library_root, relative_path = _image_fixtures(tmp_path, "still.png")
    runner = _FakeRunner(probe_json={"streams": []}, frame_payload=b"")
    content_reader = _FakeContentReader(byte_size=1, mtime_ns=None)

    class _RejectingReader(_FakeContentReader):
        def open(self, root, relative_path, kind):
            if kind is not MediaKind.IMAGE:
                raise AssertionError("unexpected kind")
            return super().open(root, relative_path, kind)

    adapter = LocalCoverSourceAdapter(runner=runner, content_reader=_RejectingReader(byte_size=1, mtime_ns=None))
    probe = adapter.probe(library_root, relative_path, MediaKind.IMAGE)
    assert probe.source_size_bytes == 1
