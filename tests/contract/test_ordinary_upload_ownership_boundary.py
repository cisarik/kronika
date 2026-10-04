"""Contract tests for ordinary upload ownership and duplicate privacy."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.configuration import KronikaSettings
from kronika.domain.uploads import (
    UploadDuplicateDisposition,
    UploadDuplicateResolutionMode,
    UploadSessionId,
    UploadSessionState,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    uses_explicit_duplicate_resolution,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.upload_session_repository import (
    SqliteUploadSessionRepository,
)

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"
USER_A_LOGIN = "usera@example.com"
USER_B_LOGIN = "userb@example.com"


def _serve_headers(login: str) -> dict[str, str]:
    return {
        "Tailscale-User-Login": login,
        "Tailscale-User-Name": login.split("@", 1)[0],
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
    }


def _mutation_headers(login: str) -> dict[str, str]:
    return {
        **_serve_headers(login),
        "Origin": EXTERNAL_ORIGIN,
        "X-FrameNest-Request": "1",
    }


@pytest.fixture
def upload_tailscale_client(tmp_path: Path):
    quarantine = tmp_path / "quarantine"
    quarantine.mkdir()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        upload_quarantine_root=quarantine,
        upload_max_total_bytes=64,
        upload_max_patch_bytes=32,
        upload_session_ttl_seconds=120,
        upload_min_free_space_reserve_bytes=0,
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={
            ADMIN_LOGIN: "admin",
            USER_A_LOGIN: "user",
            USER_B_LOGIN: "user",
        },
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client, settings


def _create(client: TestClient, login: str, *, size: int = 5) -> dict:
    response = client.post(
        "/api/uploads",
        headers=_mutation_headers(login),
        json={"display_filename": "clip.mp4", "declared_size_bytes": size},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _patch(client: TestClient, login: str, upload_id: str, offset: int, payload: bytes):
    return client.patch(
        f"/api/uploads/{upload_id}",
        content=payload,
        headers={
            **_mutation_headers(login),
            "content-type": "application/offset+octet-stream",
            "upload-offset": str(offset),
        },
    )


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def test_uses_explicit_duplicate_resolution_only_for_proven_explicit() -> None:
    assert uses_explicit_duplicate_resolution(UploadDuplicateResolutionMode.EXPLICIT)
    assert uses_explicit_duplicate_resolution("explicit")
    assert not uses_explicit_duplicate_resolution(
        UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    )
    assert not uses_explicit_duplicate_resolution("silent_keep_separate")
    assert not uses_explicit_duplicate_resolution(None)
    assert not uses_explicit_duplicate_resolution("not_a_mode")
    assert not uses_explicit_duplicate_resolution("")


def test_creation_persists_owner_and_duplicate_mode(upload_tailscale_client) -> None:
    client, settings = upload_tailscale_client
    ordinary = _create(client, USER_A_LOGIN)
    admin = _create(client, ADMIN_LOGIN)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    try:
        ordinary_session = repository.get(UploadSessionId.from_string(ordinary["id"]))
        admin_session = repository.get(UploadSessionId.from_string(admin["id"]))
    finally:
        dispose_engine(engine)
    assert ordinary_session is not None
    assert ordinary_session.created_by_login_key == USER_A_LOGIN
    assert (
        ordinary_session.duplicate_resolution_mode
        is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    )
    assert admin_session is not None
    assert admin_session.created_by_login_key == ADMIN_LOGIN
    assert admin_session.duplicate_resolution_mode is UploadDuplicateResolutionMode.EXPLICIT


@pytest.mark.parametrize(
    "method,path_builder,body",
    [
        ("GET", lambda upload_id: f"/api/uploads/{upload_id}", None),
        ("PATCH", lambda upload_id: f"/api/uploads/{upload_id}", b"x"),
        ("POST", lambda upload_id: f"/api/uploads/{upload_id}/complete", None),
        (
            "POST",
            lambda upload_id: f"/api/uploads/{upload_id}/duplicate-resolution",
            {"resolution": "keep_separate"},
        ),
        ("DELETE", lambda upload_id: f"/api/uploads/{upload_id}", None),
    ],
)
def test_foreign_ordinary_user_session_access_is_indistinguishable_not_found(
    upload_tailscale_client, method, path_builder, body
) -> None:
    client, _ = upload_tailscale_client
    created = _create(client, USER_A_LOGIN)
    upload_id = created["id"]
    missing_id = str(uuid.uuid4())
    path = path_builder(upload_id)
    missing_path = path_builder(missing_id)
    headers = _mutation_headers(USER_B_LOGIN)
    if method == "GET":
        foreign = client.get(path, headers=_serve_headers(USER_B_LOGIN))
        missing = client.get(missing_path, headers=_serve_headers(USER_B_LOGIN))
    elif method == "PATCH":
        foreign = client.patch(
            path,
            content=body,
            headers={
                **headers,
                "content-type": "application/offset+octet-stream",
                "upload-offset": "0",
            },
        )
        missing = client.patch(
            missing_path,
            content=body,
            headers={
                **headers,
                "content-type": "application/offset+octet-stream",
                "upload-offset": "0",
            },
        )
    elif method == "DELETE":
        foreign = client.delete(path, headers=headers)
        missing = client.delete(missing_path, headers=headers)
    else:
        foreign = client.post(path, headers=headers, json=body)
        missing = client.post(missing_path, headers=headers, json=body)
    assert foreign.status_code == 404
    assert missing.status_code == 404
    assert _error_code(foreign) == "UPLOAD_SESSION_NOT_FOUND"
    assert _error_code(missing) == "UPLOAD_SESSION_NOT_FOUND"
    assert foreign.json() == missing.json()


def test_owner_and_admin_can_access_session(upload_tailscale_client) -> None:
    client, _ = upload_tailscale_client
    created = _create(client, USER_A_LOGIN)
    upload_id = created["id"]
    owner = client.get(f"/api/uploads/{upload_id}", headers=_serve_headers(USER_A_LOGIN))
    admin = client.get(f"/api/uploads/{upload_id}", headers=_serve_headers(ADMIN_LOGIN))
    assert owner.status_code == 200
    assert admin.status_code == 200
    assert owner.json()["id"] == upload_id
    assert admin.json()["id"] == upload_id


def test_legacy_null_owner_is_administrator_only(upload_tailscale_client) -> None:
    client, settings = upload_tailscale_client
    created = _create(client, USER_A_LOGIN)
    upload_id = created["id"]
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE upload_sessions SET created_by_login_key = NULL WHERE id = ?",
                (upload_id,),
            )
    finally:
        dispose_engine(engine)
    ordinary = client.get(
        f"/api/uploads/{upload_id}", headers=_serve_headers(USER_A_LOGIN)
    )
    admin = client.get(f"/api/uploads/{upload_id}", headers=_serve_headers(ADMIN_LOGIN))
    assert ordinary.status_code == 404
    assert _error_code(ordinary) == "UPLOAD_SESSION_NOT_FOUND"
    assert admin.status_code == 200


def _force_validate(
    repository: SqliteUploadSessionRepository,
    engine,
    upload_id: str,
    checksum_hex: str,
):
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "UPDATE upload_sessions SET state = 'validating', "
            "received_size_bytes = declared_size_bytes, version = version + 1, "
            "updated_at_ms = updated_at_ms + 1 WHERE id = ?",
            (upload_id,),
        )
    session = repository.get(UploadSessionId.from_string(upload_id))
    assert session is not None
    return repository.complete_validation_success(
        session.id,
        expected_version=session.version,
        checksum_hex=checksum_hex,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        updated_at_ms=session.updated_at_ms + 1,
    )


def _resolve(client: TestClient, login: str, upload_id: str, resolution: str):
    return client.post(
        f"/api/uploads/{upload_id}/duplicate-resolution",
        headers=_mutation_headers(login),
        json={"resolution": resolution},
    )


def _assert_constant_privacy_conflict(response) -> dict:
    assert response.status_code == 409
    body = response.json()
    assert body == {
        "error": {
            "code": "UPLOAD_SESSION_STATE_CONFLICT",
            "message": "Upload session state conflict.",
        }
    }
    lowered = response.text.lower()
    for needle in (
        "duplicate",
        "matching",
        "owner",
        "media_id",
        "publish",
        "removal",
        "keep_separate",
        "discard",
        "silent",
    ):
        assert needle not in lowered
    return body


def test_ordinary_duplicate_match_is_silent_and_indistinguishable(
    upload_tailscale_client,
) -> None:
    client, settings = upload_tailscale_client
    first = _create(client, USER_A_LOGIN, size=5)
    second = _create(client, USER_B_LOGIN, size=5)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    try:
        for upload, owner in ((first, USER_A_LOGIN), (second, USER_B_LOGIN)):
            session_id = UploadSessionId.from_string(upload["id"])
            session = repository.get(session_id)
            assert session is not None
            assert session.created_by_login_key == owner
            assert (
                session.duplicate_resolution_mode
                is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
            )
        first_completed = _force_validate(repository, engine, first["id"], "a" * 64)
        second_completed = _force_validate(repository, engine, second["id"], "a" * 64)
        assert first_completed.state is UploadSessionState.PUBLISH_PENDING
        assert first_completed.duplicate_disposition is None
        assert second_completed.state is UploadSessionState.PUBLISH_PENDING
        assert (
            second_completed.duplicate_disposition
            is UploadDuplicateDisposition.KEEP_SEPARATE
        )
        for upload, login in ((first, USER_A_LOGIN), (second, USER_B_LOGIN)):
            status = client.get(
                f"/api/uploads/{upload['id']}", headers=_serve_headers(login)
            )
            payload = status.json()
            assert status.status_code == 200
            assert payload["state"] == "publish_pending"
            assert payload["state"] != "duplicate_pending"
            assert "duplicate" not in status.text.lower()
            assert "matching" not in status.text.lower()
            assert payload.get("media_id") in (None, "")
        unique_keep = _resolve(client, USER_A_LOGIN, first["id"], "keep_separate")
        match_keep = _resolve(client, USER_B_LOGIN, second["id"], "keep_separate")
        unique_body = _assert_constant_privacy_conflict(unique_keep)
        match_body = _assert_constant_privacy_conflict(match_keep)
        assert unique_body == match_body
    finally:
        dispose_engine(engine)


def test_privacy_safe_duplicate_resolution_is_equivalent_and_side_effect_free(
    upload_tailscale_client,
) -> None:
    client, settings = upload_tailscale_client
    unique = _create(client, USER_A_LOGIN, size=5)
    protected_owner = _create(client, ADMIN_LOGIN, size=5)
    protected = _create(client, USER_A_LOGIN, size=5)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    try:
        unique_done = _force_validate(repository, engine, unique["id"], "c" * 64)
        admin_done = _force_validate(
            repository, engine, protected_owner["id"], "d" * 64
        )
        protected_done = _force_validate(repository, engine, protected["id"], "d" * 64)
        assert unique_done.duplicate_disposition is None
        assert admin_done.state is UploadSessionState.PUBLISH_PENDING
        assert (
            protected_done.duplicate_disposition
            is UploadDuplicateDisposition.KEEP_SEPARATE
        )
        assert protected_done.state is UploadSessionState.PUBLISH_PENDING

        # Persist-mode gate: reload before invoking the endpoint.
        reloaded_unique = repository.get(UploadSessionId.from_string(unique["id"]))
        reloaded_protected = repository.get(
            UploadSessionId.from_string(protected["id"])
        )
        assert reloaded_unique is not None and reloaded_protected is not None
        assert (
            reloaded_unique.duplicate_resolution_mode
            is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
        )
        assert (
            reloaded_protected.duplicate_resolution_mode
            is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
        )

        before_logical = _count(engine, "logical_media")
        before_upload_pubs = _count(engine, "upload_publications")
        before_content_pubs = _count(engine, "media_content_publications")
        before_analysis = _count(engine, "media_analysis_runs")

        keep_bodies = []
        discard_bodies = []
        for upload_id in (unique["id"], protected["id"]):
            for _ in range(2):
                keep = _resolve(client, USER_A_LOGIN, upload_id, "keep_separate")
                keep_bodies.append(_assert_constant_privacy_conflict(keep))
                discard = _resolve(client, USER_A_LOGIN, upload_id, "discard")
                discard_bodies.append(_assert_constant_privacy_conflict(discard))

        assert len({json.dumps(body, sort_keys=True) for body in keep_bodies}) == 1
        assert len({json.dumps(body, sort_keys=True) for body in discard_bodies}) == 1
        assert keep_bodies[0] == discard_bodies[0]

        for upload_id, expected in (
            (unique["id"], unique_done),
            (protected["id"], protected_done),
        ):
            after = repository.get(UploadSessionId.from_string(upload_id))
            assert after is not None
            assert after.state is expected.state
            assert after.duplicate_disposition is expected.duplicate_disposition
            assert after.version == expected.version
            assert (
                after.duplicate_resolution_mode
                is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
            )

        assert _count(engine, "logical_media") == before_logical
        assert _count(engine, "upload_publications") == before_upload_pubs
        assert _count(engine, "media_content_publications") == before_content_pubs
        assert _count(engine, "media_analysis_runs") == before_analysis

        foreign_keep = _resolve(client, USER_B_LOGIN, unique["id"], "keep_separate")
        foreign_discard = _resolve(client, USER_B_LOGIN, unique["id"], "discard")
        for response in (foreign_keep, foreign_discard):
            assert response.status_code == 404
            assert _error_code(response) == "UPLOAD_SESSION_NOT_FOUND"
            assert response.json() == {
                "error": {
                    "code": "UPLOAD_SESSION_NOT_FOUND",
                    "message": "Upload session not found.",
                }
            }
    finally:
        dispose_engine(engine)


def _count(engine, table: str) -> int:
    with engine.connect() as connection:
        return int(
            connection.exec_driver_sql(f"SELECT COUNT(*) FROM {table}").scalar_one()
        )


def test_administrator_duplicate_pending_regression(upload_tailscale_client) -> None:
    client, settings = upload_tailscale_client
    first = _create(client, ADMIN_LOGIN, size=5)
    second = _create(client, ADMIN_LOGIN, size=5)
    third = _create(client, ADMIN_LOGIN, size=5)
    fourth = _create(client, ADMIN_LOGIN, size=5)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteUploadSessionRepository(engine)
    try:
        completed = [
            _force_validate(repository, engine, first["id"], "b" * 64),
            _force_validate(repository, engine, second["id"], "b" * 64),
            _force_validate(repository, engine, third["id"], "e" * 64),
            _force_validate(repository, engine, fourth["id"], "e" * 64),
        ]
    finally:
        dispose_engine(engine)
    assert completed[0].state is UploadSessionState.PUBLISH_PENDING
    assert completed[1].state is UploadSessionState.DUPLICATE_PENDING
    assert completed[2].state is UploadSessionState.PUBLISH_PENDING
    assert completed[3].state is UploadSessionState.DUPLICATE_PENDING
    status = client.get(
        f"/api/uploads/{second['id']}", headers=_serve_headers(ADMIN_LOGIN)
    )
    assert status.json()["state"] == "duplicate_pending"
    kept = _resolve(client, ADMIN_LOGIN, second["id"], "keep_separate")
    assert kept.status_code == 200
    assert kept.json()["state"] == "publish_pending"
    # Accepted idempotent keep for explicit sessions.
    kept_again = _resolve(client, ADMIN_LOGIN, second["id"], "keep_separate")
    assert kept_again.status_code == 200
    assert kept_again.json()["state"] == "publish_pending"
    discarded = _resolve(client, ADMIN_LOGIN, fourth["id"], "discard")
    assert discarded.status_code == 200
    assert discarded.json()["state"] == "cancelled"
    discarded_again = _resolve(client, ADMIN_LOGIN, fourth["id"], "discard")
    assert discarded_again.status_code == 200
    assert discarded_again.json()["state"] == "cancelled"
