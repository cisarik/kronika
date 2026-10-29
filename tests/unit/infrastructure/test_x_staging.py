"""Filesystem evidence that X staging clear uses descriptor-safe cleanup."""

from __future__ import annotations

from pathlib import Path
import os

import pytest

from kronika.application.ports.x_extractor import XExtractionError
from kronika.application.ports.youtube_staging import YouTubeStagingInconsistentError
from kronika.infrastructure.x.staging import (
    ARTIFACT_FILENAME,
    FilesystemXStaging,
    find_artifact,
)

STAGING_KEY = "a" * 32


def _private_root(tmp_path: Path) -> Path:
    root = tmp_path / "x-staging"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return root


def test_clear_removes_partial_artifact_through_descriptor_safe_cleanup(
    tmp_path: Path,
) -> None:
    root = _private_root(tmp_path)
    staging = FilesystemXStaging(root)
    claim_directory = staging.prepare(STAGING_KEY)
    artifact = claim_directory / ARTIFACT_FILENAME
    artifact.write_bytes(b"partial-artifact-bytes")
    assert artifact.exists()

    staging.clear(STAGING_KEY)

    assert not (root / STAGING_KEY).exists()
    assert not artifact.exists()


def test_clear_is_idempotent_when_staging_directory_is_absent(tmp_path: Path) -> None:
    staging = FilesystemXStaging(_private_root(tmp_path))
    staging.clear(STAGING_KEY)
    staging.clear(STAGING_KEY)


def test_x_staging_artifact_filename_is_bin(tmp_path: Path) -> None:
    assert ARTIFACT_FILENAME == "artifact.bin"
    staging = FilesystemXStaging(_private_root(tmp_path))
    claim_directory = staging.prepare(STAGING_KEY)
    artifact = claim_directory / "artifact.bin"
    artifact.write_bytes(b"x-neutral-name")
    reader = staging.open_artifact(STAGING_KEY, expected_size_bytes=14)
    try:
        assert reader.read(14) == b"x-neutral-name"
    finally:
        reader.close()


@pytest.mark.parametrize("name", ["artifact.bin.mp4", "artifact.bin.WebM", "artifact.bin.123"])
def test_resolver_accepts_one_merged_name(tmp_path: Path, name: str) -> None:
    artifact = tmp_path / name
    artifact.write_bytes(b"merged")
    assert find_artifact(tmp_path) == artifact


def test_resolver_prefers_exact_name_over_merged_candidates(tmp_path: Path) -> None:
    exact = tmp_path / ARTIFACT_FILENAME
    for name in (ARTIFACT_FILENAME, "artifact.bin.mp4", "artifact.bin.mkv"):
        (tmp_path / name).write_bytes(name.encode())
    assert find_artifact(tmp_path) == exact


@pytest.mark.parametrize(
    "name",
    [
        "artifact.bin.part", "artifact.bin.PART", "artifact.bin.partial",
        "artifact.bin.tmp", "artifact.bin.temp", "artifact.bin.ytdl",
        "artifact.bin.download", "artifact.bin.mp4.part", "artifact.bin.f137.mp4",
        "artifact.bin.", "artifact.bin.toolongsuffix", "artifact.bin.mp_4",
        "artifact.bin.mр4", "unrelated.mp4", "artifactXbin.mp4",
    ],
)
def test_resolver_rejects_temporary_or_nonmatching_names(tmp_path: Path, name: str) -> None:
    (tmp_path / name).write_bytes(b"not-a-completed-artifact")
    assert find_artifact(tmp_path) is None


@pytest.mark.parametrize("name", [ARTIFACT_FILENAME, "artifact.bin.mp4"])
@pytest.mark.parametrize("kind", ["symlink", "dangling-symlink", "directory", "fifo"])
def test_resolver_never_selects_nonregular_entries(tmp_path: Path, name: str, kind: str) -> None:
    entry = tmp_path / name
    if kind == "directory":
        entry.mkdir()
    elif kind == "fifo":
        os.mkfifo(entry)
    else:
        target = tmp_path / "target"
        if kind == "symlink":
            target.write_bytes(b"outside-artifact")
        entry.symlink_to(target)
    assert find_artifact(tmp_path) is None


def test_merged_reader_fails_closed_on_ambiguity(tmp_path: Path) -> None:
    staging = FilesystemXStaging(_private_root(tmp_path))
    directory = staging.prepare(STAGING_KEY)
    for name in ("artifact.bin.mp4", "artifact.bin.mkv"):
        (directory / name).write_bytes(b"video")
    with pytest.raises(XExtractionError) as ctx:
        staging.open_artifact(STAGING_KEY)
    assert ctx.value.code == "X_STAGING_AMBIGUOUS"
    staging.clear(STAGING_KEY)
    assert not directory.exists()


@pytest.mark.parametrize("suffix", [".part", ".tmp", ".mp4.part"])
def test_staging_reader_does_not_publish_partial_files(tmp_path: Path, suffix: str) -> None:
    staging = FilesystemXStaging(_private_root(tmp_path))
    directory = staging.prepare(STAGING_KEY)
    (directory / (ARTIFACT_FILENAME + suffix)).write_bytes(b"partial")
    assert staging.artifact_size(STAGING_KEY) is None
    with pytest.raises(FileNotFoundError):
        staging.open_artifact(STAGING_KEY)


@pytest.mark.parametrize("defect", ["size", "hardlink", "executable", "symlink"])
def test_merged_reader_retains_descriptor_validation(tmp_path: Path, defect: str) -> None:
    staging = FilesystemXStaging(_private_root(tmp_path))
    directory = staging.prepare(STAGING_KEY)
    artifact = directory / "artifact.bin.mp4"
    artifact.write_bytes(b"merged")
    if defect == "hardlink":
        os.link(artifact, tmp_path / "other-link")
    elif defect == "executable":
        artifact.chmod(0o700)
    elif defect == "symlink":
        artifact.unlink()
        artifact.symlink_to(tmp_path / "outside")
        (tmp_path / "outside").write_bytes(b"merged")
    error = FileNotFoundError if defect == "symlink" else YouTubeStagingInconsistentError
    with pytest.raises(error):
        staging.open_artifact(STAGING_KEY, expected_size_bytes=7 if defect == "size" else 6)


def test_merged_reader_detects_replacement_and_can_resume(tmp_path: Path) -> None:
    staging = FilesystemXStaging(_private_root(tmp_path))
    directory = staging.prepare(STAGING_KEY)
    artifact = directory / "artifact.bin.mp4"
    artifact.write_bytes(b"merged")
    reader = staging.open_artifact(STAGING_KEY, expected_size_bytes=6)
    try:
        reader.seek(3)
        assert reader.read(3) == b"ged"
        reader.verify_still_consistent()
        replacement = directory / "artifact.bin.tmp"
        replacement.write_bytes(b"merged")
        replacement.replace(artifact)
        with pytest.raises(YouTubeStagingInconsistentError):
            reader.verify_still_consistent()
    finally:
        reader.close()
