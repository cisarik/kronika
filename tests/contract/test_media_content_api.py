"""Contract tests for the secure media content API."""

from __future__ import annotations

from dataclasses import dataclass, fields
from pathlib import Path

from fastapi.testclient import TestClient

from framenest.adapters.api.application import create_app
from framenest.adapters.api.media_content_api import (
    MediaContentApiDependencies,
    _FALLBACK_DOWNLOAD_FILENAME,
)
from framenest.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from tests.support.record_access import scoped_policy, synthetic_identity
from framenest.application.media_content import (
    MediaContentFailedError,
    MediaContentNotFoundError,
    MediaContentUnavailableError,
    ResolvedMediaContent,
)
from framenest.application.ports.media_repository import FrameNestMediaRepositoryError
from framenest.application.ports.library_repository import FrameNestLibraryRepositoryError
from framenest.configuration import FrameNestSettings

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID = "abcdefab-cdef-4abc-8def-abcdefabcdef"
PRIVATE_TEXT = "secret-private-db-path-leak"
MP4_BYTES = bytes(range(256)) * 4
GIF_BYTES = b"GIF89a" + b"\x00" * 100
CONTENT_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/content"
DOWNLOAD_PATH = f"/api/media/{MEDIA_ID}/locations/{LOCATION_ID}/download"


@dataclass
class _FakeResolveContent:
    result: ResolvedMediaContent | None = None
    error: Exception | None = None

    def execute(self, media_id, location_id):
        if self.error is not None:
            raise self.error
        return self.result


def _slice(payload, start, length):
    if length is None:
        yield payload[start:]
    else:
        yield payload[start : start + length]


def _resolved(media_type, payload, close=None, download_filename="safe-media.mp4"):
    return ResolvedMediaContent(
        media_type=media_type,
        byte_size=len(payload),
        stream=lambda start, length: _slice(payload, start, length),
        close=close or (lambda: None),
        download_filename=download_filename,
    )


def _client(
    resolve=None,
    catalog_available=True,
    database_path=None,
    audience_ids: set[str] | None = None,
):
    deps = MediaContentApiDependencies(
        resolve_content=resolve or _FakeResolveContent(),
        catalog_available=lambda: catalog_available,
        audience_policy=scoped_policy(
            {MEDIA_ID} if audience_ids is None else audience_ids
        ),
    )
    settings = FrameNestSettings(
        database_path=database_path or Path("/tmp/framenest-media-content-api.sqlite3"),
        _env_file=None,
    )
    app = create_app(settings=settings, media_content_api_dependencies=deps)

    class _Caller:
        def __init__(self, app):
            self.app = app

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http":
                scope[SCOPE_IDENTITY] = synthetic_identity("alice")
            await self.app(scope, receive, send)

    app.add_middleware(_Caller)
    return TestClient(app)


def test_content_and_download_denial_does_not_open_the_resolver():
    class _Counting(_FakeResolveContent):
        calls = 0

        def execute(self, media_id, location_id):
            self.calls += 1
            return super().execute(media_id, location_id)

    counting = _Counting(result=_resolved("image/gif", GIF_BYTES))
    client = _client(resolve=counting, audience_ids=set())
    assert client.get(CONTENT_PATH).status_code == 404
    assert client.get(DOWNLOAD_PATH).status_code == 404
    assert counting.calls == 0


def test_full_gif_200():
    resolved = _resolved("image/gif", GIF_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(CONTENT_PATH)
    assert response.status_code == 200
    assert response.content == GIF_BYTES
    assert response.headers["content-type"] == "image/gif"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["accept-ranges"] == "bytes"
    assert response.headers["content-length"] == str(len(GIF_BYTES))


def test_full_mp4_200():
    resolved = _resolved("video/mp4", MP4_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(CONTENT_PATH)
    assert response.status_code == 200
    assert response.content == MP4_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == str(len(MP4_BYTES))


def test_download_returns_attachment_without_range_semantics():
    resolved = _resolved("video/mp4", MP4_BYTES, download_filename="safe-title.mp4")
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(DOWNLOAD_PATH)
    assert response.status_code == 200
    assert response.content == MP4_BYTES
    assert response.headers["content-type"] == "video/mp4"
    assert response.headers["content-length"] == str(len(MP4_BYTES))
    assert response.headers["content-disposition"] == 'attachment; filename="safe-title.mp4"'
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "private, no-store"
    assert "accept-ranges" not in response.headers


def test_download_content_disposition_uses_sanitized_filename_only():
    unsafe = 'unsafe\r\nAuthorization: secret.mp4'
    resolved = _resolved("video/mp4", MP4_BYTES, download_filename="unsafe-Authorization-secret.mp4")
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(DOWNLOAD_PATH)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == (
        'attachment; filename="unsafe-Authorization-secret.mp4"'
    )
    assert unsafe not in response.text
    assert "\r" not in response.headers["content-disposition"]
    assert "\n" not in response.headers["content-disposition"]


def test_download_content_disposition_defends_against_unsanitized_dependency_filename():
    resolved = _resolved("video/mp4", MP4_BYTES, download_filename='bad\r\nname".mp4')
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(DOWNLOAD_PATH)
    assert response.status_code == 200
    assert response.headers["content-disposition"] == 'attachment; filename="kronika-media.bin"'


def test_fallback_download_filename_is_kronika_only():
    """The unusable-filename fallback is the sole writer of the served filename."""
    default = {f.name: f for f in fields(ResolvedMediaContent)}["download_filename"].default
    assert _FALLBACK_DOWNLOAD_FILENAME == "kronika-media.bin"
    assert "framenest" not in _FALLBACK_DOWNLOAD_FILENAME.lower()
    assert default == "kronika-media.bin"
    assert default == _FALLBACK_DOWNLOAD_FILENAME


def test_closed_range_206():
    resolved = _resolved("video/mp4", MP4_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": "bytes=0-9"}
    )
    assert response.status_code == 206
    assert response.content == MP4_BYTES[0:10]
    assert response.headers["content-length"] == "10"
    assert response.headers["content-range"] == f"bytes 0-9/{len(MP4_BYTES)}"


def test_open_ended_range_206():
    resolved = _resolved("video/mp4", MP4_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": "bytes=500-"}
    )
    assert response.status_code == 206
    assert response.content == MP4_BYTES[500:]
    assert response.headers["content-length"] == str(len(MP4_BYTES) - 500)


def test_suffix_range_206():
    resolved = _resolved("video/mp4", MP4_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": "bytes=-10"}
    )
    assert response.status_code == 206
    assert response.content == MP4_BYTES[-10:]
    assert response.headers["content-length"] == "10"


def test_malformed_range_416():
    resolved = _resolved("video/mp4", MP4_BYTES)
    for bad in ["bytes=abc", "0-10", "bytes=", "bytes=1-2,3-4", "bytes=-0", "bytes=5-3"]:
        response = _client(resolve=_FakeResolveContent(result=resolved)).get(
            CONTENT_PATH, headers={"Range": bad}
        )
        assert response.status_code == 416, f"failed for {bad}"
        assert response.headers["content-range"] == f"bytes */{len(MP4_BYTES)}"
        assert response.headers["cache-control"] == "no-store"


def test_unsatisfiable_range_416():
    resolved = _resolved("video/mp4", MP4_BYTES)
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": f"bytes={len(MP4_BYTES)}-"}
    )
    assert response.status_code == 416
    assert response.headers["content-range"] == f"bytes */{len(MP4_BYTES)}"


def test_zero_byte_file_full_200():
    resolved = _resolved("video/mp4", b"")
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(CONTENT_PATH)
    assert response.status_code == 200
    assert response.content == b""
    assert response.headers["content-length"] == "0"


def test_invalid_uuid_returns_422():
    response = _client().get("/api/media/not-a-uuid/locations/not-a-uuid/content")
    assert response.status_code == 422


def test_catalog_unavailable_503():
    response = _client(catalog_available=False).get(CONTENT_PATH)
    assert response.status_code == 503
    assert response.headers["cache-control"] == "no-store"


def test_not_found_404():
    response = _client(
        resolve=_FakeResolveContent(error=MediaContentNotFoundError("x"))
    ).get(CONTENT_PATH)
    assert response.status_code == 404
    assert PRIVATE_TEXT not in response.text


def test_download_not_found_404():
    response = _client(
        resolve=_FakeResolveContent(error=MediaContentNotFoundError(PRIVATE_TEXT))
    ).get(DOWNLOAD_PATH)
    assert response.status_code == 404
    assert PRIVATE_TEXT not in response.text


def test_unavailable_409():
    response = _client(
        resolve=_FakeResolveContent(error=MediaContentUnavailableError("x"))
    ).get(CONTENT_PATH)
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"


def test_download_unavailable_409():
    response = _client(
        resolve=_FakeResolveContent(error=MediaContentUnavailableError(PRIVATE_TEXT))
    ).get(DOWNLOAD_PATH)
    assert response.status_code == 409
    assert response.headers["cache-control"] == "no-store"
    assert PRIVATE_TEXT not in response.text


def test_unexpected_failure_500():
    response = _client(
        resolve=_FakeResolveContent(error=FrameNestMediaRepositoryError(PRIVATE_TEXT))
    ).get(CONTENT_PATH)
    assert response.status_code == 500
    assert PRIVATE_TEXT not in response.text


def test_no_absolute_path_or_exception_disclosure():
    for error in [
        MediaContentNotFoundError("private-path-leak"),
        MediaContentUnavailableError("private-path-leak"),
        MediaContentFailedError("private-path-leak"),
        FrameNestLibraryRepositoryError(PRIVATE_TEXT),
    ]:
        response = _client(resolve=_FakeResolveContent(error=error)).get(CONTENT_PATH)
        assert "private-path-leak" not in response.text
        assert PRIVATE_TEXT not in response.text


def test_malformed_range_closes_content():
    closed = []
    resolved = _resolved("video/mp4", MP4_BYTES, close=lambda: closed.append(1))
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": "bytes=abc"}
    )
    assert response.status_code == 416
    assert closed == [1]


def test_unsatisfiable_range_closes_content():
    closed = []
    resolved = _resolved("video/mp4", MP4_BYTES, close=lambda: closed.append(1))
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": f"bytes={len(MP4_BYTES)}-"}
    )
    assert response.status_code == 416
    assert closed == [1]


def test_response_finalization_closes_content_on_full_stream():
    closed = []
    resolved = _resolved("video/mp4", MP4_BYTES, close=lambda: closed.append(1))
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(CONTENT_PATH)
    assert response.status_code == 200
    assert closed == [1]


def test_download_response_finalization_closes_content_on_stream():
    closed = []
    resolved = _resolved("video/mp4", MP4_BYTES, close=lambda: closed.append(1))
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(DOWNLOAD_PATH)
    assert response.status_code == 200
    assert closed == [1]


def test_response_finalization_closes_content_on_ranged_stream():
    closed = []
    resolved = _resolved("video/mp4", MP4_BYTES, close=lambda: closed.append(1))
    response = _client(resolve=_FakeResolveContent(result=resolved)).get(
        CONTENT_PATH, headers={"Range": "bytes=0-9"}
    )
    assert response.status_code == 206
    assert closed == [1]
