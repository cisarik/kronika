"""Authenticated companion meme picker API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.companion_picker import (
    COMPANION_API_VERSION,
    CompanionPickerItem,
    CompanionPickerPage,
    DEFAULT_COMPANION_PICKER_LIMIT,
    MAX_COMPANION_PICKER_LIMIT,
    ListCompanionPickerMedia,
)
from kronika.application.media_catalog import MediaCatalogValidationError
from kronika.application.ports.media_catalog_repository import (
    FrameNestMediaCatalogRepositoryError,
)
from kronika.domain.identity_access import (
    CAPABILITY_X_REQUEST,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
COMPANION_QUERY_INVALID_CODE = "INVALID_COMPANION_PICKER_QUERY"
COMPANION_QUERY_INVALID_MESSAGE = "Invalid companion picker query."
COMPANION_QUERY_FAILED_CODE = "COMPANION_PICKER_QUERY_FAILED"
COMPANION_QUERY_FAILED_MESSAGE = "Companion picker query failed."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class CompanionPickerTagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str
    display_name: str


class CompanionPickerLocationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location_id: str
    media_type: str
    observed_size_bytes: int | None


class CompanionPickerItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    media_kind: str
    created_at_ms: int
    display_title: str | None
    tags: list[CompanionPickerTagResponse]
    location: CompanionPickerLocationResponse


class CompanionPickerListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_api_version: str
    items: list[CompanionPickerItemResponse]
    next_cursor: str | None
    q: str | None
    tag_keys: list[str]
    kind: str | None
    limit: int


class CompanionErrorBody(BaseModel):
    code: str
    message: str


class CompanionErrorResponse(BaseModel):
    error: CompanionErrorBody


@dataclass(frozen=True, slots=True)
class XCompanionApiDependencies:
    """Injected companion picker behavior."""

    list_media: ListCompanionPickerMedia | None
    catalog_available: Callable[[], bool]


def create_x_companion_api_router(
    dependencies: XCompanionApiDependencies,
) -> APIRouter:
    router = APIRouter()

    @router.get(
        "/api/x/companion/media",
        response_model=CompanionPickerListResponse,
        responses={
            422: {"model": CompanionErrorResponse},
            500: {"model": CompanionErrorResponse},
            503: {"model": CompanionErrorResponse},
        },
    )
    def list_companion_media(
        request: Request,
        q: str | None = None,
        tag: list[str] = Query(default=[]),
        kind: str | None = None,
        limit: int = Query(
            default=DEFAULT_COMPANION_PICKER_LIMIT,
            ge=1,
            le=MAX_COMPANION_PICKER_LIMIT,
        ),
        cursor: str | None = None,
    ) -> JSONResponse:
        identity = _require_identity(request)
        if not dependencies.catalog_available() or dependencies.list_media is None:
            return _error(
                CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE, 503
            )
        try:
            page = dependencies.list_media.execute(
                login_key=identity.login_key,
                q=q,
                tag_keys=tag,
                kind=kind,
                limit=limit,
                cursor=cursor,
            )
        except MediaCatalogValidationError:
            return _error(
                COMPANION_QUERY_INVALID_CODE, COMPANION_QUERY_INVALID_MESSAGE, 422
            )
        except FrameNestMediaCatalogRepositoryError:
            return _error(
                COMPANION_QUERY_FAILED_CODE, COMPANION_QUERY_FAILED_MESSAGE, 500
            )
        return JSONResponse(
            status_code=200,
            content=_page_dict(page),
            headers=_NO_STORE_HEADERS,
        )

    return router


def _require_identity(request: Request) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(status_code=401, detail={"code": "IDENTITY_REQUIRED"})
    if not identity.has_capability(CAPABILITY_X_REQUEST):
        raise HTTPException(status_code=403, detail={"code": "CAPABILITY_DENIED"})
    return identity


def _page_dict(page: CompanionPickerPage) -> dict:
    return {
        "companion_api_version": page.companion_api_version or COMPANION_API_VERSION,
        "items": [_item_dict(item) for item in page.items],
        "next_cursor": page.next_cursor,
        "q": page.q,
        "tag_keys": list(page.tag_keys),
        "kind": page.kind,
        "limit": page.limit,
    }


def _item_dict(item: CompanionPickerItem) -> dict:
    return {
        "media_id": item.media_id,
        "media_kind": item.media_kind,
        "created_at_ms": item.created_at_ms,
        "display_title": item.display_title,
        "tags": [
            {"key": key, "display_name": display_name} for key, display_name in item.tags
        ],
        "location": {
            "location_id": item.location.location_id,
            "media_type": item.location.media_type,
            "observed_size_bytes": item.location.observed_size_bytes,
        },
    }


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
