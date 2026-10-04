"""Read-only safety integration tests for library scan preview."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kronika.application.library_scan import LibraryScanLimits
from kronika.domain import LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.filesystem.library_scanner import LocalLibraryScanner


def _native_root(path: Path) -> LibraryRoot:
    flavor = LibraryPathFlavor.WINDOWS if os.name == "nt" else LibraryPathFlavor.POSIX
    return LibraryRoot(flavor=flavor, path=os.path.normpath(str(path)))


def _snapshot_tree(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() or path.is_symlink():
            stat_result = path.stat()
            snapshot[str(path.relative_to(root))] = (stat_result.st_size, stat_result.st_mtime_ns)
    return snapshot


def test_preview_does_not_modify_tree_or_read_file_contents(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    nested = tmp_path / "Series"
    nested.mkdir()
    media = nested / "Episode.mkv"
    media.write_bytes(b"0123456789")
    before_paths = {path for path in tmp_path.rglob("*")}
    before_snapshot = _snapshot_tree(tmp_path)

    open_calls: list[str] = []

    original_open = open

    def tracked_open(file: object, *args: object, **kwargs: object) -> object:
        path = os.fspath(file)
        if str(media) in path or path.endswith(".mkv"):
            open_calls.append(path)
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr("builtins.open", tracked_open)

    result = LocalLibraryScanner().preview(_native_root(tmp_path), LibraryScanLimits(100, 100))

    after_paths = {path for path in tmp_path.rglob("*")}
    after_snapshot = _snapshot_tree(tmp_path)
    assert before_paths == after_paths
    assert before_snapshot == after_snapshot
    assert open_calls == []
    assert result.candidates[0].size_bytes == 10
