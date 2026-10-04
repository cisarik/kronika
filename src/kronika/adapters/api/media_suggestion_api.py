"""FastAPI routes for explicit AI media suggestion preview."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4

from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaRelativePath,
    candidate_kind_for_relative_path,
)
from kronika.application.media_analysis_lifecycle import MediaAnalysisLifecycleError
from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    ImportedMediaSuggestionPreviewResult,
    MediaSuggestion,
    MediaSuggestionNotFoundError,
    MediaSuggestionPreparationFailedError,
    MediaSuggestionPreparationUnavailableError,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionPreviewResult,
    PROMPT_VERSION,
)
from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_allows,
)
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.infrastructure.ai.constants import DEFAULT_MODEL_ID, DEFAULT_PROVIDER_ID

NO_STORE_HEADERS = {"Cache-Control": "no-store"}
MAX_RELATIVE_PATH_LENGTH = 4096

CLOUD_CONFIRMATION_REQUIRED_CODE = "CLOUD_CONFIRMATION_REQUIRED"
CLOUD_CONFIRMATION_REQUIRED_MESSAGE = "Explicit cloud upload confirmation is required."
AI_PROVIDER_NOT_CONFIGURED_CODE = "AI_PROVIDER_NOT_CONFIGURED"
AI_PROVIDER_NOT_CONFIGURED_MESSAGE = "The AI suggestion provider is not configured."
AI_MODEL_CAPABILITY_MISSING_CODE = "AI_MODEL_CAPABILITY_MISSING"
AI_MODEL_CAPABILITY_MISSING_MESSAGE = "The selected AI model does not support image analysis."
LIBRARY_NOT_FOUND_CODE = "LIBRARY_NOT_FOUND"
LIBRARY_NOT_FOUND_MESSAGE = "Library not found."
INVALID_MEDIA_PATH_CODE = "INVALID_MEDIA_PATH"
INVALID_MEDIA_PATH_MESSAGE = "Invalid media relative path."
MEDIA_PREPARATION_UNAVAILABLE_CODE = "MEDIA_PREPARATION_UNAVAILABLE"
MEDIA_PREPARATION_UNAVAILABLE_MESSAGE = "Local media preparation is not available."
MEDIA_PREPARATION_FAILED_CODE = "MEDIA_PREPARATION_FAILED"
MEDIA_PREPARATION_FAILED_MESSAGE = "Local media preparation failed."
AI_PROVIDER_AUTHENTICATION_FAILED_CODE = "AI_PROVIDER_AUTHENTICATION_FAILED"
AI_PROVIDER_AUTHENTICATION_FAILED_MESSAGE = "The configured AI provider credential was rejected."
AI_PROVIDER_RATE_LIMITED_CODE = "AI_PROVIDER_RATE_LIMITED"
AI_PROVIDER_RATE_LIMITED_MESSAGE = "The AI suggestion provider rate limit was reached."
AI_PROVIDER_MODEL_UNAVAILABLE_CODE = "AI_PROVIDER_MODEL_UNAVAILABLE"
AI_PROVIDER_MODEL_UNAVAILABLE_MESSAGE = "The configured AI provider model is not available."
AI_PROVIDER_UNAVAILABLE_CODE = "AI_PROVIDER_UNAVAILABLE"
AI_PROVIDER_UNAVAILABLE_MESSAGE = "The AI suggestion provider is not available."
AI_PROVIDER_INVALID_RESPONSE_CODE = "AI_PROVIDER_INVALID_RESPONSE"
AI_PROVIDER_INVALID_RESPONSE_MESSAGE = "The AI suggestion provider response was invalid."
AI_PROVIDER_FAILED_CODE = "AI_PROVIDER_FAILED"
AI_PROVIDER_FAILED_MESSAGE = "The AI suggestion provider request failed."
ANALYSIS_JOIN_FAILED_CODE = "ANALYSIS_JOIN_FAILED"
ANALYSIS_JOIN_FAILED_MESSAGE = "Durable analysis persistence failed."

AiProviderStatus = Literal[
    "not_configured",
    "credential_unavailable",
    "configured_unverified",
    "available",
    "authentication_failed",
    "rate_limited_or_quota_exhausted",
    "model_unavailable",
    "provider_unreachable",
    "provider_error",
]


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class MediaSuggestionCapabilityResponse(BaseModel):
    available: bool
    provider_id: str | None = None
    provider_display_name: str | None = None
    model_id: str | None = None
    prompt_version: str
    execution: str
    status: AiProviderStatus
    configured: bool
    credential_available: bool
    last_status_check: dict[str, object] | None
    last_connection_test: dict[str, object] | None
    requires_explicit_confirmation: bool


class MediaSuggestionPreviewRequest(BaseModel):
    relative_path: object
    confirm_cloud_upload: object


class ImportedMediaSuggestionPreviewRequest(BaseModel):
    confirm_cloud_upload: object


class SuggestionBodyResponse(BaseModel):
    title: str
    description: str
    collection: str
    tags: list[str]
    suggested_filename: str
    confidence: float
    evidence: list[str]
    uncertainties: list[str]


class MediaSuggestionPreviewResponse(BaseModel):
    library_id: str
    relative_path: str
    sent_frame_count: int
    provider_id: str
    model_id: str
    prompt_version: str
    suggestion: SuggestionBodyResponse


class ImportedMediaSuggestionPreviewResponse(BaseModel):
    media_id: str
    location_id: str
    sent_frame_count: int
    provider_id: str
    model_id: str
    prompt_version: str
    suggestion: SuggestionBodyResponse


@dataclass(frozen=True, slots=True)
class MediaSuggestionStatusRead:
    """Current sanitized AI status payload read at request time."""

    last_status_check: dict[str, object] | None = None
    last_connection_test: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class MediaSuggestionApiDependencies:
    """Injected dependencies for explicit AI media suggestion API routes."""

    preview_suggestion: object | None
    provider_configured: bool
    preview_imported_suggestion: object | None = None
    provider_id: str | None = DEFAULT_PROVIDER_ID
    provider_display_name: str | None = "NVIDIA NIM"
    model_id: str | None = DEFAULT_MODEL_ID
    credential_available: bool = False
    prompt_version: str = PROMPT_VERSION
    status: AiProviderStatus = "not_configured"
    last_status_check: dict[str, object] | None = None
    last_connection_test: dict[str, object] | None = None
    read_status: Callable[[], MediaSuggestionStatusRead] | None = None
    read_provider: Callable[[], object] | None = None
    audience_policy: ContentAudiencePolicy | None = None


def create_media_suggestion_api_router(dependencies: MediaSuggestionApiDependencies) -> APIRouter:
    """Create the explicit AI media suggestion preview router."""
    router = APIRouter()

    @router.get(
        "/api/ai/media-suggestion-capability",
        response_model=MediaSuggestionCapabilityResponse,
    )
    def media_suggestion_capability() -> JSONResponse:
        if dependencies.read_provider is not None:
            try:
                resolved = dependencies.read_provider()
            except Exception:
                resolved = None
            if resolved is None:
                return _json_response(_unavailable_capability_response(dependencies.prompt_version))
            return _json_response(
                _resolved_capability_response(
                    resolved,
                    prompt_version=dependencies.prompt_version,
                )
            )
        current_status = _current_status_payload(dependencies)
        provider_selected = dependencies.provider_id is not None and dependencies.model_id is not None
        credential_available = dependencies.credential_available or dependencies.provider_configured
        status = _capability_status(
            provider_selected=provider_selected,
            credential_available=credential_available,
            configured_status=dependencies.status,
            last_connection_test=current_status.last_connection_test,
            fresh_status_read=dependencies.read_status is not None,
        )
        return _json_response(
            MediaSuggestionCapabilityResponse(
                available=dependencies.provider_configured,
                provider_id=dependencies.provider_id,
                provider_display_name=dependencies.provider_display_name,
                model_id=dependencies.model_id,
                prompt_version=dependencies.prompt_version,
                execution="server",
                status=status,
                configured=provider_selected,
                credential_available=credential_available,
                last_status_check=current_status.last_status_check,
                last_connection_test=current_status.last_connection_test,
                requires_explicit_confirmation=True,
            )
        )

    @router.post(
        "/api/libraries/{library_id}/media-suggestion-preview",
        response_model=MediaSuggestionPreviewResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def preview_media_suggestion(
        library_id: UUID4,
        request: MediaSuggestionPreviewRequest,
    ) -> MediaSuggestionPreviewResponse | JSONResponse:
        if request.confirm_cloud_upload is not True:
            return _error_response(
                409,
                CLOUD_CONFIRMATION_REQUIRED_CODE,
                CLOUD_CONFIRMATION_REQUIRED_MESSAGE,
            )
        if dependencies.preview_suggestion is None or not _provider_configured(dependencies):
            return _error_response(
                503,
                AI_PROVIDER_NOT_CONFIGURED_CODE,
                AI_PROVIDER_NOT_CONFIGURED_MESSAGE,
            )
        if _model_lacks_vision(dependencies):
            return _error_response(
                409,
                AI_MODEL_CAPABILITY_MISSING_CODE,
                AI_MODEL_CAPABILITY_MISSING_MESSAGE,
            )
        try:
            relative_path = _media_relative_path_from_request(request.relative_path)
            result = dependencies.preview_suggestion.execute(
                LibraryId.from_string(str(library_id)),
                relative_path,
            )
        except FrameNestLibraryRepositoryError:
            return _error_response(503, AI_PROVIDER_UNAVAILABLE_CODE, AI_PROVIDER_UNAVAILABLE_MESSAGE)
        except MediaSuggestionNotFoundError:
            return _error_response(404, LIBRARY_NOT_FOUND_CODE, LIBRARY_NOT_FOUND_MESSAGE)
        except (FrameNestMediaAnalysisError, FrameNestMediaSuggestionError):
            return _error_response(422, INVALID_MEDIA_PATH_CODE, INVALID_MEDIA_PATH_MESSAGE)
        except MediaSuggestionPreparationUnavailableError:
            return _error_response(
                409,
                MEDIA_PREPARATION_UNAVAILABLE_CODE,
                MEDIA_PREPARATION_UNAVAILABLE_MESSAGE,
            )
        except MediaSuggestionPreparationFailedError:
            return _error_response(
                500,
                MEDIA_PREPARATION_FAILED_CODE,
                MEDIA_PREPARATION_FAILED_MESSAGE,
            )
        except MediaSuggestionProviderAuthError:
            return _error_response(
                503,
                AI_PROVIDER_AUTHENTICATION_FAILED_CODE,
                AI_PROVIDER_AUTHENTICATION_FAILED_MESSAGE,
            )
        except MediaSuggestionProviderRateLimitedError:
            return _error_response(429, AI_PROVIDER_RATE_LIMITED_CODE, AI_PROVIDER_RATE_LIMITED_MESSAGE)
        except MediaSuggestionProviderModelUnavailableError:
            return _error_response(
                503,
                AI_PROVIDER_MODEL_UNAVAILABLE_CODE,
                AI_PROVIDER_MODEL_UNAVAILABLE_MESSAGE,
            )
        except MediaSuggestionProviderUnavailableError:
            return _error_response(503, AI_PROVIDER_UNAVAILABLE_CODE, AI_PROVIDER_UNAVAILABLE_MESSAGE)
        except MediaSuggestionProviderInvalidResponseError:
            return _error_response(
                502,
                AI_PROVIDER_INVALID_RESPONSE_CODE,
                AI_PROVIDER_INVALID_RESPONSE_MESSAGE,
            )
        except MediaSuggestionProviderFailedError:
            return _error_response(502, AI_PROVIDER_FAILED_CODE, AI_PROVIDER_FAILED_MESSAGE)
        except Exception:
            return _error_response(502, AI_PROVIDER_FAILED_CODE, AI_PROVIDER_FAILED_MESSAGE)
        return _json_response(_preview_response(result))

    @router.post(
        "/api/media/{media_id}/locations/{location_id}/ai-suggestion-preview",
        response_model=ImportedMediaSuggestionPreviewResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            429: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            502: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def preview_imported_media_suggestion(
        media_id: UUID4,
        location_id: UUID4,
        request: ImportedMediaSuggestionPreviewRequest,
        http_request: Request,
    ) -> ImportedMediaSuggestionPreviewResponse | JSONResponse:
        parsed_media_id = MediaId.from_string(str(media_id))
        try:
            if not content_audience_allows(
                request=http_request,
                media_id=parsed_media_id,
                policy=dependencies.audience_policy,
            ):
                return _error_response(
                    404,
                    LIBRARY_NOT_FOUND_CODE,
                    LIBRARY_NOT_FOUND_MESSAGE,
                )
        except ContentAudienceUnavailableError:
            return _error_response(
                503,
                AI_PROVIDER_UNAVAILABLE_CODE,
                AI_PROVIDER_UNAVAILABLE_MESSAGE,
            )
        if request.confirm_cloud_upload is not True:
            return _error_response(
                409,
                CLOUD_CONFIRMATION_REQUIRED_CODE,
                CLOUD_CONFIRMATION_REQUIRED_MESSAGE,
            )
        if dependencies.preview_imported_suggestion is None or not _provider_configured(dependencies):
            return _error_response(
                503,
                AI_PROVIDER_NOT_CONFIGURED_CODE,
                AI_PROVIDER_NOT_CONFIGURED_MESSAGE,
            )
        if _model_lacks_vision(dependencies):
            return _error_response(
                409,
                AI_MODEL_CAPABILITY_MISSING_CODE,
                AI_MODEL_CAPABILITY_MISSING_MESSAGE,
            )
        try:
            result = dependencies.preview_imported_suggestion.execute(
                parsed_media_id,
                MediaLocationId.from_string(str(location_id)),
            )
        except MediaSuggestionNotFoundError:
            return _error_response(404, LIBRARY_NOT_FOUND_CODE, LIBRARY_NOT_FOUND_MESSAGE)
        except (FrameNestMediaAnalysisError, FrameNestMediaSuggestionError):
            return _error_response(422, INVALID_MEDIA_PATH_CODE, INVALID_MEDIA_PATH_MESSAGE)
        except MediaSuggestionPreparationUnavailableError:
            return _error_response(
                409,
                MEDIA_PREPARATION_UNAVAILABLE_CODE,
                MEDIA_PREPARATION_UNAVAILABLE_MESSAGE,
            )
        except MediaSuggestionPreparationFailedError:
            return _error_response(
                500,
                MEDIA_PREPARATION_FAILED_CODE,
                MEDIA_PREPARATION_FAILED_MESSAGE,
            )
        except MediaSuggestionProviderAuthError:
            return _error_response(
                503,
                AI_PROVIDER_AUTHENTICATION_FAILED_CODE,
                AI_PROVIDER_AUTHENTICATION_FAILED_MESSAGE,
            )
        except MediaSuggestionProviderRateLimitedError:
            return _error_response(429, AI_PROVIDER_RATE_LIMITED_CODE, AI_PROVIDER_RATE_LIMITED_MESSAGE)
        except MediaSuggestionProviderModelUnavailableError:
            return _error_response(
                503,
                AI_PROVIDER_MODEL_UNAVAILABLE_CODE,
                AI_PROVIDER_MODEL_UNAVAILABLE_MESSAGE,
            )
        except MediaSuggestionProviderUnavailableError:
            return _error_response(503, AI_PROVIDER_UNAVAILABLE_CODE, AI_PROVIDER_UNAVAILABLE_MESSAGE)
        except MediaSuggestionProviderInvalidResponseError:
            return _error_response(
                502,
                AI_PROVIDER_INVALID_RESPONSE_CODE,
                AI_PROVIDER_INVALID_RESPONSE_MESSAGE,
            )
        except MediaSuggestionProviderFailedError:
            return _error_response(502, AI_PROVIDER_FAILED_CODE, AI_PROVIDER_FAILED_MESSAGE)
        except MediaAnalysisLifecycleError:
            return _error_response(500, ANALYSIS_JOIN_FAILED_CODE, ANALYSIS_JOIN_FAILED_MESSAGE)
        except Exception:
            return _error_response(502, AI_PROVIDER_FAILED_CODE, AI_PROVIDER_FAILED_MESSAGE)
        return _json_response(_imported_preview_response(result))

    return router


def _current_status_payload(dependencies: MediaSuggestionApiDependencies) -> MediaSuggestionStatusRead:
    if dependencies.read_status is None:
        return MediaSuggestionStatusRead(
            last_status_check=dependencies.last_status_check,
            last_connection_test=dependencies.last_connection_test,
        )
    try:
        return dependencies.read_status()
    except Exception:
        return MediaSuggestionStatusRead()


def _provider_configured(dependencies: MediaSuggestionApiDependencies) -> bool:
    if dependencies.read_provider is not None:
        try:
            return getattr(dependencies.read_provider(), "provider", None) is not None
        except Exception:
            return False
    return dependencies.provider_configured


def _model_lacks_vision(dependencies: MediaSuggestionApiDependencies) -> bool:
    """True only when a dynamic resolution proves the selected model has no vision_input."""
    if dependencies.read_provider is None:
        return False
    try:
        resolved = dependencies.read_provider()
    except Exception:
        return False
    capabilities_for = getattr(resolved, "capabilities_for", None)
    if not callable(capabilities_for):
        return False
    model_id = getattr(resolved, "model_id", None)
    return "vision_input" not in capabilities_for(model_id or "")


def _resolved_capability_response(
    resolved: object,
    *,
    prompt_version: str,
) -> MediaSuggestionCapabilityResponse:
    provider_selected = (
        getattr(resolved, "provider_id", None) is not None
        and getattr(resolved, "model_id", None) is not None
    )
    credential_available = bool(getattr(resolved, "credential_available", False))
    last_test = getattr(resolved, "last_test", None)
    last_status = getattr(resolved, "last_status", None)
    return MediaSuggestionCapabilityResponse(
        available=getattr(resolved, "provider", None) is not None,
        provider_id=getattr(resolved, "provider_id", None),
        provider_display_name=getattr(resolved, "display_name", None),
        model_id=getattr(resolved, "model_id", None),
        prompt_version=prompt_version,
        execution="server",
        status=_resolved_capability_status(
            provider_selected=provider_selected,
            credential_available=credential_available,
            last_connection_test_status=(
                None if last_test is None else getattr(last_test, "status", None)
            ),
        ),
        configured=provider_selected,
        credential_available=credential_available,
        last_status_check=_persisted_status_payload(last_status),
        last_connection_test=_persisted_test_payload(last_test),
        requires_explicit_confirmation=True,
    )


def _unavailable_capability_response(prompt_version: str) -> MediaSuggestionCapabilityResponse:
    return MediaSuggestionCapabilityResponse(
        available=False,
        provider_id=None,
        provider_display_name=None,
        model_id=None,
        prompt_version=prompt_version,
        execution="server",
        status="not_configured",
        configured=False,
        credential_available=False,
        last_status_check=None,
        last_connection_test=None,
        requires_explicit_confirmation=True,
    )


def _resolved_capability_status(
    *,
    provider_selected: bool,
    credential_available: bool,
    last_connection_test_status: str | None,
) -> AiProviderStatus:
    if not provider_selected:
        return "not_configured"
    if not credential_available:
        return "credential_unavailable"
    if last_connection_test_status == "success":
        return "available"
    if last_connection_test_status in {
        "authentication_failed",
        "rate_limited_or_quota_exhausted",
        "model_unavailable",
        "provider_unreachable",
    }:
        return last_connection_test_status
    if last_connection_test_status in {"invalid_response", "provider_error"}:
        return "provider_error"
    return "configured_unverified"


def _persisted_test_payload(last_test: object | None) -> dict[str, object] | None:
    if last_test is None:
        return None
    return {
        "status": getattr(last_test, "status"),
        "tested_at_ms": getattr(last_test, "tested_at_ms"),
    }


def _persisted_status_payload(last_status: object | None) -> dict[str, object] | None:
    if last_status is None:
        return None
    return {
        "configuration_state": getattr(last_status, "configuration_state"),
        "checked_at_ms": getattr(last_status, "checked_at_ms"),
    }


def _capability_status(
    *,
    provider_selected: bool,
    credential_available: bool,
    configured_status: AiProviderStatus,
    last_connection_test: dict[str, object] | None,
    fresh_status_read: bool,
) -> AiProviderStatus:
    if not provider_selected:
        return "not_configured"
    if not credential_available:
        return "credential_unavailable"
    if not fresh_status_read:
        if configured_status == "not_configured":
            return "configured_unverified"
        return configured_status
    status = last_connection_test.get("status") if last_connection_test is not None else None
    if status == "success":
        return "available"
    if status in {
        "authentication_failed",
        "rate_limited_or_quota_exhausted",
        "model_unavailable",
        "provider_unreachable",
    }:
        return status
    if status in {"invalid_response", "provider_error"}:
        return "provider_error"
    return "configured_unverified"


def _media_relative_path_from_request(value: object) -> MediaRelativePath:
    if not isinstance(value, str) or len(value) > MAX_RELATIVE_PATH_LENGTH:
        raise FrameNestMediaAnalysisError(INVALID_MEDIA_PATH_MESSAGE)
    relative_path = MediaRelativePath(value)
    candidate_kind_for_relative_path(relative_path)
    return relative_path


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=NO_STORE_HEADERS,
    )


def _json_response(response: BaseModel) -> JSONResponse:
    return JSONResponse(
        status_code=200,
        content=response.model_dump(exclude_none=True),
        headers=NO_STORE_HEADERS,
    )


def _suggestion_response(suggestion: MediaSuggestion) -> SuggestionBodyResponse:
    return SuggestionBodyResponse(
        title=suggestion.title,
        description=suggestion.description,
        collection=suggestion.collection,
        tags=list(suggestion.tags),
        suggested_filename=suggestion.suggested_filename,
        confidence=suggestion.confidence,
        evidence=list(suggestion.evidence),
        uncertainties=list(suggestion.uncertainties),
    )


def _preview_response(result: MediaSuggestionPreviewResult) -> MediaSuggestionPreviewResponse:
    return MediaSuggestionPreviewResponse(
        library_id=result.library_id.to_string(),
        relative_path=result.relative_path.value,
        sent_frame_count=result.sent_frame_count,
        provider_id=result.suggestion.provider_id,
        model_id=result.suggestion.model_id,
        prompt_version=result.suggestion.prompt_version,
        suggestion=_suggestion_response(result.suggestion),
    )


def _imported_preview_response(
    result: ImportedMediaSuggestionPreviewResult,
) -> ImportedMediaSuggestionPreviewResponse:
    return ImportedMediaSuggestionPreviewResponse(
        media_id=result.media_id.to_string(),
        location_id=result.location_id.to_string(),
        sent_frame_count=result.sent_frame_count,
        provider_id=result.suggestion.provider_id,
        model_id=result.suggestion.model_id,
        prompt_version=result.suggestion.prompt_version,
        suggestion=_suggestion_response(result.suggestion),
    )
