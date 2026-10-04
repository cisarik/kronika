"""Contract tests for identity-only gallery preview delivery."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.gallery_preview_api import GalleryPreviewApiDependencies
from kronika.application.gallery_preview import GalleryPreviewNotFoundError, GalleryPreviewUnavailableError
from kronika.application.ports.gallery_preview import OpenedGalleryPreview
from kronika.configuration import KronikaSettings
from tests.support.record_access import install_synthetic_caller, scoped_policy

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
PREVIEW_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/gallery-preview"
PRIVATE_PATH = "/Users/example/private/cache"
JPEG_BYTES = b"\xff\xd8\xff\xd9"


@dataclass
class _FakePreviewService:
    result: OpenedGalleryPreview | None = None
    error: Exception | None = None
    calls: int = 0

    def open_ready(self, media_id, location_id):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def _opened(payload: bytes = JPEG_BYTES, etag: str = '"abc"') -> OpenedGalleryPreview:
    return OpenedGalleryPreview(
        media_type="image/jpeg",
        byte_size=len(payload),
        etag=etag,
        payload=payload,
        close=lambda: None,
    )


def _client(
    service: _FakePreviewService,
    *,
    catalog_available: bool = True,
    audience_ids: set[str] | None = None,
) -> TestClient:
    settings = KronikaSettings(
        database_path=Path("/tmp/framenest-gallery-preview-api.sqlite3"),
        gallery_preview_cache_path=Path("/tmp/framenest-gallery-preview-cache"),
        _env_file=None,
    )
    deps = GalleryPreviewApiDependencies(
        preview_service=service,
        catalog_available=lambda: catalog_available,
        audience_policy=scoped_policy(
            {MEDIA_ID} if audience_ids is None else audience_ids
        ),
    )
    app = create_app(settings=settings, gallery_preview_api_dependencies=deps)
    return TestClient(install_synthetic_caller(app))


def test_preview_denial_does_not_open_the_cache() -> None:
    service = _FakePreviewService(result=_opened())
    response = _client(service, audience_ids=set()).get(PREVIEW_PATH)
    assert response.status_code == 404
    assert service.calls == 0


def test_gallery_preview_returns_jpeg_etag_and_no_paths() -> None:
    response = _client(_FakePreviewService(result=_opened())).get(PREVIEW_PATH)
    assert response.status_code == 200
    assert response.content == JPEG_BYTES
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["etag"] == '"abc"'
    assert response.headers["cache-control"] == "private, max-age=0, must-revalidate"
    assert response.headers["content-disposition"] == "inline"
    assert PRIVATE_PATH not in response.text


def test_gallery_preview_matching_if_none_match_returns_304() -> None:
    response = _client(_FakePreviewService(result=_opened())).get(
        PREVIEW_PATH,
        headers={"If-None-Match": '"abc"'},
    )
    assert response.status_code == 304
    assert response.content == b""
    assert response.headers["etag"] == '"abc"'


def test_missing_derivative_is_not_generated_by_get_and_error_is_sanitized() -> None:
    service = _FakePreviewService(error=GalleryPreviewUnavailableError(PRIVATE_PATH))
    response = _client(service).get(PREVIEW_PATH)
    assert response.status_code == 409
    assert service.calls == 1
    assert PRIVATE_PATH not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_endpoint_validates_relationship_error_as_not_found() -> None:
    response = _client(_FakePreviewService(error=GalleryPreviewNotFoundError(PRIVATE_PATH))).get(
        PREVIEW_PATH
    )
    assert response.status_code == 404
    assert PRIVATE_PATH not in response.text


def test_catalog_unavailable_returns_503_without_path_disclosure() -> None:
    response = _client(_FakePreviewService(result=_opened()), catalog_available=False).get(PREVIEW_PATH)
    assert response.status_code == 503
    assert PRIVATE_PATH not in response.text
