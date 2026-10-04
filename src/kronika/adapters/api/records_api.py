"""HTTP surface for records, personal history, Timeline and safe rendering.

The S6 record service owns the object decision (owner, administrator,
approved household projection). These routes only carry the verified identity
into it and serialize safe payloads. Rendering returns escaped HTML and never
raw stored text.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.document_rendering import render_record_document
from kronika.domain.identity_access import (
    CAPABILITY_RECORDS_APPROVE,
    IdentityContext,
)
from kronika.domain.records import (
    RecordConflictError,
    RecordNotFoundError,
    RecordValueError,
)

DEFAULT_RECORD_PAGE_LIMIT = 24
MAX_RECORD_PAGE_LIMIT = 100


@dataclass(frozen=True, slots=True)
class RecordsApiDependencies:
    """Record service plus the clock used for administrator approval."""

    service: object
    clock_ms: object | None = None


class ApprovalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: str
    expected_version: int


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code, content={"error": {"code": code, "message": message}}
    )


def _request_identity(request: Request) -> IdentityContext | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    if isinstance(identity, IdentityContext):
        return identity
    return None


def _require_identity(
    request: Request,
) -> tuple[IdentityContext | None, JSONResponse | None]:
    identity = _request_identity(request)
    if identity is None or not identity.login_key:
        return None, _error_response(
            401, "IDENTITY_REQUIRED", "A verified identity is required."
        )
    return identity, None


def _require_admin(
    request: Request,
) -> tuple[IdentityContext | None, JSONResponse | None]:
    identity, denial = _require_identity(request)
    if denial is not None:
        return None, denial
    assert identity is not None
    if not identity.has_capability(CAPABILITY_RECORDS_APPROVE):
        return None, _error_response(
            403, "CAPABILITY_DENIED", "The verified identity is not authorized."
        )
    return identity, None


def _page_params(request: Request) -> tuple[int, int]:
    try:
        limit = int(request.query_params.get("limit", DEFAULT_RECORD_PAGE_LIMIT))
        offset = int(request.query_params.get("offset", 0))
    except (TypeError, ValueError):
        return DEFAULT_RECORD_PAGE_LIMIT, 0
    limit = max(1, min(limit, MAX_RECORD_PAGE_LIMIT))
    offset = max(0, offset)
    return limit, offset


def _summary_payload(summary: object) -> dict[str, object]:
    return {
        "record_id": summary.record_id,
        "kind": summary.kind.value,
        "owner_login_key": summary.owner_login_key,
        "visibility": summary.visibility.value,
        "created_at_ms": summary.created_at_ms,
        "completed_at_ms": summary.completed_at_ms,
        "timeline_entered_at_ms": summary.timeline_entered_at_ms,
        "version": summary.version,
        "media_id": summary.media_id,
        "read_decision": summary.read_decision,
        "display_title": summary.display_title,
        "content_category": summary.content_category,
    }


def _optional_query(request: Request, name: str) -> str | None:
    if name not in request.query_params:
        return None
    value = request.query_params.get(name)
    if value is None or value == "":
        return None
    return value


def _list_filters(request: Request, *, administrator: bool) -> dict[str, str | None]:
    visibility = _optional_query(request, "visibility")
    if visibility is not None and not administrator:
        raise RecordValueError()
    return {
        "kind": _optional_query(request, "kind"),
        "content_category": _optional_query(request, "content_category"),
        "visibility": visibility if administrator else None,
    }


def _detail_payload(detail: object) -> dict[str, object]:
    document = detail.document
    return {
        "record": _summary_payload(detail.summary),
        "version": detail.version,
        "document": (
            None
            if document is None
            else {
                "operation_id": document.operation_id,
                "kind": document.kind.value,
                "question_text": document.question_text,
                "citations": [item.as_json_object() for item in document.citations],
                "created_at_ms": document.created_at_ms,
                "completed_at_ms": document.completed_at_ms,
            }
        ),
    }


def _page_payload(page: object) -> dict[str, object]:
    return {
        "items": [_summary_payload(item) for item in page.items],
        "total": page.total,
        "limit": page.limit,
        "offset": page.offset,
    }


def create_records_api_router(dependencies: RecordsApiDependencies) -> APIRouter:
    router = APIRouter()
    service = dependencies.service

    @router.get("/api/my/records")
    def list_my_records(request: Request) -> JSONResponse:
        identity, denial = _require_identity(request)
        if denial is not None:
            return denial
        assert identity is not None
        limit, offset = _page_params(request)
        try:
            filters = _list_filters(request, administrator=False)
            page = service.list_own_history(
                identity,
                limit=limit,
                offset=offset,
                kind=filters["kind"],
                content_category=filters["content_category"],
            )
        except RecordValueError:
            return _error_response(422, "INVALID_REQUEST", "The list request is invalid.")
        return JSONResponse(status_code=200, content=_page_payload(page))

    @router.get("/api/timeline")
    def list_timeline(request: Request) -> JSONResponse:
        identity, denial = _require_identity(request)
        if denial is not None:
            return denial
        assert identity is not None
        limit, offset = _page_params(request)
        try:
            filters = _list_filters(request, administrator=False)
            page = service.list_timeline(
                identity,
                limit=limit,
                offset=offset,
                kind=filters["kind"],
                content_category=filters["content_category"],
            )
        except RecordValueError:
            return _error_response(422, "INVALID_REQUEST", "The list request is invalid.")
        return JSONResponse(status_code=200, content=_page_payload(page))

    @router.get("/api/admin/records")
    def list_admin_records(request: Request) -> JSONResponse:
        identity, denial = _require_admin(request)
        if denial is not None:
            return denial
        assert identity is not None
        limit, offset = _page_params(request)
        try:
            filters = _list_filters(request, administrator=True)
            page = service.list_admin_inventory(
                identity,
                limit=limit,
                offset=offset,
                kind=filters["kind"],
                content_category=filters["content_category"],
                visibility=filters["visibility"],
            )
        except RecordValueError:
            return _error_response(422, "INVALID_REQUEST", "The list request is invalid.")
        return JSONResponse(status_code=200, content=_page_payload(page))

    @router.get("/api/records/{record_id}")
    def read_record(request: Request, record_id: str) -> JSONResponse:
        identity, denial = _require_identity(request)
        if denial is not None:
            return denial
        assert identity is not None
        try:
            detail = service.read_detail(identity, record_id)
        except RecordNotFoundError:
            return _error_response(404, "NOT_FOUND", "The record was not found.")
        return JSONResponse(status_code=200, content=_detail_payload(detail))

    @router.get("/api/records/{record_id}/render", response_model=None)
    def render_record(request: Request, record_id: str) -> HTMLResponse | JSONResponse:
        identity, denial = _require_identity(request)
        if denial is not None:
            return denial
        assert identity is not None
        try:
            detail = service.read_detail(identity, record_id)
        except RecordNotFoundError:
            return _error_response(404, "NOT_FOUND", "The record was not found.")
        document = detail.document
        if document is None:
            return _error_response(
                404, "NOT_FOUND", "No document is available for this record."
            )
        html = render_record_document(
            question_text=document.question_text,
            answer_text=document.answer_text,
        )
        return HTMLResponse(
            content=html,
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": (
                    "default-src 'none'; style-src 'unsafe-inline'; "
                    "img-src data:; base-uri 'none'; form-action 'none'"
                ),
            },
        )

    @router.post("/api/admin/records/{record_id}/approval")
    def approve_record(request: Request, record_id: str, body: ApprovalRequest) -> JSONResponse:
        identity, denial = _require_admin(request)
        if denial is not None:
            return denial
        assert identity is not None
        clock = dependencies.clock_ms
        now_ms = int(clock()) if callable(clock) else time.time_ns() // 1_000_000
        try:
            if body.action == "approve":
                candidate = service.prepare_approval(identity, record_id)
                if body.expected_version != candidate.version:
                    raise RecordConflictError()
                result = service.approve(
                    identity,
                    candidate,
                    approved_at_ms=now_ms,
                )
            elif body.action == "withdraw":
                result = service.withdraw(
                    identity,
                    record_id=record_id,
                    expected_version=body.expected_version,
                )
            else:
                return _error_response(
                    422,
                    "UNSUPPORTED_ACTION",
                    "Supported actions are approve and withdraw.",
                )
        except RecordNotFoundError:
            return _error_response(404, "NOT_FOUND", "The record was not found.")
        except RecordConflictError:
            return _error_response(
                409, "RECORD_CONFLICT", "The record changed; reload and retry."
            )
        except RecordValueError:
            return _error_response(
                422, "INVALID_REQUEST", "The approval request is invalid."
            )
        return JSONResponse(
            status_code=200,
            content={
                "record_id": result.record_id,
                "version": result.version,
                "changed": result.changed,
            },
        )

    return router
