"""Unit tests for catalog library root preparation."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kronika.adapters.cli.library_root import (
    LibraryRootNotUsableError,
    native_library_path_flavor,
    prepare_library_root,
)
from kronika.domain import LibraryPathFlavor

SECRET_PATH = "/secret/rejected/library/path"


def _expected_flavor() -> LibraryPathFlavor:
    return (
        LibraryPathFlavor.WINDOWS
        if os.name == "nt"
        else LibraryPathFlavor.POSIX
    )


def test_absolute_directory_is_accepted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    library_dir = tmp_path / "absolute-library"
    library_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    root = prepare_library_root(str(library_dir))

    assert root.flavor == _expected_flavor()
    assert root.path == os.path.normpath(str(library_dir))


def test_relative_directory_is_accepted_from_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "work"
    library_dir = workdir / "nested" / "library"
    library_dir.mkdir(parents=True)
    monkeypatch.chdir(workdir)

    root = prepare_library_root("nested/library")

    assert root.path == os.path.normpath(str(library_dir))


def test_tilde_expansion_uses_home_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    library_dir = home / "Videos"
    library_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)

    root = prepare_library_root("~/Videos")

    assert root.path == os.path.normpath(str(library_dir))


def test_dot_segments_are_lexically_normalized(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workdir = tmp_path / "work"
    library_dir = workdir / "library"
    library_dir.mkdir(parents=True)
    monkeypatch.chdir(workdir)

    root = prepare_library_root("./nested/../library")

    assert root.path == os.path.normpath(str(library_dir))


def test_missing_path_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LibraryRootNotUsableError):
        prepare_library_root(str(tmp_path / "missing"))


def test_regular_file_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    file_path = tmp_path / "not-a-directory.txt"
    file_path.write_text("x", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(LibraryRootNotUsableError):
        prepare_library_root(str(file_path))


def test_unicode_and_spaces_in_directory_names_are_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    library_dir = tmp_path / "Café Library"
    library_dir.mkdir()
    monkeypatch.chdir(tmp_path)

    root = prepare_library_root(str(library_dir))

    assert root.path == os.path.normpath(str(library_dir))


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink preservation test")
def test_symlink_path_is_preserved_without_target_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = tmp_path / "target-library"
    target.mkdir()
    link = tmp_path / "linked-library"
    link.symlink_to(target, target_is_directory=True)
    monkeypatch.chdir(tmp_path)

    root = prepare_library_root(str(link))

    assert root.path == os.path.normpath(str(link))
    assert root.path != os.path.normpath(str(target.resolve()))


def test_native_flavor_matches_platform() -> None:
    assert native_library_path_flavor() == _expected_flavor()


def test_errors_do_not_echo_rejected_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(LibraryRootNotUsableError) as exc_info:
        prepare_library_root(SECRET_PATH)
    assert SECRET_PATH not in str(exc_info.value)
