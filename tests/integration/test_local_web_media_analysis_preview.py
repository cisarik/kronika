"""Integration proof for explicit local web media-analysis preview."""

from __future__ import annotations

import base64
import os
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import inspect, text

from kronika.adapters.api.application import create_app
from kronika.adapters.api.media_analysis_api import MediaAnalysisApiDependencies
from kronika.application.library_scan import LibraryScanCandidateKind
from kronika.application.media_analysis import (
    MediaRelativePath,
    MediaAnalysisUnavailableError,
    PreparedAnalysisResult,
    PrepareLocalMediaAnalysis,
    REQUESTED_FRAME_COUNT,
    TechnicalMetadata,
    build_representative_frame,
    PNG_SIGNATURE,
)
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


class _DeterministicLocalPreparer:
    def __init__(self, expected_relative_path: str, png_payload: bytes) -> None:
        self.expected_relative_path = expected_relative_path
        self.png_payload = png_payload
        self.calls: list[tuple[LibraryRoot, MediaRelativePath]] = []

    def prepare(
        self,
        root: LibraryRoot,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        self.calls.append((root, relative_path))
        if relative_path.value != self.expected_relative_path:
            raise MediaAnalysisUnavailableError("Local media analysis preparation is not available.")
        return PreparedAnalysisResult(
            relative_path=relative_path,
            candidate_kind=LibraryScanCandidateKind.VIDEO,
            technical_metadata=TechnicalMetadata(
                duration_ms=1000,
                width=64,
                height=48,
                video_codec="h264",
                container_formats=("mp4",),
                has_audio=False,
            ),
            representative_frames=(
                build_representative_frame(timestamp_ms=100, payload=self.png_payload),
            ),
            requested_frame_count=REQUESTED_FRAME_COUNT,
            warnings=(),
            ffprobe_version="ffprobe version hidden",
            ffmpeg_version="ffmpeg version hidden",
        )


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


def _row_counts(database_path: Path) -> dict[str, int]:
    engine = create_sqlite_engine(database_path)
    try:
        table_names = sorted(inspect(engine).get_table_names())
        with engine.connect() as connection:
            return {
                table_name: connection.execute(text(f"select count(*) from {table_name}")).scalar_one()
                for table_name in table_names
            }
    finally:
        dispose_engine(engine)


def test_local_web_media_analysis_preview_is_explicit_readonly_and_stateless(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "registered-library"
    library_root.mkdir()
    source = library_root / "clip.mp4"
    original_source = b"not-real-media-but-explicit-candidate"
    source.write_bytes(original_source)
    before_files = _snapshot_files(library_root)

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
    before_rows = _row_counts(database_path)

    png_payload = PNG_SIGNATURE + b"integration-frame"
    engine = create_sqlite_engine(database_path)
    repository = SqliteLibraryRepository(engine)
    preparer = _DeterministicLocalPreparer("clip.mp4", png_payload)
    try:
        with TestClient(
            create_app(
                settings=settings,
                media_analysis_api_dependencies=MediaAnalysisApiDependencies(
                    prepare_preview=PrepareLocalMediaAnalysis(repository, preparer),
                    catalog_available=database_path.exists,
                ),
            )
        ) as client:
            response = client.post(
                f"/api/libraries/{library_id}/media-analysis-preview",
                json={"relative_path": "clip.mp4"},
            )
            assert response.status_code == 200
            payload = response.json()
            frame_payload = payload["representative_frames"][0]["payload_base64"]
            assert base64.b64decode(frame_payload, validate=True) == png_payload
            assert payload["technical_metadata"]["width"] == 64
            assert payload["relative_path"] == "clip.mp4"
            assert str(library_root) not in response.text
            assert str(database_path) not in response.text

            missing = client.post(
                f"/api/libraries/{LibraryId.new()}/media-analysis-preview",
                json={"relative_path": "clip.mp4"},
            )
            assert missing.status_code == 404
            assert missing.json()["error"]["code"] == "LIBRARY_NOT_FOUND"

            invalid = client.post(
                f"/api/libraries/{library_id}/media-analysis-preview",
                json={"relative_path": "../clip.mp4"},
            )
            assert invalid.status_code == 422
            assert invalid.json()["error"]["code"] == "INVALID_MEDIA_PATH"

            unavailable = client.post(
                f"/api/libraries/{library_id}/media-analysis-preview",
                json={"relative_path": "missing.mp4"},
            )
            assert unavailable.status_code == 409
            assert unavailable.json()["error"]["code"] == "MEDIA_ANALYSIS_UNAVAILABLE"
    finally:
        dispose_engine(engine)

    assert len(preparer.calls) == 2
    assert preparer.calls[0][1] == MediaRelativePath("clip.mp4")
    assert source.read_bytes() == original_source
    assert _snapshot_files(library_root) == before_files
    assert not any(path.suffix == ".png" for path in library_root.rglob("*"))
    assert _row_counts(database_path) == before_rows
