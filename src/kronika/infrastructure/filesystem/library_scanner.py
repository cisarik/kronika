"""Standard-library local filesystem scanner for library scan preview."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from kronika.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanFailedError,
    LibraryScanLimits,
    LibraryScanSummary,
    LibraryScanUnavailableError,
    SCAN_FAILED_MESSAGE,
    SCAN_UNAVAILABLE_MESSAGE,
    classify_candidate_extension,
)
from kronika.domain import LibraryPathFlavor, LibraryRoot

_SCAN_UNAVAILABLE = LibraryScanUnavailableError(SCAN_UNAVAILABLE_MESSAGE)
_SCAN_FAILED = LibraryScanFailedError(SCAN_FAILED_MESSAGE)


def _native_path_flavor() -> LibraryPathFlavor:
    if os.name == "nt":
        return LibraryPathFlavor.WINDOWS
    return LibraryPathFlavor.POSIX


def _entry_sort_key(name: str) -> tuple[str, str]:
    return (name.casefold(), name)


def _join_relative_path(parent: str, name: str) -> str:
    if not parent:
        return name
    return f"{parent}/{name}"


@dataclass
class _ScanState:
    limits: LibraryScanLimits
    entries_seen: int = 0
    directories_seen: int = 0
    regular_files_seen: int = 0
    candidate_files_seen: int = 0
    candidate_bytes_seen: int = 0
    skipped_hidden_entries: int = 0
    skipped_symlink_entries: int = 0
    skipped_other_entries: int = 0
    inaccessible_entries: int = 0
    truncated: bool = False
    candidates_truncated: bool = False
    candidates: list[LibraryScanCandidate] = field(default_factory=list)

    def summary(self) -> LibraryScanSummary:
        return LibraryScanSummary(
            entries_seen=self.entries_seen,
            directories_seen=self.directories_seen,
            regular_files_seen=self.regular_files_seen,
            candidate_files_seen=self.candidate_files_seen,
            candidate_bytes_seen=self.candidate_bytes_seen,
            skipped_hidden_entries=self.skipped_hidden_entries,
            skipped_symlink_entries=self.skipped_symlink_entries,
            skipped_other_entries=self.skipped_other_entries,
            inaccessible_entries=self.inaccessible_entries,
            truncated=self.truncated,
            candidates_truncated=self.candidates_truncated,
        )

    def begin_entry(self) -> bool:
        if self.entries_seen >= self.limits.max_entries:
            self.truncated = True
            return False
        self.entries_seen += 1
        return True

    def record_candidate(self, *, relative_path: str, extension: str, size_bytes: int) -> None:
        kind = classify_candidate_extension(extension)
        if kind is None:
            return
        self.candidate_files_seen += 1
        self.candidate_bytes_seen += size_bytes
        if len(self.candidates) < self.limits.max_candidates:
            self.candidates.append(
                LibraryScanCandidate(
                    relative_path=relative_path,
                    kind=kind,
                    extension=extension,
                    size_bytes=size_bytes,
                )
            )
        else:
            self.candidates_truncated = True


class LocalLibraryScanner:
    """Read-only deterministic filesystem scanner for one library root."""

    def preview(
        self,
        root: LibraryRoot,
        limits: LibraryScanLimits,
    ) -> LibraryFilesystemScanResult:
        if root.flavor != _native_path_flavor():
            raise _SCAN_UNAVAILABLE
        host_root = Path(root.path)
        try:
            if not host_root.exists():
                raise _SCAN_UNAVAILABLE
            if not host_root.is_dir():
                raise _SCAN_UNAVAILABLE
        except OSError:
            raise _SCAN_UNAVAILABLE from None

        state = _ScanState(limits=limits)
        try:
            self._traverse_directory(host_root, "", state)
        except LibraryScanUnavailableError:
            raise
        except OSError:
            raise _SCAN_UNAVAILABLE from None
        except Exception as exc:
            if isinstance(exc, (LibraryScanUnavailableError, LibraryScanFailedError)):
                raise
            raise _SCAN_FAILED from None

        return LibraryFilesystemScanResult(
            summary=state.summary(),
            candidates=tuple(state.candidates),
        )

    def _traverse_directory(self, directory: Path, relative_prefix: str, state: _ScanState) -> None:
        try:
            with os.scandir(directory) as scanner:
                entries = sorted(scanner, key=lambda entry: _entry_sort_key(entry.name))
        except OSError:
            if not relative_prefix:
                raise _SCAN_UNAVAILABLE from None
            state.inaccessible_entries += 1
            return

        for entry in entries:
            if not state.begin_entry():
                return

            name = entry.name
            if name.startswith("."):
                state.skipped_hidden_entries += 1
                continue

            try:
                if entry.is_symlink():
                    state.skipped_symlink_entries += 1
                    continue
                if entry.is_dir(follow_symlinks=False):
                    state.directories_seen += 1
                    child_relative = _join_relative_path(relative_prefix, name)
                    self._traverse_directory(Path(entry.path), child_relative, state)
                    if state.truncated:
                        return
                    continue
                if entry.is_file(follow_symlinks=False):
                    state.regular_files_seen += 1
                    try:
                        stat_result = entry.stat(follow_symlinks=False)
                        size_bytes = stat_result.st_size
                    except OSError:
                        state.inaccessible_entries += 1
                        continue
                    extension = Path(name).suffix.lower()
                    if extension:
                        child_relative = _join_relative_path(relative_prefix, name)
                        self._validate_output_relative_path(child_relative)
                        state.record_candidate(
                            relative_path=child_relative,
                            extension=extension,
                            size_bytes=size_bytes,
                        )
                    continue
                state.skipped_other_entries += 1
            except OSError:
                state.inaccessible_entries += 1

    @staticmethod
    def _validate_output_relative_path(relative_path: str) -> None:
        parsed = PurePosixPath(relative_path)
        if parsed.is_absolute() or any(part in (".", "..") for part in parsed.parts):
            raise _SCAN_FAILED
