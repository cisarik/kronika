"""Unit tests for the local media content filesystem reader."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from kronika.application.media_content import (
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MediaContentUnavailableError,
)
from kronika.domain import LibraryPathFlavor, LibraryRoot
from kronika.domain.media import (
    FrameNestMediaRelativePathError,
    MediaKind,
    MediaRelativePath,
)
from kronika.infrastructure.filesystem.media_content import LocalMediaContentReader

MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100
GIF_BYTES = b"GIF89a" + b"\x02" * 50


def _reader():
    return LocalMediaContentReader()


def _posix_root(path):
    return LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(path))


def _write_mp4(root, name="clip.mp4", content=MP4_BYTES):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _write_gif(root, name="anim.gif", content=GIF_BYTES):
    target = root / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def test_valid_mp4_resolution_returns_content_type_and_size(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root)
    opened = _reader().open(
        _posix_root(root),
        MediaRelativePath("clip.mp4"),
        MediaKind.VIDEO,
    )
    assert opened.media_type == "video/mp4"
    assert opened.byte_size == len(MP4_BYTES)
    assert b"".join(opened.stream(0, None)) == MP4_BYTES


def test_valid_gif_resolution_returns_content_type_and_size(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_gif(root)
    opened = _reader().open(
        _posix_root(root),
        MediaRelativePath("anim.gif"),
        MediaKind.ANIMATED_IMAGE,
    )
    assert opened.media_type == "image/gif"
    assert opened.byte_size == len(GIF_BYTES)
    assert b"".join(opened.stream(0, None)) == GIF_BYTES


def test_traversal_and_absolute_path_rejected_by_domain():
    with pytest.raises(FrameNestMediaRelativePathError):
        MediaRelativePath("../escape.mp4")
    with pytest.raises(FrameNestMediaRelativePathError):
        MediaRelativePath("/etc/passwd")


def test_symlink_escaping_root_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    outside = tmp_path / "outside.mp4"
    outside.write_bytes(MP4_BYTES)
    link = root / "link.mp4"
    os.symlink(outside, link)
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(root), MediaRelativePath("link.mp4"), MediaKind.VIDEO)


def test_symlink_inside_root_accepted(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    real = _write_mp4(root, "real/video.mp4")
    link = root / "alias.mp4"
    os.symlink(real, link)
    opened = _reader().open(
        _posix_root(root),
        MediaRelativePath("alias.mp4"),
        MediaKind.VIDEO,
    )
    assert opened.media_type == "video/mp4"
    assert b"".join(opened.stream(0, None)) == MP4_BYTES


def test_absent_root_rejected(tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(missing), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)


def test_root_not_a_directory_rejected(tmp_path):
    file_root = tmp_path / "file_root"
    file_root.write_bytes(b"data")
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(file_root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)


def test_absent_target_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(root), MediaRelativePath("missing.mp4"), MediaKind.VIDEO)


def test_directory_target_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "subdir").mkdir()
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(root), MediaRelativePath("subdir"), MediaKind.VIDEO)


def test_unsupported_extension_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    (root / "notes.txt").write_bytes(b"text")
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(root), MediaRelativePath("notes.txt"), MediaKind.VIDEO)


def test_kind_extension_mismatch_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_gif(root)
    with pytest.raises(MediaContentUnavailableError):
        _reader().open(_posix_root(root), MediaRelativePath("anim.gif"), MediaKind.VIDEO)


def test_bounded_range_returns_exact_subset(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    payload = bytes(range(256))
    _write_mp4(root, "clip.mp4", payload)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    assert b"".join(opened.stream(10, 5)) == payload[10:15]


def test_zero_length_range_returns_empty(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    payload = bytes(range(256))
    _write_mp4(root, "clip.mp4", payload)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    assert b"".join(opened.stream(0, 0)) == b""


def test_bounded_range_beyond_eof_returns_available(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    payload = bytes(range(256))
    _write_mp4(root, "clip.mp4", payload)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    assert b"".join(opened.stream(250, 100)) == payload[250:]


def test_handle_closed_after_full_iteration(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    gen = opened.stream(0, None)
    list(gen)
    assert gen.gi_frame is None


def test_handle_closed_on_early_generator_close(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "big.mp4", b"\x00" * 200_000)
    opened = _reader().open(_posix_root(root), MediaRelativePath("big.mp4"), MediaKind.VIDEO)
    gen = opened.stream(0, None)
    next(gen)
    gen.close()
    assert gen.gi_frame is None


@pytest.mark.skipif(sys.platform == "win32" or os.geteuid() == 0, reason="requires non-root posix")
def test_unreadable_file_rejected(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    target = _write_mp4(root, "clip.mp4")
    os.chmod(target, 0o000)
    try:
        with pytest.raises(MediaContentUnavailableError):
            _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    finally:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)


def test_sanitized_failures_do_not_disclose_paths(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    try:
        _reader().open(_posix_root(root), MediaRelativePath("missing.mp4"), MediaKind.VIDEO)
    except MediaContentUnavailableError as exc:
        assert str(root) not in str(exc)
        assert "missing.mp4" not in str(exc)
        assert str(exc) == MEDIA_CONTENT_UNAVAILABLE_MESSAGE
    else:
        pytest.fail("expected MediaContentUnavailableError")


def test_size_obtained_from_open_descriptor(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    original_size = opened.byte_size
    with open(root / "clip.mp4", "ab") as handle:
        handle.write(b"\x00" * 50)
    assert opened.byte_size == original_size
    assert opened.byte_size == len(MP4_BYTES)
    opened.close()


@pytest.mark.skipif(sys.platform == "win32", reason="requires posix unlink-on-open semantics")
def test_file_not_reopened_for_streaming(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    (root / "clip.mp4").unlink()
    assert b"".join(opened.stream(0, None)) == MP4_BYTES


@pytest.mark.skipif(sys.platform == "win32", reason="requires posix unlink-on-open semantics")
def test_replacing_path_after_open_does_not_change_content(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    (root / "clip.mp4").unlink()
    (root / "clip.mp4").write_bytes(b"REPLACED")
    assert b"".join(opened.stream(0, None)) == MP4_BYTES
    assert opened.byte_size == len(MP4_BYTES)


def test_close_is_idempotent(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    opened.close()
    opened.close()
    opened.close()


def test_close_before_stream_prevents_subsequent_streaming(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    opened.close()
    with pytest.raises(Exception):
        list(opened.stream(0, None))


def test_normal_completion_closes_handle(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    list(opened.stream(0, None))
    opened.close()


def test_response_finalization_close_is_safe_after_stream(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    gen = opened.stream(0, None)
    list(gen)
    opened.close()
    opened.close()


def test_ranged_stream_uses_stable_handle(tmp_path):
    root = tmp_path / "library"
    root.mkdir()
    payload = bytes(range(256)) * 4
    _write_mp4(root, "clip.mp4", payload)
    opened = _reader().open(_posix_root(root), MediaRelativePath("clip.mp4"), MediaKind.VIDEO)
    assert b"".join(opened.stream(100, 50)) == payload[100:150]
    assert opened.byte_size == len(payload)


def test_fstat_failure_with_close_failure_sanitizes_and_attempts_once(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)

    close_calls = []
    synthetic_fd = 999

    def fake_open(path, flags):
        return synthetic_fd

    def fake_fstat(fd):
        raise OSError("private fstat raw text")

    def fake_close(fd):
        close_calls.append(fd)
        raise OSError("private close raw text")

    monkeypatch.setattr("os.open", fake_open)
    monkeypatch.setattr("os.fstat", fake_fstat)
    monkeypatch.setattr("os.close", fake_close)

    with pytest.raises(MediaContentUnavailableError) as exc_info:
        _reader().open(
            _posix_root(root),
            MediaRelativePath("clip.mp4"),
            MediaKind.VIDEO,
        )

    assert str(exc_info.value) == MEDIA_CONTENT_UNAVAILABLE_MESSAGE
    assert close_calls == [synthetic_fd]
    assert "private fstat raw text" not in str(exc_info.value)
    assert "private close raw text" not in str(exc_info.value)


def test_non_regular_descriptor_with_close_failure_sanitizes_and_attempts_once(
    tmp_path,
    monkeypatch,
):
    root = tmp_path / "library"
    root.mkdir()
    _write_mp4(root, "clip.mp4", MP4_BYTES)

    close_calls = []
    synthetic_fd = 999

    class FakeStat:
        def __init__(self):
            self.st_mode = stat.S_IFDIR
            self.st_size = 0

    def fake_open(path, flags):
        return synthetic_fd

    def fake_fstat(fd):
        return FakeStat()

    def fake_close(fd):
        close_calls.append(fd)
        raise OSError("private close raw text")

    monkeypatch.setattr("os.open", fake_open)
    monkeypatch.setattr("os.fstat", fake_fstat)
    monkeypatch.setattr("os.close", fake_close)

    with pytest.raises(MediaContentUnavailableError) as exc_info:
        _reader().open(
            _posix_root(root),
            MediaRelativePath("clip.mp4"),
            MediaKind.VIDEO,
        )

    assert str(exc_info.value) == MEDIA_CONTENT_UNAVAILABLE_MESSAGE
    assert close_calls == [synthetic_fd]
    assert "private close raw text" not in str(exc_info.value)
