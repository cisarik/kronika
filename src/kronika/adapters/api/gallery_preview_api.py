"""FastAPI routes for identity-only persistent gallery preview delivery."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, UUID4

from kronika.application.gallery_preview import (
    GalleryPreviewFailedError,
    GalleryPreviewNotFoundError,
    GalleryPreviewUnavailableError,
)
from kronika.application.media_content import (
    MEDIA_CONTENT_FAILED_MESSAGE,
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MEDIA_NOT_FOUND_MESSAGE,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.application.ports.media_repository import FrameNestMediaRepositoryError
from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_decision,
    load_approved_projection,
)
from kronika.domain.record_access import READ_APPROVED, READ_DENY
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.domain import MediaId, MediaLocationId

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
GALLERY_PREVIEW_NOT_FOUND_CODE = "GALLERY_PREVIEW_NOT_FOUND"
GALLERY_PREVIEW_UNAVAILABLE_CODE = "GALLERY_PREVIEW_UNAVAILABLE"
GALLERY_PREVIEW_FAILED_CODE = "GALLERY_PREVIEW_FAILED"
_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_READY_CACHE_CONTROL = "private, max-age=0, must-revalidate"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


@dataclass(frozen=True, slots=True)
class GalleryPreviewApiDependencies:
    """Injected dependencies for gallery preview routes."""

    preview_service: object
    catalog_available: Callable[[], bool]
    audience_policy: ContentAudiencePolicy | None = None


def create_gallery_preview_api_router(
    dependencies: GalleryPreviewApiDependencies,
) -> APIRouter:
    """Create the persistent gallery preview API router."""
    router = APIRouter()

    @router.get(
        "/api/media/{media_id}/locations/{location_id}/gallery-preview",
        response_model=None,
        responses={
            304: {"description": "Not Modified"},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_gallery_preview(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
    ) -> Response | JSONResponse:
        if not dependencies.catalog_available():
            return _error_response(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        parsed_media_id = MediaId.from_string(str(media_id))
        try:
            decision = content_audience_decision(
                request=request,
                media_id=parsed_media_id,
                policy=dependencies.audience_policy,
            )
        except ContentAudienceUnavailableError:
            return _error_response(
                500,
                GALLERY_PREVIEW_FAILED_CODE,
                MEDIA_CONTENT_FAILED_MESSAGE,
            )
        if decision == READ_DENY:
            return _error_response(
                404,
                GALLERY_PREVIEW_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        if decision == READ_APPROVED:
            try:
                projection = load_approved_projection(
                    policy=dependencies.audience_policy,
                    media_id=parsed_media_id,
                )
            except Exception:
                return _error_response(
                    500,
                    GALLERY_PREVIEW_FAILED_CODE,
                    MEDIA_CONTENT_FAILED_MESSAGE,
                )
            approved_locations = (
                set()
                if projection is None
                else {str(item.location_id) for item in projection.locations}
            )
            if str(location_id) not in approved_locations:
                return _error_response(
                    404,
                    GALLERY_PREVIEW_NOT_FOUND_CODE,
                    MEDIA_NOT_FOUND_MESSAGE,
                )
        try:
            opened = dependencies.preview_service.open_ready(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
            )
        except GalleryPreviewNotFoundError:
            return _error_response(
                404,
                GALLERY_PREVIEW_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        except GalleryPreviewUnavailableError:
            return _error_response(
                409,
                GALLERY_PREVIEW_UNAVAILABLE_CODE,
                MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
            )
        except (FrameNestLibraryRepositoryError, FrameNestMediaRepositoryError):
            return _error_response(
                500,
                GALLERY_PREVIEW_FAILED_CODE,
                MEDIA_CONTENT_FAILED_MESSAGE,
            )
        except GalleryPreviewFailedError:
            return _error_response(
                500,
                GALLERY_PREVIEW_FAILED_CODE,
                MEDIA_CONTENT_FAILED_MESSAGE,
            )
        except Exception:
            return _error_response(
                500,
                GALLERY_PREVIEW_FAILED_CODE,
                MEDIA_CONTENT_FAILED_MESSAGE,
            )
        try:
            headers = {
                "X-Content-Type-Options": "nosniff",
                "Cache-Control": _READY_CACHE_CONTROL,
                "ETag": opened.etag,
                "Content-Type": opened.media_type,
                "Content-Length": str(opened.byte_size),
                "Content-Disposition": "inline",
            }
            if request.headers.get("if-none-match") == opened.etag:
                return Response(status_code=304, headers=headers)
            return Response(
                content=opened.payload,
                status_code=200,
                media_type=opened.media_type,
                headers=headers,
            )
        finally:
            opened.close()

    return router


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
