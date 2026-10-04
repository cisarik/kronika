"""Focused lifecycle tests for durable movie-identification runs."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest

from kronika.application.media_content import supported_media_type
from kronika.application.media_suggestion import (
    MediaSuggestionProviderUnavailableError,
)
from kronika.application.movie_identification import (
    LocalMovieHints,
    MovieIdentificationRequest,
    MovieIdentificationSuggestion,
)
from kronika.application import movie_identification_lifecycle
from kronika.application.movie_identification_lifecycle import (
    ExecuteMovieIdentificationRun,
)
from kronika.application.ports.movie_identification import PreparedMovieIdentification
from kronika.domain.identities import (
    DeviceId,
    LibraryId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.libraries import Library, LibraryPathFlavor, LibraryRoot
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath as DomainMediaRelativePath,
)
from kronika.domain.media_analysis_runs import (
    MediaAnalysisRun,
    MediaAnalysisRunId,
    MediaAnalysisRunState,
)
from kronika.domain.media_classification import (
    CONTACT_SHEET_DERIVATIVE_STRATEGY,
    IdentificationConfidence,
    MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
    MOVIE_IDENTIFICATION_PROMPT_VERSION,
    MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
    MovieIdentificationStatus,
)
from kronika.infrastructure.ai.nvidia_nim import build_nvidia_movie_identification_body
from kronika.infrastructure.media_analysis.contact_sheet import compose_contact_sheet
from kronika.application.media_analysis import build_representative_frame
import io
from PIL import Image


MEDIA_ID = MediaId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LOCATION_ID = MediaLocationId.from_string("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
LIBRARY_ID = LibraryId.from_string("12345678-1234-4234-9234-123456789abc")
DEVICE_ID = DeviceId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef")
RUN_ID = MediaAnalysisRunId("11111111-1111-4111-8111-111111111111")


def _png(color: tuple[int, int, int] = (40, 120, 80)) -> bytes:
    image = Image.new("RGB", (48, 32), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _contact_sheet():
    frames = tuple(
        build_representative_frame(timestamp_ms=i * 100, payload=_png((30 + i * 40, 90, 120)))
        for i in range(3)
    )
    return compose_contact_sheet(frames)


def _suggestion() -> MovieIdentificationSuggestion:
    return MovieIdentificationSuggestion(
        identified_title="Synthetic Movie",
        release_year=2001,
        identification_status=MovieIdentificationStatus.IDENTIFIED,
        confidence=IdentificationConfidence.HIGH,
        candidate_titles=(),
        genres=(),
        description="Synthetic confident identification.",
        tags=("Cinema",),
        evidence_summary="Opening title card.",
        provider_id="fake-provider",
        model_id="fake-model",
        prompt_version=MOVIE_IDENTIFICATION_PROMPT_VERSION,
        result_schema_version=MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION,
        derivative_count=1,
        reasoning_enabled=True,
    )


def _library() -> Library:
    return Library(
        id=LIBRARY_ID,
        device_id=DEVICE_ID,
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


def _media(kind: MediaKind = MediaKind.VIDEO) -> LogicalMedia:
    return LogicalMedia(id=MEDIA_ID, kind=kind, created_at_ms=10, updated_at_ms=10)


def _location(*, relative_path: str = "clips/sample.mp4") -> MediaLocation:
    return MediaLocation(
        id=LOCATION_ID,
        media_id=MEDIA_ID,
        library_id=LIBRARY_ID,
        relative_path=DomainMediaRelativePath(relative_path),
        availability=MediaLocationAvailability.AVAILABLE,
        observed_size_bytes=100,
        observed_mtime_ns=200,
        created_at_ms=10,
        updated_at_ms=10,
    )


def _pending_run() -> MediaAnalysisRun:
    return MediaAnalysisRun(
        id=RUN_ID,
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=MOVIE_IDENTIFICATION_ANALYSIS_DEFINITION,
        state=MediaAnalysisRunState.PENDING,
        attempt_count=0,
        provider_id=None,
        model_id=None,
        prompt_version=None,
        result_schema_version=None,
        result_json=None,
        error_code=None,
        error_message=None,
        created_at_ms=10,
        started_at_ms=None,
        completed_at_ms=None,
        version=1,
    )


@dataclass
class _FakeMediaRepository:
    media: LogicalMedia
    location: MediaLocation

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        if media_id == self.media.id:
            return self.media
        return None

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        if location_id == self.location.id:
            return self.location
        return None


@dataclass
class _FakeLibraryRepository:
    library: Library

    def get(self, library_id: LibraryId) -> Library | None:
        if library_id == self.library.id:
            return self.library
        return None


@dataclass
class _FakePreparer:
    prepared: PreparedMovieIdentification
    calls: list[tuple[Any, Any]]

    def prepare(self, root, relative_path) -> PreparedMovieIdentification:
        self.calls.append((root, relative_path))
        return self.prepared


@dataclass
class _FakeProvider:
    suggestion: MovieIdentificationSuggestion
    calls: list[MovieIdentificationRequest]
    error: Exception | None = None
    network_calls: int = 0

    def identify_movie(
        self, request: MovieIdentificationRequest
    ) -> MovieIdentificationSuggestion:
        self.calls.append(request)
        if self.error is not None:
            raise self.error
        return self.suggestion


class _FakeRepository:
    def __init__(self, run: MediaAnalysisRun) -> None:
        self.run = run
        self.failed_kwargs: dict[str, Any] | None = None
        self.analyzed_kwargs: dict[str, Any] | None = None

    def claim_pending(self, *, run_id, expected_version, started_at_ms, max_attempts):
        del run_id, max_attempts
        assert self.run.version == expected_version
        self.run = replace(
            self.run,
            state=MediaAnalysisRunState.ANALYZING,
            attempt_count=self.run.attempt_count + 1,
            started_at_ms=started_at_ms,
            version=self.run.version + 1,
        )
        return self.run

    def record_analyzed(self, **kwargs):
        self.analyzed_kwargs = kwargs
        self.run = replace(
            self.run,
            state=MediaAnalysisRunState.ANALYZED,
            provider_id=kwargs["provider_id"],
            model_id=kwargs["model_id"],
            prompt_version=kwargs["prompt_version"],
            result_schema_version=kwargs["result_schema_version"],
            result_json=kwargs["result_json"],
            completed_at_ms=kwargs["completed_at_ms"],
            analysis_profile=kwargs.get("analysis_profile"),
            reasoning_enabled=kwargs.get("reasoning_enabled"),
            derivative_strategy=kwargs.get("derivative_strategy"),
            derivative_count=kwargs.get("derivative_count"),
            provider_submission_occurred=kwargs.get("provider_submission_occurred"),
            version=self.run.version + 1,
        )
        return self.run

    def record_failed(self, **kwargs):
        self.failed_kwargs = kwargs
        self.run = replace(
            self.run,
            state=MediaAnalysisRunState.FAILED,
            error_code=kwargs["error_code"],
            error_message=kwargs["error_message"],
            provider_id=kwargs["provider_id"],
            model_id=kwargs["model_id"],
            prompt_version=kwargs["prompt_version"],
            completed_at_ms=kwargs["completed_at_ms"],
            provider_submission_occurred=kwargs.get("provider_submission_occurred"),
            version=self.run.version + 1,
        )
        return self.run


def _executor(
    *,
    relative_path: str = "clips/sample.mp4",
    kind: MediaKind = MediaKind.VIDEO,
    provider_error: Exception | None = None,
) -> tuple[ExecuteMovieIdentificationRun, _FakeRepository, _FakeProvider, _FakePreparer]:
    sheet = _contact_sheet()
    prepared = PreparedMovieIdentification(
        basename="sample.mp4",
        contact_sheet=sheet,
        hints=LocalMovieHints(
            filename_stem="sample",
            container_title=None,
            duration_ms=3000,
            width=48,
            height=32,
        ),
        warnings=(),
    )
    repository = _FakeRepository(_pending_run())
    provider = _FakeProvider(_suggestion(), calls=[], error=provider_error)
    preparer = _FakePreparer(prepared, calls=[])
    executor = ExecuteMovieIdentificationRun(
        repository=repository,
        media_repository=_FakeMediaRepository(_media(kind), _location(relative_path=relative_path)),
        library_repository=_FakeLibraryRepository(_library()),
        preparer=preparer,
        provider=provider,
        now_ms=lambda: 99,
    )
    return executor, repository, provider, preparer


def test_prior_one_argument_supported_media_type_call_raises_typeerror() -> None:
    with pytest.raises(TypeError, match="extension"):
        supported_media_type("clips/sample.mp4")  # type: ignore[misc, arg-type]


def test_movie_identification_accepts_eligible_published_video(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        movie_identification_lifecycle.LOGGER,
        "emit",
        lambda **fields: events.append(fields),
    )
    executor, repository, provider, preparer = _executor()
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.ANALYZED
    assert len(preparer.calls) == 1
    assert len(provider.calls) == 1
    assert provider.network_calls == 0
    assert repository.analyzed_kwargs is not None
    assert repository.analyzed_kwargs["reasoning_enabled"] is True
    assert repository.analyzed_kwargs["derivative_strategy"] == CONTACT_SHEET_DERIVATIVE_STRATEGY
    assert repository.analyzed_kwargs["derivative_count"] == 1
    assert repository.analyzed_kwargs["provider_submission_occurred"] is True

    request = provider.calls[0]
    body = build_nvidia_movie_identification_body(request, model_id="fake-model")
    assert body["chat_template_kwargs"]["enable_thinking"] is True
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["reasoning_budget"] == 2048
    assert body["max_tokens"] == 4096
    assert body["temperature"] == 0.6
    assert body["top_p"] == 0.95
    assert "top_k" not in body
    assert "thinking_token_budget" not in body
    images = [
        part for part in body["messages"][0]["content"] if part.get("type") == "image_url"
    ]
    assert len(images) == 1
    assert len(events) == 1
    assert events[0]["level"] == "INFO"
    assert events[0]["error_code"] is None
    assert events[0]["context"] == {
        "run_uuid": RUN_ID.to_string(),
        "supersedes_run_uuid": None,
        "lineage": "primary",
        "terminal_state": "analyzed",
        "terminal_domain_error": None,
        "provider_submission_occurred": True,
        "durable_suggestion_exists": True,
    }
    assert "result_json" not in events[0]["context"]


def test_movie_identification_rejects_unsupported_media_closed() -> None:
    executor, repository, provider, preparer = _executor(relative_path="clips/sample.txt")
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PREPARATION_UNAVAILABLE"
    assert len(preparer.calls) == 0
    assert len(provider.calls) == 0
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is False


def test_movie_identification_rejects_non_video_kind() -> None:
    executor, repository, provider, preparer = _executor(kind=MediaKind.IMAGE)
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PREPARATION_UNAVAILABLE"
    assert len(preparer.calls) == 0
    assert len(provider.calls) == 0
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is False


def test_provider_unavailable_marks_submission_attempted() -> None:
    executor, repository, provider, preparer = _executor(
        provider_error=MediaSuggestionProviderUnavailableError("unavailable")
    )
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PROVIDER_UNAVAILABLE"
    assert len(preparer.calls) == 1
    assert len(provider.calls) == 1
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is True
    assert repository.failed_kwargs["analysis_profile"] == "movie_identification"
    assert repository.failed_kwargs["reasoning_enabled"] is True
    assert repository.failed_kwargs["derivative_strategy"] == CONTACT_SHEET_DERIVATIVE_STRATEGY
    assert repository.failed_kwargs["derivative_count"] == 1
    assert repository.failed_kwargs["provider_id"] is None
    assert repository.failed_kwargs["model_id"] is None
    assert repository.failed_kwargs["prompt_version"] == (
        "framenest-movie-identification-prompt-v2"
    )


def test_submitted_failure_persists_safe_provider_provenance() -> None:
    from kronika.domain.media_classification import MOVIE_IDENTIFICATION_PROMPT_VERSION

    executor, repository, provider, preparer = _executor(
        provider_error=MediaSuggestionProviderUnavailableError("unavailable")
    )
    object.__setattr__(executor, "provider_id", "nvidia-nim")
    object.__setattr__(executor, "model_id", "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning")
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is True
    assert repository.failed_kwargs["provider_id"] == "nvidia-nim"
    assert repository.failed_kwargs["model_id"] == (
        "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning"
    )
    assert repository.failed_kwargs["prompt_version"] == MOVIE_IDENTIFICATION_PROMPT_VERSION
    assert len(preparer.calls) == 1
    assert len(provider.calls) == 1


def test_empty_provider_response_classified_distinctly() -> None:
    from kronika.application.media_suggestion import (
        MediaSuggestionProviderEmptyResponseError,
    )

    executor, repository, provider, preparer = _executor(
        provider_error=MediaSuggestionProviderEmptyResponseError("empty")
    )
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PROVIDER_RESPONSE_EMPTY"
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is True


def test_truncated_provider_response_classified_distinctly() -> None:
    from kronika.application.media_suggestion import (
        MediaSuggestionProviderTruncatedResponseError,
    )

    executor, repository, provider, preparer = _executor(
        provider_error=MediaSuggestionProviderTruncatedResponseError("truncated")
    )
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PROVIDER_RESPONSE_TRUNCATED"
    assert len(preparer.calls) == 1
    assert len(provider.calls) == 1
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is True
    assert repository.failed_kwargs["reasoning_enabled"] is True
    assert repository.failed_kwargs["analysis_profile"] == "movie_identification"
    assert repository.failed_kwargs["derivative_count"] == 1


def test_invalid_provider_response_remains_distinct_from_truncation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.application.media_suggestion import (
        MediaSuggestionProviderInvalidResponseError,
    )

    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        movie_identification_lifecycle.LOGGER,
        "emit",
        lambda **fields: events.append(fields),
    )
    executor, repository, provider, preparer = _executor(
        provider_error=MediaSuggestionProviderInvalidResponseError("invalid")
    )
    result = executor.execute(repository.run)

    assert result.state is MediaAnalysisRunState.FAILED
    assert result.error_code == "PROVIDER_INVALID_RESPONSE"
    assert repository.failed_kwargs is not None
    assert repository.failed_kwargs["provider_submission_occurred"] is True
    assert type(provider.error) is MediaSuggestionProviderInvalidResponseError
    assert len(preparer.calls) == 1
    assert len(events) == 1
    assert events[0]["level"] == "WARNING"
    assert events[0]["error_code"] == "PROVIDER_INVALID_RESPONSE"
    assert events[0]["context"] == {
        "run_uuid": RUN_ID.to_string(),
        "supersedes_run_uuid": None,
        "lineage": "primary",
        "terminal_state": "failed",
        "terminal_domain_error": "PROVIDER_INVALID_RESPONSE",
        "provider_submission_occurred": True,
        "durable_suggestion_exists": False,
    }
