"""Filesystem safety evidence for private YouTube acquisition staging."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kronika.application.ports.youtube_staging import (
    YouTubeStagingInconsistentError,
    YouTubeStagingUnavailableError,
)
from kronika.infrastructure.youtube.staging import (
    ARTIFACT_FILENAME,
    FilesystemYouTubeStaging,
)

STAGING_KEY = "1" * 32


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "youtube"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def test_prepare_resume_read_and_exact_cleanup(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    storage = FilesystemYouTubeStaging(root)

    claim_directory = storage.prepare(STAGING_KEY)
    assert claim_directory.stat().st_mode & 0o777 == 0o700
    partial = claim_directory / "artifact.mp4.part"
    partial.write_bytes(b"partial")
    assert storage.prepare(STAGING_KEY) == claim_directory
    assert storage.usage_bytes(STAGING_KEY) == 7

    partial.unlink()
    artifact = claim_directory / ARTIFACT_FILENAME
    artifact.write_bytes(b"synthetic-media")
    reader = storage.open_artifact(
        STAGING_KEY,
        expected_size_bytes=len(b"synthetic-media"),
    )
    try:
        assert reader.read(9) == b"synthetic"
        reader.seek(0)
        reader.verify_still_consistent()
    finally:
        reader.close()

    storage.cleanup(STAGING_KEY)
    assert not claim_directory.exists()
    assert root.exists()


def test_default_artifact_filename_remains_mp4(tmp_path: Path) -> None:
    assert ARTIFACT_FILENAME == "artifact.mp4"
    storage = FilesystemYouTubeStaging(_private_root(tmp_path))
    claim_directory = storage.prepare(STAGING_KEY)
    artifact = claim_directory / "artifact.mp4"
    artifact.write_bytes(b"youtube-default-name")
    reader = storage.open_artifact(
        STAGING_KEY,
        expected_size_bytes=len(b"youtube-default-name"),
    )
    try:
        assert reader.read(20) == b"youtube-default-name"
    finally:
        reader.close()


def test_symlink_root_and_overlapping_root_are_rejected(tmp_path: Path) -> None:
    real_root = _private_root(tmp_path)
    symlink_root = tmp_path / "linked"
    symlink_root.symlink_to(real_root, target_is_directory=True)

    linked = FilesystemYouTubeStaging(symlink_root)
    assert linked.root_available is False
    with pytest.raises(YouTubeStagingUnavailableError):
        linked.prepare(STAGING_KEY)

    with pytest.raises(YouTubeStagingUnavailableError):
        FilesystemYouTubeStaging(
            real_root,
            forbidden_roots=(real_root / "quarantine",),
        )


def test_traversal_symlink_and_hard_link_objects_are_rejected(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    storage = FilesystemYouTubeStaging(root)
    claim_directory = storage.prepare(STAGING_KEY)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"outside")

    with pytest.raises(YouTubeStagingInconsistentError):
        storage.prepare("../escape")

    (claim_directory / ARTIFACT_FILENAME).symlink_to(outside)
    with pytest.raises(YouTubeStagingInconsistentError):
        storage.usage_bytes(STAGING_KEY)
    (claim_directory / ARTIFACT_FILENAME).unlink()

    os.link(outside, claim_directory / ARTIFACT_FILENAME)
    with pytest.raises(YouTubeStagingInconsistentError):
        storage.open_artifact(STAGING_KEY)


def test_open_reader_detects_path_replacement(tmp_path: Path) -> None:
    root = _private_root(tmp_path)
    storage = FilesystemYouTubeStaging(root)
    claim_directory = storage.prepare(STAGING_KEY)
    artifact = claim_directory / ARTIFACT_FILENAME
    artifact.write_bytes(b"first")
    reader = storage.open_artifact(STAGING_KEY)
    replacement = claim_directory / "artifact.replacement"
    replacement.write_bytes(b"second")
    os.replace(replacement, artifact)
    try:
        with pytest.raises(YouTubeStagingInconsistentError):
            reader.verify_still_consistent()
    finally:
        reader.close()


def test_cleanup_unlinks_owned_symlink_without_following_target(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    storage = FilesystemYouTubeStaging(root)
    claim_directory = storage.prepare(STAGING_KEY)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"preserve")
    (claim_directory / ARTIFACT_FILENAME).symlink_to(outside)

    storage.cleanup(STAGING_KEY)

    assert outside.read_bytes() == b"preserve"
    assert not claim_directory.exists()
