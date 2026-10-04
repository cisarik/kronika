"""HTTP surface for the research runtime and personal question history.

Routes use the existing verified-identity scope and the stable research error
codes. A disabled runtime stays inert: capabilities reports it, admission and
cancellation refuse with ``E_DISABLED``, and history reads remain available.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.application.ports.research import (
    ResearchRequestRow,
    ResearchStoreError,
)
from kronika.application.research import ResearchCoordinator
from kronika.domain.identity_access import (
    CAPABILITY_RECORDS_APPROVE,
    CAPABILITY_RESEARCH_RUN,
    ROLE_ADMIN,
    IdentityContext,
)
from kronika.domain.research import ResearchErrorCode, ResearchOperationKind
from kronika.infrastructure.ai.research_configuration import ResearchConfiguration
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY

DEFAULT_RESEARCH_PAGE_LIMIT = 24
MAX_RESEARCH_PAGE_LIMIT = 100
MAX_CLIENT_REQUEST_ID_LENGTH = 128
MAX_CONSENT_VERSION_LENGTH = 32


@dataclass(frozen=True, slots=True)
class ResearchApiDependencies:
    """Runtime plus the durable request store used for history reads.

    ``configuration_provider`` reads a fresh validated configuration so both
    capabilities and admission reflect the current settings without restarting
    the process.
    """

    requests: object
    runtime: ResearchCoordinator | None
    configuration: ResearchConfiguration | None = None
    configuration_provider: Callable[[], ResearchConfiguration | None] | None = None


class ResearchCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str
    prompt: str
    client_request_id: str
    consent_version: str


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def _request_identity(request: Request) -> IdentityContext | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    if isinstance(identity, IdentityContext):
        return identity
    return None


def _require_capability(
    request: Request,
    capability: str,
) -> tuple[IdentityContext | None, JSONResponse | None]:
    identity = _request_identity(request)
    if identity is None or not identity.login_key:
        return None, _error_response(
            401, "IDENTITY_REQUIRED", "A verified identity is required."
        )
    if not identity.has_capability(capability):
        return None, _error_response(
            403, "CAPABILITY_DENIED", "The verified identity is not authorized."
        )
    return identity, None


def _is_admin(identity: IdentityContext) -> bool:
    return identity.role == ROLE_ADMIN or identity.has_capability(
        CAPABILITY_RECORDS_APPROVE
    )


def _summary(row: ResearchRequestRow) -> dict[str, object]:
    record = row.record
    return {
        "operation_id": record.operation_id,
        "kind": record.kind.value,
        "state": record.state.value,
        "error_code": None if record.error_code is None else record.error_code.value,
        "prompt": record.prompt,
        "created_at_ms": row.created_at_ms,
        "admitted_at_ms": row.admitted_at_ms,
        "submitted_at_ms": row.submitted_at_ms,
        "finished_at_ms": row.finished_at_ms,
        "updated_at_ms": row.updated_at_ms,
        "record_id": row.record_id,
        "provider_id": record.profile.provider_id,
        "model_id": record.profile.model_id,
        "configuration_version": record.profile.configuration_version,
        "accounting_state": record.accounting_state.value,
    }


def _current_configuration(
    dependencies: ResearchApiDependencies,
) -> ResearchConfiguration | None:
    if dependencies.configuration_provider is not None:
        try:
            return dependencies.configuration_provider()
        except Exception:
            return None
    return dependencies.configuration


def _page_params(request: Request) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", DEFAULT_RESEARCH_PAGE_LIMIT))
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        return DEFAULT_RESEARCH_PAGE_LIMIT, 0
    limit = max(1, min(limit, MAX_RESEARCH_PAGE_LIMIT))
    offset = max(0, offset)
    return limit, offset


def _store_refusal(exc: ResearchStoreError) -> JSONResponse:
    mapping = {
        ResearchErrorCode.DISABLED: (503, "Research is disabled."),
        ResearchErrorCode.NOT_CONFIGURED: (503, "Research is not configured."),
        ResearchErrorCode.BUSY: (409, "Another research request is active."),
        ResearchErrorCode.IDEMPOTENCY_CONFLICT: (
            409,
            "The client request id was reused with different content.",
        ),
        ResearchErrorCode.BUDGET_EXCEEDED: (429, "The research budget was exceeded."),
        ResearchErrorCode.INVALID_REQUEST: (422, "The research request is invalid."),
        ResearchErrorCode.STORAGE: (500, "Research storage failed."),
    }
    status, message = mapping.get(exc.code, (500, "Research storage failed."))
    return _error_response(status, exc.code.value, message)


def create_research_api_router(dependencies: ResearchApiDependencies) -> APIRouter:
    router = APIRouter()

    @router.get("/api/research/capabilities")
    def research_capabilities() -> dict[str, object]:
        configuration = _current_configuration(dependencies)
        enabled = (
            dependencies.runtime is not None
            and configuration is not None
            and configuration.enabled
        )
        payload: dict[str, object] = {
            "enabled": enabled,
            "provider_id": None,
            "model_id": None,
            "kinds": ["search", "research"],
            "retention_notice": (
                "Answers are stored locally. The remote response is deleted "
                "after a validated local save; standard provider retention "
                "may still apply."
            ),
        }
        if enabled and configuration is not None:
            payload.update(
                {
                    "provider_id": configuration.provider_id,
                    "model_id": configuration.model_id,
                    "daily_budget_usd_micros": configuration.daily_budget_usd_micros,
                    "monthly_budget_usd_micros": configuration.monthly_budget_usd_micros,
                    "search": {
                        "deadline_seconds": configuration.search.deadline_seconds,
                        "budget_reservation_usd_micros": (
                            configuration.search.budget_reservation_usd_micros
                        ),
                        "max_tool_calls": configuration.search.max_tool_calls,
                        "max_output_tokens": configuration.search.max_output_tokens,
                    },
                    "research": {
                        "deadline_seconds": configuration.research.deadline_seconds,
                        "budget_reservation_usd_micros": (
                            configuration.research.budget_reservation_usd_micros
                        ),
                        "max_tool_calls": configuration.research.max_tool_calls,
                        "max_output_tokens": configuration.research.max_output_tokens,
                    },
                }
            )
        return payload

    @router.get("/api/research-requests")
    def list_research_requests(request: Request) -> JSONResponse:
        identity, denial = _require_capability(request, CAPABILITY_RESEARCH_RUN)
        if denial is not None:
            return denial
        assert identity is not None
        limit, offset = _page_params(request)
        rows = dependencies.requests.list_for_owner(
            identity.login_key, limit=limit, offset=offset
        )
        total = dependencies.requests.count_for_owner(identity.login_key)
        return JSONResponse(
            status_code=200,
            content={
                "items": [_summary(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        )

    @router.get("/api/admin/research-requests")
    def list_admin_research_requests(request: Request) -> JSONResponse:
        identity, denial = _require_capability(request, CAPABILITY_RECORDS_APPROVE)
        if denial is not None:
            return denial
        limit, offset = _page_params(request)
        rows = dependencies.requests.list_all(limit=limit, offset=offset)
        total = dependencies.requests.count_all()
        return JSONResponse(
            status_code=200,
            content={
                "items": [_summary(row) for row in rows],
                "total": total,
                "limit": limit,
                "offset": offset,
            },
        )

    @router.post("/api/research-requests", status_code=202)
    def create_research_request(
        request: Request, body: ResearchCreateRequest
    ) -> JSONResponse:
        identity, denial = _require_capability(request, CAPABILITY_RESEARCH_RUN)
        if denial is not None:
            return denial
        assert identity is not None
        runtime = dependencies.runtime
        if runtime is None:
            return _error_response(
                503,
                ResearchErrorCode.DISABLED.value,
                "Research is disabled.",
            )
        try:
            kind = ResearchOperationKind(body.kind)
        except ValueError:
            return _error_response(
                422, ResearchErrorCode.INVALID_REQUEST.value, "The kind is invalid."
            )
        client_request_id = body.client_request_id.strip()
        consent_version = body.consent_version.strip()
        if (
            not client_request_id
            or len(client_request_id) > MAX_CLIENT_REQUEST_ID_LENGTH
            or not consent_version
            or len(consent_version) > MAX_CONSENT_VERSION_LENGTH
        ):
            return _error_response(
                422, ResearchErrorCode.INVALID_REQUEST.value, "The request is invalid."
            )
        try:
            receipt = runtime.admit(
                owner_login_key=identity.login_key,
                client_request_id=client_request_id,
                kind=kind,
                prompt=body.prompt,
                consent_version=consent_version,
            )
        except ResearchStoreError as exc:
            return _store_refusal(exc)
        row = receipt.row
        # Only a newly admitted request may trigger the submission nudge; an
        # identical replay returns the original attempt untouched.
        if receipt.newly_admitted and row.record.state.value == "admitted":
            try:
                runtime.submit_pending()
                runtime.release_remote_pending()
            except Exception:
                pass
            refreshed = dependencies.requests.get_request(row.record.operation_id)
            if refreshed is not None:
                row = refreshed
        return JSONResponse(status_code=202, content=_summary(row))

    @router.get("/api/research-requests/{operation_id}")
    def read_research_request(request: Request, operation_id: str) -> JSONResponse:
        identity, denial = _require_capability(request, CAPABILITY_RESEARCH_RUN)
        if denial is not None:
            return denial
        assert identity is not None
        row = dependencies.requests.get_request(operation_id)
        if row is None or (
            row.owner_login_key != identity.login_key and not _is_admin(identity)
        ):
            return _error_response(404, "NOT_FOUND", "The request was not found.")
        runtime = dependencies.runtime
        if runtime is not None:
            try:
                if runtime.submit_pending() is None:
                    runtime.poll_once()
                runtime.release_remote_pending()
            except Exception:
                pass
            refreshed = dependencies.requests.get_request(operation_id)
            if refreshed is not None:
                row = refreshed
        return JSONResponse(status_code=200, content=_summary(row))

    @router.post("/api/research-requests/{operation_id}/cancel")
    def cancel_research_request(request: Request, operation_id: str) -> JSONResponse:
        identity, denial = _require_capability(request, CAPABILITY_RESEARCH_RUN)
        if denial is not None:
            return denial
        assert identity is not None
        row = dependencies.requests.get_request(operation_id)
        if row is None or (
            row.owner_login_key != identity.login_key and not _is_admin(identity)
        ):
            return _error_response(404, "NOT_FOUND", "The request was not found.")
        runtime = dependencies.runtime
        if runtime is None:
            return _error_response(
                503, ResearchErrorCode.DISABLED.value, "Research is disabled."
            )
        try:
            row = runtime.cancel(operation_id)
        except ResearchStoreError as exc:
            return _store_refusal(exc)
        return JSONResponse(status_code=200, content=_summary(row))

    return router
