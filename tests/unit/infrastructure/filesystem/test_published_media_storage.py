"""Filesystem safety tests for atomic published-original ownership."""

from __future__ import annotations

import hashlib
import errno
import os
from pathlib import Path
import stat
import uuid

import pytest

from kronika.application.ports.published_media_storage import (
    PublishedMediaInsufficientSpaceError,
    PublishedMediaStorageUnavailableError,
    PublishedMediaTargetCollisionError,
    PublishedMediaVerificationError,
    PublishedMediaWriteError,
)
from kronika.domain.identities import LibraryId, MediaByteIdentityId
from kronika.domain.upload_publications import new_upload_publication_reservation
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.filesystem.published_media_storage import (
    FilesystemPublishedMediaStorage,
)
import kronika.infrastructure.filesystem.published_media_storage as storage_module
from kronika.infrastructure.filesystem.quarantine_storage import (
    FilesystemQuarantineStorage,
)


def _publication(data: bytes):
    digest = hashlib.sha256(data).hexdigest()
    upload = UploadSession(
        id=UploadSessionId(uuid.UUID("11111111-1111-4111-8111-111111111111")),
        state=UploadSessionState.PUBLISH_PENDING,
        storage_key=UploadStorageKey("synthetic-upload-0001"),
        display_filename=UploadDisplayFilename("private-client-name.mp4"),
        declared_size_bytes=len(data),
        received_size_bytes=len(data),
        checksum_algorithm="sha256",
        checksum_hex=digest,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=MediaByteIdentityId(
            uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        ),
        created_at_ms=10,
        updated_at_ms=20,
        expires_at_ms=100,
        failure_code=None,
        version=2,
    )
    return new_upload_publication_reservation(
        upload,
        destination_id=LibraryId(
            uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
        ),
        now_ms=20,
    )


def _quarantine(tmp_path: Path, data: bytes):
    root = tmp_path / "quarantine"
    root.mkdir()
    storage = FilesystemQuarantineStorage(root)
    key = UploadStorageKey("synthetic-upload-0001")
    writer = storage.open_writer(key, offset=0, create=True)
    try:
        assert writer.write(data) == len(data)
        writer.flush_and_fsync()
    finally:
        writer.close()
    return storage, key, root


def _published_storage(tmp_path: Path, publication):
    root = tmp_path / "published"
    root.mkdir()
    return FilesystemPublishedMediaStorage(
        publication.destination_id,
        root,
    ), root


def test_streams_hashes_fsyncs_and_atomically_links_without_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = (b"synthetic-mp4-payload" * 70_000) + b"end"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    real_link = os.link
    calls: list[tuple[object, ...]] = []

    def recording_link(*args, **kwargs):
        calls.append((args, kwargs))
        return real_link(*args, **kwargs)

    monkeypatch.setattr(storage_module.os, "link", recording_link)
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    final = root / publication.relative_path.value
    assert final.read_bytes() == data
    assert stat.S_IMODE(final.stat().st_mode) == 0o600
    assert published.verify_target(publication) is True
    assert len(calls) == 1
    assert calls[0][1]["follow_symlinks"] is False
    assert not list(root.glob("*.publish.tmp"))


@pytest.mark.parametrize(
    "temporary_bytes",
    [b"synt", b"synthetic-safe-recovery"],
    ids=["partial", "complete"],
)
def test_owned_partial_or_complete_temporary_is_rebuilt_and_target_is_reconstructed(
    tmp_path: Path,
    temporary_bytes: bytes,
) -> None:
    data = b"synthetic-safe-recovery"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.write_bytes(temporary_bytes)
    temp.chmod(0o600)

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert published.verify_target(publication) is True
    assert not temp.exists()
    quarantine.remove(key)
    assert published.verify_target(publication) is True


def test_hard_linked_existing_temporary_is_rejected_and_retry_succeeds_after_alias_removal(
    tmp_path: Path,
) -> None:
    data = b"synthetic-hard-link-guard"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.write_bytes(b"synthetic-stale-temporary")
    temp.chmod(0o600)
    alias = root / ".synthetic-hard-link-alias"
    os.link(temp, alias)

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert temp.read_bytes() == b"synthetic-stale-temporary"
    assert alias.read_bytes() == b"synthetic-stale-temporary"
    assert not (root / publication.relative_path.value).exists()

    alias.unlink()
    retry_reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, retry_reader)
    finally:
        retry_reader.close()

    final = root / publication.relative_path.value
    assert final.read_bytes() == data
    assert not temp.exists()
    assert published.verify_target(publication) is True
    assert quarantine.file_size(key) == len(data)


def test_temporary_aliased_to_another_publication_object_fails_closed(
    tmp_path: Path,
) -> None:
    data = b"synthetic-alias-victim-guard"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    victim_bytes = b"synthetic-neighboring-publication-bytes"
    victim = root / "synthetic-neighboring-publication-object"
    victim.write_bytes(victim_bytes)
    victim.chmod(0o600)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    os.link(victim, temp)
    assert temp.stat().st_nlink == 2

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert victim.read_bytes() == victim_bytes
    assert temp.read_bytes() == victim_bytes
    assert not (root / publication.relative_path.value).exists()
    assert quarantine.file_size(key) == len(data)


def test_verified_final_first_recovery_removes_surviving_legitimate_temporary(
    tmp_path: Path,
) -> None:
    data = b"synthetic-final-first-recovery"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    first_reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, first_reader)
    finally:
        first_reader.close()
    assert published.verify_target(publication) is True

    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.write_bytes(b"synthetic-surviving-temporary")
    temp.chmod(0o600)

    second_reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, second_reader)
    finally:
        second_reader.close()

    final = root / publication.relative_path.value
    assert final.read_bytes() == data
    assert not temp.exists()
    assert published.verify_target(publication) is True
    assert quarantine.file_size(key) == len(data)


def test_symlink_temporary_is_rejected_without_touching_link_target(
    tmp_path: Path,
) -> None:
    data = b"synthetic-symlink-temporary"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    external_bytes = b"synthetic-external-bytes"
    external = tmp_path / "synthetic-external-object"
    external.write_bytes(external_bytes)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.symlink_to(external)

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert external.read_bytes() == external_bytes
    assert temp.is_symlink()
    assert not (root / publication.relative_path.value).exists()
    assert quarantine.file_size(key) == len(data)


def test_non_regular_temporary_path_is_rejected_without_rewrite(
    tmp_path: Path,
) -> None:
    data = b"synthetic-non-regular-temporary"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.mkdir()

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert temp.is_dir()
    assert not (root / publication.relative_path.value).exists()
    assert quarantine.file_size(key) == len(data)


def test_wrong_mode_existing_temporary_is_rejected_before_truncation(
    tmp_path: Path,
) -> None:
    data = b"synthetic-wrong-mode-temporary"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    temp = root / f".{publication.publication_id.value.hex}.publish.tmp"
    temp.write_bytes(b"synthetic-stale-temporary")
    temp.chmod(0o644)

    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert temp.read_bytes() == b"synthetic-stale-temporary"
    assert not (root / publication.relative_path.value).exists()
    assert quarantine.file_size(key) == len(data)


def test_unexpected_final_collision_is_never_overwritten_or_adopted(
    tmp_path: Path,
) -> None:
    expected = b"expected-source"
    unexpected = b"unexpected-existing-object"
    publication = _publication(expected)
    quarantine, key, _ = _quarantine(tmp_path, expected)
    published, root = _published_storage(tmp_path, publication)
    final = root / publication.relative_path.value
    final.write_bytes(unexpected)
    final.chmod(0o600)
    reader = quarantine.open_reader(key, expected_size_bytes=len(expected))
    try:
        with pytest.raises(PublishedMediaTargetCollisionError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert final.read_bytes() == unexpected
    assert quarantine.file_size(key) == len(expected)


def test_failure_before_durable_target_leaves_source_and_owned_partial_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-fsync-failure"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    real_fsync_file = storage_module._fsync_file

    def fail_fsync(_: int) -> None:
        raise PublishedMediaWriteError("published media write failed")

    monkeypatch.setattr(storage_module, "_fsync_file", fail_fsync)
    try:
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert quarantine.file_size(key) == len(data)
    assert not (root / publication.relative_path.value).exists()
    assert (root / f".{publication.publication_id.value.hex}.publish.tmp").exists()

    monkeypatch.setattr(storage_module, "_fsync_file", real_fsync_file)
    retry_reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, retry_reader)
    finally:
        retry_reader.close()
    assert published.verify_target(publication) is True


def test_source_mutation_and_symlink_or_non_regular_targets_fail_closed(
    tmp_path: Path,
) -> None:
    data = b"stable-source"
    publication = _publication(data)
    quarantine, key, quarantine_root = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    quarantine_file = quarantine_root / "synthetic-upload-0001.part"
    quarantine_file.write_bytes(b"changed-bytes")
    try:
        with pytest.raises(PublishedMediaVerificationError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    final = root / publication.relative_path.value
    external = tmp_path / "external"
    external.write_bytes(data)
    final.symlink_to(external)
    with pytest.raises(PublishedMediaTargetCollisionError):
        published.verify_target(publication)
    assert external.read_bytes() == data


def test_root_must_be_native_non_symlink_writable_and_separate(
    tmp_path: Path,
) -> None:
    data = b"root-safety"
    publication = _publication(data)
    real_root = tmp_path / "real-published"
    real_root.mkdir()
    symlink_root = tmp_path / "published-link"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    symlinked = FilesystemPublishedMediaStorage(
        publication.destination_id,
        symlink_root,
    )
    overlapping = FilesystemPublishedMediaStorage(
        publication.destination_id,
        real_root,
        forbidden_roots=(real_root / "database",),
    )
    missing = FilesystemPublishedMediaStorage(
        publication.destination_id,
        tmp_path / "missing",
    )

    assert symlinked.root_available is False
    assert overlapping.root_available is False
    assert missing.root_available is False
    with pytest.raises(PublishedMediaStorageUnavailableError):
        overlapping.verify_target(publication)


def test_destination_free_space_is_queried_before_copy_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-capacity-check"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    queried: list[Path] = []
    real_disk_usage = storage_module.shutil.disk_usage

    def tracking_disk_usage(path):
        queried.append(Path(path))
        return real_disk_usage(path)

    monkeypatch.setattr(storage_module.shutil, "disk_usage", tracking_disk_usage)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert queried == [root]
    assert published.verify_target(publication) is True
    assert str(root) not in "insufficient published media storage"


def test_insufficient_destination_space_fails_before_partial_materialization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-no-space"
    publication = _publication(data)
    quarantine, key, quarantine_root = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reserve = 64
    published = FilesystemPublishedMediaStorage(
        publication.destination_id,
        root,
        min_free_space_reserve_bytes=reserve,
    )

    class _Usage:
        free = len(data) + reserve - 1
        used = 0
        total = free + used

    monkeypatch.setattr(
        storage_module.shutil,
        "disk_usage",
        lambda _path: _Usage(),
    )
    opened: list[str] = []
    real_open = os.open

    def tracking_open(path, flags, *args, **kwargs):
        if isinstance(path, str) and path.endswith(".publish.tmp"):
            opened.append(path)
        return real_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(storage_module.os, "open", tracking_open)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(
            PublishedMediaInsufficientSpaceError,
            match="insufficient published media storage",
        ) as exc_info:
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert opened == []
    assert not list(root.glob("*.publish.tmp"))
    assert not (root / publication.relative_path.value).exists()
    assert quarantine.file_size(key) == len(data)
    assert quarantine_root.exists()
    error_text = str(exc_info.value)
    assert str(root) not in error_text
    assert str(quarantine_root) not in error_text


def test_copy_path_requires_source_size_plus_reserve(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-reserve-math"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    root = tmp_path / "published"
    root.mkdir()
    reserve = 100
    published = FilesystemPublishedMediaStorage(
        publication.destination_id,
        root,
        min_free_space_reserve_bytes=reserve,
    )
    observed: list[int] = []

    class _Usage:
        def __init__(self, free: int) -> None:
            self.free = free
            self.used = 0
            self.total = free

    def fake_disk_usage(_path):
        free = len(data) + reserve
        observed.append(free)
        return _Usage(free)

    monkeypatch.setattr(storage_module.shutil, "disk_usage", fake_disk_usage)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()
    assert observed == [len(data) + reserve]
    assert published.verify_target(publication) is True


def test_idempotent_verified_target_skips_allocation_charge(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-already-published"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    queried = {"count": 0}

    def fail_if_queried(_path):
        queried["count"] += 1
        raise AssertionError("verified target must not charge destination allocation")

    monkeypatch.setattr(storage_module.shutil, "disk_usage", fail_if_queried)
    retry = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, retry)
    finally:
        retry.close()
    assert queried["count"] == 0
    assert published.verify_target(publication) is True


def test_late_enospc_cleans_partial_destination_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data = b"synthetic-late-enospc-payload"
    publication = _publication(data)
    quarantine, key, quarantine_root = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    real_write = os.write

    def enospc_write(fd: int, data_view):
        del fd, data_view
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(storage_module.os, "write", enospc_write)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(
            PublishedMediaInsufficientSpaceError,
            match="insufficient published media storage",
        ) as exc_info:
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()

    assert not (root / publication.relative_path.value).exists()
    assert not list(root.glob("*.publish.tmp"))
    assert quarantine.file_size(key) == len(data)
    assert str(root) not in str(exc_info.value)
    assert str(quarantine_root) not in str(exc_info.value)

    monkeypatch.setattr(storage_module.os, "write", real_write)
    retry = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, retry)
    finally:
        retry.close()
    assert published.verify_target(publication) is True


def test_same_destination_hardlink_is_not_double_charged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Temp->final hardlink shares one inode; only one full copy allocation is charged."""
    data = b"synthetic-single-charge"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    charges: list[int] = []

    class _Usage:
        free = 10**9
        used = 0
        total = free

    original_ensure = FilesystemPublishedMediaStorage._ensure_space_for_copy

    def tracking_ensure(self, requested_bytes: int) -> None:
        charges.append(requested_bytes)
        return original_ensure(self, requested_bytes)

    monkeypatch.setattr(
        FilesystemPublishedMediaStorage,
        "_ensure_space_for_copy",
        tracking_ensure,
    )
    monkeypatch.setattr(
        storage_module.shutil,
        "disk_usage",
        lambda _path: _Usage(),
    )
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.publish_from_reader(publication, reader)
    finally:
        reader.close()
    assert charges == [len(data)]
    final = root / publication.relative_path.value
    assert final.stat().st_nlink == 1
    assert not list(root.glob("*.publish.tmp"))


def test_keep_separate_duplicate_still_requires_full_destination_allocation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keep-separate still materializes a distinct published object via copy."""
    data = b"synthetic-keep-separate-bytes"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    root = tmp_path / "published"
    root.mkdir()
    reserve = 8
    published = FilesystemPublishedMediaStorage(
        publication.destination_id,
        root,
        min_free_space_reserve_bytes=reserve,
    )

    class _Usage:
        free = len(data) + reserve - 1
        used = 0
        total = free + used

    monkeypatch.setattr(
        storage_module.shutil,
        "disk_usage",
        lambda _path: _Usage(),
    )
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        with pytest.raises(PublishedMediaInsufficientSpaceError):
            published.publish_from_reader(publication, reader)
    finally:
        reader.close()
    assert quarantine.file_size(key) == len(data)


def test_request_stop_leaves_no_final_media_and_allows_retry(tmp_path: Path) -> None:
    data = b"synthetic-cooperative-stop-bytes"
    publication = _publication(data)
    quarantine, key, _ = _quarantine(tmp_path, data)
    published, root = _published_storage(tmp_path, publication)
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        published.request_stop()
        with pytest.raises(PublishedMediaWriteError):
            published.publish_from_reader(publication, reader)
        final = root / publication.relative_path.value
        assert not final.exists()
    finally:
        reader.close()
    retried = FilesystemPublishedMediaStorage(
        publication.destination_id,
        root,
        forbidden_roots=(),
    )
    reader = quarantine.open_reader(key, expected_size_bytes=len(data))
    try:
        retried.publish_from_reader(publication, reader)
    finally:
        reader.close()
    assert (root / publication.relative_path.value).read_bytes() == data
