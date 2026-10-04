"""FastAPI routes for the read-only local library browser."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4

from kronika.application.library_scan import (
    LibraryScanCandidate,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanPreviewResult,
    LibraryScanSummary,
    LibraryScanUnavailableError,
    default_scan_limits,
)
from kronika.application.ports.library_repository import (
    FrameNestLibraryRepositoryError,
    LibraryRepository,
)
from kronika.domain import Library, LibraryId

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
LIBRARY_NOT_FOUND_CODE = "LIBRARY_NOT_FOUND"
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
LIBRARY_UNAVAILABLE_CODE = "LIBRARY_UNAVAILABLE"
LIBRARY_UNAVAILABLE_MESSAGE = "Library scan preview is not available."
SCAN_FAILED_CODE = "SCAN_FAILED"
SCAN_FAILED_MESSAGE = "Library scan failed."


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class LibraryListItem(BaseModel):
    id: str
    display_name: str
    path_flavor: str


class LibraryListResponse(BaseModel):
    libraries: list[LibraryListItem]


class ScanLimitsResponse(BaseModel):
    max_entries: int
    max_candidates: int


class ScanSummaryResponse(BaseModel):
    entries_seen: int
    directories_seen: int
    regular_files_seen: int
    candidate_files_seen: int
    candidate_bytes_seen: int
    skipped_hidden_entries: int
    skipped_symlink_entries: int
    skipped_other_entries: int
    inaccessible_entries: int
    truncated: bool
    candidates_truncated: bool


class ScanCandidateResponse(BaseModel):
    relative_path: str
    kind: str
    extension: str
    size_bytes: int


class ScanPreviewResponse(BaseModel):
    library_id: str
    limits: ScanLimitsResponse
    summary: ScanSummaryResponse
    candidates: list[ScanCandidateResponse]


@dataclass(frozen=True, slots=True)
class LibraryApiDependencies:
    """Injected dependencies for read-only library API routes."""

    repository: LibraryRepository
    scan_preview: object
    catalog_available: Callable[[], bool]


def create_library_api_router(dependencies: LibraryApiDependencies) -> APIRouter:
    """Create the read-only library API router."""
    router = APIRouter()

    @router.get(
        "/api/libraries",
        response_model=LibraryListResponse,
        responses={503: {"model": ErrorResponse}},
    )
    def list_libraries() -> LibraryListResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _error_response(
                503,
                CATALOG_UNAVAILABLE_CODE,
                CATALOG_UNAVAILABLE_MESSAGE,
            )
        try:
            libraries = dependencies.repository.list_all()
        except Exception as exc:
            if isinstance(exc, FrameNestLibraryRepositoryError):
                return _catalog_unavailable_response()
            return _catalog_unavailable_response()
        return LibraryListResponse(libraries=[_library_item(library) for library in libraries])

    @router.post(
        "/api/libraries/{library_id}/scan-preview",
        response_model=ScanPreviewResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def scan_library(library_id: UUID4) -> ScanPreviewResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        try:
            result = dependencies.scan_preview.execute(
                LibraryId.from_string(str(library_id)),
                default_scan_limits(),
            )
        except FrameNestLibraryRepositoryError:
            return _catalog_unavailable_response()
        except LibraryScanNotFoundError:
            return _error_response(404, LIBRARY_NOT_FOUND_CODE, LIBRARY_NOT_FOUND_MESSAGE)
        except LibraryScanUnavailableError:
            return _error_response(
                409, LIBRARY_UNAVAILABLE_CODE, LIBRARY_UNAVAILABLE_MESSAGE
            )
        except LibraryScanFailedError:
            return _error_response(500, SCAN_FAILED_CODE, SCAN_FAILED_MESSAGE)
        except Exception:
            return _error_response(500, SCAN_FAILED_CODE, "Library scan failed.")
        return _scan_preview_response(result)

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


def _library_item(library: Library) -> LibraryListItem:
    return LibraryListItem(
        id=library.id.to_string(),
        display_name=library.display_name,
        path_flavor=library.root.flavor.value,
    )


def _limits_response(limits: LibraryScanLimits) -> ScanLimitsResponse:
    return ScanLimitsResponse(
        max_entries=limits.max_entries,
        max_candidates=limits.max_candidates,
    )


def _summary_response(summary: LibraryScanSummary) -> ScanSummaryResponse:
    return ScanSummaryResponse(
        entries_seen=summary.entries_seen,
        directories_seen=summary.directories_seen,
        regular_files_seen=summary.regular_files_seen,
        candidate_files_seen=summary.candidate_files_seen,
        candidate_bytes_seen=summary.candidate_bytes_seen,
        skipped_hidden_entries=summary.skipped_hidden_entries,
        skipped_symlink_entries=summary.skipped_symlink_entries,
        skipped_other_entries=summary.skipped_other_entries,
        inaccessible_entries=summary.inaccessible_entries,
        truncated=summary.truncated,
        candidates_truncated=summary.candidates_truncated,
    )


def _candidate_response(candidate: LibraryScanCandidate) -> ScanCandidateResponse:
    return ScanCandidateResponse(
        relative_path=candidate.relative_path,
        kind=candidate.kind.value,
        extension=candidate.extension,
        size_bytes=candidate.size_bytes,
    )


def _scan_preview_response(result: LibraryScanPreviewResult | object) -> ScanPreviewResponse:
    return ScanPreviewResponse(
        library_id=result.library_id.to_string(),
        limits=_limits_response(result.limits),
        summary=_summary_response(result.summary),
        candidates=[_candidate_response(candidate) for candidate in result.candidates],
    )
