"""Application workflow for server-operator library onboarding and refresh."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from framenest.application.library_scan import (
    LibraryScanLimits,
    LibraryScanPreviewResult,
    PreviewLibraryScan,
)
from framenest.application.media_import import ImportMediaFromScanCandidate, MediaImportResult
from framenest.application.ports.device_repository import DeviceRepository
from framenest.application.ports.library_repository import LibraryRepository
from framenest.application.ports.media_repository import MediaRepository
from framenest.domain import Device, DeviceId, FrameNestDeviceError, FrameNestLibraryError, Library, LibraryId, LibraryRoot
from framenest.domain.media import MediaLocationAvailability, MediaRelativePath
from framenest.application.root_paths import roots_overlap

SERVER_DEVICE_DISPLAY_NAME = "Kronika Server"


class LibraryWorkflowError(RuntimeError):
    """Base sanitized workflow error."""


class LibraryWorkflowInvalidInputError(LibraryWorkflowError):
    """Raised when workflow input is invalid."""


class LibraryWorkflowReservedRootConflictError(LibraryWorkflowError):
    """Raised when a requested library root overlaps a reserved server root."""


class LibraryWorkflowDeviceSelectionRequiredError(LibraryWorkflowError):
    """Raised when multiple devices exist and none was selected."""


class LibraryWorkflowDeviceNotFoundError(LibraryWorkflowError):
    """Raised when the selected device is not registered."""


class LibraryWorkflowLibrarySelectionRequiredError(LibraryWorkflowError):
    """Raised when multiple libraries exist and none was selected."""


class LibraryWorkflowLibraryNotFoundError(LibraryWorkflowError):
    """Raised when the selected library is not registered."""


class LibraryWorkflowNoLibraryError(LibraryWorkflowError):
    """Raised when refresh is requested without any registered library."""


@dataclass(frozen=True, slots=True)
class LibraryWorkflowStatus:
    """Read-only status for the server-operator library workflow."""

    device_count: int
    library_count: int
    libraries: tuple[Library, ...]
    total_location_count: int
    available_location_count: int


@dataclass(frozen=True, slots=True)
class LibraryAddPlan:
    """Read-only add plan produced before confirmation."""

    library: Library
    preview: LibraryScanPreviewResult
    device_to_create: Device | None
    existing_root: bool


@dataclass(frozen=True, slots=True)
class LibraryRefreshPlan:
    """Read-only refresh plan produced before confirmation."""

    library: Library
    preview: LibraryScanPreviewResult


@dataclass(frozen=True, slots=True)
class LibraryImportSummary:
    """Durable import summary after confirmed operation."""

    library: Library
    scanned_candidate_count: int
    imported_candidate_count: int
    existing_candidate_count: int


class ServerLibraryWorkflow:
    """Coordinate local library add, refresh, and status without UI concerns."""

    def __init__(
        self,
        device_repository: DeviceRepository,
        library_repository: LibraryRepository,
        media_repository: MediaRepository,
        scanner: object,
        reserved_roots: tuple[Path, ...] = (),
    ) -> None:
        self._device_repository = device_repository
        self._library_repository = library_repository
        self._media_repository = media_repository
        self._scanner = scanner
        self._reserved_roots = reserved_roots

    def status(self) -> LibraryWorkflowStatus:
        devices = self._device_repository.list_all()
        libraries = self._library_repository.list_all()
        locations = self._media_repository.list_all_locations()
        available_count = sum(
            1
            for location in locations
            if location.availability == MediaLocationAvailability.AVAILABLE
        )
        return LibraryWorkflowStatus(
            device_count=len(devices),
            library_count=len(libraries),
            libraries=libraries,
            total_location_count=len(locations),
            available_location_count=available_count,
        )

    def plan_add(
        self,
        *,
        root: LibraryRoot,
        display_name: str,
        limits: LibraryScanLimits,
        device_id: DeviceId | None = None,
    ) -> LibraryAddPlan:
        existing = self._find_library_by_root(root)
        if existing is not None:
            return LibraryAddPlan(
                library=existing,
                preview=self._preview(existing.id, limits),
                device_to_create=None,
                existing_root=True,
            )

        device, device_to_create = self._resolve_device(device_id)
        try:
            library = Library(
                id=LibraryId.new(),
                device_id=device.id,
                display_name=display_name,
                root=root,
            )
        except FrameNestLibraryError:
            raise LibraryWorkflowInvalidInputError() from None

        preview = self._preview_root_before_registration(library, limits)
        return LibraryAddPlan(
            library=library,
            preview=preview,
            device_to_create=device_to_create,
            existing_root=False,
        )

    def confirm_add(self, plan: LibraryAddPlan) -> LibraryImportSummary:
        if plan.device_to_create is not None:
            self._device_repository.add(plan.device_to_create)
        if not plan.existing_root:
            self._library_repository.add(plan.library)
        return self._import_preview_candidates(plan.library, plan.preview)

    def plan_refresh(
        self,
        *,
        limits: LibraryScanLimits,
        library_id: LibraryId | None = None,
    ) -> LibraryRefreshPlan:
        library = self._resolve_library(library_id)
        return LibraryRefreshPlan(
            library=library,
            preview=self._preview(library.id, limits),
        )

    def confirm_refresh(self, plan: LibraryRefreshPlan) -> LibraryImportSummary:
        return self._import_preview_candidates(plan.library, plan.preview)

    def _resolve_device(self, device_id: DeviceId | None) -> tuple[Device, Device | None]:
        devices = self._device_repository.list_all()
        if device_id is not None:
            selected = self._device_repository.get(device_id)
            if selected is None:
                raise LibraryWorkflowDeviceNotFoundError()
            return selected, None
        if len(devices) == 0:
            try:
                device = Device(id=DeviceId.new(), display_name=SERVER_DEVICE_DISPLAY_NAME)
            except FrameNestDeviceError:
                raise LibraryWorkflowInvalidInputError() from None
            return device, device
        if len(devices) == 1:
            return devices[0], None
        raise LibraryWorkflowDeviceSelectionRequiredError()

    def _resolve_library(self, library_id: LibraryId | None) -> Library:
        if library_id is not None:
            library = self._library_repository.get(library_id)
            if library is None:
                raise LibraryWorkflowLibraryNotFoundError()
            return library
        libraries = self._library_repository.list_all()
        if len(libraries) == 0:
            raise LibraryWorkflowNoLibraryError()
        if len(libraries) == 1:
            return libraries[0]
        raise LibraryWorkflowLibrarySelectionRequiredError()

    def _find_library_by_root(self, root: LibraryRoot) -> Library | None:
        self._ensure_not_reserved_root(root)
        for library in self._library_repository.list_all():
            if library.root == root:
                return library
        return None

    def _ensure_not_reserved_root(self, root: LibraryRoot) -> None:
        for reserved in self._reserved_roots:
            if roots_overlap(Path(root.path), reserved):
                raise LibraryWorkflowReservedRootConflictError()

    def _preview(self, library_id: LibraryId, limits: LibraryScanLimits) -> LibraryScanPreviewResult:
        return PreviewLibraryScan(self._library_repository, self._scanner).execute(library_id, limits)

    def _preview_root_before_registration(
        self,
        library: Library,
        limits: LibraryScanLimits,
    ) -> LibraryScanPreviewResult:
        filesystem_result = self._scanner.preview(library.root, limits)
        return LibraryScanPreviewResult(
            library_id=library.id,
            limits=limits,
            summary=filesystem_result.summary,
            candidates=filesystem_result.candidates,
        )

    def _import_preview_candidates(
        self,
        library: Library,
        preview: LibraryScanPreviewResult,
    ) -> LibraryImportSummary:
        importer = ImportMediaFromScanCandidate(
            self._library_repository,
            self._media_repository,
            self._scanner,
        )
        results: list[MediaImportResult] = []
        for candidate in preview.candidates:
            results.append(
                importer.execute(
                    library.id,
                    MediaRelativePath(candidate.relative_path),
                    preview.limits,
                )
            )
        imported_count = sum(1 for result in results if result.created)
        return LibraryImportSummary(
            library=library,
            scanned_candidate_count=len(preview.candidates),
            imported_candidate_count=imported_count,
            existing_candidate_count=len(results) - imported_count,
        )
