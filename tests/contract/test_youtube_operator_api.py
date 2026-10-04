"""Contract evidence for the loopback-only YouTube operator API."""

from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.adapters.api.youtube_operator_api import (
    YouTubeOperatorApiDependencies,
    create_youtube_operator_api_router,
)
from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
from tests.support.record_access import synthetic_identity
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionInfrastructureError,
    YouTubeAcquisitionInvalidRequestError,
    YouTubeClaimSnapshot,
    YouTubeClaimSubmission,
)
from kronika.domain.youtube_acquisition import (
    FrameNestYouTubeUrlError,
    canonicalize_youtube_url,
)

CLAIM_ID = "11111111-1111-4111-8111-111111111111"
VIDEO_ID = "AbCdEf123_-"


def _snapshot(**changes: object) -> YouTubeClaimSnapshot:
    snapshot = YouTubeClaimSnapshot(
        id=CLAIM_ID,
        state="claimed",
        acquisition_source="youtube_manual_claim",
        youtube_video_id=VIDEO_ID,
        upload_id=None,
        upload_state=None,
        media_id=None,
        media_location_id=None,
        result=None,
        downloaded_size_bytes=None,
        failure_stage=None,
        failure_code=None,
        cleanup_state="pending",
        retry_of_claim_id=None,
        resolved_claim_id=None,
        created_at_ms=10,
        updated_at_ms=10,
        completed_at_ms=None,
        version=0,
    )
    return replace(snapshot, **changes)


class _Service:
    def __init__(self) -> None:
        self.submissions: list[tuple[str, str, str | None]] = []
        self.retries: list[tuple[str, str, str | None]] = []
        self.gets: list[str | None] = []
        self.failure: Exception | None = None

    def submit(self, *, submitted_url, confirmation_method, created_by_login_key=None):
        if self.failure is not None:
            raise self.failure
        try:
            identity = canonicalize_youtube_url(submitted_url)
        except FrameNestYouTubeUrlError as exc:
            raise YouTubeAcquisitionInvalidRequestError(
                "raw submitted target must remain private"
            ) from exc
        assert identity.video_id == VIDEO_ID
        self.submissions.append(
            (submitted_url, confirmation_method.value, created_by_login_key)
        )
        return YouTubeClaimSubmission(_snapshot(), created=True)

    def get(self, claim_id, *, created_by_login_key=None):
        if self.failure is not None:
            raise self.failure
        assert claim_id.to_string() == CLAIM_ID
        self.gets.append(created_by_login_key)
        return _snapshot()

    def retry(self, claim_id, *, confirmation_method, created_by_login_key=None):
        if self.failure is not None:
            raise self.failure
        self.retries.append(
            (claim_id.to_string(), confirmation_method.value, created_by_login_key)
        )
        return YouTubeClaimSubmission(
            _snapshot(retry_of_claim_id=CLAIM_ID),
            created=True,
        )


def _client(
    service: object | None,
    *,
    enabled: bool = True,
    peer: str = "127.0.0.1",
    identity: str | None = "admin",
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_youtube_operator_api_router(
            YouTubeOperatorApiDependencies(
                service=service,
                enabled=enabled,
            )
        )
    )
    if identity == "admin":
        caller = synthetic_identity("ada@example.com", role=ROLE_ADMIN)
    elif identity == "user":
        caller = synthetic_identity("alice@example.com", role=ROLE_USER)
    else:
        caller = None

    if caller is not None:

        @app.middleware("http")
        async def inject_identity(request, call_next):
            request.scope[SCOPE_IDENTITY] = caller
            return await call_next(request)

    return TestClient(app, client=(peer, 50_000))


def test_loopback_create_get_and_retry_use_bounded_server_service() -> None:
    service = _Service()
    with _client(service) as client:
        created = client.post(
            "/api/operator/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"yes_flag"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        status = client.get(
            f"/api/operator/youtube/claims/{CLAIM_ID}"
        )
        retried = client.post(
            f"/api/operator/youtube/claims/{CLAIM_ID}/retry",
            content='{"confirmation_method":"interactive"}',
            headers={"Content-Type": "application/json"},
        )

    assert created.status_code == 201
    assert created.json()["id"] == CLAIM_ID
    assert created.json()["state"] == "claimed"
    assert status.status_code == 200
    assert retried.status_code == 201
    assert service.submissions == [
        ("https://youtu.be/AbCdEf123_-", "yes_flag", "ada@example.com")
    ]
    assert service.gets == ["ada@example.com"]
    assert service.retries == [(CLAIM_ID, "interactive", "ada@example.com")]


def test_loopback_without_acquisition_identity_is_denied() -> None:
    service = _Service()
    with _client(service, identity=None) as client:
        missing = client.post(
            "/api/operator/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-","confirmation_method":"yes_flag"}'
            ),
            headers={"Content-Type": "application/json"},
        )
    with _client(service, identity="user") as client:
        ordinary = client.get(f"/api/operator/youtube/claims/{CLAIM_ID}")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "IDENTITY_REQUIRED"
    assert ordinary.status_code == 403
    assert ordinary.json()["error"]["code"] == "CAPABILITY_DENIED"
    assert service.submissions == []
    assert service.gets == []


def test_non_loopback_origin_and_non_loopback_bind_are_rejected() -> None:
    service = _Service()
    with _client(service, peer="192.0.2.10") as client:
        peer_response = client.get(
            f"/api/operator/youtube/claims/{CLAIM_ID}"
        )
    with _client(service) as client:
        origin_response = client.get(
            f"/api/operator/youtube/claims/{CLAIM_ID}",
            headers={"Origin": "http://127.0.0.1:8000"},
        )
    with _client(service, enabled=False) as client:
        disabled_response = client.get(
            f"/api/operator/youtube/claims/{CLAIM_ID}"
        )

    assert peer_response.status_code == 403
    assert (
        peer_response.json()["error"]["code"]
        == "YOUTUBE_OPERATOR_LOOPBACK_REQUIRED"
    )
    assert origin_response.status_code == 403
    assert (
        origin_response.json()["error"]["code"]
        == "YOUTUBE_OPERATOR_ORIGIN_FORBIDDEN"
    )
    assert disabled_response.status_code == 503
    assert (
        disabled_response.json()["error"]["code"]
        == "YOUTUBE_OPERATOR_NOT_CONFIGURED"
    )
    assert service.submissions == []


def test_exact_json_media_type_unknown_fields_and_client_provenance_are_rejected() -> None:
    service = _Service()
    with _client(service) as client:
        charset = client.post(
            "/api/operator/youtube/claims",
            content='{"url":"x","confirmation_method":"yes_flag"}',
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        unknown = client.post(
            "/api/operator/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"yes_flag","canonical_url":"forged"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        malformed = client.post(
            "/api/operator/youtube/claims",
            content="{",
            headers={"Content-Type": "application/json"},
        )
        oversized = client.post(
            "/api/operator/youtube/claims",
            content='{"url":"' + ("x" * 5_000) + '"}',
            headers={"Content-Type": "application/json"},
        )

    assert charset.status_code == 415
    assert unknown.status_code == 400
    assert malformed.status_code == 400
    assert oversized.status_code == 413
    assert service.submissions == []


def test_invalid_url_and_internal_failure_are_sanitized() -> None:
    service = _Service()
    with _client(service) as client:
        invalid = client.post(
            "/api/operator/youtube/claims",
            content=(
                '{"url":"https://evil.example/private",'
                '"confirmation_method":"yes_flag"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        service.failure = YouTubeAcquisitionInfrastructureError(
            "raw /private/path secret upstream error"
        )
        failed = client.get(
            f"/api/operator/youtube/claims/{CLAIM_ID}"
        )

    assert invalid.status_code == 400
    assert (
        invalid.json()["error"]["code"]
        == "YOUTUBE_OPERATOR_INVALID_URL"
    )
    assert "evil.example" not in invalid.text
    assert failed.status_code == 503
    assert "/private/path" not in failed.text
    assert "secret" not in failed.text


def test_handoff_keeps_explicit_duplicates_for_upload_manage_only() -> None:
    """Administrator handoff stays explicit; an ordinary login stays silent.

    The previous handoff treated every present login as silent. That hid the
    operator administrator after claims began carrying a verified identity.
    """
    from kronika.application.youtube_acquisition import handoff_duplicate_resolution
    from kronika.domain.identity_access import (
        CAPABILITY_UPLOAD_MANAGE,
        build_identity_mapping,
        mapped_role_has_capability,
    )
    from kronika.domain.uploads import UploadDuplicateResolutionMode

    mapping = build_identity_mapping(
        {
            "ada@example.com": "admin",
            "alice@example.com": "user",
        }
    )

    def manages(login: str) -> bool:
        return mapped_role_has_capability(
            mapping,
            login,
            CAPABILITY_UPLOAD_MANAGE,
        )

    missing_login, missing_mode = handoff_duplicate_resolution(
        None,
        creator_manages_duplicates=manages,
    )
    admin_login, admin_mode = handoff_duplicate_resolution(
        "ada@example.com",
        creator_manages_duplicates=manages,
    )
    ordinary_login, ordinary_mode = handoff_duplicate_resolution(
        "alice@example.com",
        creator_manages_duplicates=manages,
    )
    unwired_login, unwired_mode = handoff_duplicate_resolution(
        "ada@example.com",
    )
    assert missing_login is None
    assert missing_mode is UploadDuplicateResolutionMode.EXPLICIT
    assert admin_login == "ada@example.com"
    assert admin_mode is UploadDuplicateResolutionMode.EXPLICIT
    assert ordinary_login == "alice@example.com"
    assert ordinary_mode is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
    assert unwired_login == "ada@example.com"
    assert unwired_mode is UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE
