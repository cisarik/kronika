"""Focused tests for persistent gallery preview derivatives."""

from __future__ import annotations

import io
import os
from pathlib import Path

import pytest
from PIL import Image

from kronika.application.gallery_preview import (
    GALLERY_PREVIEW_ALGORITHM_VERSION,
    GalleryPreviewService,
    GalleryPreviewState,
    GalleryPreviewUnavailableError,
)
from kronika.application.media_analysis import (
    PREPARATION_UNAVAILABLE_MESSAGE,
    MediaAnalysisUnavailableError,
    build_representative_frame,
)
from kronika.configuration import KronikaSettings
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot, MediaId, MediaLocationId
from kronika.domain.media import LogicalMedia, MediaKind, MediaLocation, MediaLocationAvailability, MediaRelativePath
from kronika.infrastructure.filesystem.media_content import LocalMediaContentReader
from kronika.infrastructure.media_analysis.gallery_preview import (
    FilesystemGalleryPreviewCache,
    PillowGalleryPreviewEncoder,
)


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _png_payload(color: tuple[int, int, int] = (20, 80, 140)) -> bytes:
    output = io.BytesIO()
    Image.new("RGB", (32, 24), color=color).save(output, format="PNG")
    return output.getvalue()


class _Prepared:
    representative_frames = (build_representative_frame(timestamp_ms=100, payload=_png_payload()),)


class _FakePreparer:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def prepare(self, root, relative_path):
        self.calls += 1
        if self.fail:
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        return _Prepared()


class _ConditionalPreparer:
    def __init__(self, failed_name: str) -> None:
        self.failed_name = failed_name
        self.calls = 0

    def prepare(self, root, relative_path):
        self.calls += 1
        if relative_path.value == self.failed_name:
            raise MediaAnalysisUnavailableError(PREPARATION_UNAVAILABLE_MESSAGE)
        return _Prepared()


class _LibraryRepository:
    def __init__(self, libraries):
        self.libraries = tuple(libraries)

    def get(self, library_id):
        return next((library for library in self.libraries if library.id == library_id), None)

    def list_all(self):
        return self.libraries


class _MediaRepository:
    def __init__(self, media, locations):
        self.media = tuple(media)
        self.locations = tuple(locations)

    def get_media(self, media_id):
        return next((media for media in self.media if media.id == media_id), None)

    def list_media(self):
        return self.media

    def get_location(self, location_id):
        return next((location for location in self.locations if location.id == location_id), None)

    def list_all_locations(self):
        return self.locations


def _fixture(tmp_path: Path, *, cache_inside_root: bool = False, availability=MediaLocationAvailability.AVAILABLE, filename="clip.gif"):
    source_root = tmp_path / "library"
    source_root.mkdir()
    media_path = source_root / filename
    media_path.write_bytes(b"GIF89a" + b"\x00" * 100)
    library = Library(
        id=LibraryId.new(),
        device_id=DeviceId.new(),
        display_name="Test Library",
        root=LibraryRoot(flavor=_native_flavor(), path=os.path.normpath(str(source_root))),
    )
    media = LogicalMedia(id=MediaId.new(), kind=MediaKind.ANIMATED_IMAGE, created_at_ms=1, updated_at_ms=1)
    location = MediaLocation(
        id=MediaLocationId.new(),
        media_id=media.id,
        library_id=library.id,
        relative_path=MediaRelativePath(filename),
        availability=availability,
        observed_size_bytes=media_path.stat().st_size,
        observed_mtime_ns=media_path.stat().st_mtime_ns,
        created_at_ms=1,
        updated_at_ms=1,
    )
    cache_root = source_root / "cache" if cache_inside_root else tmp_path / "cache"
    preparer = _FakePreparer()
    service = GalleryPreviewService(
        _MediaRepository((media,), (location,)),
        _LibraryRepository((library,)),
        LocalMediaContentReader(),
        preparer,
        PillowGalleryPreviewEncoder(),
        FilesystemGalleryPreviewCache(cache_root),
    )
    return service, media, location, media_path, cache_root, preparer


def test_configuration_rejects_relative_gallery_preview_cache_root() -> None:
    with pytest.raises(ValueError, match="gallery preview cache path must be an absolute path"):
        KronikaSettings(
            database_path=Path("/tmp/framenest-test.sqlite3"),
            gallery_preview_cache_path=Path("relative-cache"),
            _env_file=None,
        )


def test_key_is_stable_and_changes_for_source_and_algorithm(tmp_path: Path, monkeypatch) -> None:
    service, media, location, media_path, _cache_root, _preparer = _fixture(tmp_path)
    first = service.status().libraries[0]
    status_a = service.status().missing_count
    key_a = service.plan_generate(library_id=location.library_id, include_all=False, max_items=1).to_generate[0].cache_key
    key_b = service.plan_generate(library_id=location.library_id, include_all=False, max_items=1).to_generate[0].cache_key
    assert first.total_count == 1
    assert status_a == 1
    assert key_a == key_b

    media_path.write_bytes(media_path.read_bytes() + b"x")
    key_c = service.plan_generate(library_id=location.library_id, include_all=False, max_items=1).to_generate[0].cache_key
    assert key_c != key_a

    monkeypatch.setattr("kronika.application.gallery_preview.GALLERY_PREVIEW_ALGORITHM_VERSION", "gallery-preview-jpeg-v2")
    key_d = service.plan_generate(library_id=location.library_id, include_all=False, max_items=1).to_generate[0].cache_key
    assert key_d != key_c
    assert GALLERY_PREVIEW_ALGORITHM_VERSION == "gallery-preview-jpeg-v1"


def test_generation_is_bounded_idempotent_and_does_not_mutate_source(tmp_path: Path) -> None:
    service, media, location, media_path, cache_root, preparer = _fixture(tmp_path)
    before_bytes = media_path.read_bytes()
    before_stat = media_path.stat()

    generated = service.generate_one(media.id, location.id)
    assert generated.state == GalleryPreviewState.READY
    jpgs = list(cache_root.rglob("*.jpg"))
    assert len(jpgs) == 1
    assert jpgs[0].read_bytes().startswith(b"\xff\xd8")
    first_mtime = jpgs[0].stat().st_mtime_ns
    assert media_path.read_bytes() == before_bytes
    assert media_path.stat().st_size == before_stat.st_size

    repeated = service.generate_one(media.id, location.id)
    assert repeated.state == GalleryPreviewState.READY
    assert preparer.calls == 1
    assert jpgs[0].stat().st_mtime_ns == first_mtime


def test_generation_failure_publishes_no_final_and_cleans_temp(tmp_path: Path) -> None:
    service, media, location, _media_path, cache_root, preparer = _fixture(tmp_path)
    preparer.fail = True
    with pytest.raises(GalleryPreviewUnavailableError):
        service.generate_one(media.id, location.id)
    assert not list(cache_root.rglob("*.jpg"))
    assert not list(cache_root.rglob("*.tmp"))


def test_cache_writes_cannot_occur_inside_registered_source_root(tmp_path: Path) -> None:
    service, media, location, _media_path, cache_root, _preparer = _fixture(tmp_path, cache_inside_root=True)
    with pytest.raises(GalleryPreviewUnavailableError):
        service.generate_one(media.id, location.id)
    assert not cache_root.exists()


def test_missing_derivative_is_not_served_until_generated(tmp_path: Path) -> None:
    """Cataloged media alone is not enough for Gallery card preview delivery.

    Runtime acceptance fixtures must run gallery-preview generation before expecting
    card previews; Details playback uses original media content and does not require
    the derivative.
    """
    service, media, location, _media_path, _cache_root, preparer = _fixture(tmp_path)
    assert service.status().missing_count == 1
    assert preparer.calls == 0
    with pytest.raises(GalleryPreviewUnavailableError):
        service.open_ready(media.id, location.id)
    generated = service.generate_one(media.id, location.id)
    assert generated.state == GalleryPreviewState.READY
    opened = service.open_ready(media.id, location.id)
    try:
        assert opened.media_type == "image/jpeg"
        assert opened.byte_size > 0
    finally:
        opened.close()


def test_source_change_makes_existing_derivative_stale_and_not_ready(tmp_path: Path) -> None:
    service, media, location, media_path, _cache_root, _preparer = _fixture(tmp_path)
    assert service.generate_one(media.id, location.id).state == GalleryPreviewState.READY
    media_path.write_bytes(media_path.read_bytes() + b"x")
    status = service.status()
    assert status.stale_count == 1
    with pytest.raises(GalleryPreviewUnavailableError):
        service.open_ready(media.id, location.id)


def test_symlink_cache_artifact_is_rejected(tmp_path: Path) -> None:
    service, media, location, _media_path, cache_root, _preparer = _fixture(tmp_path)
    key = service.plan_generate(library_id=location.library_id, include_all=False, max_items=1).to_generate[0].cache_key
    target = cache_root.joinpath(*key.split("/"))
    target.parent.mkdir(parents=True)
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"not a jpeg")
    target.symlink_to(outside)
    assert service.status().missing_count == 1
    with pytest.raises(GalleryPreviewUnavailableError):
        service.open_ready(media.id, location.id)


def test_unavailable_media_is_classified_without_generation(tmp_path: Path) -> None:
    service, _media, _location, _media_path, _cache_root, preparer = _fixture(
        tmp_path,
        availability=MediaLocationAvailability.OFFLINE,
    )
    status = service.status()
    assert status.unavailable_count == 1
    assert preparer.calls == 0


def test_unsupported_media_is_classified_without_generation(tmp_path: Path) -> None:
    service, _media, _location, _media_path, _cache_root, preparer = _fixture(
        tmp_path,
        filename="unsupported.txt",
    )
    status = service.status()
    assert status.unsupported_count == 1
    assert preparer.calls == 0


def test_generate_continues_after_one_failed_medium(tmp_path: Path) -> None:
    service, media, location, media_path, _cache_root, _preparer = _fixture(tmp_path)
    failed_path = media_path.parent / "failed.gif"
    failed_path.write_bytes(b"GIF89a" + b"\x01" * 100)
    failed_media = LogicalMedia(id=MediaId.new(), kind=MediaKind.ANIMATED_IMAGE, created_at_ms=2, updated_at_ms=2)
    failed_location = MediaLocation(
        id=MediaLocationId.new(),
        media_id=failed_media.id,
        library_id=location.library_id,
        relative_path=MediaRelativePath("failed.gif"),
        availability=MediaLocationAvailability.AVAILABLE,
        observed_size_bytes=failed_path.stat().st_size,
        observed_mtime_ns=failed_path.stat().st_mtime_ns,
        created_at_ms=2,
        updated_at_ms=2,
    )
    preparer = _ConditionalPreparer("failed.gif")
    service._media_repository.media = (media, failed_media)  # type: ignore[attr-defined]
    service._media_repository.locations = (location, failed_location)  # type: ignore[attr-defined]
    service._preparer = preparer  # type: ignore[attr-defined]
    plan = service.plan_generate(library_id=location.library_id, include_all=False, max_items=10)
    summary = service.generate(plan)
    assert summary.generated_count == 1
    assert summary.failed_count == 1
    assert preparer.calls == 2
