"""Contract evidence for ordinary YouTube request API."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import sqlite3

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.adapters.api.youtube_request_api import (
    YouTubeRequestApiDependencies,
    create_youtube_request_api_router,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionInfrastructureError,
    YouTubeAcquisitionInvalidCursorError,
    YouTubeAcquisitionInvalidRequestError,
    YouTubeAcquisitionNotFoundError,
    YouTubeAcquisitionStateConflictError,
    YouTubeRequestInsufficientStorageError,
    YouTubeRequestLimitError,
    YouTubeRequestLimits,
    YouTubeRequestPage,
    YouTubeRequestService,
    YouTubeRequestSnapshot,
    YouTubeRequestSubmission,
    _decode_owned_cursor,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_YOUTUBE_REQUEST,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.domain.youtube_acquisition import (
    FrameNestYouTubeUrlError,
    YouTubeAcquisitionClaim,
    YouTubeConfirmationMethod,
    canonicalize_youtube_url,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    SqliteYouTubeAcquisitionClaimRepository,
)

REQUEST_ID = "11111111-1111-4111-8111-111111111111"
VIDEO_ID = "AbCdEf123_-"
OTHER_VIDEO_ID = "BcDeFg234_a"


def _snapshot(**changes: object) -> YouTubeRequestSnapshot:
    snapshot = YouTubeRequestSnapshot(
        request_id=REQUEST_ID,
        phase="queued",
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        canonical_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        media_id=None,
        failure_category=None,
        failure_code=None,
        retry_of_request_id=None,
        created_at_ms=10,
        updated_at_ms=10,
    )
    return replace(snapshot, **changes)


def _identity(role: str = ROLE_USER) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


class _Service:
    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.created = True
        self.failure: Exception | None = None
        self.submits: list[str] = []

    def submit(self, *, submitted_url, confirmation_method, created_by_login_key):
        if self.failure is not None:
            raise self.failure
        try:
            canonicalize_youtube_url(submitted_url)
        except FrameNestYouTubeUrlError as exc:
            raise YouTubeAcquisitionInvalidRequestError("invalid") from exc
        self.submits.append(created_by_login_key)
        return YouTubeRequestSubmission(snapshot=self.snapshot, created=self.created)

    def list_owned(self, *, created_by_login_key, limit=20, cursor=None):
        if self.failure is not None:
            raise self.failure
        return YouTubeRequestPage(items=(self.snapshot,), next_cursor=None)

    def get_owned(self, claim_id, *, created_by_login_key):
        if self.failure is not None:
            raise self.failure
        if str(claim_id) != REQUEST_ID and claim_id.to_string() != REQUEST_ID:
            raise YouTubeAcquisitionNotFoundError("missing")
        return self.snapshot

    def retry(self, claim_id, *, confirmation_method, created_by_login_key):
        if self.failure is not None:
            raise self.failure
        return YouTubeRequestSubmission(snapshot=self.snapshot, created=True)


def _client(service: _Service, *, identity: IdentityContext | None) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_youtube_request_api_router(
            YouTubeRequestApiDependencies(
                service=service,
                audit_recorder=None,
                enabled=True,
            )
        )
    )

    @app.middleware("http")
    async def inject(request: Request, call_next):
        request.scope[SCOPE_REQUEST_ID] = "req-1"
        request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-1"
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        return await call_next(request)

    return TestClient(app)


def test_submit_list_detail_and_field_allowlist() -> None:
    service = _Service()
    client = _client(service, identity=_identity())
    create = client.post(
        "/api/youtube/requests",
        json={
            "url": f"https://youtu.be/{VIDEO_ID}",
            "confirmation_method": "interactive",
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert set(body) == {
        "request_id",
        "phase",
        "submitted_url",
        "canonical_url",
        "media_id",
        "failure_category",
        "failure_code",
        "retry_of_request_id",
        "created_at_ms",
        "updated_at_ms",
    }
    assert "requester_login_key" not in body
    assert "submission_result" not in body
    listed = client.get("/api/youtube/requests")
    assert listed.status_code == 200
    assert listed.json()["items"][0]["request_id"] == REQUEST_ID
    detail = client.get(f"/api/youtube/requests/{REQUEST_ID}")
    assert detail.status_code == 200


def test_foreign_or_missing_is_identical_404() -> None:
    service = _Service()
    service.failure = YouTubeAcquisitionNotFoundError("missing")
    client = _client(service, identity=_identity())
    missing = client.get(
        "/api/youtube/requests/22222222-2222-4222-8222-222222222222"
    )
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "YOUTUBE_REQUEST_NOT_FOUND"


def test_limit_storage_and_unavailable_codes() -> None:
    service = _Service()
    client = _client(service, identity=_identity())
    service.failure = YouTubeRequestLimitError(
        "YOUTUBE_REQUEST_ACTIVE_LIMIT",
        "Active YouTube request limit reached.",
    )
    response = client.post(
        "/api/youtube/requests",
        json={
            "url": f"https://youtu.be/{VIDEO_ID}",
            "confirmation_method": "interactive",
        },
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "YOUTUBE_REQUEST_ACTIVE_LIMIT"

    service.failure = YouTubeRequestInsufficientStorageError("no space")
    response = client.post(
        "/api/youtube/requests",
        json={
            "url": f"https://youtu.be/{VIDEO_ID}",
            "confirmation_method": "interactive",
        },
    )
    assert response.status_code == 507

    service.failure = YouTubeAcquisitionInfrastructureError("unavailable")
    response = client.post(
        "/api/youtube/requests",
        json={
            "url": f"https://youtu.be/{VIDEO_ID}",
            "confirmation_method": "interactive",
        },
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "YOUTUBE_REQUEST_UNAVAILABLE"


def test_invalid_url_is_redacted() -> None:
    service = _Service()
    client = _client(service, identity=_identity())
    response = client.post(
        "/api/youtube/requests",
        json={
            "url": "https://evil.example/watch?v=AbCdEf123_-",
            "confirmation_method": "interactive",
        },
    )
    assert response.status_code == 400
    assert "evil.example" not in response.text
    assert response.json()["error"]["code"] == "YOUTUBE_REQUEST_INVALID_URL"


def test_malformed_cursor_maps_to_invalid_request_without_raiser_text() -> None:
    service = _Service()
    hostile_cursor = "!!!not-a-cursor"
    service.failure = YouTubeAcquisitionInvalidCursorError(
        "Invalid YouTube request cursor."
    )
    client = _client(service, identity=_identity())
    response = client.get("/api/youtube/requests", params={"cursor": hostile_cursor})

    assert response.status_code == 400
    assert response.json() == {
        "error": {
            "code": "YOUTUBE_REQUEST_INVALID_REQUEST",
            "message": "Invalid YouTube request cursor.",
        }
    }
    assert hostile_cursor not in response.text


def test_decode_owned_cursor_raises_typed_invalid_cursor_error() -> None:
    with pytest.raises(YouTubeAcquisitionInvalidCursorError) as excinfo:
        _decode_owned_cursor("!!!not-a-cursor")

    assert str(excinfo.value) == "Invalid YouTube request cursor."
    assert isinstance(excinfo.value, YouTubeAcquisitionInvalidRequestError)


def test_retry_state_conflict() -> None:
    service = _Service()
    service.failure = YouTubeAcquisitionStateConflictError("conflict")
    client = _client(service, identity=_identity(ROLE_ADMIN))
    response = client.post(
        f"/api/youtube/requests/{REQUEST_ID}/retry",
        json={"confirmation_method": "interactive"},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "YOUTUBE_REQUEST_STATE_CONFLICT"
    assert CAPABILITY_YOUTUBE_REQUEST in CAPABILITIES_BY_ROLE[ROLE_USER]


class _PublicationStub:
    def is_published(self, media_id: object) -> bool:
        return False


class _StagingStub:
    def cleanup(self, staging_key: str) -> None:
        raise AssertionError("submit must not touch staging")


def _real_service_client(
    tmp_path: Path,
) -> tuple[TestClient, sqlite3.Connection, object]:
    database_path = tmp_path / "requests.sqlite3"
    upgrade_database_to_head(
        KronikaSettings(database_path=database_path, _env_file=None)
    )
    engine = create_sqlite_engine(database_path)
    repository = SqliteYouTubeAcquisitionClaimRepository(engine)
    service = YouTubeRequestService(
        repository,
        _PublicationStub(),
        _StagingStub(),
        limits=YouTubeRequestLimits(),
    )
    connection = sqlite3.connect(database_path)
    return _client(service, identity=_identity()), connection, engine


def _claim_count(connection: sqlite3.Connection) -> int:
    row = connection.execute(
        "SELECT COUNT(*) FROM youtube_acquisition_claims"
    ).fetchone()
    assert row is not None
    return int(row[0])


def test_same_requester_same_source_active_reuse_skips_admission_limits(
    tmp_path: Path,
) -> None:
    client, connection, engine = _real_service_client(tmp_path)
    try:
        first = client.post(
            "/api/youtube/requests",
            json={
                "url": f"https://youtu.be/{VIDEO_ID}",
                "confirmation_method": "interactive",
            },
        )
        assert first.status_code == 201
        request_id = first.json()["request_id"]

        second = client.post(
            "/api/youtube/requests",
            json={
                "url": f"https://www.youtube.com/watch?v={VIDEO_ID}",
                "confirmation_method": "interactive",
            },
        )
        assert second.status_code == 200
        assert second.json()["request_id"] == request_id
        assert "error" not in second.json()
        assert _claim_count(connection) == 1

        different = client.post(
            "/api/youtube/requests",
            json={
                "url": f"https://youtu.be/{OTHER_VIDEO_ID}",
                "confirmation_method": "interactive",
            },
        )
        assert different.status_code == 429
        assert different.json()["error"]["code"] == "YOUTUBE_REQUEST_ACTIVE_LIMIT"
        assert _claim_count(connection) == 1
    finally:
        connection.close()
        dispose_engine(engine)


def test_same_requester_different_source_keeps_active_limit(
    tmp_path: Path,
) -> None:
    client, connection, engine = _real_service_client(tmp_path)
    try:
        first = client.post(
            "/api/youtube/requests",
            json={
                "url": f"https://youtu.be/{VIDEO_ID}",
                "confirmation_method": "interactive",
            },
        )
        assert first.status_code == 201

        second = client.post(
            "/api/youtube/requests",
            json={
                "url": f"https://youtu.be/{OTHER_VIDEO_ID}",
                "confirmation_method": "interactive",
            },
        )
        assert second.status_code == 429
        assert second.json()["error"]["code"] == "YOUTUBE_REQUEST_ACTIVE_LIMIT"
        assert _claim_count(connection) == 1
    finally:
        connection.close()
        dispose_engine(engine)


class _RacingRepository:
    """Pre-admission lookup misses a racing claim; creation authority wins."""

    def __init__(self, winner: YouTubeAcquisitionClaim) -> None:
        self._winner = winner
        self.created_rows = 0

    def find_owned_live_successful_by_source_identity(self, **kwargs: object) -> None:
        return None

    def find_active_by_source_identity(self, **kwargs: object) -> None:
        return None

    def find_published_successful_by_source_identity(self, **kwargs: object) -> None:
        return None

    def count_active_for_requester(self, **kwargs: object) -> int:
        return 0

    def count_global_active_ordinary(self) -> int:
        return 0

    def count_submits_since(self, **kwargs: object) -> int:
        return 0

    def count_failed_transitions_since(self, **kwargs: object) -> int:
        return 0

    def private_successful_quota(self, **kwargs: object) -> tuple[int, int]:
        return (0, 0)

    def get_owned(
        self,
        claim_id: object,
        *,
        created_by_login_key: str,
    ) -> None:
        return None

    def create_or_get_active(
        self,
        claim: YouTubeAcquisitionClaim,
    ) -> tuple[YouTubeAcquisitionClaim, bool]:
        return (self._winner, False)


def test_racing_active_claim_is_returned_by_transactional_authority() -> None:
    winner = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=10,
        created_by_login_key="user@example.com",
    )
    repository = _RacingRepository(winner)
    service = YouTubeRequestService(
        repository,
        _PublicationStub(),
        _StagingStub(),
        limits=YouTubeRequestLimits(),
    )
    client = _client(service, identity=_identity())
    response = client.post(
        "/api/youtube/requests",
        json={
            "url": f"https://youtu.be/{VIDEO_ID}",
            "confirmation_method": "interactive",
        },
    )
    assert response.status_code == 200
    assert response.json()["request_id"] == winner.id.to_string()
    assert repository.created_rows == 0
