"""Contract evidence for the authenticated administrator YouTube API."""

from __future__ import annotations

from dataclasses import replace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.adapters.api.youtube_browser_api import (
    YouTubeBrowserApiDependencies,
    create_youtube_browser_api_router,
)
from kronika.application.ports.content_publication_repository import (
    MediaWorkflowStatus,
)
from kronika.application.youtube_acquisition import (
    YouTubeAcquisitionInfrastructureError,
    YouTubeAcquisitionInvalidRequestError,
    YouTubeAcquisitionNotFoundError,
    YouTubeAcquisitionStateConflictError,
    YouTubeClaimSnapshot,
    YouTubeClaimSubmission,
)
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_YOUTUBE_ACQUIRE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)
from kronika.domain.youtube_acquisition import (
    FrameNestYouTubeUrlError,
    canonicalize_youtube_url,
)

CLAIM_ID = "11111111-1111-4111-8111-111111111111"
RETRY_ID = "22222222-2222-4222-8222-222222222222"
MEDIA_ID = "33333333-3333-4333-8333-333333333333"
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


def _identity(role: str, *, capabilities: frozenset[str] | None = None) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=(
            CAPABILITIES_BY_ROLE[role] if capabilities is None else capabilities
        ),
        provenance="tailscale-serve",
    )


class _Service:
    def __init__(self) -> None:
        self.snapshot = _snapshot()
        self.created = True
        self.submissions: list[tuple[str, str]] = []
        self.retries: list[tuple[str, str]] = []
        self.failure: Exception | None = None

    def submit(self, *, submitted_url, confirmation_method):
        if self.failure is not None:
            raise self.failure
        try:
            identity = canonicalize_youtube_url(submitted_url)
        except FrameNestYouTubeUrlError as exc:
            raise YouTubeAcquisitionInvalidRequestError("invalid") from exc
        assert identity.video_id == VIDEO_ID
        self.submissions.append((submitted_url, confirmation_method.value))
        return YouTubeClaimSubmission(self.snapshot, created=self.created)

    def get(self, claim_id):
        if self.failure is not None:
            raise self.failure
        if claim_id.to_string() != CLAIM_ID:
            raise YouTubeAcquisitionNotFoundError("missing")
        return self.snapshot

    def retry(self, claim_id, *, confirmation_method):
        if self.failure is not None:
            raise self.failure
        self.retries.append((claim_id.to_string(), confirmation_method.value))
        return YouTubeClaimSubmission(
            replace(self.snapshot, id=RETRY_ID, retry_of_claim_id=CLAIM_ID),
            created=self.created,
        )


class _WorkflowStatus:
    def __init__(self, status: MediaWorkflowStatus) -> None:
        self.status = status
        self.calls: list[str] = []

    def execute(self, media_id: str) -> MediaWorkflowStatus:
        self.calls.append(media_id)
        return self.status


class _AuditRecorder:
    def __init__(self, *, fail: bool = False) -> None:
        self.events = []
        self.fail = fail

    def record(self, event) -> None:
        if self.fail:
            raise RuntimeError("audit unavailable")
        self.events.append(event)


def _client(
    service: _Service,
    *,
    identity: IdentityContext | None,
    audit_proof: bool = True,
    workflow: _WorkflowStatus | None = None,
    audit: _AuditRecorder | None = None,
) -> TestClient:
    app = FastAPI()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        request.scope[SCOPE_REQUEST_ID] = "request-1"
        if audit_proof:
            request.scope[SCOPE_AUDIT_EVENT_ID] = "pre-mutation-audit"
        return await call_next(request)

    app.include_router(
        create_youtube_browser_api_router(
            YouTubeBrowserApiDependencies(
                service=service,
                workflow_status=workflow,
                audit_recorder=audit,
                enabled=True,
            )
        )
    )
    return TestClient(app)


def test_admin_can_create_read_and_retry_with_bounded_response_and_audit() -> None:
    service = _Service()
    audit = _AuditRecorder()
    with _client(service, identity=_identity(ROLE_ADMIN), audit=audit) as client:
        created = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        status = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")
        retried = client.post(
            f"/api/admin/youtube/claims/{CLAIM_ID}/retry",
            content='{"confirmation_method":"interactive"}',
            headers={"Content-Type": "application/json"},
        )

    assert created.status_code == 201
    assert status.status_code == 200
    assert retried.status_code == 201
    assert created.json() == {
        "claim_id": CLAIM_ID,
        "state": "claimed",
        "phase": "queued",
        "submission_result": "new",
        "media_id": None,
        "catalog_state": "not_cataloged",
        "metadata_state": "unknown",
        "missing_metadata_fields": [],
        "publication_state": "unknown",
        "failure": None,
        "retry_of_claim_id": None,
        "requester_login_key": None,
    }
    assert set(created.json()) == {
        "claim_id",
        "state",
        "phase",
        "submission_result",
        "media_id",
        "catalog_state",
        "metadata_state",
        "missing_metadata_fields",
        "publication_state",
        "failure",
        "retry_of_claim_id",
        "requester_login_key",
    }
    assert created.text.find(VIDEO_ID) == -1
    assert created.text.find("youtu.be") == -1
    assert service.submissions == [
        ("https://youtu.be/AbCdEf123_-", "interactive")
    ]
    assert service.retries == [(CLAIM_ID, "interactive")]
    assert [event.action for event in audit.events] == [
        "youtube.claim.submit.new",
        "youtube.claim.retry.new",
    ]
    assert all(event.target_type == "youtube_claim" for event in audit.events)
    assert all(event.target_id in {CLAIM_ID, RETRY_ID} for event in audit.events)
    assert all(VIDEO_ID not in repr(event) for event in audit.events)
    assert all("youtu.be" not in repr(event) for event in audit.events)


def test_cataloged_claim_projects_single_media_truth_without_publication_or_ai() -> None:
    service = _Service()
    service.snapshot = _snapshot(state="cataloged", media_id=MEDIA_ID)
    workflow = _WorkflowStatus(
        MediaWorkflowStatus(
            metadata_state="incomplete",
            missing_metadata_fields=("description", "tags"),
            publication_state="unpublished",
        )
    )
    with _client(
        service,
        identity=_identity(ROLE_ADMIN),
        workflow=workflow,
    ) as client:
        response = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")

    assert response.status_code == 200
    assert response.json()["catalog_state"] == "cataloged"
    assert response.json()["metadata_state"] == "incomplete"
    assert response.json()["missing_metadata_fields"] == ["description", "tags"]
    assert response.json()["publication_state"] == "unpublished"
    assert workflow.calls == [MEDIA_ID]


def test_successful_source_duplicate_is_200_and_has_terminal_duplicate_result() -> None:
    service = _Service()
    service.snapshot = _snapshot(
        state="duplicate_resolved",
        media_id=MEDIA_ID,
    )
    workflow = _WorkflowStatus(
        MediaWorkflowStatus(
            metadata_state="complete",
            missing_metadata_fields=(),
            publication_state="published",
        )
    )
    with _client(
        service,
        identity=_identity(ROLE_ADMIN),
        workflow=workflow,
    ) as client:
        response = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 200
    assert response.json()["submission_result"] == "terminal_duplicate_reuse"
    assert response.json()["publication_state"] == "published"


def test_active_reuse_and_failed_retry_conflict_are_mapped_truthfully() -> None:
    service = _Service()
    service.created = False
    with _client(service, identity=_identity(ROLE_ADMIN)) as client:
        reused = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        service.failure = YouTubeAcquisitionStateConflictError("not retryable")
        conflict = client.post(
            f"/api/admin/youtube/claims/{CLAIM_ID}/retry",
            content='{"confirmation_method":"interactive"}',
            headers={"Content-Type": "application/json"},
        )

    assert reused.status_code == 200
    assert reused.json()["submission_result"] == "active_reuse"
    assert conflict.status_code == 409


def test_authentication_capability_audit_and_identifier_guards_precede_service() -> None:
    service = _Service()
    with _client(service, identity=None) as client:
        unauthenticated = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")
    with _client(service, identity=_identity(ROLE_USER)) as client:
        ordinary = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")
    with _client(
        service,
        identity=_identity(ROLE_ADMIN, capabilities=frozenset()),
    ) as client:
        missing_capability = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")
    with _client(service, identity=_identity(ROLE_ADMIN), audit_proof=False) as client:
        missing_audit = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        invalid_id = client.get("/api/admin/youtube/claims/not-a-claim")

    assert unauthenticated.status_code == 401
    assert ordinary.status_code == 403
    assert missing_capability.status_code == 403
    assert missing_audit.status_code == 500
    assert invalid_id.status_code == 404
    assert service.submissions == []


def test_request_contract_is_exact_and_no_claim_list_exists() -> None:
    service = _Service()
    with _client(service, identity=_identity(ROLE_ADMIN)) as client:
        wrong_type = client.post(
            "/api/admin/youtube/claims",
            content='{"url":"x","confirmation_method":"interactive"}',
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        unknown = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive","secret":"x"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        oversized = client.post(
            "/api/admin/youtube/claims",
            content='{"url":"' + ("x" * 5_000) + '"}',
            headers={"Content-Type": "application/json"},
        )
        invalid_url = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://evil.example/private",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )
        list_response = client.get("/api/admin/youtube/claims")

    assert wrong_type.status_code == 415
    assert unknown.status_code == 400
    assert oversized.status_code == 413
    assert invalid_url.status_code == 400
    assert "evil.example" not in invalid_url.text
    assert list_response.status_code == 405
    assert service.submissions == []


def test_missing_and_nonexistent_claim_ids_are_equivalent_and_polling_has_no_audit() -> None:
    service = _Service()
    audit = _AuditRecorder()
    with _client(service, identity=_identity(ROLE_ADMIN), audit=audit) as client:
        invalid = client.get("/api/admin/youtube/claims/not-a-claim")
        nonexistent = client.get(
            "/api/admin/youtube/claims/44444444-4444-4444-8444-444444444444"
        )
        polling = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")

    assert invalid.status_code == nonexistent.status_code == 404
    assert invalid.json() == nonexistent.json()
    assert polling.status_code == 200
    assert audit.events == []


def test_supplemental_audit_failure_does_not_false_fail_durable_result() -> None:
    service = _Service()
    audit = _AuditRecorder(fail=True)
    with _client(service, identity=_identity(ROLE_ADMIN), audit=audit) as client:
        response = client.post(
            "/api/admin/youtube/claims",
            content=(
                '{"url":"https://youtu.be/AbCdEf123_-",'
                '"confirmation_method":"interactive"}'
            ),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 201
    assert response.json()["claim_id"] == CLAIM_ID


def test_infrastructure_errors_are_sanitized() -> None:
    service = _Service()
    service.failure = YouTubeAcquisitionInfrastructureError(
        "raw /private/path secret provider output"
    )
    with _client(service, identity=_identity(ROLE_ADMIN)) as client:
        response = client.get(f"/api/admin/youtube/claims/{CLAIM_ID}")

    assert response.status_code == 503
    assert "/private/path" not in response.text
    assert "secret" not in response.text
