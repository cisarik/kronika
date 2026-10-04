"""Contract tests for resumable quarantine upload API."""

from __future__ import annotations

import asyncio
import json
import sqlite3
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.domain.identity_access import ROLE_ADMIN
from tests.support.record_access import synthetic_identity
from sqlalchemy import insert, text

from kronika.adapters.api.upload_api import (
    UploadApiDependencies,
    _catalog_media_id,
    _parse_content_length,
    _parse_upload_offset,
)
from kronika.application.upload_catalog import CatalogPublishedUpload
from kronika.application.upload_transport import (
    UploadSessionSnapshot,
    UploadTransportLimits,
    UploadTransportService,
)
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identities import MediaByteIdentityId
from kronika.domain.upload_publications import new_upload_publication_reservation
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)
from kronika.infrastructure.filesystem.quarantine_storage import FilesystemQuarantineStorage
from kronika.infrastructure.persistence.catalog_schema import devices, libraries, media_byte_identities
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_publication_repository import (
    SqliteUploadPublicationRepository,
)
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)


def _identified(app):
    class _Caller:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and SCOPE_IDENTITY not in scope:
                scope[SCOPE_IDENTITY] = synthetic_identity("ada", role=ROLE_ADMIN)
            await self.inner(scope, receive, send)

    app.add_middleware(_Caller)
    return app


def _settings(tmp_path: Path, *, quarantine: bool = True, reserve: int = 0) -> KronikaSettings:
    quarantine_root = tmp_path / "quarantine"
    if quarantine:
        quarantine_root.mkdir(parents=True, exist_ok=True)
    return KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        upload_quarantine_root=quarantine_root if quarantine else None,
        upload_max_total_bytes=16,
        upload_max_patch_bytes=8,
        upload_session_ttl_seconds=60,
        upload_min_free_space_reserve_bytes=reserve,
        _env_file=None,
    )


def _migrated_client(settings: KronikaSettings) -> TestClient:
    upgrade_database_to_head(settings)
    return TestClient(_identified(create_app(settings=settings)))


def _create(client: TestClient, *, size: int = 5, origin: str | None = None):
    headers = {} if origin is None else {"origin": origin}
    return client.post(
        "/api/uploads",
        json={"display_filename": "example.gif", "declared_size_bytes": size},
        headers=headers,
    )


def _patch(client: TestClient, upload_id: str, offset: int, payload: bytes):
    return client.patch(
        f"/api/uploads/{upload_id}",
        content=payload,
        headers={
            "content-type": "application/offset+octet-stream",
            "upload-offset": str(offset),
        },
    )


def _patch_with_content_length(
    client: TestClient,
    upload_id: str,
    *,
    content_length: str,
    payload: bytes = b"a",
):
    return client.patch(
        f"/api/uploads/{upload_id}",
        content=payload,
        headers={
            "content-type": "application/offset+octet-stream",
            "upload-offset": "0",
            "content-length": content_length,
        },
    )


async def _asgi_patch(
    app: Any,
    upload_id: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes = b"a",
) -> tuple[int, dict[str, object]]:
    messages: list[dict[str, object]] = []
    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.disconnect"}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message: dict[str, object]) -> None:
        messages.append(message)

    path = f"/api/uploads/{upload_id}"
    await app(
        {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": "PATCH",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
        },
        receive,
        send,
    )
    status = next(
        int(message["status"])
        for message in messages
        if message["type"] == "http.response.start"
    )
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    return status, json.loads(response_body)


def _raw_patch(
    app: Any,
    upload_id: str,
    headers: list[tuple[bytes, bytes]],
    body: bytes = b"a",
) -> tuple[int, dict[str, object]]:
    return asyncio.run(_asgi_patch(app, upload_id, headers, body))


def _raw_patch_headers(
    *,
    content_type_headers: list[tuple[bytes, bytes]],
    content_length_headers: list[tuple[bytes, bytes]] | None = None,
    upload_offset_headers: list[tuple[bytes, bytes]] | None = None,
) -> list[tuple[bytes, bytes]]:
    return (
        [(b"host", b"testserver")]
        + content_type_headers
        + (content_length_headers or [(b"content-length", b"1")])
        + (upload_offset_headers or [(b"upload-offset", b"0")])
    )


def _raw_request(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "PATCH",
            "path": "/api/uploads/example",
            "headers": headers,
        },
        receive=lambda: None,
    )


class _EmptyLibraryRepository:
    def list_all(self) -> tuple[Library, ...]:
        return ()


class _RecordingCoordinator:
    def __init__(self) -> None:
        self.notifications = 0

    async def start(self) -> None:
        return None

    async def shutdown(self) -> None:
        return None

    def notify(self) -> None:
        self.notifications += 1


class _FailingCoordinator(_RecordingCoordinator):
    def notify(self) -> None:
        raise RuntimeError("synthetic coordinator notification failure")


def _part_file(settings: KronikaSettings) -> Path:
    files = list(settings.upload_quarantine_root.glob("*.part"))  # type: ignore[union-attr]
    assert len(files) == 1
    return files[0]


def _create_validated_duplicate_pair(
    client: TestClient,
    settings: KronikaSettings,
) -> tuple[UploadSession, UploadSession]:
    upload_ids = []
    for _ in range(2):
        created = _create(client).json()
        upload_ids.append(created["id"])
        assert _patch(client, created["id"], 0, b"abcde").status_code == 200
        assert client.post(f"/api/uploads/{created['id']}/complete").status_code == 200

    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    completed = []
    try:
        for index, upload_id in enumerate(upload_ids):
            session_id = UploadSessionId.from_string(upload_id)
            session = repository.get(session_id)
            assert session is not None
            validating = repository.start_validation(
                session_id,
                expected_version=session.version,
                updated_at_ms=session.updated_at_ms + 1,
            )
            completed.append(
                repository.complete_validation_success(
                    session_id,
                    expected_version=validating.version,
                    checksum_hex="9" * 64,
                    validated_media_kind=UploadValidatedMediaKind.VIDEO,
                    validated_format=UploadValidatedFormat.MP4,
                    updated_at_ms=validating.updated_at_ms + 1,
                )
            )
    finally:
        dispose_engine(engine)

    assert completed[0].state is UploadSessionState.PUBLISH_PENDING
    assert completed[1].state is UploadSessionState.DUPLICATE_PENDING
    return completed[0], completed[1]


def test_unconfigured_upload_capability_fails_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path, quarantine=False)
    client = _migrated_client(settings)

    response = _create(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPLOAD_CAPABILITY_NOT_CONFIGURED"
    assert str(tmp_path) not in response.text


def test_upload_capability_exposes_enabled_limits_ttl_and_disabled_state(
    tmp_path: Path,
) -> None:
    enabled_settings = _settings(tmp_path / "enabled")
    disabled_settings = _settings(tmp_path / "disabled", quarantine=False)
    enabled_client = _migrated_client(enabled_settings)
    disabled_client = _migrated_client(disabled_settings)

    enabled = enabled_client.get("/api/uploads/capability")
    disabled = disabled_client.get("/api/uploads/capability")

    assert enabled.status_code == 200
    assert enabled.json() == {
        "uploads_enabled": True,
        "max_total_size_bytes": 16,
        "max_chunk_size_bytes": 8,
        "session_ttl_seconds": 60,
    }
    assert disabled.status_code == 200
    assert disabled.json() == {
        "uploads_enabled": False,
        "max_total_size_bytes": 16,
        "max_chunk_size_bytes": 8,
        "session_ttl_seconds": 60,
    }
    combined = enabled.text + disabled.text
    assert str(enabled_settings.upload_quarantine_root) not in combined
    assert str(disabled_settings.database_path) not in combined
    assert "storage_key" not in combined
    assert "byte_identity" not in combined


def test_create_and_status_return_sanitized_snapshot_without_storage_details(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)

    response = _create(client)
    payload = response.json()
    status = client.get(f"/api/uploads/{payload['id']}")

    assert response.status_code == 201
    assert status.status_code == 200
    assert payload["state"] == "created"
    assert payload["received_size_bytes"] == 0
    assert payload["failure_code"] is None
    assert status.json()["failure_code"] is None
    assert "storage_key" not in payload
    assert "byte_identity_id" not in payload
    assert "byte_identity" not in response.text + status.text
    assert ".part" not in response.text + status.text
    assert str(settings.upload_quarantine_root) not in response.text + status.text


def test_status_exposes_only_sanitized_failure_code_without_diagnostics(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    created = _create(client).json()
    assert _patch(client, created["id"], 0, b"abcde").status_code == 200
    assert client.post(f"/api/uploads/{created['id']}/complete").status_code == 200
    with sqlite3.connect(settings.database_path) as connection:
        connection.execute(
            "UPDATE upload_sessions SET state = 'rejected', failure_code = 'INVALID_MEDIA' WHERE id = ?",
            (created["id"],),
        )

    status = client.get(f"/api/uploads/{created['id']}")

    assert status.status_code == 200
    assert status.json()["state"] == "rejected"
    assert status.json()["failure_code"] == "INVALID_MEDIA"
    assert "ffprobe" not in status.text.lower()
    assert "traceback" not in status.text.lower()
    assert str(settings.upload_quarantine_root) not in status.text
    assert str(settings.database_path) not in status.text


def test_create_rejects_invalid_metadata_size_limit_free_space_and_cross_origin(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)

    invalid_name = client.post(
        "/api/uploads",
        json={"display_filename": "../../example.gif", "declared_size_bytes": 1},
    )
    too_large = _create(client, size=17)
    cross_origin = _create(client, origin="http://evil.example")

    reserve_settings = _settings(tmp_path / "reserve", reserve=10**18)
    reserve_client = _migrated_client(reserve_settings)
    no_space = _create(reserve_client, size=1)

    assert invalid_name.status_code in {400, 422}
    assert too_large.status_code == 413
    assert cross_origin.status_code == 403
    assert no_space.status_code == 507
    assert str(tmp_path) not in invalid_name.text + too_large.text + no_space.text


def test_patch_multiple_chunks_resume_after_app_reconstruction_and_complete(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    created = _create(client).json()

    first = _patch(client, created["id"], 0, b"ab")
    assert first.status_code == 200
    assert first.json()["state"] == "receiving"
    assert first.json()["received_size_bytes"] == 2

    resumed_client = TestClient(_identified(create_app(settings=settings)))
    status = resumed_client.get(f"/api/uploads/{created['id']}")
    second = _patch(resumed_client, created["id"], status.json()["received_size_bytes"], b"cde")
    complete = resumed_client.post(f"/api/uploads/{created['id']}/complete")

    assert status.json()["received_size_bytes"] == 2
    assert second.status_code == 200
    assert second.json()["received_size_bytes"] == 5
    assert complete.status_code == 200
    assert complete.json()["state"] == "received"
    assert _part_file(settings).read_bytes() == b"abcde"
    with sqlite3.connect(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM logical_media").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM media_metadata").fetchone() == (0,)


def test_complete_notifies_validation_after_received_response(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    coordinator = _RecordingCoordinator()
    try:
        transport = UploadTransportService(
            SqliteUploadSessionRepository(engine),
            FilesystemQuarantineStorage(settings.upload_quarantine_root),  # type: ignore[arg-type]
            _EmptyLibraryRepository(),
            UploadTransportLimits(16, 8, 60, 0),
            quarantine_root=settings.upload_quarantine_root,
            preview_cache_root=tmp_path / "preview-cache",
        )
        app = create_app(
            settings=settings,
            upload_api_dependencies=UploadApiDependencies(
                transport=transport,
                validation_coordinator=coordinator,
            ),
        )
        client = TestClient(_identified(app))
        created = _create(client).json()
        assert _patch(client, created["id"], 0, b"abcde").status_code == 200

        complete = client.post(f"/api/uploads/{created['id']}/complete")
    finally:
        dispose_engine(engine)

    assert complete.status_code == 200
    assert complete.json()["state"] == "received"
    assert coordinator.notifications == 1


def test_unsuccessful_complete_does_not_notify_validation(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    coordinator = _RecordingCoordinator()
    try:
        transport = UploadTransportService(
            SqliteUploadSessionRepository(engine),
            FilesystemQuarantineStorage(settings.upload_quarantine_root),  # type: ignore[arg-type]
            _EmptyLibraryRepository(),
            UploadTransportLimits(16, 8, 60, 0),
            quarantine_root=settings.upload_quarantine_root,
            preview_cache_root=tmp_path / "preview-cache",
        )
        app = create_app(
            settings=settings,
            upload_api_dependencies=UploadApiDependencies(
                transport=transport,
                validation_coordinator=coordinator,
            ),
        )
        client = TestClient(_identified(app))
        created = _create(client).json()

        complete = client.post(f"/api/uploads/{created['id']}/complete")
    finally:
        dispose_engine(engine)

    assert complete.status_code == 409
    assert coordinator.notifications == 0


def test_complete_response_survives_validation_notification_failure(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        transport = UploadTransportService(
            SqliteUploadSessionRepository(engine),
            FilesystemQuarantineStorage(settings.upload_quarantine_root),  # type: ignore[arg-type]
            _EmptyLibraryRepository(),
            UploadTransportLimits(16, 8, 60, 0),
            quarantine_root=settings.upload_quarantine_root,
            preview_cache_root=tmp_path / "preview-cache",
        )
        app = create_app(
            settings=settings,
            upload_api_dependencies=UploadApiDependencies(
                transport=transport,
                validation_coordinator=_FailingCoordinator(),
            ),
        )
        client = TestClient(_identified(app))
        created = _create(client).json()
        assert _patch(client, created["id"], 0, b"abcde").status_code == 200

        complete = client.post(f"/api/uploads/{created['id']}/complete")
        status = client.get(f"/api/uploads/{created['id']}")
    finally:
        dispose_engine(engine)

    assert complete.status_code == 200
    assert complete.json()["state"] == "received"
    assert status.json()["state"] == "received"


def test_patch_rejects_header_errors_size_limits_wrong_state_and_offset_conflict(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    upload_id = _create(client).json()["id"]

    wrong_content_type = client.patch(
        f"/api/uploads/{upload_id}",
        content=b"a",
        headers={"content-type": "application/octet-stream", "upload-offset": "0"},
    )
    missing_offset = client.patch(
        f"/api/uploads/{upload_id}",
        content=b"a",
        headers={"content-type": "application/offset+octet-stream"},
    )
    chunk_too_large = _patch(client, upload_id, 0, b"abcdefghi")
    first = _patch(client, upload_id, 0, b"ab")
    conflict = _patch(client, upload_id, 0, b"c")

    assert wrong_content_type.status_code == 400
    assert missing_offset.status_code == 400
    assert chunk_too_large.status_code == 413
    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json()["error"]["current_offset"] == 2


def test_patch_content_length_accepts_ascii_digits_and_leading_zero(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)

    plain_upload_id = _create(client, size=1).json()["id"]
    plain = _patch_with_content_length(client, plain_upload_id, content_length="1")
    leading_zero_upload_id = _create(client, size=1).json()["id"]
    leading_zero = _patch_with_content_length(
        client,
        leading_zero_upload_id,
        content_length="01",
    )

    assert plain.status_code == 200
    assert plain.json()["received_size_bytes"] == 1
    assert leading_zero.status_code == 200
    assert leading_zero.json()["received_size_bytes"] == 1


def test_patch_content_length_rejects_signed_whitespace_mixed_and_zero_values(
    tmp_path: Path,
) -> None:
    for value in ("+1", "-1", " 1", "1 ", "1x", "0", ""):
        settings = _settings(tmp_path / value.encode("utf-8").hex())
        client = _migrated_client(settings)
        upload_id = _create(client, size=1).json()["id"]

        response = _patch_with_content_length(client, upload_id, content_length=value)

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_UPLOAD_CONTENT_LENGTH"


def test_patch_content_length_rejects_non_ascii_decimal_text() -> None:
    request = _raw_request([(b"content-length", "١".encode("utf-8"))])

    response = _parse_content_length(request)

    assert not isinstance(response, int)
    assert response.status_code == 400


def test_patch_raw_content_type_single_valid_header_is_accepted_and_advances_offset(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    upload_id = _create(TestClient(_identified(app)), size=2).json()["id"]

    status, payload = _raw_patch(
        app,
        upload_id,
        _raw_patch_headers(
            content_type_headers=[(b"content-type", b"application/offset+octet-stream")]
        ),
    )

    assert status == 200
    assert payload["received_size_bytes"] == 1
    assert _part_file(settings).read_bytes() == b"a"


def test_patch_raw_content_type_rejects_ambiguous_or_invalid_values(
    tmp_path: Path,
) -> None:
    cases = (
        [
            (b"content-type", b"application/offset+octet-stream"),
            (b"content-type", b"application/offset+octet-stream"),
        ],
        [
            (b"content-type", b"application/offset+octet-stream"),
            (b"content-type", b"application/octet-stream"),
        ],
        [
            (b"content-type", b"application/octet-stream"),
            (b"content-type", b"application/offset+octet-stream"),
        ],
        [
            (b"Content-Type", b"application/offset+octet-stream"),
            (b"content-type", b"application/offset+octet-stream"),
        ],
        [(b"content-type", b"application/offset+octet-stream, application/octet-stream")],
        [
            (
                b"content-type",
                b"application/offset+octet-stream,application/offset+octet-stream",
            )
        ],
        [],
        [(b"content-type", b"application/octet-stream")],
        [(b"content-type", b"")],
        [(b"content-type", b"application/offset+octet-stream\xff")],
    )

    for index, content_type_headers in enumerate(cases):
        settings = _settings(tmp_path / str(index))
        upgrade_database_to_head(settings)
        app = create_app(settings=settings)
        upload_id = _create(TestClient(_identified(app)), size=1).json()["id"]

        status, payload = _raw_patch(
            app,
            upload_id,
            _raw_patch_headers(content_type_headers=content_type_headers),
        )

        assert status == 400
        assert payload == {
            "error": {
                "code": "INVALID_UPLOAD_CONTENT_TYPE",
                "message": "Invalid upload content type.",
            }
        }


def test_patch_raw_content_length_and_upload_offset_singleton_contract_remains(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    client = TestClient(_identified(app))

    duplicate_content_length_id = _create(client, size=1).json()["id"]
    duplicate_content_length = _raw_patch(
        app,
        duplicate_content_length_id,
        _raw_patch_headers(
            content_type_headers=[(b"content-type", b"application/offset+octet-stream")],
            content_length_headers=[
                (b"content-length", b"1"),
                (b"content-length", b"1"),
            ],
        ),
    )
    duplicate_upload_offset_id = _create(client, size=1).json()["id"]
    duplicate_upload_offset = _raw_patch(
        app,
        duplicate_upload_offset_id,
        _raw_patch_headers(
            content_type_headers=[(b"content-type", b"application/offset+octet-stream")],
            upload_offset_headers=[
                (b"upload-offset", b"0"),
                (b"upload-offset", b"0"),
            ],
        ),
    )

    assert duplicate_content_length[0] == 400
    assert duplicate_content_length[1]["error"]["code"] == "INVALID_UPLOAD_CONTENT_LENGTH"
    assert duplicate_upload_offset[0] == 400
    assert duplicate_upload_offset[1]["error"]["code"] == "INVALID_UPLOAD_OFFSET"


def test_patch_content_length_raw_singleton_accepts_digits_and_leading_zero() -> None:
    plain = _parse_content_length(_raw_request([(b"Content-Length", b"1")]))
    leading_zero = _parse_content_length(_raw_request([(b"content-length", b"001")]))

    assert plain == 1
    assert leading_zero == 1


def test_patch_content_length_raw_duplicates_and_combined_values_are_rejected() -> None:
    cases = (
        [(b"content-length", b"1"), (b"content-length", b"+1")],
        [(b"content-length", b"+1"), (b"content-length", b"1")],
        [(b"Content-Length", b"1"), (b"content-length", b"1")],
        [(b"content-length", b"1,1")],
        [(b"content-length", b"1, 1")],
        [(b"content-length", b"1,+1")],
    )

    for headers in cases:
        response = _parse_content_length(_raw_request(headers))

        assert not isinstance(response, int)
        assert response.status_code == 400
        assert response.body == (
            b'{"error":{"code":"INVALID_UPLOAD_CONTENT_LENGTH",'
            b'"message":"Missing or invalid upload content length."}}'
        )


def test_patch_content_length_raw_rejects_signed_whitespace_mixed_zero_and_missing() -> None:
    cases = (
        [(b"content-length", b"+1")],
        [(b"content-length", b"-1")],
        [(b"content-length", b" 1")],
        [(b"content-length", b"1 ")],
        [(b"content-length", b"1x")],
        [(b"content-length", "١".encode("utf-8"))],
        [(b"content-length", b"0")],
        [],
    )

    for headers in cases:
        response = _parse_content_length(_raw_request(headers))

        assert not isinstance(response, int)
        assert response.status_code == 400


def test_patch_upload_offset_raw_duplicates_are_rejected() -> None:
    valid = _parse_upload_offset(_raw_request([(b"Upload-Offset", b"0")]))
    duplicate_same = _parse_upload_offset(
        _raw_request([(b"upload-offset", b"0"), (b"upload-offset", b"0")])
    )
    duplicate_mixed = _parse_upload_offset(
        _raw_request([(b"upload-offset", b"0"), (b"upload-offset", b"1")])
    )
    duplicate_malformed = _parse_upload_offset(
        _raw_request([(b"upload-offset", b"0"), (b"upload-offset", b"+0")])
    )

    assert valid == 0
    for response in (duplicate_same, duplicate_mixed, duplicate_malformed):
        assert not isinstance(response, int)
        assert response.status_code == 400


def test_file_ahead_is_truncated_and_file_behind_fails_closed(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    upload_id = _create(client).json()["id"]
    assert _patch(client, upload_id, 0, b"ab").status_code == 200
    part = _part_file(settings)
    part.write_bytes(b"abCRASH")

    resumed = _patch(client, upload_id, 2, b"c")
    assert resumed.status_code == 200
    assert part.read_bytes() == b"abc"

    part.write_bytes(b"a")
    failed = client.post(f"/api/uploads/{upload_id}/complete")
    status = client.get(f"/api/uploads/{upload_id}")
    assert failed.status_code == 409
    assert failed.json()["error"]["code"] == "QUARANTINE_STATE_INCONSISTENT"
    assert status.json()["state"] == "failed"


def test_cancel_persists_cancelled_removes_file_and_is_idempotent(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    upload_id = _create(client).json()["id"]
    assert _patch(client, upload_id, 0, b"abcde").status_code == 200

    cancelled = client.delete(f"/api/uploads/{upload_id}")
    repeated = client.delete(f"/api/uploads/{upload_id}")
    later_write = _patch(client, upload_id, 5, b"x")

    assert cancelled.status_code == 200
    assert cancelled.json()["state"] == "cancelled"
    assert repeated.status_code == 200
    assert later_write.status_code == 409
    assert list(settings.upload_quarantine_root.glob("*.part")) == []  # type: ignore[union-attr]


def test_duplicate_resolution_keep_is_idempotent_sanitized_and_origin_guarded(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    canonical, duplicate = _create_validated_duplicate_pair(client, settings)
    endpoint = f"/api/uploads/{duplicate.id.to_string()}/duplicate-resolution"

    duplicate_status = client.get(f"/api/uploads/{duplicate.id.to_string()}")
    kept = client.post(
        endpoint,
        json={"resolution": "keep_separate"},
        headers={"origin": "http://testserver"},
    )
    repeated = client.post(endpoint, json={"resolution": "keep_separate"})
    conflicting = client.post(endpoint, json={"resolution": "discard"})
    canonical_status = client.get(f"/api/uploads/{canonical.id.to_string()}")

    assert duplicate_status.status_code == 200
    assert duplicate_status.json()["state"] == "duplicate_pending"
    assert kept.status_code == 200
    assert kept.json()["state"] == "publish_pending"
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "publish_pending"
    assert conflicting.status_code == 409
    assert conflicting.json()["error"]["code"] == "UPLOAD_SESSION_STATE_CONFLICT"
    assert canonical_status.json()["state"] == "publish_pending"
    assert set(kept.json()) == {
        "id",
        "state",
        "declared_size_bytes",
        "received_size_bytes",
        "expires_at",
        "failure_code",
    }
    forbidden_values = (
        canonical.id.to_string(),
        canonical.storage_key.value,
        duplicate.storage_key.value,
        canonical.byte_identity_id.to_string(),
        "9" * 64,
        "example.gif",
        str(settings.upload_quarantine_root),
        str(settings.database_path),
    )
    for forbidden in forbidden_values:
        assert forbidden not in kept.text


def test_duplicate_resolution_discard_removes_only_duplicate_and_is_idempotent(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    canonical, duplicate = _create_validated_duplicate_pair(client, settings)
    endpoint = f"/api/uploads/{duplicate.id.to_string()}/duplicate-resolution"
    canonical_path = settings.upload_quarantine_root / f"{canonical.storage_key.value}.part"  # type: ignore[operator]
    duplicate_path = settings.upload_quarantine_root / f"{duplicate.storage_key.value}.part"  # type: ignore[operator]

    discarded = client.post(endpoint, json={"resolution": "discard"})
    repeated = client.post(endpoint, json={"resolution": "discard"})
    conflicting = client.post(endpoint, json={"resolution": "keep_separate"})

    assert discarded.status_code == 200
    assert discarded.json()["state"] == "cancelled"
    assert repeated.status_code == 200
    assert repeated.json()["state"] == "cancelled"
    assert conflicting.status_code == 409
    assert canonical_path.read_bytes() == b"abcde"
    assert not duplicate_path.exists()
    assert client.get(f"/api/uploads/{canonical.id.to_string()}").json()["state"] == (
        "publish_pending"
    )


def test_duplicate_resolution_rejects_invalid_state_body_identity_and_origin(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    client = _migrated_client(settings)
    canonical, duplicate = _create_validated_duplicate_pair(client, settings)
    duplicate_endpoint = f"/api/uploads/{duplicate.id.to_string()}/duplicate-resolution"
    canonical_endpoint = f"/api/uploads/{canonical.id.to_string()}/duplicate-resolution"

    unknown_resolution = client.post(duplicate_endpoint, json={"resolution": "merge"})
    missing_resolution = client.post(duplicate_endpoint, json={})
    extra_field = client.post(
        duplicate_endpoint,
        json={"resolution": "discard", "target": canonical.id.to_string()},
    )
    wrong_state = client.post(canonical_endpoint, json={"resolution": "discard"})
    canonical_keep = client.post(
        canonical_endpoint,
        json={"resolution": "keep_separate"},
    )
    unknown_identity = client.post(
        "/api/uploads/33333333-3333-4333-8333-333333333333/duplicate-resolution",
        json={"resolution": "discard"},
    )
    cross_origin = client.post(
        duplicate_endpoint,
        json={"resolution": "discard"},
        headers={"origin": "http://evil.example"},
    )

    assert unknown_resolution.status_code == 422
    assert missing_resolution.status_code == 422
    assert extra_field.status_code == 422
    assert wrong_state.status_code == 409
    assert wrong_state.json()["error"]["code"] == "UPLOAD_SESSION_STATE_CONFLICT"
    assert canonical_keep.status_code == 409
    assert canonical_keep.json() == {
        "error": {
            "code": "UPLOAD_SESSION_STATE_CONFLICT",
            "message": "Upload session state conflict.",
        }
    }
    assert unknown_identity.status_code == 404
    assert unknown_identity.json()["error"]["code"] == "UPLOAD_SESSION_NOT_FOUND"
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error"]["code"] == "UPLOAD_ORIGIN_FORBIDDEN"
    assert client.get(f"/api/uploads/{duplicate.id.to_string()}").json()["state"] == (
        "duplicate_pending"
    )
    engine = create_sqlite_engine(settings.database_path)
    try:
        canonical_after = SqliteUploadSessionRepository(engine).get(canonical.id)
    finally:
        dispose_engine(engine)
    assert canonical_after is not None
    assert canonical_after.state is UploadSessionState.PUBLISH_PENDING
    assert canonical_after.version == canonical.version
    assert canonical_after.duplicate_disposition is None
    assert settings.upload_quarantine_root.joinpath(  # type: ignore[union-attr]
        f"{canonical.storage_key.value}.part"
    ).read_bytes() == b"abcde"


def test_status_media_id_absent_until_cataloged_and_gates_published_disclosure(
    tmp_path: Path,
) -> None:
    destination_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    upload_id = "11111111-1111-4111-8111-111111111111"
    identity_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        upload_quarantine_root=tmp_path / "quarantine",
        upload_max_total_bytes=16,
        upload_max_patch_bytes=8,
        upload_session_ttl_seconds=60,
        upload_min_free_space_reserve_bytes=0,
        upload_publication_library_id=destination_id,
        _env_file=None,
    )
    settings.upload_quarantine_root.mkdir(parents=True)  # type: ignore[union-attr]
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                insert(devices).values(
                    id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    display_name="Synthetic device",
                )
            )
            connection.execute(
                insert(libraries).values(
                    id=destination_id,
                    device_id="dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                    display_name="Published originals",
                    path_flavor="posix",
                    root_path=str(tmp_path / "published"),
                )
            )
            connection.execute(
                insert(media_byte_identities).values(
                    id=identity_id,
                    checksum_algorithm="sha256",
                    size_bytes=8,
                    checksum_hex="a" * 64,
                    created_at_ms=10,
                )
            )
        uploads = SqliteUploadSessionRepository(engine)
        publications = SqliteUploadPublicationRepository(engine)
        uploads.create(
            UploadSession(
                id=UploadSessionId.from_string(upload_id),
                state=UploadSessionState.PUBLISH_PENDING,
                storage_key=UploadStorageKey(upload_id.replace("-", "")),
                display_filename=UploadDisplayFilename("synthetic.mp4"),
                declared_size_bytes=8,
                received_size_bytes=8,
                checksum_algorithm="sha256",
                checksum_hex="a" * 64,
                validated_media_kind=UploadValidatedMediaKind.VIDEO,
                validated_format=UploadValidatedFormat.MP4,
                byte_identity_id=MediaByteIdentityId.from_string(identity_id),
                duplicate_disposition=None,
                created_at_ms=10,
                updated_at_ms=20,
                expires_at_ms=100,
                failure_code=None,
                version=4,
            )
        )
        upload = uploads.get(UploadSessionId.from_string(upload_id))
        assert upload is not None
        reservation = new_upload_publication_reservation(
            upload,
            destination_id=LibraryId.from_string(destination_id),
            now_ms=21,
        )
        reserved = publications.get_or_create_reservation(
            reservation,
            expected_upload_version=upload.version,
        )
        assert reserved.publication is not None
        published = publications.commit_verified_publication(
            upload.id,
            publication_id=reserved.publication.publication_id,
            expected_upload_version=upload.version,
            expected_publication_version=reserved.publication.version,
            updated_at_ms=30,
        )
        assert published.publication is not None
        completed = publications.mark_cleanup_complete(
            upload.id,
            publication_id=published.publication.publication_id,
            expected_publication_version=published.publication.version,
            updated_at_ms=31,
        )
        assert completed.upload.state is UploadSessionState.PUBLISHED
        assert completed.publication is not None
        assert completed.publication.media_id is None

        transport = UploadTransportService(
            uploads,
            FilesystemQuarantineStorage(settings.upload_quarantine_root),  # type: ignore[arg-type]
            _EmptyLibraryRepository(),
            UploadTransportLimits(16, 8, 60, 0),
            quarantine_root=settings.upload_quarantine_root,
            preview_cache_root=tmp_path / "preview-cache",
        )
        dependencies = UploadApiDependencies(
            transport=transport,
            publication_repository=publications,
        )
        app = create_app(settings=settings, upload_api_dependencies=dependencies)
        client = TestClient(_identified(app))

        published_status = client.get(f"/api/uploads/{upload_id}")
        assert published_status.status_code == 200
        published_payload = published_status.json()
        assert published_payload["state"] == "published"
        assert published_payload["media_id"] is None
        assert set(published_payload) == {
            "id",
            "state",
            "display_filename",
            "declared_size_bytes",
            "received_size_bytes",
            "expires_at",
            "failure_code",
            "media_id",
        }
        assert "checksum" not in published_payload
        assert "publication_id" not in published_payload
        assert "relative_target" not in published_payload
        assert "cleanup_state" not in published_payload
        assert "storage_key" not in published_payload
        assert str(tmp_path / "published") not in published_status.text
        assert str(settings.database_path) not in published_status.text

        cataloged = CatalogPublishedUpload(publications, now_ms=lambda: 40)
        catalog_result = cataloged.catalog_owned_blocking(
            UploadSessionId.from_string(upload_id)
        )
        assert catalog_result.state == "cataloged"
        assert catalog_result.media_id is not None

        cataloged_status = client.get(f"/api/uploads/{upload_id}")
        assert cataloged_status.status_code == 200
        cataloged_payload = cataloged_status.json()
        assert cataloged_payload["state"] == "cataloged"
        assert cataloged_payload["media_id"] == catalog_result.media_id
        assert set(cataloged_payload) == set(published_payload)
        assert "checksum" not in cataloged_payload
        assert "publication_id" not in cataloged_payload
        assert str(tmp_path / "published") not in cataloged_status.text

        linked = publications.get_candidate(UploadSessionId.from_string(upload_id))
        assert linked is not None and linked.publication is not None
        assert linked.publication.media_id is not None
        assert linked.publication.media_location_id is not None
        with engine.begin() as connection:
            connection.execute(
                text("UPDATE upload_sessions SET state = 'published' WHERE id = :id"),
                {"id": upload_id},
            )

        contradictory = client.get(f"/api/uploads/{upload_id}")
        assert contradictory.status_code == 200
        contradictory_payload = contradictory.json()
        assert contradictory_payload["state"] == "published"
        assert contradictory_payload["media_id"] is None
        assert (
            _catalog_media_id(
                dependencies,
                UploadSessionSnapshot(
                    id=upload_id,
                    state="published",
                    display_filename="synthetic.mp4",
                    declared_size_bytes=8,
                    received_size_bytes=8,
                    expires_at=100,
                    failure_code=None,
                ),
            )
            is None
        )
    finally:
        dispose_engine(engine)


def test_quarantine_root_overlapping_registered_library_rejects_create(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    upgrade_database_to_head(settings)
    library_root = settings.upload_quarantine_root.parent  # type: ignore[union-attr]
    engine = create_sqlite_engine(settings.database_path)
    try:
        device = Device(id=DeviceId.new(), display_name="Device")
        SqliteDeviceRepository(engine).add(device)
        SqliteLibraryRepository(engine).add(
            Library(
                id=LibraryId.new(),
                device_id=device.id,
                display_name="Library",
                root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(library_root)),
            )
        )
    finally:
        dispose_engine(engine)
    client = TestClient(_identified(create_app(settings=settings)))

    response = _create(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "UPLOAD_CAPABILITY_NOT_CONFIGURED"
    assert str(library_root) not in response.text
