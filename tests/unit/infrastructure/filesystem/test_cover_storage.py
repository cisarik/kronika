"""Filesystem and Pillow adapters for durable cover artifacts and thumbnails."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from kronika.application.media_analysis import RepresentativeFrame
from kronika.application.ports.cover_storage import (
    CoverArtifact,
    CoverStorageError,
    CoverThumbnailImage,
    CoverThumbnailUnavailableError,
)
from kronika.domain.identities import MediaId
from kronika.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    COVER_THUMBNAIL_ALGORITHM,
)
from kronika.infrastructure.filesystem.cover_storage import (
    COVER_ARTIFACT_MAX_BYTES,
    COVER_ARTIFACT_MAX_LONG_EDGE,
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
    PillowCoverEncoder,
)

MEDIA_ID = MediaId.from_string("11111111-1111-4111-8111-111111111111")
DIGEST = "b" * 64


def _png_frame(width: int = 160, height: int = 90, color: tuple[int, int, int] = (200, 30, 30)) -> RepresentativeFrame:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buffer, format="PNG")
    return RepresentativeFrame(
        timestamp_ms=250,
        mime_type="image/png",
        sha256=__import__("hashlib").sha256(buffer.getvalue()).hexdigest(),
        byte_size=len(buffer.getvalue()),
        payload=buffer.getvalue(),
    )


def _artifact_from_payload(payload: bytes, width: int, height: int) -> CoverArtifact:
    import hashlib

    return CoverArtifact(
        profile=COVER_ARTIFACT_PROFILE,
        media_type=COVER_ARTIFACT_MEDIA_TYPE,
        digest=hashlib.sha256(payload).hexdigest(),
        width=width,
        height=height,
        byte_size=len(payload),
        payload=payload,
    )


def test_encoder_produces_bounded_durable_artifact() -> None:
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame(width=1100, height=800))
    assert artifact.media_type == COVER_ARTIFACT_MEDIA_TYPE
    assert artifact.profile == COVER_ARTIFACT_PROFILE
    assert artifact.width <= COVER_ARTIFACT_MAX_LONG_EDGE
    assert artifact.height <= COVER_ARTIFACT_MAX_LONG_EDGE
    assert artifact.byte_size == len(artifact.payload)
    assert artifact.byte_size <= COVER_ARTIFACT_MAX_BYTES
    assert len(artifact.digest) == 64


def test_still_image_pipeline_normalizes_jpeg_and_png_sources_deterministically(
    tmp_path: Path,
) -> None:
    import hashlib

    from kronika.application.media_analysis import MediaRelativePath
    from kronika.infrastructure.media_analysis.still_image import prepare_still_image_analysis

    def prepared_frame(filename: str, payload: bytes) -> RepresentativeFrame:
        source = tmp_path / filename
        source.write_bytes(payload)
        return prepare_still_image_analysis(
            source,
            MediaRelativePath(filename),
        ).representative_frames[0]

    def still_bytes(fmt: str) -> bytes:
        buffer = io.BytesIO()
        Image.new("RGB", (96, 64), (5, 15, 25)).save(buffer, format=fmt)
        return buffer.getvalue()

    jpeg_bytes = still_bytes("JPEG")
    png_bytes = still_bytes("PNG")
    encoder = PillowCoverEncoder()

    jpeg_artifact = encoder.encode_artifact_frame(prepared_frame("source.jpg", jpeg_bytes))
    png_artifact = encoder.encode_artifact_frame(prepared_frame("source.png", png_bytes))

    # Both source formats normalize to the identical durable JPEG artifact.
    assert jpeg_artifact.digest == png_artifact.digest
    assert jpeg_artifact.width == png_artifact.width == 96
    assert jpeg_artifact.height == png_artifact.height == 64
    assert jpeg_artifact.byte_size == png_artifact.byte_size
    assert jpeg_artifact.width <= COVER_ARTIFACT_MAX_LONG_EDGE
    assert jpeg_artifact.byte_size <= COVER_ARTIFACT_MAX_BYTES

    with Image.open(io.BytesIO(jpeg_artifact.payload)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (jpeg_artifact.width, jpeg_artifact.height)

    # Deterministic artifact digest for identical source.
    assert (
        encoder.encode_artifact_frame(prepared_frame("source2.jpg", jpeg_bytes)).digest
        == jpeg_artifact.digest
    )

    # Original fixture bytes are never modified.
    assert (tmp_path / "source.jpg").read_bytes() == jpeg_bytes
    assert (tmp_path / "source.png").read_bytes() == png_bytes

    # Thumbnail generation succeeds from the normalized artifact.
    thumbnail = encoder.encode_thumbnail(jpeg_artifact.payload)
    assert thumbnail.media_type == COVER_ARTIFACT_MEDIA_TYPE
    assert thumbnail.sha256 == hashlib.sha256(thumbnail.payload).hexdigest()


def test_encoder_produces_bounded_thumbnail_from_artifact() -> None:
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    thumbnail = encoder.encode_thumbnail(artifact.payload)
    assert thumbnail.media_type == COVER_ARTIFACT_MEDIA_TYPE
    assert thumbnail.byte_size == len(thumbnail.payload)
    assert len(thumbnail.sha256) == 64


def test_storage_publishes_and_validates_immutable_artifact(tmp_path: Path) -> None:
    root = tmp_path / "covers"
    storage = FilesystemDurableCoverStorage(root)
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())

    storage.publish(media_id=MEDIA_ID, artifact=artifact)
    assert storage.artifact_valid(media_id=MEDIA_ID, digest=artifact.digest) is True
    assert storage.read_bytes(media_id=MEDIA_ID, digest=artifact.digest) == artifact.payload

    # Idempotent re-publish of identical bytes is a no-op.
    storage.publish(media_id=MEDIA_ID, artifact=artifact)

    final = root / MEDIA_ID.to_string() / f"{artifact.digest}.jpg"
    assert final.is_file()
    assert storage.artifact_valid(media_id=MEDIA_ID, digest="0" * 64) is False


def test_storage_never_replaces_existing_immutable_identity(tmp_path: Path) -> None:
    root = tmp_path / "covers"
    storage = FilesystemDurableCoverStorage(root)
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    storage.publish(media_id=MEDIA_ID, artifact=artifact)

    final = root / MEDIA_ID.to_string() / f"{artifact.digest}.jpg"
    original = final.read_bytes()

    # Pre-place different bytes at the same immutable identity: publish must
    # refuse to clobber and leave the differing bytes untouched.
    tampered = artifact.payload[:-1] + bytes([artifact.payload[-1] ^ 0xFF])
    final.write_bytes(tampered)
    with pytest.raises(CoverStorageError):
        storage.publish(media_id=MEDIA_ID, artifact=artifact)
    assert final.read_bytes() == tampered


def test_storage_rejects_mismatched_digest_without_publishing(tmp_path: Path) -> None:
    storage = FilesystemDurableCoverStorage(tmp_path / "covers")
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    corrupted = CoverArtifact(
        profile=artifact.profile,
        media_type=artifact.media_type,
        digest="0" * 64,
        width=artifact.width,
        height=artifact.height,
        byte_size=artifact.byte_size,
        payload=artifact.payload,
    )
    with pytest.raises(CoverStorageError):
        storage.publish(media_id=MEDIA_ID, artifact=corrupted)
    assert storage.artifact_valid(media_id=MEDIA_ID, digest="0" * 64) is False


def test_storage_rejects_non_regular_and_symlinked_targets(tmp_path: Path) -> None:
    root = tmp_path / "covers"
    storage = FilesystemDurableCoverStorage(root)
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    media_dir = root / MEDIA_ID.to_string()
    media_dir.mkdir(parents=True)
    final = media_dir / f"{artifact.digest}.jpg"
    final.mkdir()  # directory where a file is expected
    with pytest.raises(CoverStorageError):
        storage.publish(media_id=MEDIA_ID, artifact=artifact)
    assert storage.artifact_valid(media_id=MEDIA_ID, digest=artifact.digest) is False

    final.rmdir()
    outside = tmp_path / "outside0"
    outside.write_bytes(artifact.payload)
    final.symlink_to(outside)
    assert storage.artifact_valid(media_id=MEDIA_ID, digest=artifact.digest) is False
    with pytest.raises(CoverStorageError):
        storage.publish(media_id=MEDIA_ID, artifact=artifact)


def test_thumbnail_cache_no_clobber_contains_and_open(tmp_path: Path) -> None:
    root = tmp_path / "thumbnails"
    cache = FilesystemCoverThumbnailCache(root)
    assert cache.algorithm == COVER_THUMBNAIL_ALGORITHM
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    thumbnail = encoder.encode_thumbnail(artifact.payload)
    key = cache.key_for(media_id=MEDIA_ID, artifact_digest=artifact.digest)

    assert cache.contains(key) is False
    cache.publish(key, thumbnail)
    assert cache.contains(key) is True
    assert cache.contains_many((key,)) == {key}

    # No-clobber on identical re-publish.
    cache.publish(key, thumbnail)

    opened = cache.open(key)
    assert opened.media_type == COVER_ARTIFACT_MEDIA_TYPE
    assert opened.byte_size == len(thumbnail.payload)
    assert opened.payload == thumbnail.payload
    opened.close()

    cached_path = root / COVER_THUMBNAIL_ALGORITHM / MEDIA_ID.to_string() / f"{artifact.digest}.jpg"
    assert cached_path.is_file()


def test_thumbnail_cache_rejects_traversal_and_corruption(tmp_path: Path) -> None:
    cache = FilesystemCoverThumbnailCache(tmp_path / "thumbnails")
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    thumbnail = encoder.encode_thumbnail(artifact.payload)

    with pytest.raises(CoverStorageError):
        cache.key_for(media_id=MEDIA_ID, artifact_digest="not-a-hex")
    cache.publish(
        cache.key_for(media_id=MEDIA_ID, artifact_digest=artifact.digest),
        thumbnail,
    )
    with pytest.raises(CoverStorageError):
        cache.publish(
            cache.key_for(media_id=MEDIA_ID, artifact_digest=artifact.digest),
            CoverThumbnailImage(
                media_type=COVER_ARTIFACT_MEDIA_TYPE,
                byte_size=len(b"not-a-jpeg"),
                sha256=__import__("hashlib").sha256(b"not-a-jpeg").hexdigest(),
                payload=b"not-a-jpeg",
            ),
        )


def test_thumbnail_open_reports_unavailable_for_corrupt_or_absent(tmp_path: Path) -> None:
    root = tmp_path / "thumbnails"
    cache = FilesystemCoverThumbnailCache(root)
    encoder = PillowCoverEncoder()
    artifact = encoder.encode_artifact_frame(_png_frame())
    key = cache.key_for(media_id=MEDIA_ID, artifact_digest=artifact.digest)
    with pytest.raises(CoverThumbnailUnavailableError):
        cache.open(key)

    target = (
        root
        / COVER_THUMBNAIL_ALGORITHM
        / MEDIA_ID.to_string()
        / f"{artifact.digest}.jpg"
    )
    target.parent.mkdir(parents=True)
    target.write_bytes(b"broken")
    assert cache.contains(key) is False
    with pytest.raises(CoverThumbnailUnavailableError):
        cache.open(key)
