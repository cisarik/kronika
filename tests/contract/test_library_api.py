"""Contract tests for the read-only library browser API."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanSummary,
    LibraryScanUnavailableError,
    SCAN_FAILED_MESSAGE,
    SCAN_UNAVAILABLE_MESSAGE,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.configuration import KronikaSettings
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.adapters.api.application import create_app

CANONICAL_LIBRARY_ID = "12345678-1234-4234-9234-123456789abc"
SECOND_LIBRARY_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
PRIVATE_ROOT_PATH = "/Users/example/private/videos"
PRIVATE_DATABASE_PATH = "/Users/example/private/catalog.sqlite3"
UNDERLYING_EXCEPTION_TEXT = "sqlite failed near private table"


@dataclass
class _FakeRepository:
    libraries: tuple[Library, ...] = ()
    list_error: Exception | None = None
    get_error: Exception | None = None
    list_calls: int = 0

    def add(self, library: Library) -> None:
        raise AssertionError("API tests must not register libraries")

    def get(self, library_id: LibraryId) -> Library | None:
        if self.get_error is not None:
            raise self.get_error
        for library in self.libraries:
            if library.id == library_id:
                return library
        return None

    def list_all(self) -> tuple[Library, ...]:
        self.list_calls += 1
        if self.list_error is not None:
            raise self.list_error
        return self.libraries


@dataclass
class _FakePreviewScan:
    result: LibraryFilesystemScanResult | None = None
    error: Exception | None = None
    calls: int = 0

    def execute(
        self,
        library_id: LibraryId,
        limits: LibraryScanLimits,
    ) -> object:
        self.calls += 1
        if self.error is not None:
            raise self.error
        if self.result is None:
            raise AssertionError("fake scan result was not configured")
        return type(
            "PreviewResult",
            (),
            {
                "library_id": library_id,
                "limits": limits,
                "summary": self.result.summary,
                "candidates": self.result.candidates,
            },
        )()


def _library(
    *,
    library_id: str = CANONICAL_LIBRARY_ID,
    display_name: str = "My Videos",
    root_path: str = PRIVATE_ROOT_PATH,
) -> Library:
    return Library(
        id=LibraryId.from_string(library_id),
        device_id=DeviceId.new(),
        display_name=display_name,
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=root_path),
    )


def _summary(**overrides: object) -> LibraryScanSummary:
    values = {
        "entries_seen": 0,
        "directories_seen": 0,
        "regular_files_seen": 0,
        "candidate_files_seen": 0,
        "candidate_bytes_seen": 0,
        "skipped_hidden_entries": 0,
        "skipped_symlink_entries": 0,
        "skipped_other_entries": 0,
        "inaccessible_entries": 0,
        "truncated": False,
        "candidates_truncated": False,
    }
    values.update(overrides)
    return LibraryScanSummary(**values)  # type: ignore[arg-type]


def _scan_result() -> LibraryFilesystemScanResult:
    candidates = (
        LibraryScanCandidate(
            relative_path="Series/Episode 01.mkv",
            kind=LibraryScanCandidateKind.VIDEO,
            extension=".mkv",
            size_bytes=123456,
        ),
    )
    return LibraryFilesystemScanResult(
        summary=_summary(
            entries_seen=3,
            regular_files_seen=1,
            candidate_files_seen=1,
            candidate_bytes_seen=123456,
        ),
        candidates=candidates,
    )


def _client(
    *,
    repository: _FakeRepository | None = None,
    scan: _FakePreviewScan | None = None,
    catalog_available: bool = True,
    database_path: Path | None = None,
) -> TestClient:
    from kronika.adapters.api.library_api import LibraryApiDependencies

    dependencies = LibraryApiDependencies(
        repository=repository or _FakeRepository(),
        scan_preview=scan or _FakePreviewScan(result=_scan_result()),
        catalog_available=lambda: catalog_available,
    )
    if database_path is None:
        settings = KronikaSettings(host="127.0.0.1", _env_file=None)
    else:
        settings = KronikaSettings(
            host="127.0.0.1",
            database_path=database_path,
            _env_file=None,
        )
    return TestClient(
        create_app(
            settings=settings,
            library_api_dependencies=dependencies,
        )
    )


def test_list_libraries_returns_empty_list_for_valid_empty_catalog() -> None:
    response = _client(repository=_FakeRepository()).get("/api/libraries")

    assert response.status_code == 200
    assert response.json() == {"libraries": []}


def test_list_libraries_returns_deterministic_public_fields_only() -> None:
    repository = _FakeRepository(
        libraries=(
            _library(library_id=CANONICAL_LIBRARY_ID, display_name="Alpha"),
            _library(library_id=SECOND_LIBRARY_ID, display_name="Beta", root_path="/private/beta"),
        )
    )

    response = _client(repository=repository).get("/api/libraries")

    assert response.status_code == 200
    assert response.json() == {
        "libraries": [
            {
                "id": CANONICAL_LIBRARY_ID,
                "display_name": "Alpha",
                "path_flavor": "posix",
            },
            {
                "id": SECOND_LIBRARY_ID,
                "display_name": "Beta",
                "path_flavor": "posix",
            },
        ]
    }
    body = response.text
    assert PRIVATE_ROOT_PATH not in body
    assert "/private/beta" not in body
    assert PRIVATE_DATABASE_PATH not in body
    assert "FRAMENEST_DATABASE_PATH" not in body


def test_absent_catalog_returns_sanitized_503_and_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"

    response = _client(
        catalog_available=False,
        database_path=database_path,
    ).get("/api/libraries")

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


def test_scan_success_returns_default_limits_summary_and_relative_candidates() -> None:
    repository = _FakeRepository(libraries=(_library(),))
    scan = _FakePreviewScan(result=_scan_result())

    response = _client(repository=repository, scan=scan).post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/scan-preview"
    )

    assert response.status_code == 200
    assert response.json() == {
        "library_id": CANONICAL_LIBRARY_ID,
        "limits": {"max_entries": 100000, "max_candidates": 1000},
        "summary": {
            "entries_seen": 3,
            "directories_seen": 0,
            "regular_files_seen": 1,
            "candidate_files_seen": 1,
            "candidate_bytes_seen": 123456,
            "skipped_hidden_entries": 0,
            "skipped_symlink_entries": 0,
            "skipped_other_entries": 0,
            "inaccessible_entries": 0,
            "truncated": False,
            "candidates_truncated": False,
        },
        "candidates": [
            {
                "relative_path": "Series/Episode 01.mkv",
                "kind": "video",
                "extension": ".mkv",
                "size_bytes": 123456,
            }
        ],
    }
    assert scan.calls == 1
    assert PRIVATE_ROOT_PATH not in response.text


def test_list_endpoint_does_not_invoke_scan_preview() -> None:
    scan = _FakePreviewScan(result=_scan_result())

    response = _client(scan=scan).get("/api/libraries")

    assert response.status_code == 200
    assert scan.calls == 0


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
            LibraryScanUnavailableError(SCAN_UNAVAILABLE_MESSAGE),
            409,
            "LIBRARY_UNAVAILABLE",
            "Library scan preview is not available.",
        ),
        (
            LibraryScanFailedError(SCAN_FAILED_MESSAGE),
            500,
            "SCAN_FAILED",
            "Library scan failed.",
        ),
        (
            RuntimeError(UNDERLYING_EXCEPTION_TEXT),
            500,
            "SCAN_FAILED",
            "Library scan failed.",
        ),
    ],
)
def test_scan_errors_are_sanitized(
    error: Exception,
    status_code: int,
    code: str,
    message: str,
) -> None:
    repository = _FakeRepository(libraries=(_library(),))
    scan = _FakePreviewScan(error=error)

    response = _client(repository=repository, scan=scan).post(
        f"/api/libraries/{CANONICAL_LIBRARY_ID}/scan-preview"
    )

    assert response.status_code == status_code
    assert response.json() == {"error": {"code": code, "message": message}}
    assert UNDERLYING_EXCEPTION_TEXT not in response.text
    assert PRIVATE_ROOT_PATH not in response.text


@pytest.mark.parametrize("method", ["get", "post"])
def test_repository_failure_returns_catalog_unavailable_without_exception_text(method: str) -> None:
    repository = _FakeRepository(
        libraries=(_library(),),
        list_error=FrameNestLibraryRepositoryError(UNDERLYING_EXCEPTION_TEXT),
        get_error=FrameNestLibraryRepositoryError(UNDERLYING_EXCEPTION_TEXT),
    )
    if method == "get":
        client = _client(repository=repository)
        response = client.get("/api/libraries")
    else:
        client = _client(
            repository=repository,
            scan=_FakePreviewScan(
                error=FrameNestLibraryRepositoryError(UNDERLYING_EXCEPTION_TEXT)
            ),
        )
        response = client.post(f"/api/libraries/{CANONICAL_LIBRARY_ID}/scan-preview")

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "CATALOG_UNAVAILABLE",
            "message": "The local catalog is not available.",
        }
    }
    assert UNDERLYING_EXCEPTION_TEXT not in response.text


def test_malformed_library_id_uses_uniform_validation_contract() -> None:
    response = _client().post("/api/libraries/not-a-uuid/scan-preview")

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


def test_production_missing_catalog_read_does_not_create_database(tmp_path: Path) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"
    client = TestClient(
        create_app(
            settings=KronikaSettings(
                host="127.0.0.1",
                database_path=database_path,
                _env_file=None,
            )
        )
    )

    response = client.get("/api/libraries")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "CATALOG_UNAVAILABLE"
    assert not database_path.exists()
    assert not database_path.parent.exists()
