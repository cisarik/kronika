"""Contract tests for ADR-0085 dual-spelling durable-artifact readers.

Every writer in this cut still emits the former spelling. Each test here writes a
durable artifact by hand under one accepted spelling, proves the reader accepts
it, and proves the writer constants are unchanged.
"""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

PRIMARY_APPLICATION_NAME = "kronika"
COMPATIBLE_APPLICATION_NAME = "framenest"
PRIMARY_SIDECAR_FORMAT = "kronika-media-sidecar"
COMPATIBLE_SIDECAR_FORMAT = "framenest-media-sidecar"
DESTINATION_ID = "0123456789abcdef0123456789abcdef"
STORE_ID = "fedcba9876543210fedcba9876543210"
MEDIA_ID_TEXT = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
LIBRARY_ID_TEXT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"


def _migrated_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


# ---------------------------------------------------------------------------
# Catalog backup manifest application name
# ---------------------------------------------------------------------------


def _backup_bundle(tmp_path: Path) -> Path:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup

    database_path = _migrated_database(tmp_path / "source" / "catalog.sqlite3")
    bundle = tmp_path / "backup"
    create_catalog_backup(database_path, bundle)
    return bundle


def _rewrite_manifest_name(bundle: Path, name: str) -> None:
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["application"]["name"] = name
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def test_backup_manifest_writer_keeps_the_former_application_name(tmp_path: Path) -> None:
    bundle = _backup_bundle(tmp_path)
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))

    assert manifest["application"]["name"] == COMPATIBLE_APPLICATION_NAME


def test_old_name_backup_manifest_still_verifies(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import verify_catalog_backup

    bundle = _backup_bundle(tmp_path)
    _rewrite_manifest_name(bundle, COMPATIBLE_APPLICATION_NAME)

    result = verify_catalog_backup(bundle)

    assert result.state == "verified"


def test_new_name_backup_manifest_also_verifies(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import verify_catalog_backup

    bundle = _backup_bundle(tmp_path)
    _rewrite_manifest_name(bundle, PRIMARY_APPLICATION_NAME)

    result = verify_catalog_backup(bundle)

    assert result.state == "verified"


def test_an_unrelated_application_name_still_fails_closed(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        BackupError,
        verify_catalog_backup,
    )

    bundle = _backup_bundle(tmp_path)
    _rewrite_manifest_name(bundle, "someone-else")

    with pytest.raises(BackupError) as excinfo:
        verify_catalog_backup(bundle)

    assert excinfo.value.error_code == "MANIFEST_MALFORMED"


def test_backup_manifest_keeps_its_closed_field_set(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        BackupError,
        verify_catalog_backup,
    )

    bundle = _backup_bundle(tmp_path)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["application"]["extra"] = "x"
    manifest_path.write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )

    with pytest.raises(BackupError) as excinfo:
        verify_catalog_backup(bundle)

    assert excinfo.value.error_code == "MANIFEST_MALFORMED"


# ---------------------------------------------------------------------------
# Media sidecar format and filename suffix
# ---------------------------------------------------------------------------


def test_sidecar_writer_keeps_the_former_format_and_suffix() -> None:
    from kronika.application.ports.media_sidecar_store import (
        SIDECAR_FILENAME_SUFFIX,
        sidecar_filename,
    )
    from kronika.domain.media import MediaRelativePath
    from kronika.domain.media_sidecar import SIDECAR_FORMAT

    assert SIDECAR_FORMAT == COMPATIBLE_SIDECAR_FORMAT
    assert SIDECAR_FILENAME_SUFFIX == ".framenest.json"
    assert sidecar_filename(MediaRelativePath("movie.mkv")).endswith(".framenest.json")


@pytest.mark.parametrize("spelling", [COMPATIBLE_SIDECAR_FORMAT, PRIMARY_SIDECAR_FORMAT])
def test_sidecar_reader_accepts_both_format_spellings(spelling: str) -> None:
    from kronika.domain.media_sidecar import decode_media_sidecar

    payload = _sample_sidecar_payload()
    payload["format"] = spelling

    decoded = decode_media_sidecar(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    )

    assert decoded.media_id.to_string() == MEDIA_ID_TEXT
    assert decoded.format == COMPATIBLE_SIDECAR_FORMAT


def test_sidecar_reader_still_rejects_an_unknown_format() -> None:
    from kronika.domain.media_sidecar import (
        FrameNestMediaSidecarError,
        decode_media_sidecar,
    )

    payload = _sample_sidecar_payload()
    payload["format"] = "someone-else-sidecar"

    with pytest.raises(FrameNestMediaSidecarError) as excinfo:
        decode_media_sidecar(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
        )

    assert excinfo.value.error_code == "SIDECAR_UNSUPPORTED"


def test_sidecar_store_observes_both_filename_spellings(tmp_path: Path) -> None:
    from kronika.application.ports.media_sidecar_store import (
        COMPATIBLE_SIDECAR_FILENAME_SUFFIX,
        SIDECAR_FILENAME_SUFFIX,
        sidecar_filename,
    )
    from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
    from kronika.domain.media import MediaRelativePath
    from kronika.infrastructure.filesystem.media_sidecar import (
        FilesystemMediaSidecarStore,
    )

    assert SIDECAR_FILENAME_SUFFIX == ".framenest.json"
    assert COMPATIBLE_SIDECAR_FILENAME_SUFFIX == ".kronika.json"

    media_relative_path = MediaRelativePath("dual-read.mkv")
    payload = encode_sample_sidecar()
    store = FilesystemMediaSidecarStore()
    root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(tmp_path))
    (tmp_path / "dual-read.mkv").write_bytes(b"not-real-media")

    store.create_adjacent(root, media_relative_path, payload)
    assert store.observe_adjacent(root, media_relative_path).payload == payload

    (tmp_path / f"dual-read.mkv{COMPATIBLE_SIDECAR_FILENAME_SUFFIX}").write_bytes(payload)
    assert store.observe_adjacent(root, media_relative_path).payload == payload

    assert sidecar_filename(media_relative_path).endswith(SIDECAR_FILENAME_SUFFIX)


def test_sidecar_store_reports_missing_when_no_accepted_name_exists(tmp_path: Path) -> None:
    from kronika.application.ports.media_sidecar_store import SidecarTargetKind
    from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
    from kronika.domain.media import MediaRelativePath
    from kronika.infrastructure.filesystem.media_sidecar import (
        FilesystemMediaSidecarStore,
    )

    (tmp_path / "absent.mkv").write_bytes(b"not-real-media")
    observation = FilesystemMediaSidecarStore().observe_adjacent(
        LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(tmp_path)),
        MediaRelativePath("absent.mkv"),
    )

    assert observation.kind is SidecarTargetKind.MISSING


def encode_sample_sidecar() -> bytes:
    from kronika.domain.identities import LibraryId, MediaId, MediaLocationId
    from kronika.domain.media import MediaKind, MediaRelativePath
    from kronika.domain.media_classification import AcquisitionSource, ContentCategory
    from kronika.domain.media_sidecar import (
        SidecarDocument,
        SidecarLocation,
        encode_media_sidecar,
    )

    document = SidecarDocument(
        media_id=MediaId.from_string(MEDIA_ID_TEXT),
        media_kind=MediaKind.VIDEO,
        display_title=None,
        description=None,
        tag_keys=(),
        tag_definitions=(),
        content_category=ContentCategory.GENERAL,
        acquisition_source=AcquisitionSource.UNKNOWN,
        genre_keys=(),
        creator_attribution_kind=None,
        creator_stable_id=None,
        creator_handle=None,
        creator_display_name=None,
        processed=None,
        created_at_ms=None,
        updated_at_ms=None,
        location=SidecarLocation(
            location_id=MediaLocationId.from_string(LOCATION_ID_TEXT),
            library_id=LibraryId.from_string(LIBRARY_ID_TEXT),
            relative_path=MediaRelativePath("dual-read.mkv"),
        ),
    )
    return encode_media_sidecar(document)


def _sample_sidecar_payload() -> dict:
    return json.loads(encode_sample_sidecar().decode("utf-8"))


# ---------------------------------------------------------------------------
# Off-device destination marker
# ---------------------------------------------------------------------------


def _test_rename_noreplace(source: Path, destination: Path) -> None:
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(errno.EEXIST, os.strerror(errno.EEXIST), str(destination))
    os.rename(source, destination)


def _offdevice_hooks(destination_root: Path, *, is_mountpoint: bool = True):
    from kronika.infrastructure.persistence.catalog_backup_offdevice import OffdeviceOsHooks

    resolved = destination_root.resolve()

    def _device_id(path: Path) -> int:
        target = Path(path).resolve()
        if target == resolved:
            return 101
        return 202

    return OffdeviceOsHooks(
        is_mountpoint=lambda path: is_mountpoint and Path(path).resolve() == resolved,
        device_id=_device_id,
        rename_noreplace=_test_rename_noreplace,
        fsync=os.fsync,
        marker_uid_allowed=lambda uid: uid == os.getuid(),
        bundles_uid_allowed=lambda uid: uid == os.getuid(),
    )


def _offdevice_root(tmp_path: Path, *, marker_name: str, purpose: str) -> Path:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import BUNDLES_DIRNAME

    root = tmp_path / f"offdevice-{marker_name}"
    root.mkdir()
    marker = root / marker_name
    marker.write_text(
        json.dumps({"schema_version": 1, "purpose": purpose, "destination_id": DESTINATION_ID}),
        encoding="utf-8",
    )
    os.chmod(marker, 0o644)
    bundles = root / BUNDLES_DIRNAME
    bundles.mkdir(mode=0o700)
    os.chmod(bundles, 0o700)
    return root


def test_offdevice_marker_writer_keeps_the_former_spelling() -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        COMPATIBLE_MARKER_NAME,
        COMPATIBLE_MARKER_PURPOSE,
        MARKER_NAME,
        MARKER_PURPOSE,
    )

    assert MARKER_NAME == ".framenest-catalog-offdevice.json"
    assert MARKER_PURPOSE == "framenest-catalog-offdevice"
    assert COMPATIBLE_MARKER_NAME == ".kronika-catalog-offdevice.json"
    assert COMPATIBLE_MARKER_PURPOSE == "kronika-catalog-offdevice"


@pytest.mark.parametrize(
    ("marker_name", "purpose"),
    [
        (".framenest-catalog-offdevice.json", "framenest-catalog-offdevice"),
        (".kronika-catalog-offdevice.json", "kronika-catalog-offdevice"),
    ],
)
def test_offdevice_marker_reader_accepts_both_spellings(
    tmp_path: Path,
    marker_name: str,
    purpose: str,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        validate_offdevice_destination,
    )

    root = _offdevice_root(tmp_path, marker_name=marker_name, purpose=purpose)
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()

    validated = validate_offdevice_destination(
        destination_root=root,
        configured_destination_id=DESTINATION_ID,
        local_backup_root=backup_root,
        hooks=_offdevice_hooks(root),
    )

    assert validated.destination_id == DESTINATION_ID


def test_offdevice_marker_reader_still_rejects_an_unknown_purpose(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _offdevice_root(
        tmp_path,
        marker_name=".framenest-catalog-offdevice.json",
        purpose="someone-else",
    )
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()

    with pytest.raises(OffdeviceError) as excinfo:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_offdevice_hooks(root),
        )

    assert excinfo.value.error_code == "OFFDEVICE_MARKER_PURPOSE_MISMATCH"


def test_offdevice_marker_reader_fails_closed_on_an_unsafe_accepted_name(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        validate_offdevice_destination,
    )

    root = _offdevice_root(
        tmp_path,
        marker_name=".framenest-catalog-offdevice.json",
        purpose="framenest-catalog-offdevice",
    )
    (root / ".framenest-catalog-offdevice.json").unlink()
    (root / ".framenest-catalog-offdevice.json").symlink_to(tmp_path / "elsewhere.json")
    backup_root = tmp_path / "catalog-backups"
    backup_root.mkdir()

    with pytest.raises(OffdeviceError) as excinfo:
        validate_offdevice_destination(
            destination_root=root,
            configured_destination_id=DESTINATION_ID,
            local_backup_root=backup_root,
            hooks=_offdevice_hooks(root),
        )

    assert excinfo.value.error_code == "OFFDEVICE_MARKER_INVALID"


# ---------------------------------------------------------------------------
# Workstation snapshot store marker
# ---------------------------------------------------------------------------


def _workstation_hooks(mount_root: Path):
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationOsHooks,
    )

    resolved = mount_root.resolve()

    def _device_id(path: Path) -> int:
        target = Path(path).resolve()
        if target == resolved or resolved in target.parents:
            return 42
        return 7

    return WorkstationOsHooks(
        is_mountpoint=lambda path: Path(path).resolve() == resolved,
        device_id=_device_id,
        rename_noreplace=_test_rename_noreplace,
        geteuid=os.geteuid,
    )


def _workstation_mount(tmp_path: Path) -> tuple[Path, Path]:
    mount = tmp_path / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    store = mount / "framenest_backups"
    store.mkdir(mode=0o700)
    os.chmod(store, 0o700)
    return mount, store


def test_workstation_marker_writer_keeps_the_former_spelling() -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        COMPATIBLE_MARKER_NAME,
        COMPATIBLE_MARKER_PURPOSE,
        COMPATIBLE_SNAPSHOT_PURPOSE,
        MARKER_NAME,
        MARKER_PURPOSE,
        SNAPSHOT_PURPOSE,
    )

    assert MARKER_NAME == ".framenest-workstation-snapshot-store.json"
    assert MARKER_PURPOSE == "framenest-workstation-snapshot-store"
    assert SNAPSHOT_PURPOSE == "framenest-workstation-catalog-snapshot"
    assert COMPATIBLE_MARKER_NAME == ".kronika-workstation-snapshot-store.json"
    assert COMPATIBLE_MARKER_PURPOSE == "kronika-workstation-snapshot-store"
    assert COMPATIBLE_SNAPSHOT_PURPOSE == "kronika-workstation-catalog-snapshot"


@pytest.mark.parametrize(
    ("marker_name", "purpose"),
    [
        (".framenest-workstation-snapshot-store.json", "framenest-workstation-snapshot-store"),
        (".kronika-workstation-snapshot-store.json", "kronika-workstation-snapshot-store"),
    ],
)
def test_workstation_marker_reader_accepts_both_spellings(
    tmp_path: Path,
    marker_name: str,
    purpose: str,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        validate_workstation_store,
    )

    mount, store = _workstation_mount(tmp_path)
    marker = store / marker_name
    marker.write_text(
        json.dumps({"schema_version": 1, "purpose": purpose, "store_id": STORE_ID}),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    (store / "snapshots").mkdir(mode=0o700)
    os.chmod(store / "snapshots", 0o700)

    validated = validate_workstation_store(
        store_root=store,
        mount_root=mount,
        expected_store_id=STORE_ID,
        hooks=_workstation_hooks(mount),
    )

    assert validated.store_id == STORE_ID


def test_workstation_marker_reader_still_rejects_an_unknown_purpose(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        validate_workstation_store,
    )

    mount, store = _workstation_mount(tmp_path)
    marker = store / ".framenest-workstation-snapshot-store.json"
    marker.write_text(
        json.dumps({"schema_version": 1, "purpose": "someone-else", "store_id": STORE_ID}),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    (store / "snapshots").mkdir(mode=0o700)
    os.chmod(store / "snapshots", 0o700)

    with pytest.raises(WorkstationError) as excinfo:
        validate_workstation_store(
            store_root=store,
            mount_root=mount,
            expected_store_id=None,
            hooks=_workstation_hooks(mount),
        )

    assert excinfo.value.error_code == "WORKSTATION_MARKER_PURPOSE_MISMATCH"


def test_workstation_store_init_accepts_a_marker_under_the_new_spelling(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        init_workstation_store,
    )

    mount, store = _workstation_mount(tmp_path)
    marker = store / ".kronika-workstation-snapshot-store.json"
    marker.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "purpose": "kronika-workstation-snapshot-store",
                "store_id": STORE_ID,
            }
        ),
        encoding="utf-8",
    )
    os.chmod(marker, 0o600)
    (store / "snapshots").mkdir(mode=0o700)
    os.chmod(store / "snapshots", 0o700)
    (store / ".restore-verify").mkdir(mode=0o700)
    os.chmod(store / ".restore-verify", 0o700)

    result = init_workstation_store(
        store_root=store,
        mount_root=mount,
        hooks=_workstation_hooks(mount),
    )

    assert result.store_id == STORE_ID
    assert result.created is False
