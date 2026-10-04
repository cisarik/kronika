"""FastAPI routes for explicit media import from scan candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4, field_validator

from kronika.application.library_scan import (
    LibraryScanFailedError,
    LibraryScanNotFoundError,
    LibraryScanUnavailableError,
    default_scan_limits,
)
from kronika.application.media_import import (
    MediaImportCandidateUnavailableError,
    MediaImportFailedError,
    MediaImportResult,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.application.ports.media_repository import FrameNestMediaRepositoryError
from kronika.domain import LibraryId
from kronika.domain.media import MediaRelativePath

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
LIBRARY_NOT_FOUND_CODE = "LIBRARY_NOT_FOUND"
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_CODE = "MEDIA_IMPORT_CANDIDATE_UNAVAILABLE"
MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_MESSAGE = "Media import candidate is not available."
MEDIA_IMPORT_FAILED_CODE = "MEDIA_IMPORT_FAILED"
MEDIA_IMPORT_FAILED_MESSAGE = "Media import failed."


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class MediaImportRequest(BaseModel):
    relative_path: str

    @field_validator("relative_path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        MediaRelativePath(value)
        return value


class MediaImportMediaResponse(BaseModel):
    id: str
    kind: str
    created_at_ms: int
    updated_at_ms: int


class MediaImportLocationResponse(BaseModel):
    id: str
    media_id: str
    library_id: str
    relative_path: str
    availability: str
    observed_size_bytes: int | None
    observed_mtime_ns: int | None
    created_at_ms: int
    updated_at_ms: int


class MediaImportResponse(BaseModel):
    library_id: str
    status: str
    media: MediaImportMediaResponse
    location: MediaImportLocationResponse


@dataclass(frozen=True, slots=True)
class MediaImportApiDependencies:
    """Injected dependencies for explicit media import routes."""

    import_media: object
    catalog_available: Callable[[], bool]


def create_media_import_api_router(dependencies: MediaImportApiDependencies) -> APIRouter:
    """Create the explicit media import API router."""
    router = APIRouter()

    @router.post(
        "/api/libraries/{library_id}/media-imports",
        response_model=MediaImportResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def import_media(
        library_id: UUID4,
        request: MediaImportRequest,
    ) -> MediaImportResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        try:
            result = dependencies.import_media.execute(
                LibraryId.from_string(str(library_id)),
                MediaRelativePath(request.relative_path),
                default_scan_limits(),
            )
        except (FrameNestLibraryRepositoryError, FrameNestMediaRepositoryError):
            return _catalog_unavailable_response()
        except LibraryScanNotFoundError:
            return _error_response(404, LIBRARY_NOT_FOUND_CODE, LIBRARY_NOT_FOUND_MESSAGE)
        except (MediaImportCandidateUnavailableError, LibraryScanUnavailableError):
            return _error_response(
                409,
                MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_CODE,
                MEDIA_IMPORT_CANDIDATE_UNAVAILABLE_MESSAGE,
            )
        except (MediaImportFailedError, LibraryScanFailedError):
            return _error_response(500, MEDIA_IMPORT_FAILED_CODE, MEDIA_IMPORT_FAILED_MESSAGE)
        except Exception:
            return _error_response(500, MEDIA_IMPORT_FAILED_CODE, MEDIA_IMPORT_FAILED_MESSAGE)
        return _media_import_response(result)

    return router


def _catalog_unavailable_response() -> JSONResponse:
    return _error_response(
        503,
        CATALOG_UNAVAILABLE_CODE,
        CATALOG_UNAVAILABLE_MESSAGE,
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )


def _media_import_response(result: MediaImportResult | object) -> MediaImportResponse:
    media = result.media
    location = result.location
    return MediaImportResponse(
        library_id=result.library_id.to_string(),
        status="created" if result.created else "already_imported",
        media=MediaImportMediaResponse(
            id=media.id.to_string(),
            kind=media.kind.value,
            created_at_ms=media.created_at_ms,
            updated_at_ms=media.updated_at_ms,
        ),
        location=MediaImportLocationResponse(
            id=location.id.to_string(),
            media_id=location.media_id.to_string(),
            library_id=location.library_id.to_string(),
            relative_path=location.relative_path.value,
            availability=location.availability.value,
            observed_size_bytes=location.observed_size_bytes,
            observed_mtime_ns=location.observed_mtime_ns,
            created_at_ms=location.created_at_ms,
            updated_at_ms=location.updated_at_ms,
        ),
    )
