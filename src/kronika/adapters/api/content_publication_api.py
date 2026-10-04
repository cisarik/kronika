"""Capability-gated administrator content-publication API."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kronika.adapters.api.tailscale_ingress import (
    SCOPE_AUDIT_EVENT_ID,
    SCOPE_IDENTITY,
)
from kronika.application.content_publication import (
    ContentPublicationValidationError,
)
from kronika.application.ports.content_publication_repository import (
    ContentPublicationBoundRecordError,
    ContentPublicationMediaNotFoundError,
    FrameNestContentPublicationRepositoryError,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_CONTENT_PUBLISH,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
INVALID_QUERY_CODE = "INVALID_ADMIN_MEDIA_QUERY"
INVALID_QUERY_MESSAGE = "Invalid admin media query."
WORKFLOW_FAILED_CODE = "MEDIA_WORKFLOW_QUERY_FAILED"
WORKFLOW_FAILED_MESSAGE = "Media workflow query failed."
PUBLICATION_NOT_READY_CODE = "CONTENT_PUBLICATION_NOT_READY"
PUBLICATION_NOT_READY_MESSAGE = "Content is not ready for publication."
PUBLICATION_FAILED_CODE = "CONTENT_PUBLICATION_FAILED"
PUBLICATION_FAILED_MESSAGE = "Content publication failed."
INVALID_PUBLICATION_BODY_CODE = "INVALID_CONTENT_PUBLICATION"
INVALID_PUBLICATION_BODY_MESSAGE = "Invalid content publication request."
_UNPUBLISH_STATUSES = frozenset({"unpublished", "already_unpublished"})
IDENTITY_REQUIRED_CODE = "IDENTITY_REQUIRED"
IDENTITY_REQUIRED_MESSAGE = "A verified application identity is required."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"
CAPABILITY_DENIED_MESSAGE = "The verified identity is not authorized."
AUDIT_UNAVAILABLE_CODE = "AUDIT_UNAVAILABLE"
AUDIT_UNAVAILABLE_MESSAGE = "The privileged action could not be recorded."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class ErrorBody(BaseModel):
    code: str
    message: str
    missing_fields: list[str] | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class AdminTagResponse(BaseModel):
    key: str
    display_name: str
    position: int


class AdminLocationResponse(BaseModel):
    location_id: str
    library_id: str
    availability: str
    observed_size_bytes: int | None
    observed_mtime_ns: int | None


class AdminContributorResponse(BaseModel):
    login_key: str
    sources: list[str]


class AdminMediaResponse(BaseModel):
    media_id: str
    media_kind: str
    created_at_ms: int
    updated_at_ms: int
    display_title: str | None
    description: str | None
    collection_key: str | None
    processed_at_ms: int | None
    processed: bool
    content_category: str
    acquisition_source: str
    tags: list[AdminTagResponse]
    locations: list[AdminLocationResponse]
    content_publication_state: str
    publication_origin: str | None
    published_at_ms: int | None
    publication_ready: bool
    missing_fields: list[str]
    analysis_state: str
    contributors: list[AdminContributorResponse] = []


class AdminMediaPageResponse(BaseModel):
    items: list[AdminMediaResponse]
    total: int
    limit: int
    offset: int
    q: str | None
    tag_keys: list[str]
    publication: str
    readiness: str
    analysis: str
    contributor: str | None = None
    has_previous: bool
    has_next: bool


class PublicationRepresentationResponse(BaseModel):
    state: str
    publication_origin: str
    published_at_ms: int


class PublishContentResponse(BaseModel):
    status: str
    media_id: str
    publication: PublicationRepresentationResponse | None
    publication_ready: bool
    missing_fields: list[str]


@dataclass(frozen=True, slots=True)
class ContentPublicationApiDependencies:
    """Injected application behavior for publication workflow routes."""

    list_admin_media: object
    publish_content: object
    catalog_available: Callable[[], bool]


def create_content_publication_api_router(
    dependencies: ContentPublicationApiDependencies,
) -> APIRouter:
    """Create the capability-gated publication workflow API."""
    router = APIRouter()

    @router.get(
        "/api/admin/media",
        response_model=AdminMediaPageResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_admin_media(
        request: Request,
        q: str | None = None,
        tag: list[str] = Query(default=[]),
        publication: str = "unpublished",
        readiness: str = "all",
        analysis: str = "all",
        contributor: str | None = None,
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
    ) -> AdminMediaPageResponse | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_MEDIA_WORKFLOW_READ,
        )
        if identity_error is not None:
            return identity_error
        if not dependencies.catalog_available():
            return _error(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            page = dependencies.list_admin_media.execute(
                q=q,
                tag_keys=tag,
                publication=publication,
                readiness=readiness,
                analysis=analysis,
                contributor=contributor,
                limit=limit,
                offset=offset,
            )
        except ContentPublicationValidationError:
            return _error(422, INVALID_QUERY_CODE, INVALID_QUERY_MESSAGE)
        except FrameNestContentPublicationRepositoryError:
            return _error(500, WORKFLOW_FAILED_CODE, WORKFLOW_FAILED_MESSAGE)
        except Exception:
            return _error(500, WORKFLOW_FAILED_CODE, WORKFLOW_FAILED_MESSAGE)
        return _page_response(page)

    @router.put(
        "/api/admin/media/{media_id}/content-publication",
        response_model=PublishContentResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def publish_content(
        media_id: str,
        request: Request,
    ) -> PublishContentResponse | JSONResponse:
        identity_error = _require_capability(
            request,
            CAPABILITY_MEDIA_CONTENT_PUBLISH,
        )
        if identity_error is not None:
            return identity_error
        if not request.scope.get(SCOPE_AUDIT_EVENT_ID):
            return _error(
                500,
                AUDIT_UNAVAILABLE_CODE,
                AUDIT_UNAVAILABLE_MESSAGE,
            )
        if not dependencies.catalog_available():
            return _error(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            published = await _published_from_body(request)
        except ContentPublicationValidationError:
            return _error(
                422,
                INVALID_PUBLICATION_BODY_CODE,
                INVALID_PUBLICATION_BODY_MESSAGE,
            )
        try:
            result = dependencies.publish_content.execute(
                media_id,
                published=published,
            )
        except (ContentPublicationMediaNotFoundError, FrameNestIdentityError):
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except ContentPublicationBoundRecordError:
            return _error(
                409,
                "BOUND_RECORD_CONFLICT",
                "The media item cannot be published through the legacy action.",
            )
        except FrameNestContentPublicationRepositoryError:
            return _error(
                500,
                PUBLICATION_FAILED_CODE,
                PUBLICATION_FAILED_MESSAGE,
            )
        except Exception:
            return _error(
                500,
                PUBLICATION_FAILED_CODE,
                PUBLICATION_FAILED_MESSAGE,
            )
        if result.status == "not_ready":
            return _error(
                409,
                PUBLICATION_NOT_READY_CODE,
                PUBLICATION_NOT_READY_MESSAGE,
                missing_fields=list(result.readiness.missing_fields),
            )
        if result.status in _UNPUBLISH_STATUSES:
            response = PublishContentResponse(
                status=result.status,
                media_id=media_id,
                publication=None,
                publication_ready=result.readiness.ready,
                missing_fields=list(result.readiness.missing_fields),
            )
            return JSONResponse(
                status_code=200,
                content=response.model_dump(),
                headers=_NO_STORE_HEADERS,
            )
        publication = result.publication
        if publication is None:
            return _error(
                500,
                PUBLICATION_FAILED_CODE,
                PUBLICATION_FAILED_MESSAGE,
            )
        response = PublishContentResponse(
            status=result.status,
            media_id=media_id,
            publication=PublicationRepresentationResponse(
                state="published",
                publication_origin=publication.publication_origin.value,
                published_at_ms=publication.published_at_ms,
            ),
            publication_ready=result.readiness.ready,
            missing_fields=list(result.readiness.missing_fields),
        )
        return JSONResponse(
            status_code=201 if result.status == "published" else 200,
            content=response.model_dump(),
            headers=_NO_STORE_HEADERS,
        )

    return router


async def _published_from_body(request: Request) -> bool:
    raw = await request.body()
    if not raw or not raw.strip():
        return True
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ContentPublicationValidationError() from exc
    if not isinstance(payload, dict):
        raise ContentPublicationValidationError()
    if set(payload) - {"published"}:
        raise ContentPublicationValidationError()
    if "published" not in payload:
        return True
    published = payload["published"]
    if not isinstance(published, bool):
        raise ContentPublicationValidationError()
    return published


def _require_capability(
    request: Request,
    capability: str,
) -> JSONResponse | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
    if not identity.has_capability(capability):
        return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
    return None


def _page_response(page: object) -> AdminMediaPageResponse:
    return AdminMediaPageResponse(
        items=[_item_response(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
        q=page.q,
        tag_keys=list(page.tag_keys),
        publication=page.publication,
        readiness=page.readiness,
        analysis=page.analysis,
        contributor=getattr(page, "contributor", None),
        has_previous=page.offset > 0,
        has_next=page.offset + page.limit < page.total,
    )


def _item_response(item: object) -> AdminMediaResponse:
    publication = item.publication
    return AdminMediaResponse(
        media_id=item.media_id,
        media_kind=item.media_kind,
        created_at_ms=item.created_at_ms,
        updated_at_ms=item.updated_at_ms,
        display_title=item.display_title,
        description=item.description,
        collection_key=item.collection_key,
        processed_at_ms=item.processed_at_ms,
        processed=item.collection_key == "processed",
        content_category=item.content_category,
        acquisition_source=item.acquisition_source,
        tags=[
            AdminTagResponse(
                key=tag.key,
                display_name=tag.display_name,
                position=tag.position,
            )
            for tag in item.tags
        ],
        locations=[
            AdminLocationResponse(
                location_id=location.location_id,
                library_id=location.library_id,
                availability=location.availability,
                observed_size_bytes=location.observed_size_bytes,
                observed_mtime_ns=location.observed_mtime_ns,
            )
            for location in item.locations
        ],
        content_publication_state=(
            "published" if publication is not None else "unpublished"
        ),
        publication_origin=(
            None if publication is None else publication.publication_origin.value
        ),
        published_at_ms=(
            None if publication is None else publication.published_at_ms
        ),
        publication_ready=item.readiness.ready,
        missing_fields=list(item.readiness.missing_fields),
        analysis_state=item.analysis_state,
        contributors=[
            AdminContributorResponse(
                login_key=contribution.login_key,
                sources=list(contribution.sources),
            )
            for contribution in getattr(item, "contributors", ())
        ],
    )


def _error(
    status_code: int,
    code: str,
    message: str,
    *,
    missing_fields: list[str] | None = None,
) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    if missing_fields is not None:
        body["missing_fields"] = missing_fields
    return JSONResponse(
        status_code=status_code,
        content={"error": body},
        headers=_NO_STORE_HEADERS,
    )
