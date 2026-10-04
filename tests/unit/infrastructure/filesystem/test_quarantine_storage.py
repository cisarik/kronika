"""Unit tests for server-owned quarantine upload storage."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from kronika.application.ports.quarantine_storage import QuarantineStateInconsistentError
from kronika.domain.uploads import FrameNestUploadSessionError, UploadStorageKey
from kronika.infrastructure.filesystem.quarantine_storage import FilesystemQuarantineStorage


def _key(value: str = "uploadquarantinekey0001") -> UploadStorageKey:
    return UploadStorageKey(value)


def test_root_must_exist_and_not_be_symlink(tmp_path: Path) -> None:
    missing = FilesystemQuarantineStorage(tmp_path / "missing")
    assert not missing.root_available

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    assert not FilesystemQuarantineStorage(link).root_available


def test_exclusive_regular_file_creation_permissions_append_truncate_and_remove(
    tmp_path: Path,
) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    key = _key()

    writer = storage.open_writer(key, offset=0, create=True)
    try:
        assert writer.write(b"abc") == 3
        writer.flush_and_fsync()
    finally:
        writer.close()

    path = next(tmp_path.iterdir())
    assert path.name == f"{key.value}.part"
    assert path.read_bytes() == b"abc"
    assert stat.S_IMODE(path.stat().st_mode) & 0o111 == 0
    assert storage.file_size(key) == 3

    with pytest.raises(Exception):
        storage.open_writer(key, offset=3, create=True)

    writer = storage.open_writer(key, offset=3, create=False)
    try:
        assert writer.write(b"def") == 3
        writer.truncate_and_fsync(4)
    finally:
        writer.close()

    assert path.read_bytes() == b"abcd"
    storage.truncate(key, 2)
    assert path.read_bytes() == b"ab"
    storage.remove(key)
    storage.remove(key)
    assert not path.exists()


def test_storage_key_cannot_select_path_or_client_filename(tmp_path: Path) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    with pytest.raises(FrameNestUploadSessionError):
        UploadStorageKey("../private-file")

    key = _key("clientfilenameignored001")
    writer = storage.open_writer(key, offset=0, create=True)
    try:
        writer.write(b"x")
        writer.flush_and_fsync()
    finally:
        writer.close()

    assert sorted(path.name for path in tmp_path.iterdir()) == [f"{key.value}.part"]


def test_open_reader_reads_complete_regular_file_and_rejects_size_mismatch(
    tmp_path: Path,
) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    key = _key("readeruploadkey0001")
    path = tmp_path / f"{key.value}.part"
    path.write_bytes(b"abcdef")

    reader = storage.open_reader(key, expected_size_bytes=6)
    try:
        assert reader.size_bytes == 6
        assert reader.read(3) == b"abc"
        reader.seek_start()
        assert reader.read(10) == b"abcdef"
        reader.verify_still_consistent()
    finally:
        reader.close()

    with pytest.raises(QuarantineStateInconsistentError):
        storage.open_reader(key, expected_size_bytes=5)


def test_open_reader_rejects_missing_and_non_regular_objects(tmp_path: Path) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    with pytest.raises(QuarantineStateInconsistentError):
        storage.open_reader(_key("missingreaderkey001"), expected_size_bytes=1)

    directory_key = _key("directoryreaderkey1")
    (tmp_path / f"{directory_key.value}.part").mkdir()
    with pytest.raises(QuarantineStateInconsistentError):
        storage.open_reader(directory_key, expected_size_bytes=0)


def test_reader_keeps_stable_identity_after_path_replacement(tmp_path: Path) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    key = _key("replacementreader001")
    path = tmp_path / f"{key.value}.part"
    path.write_bytes(b"original")

    reader = storage.open_reader(key, expected_size_bytes=8)
    try:
        path.unlink()
        path.write_bytes(b"replaced")

        assert reader.read(100) == b"original"
        reader.verify_still_consistent()
        replacement_reader = storage.open_reader(key, expected_size_bytes=8)
        try:
            assert replacement_reader.read(100) == b"replaced"
        finally:
            replacement_reader.close()
    finally:
        reader.close()


def test_reader_detects_mutation_before_success(tmp_path: Path) -> None:
    storage = FilesystemQuarantineStorage(tmp_path)
    key = _key("mutationreader0001")
    path = tmp_path / f"{key.value}.part"
    path.write_bytes(b"abcdef")

    reader = storage.open_reader(key, expected_size_bytes=6)
    try:
        path.write_bytes(b"ABCDEF")
        with pytest.raises(QuarantineStateInconsistentError):
            reader.verify_still_consistent()
    finally:
        reader.close()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="symlink unavailable")
def test_symlink_file_is_rejected_without_following_target(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-upload-target"
    outside.write_bytes(b"outside")
    key = _key("symlinkuploadkey001")
    (tmp_path / f"{key.value}.part").symlink_to(outside)

    storage = FilesystemQuarantineStorage(tmp_path)

    with pytest.raises(QuarantineStateInconsistentError):
        storage.file_size(key)
    with pytest.raises(QuarantineStateInconsistentError):
        storage.open_reader(key, expected_size_bytes=7)
    assert outside.read_bytes() == b"outside"
