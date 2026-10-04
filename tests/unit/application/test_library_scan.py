"""Unit tests for library scan preview application values and service."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

from kronika.application.library_scan import (
    DEFAULT_MAX_CANDIDATES,
    DEFAULT_MAX_ENTRIES,
    FrameNestLibraryScanError,
    LibraryFilesystemScanResult,
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanNotFoundError,
    LibraryScanPreviewResult,
    LibraryScanSummary,
    PreviewLibraryScan,
    VIDEO_EXTENSIONS,
    classify_candidate_extension,
    default_scan_limits,
)
from kronika.domain import DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_SCAN = REPOSITORY_ROOT / "src" / "kronika" / "application" / "library_scan.py"


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
        self.last_root: LibraryRoot | None = None
        self.last_limits: LibraryScanLimits | None = None

    def preview(
        self,
        root: LibraryRoot,
        limits: LibraryScanLimits,
    ) -> LibraryFilesystemScanResult:
        self.last_root = root
        self.last_limits = limits
        return self.result


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


def test_default_limits_are_valid() -> None:
    limits = default_scan_limits()
    assert limits.max_entries == DEFAULT_MAX_ENTRIES
    assert limits.max_candidates == DEFAULT_MAX_CANDIDATES


@pytest.mark.parametrize(
    ("max_entries", "max_candidates"),
    [
        (1, 1),
        (1_000_000, 10_000),
    ],
)
def test_limits_accept_bounds(max_entries: int, max_candidates: int) -> None:
    limits = LibraryScanLimits(max_entries=max_entries, max_candidates=max_candidates)
    assert limits.max_entries == max_entries
    assert limits.max_candidates == max_candidates


@pytest.mark.parametrize(
    "invalid",
    [0, 1_000_001, -1, True, "100"],
)
def test_max_entries_outside_bounds_rejected(invalid: object) -> None:
    with pytest.raises(FrameNestLibraryScanError):
        LibraryScanLimits(max_entries=invalid, max_candidates=1)


@pytest.mark.parametrize(
    "invalid",
    [0, 10_001, -1, True, "10"],
)
def test_max_candidates_outside_bounds_rejected(invalid: object) -> None:
    with pytest.raises(FrameNestLibraryScanError):
        LibraryScanLimits(max_entries=1, max_candidates=invalid)


def test_candidate_and_summary_values_are_immutable() -> None:
    candidate = LibraryScanCandidate(
        relative_path="a/b.mkv",
        kind=LibraryScanCandidateKind.VIDEO,
        extension=".mkv",
        size_bytes=10,
    )
    summary = _empty_summary()
    with pytest.raises(AttributeError):
        candidate.relative_path = "x"  # type: ignore[misc]
    with pytest.raises(AttributeError):
        summary.entries_seen = 1  # type: ignore[misc]


def test_candidate_rejects_invalid_relative_paths() -> None:
    for path in ("", "/abs.mkv", "a/../b.mkv", "a/./b.mkv"):
        with pytest.raises(FrameNestLibraryScanError):
            LibraryScanCandidate(
                relative_path=path,
                kind=LibraryScanCandidateKind.VIDEO,
                extension=".mkv",
                size_bytes=1,
            )


def test_candidate_rejects_boolean_size() -> None:
    with pytest.raises(FrameNestLibraryScanError):
        LibraryScanCandidate(
            relative_path="a.mkv",
            kind=LibraryScanCandidateKind.VIDEO,
            extension=".mkv",
            size_bytes=True,  # type: ignore[arg-type]
        )


def test_scan_errors_do_not_echo_invalid_values() -> None:
    rejected = 9_999_999
    with pytest.raises(FrameNestLibraryScanError) as exc_info:
        LibraryScanLimits(max_entries=rejected, max_candidates=1)
    assert str(rejected) not in str(exc_info.value)


@pytest.mark.parametrize("extension", sorted(VIDEO_EXTENSIONS))
def test_video_extensions_classify_as_video(extension: str) -> None:
    assert classify_candidate_extension(extension) == LibraryScanCandidateKind.VIDEO


def test_gif_extension_classifies_as_gif() -> None:
    assert classify_candidate_extension(".gif") == LibraryScanCandidateKind.GIF


def test_unknown_extension_returns_none() -> None:
    assert classify_candidate_extension(".txt") is None


def test_preview_service_delegates_root_to_scanner() -> None:
    library = _sample_library()
    filesystem_result = LibraryFilesystemScanResult(summary=_empty_summary(), candidates=())
    scanner = _FakeScanner(filesystem_result)
    repository = _FakeLibraryRepository(library)
    service = PreviewLibraryScan(repository, scanner)

    result = service.execute(library.id, default_scan_limits())

    assert scanner.last_root == library.root
    assert scanner.last_limits == default_scan_limits()
    assert result.library_id == library.id


def test_preview_service_raises_not_found_for_missing_library() -> None:
    service = PreviewLibraryScan(_FakeLibraryRepository(None), _FakeScanner(
        LibraryFilesystemScanResult(summary=_empty_summary(), candidates=())
    ))
    with pytest.raises(LibraryScanNotFoundError):
        service.execute(LibraryId.new(), default_scan_limits())


def test_preview_service_requests_no_database_write() -> None:
    library = _sample_library()
    repository = _FakeLibraryRepository(library)
    service = PreviewLibraryScan(
        repository,
        _FakeScanner(LibraryFilesystemScanResult(summary=_empty_summary(), candidates=())),
    )
    service.execute(library.id, default_scan_limits())
    assert repository.write_calls == 0


def test_application_scan_module_imports_no_infrastructure() -> None:
    tree = ast.parse(APPLICATION_SCAN.read_text(encoding="utf-8"), filename=str(APPLICATION_SCAN))
    violations: list[str] = []
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
