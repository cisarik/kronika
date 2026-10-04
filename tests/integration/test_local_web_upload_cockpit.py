"""Integration proof for the local upload cockpit backend flow."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from kronika.adapters.api.application import create_app
from kronika.domain.identity_access import ROLE_ADMIN
from tests.support.record_access import install_synthetic_caller
from kronika.adapters.api.upload_api import UploadApiDependencies
from kronika.application.ports.upload_media_validation import (
    UploadMediaValidationEvidence,
    UploadMediaValidationRejectedError,
)
from kronika.application.upload_transport import (
    UploadSessionLockRegistry,
    UploadTransportLimits,
    UploadTransportService,
)
from kronika.application.upload_validation import UPLOAD_VALIDATION_INVALID_MEDIA
from kronika.application.upload_validation import ValidateReceivedUpload
from kronika.application.upload_validation_coordinator import UploadValidationCoordinator
from kronika.configuration import KronikaSettings
from kronika.domain.uploads import UploadValidatedFormat, UploadValidatedMediaKind
from kronika.infrastructure.filesystem.quarantine_storage import FilesystemQuarantineStorage
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)


class _EmptyLibraryRepository:
    def list_all(self) -> tuple[object, ...]:
        return ()


class _SyntheticUploadValidator:
    def validate(self, reader) -> UploadMediaValidationEvidence:
        prefix = reader.read(16)
        if prefix.startswith(b"GIF89a") or prefix.startswith(b"GIF87a"):
            return UploadMediaValidationEvidence(
                UploadValidatedMediaKind.ANIMATED_IMAGE,
                UploadValidatedFormat.GIF,
            )
        if prefix.startswith(b"\xff\xd8\xff"):
            return UploadMediaValidationEvidence(
                UploadValidatedMediaKind.IMAGE,
                UploadValidatedFormat.JPEG,
            )
        if len(prefix) >= 8 and prefix[4:8] == b"ftyp":
            return UploadMediaValidationEvidence(
                UploadValidatedMediaKind.VIDEO,
                UploadValidatedFormat.MP4,
            )
        raise UploadMediaValidationRejectedError(UPLOAD_VALIDATION_INVALID_MEDIA)


def _settings(tmp_path: Path) -> KronikaSettings:
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        upload_quarantine_root=quarantine_root,
        upload_max_total_bytes=128,
        upload_max_patch_bytes=4,
        upload_session_ttl_seconds=60,
        upload_min_free_space_reserve_bytes=0,
        _env_file=None,
    )


def _client_with_upload_validation(tmp_path: Path):
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    storage = FilesystemQuarantineStorage(settings.upload_quarantine_root)  # type: ignore[arg-type]
    locks = UploadSessionLockRegistry()
    transport = UploadTransportService(
        repository,
        storage,
        _EmptyLibraryRepository(),
        UploadTransportLimits(128, 4, 60, 0),
        quarantine_root=settings.upload_quarantine_root,
        preview_cache_root=tmp_path / "preview-cache",
        locks=locks,
    )
    validator = ValidateReceivedUpload(
        repository,
        storage,
        _SyntheticUploadValidator(),
        locks=locks,
    )
    coordinator = UploadValidationCoordinator(repository, validator, locks)
    app = install_synthetic_caller(
        create_app(
            settings=settings,
            upload_api_dependencies=UploadApiDependencies(
                transport=transport,
                validation_coordinator=coordinator,
            ),
        ),
        "admin@example.com",
        role=ROLE_ADMIN,
    )
    return TestClient(app), settings, engine, coordinator


def _patch(client: TestClient, upload_id: str, offset: int, payload: bytes):
    return client.patch(
        f"/api/uploads/{upload_id}",
        content=payload,
        headers={
            "content-type": "application/offset+octet-stream",
            "upload-offset": str(offset),
        },
    )


def _upload_and_validate(
    client: TestClient,
    coordinator: UploadValidationCoordinator,
    *,
    filename: str,
    payload: bytes,
) -> dict[str, object]:
    created = client.post(
        "/api/uploads",
        json={"display_filename": filename, "declared_size_bytes": len(payload)},
    )
    assert created.status_code == 201
    upload_id = created.json()["id"]

    offset = 0
    patch_responses = []
    while offset < len(payload):
        response = _patch(client, upload_id, offset, payload[offset : offset + 4])
        patch_responses.append(response)
        assert response.status_code == 200
        offset = response.json()["received_size_bytes"]
    complete = client.post(f"/api/uploads/{upload_id}/complete")
    asyncio.run(coordinator.drain())
    status = client.get(f"/api/uploads/{upload_id}")

    assert len(patch_responses) >= 2
    assert complete.status_code == 200
    assert complete.json()["state"] == "received"
    assert status.status_code == 200
    return status.json()


@pytest.mark.parametrize(
    ("filename", "payload", "expected_kind", "expected_format"),
    [
        ("synthetic.gif", b"GIF89a" + b"\x00" * 10, "animated_image", "gif"),
        ("synthetic.mp4", b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 8, "video", "mp4"),
        ("synthetic.jpg", b"\xff\xd8\xff" + b"\x00" * 10, "image", "jpg"),
    ],
)
def test_local_upload_api_flow_reaches_publish_pending_without_catalog_visibility(
    tmp_path: Path,
    filename: str,
    payload: bytes,
    expected_kind: str,
    expected_format: str,
) -> None:
    client, settings, engine, coordinator = _client_with_upload_validation(tmp_path)
    try:
        status = _upload_and_validate(
            client,
            coordinator,
            filename=filename,
            payload=payload,
        )
        with sqlite3.connect(settings.database_path) as connection:
            logical_count = connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()
            location_count = connection.execute(
                "SELECT COUNT(*) FROM physical_media_locations"
            ).fetchone()
            upload_evidence = connection.execute(
                "SELECT validated_media_kind, validated_format FROM upload_sessions"
            ).fetchone()
    finally:
        asyncio.run(coordinator.shutdown())
        dispose_engine(engine)

    assert status["state"] == "publish_pending"
    assert status["received_size_bytes"] == status["declared_size_bytes"]
    assert status["failure_code"] is None
    assert logical_count == (0,)
    assert location_count == (0,)
    assert upload_evidence == (expected_kind, expected_format)
    assert str(settings.upload_quarantine_root) not in str(status)
    assert str(settings.database_path) not in str(status)


def test_exact_duplicate_requires_resolution_without_catalog_visibility(
    tmp_path: Path,
) -> None:
    client, settings, engine, coordinator = _client_with_upload_validation(tmp_path)
    quarantine_root = settings.upload_quarantine_root
    assert quarantine_root is not None
    payload = b"GIF89a" + b"\x00" * 10
    try:
        canonical = _upload_and_validate(
            client,
            coordinator,
            filename="canonical.gif",
            payload=payload,
        )
        duplicate = _upload_and_validate(
            client,
            coordinator,
            filename="different-name.gif",
            payload=payload,
        )

        with sqlite3.connect(settings.database_path) as connection:
            before_resolution = connection.execute(
                "SELECT id, state, storage_key FROM upload_sessions ORDER BY created_at_ms, id"
            ).fetchall()
            logical_before = connection.execute(
                "SELECT COUNT(*) FROM logical_media"
            ).fetchone()
            locations_before = connection.execute(
                "SELECT COUNT(*) FROM physical_media_locations"
            ).fetchone()

        kept = client.post(
            f"/api/uploads/{duplicate['id']}/duplicate-resolution",
            json={"resolution": "keep_separate"},
        )
        later_duplicate = _upload_and_validate(
            client,
            coordinator,
            filename="third-name.gif",
            payload=payload,
        )
        with sqlite3.connect(settings.database_path) as connection:
            later_storage_key = connection.execute(
                "SELECT storage_key FROM upload_sessions WHERE id = ?",
                (later_duplicate["id"],),
            ).fetchone()
        discarded = client.post(
            f"/api/uploads/{later_duplicate['id']}/duplicate-resolution",
            json={"resolution": "discard"},
        )

        with sqlite3.connect(settings.database_path) as connection:
            final_states = dict(
                connection.execute("SELECT id, state FROM upload_sessions").fetchall()
            )
            logical_after = connection.execute(
                "SELECT COUNT(*) FROM logical_media"
            ).fetchone()
            locations_after = connection.execute(
                "SELECT COUNT(*) FROM physical_media_locations"
            ).fetchone()
    finally:
        asyncio.run(coordinator.shutdown())
        dispose_engine(engine)

    assert canonical["state"] == "publish_pending"
    assert duplicate["state"] == "duplicate_pending"
    assert sorted(row[1] for row in before_resolution) == [
        "duplicate_pending",
        "publish_pending",
    ]
    assert logical_before == logical_after == (0,)
    assert locations_before == locations_after == (0,)
    assert kept.status_code == 200
    assert kept.json()["state"] == "publish_pending"
    assert later_duplicate["state"] == "duplicate_pending"
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "cancelled"
    assert final_states[canonical["id"]] == "publish_pending"
    assert final_states[duplicate["id"]] == "publish_pending"
    assert final_states[later_duplicate["id"]] == "cancelled"
    assert later_storage_key is not None
    assert not (quarantine_root / f"{later_storage_key[0]}.part").exists()
    for upload_id, _, storage_key in before_resolution:
        assert upload_id in {canonical["id"], duplicate["id"]}
        quarantine_path = quarantine_root / f"{storage_key}.part"
        assert quarantine_path.read_bytes() == payload


def test_local_upload_api_flow_rejects_invalid_content_with_sanitized_failure(
    tmp_path: Path,
) -> None:
    client, settings, engine, coordinator = _client_with_upload_validation(tmp_path)
    try:
        status = _upload_and_validate(
            client,
            coordinator,
            filename="invalid.gif",
            payload=b"not-media-bytes",
        )
        with sqlite3.connect(settings.database_path) as connection:
            logical_count = connection.execute("SELECT COUNT(*) FROM logical_media").fetchone()
    finally:
        asyncio.run(coordinator.shutdown())
        dispose_engine(engine)

    assert status["state"] == "rejected"
    assert status["failure_code"] == UPLOAD_VALIDATION_INVALID_MEDIA
    assert logical_count == (0,)
    assert "not-media-bytes" not in str(status)
    assert str(settings.upload_quarantine_root) not in str(status)
