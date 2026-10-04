"""FastAPI routes for resumable quarantine upload transport."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, UUID4, field_validator

from kronika.application.upload_transport import (
    UploadBodyLengthMismatchError,
    UploadCapabilityNotConfiguredError,
    UploadChunkTooLargeError,
    UploadConcurrencyConflictError,
    UploadDuplicateResolution,
    UploadInsufficientStorageError,
    UploadInvalidMetadataError,
    UploadInvalidOffsetError,
    UploadOffsetConflictTransportError,
    UploadQuarantineStateInconsistentError,
    UploadQuarantineUnavailableError,
    UploadSessionExpiredError,
    UploadSessionNotFoundTransportError,
    UploadSessionSnapshot,
    UploadSessionStateConflictError,
    UploadTooLargeError,
)
from kronika.adapters.api.tailscale_ingress import (
    CHANNEL_TAILSCALE,
    SCOPE_IDENTITY,
    SCOPE_INGRESS_CHANNEL,
)
from kronika.domain.identity_access import (
    CAPABILITY_UPLOAD_MANAGE,
    CAPABILITY_UPLOAD_SUBMIT,
    IdentityContext,
)
from kronika.domain.uploads import (
    FrameNestUploadSessionError,
    UploadDuplicateResolutionMode,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
)
from kronika.structured_logging import get_logger

UPLOAD_CAPABILITY_NOT_CONFIGURED = "UPLOAD_CAPABILITY_NOT_CONFIGURED"
UPLOAD_SESSION_NOT_FOUND = "UPLOAD_SESSION_NOT_FOUND"
INVALID_UPLOAD_METADATA = "INVALID_UPLOAD_METADATA"
UPLOAD_TOO_LARGE = "UPLOAD_TOO_LARGE"
UPLOAD_CHUNK_TOO_LARGE = "UPLOAD_CHUNK_TOO_LARGE"
INVALID_UPLOAD_CONTENT_TYPE = "INVALID_UPLOAD_CONTENT_TYPE"
INVALID_UPLOAD_CONTENT_LENGTH = "INVALID_UPLOAD_CONTENT_LENGTH"
INVALID_UPLOAD_OFFSET = "INVALID_UPLOAD_OFFSET"
UPLOAD_OFFSET_CONFLICT = "UPLOAD_OFFSET_CONFLICT"
UPLOAD_SESSION_STATE_CONFLICT = "UPLOAD_SESSION_STATE_CONFLICT"
UPLOAD_SESSION_EXPIRED = "UPLOAD_SESSION_EXPIRED"
INSUFFICIENT_QUARANTINE_STORAGE = "INSUFFICIENT_QUARANTINE_STORAGE"
UPLOAD_BODY_LENGTH_MISMATCH = "UPLOAD_BODY_LENGTH_MISMATCH"
QUARANTINE_STORAGE_UNAVAILABLE = "QUARANTINE_STORAGE_UNAVAILABLE"
QUARANTINE_STATE_INCONSISTENT = "QUARANTINE_STATE_INCONSISTENT"
UPLOAD_CONCURRENCY_CONFLICT = "UPLOAD_CONCURRENCY_CONFLICT"
UPLOAD_ORIGIN_FORBIDDEN = "UPLOAD_ORIGIN_FORBIDDEN"

_PATCH_MEDIA_TYPE = "application/offset+octet-stream"
LOGGER = get_logger("upload_api")


class ErrorBody(BaseModel):
    code: str
    message: str
    current_offset: int | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class UploadCreateRequest(BaseModel):
    display_filename: str
    declared_size_bytes: int

    @field_validator("display_filename")
    @classmethod
    def validate_display_filename(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("invalid upload metadata")
        return value


class UploadSessionResponse(BaseModel):
    id: str
    state: str
    display_filename: str
    declared_size_bytes: int
    received_size_bytes: int
    expires_at: int
    failure_code: str | None = None
    media_id: str | None = None


class UploadDuplicateResolutionResponse(BaseModel):
    id: str
    state: str
    declared_size_bytes: int
    received_size_bytes: int
    expires_at: int
    failure_code: str | None = None


class UploadDuplicateResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resolution: Literal["keep_separate", "discard"]


class UploadCapabilityResponse(BaseModel):
    uploads_enabled: bool
    max_total_size_bytes: int
    max_chunk_size_bytes: int
    session_ttl_seconds: int


@dataclass(frozen=True, slots=True)
class UploadApiDependencies:
    """Injected dependencies for quarantine upload routes."""

    transport: object
    validation_coordinator: object | None = None
    publication_coordinator: object | None = None
    publication_repository: object | None = None


def create_upload_api_router(dependencies: UploadApiDependencies) -> APIRouter:
    """Create the upload transport API router."""
    router = APIRouter()

    @router.post(
        "/api/uploads",
        response_model=UploadSessionResponse,
        status_code=201,
        responses={
            400: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            507: {"model": ErrorResponse},
        },
    )
    def create_upload(
        request: Request,
        payload: UploadCreateRequest,
    ) -> UploadSessionResponse | JSONResponse:
        origin_error = _reject_cross_origin_mutation(request)
        if origin_error is not None:
            return origin_error
        try:
            capability = dependencies.transport.get_capability()
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                UPLOAD_CAPABILITY_NOT_CONFIGURED,
                "Upload capability is not configured.",
            )
        if not capability.uploads_enabled:
            return _error_response(
                503,
                UPLOAD_CAPABILITY_NOT_CONFIGURED,
                "Upload capability is not configured.",
            )
        identity_error = _require_upload_identity(request)
        if identity_error is not None:
            return identity_error
        created_by_login_key, duplicate_resolution_mode = _creation_ownership_fields(
            request
        )
        try:
            snapshot = dependencies.transport.create_session(
                display_filename=payload.display_filename,
                declared_size_bytes=payload.declared_size_bytes,
                created_by_login_key=created_by_login_key,
                duplicate_resolution_mode=duplicate_resolution_mode,
            )
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        return _enriched_snapshot_response(dependencies, snapshot)

    @router.get(
        "/api/uploads/capability",
        response_model=UploadCapabilityResponse,
        responses={503: {"model": ErrorResponse}},
    )
    def upload_capability(request: Request) -> UploadCapabilityResponse | JSONResponse:
        identity_error = _require_upload_identity(request)
        if identity_error is not None:
            return identity_error
        try:
            capability = dependencies.transport.get_capability()
        except Exception:
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Upload capability is unavailable.",
            )
        return UploadCapabilityResponse(
            uploads_enabled=capability.uploads_enabled,
            max_total_size_bytes=capability.max_total_size_bytes,
            max_chunk_size_bytes=capability.max_chunk_size_bytes,
            session_ttl_seconds=capability.session_ttl_seconds,
        )

    @router.get(
        "/api/uploads/{upload_id}",
        response_model=UploadSessionResponse,
        responses={404: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def upload_status(
        upload_id: UUID4, request: Request
    ) -> UploadSessionResponse | JSONResponse:
        access_error = _enforce_upload_session_access(
            dependencies.transport, request, upload_id
        )
        if access_error is not None:
            return access_error
        try:
            snapshot = dependencies.transport.get_status(_session_id(upload_id))
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        return _enriched_snapshot_response(dependencies, snapshot)

    @router.patch(
        "/api/uploads/{upload_id}",
        response_model=UploadSessionResponse,
        responses={
            400: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            413: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
            507: {"model": ErrorResponse},
        },
    )
    async def patch_upload(
        upload_id: UUID4,
        request: Request,
    ) -> UploadSessionResponse | JSONResponse:
        origin_error = _reject_cross_origin_mutation(request)
        if origin_error is not None:
            return origin_error
        access_error = _enforce_upload_session_access(
            dependencies.transport, request, upload_id
        )
        if access_error is not None:
            return access_error
        content_type = _parse_upload_content_type(request)
        if isinstance(content_type, JSONResponse):
            return content_type
        content_length = _parse_content_length(request)
        if isinstance(content_length, JSONResponse):
            return content_length
        upload_offset = _parse_upload_offset(request)
        if isinstance(upload_offset, JSONResponse):
            return upload_offset
        try:
            snapshot = await dependencies.transport.receive_chunk(
                _session_id(upload_id),
                upload_offset=upload_offset,
                content_length=content_length,
                body=request.stream(),
            )
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        return _enriched_snapshot_response(dependencies, snapshot)

    @router.post(
        "/api/uploads/{upload_id}/complete",
        response_model=UploadSessionResponse,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def complete_upload(
        upload_id: UUID4,
        request: Request,
    ) -> UploadSessionResponse | JSONResponse:
        origin_error = _reject_cross_origin_mutation(request)
        if origin_error is not None:
            return origin_error
        access_error = _enforce_upload_session_access(
            dependencies.transport, request, upload_id
        )
        if access_error is not None:
            return access_error
        try:
            snapshot = await dependencies.transport.complete(_session_id(upload_id))
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        if snapshot.state == "received":
            _notify_validation_coordinator(dependencies.validation_coordinator)
        return _enriched_snapshot_response(dependencies, snapshot)

    @router.post(
        "/api/uploads/{upload_id}/duplicate-resolution",
        response_model=UploadDuplicateResolutionResponse,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def resolve_upload_duplicate(
        upload_id: UUID4,
        request: Request,
        payload: UploadDuplicateResolutionRequest,
    ) -> UploadDuplicateResolutionResponse | JSONResponse:
        origin_error = _reject_cross_origin_mutation(request)
        if origin_error is not None:
            return origin_error
        access_error = _enforce_upload_session_access(
            dependencies.transport, request, upload_id
        )
        if access_error is not None:
            return access_error
        try:
            snapshot = await dependencies.transport.resolve_duplicate(
                _session_id(upload_id),
                UploadDuplicateResolution(payload.resolution),
            )
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        if snapshot.state == "publish_pending":
            _notify_publication_coordinator(dependencies.publication_coordinator)
        return _duplicate_resolution_response(snapshot)

    @router.delete(
        "/api/uploads/{upload_id}",
        response_model=UploadSessionResponse,
        responses={
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    async def cancel_upload(
        upload_id: UUID4,
        request: Request,
    ) -> UploadSessionResponse | JSONResponse:
        origin_error = _reject_cross_origin_mutation(request)
        if origin_error is not None:
            return origin_error
        access_error = _enforce_upload_session_access(
            dependencies.transport, request, upload_id
        )
        if access_error is not None:
            return access_error
        try:
            snapshot = await dependencies.transport.cancel(_session_id(upload_id))
        except Exception as exc:
            mapped = _map_error(exc)
            if mapped is not None:
                return mapped
            return _error_response(
                503,
                QUARANTINE_STORAGE_UNAVAILABLE,
                "Quarantine storage is unavailable.",
            )
        return _enriched_snapshot_response(dependencies, snapshot)

    return router


def _session_id(upload_id: UUID4) -> UploadSessionId:
    try:
        return UploadSessionId.from_string(str(upload_id))
    except FrameNestUploadSessionError:
        raise UploadSessionNotFoundTransportError("upload session not found") from None


def _require_upload_identity(request: Request) -> JSONResponse | None:
    identity = _request_identity(request)
    if identity is None or not identity.login_key:
        return _error_response(
            401,
            "IDENTITY_REQUIRED",
            "A verified identity is required.",
        )
    if not (
        identity.has_capability(CAPABILITY_UPLOAD_SUBMIT)
        or identity.has_capability(CAPABILITY_UPLOAD_MANAGE)
    ):
        return _error_response(
            403,
            "CAPABILITY_DENIED",
            "The verified identity is not authorized for this action.",
        )
    return None


def _request_identity(request: Request) -> IdentityContext | None:
    identity = request.scope.get(SCOPE_IDENTITY)
    if isinstance(identity, IdentityContext):
        return identity
    return None


def _creation_ownership_fields(
    request: Request,
) -> tuple[str | None, UploadDuplicateResolutionMode]:
    identity = _request_identity(request)
    if identity is None:
        return None, UploadDuplicateResolutionMode.EXPLICIT
    if identity.has_capability(CAPABILITY_UPLOAD_MANAGE):
        return identity.login_key, UploadDuplicateResolutionMode.EXPLICIT
    return identity.login_key, UploadDuplicateResolutionMode.SILENT_KEEP_SEPARATE


def may_access_upload_session(
    session: UploadSession,
    identity: IdentityContext | None,
) -> bool:
    """Return whether the request identity may observe or mutate the session."""
    if identity is None or not identity.login_key:
        return False
    if identity.has_capability(CAPABILITY_UPLOAD_MANAGE):
        return True
    if session.created_by_login_key is None:
        return False
    return session.created_by_login_key == identity.login_key


def _enforce_upload_session_access(
    transport: object,
    request: Request,
    upload_id: UUID4,
) -> JSONResponse | None:
    identity = _request_identity(request)
    if identity is None or not identity.login_key:
        return _error_response(
            404,
            UPLOAD_SESSION_NOT_FOUND,
            "Upload session not found.",
        )
    try:
        session = transport.load_session(_session_id(upload_id))
    except Exception as exc:
        mapped = _map_error(exc)
        if mapped is not None:
            return mapped
        return _error_response(
            503,
            QUARANTINE_STORAGE_UNAVAILABLE,
            "Quarantine storage is unavailable.",
        )
    if may_access_upload_session(session, identity):
        return None
    return _error_response(
        404,
        UPLOAD_SESSION_NOT_FOUND,
        "Upload session not found.",
    )


def _parse_content_length(request: Request) -> int | JSONResponse:
    raw = _single_raw_header_value(request, b"content-length")
    if raw is None or not _is_ascii_digit_sequence(raw):
        return _error_response(
            400,
            INVALID_UPLOAD_CONTENT_LENGTH,
            "Missing or invalid upload content length.",
        )
    parsed = int(raw)
    if parsed <= 0:
        return _error_response(
            400,
            INVALID_UPLOAD_CONTENT_LENGTH,
            "Missing or invalid upload content length.",
        )
    return parsed


def _parse_upload_content_type(request: Request) -> str | JSONResponse:
    raw = _single_raw_header_value(request, b"content-type")
    if raw is None:
        return _invalid_upload_content_type_response()
    try:
        value = raw.decode("ascii").strip().lower()
    except UnicodeDecodeError:
        return _invalid_upload_content_type_response()
    if value != _PATCH_MEDIA_TYPE:
        return _invalid_upload_content_type_response()
    return value


def _parse_upload_offset(request: Request) -> int | JSONResponse:
    raw = _single_raw_header_value(request, b"upload-offset")
    if raw is None or not _is_ascii_digit_sequence(raw):
        return _error_response(400, INVALID_UPLOAD_OFFSET, "Invalid upload offset.")
    return int(raw)


def _single_raw_header_value(request: Request, name: bytes) -> bytes | None:
    matches = [
        value
        for raw_name, value in request.scope.get("headers", ())
        if raw_name.lower() == name
    ]
    if len(matches) != 1:
        return None
    return matches[0]


def _is_ascii_digit_sequence(value: bytes) -> bool:
    return bool(value) and all(ord("0") <= byte <= ord("9") for byte in value)


def _invalid_upload_content_type_response() -> JSONResponse:
    return _error_response(
        400,
        INVALID_UPLOAD_CONTENT_TYPE,
        "Invalid upload content type.",
    )


def _notify_validation_coordinator(coordinator: object | None) -> None:
    if coordinator is None:
        return
    try:
        notify = getattr(coordinator, "notify")
        notify()
    except Exception:
        LOGGER.emit(
            level="WARNING",
            event="upload_validation_notification_failed",
            operation="complete_upload",
            error_code="UPLOAD_VALIDATION_NOTIFICATION_FAILED",
            retryable=True,
        )


def _notify_publication_coordinator(coordinator: object | None) -> None:
    if coordinator is None:
        return
    try:
        notify = getattr(coordinator, "notify")
        notify()
    except Exception:
        LOGGER.emit(
            level="WARNING",
            event="upload_publication_notification_failed",
            operation="resolve_upload_duplicate",
            error_code="UPLOAD_PUBLICATION_NOTIFICATION_FAILED",
            retryable=True,
        )


def _reject_cross_origin_mutation(request: Request) -> JSONResponse | None:
    if request.scope.get(SCOPE_INGRESS_CHANNEL) == CHANNEL_TAILSCALE:
        # The trusted-ingress middleware already enforces the exact external
        # Origin and the FrameNest mutation header for this channel; the
        # loopback scheme comparison below cannot evaluate UDS requests.
        return None
    origin = request.headers.get("origin")
    if origin is None:
        return None
    expected = f"{request.url.scheme}://{request.headers.get('host', request.url.netloc)}"
    if origin == expected:
        return None
    return _error_response(
        403,
        UPLOAD_ORIGIN_FORBIDDEN,
        "Cross-origin upload mutation is forbidden.",
    )


def _map_error(exc: Exception) -> JSONResponse | None:
    if isinstance(exc, UploadCapabilityNotConfiguredError):
        return _error_response(
            503,
            UPLOAD_CAPABILITY_NOT_CONFIGURED,
            "Upload capability is not configured.",
        )
    if isinstance(exc, UploadSessionNotFoundTransportError):
        return _error_response(404, UPLOAD_SESSION_NOT_FOUND, "Upload session not found.")
    if isinstance(exc, UploadInvalidMetadataError):
        return _error_response(400, INVALID_UPLOAD_METADATA, "Invalid upload metadata.")
    if isinstance(exc, UploadTooLargeError):
        return _error_response(413, UPLOAD_TOO_LARGE, "Upload is too large.")
    if isinstance(exc, UploadChunkTooLargeError):
        return _error_response(413, UPLOAD_CHUNK_TOO_LARGE, "Upload chunk is too large.")
    if isinstance(exc, UploadInvalidOffsetError):
        return _error_response(400, INVALID_UPLOAD_OFFSET, "Invalid upload offset.")
    if isinstance(exc, UploadOffsetConflictTransportError):
        return _error_response(
            409,
            UPLOAD_OFFSET_CONFLICT,
            "Upload offset conflict.",
            current_offset=exc.current_offset,
        )
    if isinstance(exc, UploadSessionStateConflictError):
        return _error_response(
            409,
            UPLOAD_SESSION_STATE_CONFLICT,
            "Upload session state conflict.",
        )
    if isinstance(exc, UploadSessionExpiredError):
        return _error_response(409, UPLOAD_SESSION_EXPIRED, "Upload session expired.")
    if isinstance(exc, UploadInsufficientStorageError):
        return _error_response(
            507,
            INSUFFICIENT_QUARANTINE_STORAGE,
            "Insufficient quarantine storage.",
        )
    if isinstance(exc, UploadBodyLengthMismatchError):
        return _error_response(
            400,
            UPLOAD_BODY_LENGTH_MISMATCH,
            "Upload body length mismatch.",
        )
    if isinstance(exc, UploadQuarantineStateInconsistentError):
        return _error_response(
            409,
            QUARANTINE_STATE_INCONSISTENT,
            "Quarantine state is inconsistent.",
        )
    if isinstance(exc, UploadConcurrencyConflictError):
        return _error_response(
            409,
            UPLOAD_CONCURRENCY_CONFLICT,
            "Upload concurrency conflict.",
        )
    if isinstance(exc, UploadQuarantineUnavailableError):
        return _error_response(
            503,
            QUARANTINE_STORAGE_UNAVAILABLE,
            "Quarantine storage is unavailable.",
        )
    return None


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    current_offset: int | None = None,
) -> JSONResponse:
    body: dict[str, object] = {"code": code, "message": message}
    if current_offset is not None:
        body["current_offset"] = current_offset
    return JSONResponse(status_code=status_code, content={"error": body})


def _enriched_snapshot_response(
    dependencies: UploadApiDependencies,
    snapshot: UploadSessionSnapshot,
) -> UploadSessionResponse:
    return _snapshot_response(
        snapshot,
        media_id=_catalog_media_id(dependencies, snapshot),
    )


def _catalog_media_id(
    dependencies: UploadApiDependencies,
    snapshot: UploadSessionSnapshot,
) -> str | None:
    if snapshot.state != UploadSessionState.CATALOGED.value:
        return None
    repository = dependencies.publication_repository
    if repository is None:
        return None
    try:
        candidate = repository.get_candidate(
            UploadSessionId.from_string(snapshot.id)
        )
    except Exception:
        return None
    if (
        candidate is None
        or candidate.publication is None
        or candidate.publication.media_id is None
    ):
        return None
    return candidate.publication.media_id.to_string()


def _snapshot_response(
    snapshot: UploadSessionSnapshot,
    *,
    media_id: str | None = None,
) -> UploadSessionResponse:
    return UploadSessionResponse(
        id=snapshot.id,
        state=snapshot.state,
        display_filename=snapshot.display_filename,
        declared_size_bytes=snapshot.declared_size_bytes,
        received_size_bytes=snapshot.received_size_bytes,
        expires_at=snapshot.expires_at,
        failure_code=snapshot.failure_code,
        media_id=media_id if snapshot.state == "cataloged" else None,
    )


def _duplicate_resolution_response(
    snapshot: UploadSessionSnapshot,
) -> UploadDuplicateResolutionResponse:
    return UploadDuplicateResolutionResponse(
        id=snapshot.id,
        state=snapshot.state,
        declared_size_bytes=snapshot.declared_size_bytes,
        received_size_bytes=snapshot.received_size_bytes,
        expires_at=snapshot.expires_at,
        failure_code=snapshot.failure_code,
    )
