"""FastAPI routes for searchable media catalog browsing."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4

from kronika.domain.record_access import READ_APPROVED, READ_DENY, scope_for_identity
from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_decision,
)
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.media_catalog import MediaCatalogValidationError
from kronika.application.media_user_alias import (
    EMPTY_ALIAS_VIEW,
    CallerAliasOverlayPage,
)
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    CatalogMediaTag,
    FrameNestMediaCatalogRepositoryError,
)
from kronika.domain.identities import MediaId
from kronika.domain.identity_access import IdentityContext
from kronika.domain.media_metadata import MediaCollectionKey

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
MEDIA_CATALOG_INVALID_QUERY_CODE = "INVALID_MEDIA_CATALOG_QUERY"
MEDIA_CATALOG_INVALID_QUERY_MESSAGE = "Invalid media catalog query."
MEDIA_CATALOG_QUERY_FAILED_CODE = "MEDIA_CATALOG_QUERY_FAILED"
MEDIA_CATALOG_QUERY_FAILED_MESSAGE = "Media catalog query failed."


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class CatalogTagResponse(BaseModel):
    key: str
    display_name: str
    position: int


class CatalogLocationResponse(BaseModel):
    location_id: str
    library_id: str
    relative_path: str
    availability: str
    observed_size_bytes: int | None
    observed_mtime_ns: int | None


class CatalogMediaResponse(BaseModel):
    media_id: str
    media_kind: str
    created_at_ms: int
    updated_at_ms: int
    display_title: str | None
    collection_key: str | None
    processed_at_ms: int | None
    tags: list[CatalogTagResponse]
    locations: list[CatalogLocationResponse]
    content_category: str = "general"
    acquisition_source: str = "unknown"
    description: str | None = None
    cover_ready: bool = False
    creator_attribution_kind: str | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None


class MediaCatalogResponse(BaseModel):
    items: list[CatalogMediaResponse]
    total: int
    limit: int
    offset: int
    q: str | None
    tag_keys: list[str]
    content_category: str | None = None
    acquisition_source: str | None = None
    creator_attribution_kind: str | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None


@dataclass(frozen=True, slots=True)
class MediaCatalogApiDependencies:
    """Injected dependencies for media catalog routes."""

    list_media: object
    catalog_available: Callable[[], bool]
    get_media: object | None = None
    audience_policy: ContentAudiencePolicy | None = None
    list_aliases: object | None = None


def create_media_catalog_api_router(dependencies: MediaCatalogApiDependencies) -> APIRouter:
    """Create the searchable media catalog API router."""
    router = APIRouter()

    @router.get(
        "/api/media",
        response_model=MediaCatalogResponse,
        responses={
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_media(
        request: Request,
        q: str | None = None,
        tag: list[str] = Query(default=[]),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        collection: str | None = None,
        content_category: str | None = None,
        acquisition_source: str | None = None,
        creator_attribution_kind: str | None = None,
        creator_stable_id: str | None = None,
        creator_handle: str | None = None,
    ) -> MediaCatalogResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        parsed_collection: MediaCollectionKey | None = None
        if collection is not None:
            try:
                parsed_collection = MediaCollectionKey(collection)
            except Exception:
                return _error_response(
                    422,
                    MEDIA_CATALOG_INVALID_QUERY_CODE,
                    MEDIA_CATALOG_INVALID_QUERY_MESSAGE,
                )
        try:
            result = dependencies.list_media.execute(
                q=q,
                tag_keys=tag,
                limit=limit,
                offset=offset,
                collection_key=parsed_collection,
                content_category=content_category,
                acquisition_source=acquisition_source,
                creator_attribution_kind=creator_attribution_kind,
                creator_stable_id=creator_stable_id,
                creator_handle=creator_handle,
                access_scope=scope_for_identity(request.scope.get(SCOPE_IDENTITY)),
            )
        except MediaCatalogValidationError:
            return _error_response(
                422,
                MEDIA_CATALOG_INVALID_QUERY_CODE,
                MEDIA_CATALOG_INVALID_QUERY_MESSAGE,
            )
        except FrameNestMediaCatalogRepositoryError:
            return _error_response(
                500,
                MEDIA_CATALOG_QUERY_FAILED_CODE,
                MEDIA_CATALOG_QUERY_FAILED_MESSAGE,
            )
        except Exception:
            return _error_response(
                500,
                MEDIA_CATALOG_QUERY_FAILED_CODE,
                MEDIA_CATALOG_QUERY_FAILED_MESSAGE,
            )
        return _catalog_response(
            result,
            overlay_page=_caller_overlay_page(
                request,
                dependencies.list_aliases,
                [item.media_id for item in result.items],
            ),
        )

    @router.get(
        "/api/media/{media_id}",
        response_model=CatalogMediaResponse,
        responses={
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_media(
        media_id: UUID4,
        request: Request,
    ) -> CatalogMediaResponse | JSONResponse:
        if not dependencies.catalog_available() or dependencies.get_media is None:
            return _catalog_unavailable_response()
        parsed_id = MediaId.from_string(str(media_id))
        try:
            decision = content_audience_decision(
                request=request,
                media_id=parsed_id,
                policy=dependencies.audience_policy,
            )
        except ContentAudienceUnavailableError:
            return _error_response(
                500,
                MEDIA_CATALOG_QUERY_FAILED_CODE,
                MEDIA_CATALOG_QUERY_FAILED_MESSAGE,
            )
        if decision == READ_DENY:
            return _error_response(
                404,
                MEDIA_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        try:
            if decision == READ_APPROVED:
                item = dependencies.get_media.execute_approved(str(media_id))
            else:
                item = dependencies.get_media.execute(str(media_id))
        except MediaCatalogValidationError:
            return _error_response(
                404,
                MEDIA_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        except FrameNestMediaCatalogRepositoryError:
            return _error_response(
                500,
                MEDIA_CATALOG_QUERY_FAILED_CODE,
                MEDIA_CATALOG_QUERY_FAILED_MESSAGE,
            )
        except Exception:
            return _error_response(
                500,
                MEDIA_CATALOG_QUERY_FAILED_CODE,
                MEDIA_CATALOG_QUERY_FAILED_MESSAGE,
            )
        if item is None:
            return _error_response(
                404,
                MEDIA_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        overlay_page = _caller_overlay_page(
            request,
            dependencies.list_aliases,
            [item.media_id],
        )
        return _media_response(item, overlay_page=overlay_page)

    return router


def _catalog_unavailable_response() -> JSONResponse:
    return _error_response(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _catalog_response(
    result: object,
    *,
    overlay_page: CallerAliasOverlayPage | None = None,
) -> MediaCatalogResponse:
    return MediaCatalogResponse(
        items=[
            _media_response(item, overlay_page=overlay_page) for item in result.items
        ],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
        q=result.q,
        tag_keys=[key.value for key in result.tag_keys],
        content_category=getattr(result, "content_category", None),
        acquisition_source=getattr(result, "acquisition_source", None),
        creator_attribution_kind=getattr(result, "creator_attribution_kind", None),
        creator_stable_id=getattr(result, "creator_stable_id", None),
        creator_handle=getattr(result, "creator_handle", None),
    )


def _media_response(
    item: object,
    *,
    overlay_page: CallerAliasOverlayPage | None = None,
) -> CatalogMediaResponse:
    merged = _merge_overlay_into_catalog_item(item, overlay_page)
    return CatalogMediaResponse(
        media_id=merged.media_id,
        media_kind=merged.media_kind,
        created_at_ms=merged.created_at_ms,
        updated_at_ms=merged.updated_at_ms,
        display_title=merged.display_title,
        collection_key=merged.collection_key,
        processed_at_ms=merged.processed_at_ms,
        content_category=getattr(merged, "content_category", "general"),
        acquisition_source=getattr(merged, "acquisition_source", "unknown"),
        description=getattr(merged, "description", None),
        cover_ready=getattr(merged, "cover_ready", False),
        creator_attribution_kind=getattr(merged, "creator_attribution_kind", None),
        creator_stable_id=getattr(merged, "creator_stable_id", None),
        creator_handle=getattr(merged, "creator_handle", None),
        creator_display_name=getattr(merged, "creator_display_name", None),
        tags=[
            CatalogTagResponse(
                key=tag.key,
                display_name=tag.display_name,
                position=tag.position,
            )
            for tag in merged.tags
        ],
        locations=[
            CatalogLocationResponse(
                location_id=location.location_id,
                library_id=location.library_id,
                relative_path=location.relative_path,
                availability=location.availability,
                observed_size_bytes=location.observed_size_bytes,
                observed_mtime_ns=location.observed_mtime_ns,
            )
            for location in merged.locations
        ],
    )


def _caller_overlay_page(
    request: Request,
    list_aliases: object | None,
    media_ids: list[str],
) -> CallerAliasOverlayPage | None:
    if list_aliases is None or not media_ids:
        return None
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext) or not identity.login_key:
        return None
    page = list_aliases.execute(identity.login_key, media_ids)
    if not isinstance(page, CallerAliasOverlayPage):
        return None
    return page


def _merge_overlay_into_catalog_item(
    item: object,
    overlay_page: CallerAliasOverlayPage | None,
) -> object:
    if overlay_page is None or not isinstance(item, CatalogMediaItem):
        return item
    overlay = overlay_page.overlays.get(item.media_id)
    if overlay is None or overlay is EMPTY_ALIAS_VIEW or not overlay.persisted:
        return item
    display_title = (
        overlay.display_title if overlay.display_title is not None else item.display_title
    )
    description = (
        overlay.description if overlay.description is not None else item.description
    )
    tags = item.tags
    if overlay.tag_keys:
        tags = tuple(
            CatalogMediaTag(
                key=key,
                display_name=overlay_page.tag_display_names.get(key, key),
                position=index,
            )
            for index, key in enumerate(overlay.tag_keys)
        )
    return replace(
        item,
        display_title=display_title,
        description=description,
        tags=tags,
    )
