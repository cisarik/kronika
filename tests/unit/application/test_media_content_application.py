"""Unit tests for the secure media content resolution application service."""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from kronika.application.media_content import (
    MediaContentFailedError,
    MediaContentNotFoundError,
    MediaContentUnavailableError,
    ResolveMediaContent,
    safe_download_filename,
    supported_media_type,
)
from kronika.application.ports.media_content import OpenedMediaContent
from kronika.application.ports.media_repository import FrameNestMediaRepositoryError
from kronika.domain import (
    DeviceId,
    Library,
    LibraryId,
    LibraryPathFlavor,
    LibraryRoot,
    MediaId,
    MediaLocationId,
)
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_MEDIA_CONTENT = (
    REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_content.py"
)

MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
LOCATION_ID = MediaLocationId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef")
LIBRARY_ID = LibraryId.from_string("11111111-2222-4333-8444-555555555555")
DEVICE_ID = DeviceId.from_string("22222222-3333-4444-9555-666666666666")
PRIVATE_TEXT = "secret-private-path-in-error"


class _FakeMediaRepository:
    def __init__(self, media=None, location=None, error=None):
        self._media = media
        self._location = location
        self._error = error
        self.write_calls = 0

    def get_media(self, media_id):
        if self._error is not None:
            raise self._error
        if self._media is not None and self._media.id == media_id:
            return self._media
        return None

    def get_location(self, location_id):
        if self._error is not None:
            raise self._error
        if self._location is not None and self._location.id == location_id:
            return self._location
        return None

    def add_media(self, media):
        self.write_calls += 1

    def add_location(self, location):
        self.write_calls += 1

    def add_media_with_location(self, media, location):
        self.write_calls += 1

    def list_media(self):
        return ()

    def list_locations_for_media(self, media_id):
        return ()

    def get_location_by_library_path(self, library_id, relative_path):
        return None


class _FakeLibraryRepository:
    def __init__(self, library=None, error=None):
        self._library = library
        self._error = error
        self.write_calls = 0

    def get(self, library_id):
        if self._error is not None:
            raise self._error
        if self._library is not None and self._library.id == library_id:
            return self._library
        return None

    def add(self, library):
        self.write_calls += 1

    def list_all(self):
        return ()


class _FakeContentReader:
    def __init__(self, result=None, error=None):
        self._result = result
        self._error = error

    def open(self, root, relative_path, kind):
        if self._error is not None:
            raise self._error
        return self._result


def _opened(media_type="video/mp4", byte_size=10):
    return OpenedMediaContent(
        media_type=media_type,
        byte_size=byte_size,
        stream=lambda start, length: iter([b"x" * (length or byte_size)]),
        close=lambda: None,
    )


def _video_media():
    return LogicalMedia(id=MEDIA_ID, kind=MediaKind.VIDEO, created_at_ms=10, updated_at_ms=20)


def _gif_media():
    return LogicalMedia(id=MEDIA_ID, kind=MediaKind.ANIMATED_IMAGE, created_at_ms=10, updated_at_ms=20)


def _location(path="clips/sample.mp4", availability=MediaLocationAvailability.AVAILABLE, media_id=None):
    return MediaLocation(
        id=LOCATION_ID,
        media_id=media_id or MEDIA_ID,
        library_id=LIBRARY_ID,
        relative_path=MediaRelativePath(path),
        availability=availability,
        observed_size_bytes=100,
        observed_mtime_ns=200,
        created_at_ms=10,
        updated_at_ms=20,
    )


def _library():
    return Library(
        id=LIBRARY_ID,
        device_id=DEVICE_ID,
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


def _service(
    media=None,
    location=None,
    library=None,
    content_result=None,
    content_error=None,
    media_error=None,
    library_error=None,
):
    return ResolveMediaContent(
        _FakeMediaRepository(media=media, location=location, error=media_error),
        _FakeLibraryRepository(library=library, error=library_error),
        _FakeContentReader(result=content_result, error=content_error),
    )


def _full_service(content_result=None, content_error=None):
    return _service(
        media=_video_media(),
        location=_location(),
        library=_library(),
        content_result=content_result,
        content_error=content_error,
    )


def test_missing_media_raises_not_found():
    with pytest.raises(MediaContentNotFoundError):
        _service().execute(MEDIA_ID, LOCATION_ID)


def test_missing_location_raises_not_found():
    with pytest.raises(MediaContentNotFoundError):
        _service(media=_video_media()).execute(MEDIA_ID, LOCATION_ID)


def test_media_location_mismatch_raises_not_found():
    other_media = MediaId.from_string("99999999-9999-4999-8999-999999999999")
    with pytest.raises(MediaContentNotFoundError):
        _service(
            media=_video_media(),
            location=_location(media_id=other_media),
        ).execute(MEDIA_ID, LOCATION_ID)


def test_non_available_location_raises_unavailable():
    with pytest.raises(MediaContentUnavailableError):
        _service(
            media=_video_media(),
            location=_location(availability=MediaLocationAvailability.OFFLINE),
        ).execute(MEDIA_ID, LOCATION_ID)


def test_missing_library_raises_unavailable():
    with pytest.raises(MediaContentUnavailableError):
        _service(
            media=_video_media(),
            location=_location(),
            library=None,
        ).execute(MEDIA_ID, LOCATION_ID)


def test_unsupported_kind_extension_pair_raises_unavailable():
    with pytest.raises(MediaContentUnavailableError):
        _service(
            media=_gif_media(),
            location=_location(path="clips/sample.mp4"),
            library=_library(),
        ).execute(MEDIA_ID, LOCATION_ID)


def test_successful_resolution_returns_resolved_content():
    opened = _opened()
    result = _full_service(content_result=opened).execute(MEDIA_ID, LOCATION_ID)
    assert result.media_type == "video/mp4"
    assert result.byte_size == 10
    assert result.download_filename == "sample.mp4"


def test_safe_download_filename_sanitizes_unsafe_unicode_and_header_text():
    filename = safe_download_filename(
        media_id=MEDIA_ID,
        relative_path=MediaRelativePath("clips/😈 bad\r\nname; report.mp4"),
    )
    assert filename == "bad-name-report.mp4"
    assert "\r" not in filename
    assert "\n" not in filename
    assert ";" not in filename


def test_safe_download_filename_uses_deterministic_fallback_when_required():
    filename = safe_download_filename(
        media_id=MEDIA_ID,
        relative_path=MediaRelativePath("clips/😈.gif"),
    )
    stem, _, extension = filename.rpartition(".")
    identity = MEDIA_ID.to_string()
    # The fallback is deterministic: it is derived only from the media identity,
    # and only the extension of the sanitised-away name survives.
    assert extension == "gif"
    assert stem.endswith(f"-{identity}")
    assert re.fullmatch(r"[a-z0-9-]+", stem)
    # The fallback stem is what lands in a Downloads folder, so it is
    # user-visible. It must carry the sole product identity and never the
    # retired spelling. The current brand word is deliberately not spelled out
    # here, so this pin keeps its meaning across the remaining identity cuts
    # instead of breaking on a spelling this test does not own.
    assert "framenest" not in filename.lower()
    assert filename == safe_download_filename(
        media_id=MEDIA_ID,
        relative_path=MediaRelativePath("clips/😈.gif"),
    )


def test_repository_failure_propagates_without_private_info():
    sanitized = FrameNestMediaRepositoryError("Media catalog operation failed.")
    with pytest.raises(FrameNestMediaRepositoryError) as exc_info:
        _service(media_error=sanitized).execute(MEDIA_ID, LOCATION_ID)
    assert str(exc_info.value) == "Media catalog operation failed."


def test_content_reader_unexpected_error_becomes_failed():
    with pytest.raises(MediaContentFailedError):
        _full_service(content_error=RuntimeError(PRIVATE_TEXT)).execute(MEDIA_ID, LOCATION_ID)


def test_no_repository_mutation_calls_during_resolution():
    media_repo = _FakeMediaRepository(media=_video_media(), location=_location())
    lib_repo = _FakeLibraryRepository(library=_library())
    service = ResolveMediaContent(media_repo, lib_repo, _FakeContentReader(result=_opened()))
    service.execute(MEDIA_ID, LOCATION_ID)
    assert media_repo.write_calls == 0
    assert lib_repo.write_calls == 0


def test_application_media_content_module_imports_no_infrastructure():
    tree = ast.parse(
        APPLICATION_MEDIA_CONTENT.read_text(encoding="utf-8"),
        filename=str(APPLICATION_MEDIA_CONTENT),
    )
    violations = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        if module.startswith("kronika.infrastructure"):
            violations.append(module)
    assert violations == []


def test_supported_media_type_returns_none_for_unsupported():
    assert supported_media_type(MediaKind.VIDEO, ".gif") is None
    assert supported_media_type(MediaKind.ANIMATED_IMAGE, ".mp4") is None
    assert supported_media_type(MediaKind.VIDEO, ".txt") is None
