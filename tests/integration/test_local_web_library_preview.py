"""Integration proof for the read-only local web library browser."""

from __future__ import annotations

import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect

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


def test_local_web_lists_and_explicitly_previews_registered_library(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "registered-library"
    series = library_root / "Series"
    hidden = library_root / ".hidden"
    series.mkdir(parents=True)
    hidden.mkdir()
    (library_root / "clip.gif").write_bytes(b"gif")
    (library_root / "sample.mp4").write_bytes(b"mp4-data")
    (library_root / "notes.txt").write_text("not media", encoding="utf-8")
    (hidden / "secret.mp4").write_bytes(b"hidden")
    (series / "Episode 01.mkv").write_bytes(b"mkv-data")
    before = _snapshot_files(library_root)

    settings = KronikaSettings(database_path=database_path, _env_file=None)
    upgrade_database_to_head(settings)
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
    finally:
        dispose_engine(engine)

    with TestClient(create_app(settings=settings)) as client:
        list_response = client.get("/api/libraries")
        assert list_response.status_code == 200
        assert list_response.json() == {
            "libraries": [
                {
                    "id": library_id.to_string(),
                    "display_name": "Temporary Videos",
                    "path_flavor": _native_flavor().value,
                }
            ]
        }
        assert str(library_root) not in list_response.text

        scan_response = client.post(f"/api/libraries/{library_id}/scan-preview")
        assert scan_response.status_code == 200
        payload = scan_response.json()
        assert payload["library_id"] == library_id.to_string()
        assert payload["limits"] == {"max_entries": 100000, "max_candidates": 1000}
        assert [candidate["relative_path"] for candidate in payload["candidates"]] == [
            "clip.gif",
            "sample.mp4",
            "Series/Episode 01.mkv",
        ]
        assert all(not candidate["relative_path"].startswith("/") for candidate in payload["candidates"])
        assert ".hidden/secret.mp4" not in scan_response.text
        assert "notes.txt" not in scan_response.text
        assert str(library_root) not in scan_response.text

    assert _snapshot_files(library_root) == before
    engine = create_sqlite_engine(database_path)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        dispose_engine(engine)
    assert "media" not in table_names
    assert "media_locations" not in table_names
