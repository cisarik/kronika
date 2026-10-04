"""FastAPI routes for explicit local media-analysis preview."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4

from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisNotFoundError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    candidate_kind_for_relative_path,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.domain import LibraryId

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
LIBRARY_NOT_FOUND_CODE = "LIBRARY_NOT_FOUND"
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
INVALID_MEDIA_PATH_CODE = "INVALID_MEDIA_PATH"
INVALID_MEDIA_PATH_MESSAGE = "Invalid media relative path."
MEDIA_ANALYSIS_UNAVAILABLE_CODE = "MEDIA_ANALYSIS_UNAVAILABLE"
MEDIA_ANALYSIS_UNAVAILABLE_MESSAGE = "Local media analysis is not available."
MEDIA_ANALYSIS_FAILED_CODE = "MEDIA_ANALYSIS_FAILED"
MEDIA_ANALYSIS_FAILED_MESSAGE = "Local media analysis failed."

MAX_RELATIVE_PATH_LENGTH = 4096
NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class MediaAnalysisPreviewRequest(BaseModel):
    relative_path: str


class TechnicalMetadataResponse(BaseModel):
    duration_ms: int | None
    width: int
    height: int
    video_codec: str
    container_formats: list[str]
    has_audio: bool


class RepresentativeFrameResponse(BaseModel):
    timestamp_ms: int
    mime_type: str
    sha256: str
    byte_size: int
    payload_base64: str


class MediaAnalysisPreviewResponse(BaseModel):
    library_id: str
    relative_path: str
    candidate_kind: str
    technical_metadata: TechnicalMetadataResponse
    requested_frame_count: int
    representative_frames: list[RepresentativeFrameResponse]
    warnings: list[str]


@dataclass(frozen=True, slots=True)
class MediaAnalysisApiDependencies:
    """Injected dependencies for explicit local media-analysis API routes."""

    prepare_preview: object
    catalog_available: Callable[[], bool]


def create_media_analysis_api_router(dependencies: MediaAnalysisApiDependencies) -> APIRouter:
    """Create the explicit local media-analysis preview router."""
    router = APIRouter()

    @router.post(
        "/api/libraries/{library_id}/media-analysis-preview",
        response_model=MediaAnalysisPreviewResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def preview_media_analysis(
        library_id: UUID4,
        request: MediaAnalysisPreviewRequest,
    ) -> MediaAnalysisPreviewResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        try:
            relative_path = _media_relative_path_from_request(request.relative_path)
            result = dependencies.prepare_preview.execute(
                LibraryId.from_string(str(library_id)),
                relative_path,
            )
        except FrameNestLibraryRepositoryError:
            return _catalog_unavailable_response()
        except MediaAnalysisNotFoundError:
            return _error_response(404, LIBRARY_NOT_FOUND_CODE, LIBRARY_NOT_FOUND_MESSAGE)
        except FrameNestMediaAnalysisError:
            return _invalid_media_path_response()
        except MediaAnalysisUnavailableError:
            return _error_response(
                409,
                MEDIA_ANALYSIS_UNAVAILABLE_CODE,
                MEDIA_ANALYSIS_UNAVAILABLE_MESSAGE,
            )
        except MediaAnalysisFailedError:
            return _error_response(500, MEDIA_ANALYSIS_FAILED_CODE, MEDIA_ANALYSIS_FAILED_MESSAGE)
        except Exception:
            return _error_response(500, MEDIA_ANALYSIS_FAILED_CODE, MEDIA_ANALYSIS_FAILED_MESSAGE)
        return _json_response(_preview_response(library_id=LibraryId.from_string(str(library_id)), result=result))

    return router


def _media_relative_path_from_request(value: object) -> MediaRelativePath:
    if not isinstance(value, str) or len(value) > MAX_RELATIVE_PATH_LENGTH:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    relative_path = MediaRelativePath(value)
    candidate_kind_for_relative_path(relative_path)
    return relative_path


def _catalog_unavailable_response() -> JSONResponse:
    return _error_response(
        503,
        CATALOG_UNAVAILABLE_CODE,
        CATALOG_UNAVAILABLE_MESSAGE,
    )


def _invalid_media_path_response() -> JSONResponse:
    return _error_response(
        422,
        INVALID_MEDIA_PATH_CODE,
        INVALID_MEDIA_PATH_MESSAGE,
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=NO_STORE_HEADERS,
    )


def _json_response(response: MediaAnalysisPreviewResponse) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=response.model_dump(),
        headers=NO_STORE_HEADERS,
    )


def _preview_response(
    *,
    library_id: LibraryId,
    result: PreparedAnalysisResult,
) -> MediaAnalysisPreviewResponse:
    metadata = result.technical_metadata
    return MediaAnalysisPreviewResponse(
        library_id=library_id.to_string(),
        relative_path=result.relative_path.value,
        candidate_kind=result.candidate_kind.value,
        technical_metadata=TechnicalMetadataResponse(
            duration_ms=metadata.duration_ms,
            width=metadata.width,
            height=metadata.height,
            video_codec=metadata.video_codec,
            container_formats=list(metadata.container_formats),
            has_audio=metadata.has_audio,
        ),
        requested_frame_count=result.requested_frame_count,
        representative_frames=[
            RepresentativeFrameResponse(
                timestamp_ms=frame.timestamp_ms,
                mime_type=frame.mime_type,
                sha256=frame.sha256,
                byte_size=frame.byte_size,
                payload_base64=base64.b64encode(frame.payload).decode("ascii"),
            )
            for frame in result.representative_frames
        ],
        warnings=list(result.warnings),
    )
