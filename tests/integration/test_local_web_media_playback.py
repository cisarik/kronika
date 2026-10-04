"""Integration proof for the identity-only local media playback endpoint."""

from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import text

from framenest.adapters.api.application import create_app
from framenest.configuration import FrameNestSettings
from framenest.domain.identity_access import ROLE_ADMIN
from tests.support.record_access import install_synthetic_caller
from framenest.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from framenest.infrastructure.persistence.device_repository import SqliteDeviceRepository
from framenest.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from framenest.infrastructure.persistence.library_repository import SqliteLibraryRepository
from framenest.infrastructure.persistence.migrations import upgrade_database_to_head


MP4_BYTES = b"\x00\x00\x00\x18ftypmp42" + b"\x01" * 100
GIF_BYTES = b"GIF89a" + b"\x02" * 50


def _native_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _register_library(database_path: Path, library_root: Path) -> LibraryId:
    engine = create_sqlite_engine(database_path)
    library_id = LibraryId.new()
    try:
        device = Device(id=DeviceId.new(), display_name="Playback Test Device")
        SqliteDeviceRepository(engine).add(device)
        library = Library(
            id=library_id,
            device_id=device.id,
            display_name="Playback Test Library",
            root=LibraryRoot(flavor=_native_flavor(), path=os.path.normpath(str(library_root))),
        )
        SqliteLibraryRepository(engine).add(library)
        return library_id
    finally:
        dispose_engine(engine)


def _publish_for_test(database_path: Path, *media_ids: str) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.begin() as connection:
            for media_id in media_ids:
                connection.execute(
                    text(
                        "INSERT INTO media_content_publications "
                        "(media_id, published_at_ms, publication_origin) "
                        "VALUES (:media_id, 1, 'admin_explicit')"
                    ),
                    {"media_id": media_id},
                )
    finally:
        dispose_engine(engine)


def test_local_web_playback_endpoint_returns_gif_and_mp4_content_identity_only(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "playback.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    (library_root / "entropy.mp4").write_bytes(MP4_BYTES)
    (library_root / "reaction.gif").write_bytes(GIF_BYTES)
    settings = FrameNestSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with TestClient(
        install_synthetic_caller(
            create_app(settings=settings),
            "admin@example.com",
            role=ROLE_ADMIN,
        )
    ) as client:
        imported_entropy = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "entropy.mp4"},
        )
        imported_reaction = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "reaction.gif"},
        )
        assert imported_entropy.status_code == 200
        assert imported_reaction.status_code == 200
        entropy_media_id = imported_entropy.json()["media"]["id"]
        reaction_media_id = imported_reaction.json()["media"]["id"]
        _publish_for_test(database_path, entropy_media_id, reaction_media_id)

        catalog = client.get("/api/media")
        assert catalog.status_code == 200
        catalog_payload = catalog.json()
        entropy_item = next(item for item in catalog_payload["items"] if item["media_id"] == entropy_media_id)
        reaction_item = next(item for item in catalog_payload["items"] if item["media_id"] == reaction_media_id)
        entropy_location = entropy_item["locations"][0]
        reaction_location = reaction_item["locations"][0]
        assert entropy_location["availability"] == "available"
        assert reaction_location["availability"] == "available"
        assert "location_id" in entropy_location
        assert "location_id" in reaction_location

        entropy_url = f"/api/media/{entropy_media_id}/locations/{entropy_location['location_id']}/content"
        reaction_url = f"/api/media/{reaction_media_id}/locations/{reaction_location['location_id']}/content"
        entropy_download_url = f"/api/media/{entropy_media_id}/locations/{entropy_location['location_id']}/download"
        entropy_response = client.get(entropy_url)
        reaction_response = client.get(reaction_url)
        before_counts = _catalog_counts(database_path)
        before_stat = (library_root / "entropy.mp4").stat()
        entropy_download = client.get(entropy_download_url)
        after_stat = (library_root / "entropy.mp4").stat()
        after_counts = _catalog_counts(database_path)

        assert entropy_response.status_code == 200
        assert entropy_response.content == MP4_BYTES
        assert entropy_response.headers["content-type"] == "video/mp4"
        assert "content-disposition" not in entropy_response.headers
        assert "text/html" not in entropy_response.headers["content-type"]
        assert "application/json" not in entropy_response.headers["content-type"]
        assert reaction_response.status_code == 200
        assert reaction_response.content == GIF_BYTES
        assert reaction_response.headers["content-type"] == "image/gif"
        assert "text/html" not in reaction_response.headers["content-type"]
        assert "application/json" not in reaction_response.headers["content-type"]
        assert entropy_download.status_code == 200
        assert entropy_download.content == MP4_BYTES
        assert entropy_download.headers["content-type"] == "video/mp4"
        assert entropy_download.headers["content-disposition"] == 'attachment; filename="entropy.mp4"'
        assert before_counts == after_counts
        assert before_stat.st_size == after_stat.st_size
        assert before_stat.st_mtime_ns == after_stat.st_mtime_ns

        assert str(library_root) not in entropy_response.text
        assert str(library_root) not in reaction_response.text
        assert str(library_root) not in entropy_download.text
        assert "relative_path" not in entropy_url
        assert "relative_path" not in entropy_download_url


def test_local_web_playback_rejects_offline_location(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "playback-offline.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    (library_root / "offline.mp4").write_bytes(MP4_BYTES)
    settings = FrameNestSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with TestClient(
        install_synthetic_caller(
            create_app(settings=settings),
            "admin@example.com",
            role=ROLE_ADMIN,
        )
    ) as client:
        imported = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "offline.mp4"},
        )
        assert imported.status_code == 200
        media_id = imported.json()["media"]["id"]
        _publish_for_test(database_path, media_id)

        engine = create_sqlite_engine(database_path)
        try:
            with engine.begin() as connection:
                connection.execute(
                    text("UPDATE physical_media_locations SET availability = 'offline' WHERE media_id = :media_id"),
                    {"media_id": media_id},
                )
        finally:
            dispose_engine(engine)

        catalog = client.get("/api/media")
        assert catalog.status_code == 200
        item = next(i for i in catalog.json()["items"] if i["media_id"] == media_id)
        location = item["locations"][0]
        assert location["availability"] == "offline"

        response = client.get(f"/api/media/{media_id}/locations/{location['location_id']}/content")
        assert response.status_code == 409
        download = client.get(f"/api/media/{media_id}/locations/{location['location_id']}/download")
        assert download.status_code == 409


def test_local_web_download_uses_sanitized_fallback_for_unsafe_filename(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "download-filename.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    unsafe_name = "💾\r\n.mp4"
    (library_root / unsafe_name).write_bytes(MP4_BYTES)
    settings = FrameNestSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with TestClient(
        install_synthetic_caller(
            create_app(settings=settings),
            "admin@example.com",
            role=ROLE_ADMIN,
        )
    ) as client:
        imported = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": unsafe_name},
        )
        assert imported.status_code == 200
        media_id = imported.json()["media"]["id"]
        _publish_for_test(database_path, media_id)
        catalog = client.get("/api/media")
        item = next(i for i in catalog.json()["items"] if i["media_id"] == media_id)
        location = item["locations"][0]
        response = client.get(f"/api/media/{media_id}/locations/{location['location_id']}/download")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    # The served fallback filename is what reaches a Downloads folder, so it is
    # user-visible. The structural contract is pinned - one attachment filename
    # carrying the media identity and the preserved extension - and the retired
    # spelling is forbidden, while the current brand word is deliberately not
    # spelled out here so this pin keeps its meaning across the remaining
    # identity cuts instead of breaking on a spelling this test does not own.
    assert re.fullmatch(
        rf'attachment; filename="[a-z0-9-]+-{media_id}\.mp4"', disposition
    ), disposition
    assert "framenest" not in disposition.lower()
    assert "\r" not in disposition
    assert "\n" not in disposition


def test_local_web_download_rejects_missing_file_without_path_disclosure(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "download-missing.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    target = library_root / "missing-later.mp4"
    target.write_bytes(MP4_BYTES)
    settings = FrameNestSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
    library_id = _register_library(database_path, library_root)

    with TestClient(
        install_synthetic_caller(
            create_app(settings=settings),
            "admin@example.com",
            role=ROLE_ADMIN,
        )
    ) as client:
        imported = client.post(
            f"/api/libraries/{library_id}/media-imports",
            json={"relative_path": "missing-later.mp4"},
        )
        assert imported.status_code == 200
        media_id = imported.json()["media"]["id"]
        _publish_for_test(database_path, media_id)
        catalog = client.get("/api/media")
        item = next(i for i in catalog.json()["items"] if i["media_id"] == media_id)
        location = item["locations"][0]
        target.unlink()
        response = client.get(f"/api/media/{media_id}/locations/{location['location_id']}/download")

    assert response.status_code == 409
    assert str(library_root) not in response.text
    assert "missing-later.mp4" not in response.text


def _catalog_counts(database_path: Path) -> tuple[int, int, int]:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            return (
                connection.execute(text("SELECT COUNT(*) FROM logical_media")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM physical_media_locations")).scalar_one(),
                connection.execute(text("SELECT COUNT(*) FROM media_metadata")).scalar_one(),
            )
    finally:
        dispose_engine(engine)
