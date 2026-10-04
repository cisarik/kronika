"""Unit tests for local media analysis filesystem safety."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kronika.application.media_analysis import (
    INVALID_MEDIA_PATH_MESSAGE,
    FrameNestMediaAnalysisError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PREPARATION_UNAVAILABLE_MESSAGE,
)
from kronika.domain import LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.media_analysis.filesystem import resolve_safe_candidate_path

PRIVATE_ROOT = "/Users/agile/Video"


def _posix_root(path: Path) -> LibraryRoot:
    return LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(path))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_regular_mp4_candidate_accepted(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = root / "clip.mp4"
    media.write_bytes(b"not-a-real-mp4")
    candidate, _extension = resolve_safe_candidate_path(
        _posix_root(root),
        MediaRelativePath("clip.mp4"),
    )
    assert candidate == media


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_gif_candidate_accepted(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    media = root / "anim.gif"
    media.write_bytes(b"gif")
    candidate, _extension = resolve_safe_candidate_path(
        _posix_root(root),
        MediaRelativePath("anim.gif"),
    )
    assert candidate.name == "anim.gif"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_nested_symlink_directory_rejected(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "clip.mp4").write_bytes(b"x")
    linked = root / "linked"
    linked.symlink_to(secret)
    with pytest.raises(MediaAnalysisUnavailableError):
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("linked/clip.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_final_symlink_file_rejected(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    real = root / "real.mp4"
    real.write_bytes(b"x")
    link = root / "link.mp4"
    link.symlink_to(real)
    with pytest.raises(MediaAnalysisUnavailableError):
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("link.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_root_symlink_allowed(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "clip.mp4").write_bytes(b"x")
    linked_root = tmp_path / "linked"
    linked_root.symlink_to(real_root)
    candidate, _extension = resolve_safe_candidate_path(
        _posix_root(linked_root),
        MediaRelativePath("clip.mp4"),
    )
    assert candidate.name == "clip.mp4"


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_hidden_segment_rejected(tmp_path: Path) -> None:
    with pytest.raises(FrameNestMediaAnalysisError):
        MediaRelativePath(".hidden/clip.mp4")


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_directory_rejected(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "folder.mp4").mkdir()
    with pytest.raises(MediaAnalysisUnavailableError):
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("folder.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_missing_candidate_rejected(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(MediaAnalysisUnavailableError):
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("missing.mp4"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
def test_unsupported_extension_rejected_before_tools(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    (root / "readme.txt").write_bytes(b"x")
    with pytest.raises(MediaAnalysisUnavailableError) as exc_info:
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("readme.txt"))
    assert str(exc_info.value) == INVALID_MEDIA_PATH_MESSAGE


@pytest.mark.skipif(os.name == "nt", reason="POSIX-specific filesystem safety tests")
@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO not supported")
def test_fifo_rejected(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    fifo = root / "pipe.mp4"
    os.mkfifo(fifo)
    with pytest.raises(MediaAnalysisUnavailableError):
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("pipe.mp4"))


def test_errors_do_not_leak_absolute_root(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    with pytest.raises(MediaAnalysisUnavailableError) as exc_info:
        resolve_safe_candidate_path(_posix_root(root), MediaRelativePath("missing.mp4"))
    assert str(root) not in str(exc_info.value)
    assert PRIVATE_ROOT not in str(exc_info.value)
    assert str(exc_info.value) == PREPARATION_UNAVAILABLE_MESSAGE
