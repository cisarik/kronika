"""Unit tests for explicit media import from scan candidates."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanSummary,
    default_scan_limits,
)
from kronika.application.media_import import (
    ImportMediaFromScanCandidate,
    MediaImportCandidateUnavailableError,
)
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
APPLICATION_IMPORT = REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_import.py"
CANONICAL_MEDIA_ID = MediaId.from_string("11111111-2222-4333-8444-555555555555")
CANONICAL_LOCATION_ID = MediaLocationId.from_string("22222222-3333-4444-8555-666666666666")


class _FakeLibraryRepository:
    def __init__(self, library: Library | None) -> None:
        self._library = library
        self.write_calls = 0

    def add(self, library: Library) -> None:
        self.write_calls += 1

    def get(self, library_id: LibraryId) -> Library | None:
        if self._library is None or self._library.id != library_id:
            return None
        return self._library

    def list_all(self) -> tuple[Library, ...]:
        return () if self._library is None else (self._library,)


class _FakeScanner:
    def __init__(self, result: LibraryFilesystemScanResult) -> None:
        self.result = result
        self.calls = 0
        self.last_root: LibraryRoot | None = None
        self.last_limits: LibraryScanLimits | None = None

    def preview(
        self,
        root: LibraryRoot,
        limits: LibraryScanLimits,
    ) -> LibraryFilesystemScanResult:
        self.calls += 1
        self.last_root = root
        self.last_limits = limits
        return self.result


class _FakeMediaRepository:
    def __init__(self) -> None:
        self.media_by_id: dict[MediaId, LogicalMedia] = {}
        self.locations_by_path: dict[tuple[LibraryId, MediaRelativePath], MediaLocation] = {}
        self.add_calls = 0

    def add_media(self, media: LogicalMedia) -> None:
        self.media_by_id[media.id] = media

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        return self.media_by_id.get(media_id)

    def list_media(self) -> tuple[LogicalMedia, ...]:
        return tuple(self.media_by_id.values())

    def add_location(self, location: MediaLocation) -> None:
        self.locations_by_path[(location.library_id, location.relative_path)] = location

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        for location in self.locations_by_path.values():
            if location.id == location_id:
                return location
        return None

    def get_location_by_library_path(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaLocation | None:
        return self.locations_by_path.get((library_id, relative_path))

    def list_locations_for_media(self, media_id: MediaId) -> tuple[MediaLocation, ...]:
        return tuple(
            location
            for location in self.locations_by_path.values()
            if location.media_id == media_id
        )

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        self.add_calls += 1
        self.media_by_id[media.id] = media
        self.locations_by_path[(location.library_id, location.relative_path)] = location


def _empty_summary() -> LibraryScanSummary:
    return LibraryScanSummary(
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
    )


def _sample_library() -> Library:
    return Library(
        id=LibraryId.from_string("12345678-1234-4234-9234-123456789abc"),
        device_id=DeviceId.from_string("abcdefab-cdef-4abc-8def-abcdefabcdef"),
        display_name="Videos",
        root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path="/tmp/videos"),
    )


def _scan_result_with_candidates() -> LibraryFilesystemScanResult:
    return LibraryFilesystemScanResult(
        summary=_empty_summary(),
        candidates=(
            LibraryScanCandidate(
                relative_path="clips/a.mp4",
                kind=LibraryScanCandidateKind.VIDEO,
                extension=".mp4",
                size_bytes=123,
            ),
            LibraryScanCandidate(
                relative_path="gifs/reaction.gif",
                kind=LibraryScanCandidateKind.GIF,
                extension=".gif",
                size_bytes=456,
            ),
        ),
    )


def _service(
    library: Library | None,
    media_repository: _FakeMediaRepository | None = None,
    scanner: _FakeScanner | None = None,
) -> tuple[ImportMediaFromScanCandidate, _FakeMediaRepository, _FakeScanner]:
    resolved_media_repository = media_repository or _FakeMediaRepository()
    resolved_scanner = scanner or _FakeScanner(_scan_result_with_candidates())
    return (
        ImportMediaFromScanCandidate(
            _FakeLibraryRepository(library),
            resolved_media_repository,
            resolved_scanner,
            media_id_factory=lambda: CANONICAL_MEDIA_ID,
            location_id_factory=lambda: CANONICAL_LOCATION_ID,
            clock_ms=lambda: 1234,
        ),
        resolved_media_repository,
        resolved_scanner,
    )


def test_import_persists_selected_video_candidate() -> None:
    library = _sample_library()
    service, media_repository, scanner = _service(library)

    result = service.execute(
        library.id,
        MediaRelativePath("clips/a.mp4"),
        default_scan_limits(),
    )

    assert scanner.calls == 1
    assert scanner.last_root == library.root
    assert result.created is True
    assert result.media.id == CANONICAL_MEDIA_ID
    assert result.media.kind == MediaKind.VIDEO
    assert result.media.created_at_ms == 1234
    assert result.location.id == CANONICAL_LOCATION_ID
    assert result.location.relative_path == MediaRelativePath("clips/a.mp4")
    assert result.location.availability == MediaLocationAvailability.AVAILABLE
    assert result.location.observed_size_bytes == 123
    assert result.location.observed_mtime_ns is None
    assert media_repository.add_calls == 1


def test_import_maps_gif_candidate_to_animated_image() -> None:
    library = _sample_library()
    service, _, _ = _service(library)

    result = service.execute(
        library.id,
        MediaRelativePath("gifs/reaction.gif"),
        default_scan_limits(),
    )

    assert result.created is True
    assert result.media.kind == MediaKind.ANIMATED_IMAGE
    assert result.location.observed_size_bytes == 456


def test_import_is_idempotent_for_existing_library_path() -> None:
    library = _sample_library()
    service, media_repository, scanner = _service(library)

    first = service.execute(library.id, MediaRelativePath("clips/a.mp4"), default_scan_limits())
    second = service.execute(library.id, MediaRelativePath("clips/a.mp4"), default_scan_limits())

    assert scanner.calls == 2
    assert first.created is True
    assert second.created is False
    assert second.media == first.media
    assert second.location == first.location
    assert media_repository.add_calls == 1


def test_import_revalidates_against_fresh_scan_candidates() -> None:
    library = _sample_library()
    scanner = _FakeScanner(_scan_result_with_candidates())
    service, _, _ = _service(library, scanner=scanner)

    with pytest.raises(MediaImportCandidateUnavailableError):
        service.execute(library.id, MediaRelativePath("missing.mp4"), default_scan_limits())

    assert scanner.calls == 1


def test_import_raises_not_found_for_missing_library() -> None:
    service, _, _ = _service(None)

    with pytest.raises(LibraryScanNotFoundError):
        service.execute(LibraryId.new(), MediaRelativePath("clips/a.mp4"), default_scan_limits())


def test_import_module_imports_no_infrastructure_or_media_tools() -> None:
    tree = ast.parse(APPLICATION_IMPORT.read_text(encoding="utf-8"), filename=str(APPLICATION_IMPORT))
    violations: list[str] = []
    forbidden_roots = {
        "kronika.infrastructure",
        "kronika.adapters",
        "kronika.infrastructure.media_analysis",
        "kronika.infrastructure.ai",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        if any(module.startswith(root) for root in forbidden_roots):
            violations.append(module)
    assert violations == []
