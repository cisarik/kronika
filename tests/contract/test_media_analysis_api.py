"""Contract tests for the local media-analysis preview API."""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.library_api import LibraryApiDependencies
from kronika.adapters.api.media_analysis_api import MediaAnalysisApiDependencies
from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanSummary,
)
from kronika.application.media_analysis import (
    FrameNestMediaAnalysisError,
    MediaAnalysisFailedError,
    MediaAnalysisNotFoundError,
    MediaAnalysisUnavailableError,
    MediaRelativePath,
    PreparedAnalysisResult,
    REQUESTED_FRAME_COUNT,
    TechnicalMetadata,
    build_representative_frame,
    PNG_SIGNATURE,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.configuration import KronikaSettings
from kronika.domain import LibraryId

CANONICAL_LIBRARY_ID = "12345678-1234-4234-9234-123456789abc"
PRIVATE_ROOT_PATH = "/Users/example/private/videos"
PRIVATE_DATABASE_PATH = "/Users/example/private/catalog.sqlite3"
UNDERLYING_EXCEPTION_TEXT = "sqlite failed near private media table"
TOOL_VERSION = "ffprobe version 8.1.2 /usr/bin/ffprobe"
TOOL_PATH = "/usr/bin/ffmpeg"


@dataclass
class _FakeLibraryRepository:
    def add(self, library: object) -> None:
        raise AssertionError("media-analysis API tests must not register libraries")

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


class _FakePreparePreview:
    def __init__(self, result: PreparedAnalysisResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls: list[tuple[LibraryId, MediaRelativePath]] = []

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> PreparedAnalysisResult:
        self.calls.append((library_id, relative_path))
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("fake media-analysis result was not configured")
        return self.result


def _prepared_result() -> PreparedAnalysisResult:
    return PreparedAnalysisResult(
        relative_path=MediaRelativePath("Series/Episode 01.mkv"),
        candidate_kind=LibraryScanCandidateKind.VIDEO,
        technical_metadata=TechnicalMetadata(
            duration_ms=123456,
            width=1920,
            height=1080,
            video_codec="h264",
            container_formats=("matroska", "webm"),
            has_audio=True,
        ),
        representative_frames=(
            build_representative_frame(timestamp_ms=12345, payload=PNG_SIGNATURE + b"frame-one"),
            build_representative_frame(timestamp_ms=67890, payload=PNG_SIGNATURE + b"frame-two"),
        ),
        requested_frame_count=REQUESTED_FRAME_COUNT,
        warnings=("Representative frame extraction failed for one target.",),
        ffprobe_version=TOOL_VERSION,
        ffmpeg_version=f"ffmpeg version 8.1.2 {TOOL_PATH}",
    )


def _client(
    *,
    prepare: _FakePreparePreview | None = None,
    catalog_available: bool = True,
    database_path: Path | None = None,
) -> TestClient:
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=database_path or Path("/tmp/framenest-contract.sqlite3"),
        _env_file=None,
    )
    return TestClient(
        create_app(
            settings=settings,
            library_api_dependencies=LibraryApiDependencies(
                repository=_FakeLibraryRepository(),
                scan_preview=_FakePreviewScan(),
                catalog_available=lambda: catalog_available,
            ),
            media_analysis_api_dependencies=MediaAnalysisApiDependencies(
                prepare_preview=prepare or _FakePreparePreview(_prepared_result()),
                catalog_available=lambda: catalog_available,
            ),
        )
    )


def _post_preview(client: TestClient, relative_path: str = "Series/Episode 01.mkv"):
    return client.post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/media-analysis-preview",
        json={"relative_path": relative_path},
    )


def test_success_returns_typed_metadata_ordered_frames_and_no_store() -> None:
    prepare = _FakePreparePreview(_prepared_result())
    response = _post_preview(_client(prepare=prepare))

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    payload = response.json()
    assert payload["library_id"] == CANONICAL_LIBRARY_ID
    assert payload["relative_path"] == "Series/Episode 01.mkv"
    assert payload["candidate_kind"] == "video"
    assert payload["technical_metadata"] == {
        "duration_ms": 123456,
        "width": 1920,
        "height": 1080,
        "video_codec": "h264",
        "container_formats": ["matroska", "webm"],
        "has_audio": True,
    }
    assert payload["requested_frame_count"] == 3
    assert payload["warnings"] == ["Representative frame extraction failed for one target."]
    assert [frame["timestamp_ms"] for frame in payload["representative_frames"]] == [12345, 67890]
    assert prepare.calls[0][0] == LibraryId.from_string(CANONICAL_LIBRARY_ID)
    assert prepare.calls[0][1] == MediaRelativePath("Series/Episode 01.mkv")


def test_frame_payloads_are_standard_base64_round_trips_with_preserved_digests() -> None:
    response = _post_preview(_client())
    frames = response.json()["representative_frames"]

    original_payloads = [PNG_SIGNATURE + b"frame-one", PNG_SIGNATURE + b"frame-two"]
    for frame, expected in zip(frames, original_payloads, strict=True):
        encoded = frame["payload_base64"]
        assert not encoded.startswith("data:")
        decoded = base64.b64decode(encoded, validate=True)
        assert decoded == expected
        assert len(decoded) == frame["byte_size"]
        assert frame["sha256"] == build_representative_frame(
            timestamp_ms=frame["timestamp_ms"],
            payload=expected,
        ).sha256


def test_success_response_omits_private_paths_tool_versions_and_executables() -> None:
    response = _post_preview(_client())

    body = response.text
    assert PRIVATE_ROOT_PATH not in body
    assert PRIVATE_DATABASE_PATH not in body
    assert "ffprobe_version" not in body
    assert "ffmpeg_version" not in body
    assert "ffprobe version" not in body
    assert "/usr/bin/ffprobe" not in body
    assert TOOL_PATH not in body
    assert "stderr" not in body.lower()


def test_absent_catalog_returns_sanitized_503_and_no_store(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"

    response = _post_preview(
        _client(catalog_available=False, database_path=database_path),
    )

    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": "CATALOG_UNAVAILABLE",
            "message": "The local catalog is not available.",
        }
    }
    assert not database_path.exists()
    assert str(database_path) not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            MediaAnalysisNotFoundError("missing private library"),
            404,
            "LIBRARY_NOT_FOUND",
            "Library not found.",
        ),
        (
            MediaAnalysisUnavailableError(PRIVATE_ROOT_PATH),
            409,
            "MEDIA_ANALYSIS_UNAVAILABLE",
            "Local media analysis is not available.",
        ),
        (
            MediaAnalysisFailedError(UNDERLYING_EXCEPTION_TEXT),
            500,
            "MEDIA_ANALYSIS_FAILED",
            "Local media analysis failed.",
        ),
        (
            RuntimeError(UNDERLYING_EXCEPTION_TEXT),
            500,
            "MEDIA_ANALYSIS_FAILED",
            "Local media analysis failed.",
        ),
    ],
)
def test_errors_are_sanitized_and_no_store(
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = _post_preview(_client(prepare=_FakePreparePreview(error=error)))

    assert response.status_code == status_code
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {"error": {"code": code, "message": message}}
    assert PRIVATE_ROOT_PATH not in response.text
    assert UNDERLYING_EXCEPTION_TEXT not in response.text


@pytest.mark.parametrize(
    "relative_path",
    [
        "",
        "../clip.mp4",
        "/abs/clip.mp4",
        "dir\\clip.mp4",
        ".hidden/clip.mp4",
        "readme.txt",
        f"{'a' * 4093}.mp4",
    ],
)
def test_invalid_relative_path_returns_sanitized_422(relative_path: str) -> None:
    response = _post_preview(_client(), relative_path)

    assert response.status_code == 422
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "error": {
            "code": "INVALID_MEDIA_PATH",
            "message": "Invalid media relative path.",
        }
    }


def test_malformed_uuid_uses_uniform_validation_contract() -> None:
    response = _client().post(
        "/api/libraries/not-a-uuid/media-analysis-preview",
        json={"relative_path": "clip.mp4"},
    )

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_FAILED",
            "message": "Request validation failed.",
        }
    }
    assert "detail" not in response.json()
    assert "not-a-uuid" not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_repository_failure_returns_catalog_unavailable() -> None:
    response = _post_preview(
        _client(prepare=_FakePreparePreview(error=FrameNestLibraryRepositoryError("private SQL"))),
    )

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert "private SQL" not in response.text


def test_frame_payloads_are_not_logged(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO")
    response = _post_preview(_client())
    encoded_payload = response.json()["representative_frames"][0]["payload_base64"]

    assert response.status_code == 200
    assert encoded_payload not in caplog.text
    assert "frame-one" not in caplog.text
