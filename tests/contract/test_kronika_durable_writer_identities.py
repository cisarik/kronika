"""The durable-writer cut emits canonical identities and retains the historical ones.

Every durable writer constant switched to the canonical Kronika spelling in this
cut. Each former spelling is retained as a named historical constant and stays a
member of the accepted set, so artifacts written before the cut remain readable
and no stored row is hidden. This module pins, per identity:

- the writer spelling and the retained historical spelling as exact literals, so
  neither can silently drift;
- the accepted set, so the historical spelling cannot fall out silently;
- the collapse defect class, demonstrated for the builders that do not raise;
- the coupled cleanup recognizers, proven against former-spelled debris;
- the round trip of both spellings for the backup and sidecar artifact types.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path

import pytest


def _module(path: str) -> object:
    return importlib.import_module(path)


#: label, module, writer attribute, writer literal, historical attribute,
#: historical literal, accepted attribute.
DURABLE_IDENTITIES = (
    (
        "generic result schema",
        "kronika.domain.media_analysis_runs",
        "RESULT_SCHEMA_VERSION",
        "kronika-media-suggestion-result-v1",
        "COMPATIBLE_RESULT_SCHEMA_VERSION",
        "framenest-media-suggestion-result-v1",
        "ACCEPTED_RESULT_SCHEMA_VERSIONS",
    ),
    (
        "generic prompt version",
        "kronika.application.media_suggestion",
        "PROMPT_VERSION",
        "kronika-media-suggestion-v4",
        "COMPATIBLE_PROMPT_VERSION",
        "framenest-media-suggestion-v4",
        "ACCEPTED_PROMPT_VERSIONS",
    ),
    (
        "movie identification result schema",
        "kronika.domain.media_classification",
        "MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION",
        "kronika-movie-identification-result-v1",
        "COMPATIBLE_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSION",
        "framenest-movie-identification-result-v1",
        "ACCEPTED_MOVIE_IDENTIFICATION_RESULT_SCHEMA_VERSIONS",
    ),
    (
        "movie identification prompt version",
        "kronika.domain.media_classification",
        "MOVIE_IDENTIFICATION_PROMPT_VERSION",
        "kronika-movie-identification-prompt-v2",
        "COMPATIBLE_MOVIE_IDENTIFICATION_PROMPT_VERSION",
        "framenest-movie-identification-prompt-v2",
        "ACCEPTED_MOVIE_IDENTIFICATION_PROMPT_VERSIONS",
    ),
    (
        "sidecar format",
        "kronika.domain.media_sidecar",
        "SIDECAR_FORMAT",
        "kronika-media-sidecar",
        "COMPATIBLE_SIDECAR_FORMAT",
        "framenest-media-sidecar",
        "ACCEPTED_SIDECAR_FORMATS",
    ),
    (
        "sidecar filename suffix",
        "kronika.application.ports.media_sidecar_store",
        "SIDECAR_FILENAME_SUFFIX",
        ".kronika.json",
        "COMPATIBLE_SIDECAR_FILENAME_SUFFIX",
        ".framenest.json",
        "ACCEPTED_SIDECAR_FILENAME_SUFFIXES",
    ),
    (
        "backup application name",
        "kronika.infrastructure.persistence.catalog_backup",
        "APPLICATION_NAME",
        "kronika",
        "COMPATIBLE_APPLICATION_NAME",
        "framenest",
        "ACCEPTED_APPLICATION_NAMES",
    ),
    (
        "backup temporary prefix",
        "kronika.infrastructure.persistence.catalog_backup",
        "TEMP_PREFIX",
        ".kronika-backup-",
        "COMPATIBLE_TEMP_PREFIX",
        ".framenest-backup-",
        "ACCEPTED_TEMP_PREFIXES",
    ),
    (
        "offdevice marker name",
        "kronika.infrastructure.persistence.catalog_backup_offdevice",
        "MARKER_NAME",
        ".kronika-catalog-offdevice.json",
        "COMPATIBLE_MARKER_NAME",
        ".framenest-catalog-offdevice.json",
        "ACCEPTED_MARKER_NAMES",
    ),
    (
        "offdevice marker purpose",
        "kronika.infrastructure.persistence.catalog_backup_offdevice",
        "MARKER_PURPOSE",
        "kronika-catalog-offdevice",
        "COMPATIBLE_MARKER_PURPOSE",
        "framenest-catalog-offdevice",
        "ACCEPTED_MARKER_PURPOSES",
    ),
    (
        "offdevice stage prefix",
        "kronika.infrastructure.persistence.catalog_backup_offdevice",
        "STAGE_PREFIX",
        ".kronika-offdevice-stage-",
        "COMPATIBLE_STAGE_PREFIX",
        ".framenest-offdevice-stage-",
        "ACCEPTED_STAGE_PREFIXES",
    ),
    (
        "workstation marker name",
        "kronika.infrastructure.persistence.catalog_backup_workstation",
        "MARKER_NAME",
        ".kronika-workstation-snapshot-store.json",
        "COMPATIBLE_MARKER_NAME",
        ".framenest-workstation-snapshot-store.json",
        "ACCEPTED_MARKER_NAMES",
    ),
    (
        "workstation marker purpose",
        "kronika.infrastructure.persistence.catalog_backup_workstation",
        "MARKER_PURPOSE",
        "kronika-workstation-snapshot-store",
        "COMPATIBLE_MARKER_PURPOSE",
        "framenest-workstation-snapshot-store",
        "ACCEPTED_MARKER_PURPOSES",
    ),
    (
        "workstation stage prefix",
        "kronika.infrastructure.persistence.catalog_backup_workstation",
        "STAGE_PREFIX",
        ".kronika-pull-stage-",
        "COMPATIBLE_STAGE_PREFIX",
        ".framenest-pull-stage-",
        "ACCEPTED_STAGE_PREFIXES",
    ),
    (
        "workstation snapshot purpose",
        "kronika.infrastructure.persistence.catalog_backup_workstation",
        "SNAPSHOT_PURPOSE",
        "kronika-workstation-catalog-snapshot",
        "COMPATIBLE_SNAPSHOT_PURPOSE",
        "framenest-workstation-catalog-snapshot",
        "ACCEPTED_SNAPSHOT_PURPOSES",
    ),
    (
        "workstation transfer protocol",
        "kronika.infrastructure.persistence.catalog_backup_workstation",
        "TRANSFER_PROTOCOL_NAME",
        "kronika-catalog-backup-export",
        "COMPATIBLE_TRANSFER_PROTOCOL_NAME",
        "framenest-catalog-backup-export",
        "ACCEPTED_TRANSFER_PROTOCOL_NAMES",
    ),
)

_IDENTITY_IDS = [entry[0] for entry in DURABLE_IDENTITIES]


@pytest.mark.parametrize(
    ("label", "module_path", "writer_name", "writer_value", "historical_name",
     "historical_value", "accepted_name"),
    DURABLE_IDENTITIES,
    ids=_IDENTITY_IDS,
)
def test_each_writer_emits_the_canonical_spelling_and_retains_the_historical_one(
    label: str,
    module_path: str,
    writer_name: str,
    writer_value: str,
    historical_name: str,
    historical_value: str,
    accepted_name: str,
) -> None:
    module = _module(module_path)

    assert getattr(module, writer_name) == writer_value, label
    assert getattr(module, historical_name) == historical_value, label
    assert writer_value != historical_value, label
    accepted = getattr(module, accepted_name)
    assert writer_value in accepted, label
    assert historical_value in accepted, label


def test_the_raising_helper_still_refuses_a_collapsed_pair() -> None:
    from kronika.domain.analysis_identities import accepted_durable_identity

    with pytest.raises(ValueError):
        accepted_durable_identity("same", "same")
    with pytest.raises(ValueError):
        accepted_durable_identity("", "historical")
    with pytest.raises(ValueError):
        accepted_durable_identity("current", "")
    # A duplicate among the retained historical spellings is the same defect.
    with pytest.raises(ValueError):
        accepted_durable_identity("current", "historical", "historical")
    accepted = accepted_durable_identity("current", "historical", "older")
    assert accepted == frozenset({"current", "historical", "older"})


#: The raising identities build their accepted set through
#: ``accepted_durable_identity``; the silent ones build it as a literal. The
#: labels are the ones in ``DURABLE_IDENTITIES``.
RAISING_LABELS = frozenset(
    {
        "generic result schema",
        "generic prompt version",
        "movie identification result schema",
        "movie identification prompt version",
    }
)


def test_a_writer_only_change_would_collapse_each_silent_set() -> None:
    """Show what a literal builder becomes without the retained historical name.

    If the writer constant had merely been repointed at the canonical value and
    the accepted set left as a two-name literal over that same constant, the set
    would silently hold one element: no exception and no reader failure would
    point at the lost historical spelling. The raising identities are excluded,
    because their builder is the alarm that reports the same defect loudly.
    """
    for (
        label,
        module_path,
        _writer_name,
        writer_value,
        _historical_name,
        historical_value,
        accepted_name,
    ) in DURABLE_IDENTITIES:
        if label in RAISING_LABELS:
            continue
        collapsed = frozenset({writer_value, writer_value})
        assert len(collapsed) == 1, label
        assert historical_value not in collapsed, label
        accepted = getattr(_module(module_path), accepted_name)
        assert historical_value in accepted, label
        assert writer_value in accepted, label


# ---------------------------------------------------------------------------
# Coupled cleanup recognizers
# ---------------------------------------------------------------------------


def _backup_bundle_directory(tmp_path: Path, *, extra_name: str | None = None) -> Path:
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "manifest.json").write_text("{}", encoding="utf-8")
    (bundle / "catalog.sqlite3").write_bytes(b"x")
    if extra_name is not None:
        (bundle / extra_name).write_text("{}", encoding="utf-8")
    return bundle


@pytest.mark.parametrize(
    "extra_name",
    [".framenest-backup-old.deadbeef", ".kronika-backup-old.deadbeef"],
)
def test_backup_incomplete_recognizer_sees_both_temp_prefixes(
    tmp_path: Path, extra_name: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        BackupError,
        verify_catalog_backup,
    )

    bundle = _backup_bundle_directory(tmp_path, extra_name=extra_name)

    with pytest.raises(BackupError) as excinfo:
        verify_catalog_backup(bundle)

    assert excinfo.value.error_code == "INCOMPLETE_BUNDLE"


def test_backup_incomplete_recognizer_still_rejects_an_unknown_hidden_name(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        BackupError,
        verify_catalog_backup,
    )

    bundle = _backup_bundle_directory(tmp_path, extra_name=".someone-else-staging")

    with pytest.raises(BackupError) as excinfo:
        verify_catalog_backup(bundle)

    assert excinfo.value.error_code == "UNEXPECTED_BUNDLE_STATE"


@pytest.mark.parametrize(
    "owned_name", [".framenest-backup-old.deadbeef", ".kronika-backup-old.deadbeef"]
)
def test_backup_owned_temp_cleanup_removes_both_prefixes(
    tmp_path: Path, owned_name: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        _remove_owned_temp_bundle,
    )

    owned = tmp_path / owned_name
    owned.mkdir()
    (owned / "manifest.json").write_text("{}", encoding="utf-8")
    foreign = tmp_path / "operator-owned"
    foreign.mkdir()

    _remove_owned_temp_bundle(owned)
    _remove_owned_temp_bundle(foreign)

    assert not owned.exists()
    assert foreign.is_dir()


@pytest.mark.parametrize(
    "extra_name",
    [".framenest-backup-old.deadbeef", ".kronika-backup-old.deadbeef"],
)
def test_backup_ops_completeness_recognizer_sees_both_temp_prefixes(
    tmp_path: Path, extra_name: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        _bundle_looks_complete,
    )

    bundle = _backup_bundle_directory(tmp_path, extra_name=extra_name)

    assert _bundle_looks_complete(bundle) is False


def test_backup_ops_completeness_recognizer_still_accepts_a_clean_bundle(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_ops import (
        _bundle_looks_complete,
    )

    assert _bundle_looks_complete(_backup_bundle_directory(tmp_path)) is True


@pytest.mark.parametrize(
    "stage_name",
    [".framenest-offdevice-stage-deadbeef", ".kronika-offdevice-stage-deadbeef"],
)
def test_offdevice_stage_cleanup_recognises_both_prefixes(
    tmp_path: Path, stage_name: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        _cleanup_owned_stage,
    )

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    stage = bundles / stage_name
    stage.mkdir()
    (stage / "manifest.json").write_text("{}", encoding="utf-8")
    (stage / "catalog.sqlite3").write_bytes(b"x")

    _cleanup_owned_stage(stage, bundles)

    assert not stage.exists()


def test_offdevice_stage_cleanup_still_refuses_a_foreign_name(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_offdevice import (
        OffdeviceError,
        _cleanup_owned_stage,
    )

    bundles = tmp_path / "bundles"
    bundles.mkdir()
    foreign = bundles / "operator-owned"
    foreign.mkdir()

    with pytest.raises(OffdeviceError) as excinfo:
        _cleanup_owned_stage(foreign, bundles)

    assert excinfo.value.error_code == "OFFDEVICE_STAGE_UNSAFE"


@pytest.mark.parametrize(
    "stage_name",
    [".framenest-pull-stage-deadbeef", ".kronika-pull-stage-deadbeef"],
)
def test_workstation_stage_cleanup_recognises_both_prefixes(
    tmp_path: Path, stage_name: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        _cleanup_owned_stage,
    )

    snapshots = tmp_path / "snapshots"
    snapshots.mkdir()
    stage = snapshots / stage_name
    stage.mkdir()
    (stage / "snapshot.json").write_text("{}", encoding="utf-8")

    _cleanup_owned_stage(stage, snapshots)

    assert not stage.exists()


# ---------------------------------------------------------------------------
# The workstation snapshot envelope protocol
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
        geteuid=os.geteuid,
    )


def _workstation_store(tmp_path: Path) -> tuple[Path, Path]:
    mount = tmp_path / "mnt"
    mount.mkdir(parents=True, exist_ok=True)
    store = mount / "backups"
    store.mkdir(mode=0o700)
    os.chmod(store, 0o700)
    return mount, store


def test_workstation_marker_writer_emits_the_canonical_identity(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        MARKER_NAME,
        MARKER_PURPOSE,
        init_workstation_store,
    )

    mount, store = _workstation_store(tmp_path)
    result = init_workstation_store(
        store_root=store,
        mount_root=mount,
        hooks=_workstation_hooks(mount),
    )

    assert result.created is True
    assert MARKER_NAME == ".kronika-workstation-snapshot-store.json"
    marker = json.loads((store / MARKER_NAME).read_text(encoding="utf-8"))
    assert marker["purpose"] == MARKER_PURPOSE == "kronika-workstation-snapshot-store"


def _snapshot_envelope_payload(*, purpose: str, protocol: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "purpose": purpose,
        "accepted_at_utc": "2026-10-06T00:00:00Z",
        "transfer_protocol": protocol,
        "transfer_protocol_version": 1,
        "original_bundle_id": "auto-20261006T000000Z-deadbeef",
        "manifest_sha256": "a" * 64,
        "manifest_size_bytes": 1,
        "catalog_sha256": "b" * 64,
        "catalog_size_bytes": 1,
        "alembic_revision": "0035",
        "bundle_verification": "verified",
        "disposable_restore_verification": "verified",
        "semantic": {},
    }


@pytest.mark.parametrize(
    ("purpose", "protocol"),
    [
        ("kronika-workstation-catalog-snapshot", "kronika-catalog-backup-export"),
        ("framenest-workstation-catalog-snapshot", "framenest-catalog-backup-export"),
    ],
)
def test_workstation_envelope_reader_accepts_both_identity_spellings(
    tmp_path: Path, purpose: str, protocol: str
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        _load_snapshot_envelope,
    )

    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(_snapshot_envelope_payload(purpose=purpose, protocol=protocol)),
        encoding="utf-8",
    )

    assert _load_snapshot_envelope(path)["transfer_protocol"] == protocol


def test_workstation_envelope_reader_still_rejects_an_unknown_protocol(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup_workstation import (
        WorkstationError,
        _load_snapshot_envelope,
    )

    path = tmp_path / "snapshot.json"
    path.write_text(
        json.dumps(
            _snapshot_envelope_payload(
                purpose="kronika-workstation-catalog-snapshot",
                protocol="someone-else-export",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(WorkstationError) as excinfo:
        _load_snapshot_envelope(path)

    assert excinfo.value.error_code == "WORKSTATION_SNAPSHOT_ENVELOPE_PROTOCOL_MISMATCH"


# ---------------------------------------------------------------------------
# Round trips per artifact type, both spellings
# ---------------------------------------------------------------------------


def _migrated_database(path: Path) -> Path:
    from kronika.configuration import KronikaSettings
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def test_backup_round_trip_in_both_spellings(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        create_catalog_backup,
        restore_catalog_backup,
        verify_catalog_backup,
    )

    database_path = _migrated_database(tmp_path / "source" / "catalog.sqlite3")
    bundle = tmp_path / "backup"
    created = create_catalog_backup(database_path, bundle)
    assert created.state == "created"
    manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["application"]["name"] == "kronika"
    assert verify_catalog_backup(bundle).state == "verified"

    # A historical checkpoint under the former spelling still verifies.
    manifest["application"]["name"] = "framenest"
    (bundle / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    assert verify_catalog_backup(bundle).state == "verified"

    destination = tmp_path / "restored" / "catalog.sqlite3"
    restored = restore_catalog_backup(bundle, destination)
    assert restored.state == "restored"
    assert destination.is_file()


def _minimal_sidecar_document():
    from kronika.domain.identities import LibraryId, MediaId, MediaLocationId
    from kronika.domain.media import MediaKind, MediaRelativePath
    from kronika.domain.media_classification import AcquisitionSource, ContentCategory
    from kronika.domain.media_sidecar import SidecarDocument, SidecarLocation

    return SidecarDocument(
        media_id=MediaId.from_string("12345678-1234-4234-9234-123456789abc"),
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
            location_id=MediaLocationId.from_string(
                "abcdefab-cdef-4abc-8def-abcdefabcdef"
            ),
            library_id=LibraryId.from_string("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            relative_path=MediaRelativePath("movie.mkv"),
        ),
    )


def test_sidecar_round_trip_in_both_spellings() -> None:
    from kronika.domain.media_sidecar import (
        COMPATIBLE_SIDECAR_FORMAT,
        decode_media_sidecar,
        encode_media_sidecar,
    )

    document = _minimal_sidecar_document()
    encoded = encode_media_sidecar(document)
    assert json.loads(encoded)["format"] == "kronika-media-sidecar"
    assert decode_media_sidecar(encoded) == document

    historical = encoded.replace(
        b'"format":"kronika-media-sidecar"', b'"format":"framenest-media-sidecar"'
    )
    assert COMPATIBLE_SIDECAR_FORMAT == "framenest-media-sidecar"
    assert decode_media_sidecar(historical) == document


def test_sidecar_store_writes_and_reads_both_filename_spellings(tmp_path: Path) -> None:
    from kronika.application.ports.media_sidecar_store import (
        COMPATIBLE_SIDECAR_FILENAME_SUFFIX,
        SIDECAR_FILENAME_SUFFIX,
    )
    from kronika.domain.libraries import LibraryPathFlavor, LibraryRoot
    from kronika.domain.media import MediaRelativePath
    from kronika.domain.media_sidecar import encode_media_sidecar
    from kronika.infrastructure.filesystem.media_sidecar import (
        FilesystemMediaSidecarStore,
    )

    document = _minimal_sidecar_document()
    payload = encode_media_sidecar(document)
    media_relative_path = MediaRelativePath("movie.mkv")
    root = LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(tmp_path))
    (tmp_path / "movie.mkv").write_bytes(b"not-real-media")
    store = FilesystemMediaSidecarStore()

    store.create_adjacent(root, media_relative_path, payload)
    canonical = tmp_path / f"movie.mkv{SIDECAR_FILENAME_SUFFIX}"
    assert canonical.is_file()
    assert SIDECAR_FILENAME_SUFFIX == ".kronika.json"

    canonical.unlink()
    historical = tmp_path / f"movie.mkv{COMPATIBLE_SIDECAR_FILENAME_SUFFIX}"
    historical.write_bytes(payload)
    observation = store.observe_adjacent(root, media_relative_path)
    assert observation.payload == payload
