"""Integration round-trip for catalog projection, export, validate, and compare."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from kronika.application.media_sidecar import MediaSidecarService
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.media_classification import ContentCategory, MovieGenre
from kronika.domain.media_metadata import (
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
)
from kronika.domain.media_sidecar import decode_media_sidecar
from kronika.infrastructure.filesystem.media_sidecar import FilesystemMediaSidecarStore
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_metadata_repository import SqliteMediaMetadataRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
LOCATION_ID = MediaLocationId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef")


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_catalog_projection_export_validate_compare_and_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "library"
    media_dir = library_root / "movies"
    media_dir.mkdir(parents=True)
    media_file = media_dir / "clip.mp4"
    media_file.write_bytes(b"synthetic-media")
    sidecar_path = media_dir / "clip.mp4.framenest.json"

    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    engine = create_sqlite_engine(database_path)
    try:
        devices = SqliteDeviceRepository(engine)
        libraries = SqliteLibraryRepository(engine)
        media = SqliteMediaRepository(engine)
        metadata = SqliteMediaMetadataRepository(engine)
        device = Device(id=DeviceId.new(), display_name="Test device")
        devices.add(device)
        library = Library(
            id=LibraryId.new(),
            device_id=device.id,
            display_name="Test library",
            root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(library_root)),
        )
        libraries.add(library)
        media.add_media(
            LogicalMedia(id=MEDIA_ID, kind=MediaKind.VIDEO, created_at_ms=10, updated_at_ms=20),
        )
        media.add_location(
            MediaLocation(
                id=LOCATION_ID,
                media_id=MEDIA_ID,
                library_id=library.id,
                relative_path=MediaRelativePath("movies/clip.mp4"),
                availability=MediaLocationAvailability.AVAILABLE,
                observed_size_bytes=len(b"synthetic-media"),
                observed_mtime_ns=1,
                created_at_ms=10,
                updated_at_ms=20,
            )
        )
        metadata.create_canonical_tag(
            CanonicalTagKey("mathematics"),
            CanonicalTagDisplayName("Math"),
            now_ms=30,
        )
        metadata.save_media_metadata(
            MEDIA_ID,
            MediaDisplayTitle("Integration Title"),
            MediaDescription("Integration description"),
            (CanonicalTagKey("mathematics"),),
            now_ms=40,
            content_category=ContentCategory.MOVIE,
            genre_keys=(MovieGenre.DRAMA,),
        )
        before_metadata = metadata.get_media_metadata(MEDIA_ID)
        tables_before = _table_names(database_path)

        replace_calls: list[object] = []
        real_replace = os.replace

        def _spy_replace(*args: object, **kwargs: object):
            replace_calls.append((args, kwargs))
            return real_replace(*args, **kwargs)

        monkeypatch.setattr(
            "kronika.infrastructure.filesystem.media_sidecar.os.replace",
            _spy_replace,
        )

        service = MediaSidecarService(
            media,
            libraries,
            metadata,
            FilesystemMediaSidecarStore(),
        )
        projected = service.project(MEDIA_ID, LOCATION_ID)
        assert projected.created_at_ms == before_metadata.created_at_ms
        assert projected.updated_at_ms == before_metadata.updated_at_ms
        assert projected.created_at_ms != 10
        assert [key.value for key in projected.tag_keys] == ["mathematics"]

        created = service.export(MEDIA_ID, LOCATION_ID)
        assert created.status == "created"
        assert sidecar_path.is_file()
        assert sidecar_path.read_bytes() == created.payload
        assert len(replace_calls) == 1

        validated = service.validate_path(str(sidecar_path))
        assert validated == projected
        assert decode_media_sidecar(sidecar_path.read_bytes()) == projected

        compared = service.compare(MEDIA_ID, LOCATION_ID)
        assert compared.status == "match"
        assert compared.error_code == "SIDECAR_COMPARE_MATCH"

        inode_before = sidecar_path.stat().st_ino
        mtime_before = sidecar_path.stat().st_mtime_ns
        replace_calls.clear()
        unchanged = service.export(MEDIA_ID, LOCATION_ID)
        assert unchanged.status == "unchanged"
        assert replace_calls == []
        assert sidecar_path.stat().st_ino == inode_before
        assert sidecar_path.stat().st_mtime_ns == mtime_before
        assert sidecar_path.read_bytes() == created.payload

        after_metadata = metadata.get_media_metadata(MEDIA_ID)
        assert after_metadata == before_metadata
        tables_after = _table_names(database_path)
        assert tables_after == tables_before
        assert not any("sidecar" in name for name in tables_after)
    finally:
        dispose_engine(engine)
