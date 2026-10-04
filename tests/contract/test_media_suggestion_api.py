"""Contract tests for explicit AI media suggestion preview API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.library_api import LibraryApiDependencies
from kronika.adapters.api.media_analysis_api import MediaAnalysisApiDependencies
from kronika.adapters.api.media_suggestion_api import (
    MediaSuggestionApiDependencies,
)
from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanSummary,
)
from kronika.application.media_analysis import (
    MediaRelativePath,
    PreparedAnalysisResult,
    REQUESTED_FRAME_COUNT,
    TechnicalMetadata,
    build_representative_frame,
    PNG_SIGNATURE,
)
from kronika.application.media_analysis_lifecycle import MediaAnalysisLifecycleError
from kronika.application.media_suggestion import (
    ImportedMediaSuggestionPreviewResult,
    MediaSuggestion,
    MediaSuggestionNotFoundError,
    MediaSuggestionPreparationFailedError,
    MediaSuggestionPreparationUnavailableError,
    MediaSuggestionProviderAuthError,
    MediaSuggestionProviderFailedError,
    MediaSuggestionProviderInvalidResponseError,
    MediaSuggestionProviderRateLimitedError,
    MediaSuggestionProviderUnavailableError,
    MediaSuggestionPreviewResult,
    PROMPT_VERSION,
)
from kronika.configuration import KronikaSettings
from kronika.domain import LibraryId, MediaId, MediaLocationId

CANONICAL_LIBRARY_ID = "12345678-1234-4234-9234-123456789abc"
CANONICAL_MEDIA_ID = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
CANONICAL_LOCATION_ID = "bbbbbbbb-cccc-4ddd-9eee-ffffffffffff"
PRIVATE_ROOT_PATH = "/Users/example/private/videos"
PRIVATE_DATABASE_PATH = "/Users/example/private/catalog.sqlite3"
RAW_PROVIDER_CONTENT = "raw provider markdown or reasoning"
RAW_EXCEPTION_TEXT = "private provider response with /Users/example/private"
SECRET_VALUE = "test-secret-value"


@dataclass
class _FakeLibraryRepository:
    def add(self, library: object) -> None:
        raise AssertionError("API tests must not register libraries")

    def get(self, library_id: LibraryId) -> None:
        return None

    def list_all(self) -> tuple[object, ...]:
        return ()


@dataclass
class _FakePreviewScan:
    calls: int = 0

    def execute(self, library_id: LibraryId, limits: LibraryScanLimits) -> object:
        self.calls += 1
        return type(
            "PreviewResult",
            (),
            {
                "library_id": library_id,
                "limits": limits,
                "summary": LibraryScanSummary(
                    entries_seen=0,
                    directories_seen=0,
                    regular_files_seen=0,
                    candidate_files_seen=0,
                    candidate_bytes_seen=0,
                    skipped_hidden_entries=0,
                    skipped_symlink_entries=0,
                    skipped_other_entries=0,
                    inaccessible_entries=0,
                    truncated=False,
                    candidates_truncated=False,
                ),
                "candidates": LibraryFilesystemScanResult(
                    summary=LibraryScanSummary(
                        entries_seen=0,
                        directories_seen=0,
                        regular_files_seen=0,
                        candidate_files_seen=0,
                        candidate_bytes_seen=0,
                        skipped_hidden_entries=0,
                        skipped_symlink_entries=0,
                        skipped_other_entries=0,
                        inaccessible_entries=0,
                        truncated=False,
                        candidates_truncated=False,
                    ),
                    candidates=(),
                ).candidates,
            },
        )()


class _FakePrepareAnalysis:
    def execute(self, library_id: LibraryId, relative_path: MediaRelativePath) -> object:
        raise AssertionError("local analysis API is not under test here")


class _FakeSuggestionPreview:
    def __init__(
        self,
        result: MediaSuggestionPreviewResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or _suggestion_result()
        self.error = error
        self.calls: list[tuple[LibraryId, MediaRelativePath]] = []

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaSuggestionPreviewResult:
        self.calls.append((library_id, relative_path))
        if self.error is not None:
            raise self.error
        return self.result


class _FakeImportedSuggestionPreview:
    def __init__(
        self,
        result: ImportedMediaSuggestionPreviewResult | None = None,
        error: Exception | None = None,
    ) -> None:
        self.result = result or _imported_suggestion_result()
        self.error = error
        self.calls: list[tuple[MediaId, MediaLocationId]] = []

    def execute(
        self,
        media_id: MediaId,
        location_id: MediaLocationId,
    ) -> ImportedMediaSuggestionPreviewResult:
        self.calls.append((media_id, location_id))
        if self.error is not None:
            raise self.error
        return self.result


def _prepared_result() -> PreparedAnalysisResult:
    frame = build_representative_frame(timestamp_ms=100, payload=PNG_SIGNATURE + b"frame")
    return PreparedAnalysisResult(
        relative_path=MediaRelativePath("Series/Episode 01.mkv"),
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=1000,
            width=64,
            height=48,
            video_codec="h264",
            container_formats=("matroska",),
            has_audio=False,
        ),
        representative_frames=(frame, frame)[:1],
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=(),
        ffprobe_version="ffprobe version hidden",
        ffmpeg_version="ffmpeg version hidden",
    )


def _suggestion() -> MediaSuggestion:
    return MediaSuggestion(
        title="Editable title",
        description="Editable description",
        collection="Meme",
        tags=("Meme", "Animation"),
        suggested_filename="editable-name.mkv",
        confidence=0.85,
        evidence=("Visible evidence",),
        uncertainties=("Unclear context",),
        provider_id="nvidia-nim",
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        prompt_version=PROMPT_VERSION,
    )


def _suggestion_result() -> MediaSuggestionPreviewResult:
    return MediaSuggestionPreviewResult(
        library_id=LibraryId.from_string(CANONICAL_LIBRARY_ID),
        relative_path=MediaRelativePath("Series/Episode 01.mkv"),
        prepared=_prepared_result(),
        suggestion=_suggestion(),
    )


def _imported_suggestion_result() -> ImportedMediaSuggestionPreviewResult:
    return ImportedMediaSuggestionPreviewResult(
        media_id=MediaId.from_string(CANONICAL_MEDIA_ID),
        location_id=MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        prepared=_prepared_result(),
        suggestion=_suggestion(),
    )


class _ResolvedModel:
    def __init__(self, capabilities: tuple[str, ...]) -> None:
        self.provider = object()
        self.model_id = "resolved-model"
        self.capabilities = capabilities

    def capabilities_for(self, model_id: str) -> tuple[str, ...]:
        del model_id
        return self.capabilities



def _record_policy():
    from tests.support.record_access import scoped_policy

    return scoped_policy({CANONICAL_MEDIA_ID})


def _client(
    *,
    configured: bool = True,
    status: str = "not_configured",
    provider_id: str | None = "nvidia-nim",
    provider_display_name: str | None = "NVIDIA NIM",
    model_id: str | None = "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    preview: _FakeSuggestionPreview | None = None,
    imported_preview: _FakeImportedSuggestionPreview | None = None,
    catalog_available: bool = True,
    database_path: Path | None = None,
    last_status_check: dict[str, object] | None = None,
    last_connection_test: dict[str, object] | None = None,
    read_provider: object | None = None,
) -> tuple[TestClient, _FakeSuggestionPreview, _FakeImportedSuggestionPreview]:
    suggestion_preview = preview or _FakeSuggestionPreview()
    imported_suggestion_preview = imported_preview or _FakeImportedSuggestionPreview()
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=database_path or Path("/tmp/framenest-media-suggestion-api.sqlite3"),
        _env_file=None,
    )
    app = create_app(
        settings=settings,
        library_api_dependencies=LibraryApiDependencies(
            repository=_FakeLibraryRepository(),
            scan_preview=_FakePreviewScan(),
            catalog_available=lambda: catalog_available,
        ),
        media_analysis_api_dependencies=MediaAnalysisApiDependencies(
            prepare_preview=_FakePrepareAnalysis(),
            catalog_available=lambda: catalog_available,
        ),
        media_suggestion_api_dependencies=MediaSuggestionApiDependencies(
            preview_suggestion=suggestion_preview if configured else None,
            provider_configured=configured,
            preview_imported_suggestion=imported_suggestion_preview if configured else None,
            provider_id=provider_id,
            provider_display_name=provider_display_name,
            model_id=model_id,
            credential_available=configured,
            status=status,
            last_status_check=last_status_check,
            last_connection_test=last_connection_test,
            read_provider=read_provider,  # type: ignore[arg-type]
            audience_policy=_record_policy(),
        ),
    )
    from kronika.domain.identity_access import ROLE_ADMIN
    from tests.support.record_access import install_synthetic_caller

    return TestClient(install_synthetic_caller(app, "ada", role=ROLE_ADMIN)), suggestion_preview, imported_suggestion_preview


def _post_preview(
    client: TestClient,
    *,
    relative_path: str = "Series/Episode 01.mkv",
    confirm_cloud_upload: object = True,
):
    return client.post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/media-suggestion-preview",
        json={
            "relative_path": relative_path,
            "confirm_cloud_upload": confirm_cloud_upload,
        },
    )


def _post_imported_preview(
    client: TestClient,
    *,
    media_id: str = CANONICAL_MEDIA_ID,
    location_id: str = CANONICAL_LOCATION_ID,
    confirm_cloud_upload: object = True,
):
    return client.post(
        f"/api/media/{media_id}/locations/{location_id}/ai-suggestion-preview",
        json={"confirm_cloud_upload": confirm_cloud_upload},
    )


def test_capability_configured_and_unconfigured_are_sanitized_no_store() -> None:
    configured_client, configured_preview, configured_imported_preview = _client(configured=True)
    unconfigured_client, unconfigured_preview, unconfigured_imported_preview = _client(
        configured=False,
        provider_id=None,
        provider_display_name=None,
        model_id=None,
    )

    configured = configured_client.get("/api/ai/media-suggestion-capability")
    unconfigured = unconfigured_client.get("/api/ai/media-suggestion-capability")

    assert configured.status_code == 200
    assert configured.headers["cache-control"] == "no-store"
    assert configured.json() == {
        "available": True,
        "provider_id": "nvidia-nim",
        "provider_display_name": "NVIDIA NIM",
        "model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "prompt_version": PROMPT_VERSION,
        "execution": "server",
        "status": "configured_unverified",
        "configured": True,
        "credential_available": True,
        "requires_explicit_confirmation": True,
    }
    assert unconfigured.status_code == 200
    assert unconfigured.headers["cache-control"] == "no-store"
    assert unconfigured.json() == {
        "available": False,
        "prompt_version": PROMPT_VERSION,
        "execution": "server",
        "status": "not_configured",
        "configured": False,
        "credential_available": False,
        "requires_explicit_confirmation": True,
    }
    combined = configured.text + unconfigured.text
    assert SECRET_VALUE not in combined
    assert "Authorization" not in combined
    assert "NVIDIA_API_KEY" not in combined
    assert configured_preview.calls == []
    assert configured_imported_preview.calls == []
    assert unconfigured_preview.calls == []
    assert unconfigured_imported_preview.calls == []


def test_capability_selected_provider_without_credential_is_actionable_without_request() -> None:
    client, preview, imported_preview = _client(
        configured=False,
        status="credential_unavailable",
        provider_id="vercel-ai-gateway",
        provider_display_name="Vercel AI Gateway",
        model_id="google/custom",
    )

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "provider_id": "vercel-ai-gateway",
        "provider_display_name": "Vercel AI Gateway",
        "model_id": "google/custom",
        "prompt_version": PROMPT_VERSION,
        "execution": "server",
        "status": "credential_unavailable",
        "configured": True,
        "credential_available": False,
        "requires_explicit_confirmation": True,
    }
    assert preview.calls == []
    assert imported_preview.calls == []


def test_capability_exposes_safe_last_connection_test_without_provider_request() -> None:
    preview = _FakeSuggestionPreview()
    imported_preview = _FakeImportedSuggestionPreview()
    client, _, _ = _client(
        preview=preview,
        imported_preview=imported_preview,
        status="provider_unreachable",
        last_connection_test={
            "status": "provider_unreachable",
            "tested_at_ms": 1_725_000_000_000,
        },
    )

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["last_connection_test"] == {
        "status": "provider_unreachable",
        "tested_at_ms": 1_725_000_000_000,
    }
    assert response.json()["credential_available"] is True
    assert preview.calls == []
    assert imported_preview.calls == []


def test_capability_exposes_safe_last_status_snapshot_without_provider_request() -> None:
    preview = _FakeSuggestionPreview()
    imported_preview = _FakeImportedSuggestionPreview()
    client, _, _ = _client(
        preview=preview,
        imported_preview=imported_preview,
        last_status_check={
            "configuration_state": "configured",
            "checked_at_ms": 123,
        },
    )

    response = client.get("/api/ai/media-suggestion-capability")

    assert response.status_code == 200
    assert response.json()["last_status_check"] == {
        "configuration_state": "configured",
        "checked_at_ms": 123,
    }
    assert preview.calls == []
    assert imported_preview.calls == []


def test_success_returns_validated_editable_suggestion_no_frames_or_raw_content() -> None:
    client, preview, imported_preview = _client()

    response = _post_preview(client)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "library_id": CANONICAL_LIBRARY_ID,
        "relative_path": "Series/Episode 01.mkv",
        "sent_frame_count": 1,
        "provider_id": "nvidia-nim",
        "model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "prompt_version": PROMPT_VERSION,
        "suggestion": {
            "title": "Editable title",
            "description": "Editable description",
            "collection": "Meme",
            "tags": ["Meme", "Animation"],
            "suggested_filename": "editable-name.mkv",
            "confidence": 0.85,
            "evidence": ["Visible evidence"],
            "uncertainties": ["Unclear context"],
        },
    }
    assert preview.calls == [(LibraryId.from_string(CANONICAL_LIBRARY_ID), MediaRelativePath("Series/Episode 01.mkv"))]
    assert imported_preview.calls == []
    body = response.text
    assert "payload_base64" not in body
    assert "image/jpeg" not in body
    assert "image/png" not in body
    assert "data:" not in body
    assert RAW_PROVIDER_CONTENT not in body
    assert "reasoning_content" not in body
    assert "tool_calls" not in body
    assert PRIVATE_ROOT_PATH not in body
    assert PRIVATE_DATABASE_PATH not in body
    assert "Authorization" not in body


@pytest.mark.parametrize("confirm_cloud_upload", [False, None, "true"])
def test_confirmation_is_required_before_preparation_or_provider(
    confirm_cloud_upload: object,
) -> None:
    client, preview, imported_preview = _client()

    response = _post_preview(client, confirm_cloud_upload=confirm_cloud_upload)

    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": "CLOUD_CONFIRMATION_REQUIRED",
            "message": "Explicit cloud upload confirmation is required.",
        }
    }
    assert preview.calls == []
    assert imported_preview.calls == []


def test_unconfigured_provider_returns_503_without_preview_call() -> None:
    client, preview, imported_preview = _client(configured=False)

    response = _post_preview(client)

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AI_PROVIDER_NOT_CONFIGURED"
    assert preview.calls == []
    assert imported_preview.calls == []


def test_non_vision_selected_model_is_refused_on_both_preview_routes() -> None:
    client, preview, imported_preview = _client(
        read_provider=lambda: _ResolvedModel(()),
    )

    listed = _post_preview(client)
    imported = _post_imported_preview(client)

    for response in (listed, imported):
        assert response.status_code == 409
        assert response.headers["cache-control"] == "no-store"
        assert response.json() == {
            "error": {
                "code": "AI_MODEL_CAPABILITY_MISSING",
                "message": "The selected AI model does not support image analysis.",
            }
        }
    assert preview.calls == []
    assert imported_preview.calls == []
    assert "Authorization" not in listed.text + imported.text


def test_vision_declared_selected_model_still_analyzes() -> None:
    client, preview, imported_preview = _client(
        read_provider=lambda: _ResolvedModel(("vision_input",)),
    )

    listed = _post_preview(client)
    imported = _post_imported_preview(client)

    assert listed.status_code == 200
    assert imported.status_code == 200
    assert len(preview.calls) == 1
    assert len(imported_preview.calls) == 1


@pytest.mark.parametrize(
    ("relative_path", "status_code", "code"),
    [
        ("../clip.mp4", 422, "INVALID_MEDIA_PATH"),
        ("readme.txt", 422, "INVALID_MEDIA_PATH"),
        ("a" * 4093 + ".mp4", 422, "INVALID_MEDIA_PATH"),
    ],
)
def test_invalid_path_mapping(relative_path: str, status_code: int, code: str) -> None:
    client, preview, imported_preview = _client()

    response = _post_preview(client, relative_path=relative_path)

    assert response.status_code == status_code
    assert response.json()["error"]["code"] == code
    assert preview.calls == []
    assert imported_preview.calls == []


def test_imported_media_identity_preview_returns_sanitized_draft_without_path() -> None:
    client, preview, imported_preview = _client()

    response = _post_imported_preview(client)

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "media_id": CANONICAL_MEDIA_ID,
        "location_id": CANONICAL_LOCATION_ID,
        "sent_frame_count": 1,
        "provider_id": "nvidia-nim",
        "model_id": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
        "prompt_version": PROMPT_VERSION,
        "suggestion": {
            "title": "Editable title",
            "description": "Editable description",
            "collection": "Meme",
            "tags": ["Meme", "Animation"],
            "suggested_filename": "editable-name.mkv",
            "confidence": 0.85,
            "evidence": ["Visible evidence"],
            "uncertainties": ["Unclear context"],
        },
    }
    assert preview.calls == []
    assert imported_preview.calls == [
        (
            MediaId.from_string(CANONICAL_MEDIA_ID),
            MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        )
    ]
    body = response.text
    assert "relative_path" not in body
    assert PRIVATE_ROOT_PATH not in body
    assert PRIVATE_DATABASE_PATH not in body
    assert "Authorization" not in body
    assert "data:" not in body


@pytest.mark.parametrize("confirm_cloud_upload", [False, None, "true"])
def test_imported_media_identity_preview_requires_confirmation_before_provider(
    confirm_cloud_upload: object,
) -> None:
    client, preview, imported_preview = _client()

    response = _post_imported_preview(client, confirm_cloud_upload=confirm_cloud_upload)

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "CLOUD_CONFIRMATION_REQUIRED"
    assert preview.calls == []
    assert imported_preview.calls == []


def test_imported_media_identity_preview_never_accepts_browser_path() -> None:
    client, preview, imported_preview = _client()

    response = client.post(
        f"/api/media/{CANONICAL_MEDIA_ID}/locations/{CANONICAL_LOCATION_ID}/ai-suggestion-preview",
        json={"confirm_cloud_upload": True, "relative_path": "/Users/example/private.mp4"},
    )

    assert response.status_code == 200
    assert preview.calls == []
    assert imported_preview.calls == [
        (
            MediaId.from_string(CANONICAL_MEDIA_ID),
            MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        )
    ]
    assert "/Users/example" not in response.text


def test_imported_preview_join_failure_is_sanitized_server_error() -> None:
    client, preview, imported_preview = _client(
        imported_preview=_FakeImportedSuggestionPreview(
            error=MediaAnalysisLifecycleError("imported preview analysis persistence failed")
        )
    )

    response = _post_imported_preview(client)

    assert response.status_code == 500
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": "ANALYSIS_JOIN_FAILED",
            "message": "Durable analysis persistence failed.",
        }
    }
    assert preview.calls == []
    assert imported_preview.calls == [
        (
            MediaId.from_string(CANONICAL_MEDIA_ID),
            MediaLocationId.from_string(CANONICAL_LOCATION_ID),
        )
    ]


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (MediaSuggestionNotFoundError(RAW_EXCEPTION_TEXT), 404, "LIBRARY_NOT_FOUND", "Library not found."),
        (
            MediaSuggestionPreparationUnavailableError(RAW_EXCEPTION_TEXT),
            409,
            "MEDIA_PREPARATION_UNAVAILABLE",
            "Local media preparation is not available.",
        ),
        (
            MediaSuggestionProviderAuthError(RAW_EXCEPTION_TEXT),
            503,
            "AI_PROVIDER_AUTHENTICATION_FAILED",
            "The configured AI provider credential was rejected.",
        ),
        (
            MediaSuggestionProviderRateLimitedError(RAW_EXCEPTION_TEXT),
            429,
            "AI_PROVIDER_RATE_LIMITED",
            "The AI suggestion provider rate limit was reached.",
        ),
        (
            MediaSuggestionProviderInvalidResponseError(RAW_EXCEPTION_TEXT),
            502,
            "AI_PROVIDER_INVALID_RESPONSE",
            "The AI suggestion provider response was invalid.",
        ),
    ],
)
def test_imported_media_identity_error_mappings_are_sanitized(
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    client, _preview, _imported_preview = _client(
        imported_preview=_FakeImportedSuggestionPreview(error=error)
    )

    response = _post_imported_preview(client)

    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"error": {"code": code, "message": message}}
    assert RAW_EXCEPTION_TEXT not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (MediaSuggestionNotFoundError(RAW_EXCEPTION_TEXT), 404, "LIBRARY_NOT_FOUND", "Library not found."),
        (
            MediaSuggestionPreparationUnavailableError(RAW_EXCEPTION_TEXT),
            409,
            "MEDIA_PREPARATION_UNAVAILABLE",
            "Local media preparation is not available.",
        ),
        (
            MediaSuggestionPreparationFailedError(RAW_EXCEPTION_TEXT),
            500,
            "MEDIA_PREPARATION_FAILED",
            "Local media preparation failed.",
        ),
        (
            MediaSuggestionProviderAuthError(RAW_EXCEPTION_TEXT),
            503,
            "AI_PROVIDER_AUTHENTICATION_FAILED",
            "The configured AI provider credential was rejected.",
        ),
        (
            MediaSuggestionProviderRateLimitedError(RAW_EXCEPTION_TEXT),
            429,
            "AI_PROVIDER_RATE_LIMITED",
            "The AI suggestion provider rate limit was reached.",
        ),
        (
            MediaSuggestionProviderUnavailableError(RAW_EXCEPTION_TEXT),
            503,
            "AI_PROVIDER_UNAVAILABLE",
            "The AI suggestion provider is not available.",
        ),
        (
            MediaSuggestionProviderInvalidResponseError(RAW_EXCEPTION_TEXT),
            502,
            "AI_PROVIDER_INVALID_RESPONSE",
            "The AI suggestion provider response was invalid.",
        ),
        (
            MediaSuggestionProviderFailedError(RAW_EXCEPTION_TEXT),
            502,
            "AI_PROVIDER_FAILED",
            "The AI suggestion provider request failed.",
        ),
        (
            RuntimeError(RAW_EXCEPTION_TEXT),
            502,
            "AI_PROVIDER_FAILED",
            "The AI suggestion provider request failed.",
        ),
    ],
)
def test_error_mappings_are_sanitized(
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    client, _preview, _imported_preview = _client(preview=_FakeSuggestionPreview(error=error))

    response = _post_preview(client)

    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"error": {"code": code, "message": message}}
    assert RAW_EXCEPTION_TEXT not in response.text


def test_existing_core_api_contracts_remain_available() -> None:
    client, _preview, _imported_preview = _client()

    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/").status_code == 200
    assert client.get("/assets/app.js").status_code == 200
    assert client.get("/assets/missing.js").status_code == 404
    assert client.get("/api/libraries").status_code == 200
    assert client.post(f"/api/libraries/{CANONICAL_LIBRARY_ID}/scan-preview").status_code == 200
    assert client.post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/media-analysis-preview",
        json={"relative_path": "clip.mp4"},
    ).status_code in {404, 409, 500}
