"""Contract tests for explicit media import API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.library_api import LibraryApiDependencies
from kronika.adapters.api.media_import_api import MediaImportApiDependencies
from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanSummary,
    LibraryScanUnavailableError,
    SCAN_FAILED_MESSAGE,
    SCAN_UNAVAILABLE_MESSAGE,
)
from kronika.application.media_import import (
    MediaImportCandidateUnavailableError,
    MediaImportFailedError,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.configuration import KronikaSettings
from kronika.domain import LibraryId, MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)

CANONICAL_LIBRARY_ID = "12345678-1234-4234-9234-123456789abc"
PRIVATE_ROOT_PATH = "/Users/example/private/videos"
PRIVATE_DATABASE_PATH = "/Users/example/private/catalog.sqlite3"
UNDERLYING_EXCEPTION_TEXT = "sqlite failed near private table"


class _FakeLibraryRepository:
    def add(self, library: object) -> None:
        raise AssertionError("media-import API tests must not register libraries")

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


@dataclass
class _FakeImportMedia:
    created: bool = True
    error: Exception | None = None
    calls: int = 0
    last_relative_path: MediaRelativePath | None = None

    def execute(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
        limits: LibraryScanLimits,
    ) -> object:
        self.calls += 1
        self.last_relative_path = relative_path
        if self.error is not None:
            raise self.error
        media = LogicalMedia(
            id=MediaId.from_string("11111111-2222-4333-8444-555555555555"),
            kind=MediaKind.VIDEO,
            created_at_ms=10,
            updated_at_ms=10,
        )
        location = MediaLocation(
            id=MediaLocationId.from_string("22222222-3333-4444-8555-666666666666"),
            media_id=media.id,
            library_id=library_id,
            relative_path=relative_path,
            availability=MediaLocationAvailability.AVAILABLE,
            observed_size_bytes=123456,
            observed_mtime_ns=None,
            created_at_ms=10,
            updated_at_ms=10,
        )
        return type(
            "ImportResult",
            (),
            {
                "library_id": library_id,
                "media": media,
                "location": location,
                "created": self.created,
            },
        )()


def _client(
    *,
    import_media: _FakeImportMedia | None = None,
    catalog_available: bool = True,
    database_path: Path | None = None,
) -> TestClient:
    settings = KronikaSettings(
        host="127.0.0.1",
        database_path=database_path or Path("/tmp/framenest-media-import-api.sqlite3"),
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
            media_import_api_dependencies=MediaImportApiDependencies(
                import_media=import_media or _FakeImportMedia(),
                catalog_available=lambda: catalog_available,
            ),
        )
    )


def _post_import(client: TestClient, relative_path: str = "Series/Episode 01.mkv"):
    return client.post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/media-imports",
        json={"relative_path": relative_path},
    )


def test_import_returns_created_catalog_location() -> None:
    importer = _FakeImportMedia(created=True)

    response = _post_import(_client(import_media=importer))

    assert response.status_code == 200
    assert response.json() == {
        "library_id": CANONICAL_LIBRARY_ID,
        "status": "created",
        "media": {
            "id": "11111111-2222-4333-8444-555555555555",
            "kind": "video",
            "created_at_ms": 10,
            "updated_at_ms": 10,
        },
        "location": {
            "id": "22222222-3333-4444-8555-666666666666",
            "media_id": "11111111-2222-4333-8444-555555555555",
            "library_id": CANONICAL_LIBRARY_ID,
            "relative_path": "Series/Episode 01.mkv",
            "availability": "available",
            "observed_size_bytes": 123456,
            "observed_mtime_ns": None,
            "created_at_ms": 10,
            "updated_at_ms": 10,
        },
    }
    assert importer.calls == 1
    assert importer.last_relative_path == MediaRelativePath("Series/Episode 01.mkv")
    assert PRIVATE_ROOT_PATH not in response.text
    assert PRIVATE_DATABASE_PATH not in response.text


def test_import_returns_existing_catalog_location_idempotently() -> None:
    response = _post_import(_client(import_media=_FakeImportMedia(created=False)))

    assert response.status_code == 200
    assert response.json()["status"] == "already_imported"


def test_import_rejects_invalid_relative_path_with_uniform_validation_contract() -> None:
    response = _post_import(_client(), "../private.mp4")

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "VALIDATION_FAILED",
            "message": "Request validation failed.",
        }
    }
    assert "detail" not in response.json()
    assert "../private.mp4" not in response.text
    assert response.headers.get("cache-control") == "no-store"


def test_missing_catalog_returns_sanitized_503_and_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"

    response = _post_import(
        _client(catalog_available=False, database_path=database_path),
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "CATALOG_UNAVAILABLE",
            "message": "The local catalog is not available.",
        }
    }
    assert not database_path.exists()
    assert not database_path.parent.exists()
    assert str(database_path) not in response.text


@pytest.mark.parametrize(
    ("error", "status_code", "code", "message"),
    [
        (
            LibraryScanNotFoundError("private missing library"),
            404,
            "LIBRARY_NOT_FOUND",
            "Library not found.",
        ),
        (
            MediaImportCandidateUnavailableError("private candidate"),
            409,
            "MEDIA_IMPORT_CANDIDATE_UNAVAILABLE",
            "Media import candidate is not available.",
        ),
        (
            LibraryScanUnavailableError(SCAN_UNAVAILABLE_MESSAGE),
            409,
            "MEDIA_IMPORT_CANDIDATE_UNAVAILABLE",
            "Media import candidate is not available.",
        ),
        (
            MediaImportFailedError("private database failure"),
            500,
            "MEDIA_IMPORT_FAILED",
            "Media import failed.",
        ),
        (
            LibraryScanFailedError(SCAN_FAILED_MESSAGE),
            500,
            "MEDIA_IMPORT_FAILED",
            "Media import failed.",
        ),
        (
            RuntimeError(UNDERLYING_EXCEPTION_TEXT),
            500,
            "MEDIA_IMPORT_FAILED",
            "Media import failed.",
        ),
    ],
)
def test_import_errors_are_sanitized(
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    response = _post_import(_client(import_media=_FakeImportMedia(error=error)))

    assert response.status_code == status_code
    assert response.json() == {"error": {"code": code, "message": message}}
    assert UNDERLYING_EXCEPTION_TEXT not in response.text
    assert PRIVATE_ROOT_PATH not in response.text


def test_repository_failure_returns_catalog_unavailable_without_exception_text() -> None:
    response = _post_import(
        _client(import_media=_FakeImportMedia(error=FrameNestLibraryRepositoryError(
            UNDERLYING_EXCEPTION_TEXT
        )))
    )

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "CATALOG_UNAVAILABLE",
            "message": "The local catalog is not available.",
        }
    }
    assert UNDERLYING_EXCEPTION_TEXT not in response.text
