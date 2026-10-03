"""FastAPI routes for secure read-only local media content delivery."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, UUID4

from framenest.application.media_content import (
    MEDIA_CONTENT_FAILED_MESSAGE,
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MEDIA_NOT_FOUND_MESSAGE,
    MediaContentFailedError,
    MediaContentNotFoundError,
    MediaContentUnavailableError,
    ResolvedMediaContent,
)
from framenest.application.ports.library_repository import (
    FrameNestLibraryRepositoryError,
)
from framenest.application.ports.media_repository import (
    FrameNestMediaRepositoryError,
)
from framenest.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_decision,
    load_approved_projection,
)
from framenest.domain.record_access import READ_APPROVED, READ_DENY
from framenest.application.content_publication import ContentAudiencePolicy
from framenest.domain import MediaId, MediaLocationId

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_CONTENT_NOT_FOUND_CODE = "MEDIA_CONTENT_NOT_FOUND"
MEDIA_CONTENT_UNAVAILABLE_CODE = "MEDIA_CONTENT_UNAVAILABLE"
MEDIA_CONTENT_FAILED_CODE = "MEDIA_CONTENT_FAILED"
RANGE_NOT_SATISFIABLE_CODE = "RANGE_NOT_SATISFIABLE"
RANGE_NOT_SATISFIABLE_MESSAGE = "Requested range is not satisfiable."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}
_FALLBACK_DOWNLOAD_FILENAME = "kronika-media.bin"


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


@dataclass(frozen=True, slots=True)
class MediaContentApiDependencies:
    """Injected dependencies for secure media content routes."""

    resolve_content: object
    catalog_available: Callable[[], bool]
    audience_policy: ContentAudiencePolicy | None = None


def create_media_content_api_router(
    dependencies: MediaContentApiDependencies,
) -> APIRouter:
    """Create the secure read-only media content API router."""
    router = APIRouter()

    @router.get(
        "/api/media/{media_id}/locations/{location_id}/content",
        response_model=None,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            416: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_media_content(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        resolved_or_error = _resolve_media_content(
            dependencies,
            media_id,
            location_id,
            request,
        )
        if isinstance(resolved_or_error, JSONResponse):
            return resolved_or_error
        resolved = resolved_or_error

        range_header = request.headers.get("range")
        if range_header is None:
            return _full_content_response(resolved)
        parsed = _parse_byte_range(range_header, resolved.byte_size)
        if parsed is None:
            resolved.close()
            return _range_not_satisfiable_response(resolved.byte_size)
        start, end = parsed
        return _partial_content_response(resolved, start, end)

    @router.get(
        "/api/media/{media_id}/locations/{location_id}/download",
        response_model=None,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def download_media_content(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        resolved_or_error = _resolve_media_content(
            dependencies,
            media_id,
            location_id,
            request,
        )
        if isinstance(resolved_or_error, JSONResponse):
            return resolved_or_error
        return _download_content_response(resolved_or_error)

    return router


def _resolve_media_content(
    dependencies: MediaContentApiDependencies,
    media_id: UUID4,
    location_id: UUID4,
    request: Request,
) -> ResolvedMediaContent | JSONResponse:
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
            MEDIA_CONTENT_FAILED_CODE,
            MEDIA_CONTENT_FAILED_MESSAGE,
        )
    if decision == READ_DENY:
        return _error_response(
            404,
            MEDIA_CONTENT_NOT_FOUND_CODE,
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
                MEDIA_CONTENT_FAILED_CODE,
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
                MEDIA_CONTENT_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
    try:
        return dependencies.resolve_content.execute(
            MediaId.from_string(str(media_id)),
            MediaLocationId.from_string(str(location_id)),
        )
    except MediaContentNotFoundError:
        return _error_response(
            404,
            MEDIA_CONTENT_NOT_FOUND_CODE,
            MEDIA_NOT_FOUND_MESSAGE,
        )
    except MediaContentUnavailableError:
        return _error_response(
            409,
            MEDIA_CONTENT_UNAVAILABLE_CODE,
            MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
        )
    except (
        FrameNestMediaRepositoryError,
        FrameNestLibraryRepositoryError,
    ):
        return _error_response(
            500,
            MEDIA_CONTENT_FAILED_CODE,
            MEDIA_CONTENT_FAILED_MESSAGE,
        )
    except MediaContentFailedError:
        return _error_response(
            500,
            MEDIA_CONTENT_FAILED_CODE,
            MEDIA_CONTENT_FAILED_MESSAGE,
        )
    except Exception:
        return _error_response(
            500,
            MEDIA_CONTENT_FAILED_CODE,
            MEDIA_CONTENT_FAILED_MESSAGE,
        )


def _stream_with_finalization(
    resolved: ResolvedMediaContent,
    start: int,
    length: int | None,
) -> Iterator[bytes]:
    try:
        yield from resolved.stream(start, length)
    finally:
        resolved.close()


def _full_content_response(resolved: ResolvedMediaContent) -> StreamingResponse:
    return StreamingResponse(
        content=_stream_with_finalization(resolved, 0, None),
        status_code=200,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
            "Content-Type": resolved.media_type,
            "Content-Length": str(resolved.byte_size),
        },
    )


def _download_content_response(resolved: ResolvedMediaContent) -> StreamingResponse:
    return StreamingResponse(
        content=_stream_with_finalization(resolved, 0, None),
        status_code=200,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Content-Type": resolved.media_type,
            "Content-Length": str(resolved.byte_size),
            "Content-Disposition": f'attachment; filename="{_header_safe_filename(resolved.download_filename)}"',
        },
    )


def _header_safe_filename(value: object) -> str:
    if not isinstance(value, str) or not value:
        return _FALLBACK_DOWNLOAD_FILENAME
    if any(character in value for character in ('"', "\\", "\r", "\n", ";", "/")):
        return _FALLBACK_DOWNLOAD_FILENAME
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return _FALLBACK_DOWNLOAD_FILENAME
    return value


def _partial_content_response(
    resolved: ResolvedMediaContent,
    start: int,
    end: int,
) -> StreamingResponse:
    length = end - start + 1
    return StreamingResponse(
        content=_stream_with_finalization(resolved, start, length),
        status_code=206,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
            "Accept-Ranges": "bytes",
            "Content-Type": resolved.media_type,
            "Content-Length": str(length),
            "Content-Range": f"bytes {start}-{end}/{resolved.byte_size}",
        },
    )


def _range_not_satisfiable_response(byte_size: int) -> JSONResponse:
    return JSONResponse(
        status_code=416,
        content={
            "error": {
                "code": RANGE_NOT_SATISFIABLE_CODE,
                "message": RANGE_NOT_SATISFIABLE_MESSAGE,
            }
        },
        headers={
            "Cache-Control": "no-store",
            "Content-Range": f"bytes */{byte_size}",
        },
    )


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )


def _parse_byte_range(header: str, size: int) -> tuple[int, int] | None:
    """Parse a single RFC-style byte range.

    Returns ``(start, end)`` inclusive when satisfiable, or ``None`` when
    the range is malformed or unsatisfiable.
    """
    if not header.lower().startswith("bytes="):
        return None
    spec = header[len("bytes="):].strip()
    if not spec or "," in spec:
        return None
    if "-" not in spec:
        return None
    parts = spec.split("-", 1)
    if len(parts) != 2:
        return None
    start_str, end_str = parts[0], parts[1]
    if not start_str and not end_str:
        return None
    if not start_str:
        suffix = _parse_non_negative_int(end_str)
        if suffix is None or suffix == 0:
            return None
        if size == 0:
            return None
        actual = min(suffix, size)
        start = size - actual
        end = size - 1
    elif not end_str:
        start = _parse_non_negative_int(start_str)
        if start is None:
            return None
        end = size - 1
    else:
        start = _parse_non_negative_int(start_str)
        end = _parse_non_negative_int(end_str)
        if start is None or end is None:
            return None
        if start > end:
            return None
    if start < 0 or start >= size:
        return None
    end = min(end, size - 1)
    return (start, end)


def _parse_non_negative_int(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = int(value)
    except ValueError:
        return None
    if parsed < 0 or str(parsed) != value:
        return None
    return parsed
