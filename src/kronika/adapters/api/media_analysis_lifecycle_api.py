"""Sanitized API for durable automatic media analysis lifecycle status."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from types import SimpleNamespace

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from kronika.application.media_analysis_lifecycle import (
    AutomaticAnalysisPublicView,
    MediaAnalysisLifecycleError,
    ReadAutomaticMediaAnalysis,
    public_view_from_run,
)
from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_allows,
    content_audience_decision,
    load_approved_projection,
)
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.companion_review import (
    COMPANION_REVIEW_QUERY_INVALID_MESSAGE,
    DEFAULT_COMPANION_REVIEW_LIMIT,
    MAX_COMPANION_REVIEW_LIMIT,
    CompanionReviewCodecError,
    CompanionReviewQueryError,
    GetCompanionReviewDetail,
    MappedSuggestedTag,
    MappedTagStatus,
    decode_stored_suggestion_result,
)
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.ports.companion_review_repository import (
    CompanionReviewMediaNotFoundError,
    CompanionReviewMovieExcludedError,
    CompanionReviewStoredResultError,
    FrameNestCompanionReviewRepositoryError,
)
from kronika.domain.identities import FrameNestIdentityError, MediaId, MediaLocationId
from kronika.domain.media_classification import MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION
from kronika.domain.record_access import READ_APPROVED, READ_DENY
from kronika.domain.identity_access import IdentityContext
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    MediaAnalysisRun,
    RESULT_SCHEMA_VERSION,
)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class AutomaticAnalysisSuggestionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    description: str
    collection: str
    tags: list[str]
    suggested_filename: str
    confidence: float
    evidence: list[str]
    uncertainties: list[str]


class AutomaticAnalysisStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    state: str
    automatic_analysis_enabled: bool
    analysis_definition: str | None = None
    analysis_profile: str | None = None
    reasoning_enabled: bool | None = None
    derivative_strategy: str | None = None
    derivative_count: int | None = None
    provider_submission_occurred: bool | None = None
    provider_id: str | None = None
    model_id: str | None = None
    prompt_version: str | None = None
    result_schema_version: str | None = None
    result: AutomaticAnalysisSuggestionResponse | None = None
    movie_identification_result: dict[str, object] | None = None
    error_code: str | None = None
    error_message: str | None = None
    attempt_count: int | None = None
    created_at_ms: int | None = None
    started_at_ms: int | None = None
    completed_at_ms: int | None = None


class AutomaticAnalysisCapabilityResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_analysis_enabled: bool
    analysis_definition: str = Field(
        default=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION
    )
    result_schema_version: str = Field(default=RESULT_SCHEMA_VERSION)
    provider_configured: bool
    provider_id: str | None = None
    model_id: str | None = None


class ManualDurableAnalysisRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_cloud_upload: bool


class ManualMovieIdentificationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirm_cloud_upload: bool


class MediaAiSuggestionTagResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: str
    status: str
    key: str | None
    display_name: str | None


class MediaAiSuggestionItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analysis_run_id: str
    completed_at_ms: int
    provider_id: str
    model_id: str
    prompt_version: str
    title: str
    description: str
    tags: list[MediaAiSuggestionTagResponse]
    suggested_filename: str


class MediaAiSuggestionListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suggestions: list[MediaAiSuggestionItemResponse]
    next_cursor: str | None


_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


@dataclass(frozen=True, slots=True)
class MediaAnalysisLifecycleApiDependencies:
    """Injected dependencies for automatic analysis status routes."""

    read_analysis: ReadAutomaticMediaAnalysis | None
    automatic_analysis_enabled: bool | Callable[[], bool]
    provider_configured: bool
    provider_id: str | None = None
    model_id: str | None = None
    read_provider: Callable[[], object] | None = None
    request_manual_analysis: (
        Callable[[MediaId, MediaLocationId], MediaAnalysisRun] | None
    ) = None
    request_movie_identification: (
        Callable[[MediaId, MediaLocationId], MediaAnalysisRun] | None
    ) = None
    read_movie_identification: (
        Callable[[MediaId], AutomaticAnalysisPublicView] | None
    ) = None
    execute_movie_identification: Callable[[MediaAnalysisRun], MediaAnalysisRun] | None = None
    audience_policy: ContentAudiencePolicy | None = None
    list_suggestions: GetCompanionReviewDetail | None = None


def create_media_analysis_lifecycle_api_router(
    dependencies: MediaAnalysisLifecycleApiDependencies,
) -> APIRouter:
    """Create the durable automatic analysis status router."""
    router = APIRouter()

    @router.get(
        "/api/ai/automatic-analysis-capability",
        response_model=AutomaticAnalysisCapabilityResponse,
    )
    def automatic_analysis_capability() -> AutomaticAnalysisCapabilityResponse:
        automatic_analysis_enabled = _resolve_automatic_analysis_enabled(
            dependencies.automatic_analysis_enabled
        )
        if dependencies.read_provider is not None:
            try:
                resolved = dependencies.read_provider()
            except Exception:
                resolved = None
            if resolved is not None:
                return AutomaticAnalysisCapabilityResponse(
                    automatic_analysis_enabled=automatic_analysis_enabled,
                    provider_configured=getattr(resolved, "provider", None) is not None,
                    provider_id=getattr(resolved, "provider_id", None),
                    model_id=getattr(resolved, "model_id", None),
                )
        return AutomaticAnalysisCapabilityResponse(
            automatic_analysis_enabled=automatic_analysis_enabled,
            provider_configured=dependencies.provider_configured,
            provider_id=dependencies.provider_id,
            model_id=dependencies.model_id,
        )

    @router.get(
        "/api/media/{media_id}/ai-suggestions",
        response_model=MediaAiSuggestionListResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_media_ai_suggestions(
        media_id: str,
        request: Request,
        limit: int = Query(default=DEFAULT_COMPANION_REVIEW_LIMIT),
        cursor: str | None = None,
    ) -> MediaAiSuggestionListResponse | JSONResponse:
        identity = request.scope.get(SCOPE_IDENTITY)
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            return _error("IDENTITY_REQUIRED", "A verified identity is required.", 401)
        try:
            parsed_media_id = MediaId.from_string(media_id)
        except FrameNestIdentityError:
            return _error("MEDIA_NOT_FOUND", "Media not found.", 404)
        try:
            decision = content_audience_decision(
                request=request,
                media_id=parsed_media_id,
                policy=dependencies.audience_policy,
            )
        except ContentAudienceUnavailableError:
            return _error(
                "ANALYSIS_STATUS_UNAVAILABLE",
                "Analysis status is unavailable.",
                503,
            )
        if decision == READ_DENY:
            return _error("MEDIA_NOT_FOUND", "Media not found.", 404)
        if decision == READ_APPROVED:
            return _approved_suggestions_response(
                request_policy=dependencies.audience_policy,
                media_id=parsed_media_id,
            )
        if dependencies.list_suggestions is None:
            return _error(
                "CATALOG_UNAVAILABLE",
                "The local catalog is not available.",
                503,
            )
        if limit < 1 or limit > MAX_COMPANION_REVIEW_LIMIT:
            return _error(
                "INVALID_MEDIA_AI_SUGGESTION_QUERY",
                COMPANION_REVIEW_QUERY_INVALID_MESSAGE,
                422,
            )
        try:
            detail = dependencies.list_suggestions.execute(
                media_id=media_id,
                actor_login_key=identity.login_key,
                limit=limit,
                cursor=cursor,
            )
        except CompanionReviewQueryError:
            return _error(
                "INVALID_MEDIA_AI_SUGGESTION_QUERY",
                COMPANION_REVIEW_QUERY_INVALID_MESSAGE,
                422,
            )
        except CompanionReviewMediaNotFoundError:
            return _error("MEDIA_NOT_FOUND", "Media not found.", 404)
        except CompanionReviewMovieExcludedError:
            return _error(
                "COMPANION_REVIEW_MOVIE_EXCLUDED",
                "Movie workflows are excluded from companion review.",
                409,
            )
        except CompanionReviewStoredResultError:
            return _error(
                "COMPANION_REVIEW_RESULT_INVALID",
                "Stored analysis result is invalid.",
                500,
            )
        except FrameNestCompanionReviewRepositoryError:
            return _error(
                "MEDIA_AI_SUGGESTION_QUERY_FAILED",
                "AI suggestion list failed.",
                500,
            )
        return JSONResponse(
            status_code=200,
            content=MediaAiSuggestionListResponse(
                suggestions=[
                    _suggestion_item(suggestion) for suggestion in detail.suggestions
                ],
                next_cursor=detail.next_cursor,
            ).model_dump(),
            headers=_NO_STORE_HEADERS,
        )

    @router.get(
        "/api/media/{media_id}/automatic-analysis",
        response_model=AutomaticAnalysisStatusResponse,
    )
    def automatic_analysis_status(
        media_id: str,
        request: Request,
    ) -> AutomaticAnalysisStatusResponse | JSONResponse:
        try:
            parsed_media_id = MediaId.from_string(media_id)
        except FrameNestIdentityError:
            return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
        decision_or_error = _analysis_read_decision(
            request,
            parsed_media_id,
            dependencies.audience_policy,
        )
        if isinstance(decision_or_error, JSONResponse):
            return decision_or_error
        if decision_or_error == READ_APPROVED:
            view = _approved_analysis_view(
                dependencies.audience_policy,
                parsed_media_id,
                movie=False,
            )
            return _status_response(
                media_id=parsed_media_id.to_string(),
                view=view,
                automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                    dependencies.automatic_analysis_enabled
                ),
            )
        if dependencies.read_analysis is None:
            view = AutomaticAnalysisPublicView(
                state="not_requested",
                analysis_definition=None,
                provider_id=None,
                model_id=None,
                prompt_version=None,
                result=None,
                error_code=None,
                error_message=None,
                attempt_count=None,
                created_at_ms=None,
                started_at_ms=None,
                completed_at_ms=None,
            )
        else:
            try:
                view = dependencies.read_analysis.execute(parsed_media_id)
            except MediaAnalysisLifecycleError:
                return _error(
                    "ANALYSIS_STATUS_UNAVAILABLE",
                    "Automatic analysis status is unavailable.",
                    503,
                )
        return _status_response(
            media_id=parsed_media_id.to_string(),
            view=view,
            automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                dependencies.automatic_analysis_enabled
            ),
        )

    @router.post(
        "/api/media/{media_id}/locations/{location_id}/durable-analysis",
        response_model=AutomaticAnalysisStatusResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def request_durable_analysis(
        media_id: str,
        location_id: str,
        request: ManualDurableAnalysisRequest,
        http_request: Request,
    ) -> AutomaticAnalysisStatusResponse | JSONResponse:
        try:
            parsed_media_id = MediaId.from_string(media_id)
            parsed_location_id = MediaLocationId.from_string(location_id)
        except FrameNestIdentityError:
            return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
        audience_error = _audience_error(
            http_request,
            parsed_media_id,
            dependencies.audience_policy,
        )
        if audience_error is not None:
            return audience_error
        if request.confirm_cloud_upload is not True:
            return _error(
                "CLOUD_CONFIRMATION_REQUIRED",
                "Cloud frame upload confirmation is required.",
                409,
            )
        if not _provider_configured(dependencies):
            return _error(
                "AI_PROVIDER_NOT_CONFIGURED",
                "AI analysis is not configured.",
                503,
            )
        if _model_lacks_vision(dependencies):
            return _error(
                "AI_MODEL_CAPABILITY_MISSING",
                "The selected AI model does not support image analysis.",
                409,
            )
        if dependencies.request_manual_analysis is None:
            return _error(
                "ANALYSIS_REQUEST_UNAVAILABLE",
                "Durable analysis request is unavailable.",
                503,
            )
        try:
            run = dependencies.request_manual_analysis(
                parsed_media_id,
                parsed_location_id,
            )
        except MediaAnalysisLifecycleError:
            return _error(
                "ANALYSIS_REQUEST_UNAVAILABLE",
                "Durable analysis request is unavailable.",
                503,
            )
        return _status_response(
            media_id=parsed_media_id.to_string(),
            view=public_view_from_run(run),
            automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                dependencies.automatic_analysis_enabled
            ),
        )

    @router.get(
        "/api/media/{media_id}/movie-identification",
        response_model=AutomaticAnalysisStatusResponse,
    )
    def movie_identification_status(
        media_id: str,
        request: Request,
    ) -> AutomaticAnalysisStatusResponse | JSONResponse:
        try:
            parsed_media_id = MediaId.from_string(media_id)
        except FrameNestIdentityError:
            return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
        decision_or_error = _analysis_read_decision(
            request,
            parsed_media_id,
            dependencies.audience_policy,
        )
        if isinstance(decision_or_error, JSONResponse):
            return decision_or_error
        if decision_or_error == READ_APPROVED:
            view = _approved_analysis_view(
                dependencies.audience_policy,
                parsed_media_id,
                movie=True,
            )
            return _status_response(
                media_id=parsed_media_id.to_string(),
                view=view,
                automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                    dependencies.automatic_analysis_enabled
                ),
            )
        if dependencies.read_movie_identification is None:
            view = public_view_from_run(None)
        else:
            try:
                view = dependencies.read_movie_identification(parsed_media_id)
            except MediaAnalysisLifecycleError:
                return _error(
                    "ANALYSIS_STATUS_UNAVAILABLE",
                    "Movie identification status is unavailable.",
                    503,
                )
        return _status_response(
            media_id=parsed_media_id.to_string(),
            view=view,
            automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                dependencies.automatic_analysis_enabled
            ),
        )

    @router.post(
        "/api/media/{media_id}/locations/{location_id}/movie-identification",
        response_model=AutomaticAnalysisStatusResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def request_movie_identification(
        media_id: str,
        location_id: str,
        request: ManualMovieIdentificationRequest,
        http_request: Request,
    ) -> AutomaticAnalysisStatusResponse | JSONResponse:
        try:
            parsed_media_id = MediaId.from_string(media_id)
            parsed_location_id = MediaLocationId.from_string(location_id)
        except FrameNestIdentityError:
            return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
        audience_error = _audience_error(
            http_request,
            parsed_media_id,
            dependencies.audience_policy,
        )
        if audience_error is not None:
            return audience_error
        if request.confirm_cloud_upload is not True:
            return _error(
                "CLOUD_CONFIRMATION_REQUIRED",
                "Cloud frame upload confirmation is required.",
                409,
            )
        if not dependencies.provider_configured:
            return _error(
                "AI_PROVIDER_NOT_CONFIGURED",
                "AI analysis is not configured.",
                503,
            )
        if (
            dependencies.request_movie_identification is None
            or dependencies.execute_movie_identification is None
        ):
            return _error(
                "ANALYSIS_REQUEST_UNAVAILABLE",
                "Movie identification request is unavailable.",
                503,
            )
        try:
            run = dependencies.request_movie_identification(
                parsed_media_id,
                parsed_location_id,
            )
            if run.state.value == "pending":
                run = dependencies.execute_movie_identification(run)
        except MediaAnalysisLifecycleError:
            return _error(
                "ANALYSIS_REQUEST_UNAVAILABLE",
                "Movie identification request is unavailable.",
                503,
            )
        return _status_response(
            media_id=parsed_media_id.to_string(),
            view=public_view_from_run(run),
            automatic_analysis_enabled=_resolve_automatic_analysis_enabled(
                dependencies.automatic_analysis_enabled
            ),
        )

    return router


def _provider_configured(dependencies: MediaAnalysisLifecycleApiDependencies) -> bool:
    if dependencies.read_provider is not None:
        try:
            return getattr(dependencies.read_provider(), "provider", None) is not None
        except Exception:
            return False
    return dependencies.provider_configured


def _model_lacks_vision(dependencies: MediaAnalysisLifecycleApiDependencies) -> bool:
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


def _resolve_automatic_analysis_enabled(
    value: bool | Callable[[], bool],
) -> bool:
    if callable(value) and not isinstance(value, bool):
        return bool(value())
    return bool(value)


def _status_response(
    *,
    media_id: str,
    view: AutomaticAnalysisPublicView,
    automatic_analysis_enabled: bool,
) -> AutomaticAnalysisStatusResponse:
    from kronika.domain.media_classification import (
        MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
        MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
    )

    result = None
    movie_result = None
    result_schema_version = None
    if view.state == "analyzed" and view.result is not None:
        if view.analysis_definition == MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION:
            movie_result = view.result
            result_schema_version = MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION
        elif "title" in view.result:
            result = AutomaticAnalysisSuggestionResponse(
                title=str(view.result["title"]),
                description=str(view.result["description"]),
                collection=str(view.result["collection"]),
                tags=[str(tag) for tag in view.result["tags"]],
                suggested_filename=str(view.result["suggested_filename"]),
                confidence=float(view.result["confidence"]),
                evidence=[str(item) for item in view.result["evidence"]],
                uncertainties=[str(item) for item in view.result["uncertainties"]],
            )
            result_schema_version = RESULT_SCHEMA_VERSION
    return AutomaticAnalysisStatusResponse(
        media_id=media_id,
        state=view.state,
        automatic_analysis_enabled=automatic_analysis_enabled,
        analysis_definition=view.analysis_definition,
        analysis_profile=view.analysis_profile,
        reasoning_enabled=view.reasoning_enabled,
        derivative_strategy=view.derivative_strategy,
        derivative_count=view.derivative_count,
        provider_submission_occurred=view.provider_submission_occurred,
        provider_id=view.provider_id if view.state != "not_requested" else None,
        model_id=view.model_id if view.state != "not_requested" else None,
        prompt_version=view.prompt_version if view.state != "not_requested" else None,
        result_schema_version=result_schema_version,
        result=result,
        movie_identification_result=movie_result,
        error_code=view.error_code if view.state == "failed" else None,
        error_message=view.error_message if view.state == "failed" else None,
        attempt_count=view.attempt_count,
        created_at_ms=view.created_at_ms,
        started_at_ms=view.started_at_ms,
        completed_at_ms=view.completed_at_ms,
    )


def _suggestion_item(suggestion: object) -> MediaAiSuggestionItemResponse:
    tags = [
        MediaAiSuggestionTagResponse(
            value=tag.value,
            status=tag.status.value if hasattr(tag.status, "value") else str(tag.status),
            key=tag.key,
            display_name=tag.display_name,
        )
        for tag in suggestion.tags
    ]
    return MediaAiSuggestionItemResponse(
        analysis_run_id=suggestion.analysis_run_id,
        completed_at_ms=suggestion.completed_at_ms,
        provider_id=suggestion.provider_id,
        model_id=suggestion.model_id,
        prompt_version=suggestion.prompt_version,
        title=suggestion.title,
        description=suggestion.description,
        tags=tags,
        suggested_filename=getattr(suggestion, "suggested_filename", "") or "",
    )


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump(),
    )


_SUGGESTION_RESULT_FIELDS = frozenset(
    {
        "title",
        "description",
        "collection",
        "tags",
        "suggested_filename",
        "confidence",
        "evidence",
        "uncertainties",
    }
)


def _analysis_read_decision(
    request: Request,
    media_id: MediaId,
    policy: ContentAudiencePolicy | None,
) -> str | JSONResponse:
    try:
        decision = content_audience_decision(
            request=request,
            media_id=media_id,
            policy=policy,
        )
    except ContentAudienceUnavailableError:
        return _error(
            "ANALYSIS_STATUS_UNAVAILABLE",
            "Analysis status is unavailable.",
            503,
        )
    if decision == READ_DENY:
        return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
    return decision


def _approved_analysis_view(
    policy: ContentAudiencePolicy | None,
    media_id: MediaId,
    *,
    movie: bool,
) -> AutomaticAnalysisPublicView:
    projection = load_approved_projection(policy=policy, media_id=media_id)
    absent = public_view_from_run(None)
    result = None if projection is None else projection.analysis_result
    if not isinstance(result, dict):
        return absent
    if movie:
        if "identified_title" not in result:
            return absent
        definition = MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION
    else:
        if not _SUGGESTION_RESULT_FIELDS.issubset(result):
            return absent
        definition = AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION
    return AutomaticAnalysisPublicView(
        state="analyzed",
        analysis_definition=definition,
        provider_id=None,
        model_id=None,
        prompt_version=None,
        result=result,
        error_code=None,
        error_message=None,
        attempt_count=None,
        created_at_ms=None,
        started_at_ms=None,
        completed_at_ms=None,
    )


def _approved_suggestions_response(
    *,
    request_policy: ContentAudiencePolicy | None,
    media_id: MediaId,
) -> JSONResponse:
    try:
        projection = load_approved_projection(policy=request_policy, media_id=media_id)
    except Exception:
        return _error(
            "MEDIA_AI_SUGGESTION_QUERY_FAILED",
            "AI suggestion list failed.",
            500,
        )
    suggestions: list[MediaAiSuggestionItemResponse] = []
    result = None if projection is None else projection.analysis_result
    run_id = None if projection is None else projection.analysis_run_id
    if isinstance(result, dict) and isinstance(run_id, str) and run_id:
        try:
            stored = decode_stored_suggestion_result(
                json.dumps(result, separators=(",", ":"), sort_keys=True)
            )
        except (CompanionReviewCodecError, TypeError, ValueError):
            stored = None
        if stored is not None:
            suggestions.append(
                _suggestion_item(
                    SimpleNamespace(
                        analysis_run_id=run_id,
                        completed_at_ms=0,
                        provider_id="",
                        model_id="",
                        prompt_version="",
                        title=stored.title,
                        description=stored.description,
                        tags=tuple(
                            MappedSuggestedTag(
                                value=tag,
                                status=MappedTagStatus.UNKNOWN,
                                key=None,
                                display_name=None,
                            )
                            for tag in stored.tags
                        ),
                        suggested_filename=stored.suggested_filename,
                    )
                )
            )
    return JSONResponse(
        status_code=200,
        content=MediaAiSuggestionListResponse(
            suggestions=suggestions,
            next_cursor=None,
        ).model_dump(),
        headers=_NO_STORE_HEADERS,
    )


def _audience_error(
    request: Request,
    media_id: MediaId,
    policy: ContentAudiencePolicy | None,
) -> JSONResponse | None:
    try:
        if content_audience_allows(
            request=request,
            media_id=media_id,
            policy=policy,
        ):
            return None
        return _error("MEDIA_NOT_FOUND", "Media was not found.", 404)
    except ContentAudienceUnavailableError:
        return _error(
            "ANALYSIS_STATUS_UNAVAILABLE",
            "Analysis status is unavailable.",
            503,
        )
