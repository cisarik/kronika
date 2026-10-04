"""Unit tests for server-operator library onboarding and refresh workflow."""

from __future__ import annotations

from pathlib import Path

from framenest.application.library_scan import (
    LibraryFilesystemScanResult,
    LibraryScanCandidate,
    LibraryScanCandidateKind,
    LibraryScanLimits,
    LibraryScanSummary,
    default_scan_limits,
)
from framenest.application.library_workflow import (
    LibraryWorkflowDeviceSelectionRequiredError,
    LibraryWorkflowLibrarySelectionRequiredError,
    LibraryWorkflowNoLibraryError,
    LibraryWorkflowReservedRootConflictError,
    ServerLibraryWorkflow,
)
from framenest.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot, MediaId, MediaLocationId
from framenest.domain.media import LogicalMedia, MediaKind, MediaLocation, MediaLocationAvailability, MediaRelativePath


class _DeviceRepository:
    def __init__(self, devices: tuple[Device, ...] = ()) -> None:
        self.devices = list(devices)

    def add(self, device: Device) -> None:
        self.devices.append(device)

    def get(self, device_id: DeviceId) -> Device | None:
        return next((device for device in self.devices if device.id == device_id), None)

    def list_all(self) -> tuple[Device, ...]:
        return tuple(self.devices)


class _LibraryRepository:
    def __init__(self, libraries: tuple[Library, ...] = ()) -> None:
        self.libraries = list(libraries)

    def add(self, library: Library) -> None:
        self.libraries.append(library)

    def get(self, library_id: LibraryId) -> Library | None:
        return next((library for library in self.libraries if library.id == library_id), None)

    def list_all(self) -> tuple[Library, ...]:
        return tuple(self.libraries)


class _MediaRepository:
    def __init__(self) -> None:
        self.media: dict[MediaId, LogicalMedia] = {}
        self.locations: dict[tuple[LibraryId, MediaRelativePath], MediaLocation] = {}

    def add_media(self, media: LogicalMedia) -> None:
        self.media[media.id] = media

    def get_media(self, media_id: MediaId) -> LogicalMedia | None:
        return self.media.get(media_id)

    def list_media(self) -> tuple[LogicalMedia, ...]:
        return tuple(self.media.values())

    def add_location(self, location: MediaLocation) -> None:
        self.locations[(location.library_id, location.relative_path)] = location

    def add_media_with_location(self, media: LogicalMedia, location: MediaLocation) -> None:
        self.media[media.id] = media
        self.locations[(location.library_id, location.relative_path)] = location

    def get_location(self, location_id: MediaLocationId) -> MediaLocation | None:
        return next(
            (location for location in self.locations.values() if location.id == location_id),
            None,
        )

    def get_location_by_library_path(
        self,
        library_id: LibraryId,
        relative_path: MediaRelativePath,
    ) -> MediaLocation | None:
        return self.locations.get((library_id, relative_path))

    def list_locations_for_media(self, media_id: MediaId) -> tuple[MediaLocation, ...]:
        return tuple(location for location in self.locations.values() if location.media_id == media_id)

    def list_all_locations(self) -> tuple[MediaLocation, ...]:
        return tuple(self.locations.values())


class _Scanner:
    def __init__(self, result: LibraryFilesystemScanResult | None = None) -> None:
        self.result = result or _scan_result(("clip.mp4",))
        self.calls = 0

    def preview(self, root: LibraryRoot, limits: LibraryScanLimits) -> LibraryFilesystemScanResult:
        self.calls += 1
        return self.result


def _summary(candidate_count: int) -> LibraryScanSummary:
    return LibraryScanSummary(
        entries_seen=candidate_count,
        directories_seen=0,
        regular_files_seen=candidate_count,
        candidate_files_seen=candidate_count,
        candidate_bytes_seen=100 * candidate_count,
        skipped_hidden_entries=0,
        skipped_symlink_entries=0,
        skipped_other_entries=0,
        inaccessible_entries=0,
        truncated=False,
        candidates_truncated=False,
    )


def _scan_result(paths: tuple[str, ...]) -> LibraryFilesystemScanResult:
    return LibraryFilesystemScanResult(
        summary=_summary(len(paths)),
        candidates=tuple(
            LibraryScanCandidate(
                relative_path=path,
                kind=LibraryScanCandidateKind.GIF if path.endswith(".gif") else LibraryScanCandidateKind.VIDEO,
                extension=".gif" if path.endswith(".gif") else ".mp4",
                size_bytes=100,
            )
            for path in paths
        ),
    )


def _device(name: str = "Device") -> Device:
    return Device(id=DeviceId.new(), display_name=name)


def _root(path: str = "/tmp/library") -> LibraryRoot:
    return LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=path)


def _library(root: LibraryRoot | None = None, device: Device | None = None) -> Library:
    selected_device = device or _device()
    return Library(
        id=LibraryId.new(),
        device_id=selected_device.id,
        display_name="Library",
        root=root or _root(),
    )


def _workflow(
    *,
    devices: tuple[Device, ...] = (),
    libraries: tuple[Library, ...] = (),
    scanner: _Scanner | None = None,
    media: _MediaRepository | None = None,
    reserved_roots: tuple[str, ...] = (),
) -> tuple[ServerLibraryWorkflow, _DeviceRepository, _LibraryRepository, _MediaRepository, _Scanner]:
    device_repository = _DeviceRepository(devices)
    library_repository = _LibraryRepository(libraries)
    media_repository = media or _MediaRepository()
    selected_scanner = scanner or _Scanner()
    return (
        ServerLibraryWorkflow(
            device_repository,
            library_repository,
            media_repository,
            selected_scanner,
            reserved_roots=tuple(Path(path) for path in reserved_roots),
        ),
        device_repository,
        library_repository,
        media_repository,
        selected_scanner,
    )


def test_status_is_read_only_and_scan_free() -> None:
    library = _library()
    media = _MediaRepository()
    location = MediaLocation(
        id=MediaLocationId.new(),
        media_id=MediaId.new(),
        library_id=library.id,
        relative_path=MediaRelativePath("clip.mp4"),
        availability=MediaLocationAvailability.AVAILABLE,
        observed_size_bytes=100,
        observed_mtime_ns=None,
        created_at_ms=1,
        updated_at_ms=1,
    )
    media.locations[(library.id, location.relative_path)] = location
    workflow, _, _, _, scanner = _workflow(libraries=(library,), scanner=_Scanner(), media=media)

    status = workflow.status()

    assert scanner.calls == 0
    assert status.library_count == 1
    assert status.total_location_count == 1
    assert status.available_location_count == 1


def test_declined_add_plan_leaves_all_state_unchanged_before_confirmation() -> None:
    workflow, devices, libraries, media, scanner = _workflow()

    plan = workflow.plan_add(root=_root(), display_name="Imported", limits=default_scan_limits())

    assert plan.device_to_create is not None
    assert plan.device_to_create.display_name == "Kronika Server"
    assert scanner.calls == 1
    assert devices.list_all() == ()
    assert libraries.list_all() == ()
    assert media.list_media() == ()
    assert media.list_all_locations() == ()


def test_confirmed_zero_device_add_creates_server_device_library_and_catalog() -> None:
    workflow, devices, libraries, media, _ = _workflow()
    plan = workflow.plan_add(root=_root(), display_name="Imported", limits=default_scan_limits())

    summary = workflow.confirm_add(plan)

    assert len(devices.list_all()) == 1
    assert devices.list_all()[0].display_name == "Kronika Server"
    assert len(libraries.list_all()) == 1
    assert len(media.list_media()) == 1
    assert len(media.list_all_locations()) == 1
    assert summary.imported_candidate_count == 1


def test_one_existing_device_is_reused_and_multiple_devices_require_selection() -> None:
    device = _device()
    workflow, devices, _, _, _ = _workflow(devices=(device,))

    plan = workflow.plan_add(root=_root(), display_name="Imported", limits=default_scan_limits())

    assert plan.device_to_create is None
    assert plan.library.device_id == device.id
    assert devices.list_all() == (device,)

    multi, _, _, _, _ = _workflow(devices=(device, _device("Other")))
    try:
        multi.plan_add(root=_root("/tmp/other"), display_name="Imported", limits=default_scan_limits())
    except LibraryWorkflowDeviceSelectionRequiredError:
        pass
    else:
        raise AssertionError("multiple devices must require explicit selection")


def test_existing_canonical_root_is_reused_without_duplicate_library() -> None:
    existing = _library(root=_root())
    workflow, _, libraries, _, _ = _workflow(libraries=(existing,))

    plan = workflow.plan_add(root=existing.root, display_name="Different", limits=default_scan_limits())
    summary = workflow.confirm_add(plan)

    assert plan.existing_root is True
    assert plan.library.id == existing.id
    assert libraries.list_all() == (existing,)
    assert summary.library.id == existing.id


def test_reserved_quarantine_root_overlap_rejects_future_library_registration() -> None:
    workflow, _, _, _, scanner = _workflow(reserved_roots=("/tmp/library/quarantine",))

    for root in (
        _root("/tmp/library"),
        _root("/tmp/library/quarantine"),
        _root("/tmp/library/quarantine/nested"),
    ):
        try:
            workflow.plan_add(root=root, display_name="Imported", limits=default_scan_limits())
        except LibraryWorkflowReservedRootConflictError:
            pass
        else:
            raise AssertionError("reserved root overlap must be rejected")

    assert scanner.calls == 0


def test_non_overlapping_reserved_root_preserves_valid_registration() -> None:
    workflow, _, _, _, scanner = _workflow(reserved_roots=("/tmp/quarantine",))

    plan = workflow.plan_add(
        root=_root("/tmp/library"),
        display_name="Imported",
        limits=default_scan_limits(),
    )

    assert plan.library.root.path == "/tmp/library"
    assert scanner.calls == 1


def test_repeating_add_and_refresh_create_no_duplicates_and_refresh_imports_only_new_candidate() -> None:
    scanner = _Scanner(_scan_result(("clip.mp4",)))
    workflow, _, _, media, _ = _workflow(scanner=scanner)
    plan = workflow.plan_add(root=_root(), display_name="Imported", limits=default_scan_limits())

    first = workflow.confirm_add(plan)
    repeat_plan = workflow.plan_add(root=_root(), display_name="Imported", limits=default_scan_limits())
    second = workflow.confirm_add(repeat_plan)

    assert first.imported_candidate_count == 1
    assert second.imported_candidate_count == 0
    assert len(media.list_media()) == 1
    assert len(media.list_all_locations()) == 1

    scanner.result = _scan_result(("clip.mp4", "new.gif"))
    refresh = workflow.plan_refresh(limits=default_scan_limits())
    refreshed = workflow.confirm_refresh(refresh)

    assert refreshed.imported_candidate_count == 1
    assert refreshed.existing_candidate_count == 1
    assert len(media.list_media()) == 2
    assert len(media.list_all_locations()) == 2


def test_refresh_selection_no_library_and_metadata_preservation_boundaries() -> None:
    workflow, _, _, _, _ = _workflow()
    try:
        workflow.plan_refresh(limits=default_scan_limits())
    except LibraryWorkflowNoLibraryError:
        pass
    else:
        raise AssertionError("refresh must fail without a library")

    first = _library(root=_root("/tmp/one"))
    second = _library(root=_root("/tmp/two"))
    multi, _, _, _, _ = _workflow(libraries=(first, second))
    try:
        multi.plan_refresh(limits=default_scan_limits())
    except LibraryWorkflowLibrarySelectionRequiredError:
        pass
    else:
        raise AssertionError("multiple libraries must require explicit selection")

    scanner = _Scanner(_scan_result(("kept.mp4",)))
    one, _, _, media, _ = _workflow(libraries=(first,), scanner=scanner)
    one.confirm_refresh(one.plan_refresh(limits=default_scan_limits()))
    existing_media = media.list_media()[0]
    scanner.result = _scan_result(())
    one.confirm_refresh(one.plan_refresh(limits=default_scan_limits()))

    assert media.list_media() == (existing_media,)
    assert media.list_all_locations()[0].availability == MediaLocationAvailability.AVAILABLE
