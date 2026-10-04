"""Contract tests for durable automatic media analysis status API."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.library_api import LibraryApiDependencies
from kronika.adapters.api.media_analysis_api import MediaAnalysisApiDependencies
from kronika.adapters.api.media_analysis_lifecycle_api import (
    MediaAnalysisLifecycleApiDependencies,
)
from kronika.adapters.api.media_suggestion_api import MediaSuggestionApiDependencies
from kronika.application.media_analysis_lifecycle import (
    AutomaticAnalysisPublicView,
    ReadAutomaticMediaAnalysis,
)
from kronika.configuration import KronikaSettings
from kronika.domain.identities import MediaId

CANONICAL_MEDIA_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PRIVATE_PATH = "/Users/example/private/videos/secret.mp4"
SECRET = "sk-test-secret-value"


class _FakeReadAnalysis:
    def __init__(self, view: AutomaticAnalysisPublicView) -> None:
        self.view = view
        self.calls: list[MediaId] = []

    def execute(self, media_id: MediaId) -> AutomaticAnalysisPublicView:
        self.calls.append(media_id)
        return self.view



def _record_policy():
    from tests.support.record_access import scoped_policy

    return scoped_policy({CANONICAL_MEDIA_ID})


def _with_admin(app):
    from kronika.domain.identity_access import ROLE_ADMIN
    from tests.support.record_access import install_synthetic_caller

    return install_synthetic_caller(app, "ada", role=ROLE_ADMIN)


def _client(view: AutomaticAnalysisPublicView, *, enabled: bool = True) -> TestClient:
    reader = _FakeReadAnalysis(view)
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=Path("/tmp/framenest-analysis-lifecycle-api.sqlite3"),
        automatic_media_analysis_enabled=enabled,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=object(),  # type: ignore[arg-type]
            scan_preview=object(),
            catalog_available=lambda: True,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=object(),
            catalog_available=lambda: True,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=None,
            provider_configured=False,
        ),
        media_analysis_lifecycle_api_dependencies=MediaAnalysisLifecycleApiDependencies(
            read_analysis=reader,  # type: ignore[arg-type]
            automatic_analysis_enabled=enabled,
            provider_configured=True,
            provider_id="nvidia-nim",
            model_id="test-model",
            audience_policy=_record_policy(),
        ),
    )
    return TestClient(_with_admin(app))


def test_capability_and_not_requested_status() -> None:
    client = _client(
        AutomaticAnalysisPublicView(
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
        ),
        enabled=False,
    )
    capability = client.get("/api/ai/automatic-analysis-capability")
    assert capability.status_code == 200
    assert capability.json() == {
        "automatic_analysis_enabled": False,
        "analysis_definition": "automatic_post_catalog",
        "result_schema_version": "framenest-media-suggestion-result-v1",
        "provider_configured": True,
        "provider_id": "nvidia-nim",
        "model_id": "test-model",
    }
    status = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis")
    assert status.status_code == 200
    payload = status.json()
    assert payload["state"] == "not_requested"
    assert payload["result"] is None
    assert payload["error_code"] is None
    assert SECRET not in status.text
    assert PRIVATE_PATH not in status.text


def test_pending_and_analyzing_omit_result() -> None:
    for state in ("pending", "analyzing"):
        client = _client(
            AutomaticAnalysisPublicView(
                state=state,
                analysis_definition="automatic_post_catalog",
                provider_id=None,
                model_id=None,
                prompt_version=None,
                result=None,
                error_code=None,
                error_message=None,
                attempt_count=1 if state == "analyzing" else 0,
                created_at_ms=10,
                started_at_ms=11 if state == "analyzing" else None,
                completed_at_ms=None,
            )
        )
        payload = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis").json()
        assert payload["state"] == state
        assert payload["result"] is None
        assert payload["error_code"] is None


def test_analyzed_returns_normalized_result_only() -> None:
    client = _client(
        AutomaticAnalysisPublicView(
            state="analyzed",
            analysis_definition="automatic_post_catalog",
            provider_id="nvidia-nim",
            model_id="test-model",
            prompt_version="framenest-media-suggestion-v3",
            result={
                "title": "Title",
                "description": "Description",
                "collection": "Collection",
                "tags": ["one"],
                "suggested_filename": "title.mp4",
                "confidence": 0.5,
                "evidence": ["frame"],
                "uncertainties": [],
            },
            error_code=None,
            error_message=None,
            attempt_count=1,
            created_at_ms=10,
            started_at_ms=11,
            completed_at_ms=12,
        )
    )
    payload = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis").json()
    assert payload["state"] == "analyzed"
    assert payload["result"]["title"] == "Title"
    assert payload["error_code"] is None
    assert PRIVATE_PATH not in str(payload)
    assert SECRET not in str(payload)


def test_failed_returns_sanitized_error_without_result() -> None:
    client = _client(
        AutomaticAnalysisPublicView(
            state="failed",
            analysis_definition="automatic_post_catalog",
            provider_id=None,
            model_id=None,
            prompt_version="framenest-media-suggestion-v3",
            result=None,
            error_code="PROVIDER_UNAVAILABLE",
            error_message="AI provider is temporarily unavailable.",
            attempt_count=3,
            created_at_ms=10,
            started_at_ms=11,
            completed_at_ms=12,
        )
    )
    payload = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis").json()
    assert payload["state"] == "failed"
    assert payload["result"] is None
    assert payload["error_code"] == "PROVIDER_UNAVAILABLE"
    assert SECRET not in payload["error_message"]
    assert PRIVATE_PATH not in payload["error_message"]


def test_ambiguous_outcome_failure_exposes_sanitized_classification_only() -> None:
    client = _client(
        AutomaticAnalysisPublicView(
            state="failed",
            analysis_definition="automatic_post_catalog",
            provider_id=None,
            model_id=None,
            prompt_version="framenest-media-suggestion-v3",
            result=None,
            error_code="ANALYSIS_OUTCOME_UNKNOWN",
            error_message=(
                "Automatic analysis was interrupted and the provider "
                "outcome cannot be determined safely."
            ),
            attempt_count=1,
            created_at_ms=10,
            started_at_ms=11,
            completed_at_ms=12,
        )
    )
    response = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["state"] == "failed"
    assert payload["result"] is None
    assert payload["error_code"] == "ANALYSIS_OUTCOME_UNKNOWN"
    assert SECRET not in response.text
    assert PRIVATE_PATH not in response.text
    assert "/tmp/" not in response.text


def test_analyzed_read_is_side_effect_free_and_does_not_schedule_provider_work() -> None:
    reader = _FakeReadAnalysis(
        AutomaticAnalysisPublicView(
            state="analyzed",
            analysis_definition="automatic_post_catalog",
            provider_id="nvidia-nim",
            model_id="test-model",
            prompt_version="framenest-media-suggestion-v3",
            result={
                "title": "Title",
                "description": "Description",
                "collection": "Collection",
                "tags": ["one"],
                "suggested_filename": "title.mp4",
                "confidence": 0.5,
                "evidence": ["frame"],
                "uncertainties": [],
            },
            error_code=None,
            error_message=None,
            attempt_count=1,
            created_at_ms=10,
            started_at_ms=11,
            completed_at_ms=12,
        )
    )
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=Path("/tmp/framenest-analysis-lifecycle-api-side-effect.sqlite3"),
        automatic_media_analysis_enabled=True,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=object(),  # type: ignore[arg-type]
            scan_preview=object(),
            catalog_available=lambda: True,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=object(),
            catalog_available=lambda: True,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=None,
            provider_configured=False,
        ),
        media_analysis_lifecycle_api_dependencies=MediaAnalysisLifecycleApiDependencies(
            read_analysis=reader,  # type: ignore[arg-type]
            automatic_analysis_enabled=True,
            provider_configured=True,
            provider_id="nvidia-nim",
            model_id="test-model",
            audience_policy=_record_policy(),
        ),
    )
    client = TestClient(_with_admin(app))
    first = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis")
    second = client.get(f"/api/media/{CANONICAL_MEDIA_ID}/automatic-analysis")
    assert first.status_code == 200
    assert second.status_code == 200
    assert len(reader.calls) == 2
    assert first.json()["result"]["title"] == "Title"
    assert first.json() == second.json()
    assert SECRET not in first.text
    assert PRIVATE_PATH not in first.text


def test_manual_durable_analysis_request_requires_confirmation_and_schedules() -> None:
    calls: list[tuple[MediaId, object]] = []

    def _request(media_id: MediaId, location_id: object) -> object:
        from kronika.domain.media_analysis_runs import (
            AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
            MediaAnalysisRun,
            MediaAnalysisRunId,
            MediaAnalysisRunState,
        )
        from kronika.domain.identities import MediaLocationId

        assert isinstance(location_id, MediaLocationId)
        calls.append((media_id, location_id))
        return MediaAnalysisRun(
            id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
            media_id=media_id,
            media_location_id=location_id,
            analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
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

    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=Path("/tmp/framenest-analysis-lifecycle-api-manual.sqlite3"),
        automatic_media_analysis_enabled=False,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=object(),  # type: ignore[arg-type]
            scan_preview=object(),
            catalog_available=lambda: True,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=object(),
            catalog_available=lambda: True,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=None,
            provider_configured=False,
        ),
        media_analysis_lifecycle_api_dependencies=MediaAnalysisLifecycleApiDependencies(
            read_analysis=_FakeReadAnalysis(
                AutomaticAnalysisPublicView(
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
            ),  # type: ignore[arg-type]
            automatic_analysis_enabled=False,
            provider_configured=True,
            provider_id="nvidia-nim",
            model_id="test-model",
            request_manual_analysis=_request,  # type: ignore[arg-type]
            audience_policy=_record_policy(),
        ),
    )
    client = TestClient(_with_admin(app))
    denied = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{location_id}/durable-analysis",
        json={"confirm_cloud_upload": False},
    )
    assert denied.status_code == 409
    assert denied.json()["error"]["code"] == "CLOUD_CONFIRMATION_REQUIRED"
    assert calls == []
    accepted = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{location_id}/durable-analysis",
        json={"confirm_cloud_upload": True},
    )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["state"] == "pending"
    assert payload["automatic_analysis_enabled"] is False
    assert payload["result"] is None
    assert len(calls) == 1
    assert SECRET not in accepted.text
    assert PRIVATE_PATH not in accepted.text


class _ResolvedModel:
    def __init__(self, capabilities: tuple[str, ...]) -> None:
        self.provider = object()
        self.model_id = "resolved-model"
        self.capabilities = capabilities

    def capabilities_for(self, model_id: str) -> tuple[str, ...]:
        del model_id
        return self.capabilities


def _manual_capability_client(
    read_provider: object,
) -> tuple[TestClient, list[object]]:
    from kronika.domain.media_analysis_runs import (
        AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        MediaAnalysisRun,
        MediaAnalysisRunId,
        MediaAnalysisRunState,
    )
    from kronika.domain.identities import MediaLocationId

    calls: list[object] = []

    def _request(media_id: MediaId, location_id: object) -> MediaAnalysisRun:
        assert isinstance(location_id, MediaLocationId)
        calls.append(location_id)
        return MediaAnalysisRun(
            id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
            media_id=media_id,
            media_location_id=location_id,
            analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
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

    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=Path("/tmp/framenest-analysis-lifecycle-capability.sqlite3"),
        automatic_media_analysis_enabled=False,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=object(),  # type: ignore[arg-type]
            scan_preview=object(),
            catalog_available=lambda: True,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=object(),
            catalog_available=lambda: True,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=None,
            provider_configured=False,
        ),
        media_analysis_lifecycle_api_dependencies=MediaAnalysisLifecycleApiDependencies(
            read_analysis=_FakeReadAnalysis(
                AutomaticAnalysisPublicView(
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
            ),  # type: ignore[arg-type]
            automatic_analysis_enabled=False,
            provider_configured=True,
            provider_id="nvidia-nim",
            model_id="resolved-model",
            read_provider=read_provider,  # type: ignore[arg-type]
            request_manual_analysis=_request,  # type: ignore[arg-type]
            audience_policy=_record_policy(),
        ),
    )
    return TestClient(_with_admin(app)), calls


def test_manual_durable_analysis_refuses_non_vision_selected_model() -> None:
    client, calls = _manual_capability_client(lambda: _ResolvedModel(()))
    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    response = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{location_id}/durable-analysis",
        json={"confirm_cloud_upload": True},
    )

    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "AI_MODEL_CAPABILITY_MISSING",
        "message": "The selected AI model does not support image analysis.",
    }
    assert calls == []
    assert SECRET not in response.text
    assert PRIVATE_PATH not in response.text


def test_manual_durable_analysis_accepts_vision_selected_model() -> None:
    client, calls = _manual_capability_client(lambda: _ResolvedModel(("vision_input",)))
    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    response = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{location_id}/durable-analysis",
        json={"confirm_cloud_upload": True},
    )

    assert response.status_code == 200
    assert response.json()["state"] == "pending"
    assert len(calls) == 1


def test_manual_durable_analysis_after_terminal_returns_new_pending_run() -> None:
    from kronika.domain.media_analysis_runs import (
        AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        MediaAnalysisRun,
        MediaAnalysisRunId,
        MediaAnalysisRunState,
    )
    from kronika.domain.identities import MediaLocationId

    calls: list[str] = []
    prior = MediaAnalysisRunId("51c2f844-0240-4c26-8d6c-e185dd42332a")

    def _request(media_id: MediaId, location_id: object) -> MediaAnalysisRun:
        assert isinstance(location_id, MediaLocationId)
        calls.append(media_id.to_string())
        return MediaAnalysisRun(
            id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
            media_id=media_id,
            media_location_id=location_id,
            analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
            state=MediaAnalysisRunState.PENDING,
            attempt_count=0,
            provider_id=None,
            model_id=None,
            prompt_version=None,
            result_schema_version=None,
            result_json=None,
            error_code=None,
            error_message=None,
            created_at_ms=30,
            started_at_ms=None,
            completed_at_ms=None,
            version=1,
            supersedes_run_id=prior,
        )

    location_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=Path("/tmp/framenest-analysis-lifecycle-api-rerun.sqlite3"),
        automatic_media_analysis_enabled=False,
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=object(),  # type: ignore[arg-type]
            scan_preview=object(),
            catalog_available=lambda: True,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=object(),
            catalog_available=lambda: True,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=None,
            provider_configured=False,
        ),
        media_analysis_lifecycle_api_dependencies=MediaAnalysisLifecycleApiDependencies(
            read_analysis=_FakeReadAnalysis(
                AutomaticAnalysisPublicView(
                    state="failed",
                    analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
                    provider_id=None,
                    model_id=None,
                    prompt_version="framenest-media-suggestion-v3",
                    result=None,
                    error_code="PROVIDER_UNAVAILABLE",
                    error_message="AI provider is temporarily unavailable.",
                    attempt_count=1,
                    created_at_ms=10,
                    started_at_ms=11,
                    completed_at_ms=12,
                    provider_submission_occurred=True,
                )
            ),  # type: ignore[arg-type]
            automatic_analysis_enabled=False,
            provider_configured=True,
            provider_id="nvidia-nim",
            model_id="test-model",
            request_manual_analysis=_request,  # type: ignore[arg-type]
            audience_policy=_record_policy(),
        ),
    )
    client = TestClient(_with_admin(app))
    accepted = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{location_id}/durable-analysis",
        json={"confirm_cloud_upload": True},
    )
    assert accepted.status_code == 200
    payload = accepted.json()
    assert payload["state"] == "pending"
    assert payload["error_code"] is None
    assert payload["analysis_definition"] == AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION
    assert calls == [CANONICAL_MEDIA_ID]
    assert SECRET not in accepted.text
