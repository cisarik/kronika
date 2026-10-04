"""Filesystem adapter tests for library scan preview."""

from __future__ import annotations

import ast
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from kronika.application.library_scan import (
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanUnavailableError,
    VIDEO_EXTENSIONS,
)
from kronika.domain import LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.filesystem.library_scanner import LocalLibraryScanner

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
SCANNER_MODULE = (
    REPOSITORY_ROOT / "src" / "kronika" / "infrastructure" / "filesystem" / "library_scanner.py"
)
FORBIDDEN_IMPORT_ROOTS = frozenset(
    {
        "alembic",
        "fastapi",
        "pydantic",
        "pydantic_settings",
        "sqlalchemy",
        "starlette",
        "uvicorn",
    }
)


def _native_root(path: Path) -> LibraryRoot:
    flavor = LibraryPathFlavor.WINDOWS if os.name == "nt" else LibraryPathFlavor.POSIX
    return LibraryRoot(flavor=flavor, path=os.path.normpath(str(path)))


def _scanner() -> LocalLibraryScanner:
    return LocalLibraryScanner()


def test_empty_root_produces_empty_preview(tmp_path: Path) -> None:
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(1, 1))
    assert result.candidates == ()
    assert result.summary.entries_seen == 0


def test_nested_regular_files_are_traversed(tmp_path: Path) -> None:
    nested = tmp_path / "Series"
    nested.mkdir()
    (nested / "Episode 01.mkv").write_bytes(b"x" * 5)
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(100, 100))
    assert len(result.candidates) == 1
    assert result.candidates[0].relative_path == "Series/Episode 01.mkv"
    assert result.candidates[0].size_bytes == 5


def test_enumeration_order_is_deterministic(tmp_path: Path) -> None:
    (tmp_path / "zeta.mkv").write_bytes(b"a")
    (tmp_path / "Alpha.mkv").write_bytes(b"b")
    (tmp_path / "beta.mkv").write_bytes(b"c")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(100, 100))
    assert [candidate.relative_path for candidate in result.candidates] == [
        "Alpha.mkv",
        "beta.mkv",
        "zeta.mkv",
    ]


def test_uppercase_extensions_classify_case_insensitively(tmp_path: Path) -> None:
    (tmp_path / "clip.MKV").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.candidates[0].kind == LibraryScanCandidateKind.VIDEO
    assert result.candidates[0].extension == ".mkv"


@pytest.mark.parametrize("extension", sorted(VIDEO_EXTENSIONS))
def test_every_video_extension_classifies(tmp_path: Path, extension: str) -> None:
    filename = f"sample{extension}"
    (tmp_path / filename).write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.candidates[0].kind == LibraryScanCandidateKind.VIDEO


def test_gif_classifies_as_gif(tmp_path: Path) -> None:
    (tmp_path / "anim.gif").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.candidates[0].kind == LibraryScanCandidateKind.GIF


def test_unknown_extension_is_not_candidate(tmp_path: Path) -> None:
    (tmp_path / "notes.txt").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.candidates == ()
    assert result.summary.regular_files_seen == 1


def test_dot_prefixed_file_is_skipped_and_counted(tmp_path: Path) -> None:
    (tmp_path / ".hidden.mkv").write_bytes(b"x")
    (tmp_path / "visible.mkv").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.summary.skipped_hidden_entries == 1
    assert [candidate.relative_path for candidate in result.candidates] == ["visible.mkv"]


def test_dot_prefixed_directory_is_skipped_without_traversal(tmp_path: Path) -> None:
    hidden = tmp_path / ".hidden"
    hidden.mkdir()
    (hidden / "secret.mkv").write_bytes(b"x")
    (tmp_path / "visible.mkv").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.summary.skipped_hidden_entries == 1
    assert result.summary.entries_seen == 2
    assert [candidate.relative_path for candidate in result.candidates] == ["visible.mkv"]


def test_hidden_registered_root_remains_scannable(tmp_path: Path) -> None:
    hidden_root = tmp_path / ".library"
    hidden_root.mkdir()
    (hidden_root / "clip.mkv").write_bytes(b"x")
    result = _scanner().preview(_native_root(hidden_root), LibraryScanLimits(10, 10))
    assert len(result.candidates) == 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX flavor mismatch test is non-Windows only")
def test_path_flavor_mismatch_raises_unavailable(tmp_path: Path) -> None:
    root = LibraryRoot(flavor=LibraryPathFlavor.WINDOWS, path="C:\\Videos")
    with pytest.raises(LibraryScanUnavailableError):
        _scanner().preview(root, LibraryScanLimits(10, 10))


def test_missing_root_raises_unavailable(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(LibraryScanUnavailableError):
        _scanner().preview(_native_root(missing), LibraryScanLimits(10, 10))


def test_non_directory_root_raises_unavailable(tmp_path: Path) -> None:
    file_path = tmp_path / "not-a-dir"
    file_path.write_bytes(b"x")
    with pytest.raises(LibraryScanUnavailableError):
        _scanner().preview(_native_root(file_path), LibraryScanLimits(10, 10))


def test_max_entries_truncates_deterministically(tmp_path: Path) -> None:
    for name in ("a.mkv", "b.mkv", "c.mkv"):
        (tmp_path / name).write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(2, 10))
    assert result.summary.entries_seen == 2
    assert result.summary.truncated is True
    assert len(result.candidates) == 2
    assert [candidate.relative_path for candidate in result.candidates] == ["a.mkv", "b.mkv"]


def test_max_candidates_truncates_with_full_counts(tmp_path: Path) -> None:
    for name in ("a.mkv", "b.mkv", "c.mkv"):
        (tmp_path / name).write_bytes(b"x" * 2)
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 2))
    assert len(result.candidates) == 2
    assert result.summary.candidate_files_seen == 3
    assert result.summary.candidate_bytes_seen == 6
    assert result.summary.candidates_truncated is True


def test_nested_scandir_error_increments_inaccessible_entries(tmp_path: Path) -> None:
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "ok.mkv").write_bytes(b"x")

    original_scandir = os.scandir

    def selective_scandir(path: str | os.PathLike[str]) -> object:
        if os.fspath(path) == os.fspath(nested):
            raise OSError("permission denied")
        return original_scandir(path)

    with patch("os.scandir", side_effect=selective_scandir):
        result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))

    assert result.summary.inaccessible_entries == 1
    assert result.candidates == ()


def test_scan_unavailable_error_does_not_leak_paths(tmp_path: Path) -> None:
    missing = tmp_path / "secret-missing-root"
    with pytest.raises(LibraryScanUnavailableError) as exc_info:
        _scanner().preview(_native_root(missing), LibraryScanLimits(10, 10))
    assert str(missing) not in str(exc_info.value)


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_nested_file_symlink_is_skipped(tmp_path: Path) -> None:
    scan_root = tmp_path / "library"
    scan_root.mkdir()
    outside = tmp_path / "external"
    outside.mkdir()
    target = outside / "real.mkv"
    target.write_bytes(b"x")
    link = scan_root / "linked.mkv"
    os.symlink(target, link)
    result = _scanner().preview(_native_root(scan_root), LibraryScanLimits(10, 10))
    assert result.summary.skipped_symlink_entries == 1
    assert result.candidates == ()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_nested_directory_symlink_is_not_traversed(tmp_path: Path) -> None:
    scan_root = tmp_path / "library"
    scan_root.mkdir()
    outside = tmp_path / "external"
    outside.mkdir()
    real_dir = outside / "real"
    real_dir.mkdir()
    (real_dir / "clip.mkv").write_bytes(b"x")
    link_dir = scan_root / "linked"
    os.symlink(real_dir, link_dir)
    result = _scanner().preview(_native_root(scan_root), LibraryScanLimits(10, 10))
    assert result.summary.skipped_symlink_entries == 1
    assert result.candidates == ()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlinks unsupported")
def test_root_symlink_path_may_be_scanned(tmp_path: Path) -> None:
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "clip.mkv").write_bytes(b"x")
    link_root = tmp_path / "linked"
    os.symlink(real_root, link_root)
    result = _scanner().preview(_native_root(link_root), LibraryScanLimits(10, 10))
    assert len(result.candidates) == 1


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO unsupported")
def test_fifo_entry_is_skipped_as_other(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    (tmp_path / "clip.mkv").write_bytes(b"x")
    result = _scanner().preview(_native_root(tmp_path), LibraryScanLimits(10, 10))
    assert result.summary.skipped_other_entries == 1


def test_filesystem_scanner_imports_no_forbidden_modules() -> None:
    tree = ast.parse(SCANNER_MODULE.read_text(encoding="utf-8"), filename=str(SCANNER_MODULE))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name.split(".")[0]
        elif isinstance(node, ast.ImportFrom):
            module = (node.module or "").split(".")[0]
        else:
            continue
        if module in FORBIDDEN_IMPORT_ROOTS:
            violations.append(module)
    assert violations == []
