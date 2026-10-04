"""Application services for durable automatic post-catalog analysis."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
import json
from typing import Protocol

from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisNotFoundError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
)
from kronika.application.media_content import supported_media_type
from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    MediaSuggestion,
    MediaSuggestionPreparationFailedError,
    MediaSuggestionPreparationUnavailableError,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderModelUnavailableError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    PROMPT_VERSION,
    build_suggestion_request,
    source_extension,
)
from kronika.application.ports.library_repository import LibraryRepository
from kronika.application.ports.media_analysis import LocalMediaAnalysisPreparer
from kronika.application.ports.media_analysis_runs import (
    FrameNestMediaAnalysisRunRepositoryError,
    MediaAnalysisRunConflictError,
    MediaAnalysisRunRepository,
)
from kronika.application.ports.media_metadata_repository import (
    MediaMetadataMediaNotFoundError,
    MediaMetadataRepository,
)
from kronika.application.ports.media_repository import MediaRepository
from kronika.application.ports.media_suggestion import MediaSuggestionProvider
from kronika.application.upload_transport import default_now_ms
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media import MediaLocationAvailability
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    DEFAULT_MAX_ANALYSIS_ATTEMPTS,
    MAX_CONFIGURED_ANALYSIS_ATTEMPTS,
    MediaAnalysisRun,
    MediaAnalysisRunId,
    MediaAnalysisRunState,
    RESULT_SCHEMA_VERSION,
    TERMINAL_ANALYSIS_RUN_STATES,
)
from kronika.domain.media_classification import (
    AnalysisProfile,
    CONTACT_SHEET_DERIVATIVE_STRATEGY,
    GENERIC_DERIVATIVE_STRATEGY,
    GENERIC_MEDIA_ANALYSIS_DEFINITION,
    MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
    ContentCategory,
)


class MediaAnalysisLifecycleError(RuntimeError):
    """Sanitized automatic-analysis lifecycle failure."""


class MediaAnalysisLifecycleDisabledError(MediaAnalysisLifecycleError):
    """Raised when automatic analysis is not enabled."""


class MediaAnalysisLifecycleNotConfiguredError(MediaAnalysisLifecycleError):
    """Raised when no provider is configured for automatic analysis."""


class MediaAnalysisLifecycleModelCapabilityError(MediaAnalysisLifecycleError):
    """Raised when the selected provider model cannot analyze images."""


@dataclass(frozen=True, slots=True)
class CatalogedAnalysisTarget:
    """Catalog identities eligible for automatic analysis after cataloging."""

    media_id: MediaId
    media_location_id: MediaLocationId


@dataclass(frozen=True, slots=True)
class AutomaticAnalysisPublicView:
    """Sanitized public representation of automatic analysis state."""

    state: str
    analysis_definition: str | None
    provider_id: str | None
    model_id: str | None
    prompt_version: str | None
    result: dict[str, object] | None
    error_code: str | None
    error_message: str | None
    attempt_count: int | None
    created_at_ms: int | None
    started_at_ms: int | None
    completed_at_ms: int | None
    analysis_profile: str | None = None
    reasoning_enabled: bool | None = None
    derivative_strategy: str | None = None
    derivative_count: int | None = None
    provider_submission_occurred: bool | None = None


class _SuggestionExecutor(Protocol):
    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> MediaSuggestion:
        """Prepare locally and invoke the configured provider outside DB writes."""


RETRYABLE_ERROR_CODES = frozenset(
    {
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_RATE_LIMITED",
    }
)


def serialize_suggestion_result(suggestion: MediaSuggestion) -> str:
    """Serialize a normalized suggestion for durable storage."""
    payload = {
        "title": suggestion.title,
        "description": suggestion.description,
        "collection": suggestion.collection,
        "tags": list(suggestion.tags),
        "suggested_filename": suggestion.suggested_filename,
        "confidence": suggestion.confidence,
        "evidence": list(suggestion.evidence),
        "uncertainties": list(suggestion.uncertainties),
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def deserialize_suggestion_result(result_json: str) -> dict[str, object]:
    """Parse a durable normalized suggestion payload for public responses."""
    payload = json.loads(result_json)
    if not isinstance(payload, dict):
        raise MediaAnalysisLifecycleError("analysis result invalid")
    return payload


def public_view_from_run(run: MediaAnalysisRun | None) -> AutomaticAnalysisPublicView:
    """Map absence or a persisted run into a truthful public view."""
    if run is None:
        return AutomaticAnalysisPublicView(
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
    result = None
    error_code = None
    error_message = None
    if run.state is MediaAnalysisRunState.ANALYZED and run.result_json is not None:
        result = deserialize_suggestion_result(run.result_json)
    if run.state is MediaAnalysisRunState.FAILED:
        error_code = run.error_code
        error_message = run.error_message
    return AutomaticAnalysisPublicView(
        state=run.state.value,
        analysis_definition=run.analysis_definition,
        provider_id=run.provider_id,
        model_id=run.model_id,
        prompt_version=run.prompt_version,
        result=result,
        error_code=error_code,
        error_message=error_message,
        attempt_count=run.attempt_count,
        created_at_ms=run.created_at_ms,
        started_at_ms=run.started_at_ms,
        completed_at_ms=run.completed_at_ms,
        analysis_profile=run.analysis_profile,
        reasoning_enabled=run.reasoning_enabled,
        derivative_strategy=run.derivative_strategy,
        derivative_count=run.derivative_count,
        provider_submission_occurred=run.provider_submission_occurred,
    )


class ScheduleAutomaticMediaAnalysis:
    """Idempotently create a pending automatic analysis after cataloging."""

    def __init__(
        self,
        repository: MediaAnalysisRunRepository,
        *,
        enabled: bool | Callable[[], bool],
        now_ms: Callable[[], int] = default_now_ms,
        analysis_definition: str = AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    ) -> None:
        self._repository = repository
        self._enabled = enabled
        self._now_ms = now_ms
        self._analysis_definition = analysis_definition

    @property
    def enabled(self) -> bool:
        flag = self._enabled
        if callable(flag) and not isinstance(flag, bool):
            return bool(flag())
        return bool(flag)

    def execute(self, target: CatalogedAnalysisTarget) -> MediaAnalysisRun | None:
        if not self.enabled:
            return None
        try:
            return self._repository.create_pending(
                media_id=target.media_id,
                media_location_id=target.media_location_id,
                analysis_definition=self._analysis_definition,
                created_at_ms=self._now_ms(),
            )
        except FrameNestMediaAnalysisRunRepositoryError as exc:
            raise MediaAnalysisLifecycleError(
                "automatic analysis schedule failed"
            ) from exc


class RequestManualMediaAnalysis:
    """Explicitly request one durable analysis run for cataloged media.

    Independent of automatic post-catalog enablement so operators can analyze
    one item while FRAMENEST_AUTOMATIC_MEDIA_ANALYSIS_ENABLED remains false.

    Terminal historical runs remain immutable evidence. An explicit manual
    request creates a new pending run with durable supersession lineage when
    the latest matching run is terminal, and remains idempotent while an
    active matching run already exists.
    """

    def __init__(
        self,
        repository: MediaAnalysisRunRepository,
        *,
        now_ms: Callable[[], int] = default_now_ms,
        analysis_definition: str = AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    ) -> None:
        self._repository = repository
        self._now_ms = now_ms
        self._analysis_definition = analysis_definition

    def execute(self, target: CatalogedAnalysisTarget) -> MediaAnalysisRun:
        try:
            return self._repository.create_manual_pending(
                media_id=target.media_id,
                media_location_id=target.media_location_id,
                analysis_definition=self._analysis_definition,
                created_at_ms=self._now_ms(),
            )
        except FrameNestMediaAnalysisRunRepositoryError as exc:
            raise MediaAnalysisLifecycleError(
                "manual analysis request failed"
            ) from exc


class PersistImportedPreviewAnalysis:
    """Record one companion-visible generic run from an already obtained suggestion.

    Does not invoke the provider and does not write canonical metadata. Movies
    are skipped using the same content-category exclusion companion uses.
    """

    def __init__(
        self,
        repository: MediaAnalysisRunRepository,
        metadata_repository: MediaMetadataRepository,
        *,
        now_ms: Callable[[], int] = default_now_ms,
        max_attempts: int = DEFAULT_MAX_ANALYSIS_ATTEMPTS,
        analysis_definition: str = AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    ) -> None:
        if (
            isinstance(max_attempts, bool)
            or max_attempts < 1
            or max_attempts > MAX_CONFIGURED_ANALYSIS_ATTEMPTS
        ):
            raise ValueError("max analysis attempts must be a positive bounded integer")
        self._repository = repository
        self._metadata_repository = metadata_repository
        self._now_ms = now_ms
        self._max_attempts = max_attempts
        self._manual = RequestManualMediaAnalysis(
            repository,
            now_ms=now_ms,
            analysis_definition=analysis_definition,
        )

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
        suggestion: MediaSuggestion,
    ) -> MediaAnalysisRun | None:
        try:
            snapshot = self._metadata_repository.get_media_metadata(media_id)
        except MediaMetadataMediaNotFoundError:
            snapshot = None
        if snapshot is not None and snapshot.content_category is ContentCategory.MOVIE:
            return None
        pending = self._manual.execute(
            CatalogedAnalysisTarget(media_id=media_id, media_location_id=location_id)
        )
        if pending.state is not MediaAnalysisRunState.PENDING:
            return None
        try:
            claimed = self._repository.claim_pending(
                run_id=pending.id.to_string(),
                expected_version=pending.version,
                started_at_ms=self._now_ms(),
                max_attempts=self._max_attempts,
            )
            return self._repository.record_analyzed(
                run_id=claimed.id.to_string(),
                expected_version=claimed.version,
                provider_id=suggestion.provider_id,
                model_id=suggestion.model_id,
                prompt_version=suggestion.prompt_version,
                result_schema_version=RESULT_SCHEMA_VERSION,
                result_json=serialize_suggestion_result(suggestion),
                completed_at_ms=self._now_ms(),
                analysis_profile=AnalysisProfile.GENERIC_MEDIA.value,
                reasoning_enabled=False,
                derivative_strategy=GENERIC_DERIVATIVE_STRATEGY,
                derivative_count=None,
                provider_submission_occurred=True,
            )
        except FrameNestMediaAnalysisRunRepositoryError as exc:
            raise MediaAnalysisLifecycleError(
                "imported preview analysis persistence failed"
            ) from exc


class ExecuteAutomaticMediaAnalysisRun:
    """Claim, execute outside the DB transaction, and persist terminal truth."""

    def __init__(
        self,
        repository: MediaAnalysisRunRepository,
        executor: _SuggestionExecutor,
        *,
        max_attempts: int = DEFAULT_MAX_ANALYSIS_ATTEMPTS,
        now_ms: Callable[[], int] = default_now_ms,
        in_transaction: Callable[[], bool] | None = None,
    ) -> None:
        if (
            isinstance(max_attempts, bool)
            or max_attempts < 1
            or max_attempts > MAX_CONFIGURED_ANALYSIS_ATTEMPTS
        ):
            raise ValueError("max analysis attempts must be a positive bounded integer")
        self._repository = repository
        self._executor = executor
        self._max_attempts = max_attempts
        self._now_ms = now_ms
        self._in_transaction = in_transaction or (lambda: False)

    def reconcile_interrupted(self, run: MediaAnalysisRun) -> MediaAnalysisRun:
        if run.state is not MediaAnalysisRunState.ANALYZING:
            return run
        try:
            return self._repository.reset_interrupted_analyzing(
                run_id=run.id.to_string(),
                expected_version=run.version,
                max_attempts=self._max_attempts,
                updated_at_ms=self._now_ms(),
            )
        except MediaAnalysisRunConflictError:
            current = self._repository.get_by_media_definition(
                run.media_id,
                run.analysis_definition,
            )
            if current is None:
                raise
            return current

    def execute(self, run: MediaAnalysisRun) -> MediaAnalysisRun:
        if run.state in TERMINAL_ANALYSIS_RUN_STATES:
            return run
        working = run
        if working.state is MediaAnalysisRunState.ANALYZING:
            working = self.reconcile_interrupted(working)
            if working.state in TERMINAL_ANALYSIS_RUN_STATES:
                return working
        if working.state is not MediaAnalysisRunState.PENDING:
            return working
        claimed = self._repository.claim_pending(
            run_id=working.id.to_string(),
            expected_version=working.version,
            started_at_ms=self._now_ms(),
            max_attempts=self._max_attempts,
        )
        if self._in_transaction():
            raise MediaAnalysisLifecycleError(
                "provider execution must not run inside a database transaction"
            )
        try:
            suggestion = self._executor.execute(
                claimed.media_id,
                claimed.media_location_id,
            )
        except Exception as exc:
            return self._persist_failure(claimed, exc)
        try:
            return self._repository.record_analyzed(
                run_id=claimed.id.to_string(),
                expected_version=claimed.version,
                provider_id=suggestion.provider_id,
                model_id=suggestion.model_id,
                prompt_version=suggestion.prompt_version,
                result_schema_version=RESULT_SCHEMA_VERSION,
                result_json=serialize_suggestion_result(suggestion),
                completed_at_ms=self._now_ms(),
                analysis_profile=AnalysisProfile.GENERIC_MEDIA.value,
                reasoning_enabled=False,
                derivative_strategy=GENERIC_DERIVATIVE_STRATEGY,
                derivative_count=None,
                provider_submission_occurred=True,
            )
        except FrameNestMediaAnalysisRunRepositoryError as exc:
            raise MediaAnalysisLifecycleError(
                "automatic analysis persistence failed"
            ) from exc

    def _persist_failure(
        self,
        claimed: MediaAnalysisRun,
        exc: Exception,
    ) -> MediaAnalysisRun:
        error_code, error_message, retryable = _classify_failure(exc)
        if (
            retryable
            and claimed.attempt_count < self._max_attempts
            and error_code in RETRYABLE_ERROR_CODES
        ):
            try:
                return self._repository.requeue_for_retry(
                    run_id=claimed.id.to_string(),
                    expected_version=claimed.version,
                    error_code=error_code,
                    error_message=error_message,
                    updated_at_ms=self._now_ms(),
                )
            except FrameNestMediaAnalysisRunRepositoryError as persist_exc:
                raise MediaAnalysisLifecycleError(
                    "automatic analysis retry persistence failed"
                ) from persist_exc
        try:
            return self._repository.record_failed(
                run_id=claimed.id.to_string(),
                expected_version=claimed.version,
                error_code=error_code,
                error_message=error_message,
                provider_id=None,
                model_id=None,
                prompt_version=PROMPT_VERSION,
                completed_at_ms=self._now_ms(),
                provider_submission_occurred=_provider_submission_occurred(error_code),
            )
        except FrameNestMediaAnalysisRunRepositoryError as persist_exc:
            raise MediaAnalysisLifecycleError(
                "automatic analysis failure persistence failed"
            ) from persist_exc


class AutomaticImportedMediaSuggestionExecutor:
    """Reuse imported-media preparation and the configured provider."""

    def __init__(
        self,
        media_repository: MediaRepository,
        library_repository: LibraryRepository,
        preparer: LocalMediaAnalysisPreparer,
        provider: MediaSuggestionProvider | None,
        read_model_capabilities: Callable[[], Sequence[str]] | None = None,
    ) -> None:
        self._media_repository = media_repository
        self._library_repository = library_repository
        self._preparer = preparer
        self._provider = provider
        self._read_model_capabilities = read_model_capabilities

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> MediaSuggestion:
        if self._provider is None:
            raise MediaAnalysisLifecycleNotConfiguredError(
                "automatic analysis provider is not configured"
            )
        if self._read_model_capabilities is not None:
            capabilities = self._read_model_capabilities()
            if "vision_input" not in capabilities:
                raise MediaAnalysisLifecycleModelCapabilityError(
                    "AI provider model does not support image analysis"
                )
        media = self._media_repository.get_media(media_id)
        if media is None:
            raise MediaSuggestionPreparationUnavailableError(
                "media is unavailable for automatic analysis"
            )
        location = self._media_repository.get_location(location_id)
        if location is None or location.media_id != media_id:
            raise MediaSuggestionPreparationUnavailableError(
                "media location is unavailable for automatic analysis"
            )
        if location.availability != MediaLocationAvailability.AVAILABLE:
            raise MediaSuggestionPreparationUnavailableError(
                "media location is unavailable for automatic analysis"
            )
        library = self._library_repository.get(location.library_id)
        if library is None:
            raise MediaSuggestionPreparationUnavailableError(
                "media library is unavailable for automatic analysis"
            )
        extension = source_extension(MediaRelativePath(location.relative_path.value))
        if supported_media_type(media.kind, extension) is None:
            raise FrameNestMediaSuggestionError(
                "media type is unsupported for automatic analysis"
            )
        try:
            prepared = self._preparer.prepare(
                library.root,
                MediaRelativePath(location.relative_path.value),
            )
        except MediaAnalysisNotFoundError as exc:
            raise MediaSuggestionPreparationUnavailableError(
                "media is unavailable for automatic analysis"
            ) from exc
        except MediaAnalysisUnavailableError as exc:
            raise MediaSuggestionPreparationUnavailableError(
                "media preparation is unavailable"
            ) from exc
        except MediaAnalysisFailedError as exc:
            raise MediaSuggestionPreparationFailedError(
                "media preparation failed"
            ) from exc
        except FrameNestMediaAnalysisError as exc:
            raise MediaSuggestionPreparationUnavailableError(
                "media preparation is unavailable"
            ) from exc
        request = build_suggestion_request(prepared)
        return self._provider.suggest(request)


class ReadAutomaticMediaAnalysis:
    """Read the sanitized public view for one media item."""

    def __init__(
        self,
        repository: MediaAnalysisRunRepository,
        *,
        analysis_definition: str = AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    ) -> None:
        self._repository = repository
        self._analysis_definition = analysis_definition

    def execute(self, media_id: MediaId) -> AutomaticAnalysisPublicView:
        run = self._repository.get_by_media_definition(
            media_id,
            self._analysis_definition,
        )
        return public_view_from_run(run)


def _classify_failure(exc: Exception) -> tuple[str, str, bool]:
    if isinstance(exc, MediaAnalysisLifecycleNotConfiguredError):
        return "PROVIDER_NOT_CONFIGURED", "AI provider is not configured.", False
    if isinstance(exc, MediaAnalysisLifecycleModelCapabilityError):
        return (
            "PROVIDER_MODEL_CAPABILITY_MISSING",
            "AI provider model does not support image analysis.",
            False,
        )
    if isinstance(exc, MediaSuggestionProviderAuthError):
        return "PROVIDER_AUTH", "AI provider authentication failed.", False
    if isinstance(exc, MediaSuggestionProviderRateLimitedError):
        return "PROVIDER_RATE_LIMITED", "AI provider rate limited the request.", True
    if isinstance(exc, MediaSuggestionProviderModelUnavailableError):
        return "PROVIDER_MODEL_UNAVAILABLE", "AI provider model is unavailable.", False
    if isinstance(exc, MediaSuggestionProviderUnavailableError):
        return "PROVIDER_UNAVAILABLE", "AI provider is temporarily unavailable.", True
    if isinstance(exc, MediaSuggestionProviderInvalidResponseError):
        return "PROVIDER_INVALID_RESPONSE", "AI provider returned an invalid response.", False
    if isinstance(exc, MediaSuggestionProviderFailedError):
        return "PROVIDER_FAILED", "AI provider failed to analyze the media.", False
    if isinstance(exc, MediaSuggestionPreparationFailedError):
        return "PREPARATION_FAILED", "Local media preparation failed.", False
    if isinstance(
        exc,
        (
            MediaSuggestionPreparationUnavailableError,
            FrameNestMediaSuggestionError,
            FrameNestMediaAnalysisError,
        ),
    ):
        return "PREPARATION_UNAVAILABLE", "Local media preparation is unavailable.", False
    return "ANALYSIS_FAILED", "Automatic analysis failed.", False


_PROVIDER_SUBMISSION_ERROR_CODES = frozenset(
    {
        "PROVIDER_AUTH",
        "PROVIDER_RATE_LIMITED",
        "PROVIDER_MODEL_UNAVAILABLE",
        "PROVIDER_UNAVAILABLE",
        "PROVIDER_INVALID_RESPONSE",
        "PROVIDER_FAILED",
    }
)


def _provider_submission_occurred(error_code: str) -> bool:
    """True only when failure classification implies the provider adapter was entered."""
    return error_code in _PROVIDER_SUBMISSION_ERROR_CODES
