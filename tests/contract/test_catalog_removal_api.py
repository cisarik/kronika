"""API contract for capability-gated administrator catalog removal."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.catalog_removal_api import (
    CatalogRemovalApiDependencies,
    create_catalog_removal_api_router,
)
from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
    SCOPE_REQUEST_ID,
)
from kronika.application.catalog_removal import (
    CatalogMediaRemovalService,
    CatalogRemovalNotFoundError,
    CatalogRemovalPreview,
    CatalogRemovalReceipt,
    CatalogRemovalResult,
    CatalogRemovalStateConflictError,
    CatalogRemovalValidationError,
)
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_MEDIA_CATALOG_REMOVE,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
)

MEDIA_ID = "11111111-1111-4111-8111-111111111111"
RECEIPT_ID = "22222222-2222-4222-8222-222222222222"
FINGERPRINT = "a" * 64


def _identity(role: str) -> IdentityContext:
    return IdentityContext(
        login=f"{role}@example.com",
        login_key=f"{role}@example.com",
        display_name=role.title(),
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


@dataclass
class _FakeService:
    preview_result: CatalogRemovalPreview | None = None
    execute_result: CatalogRemovalResult | None = None
    retry_result: CatalogRemovalResult | None = None
    execute_error: Exception | None = None
    calls: list[tuple[str, object]] | None = None

    def __post_init__(self) -> None:
        if self.calls is None:
            self.calls = []

    def preview(self, media_id: str) -> CatalogRemovalPreview:
        assert self.calls is not None
        self.calls.append(("preview", media_id))
        if self.preview_result is None:
            raise CatalogRemovalNotFoundError("Media not found.")
        return self.preview_result

    def execute(self, **kwargs: object) -> CatalogRemovalResult:
        assert self.calls is not None
        self.calls.append(("execute", kwargs))
        if self.execute_error is not None:
            raise self.execute_error
        assert self.execute_result is not None
        return self.execute_result

    def retry_cleanup(self, receipt_id: str) -> CatalogRemovalResult:
        assert self.calls is not None
        self.calls.append(("retry", receipt_id))
        if self.retry_result is None:
            raise CatalogRemovalNotFoundError("Catalog removal receipt not found.")
        return self.retry_result


def _preview() -> CatalogRemovalPreview:
    return CatalogRemovalPreview(
        media_id=MEDIA_ID,
        display_title="Synthetic",
        publication_state="published",
        acquisition_source="library_scan",
        storage_class="operator_managed",
        original_bytes_policy="retain_all",
        original_bytes_outcome="retained_operator_managed",
        recovery_limitations=("Catalog backup can restore catalog rows only.",),
        provenance_effects=(),
        analysis_run_count=0,
        provider_submission_count=0,
        derived_artifact_cleanup_intent=("gallery_preview_cache",),
        consequence_fingerprint=FINGERPRINT,
    )


def _receipt(**overrides: object) -> CatalogRemovalReceipt:
    values: dict[str, object] = {
        "id": RECEIPT_ID,
        "occurred_at_ms": 10,
        "request_id": "req",
        "actor_key": "admin@example.com",
        "media_id": MEDIA_ID,
        "display_title_snapshot": "Synthetic",
        "acquisition_source": "library_scan",
        "storage_class": "operator_managed",
        "was_published": True,
        "published_at_ms": 5,
        "consequence_fingerprint": FINGERPRINT,
        "catalog_outcome": "removed",
        "original_bytes_policy": "retain_all",
        "original_bytes_outcome": "retained_operator_managed",
        "youtube_claims_transitioned": 0,
        "upload_publications_detached": 0,
        "analysis_run_count": 0,
        "provider_submission_count": 0,
        "cover_artifact_digest": None,
        "preview_location_ids_json": None,
        "cover_cleanup_state": "none",
        "preview_cleanup_state": "complete",
        "cleanup_updated_at_ms": 11,
    }
    values.update(overrides)
    return CatalogRemovalReceipt(**values)  # type: ignore[arg-type]


def _result(**overrides: object) -> CatalogRemovalResult:
    receipt = overrides.pop("receipt", _receipt())
    values: dict[str, object] = {
        "catalog_state": "removed",
        "receipt": receipt,
        "derived_artifacts_outcome": "complete",
        "cleanup_retry_available": False,
    }
    values.update(overrides)
    return CatalogRemovalResult(**values)  # type: ignore[arg-type]


def _client(
    service: _FakeService,
    *,
    role: str = ROLE_ADMIN,
    audit: bool = True,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_catalog_removal_api_router(
            CatalogRemovalApiDependencies(service=service)  # type: ignore[arg-type]
        )
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        request.scope[SCOPE_IDENTITY] = _identity(role)
        request.scope[SCOPE_REQUEST_ID] = "request-1"
        if audit:
            request.scope[SCOPE_AUDIT_EVENT_ID] = "audit-1"
        return await call_next(request)

    return TestClient(app)


def test_preview_requires_capability_and_returns_fingerprint() -> None:
    service = _FakeService(preview_result=_preview())
    admin = _client(service)
    denied = _client(service, role=ROLE_USER)

    ok = admin.get(f"/api/admin/media/{MEDIA_ID}/catalog-removal")
    assert ok.status_code == 200
    assert ok.json()["consequence_fingerprint"] == FINGERPRINT
    assert ok.json()["original_bytes_policy"] == "retain_all"

    forbidden = denied.get(f"/api/admin/media/{MEDIA_ID}/catalog-removal")
    assert forbidden.status_code == 403
    assert forbidden.json()["error"]["code"] == "CAPABILITY_DENIED"


def test_mutation_enforces_ack_fingerprint_and_stale_conflict() -> None:
    service = _FakeService(
        execute_result=_result(),
        execute_error=CatalogRemovalStateConflictError("stale"),
    )
    client = _client(service)
    conflict = client.post(
        f"/api/admin/media/{MEDIA_ID}/catalog-removal",
        json={
            "acknowledge_consequences": True,
            "consequence_fingerprint": FINGERPRINT,
        },
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "CATALOG_REMOVAL_STATE_CONFLICT"

    service.execute_error = CatalogRemovalValidationError("ack")
    invalid = client.post(
        f"/api/admin/media/{MEDIA_ID}/catalog-removal",
        json={
            "acknowledge_consequences": False,
            "consequence_fingerprint": FINGERPRINT,
        },
    )
    assert invalid.status_code == 422

    service.execute_error = None
    ok = client.post(
        f"/api/admin/media/{MEDIA_ID}/catalog-removal",
        json={
            "acknowledge_consequences": True,
            "consequence_fingerprint": FINGERPRINT,
        },
    )
    assert ok.status_code == 200
    body = ok.json()
    assert body["catalog_state"] == "removed"
    assert body["receipt"]["receipt_id"] == RECEIPT_ID
    assert body["receipt"]["original_bytes_outcome"] == "retained_operator_managed"


def test_cleanup_retry_uses_receipt_identity() -> None:
    service = _FakeService(retry_result=_result(cleanup_retry_available=False))
    client = _client(service)
    response = client.post(
        f"/api/admin/catalog-removal-receipts/{RECEIPT_ID}/cleanup-retry"
    )
    assert response.status_code == 200
    assert service.calls == [("retry", RECEIPT_ID)]


def test_admin_capability_is_mapped_only_to_admin_role() -> None:
    assert CAPABILITY_MEDIA_CATALOG_REMOVE in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_MEDIA_CATALOG_REMOVE not in CAPABILITIES_BY_ROLE[ROLE_USER]
