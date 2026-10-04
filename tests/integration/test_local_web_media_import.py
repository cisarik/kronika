"""Integration proof for explicit local web media import."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _snapshot_files(root: Path) -> dict[str, tuple[int, int]]:
    snapshot: dict[str, tuple[int, int]] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file():
            stat = path.stat()
            snapshot[path.relative_to(root).as_posix()] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _register_library(database_path: Path, library_root: Path) -> LibraryId:
    engine = create_sqlite_engine(database_path)
    library_id = LibraryId.new()
    try:
        device = Device(id=DeviceId.new(), display_name="Test Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Temporary Videos",
            root=LibraryRoot(
                flavor=_native_flavor(),
                path=os.path.normpath(str(library_root)),
            ),
        )
        SqliteLibraryRepository(engine).add(library)
        return library_id
    finally:
        dispose_engine(engine)


def test_local_web_explicitly_imports_video_and_gif_candidates_idempotently(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    (library_root / "sample.mp4").write_bytes(b"mp4-data")
    (library_root / "clip.gif").write_bytes(b"gif-data")
    (library_root / "notes.txt").write_text("not media", encoding="utf-8")
    before = _snapshot_files(library_root)

    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with TestClient(create_app(settings=settings)) as client:
        video_response = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "sample.mp4"},
        )
        repeat_video_response = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "sample.mp4"},
        )
        gif_response = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "clip.gif"},
        )
        missing_response = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "missing.mp4"},
        )

    assert video_response.status_code == 200
    assert repeat_video_response.status_code == 200
    assert gif_response.status_code == 200
    assert missing_response.status_code == 409
    video_payload = video_response.json()
    repeat_payload = repeat_video_response.json()
    gif_payload = gif_response.json()
    assert video_payload["status"] == "created"
    assert repeat_payload["status"] == "already_imported"
    assert repeat_payload["media"] == video_payload["media"]
    assert repeat_payload["location"] == video_payload["location"]
    assert video_payload["media"]["kind"] == "video"
    assert gif_payload["media"]["kind"] == "animated_image"
    assert video_payload["location"]["relative_path"] == "sample.mp4"
    assert video_payload["location"]["observed_size_bytes"] == 8
    assert gif_payload["location"]["relative_path"] == "clip.gif"
    assert gif_payload["location"]["observed_size_bytes"] == 8
    assert missing_response.json()["error"]["code"] == "MEDIA_IMPORT_CANDIDATE_UNAVAILABLE"
    for response in (video_response, repeat_video_response, gif_response, missing_response):
        assert str(library_root) not in response.text
        assert str(database_path) not in response.text

    assert _snapshot_files(library_root) == before
    assert sorted(path.relative_to(library_root).as_posix() for path in library_root.rglob("*")) == [
        "clip.gif",
        "notes.txt",
        "sample.mp4",
    ]

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            logical_media_count = connection.execute(
                text("SELECT COUNT(*) FROM logical_media")
            ).scalar_one()
            location_rows = connection.execute(
                text(
                    "SELECT library_id, relative_path, availability, observed_size_bytes "
                    "FROM physical_media_locations ORDER BY relative_path"
                )
            ).all()
    finally:
        dispose_engine(engine)
    assert logical_media_count == 2
    assert location_rows == [
        (library_id.to_string(), "clip.gif", "available", 8),
        (library_id.to_string(), "sample.mp4", "available", 8),
    ]
