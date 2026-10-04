"""Filesystem evidence that X staging clear uses descriptor-safe cleanup."""

from __future__ import annotations

from pathlib import Path

from kronika.infrastructure.x.staging import ARTIFACT_FILENAME, FilesystemXStaging

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
