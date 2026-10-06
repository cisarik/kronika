"""Unit tests for the catalog backup and recovery boundary."""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _migrated_database(path: Path) -> Path:
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def _manifest(bundle: Path) -> dict[str, object]:
    payload = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_create_catalog_backup_from_migrated_database(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup

    database_path = _migrated_database(tmp_path / "source" / "catalog.sqlite3")
    bundle = tmp_path / "backup"

    result = create_catalog_backup(database_path, bundle)

    assert result.state == "created"
    assert (bundle / "manifest.json").is_file()
    assert (bundle / "catalog.sqlite3").is_file()
    manifest = _manifest(bundle)
    assert manifest["schema_version"] == 1
    assert manifest["catalog"]["logical_name"] == "catalog.sqlite3"
    assert manifest["catalog"]["alembic_revision"] == "0035"
    assert manifest["catalog"]["size_bytes"] == (bundle / "catalog.sqlite3").stat().st_size
    assert manifest["catalog"]["sha256"] == result.catalog_sha256
    assert "source" not in json.dumps(manifest)
    assert str(database_path) not in json.dumps(manifest)


def test_create_uses_sqlite_snapshot_while_source_connection_is_open(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")
    with sqlite3.connect(database_path) as connection:
        connection.execute("SELECT 1")

        result = create_catalog_backup(database_path, tmp_path / "bundle")

    assert result.alembic_revision == "0035"


def test_create_does_not_mutate_source_database(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup, sha256_file

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")
    before = sha256_file(database_path)

    create_catalog_backup(database_path, tmp_path / "bundle")

    assert sha256_file(database_path) == before


def test_create_refuses_existing_output_and_unsafe_source(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")
    bundle = tmp_path / "bundle"
    bundle.mkdir()

    with pytest.raises(BackupError, match="output"):
        create_catalog_backup(database_path, bundle)

    symlink = tmp_path / "linked.sqlite3"
    symlink.symlink_to(database_path)
    with pytest.raises(BackupError, match="source"):
        create_catalog_backup(symlink, tmp_path / "other-bundle")

    with pytest.raises(BackupError, match="source"):
        create_catalog_backup(tmp_path, tmp_path / "directory-source")

    with pytest.raises(BackupError, match="parent"):
        create_catalog_backup(database_path, tmp_path / "missing" / "bundle")


def test_create_cleans_temporary_state_after_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")

    original_integrity = catalog._verify_sqlite_integrity
    calls = 0

    def fail_second_integrity(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackupError("integrity failed", error_code="INTEGRITY_FAILED")
        original_integrity(path)

    monkeypatch.setattr(catalog, "_verify_sqlite_integrity", fail_second_integrity)

    with pytest.raises(BackupError):
        catalog.create_catalog_backup(database_path, tmp_path / "bundle")

    assert not (tmp_path / "bundle").exists()
    assert [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith((".kronika-backup-", ".framenest-backup-"))
    ] == []


def test_create_refuses_output_created_after_absence_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")
    bundle = tmp_path / "bundle"
    original_mkdir = catalog.os.mkdir

    def racing_mkdir(path: Path | str, mode: int = 0o777, *, dir_fd: int | None = None) -> None:
        if Path(path) == bundle:
            if dir_fd is None:
                original_mkdir(path, mode)
                return original_mkdir(path, mode)
            original_mkdir(path, mode, dir_fd=dir_fd)
            return original_mkdir(path, mode, dir_fd=dir_fd)
        if dir_fd is None:
            return original_mkdir(path, mode)
        return original_mkdir(path, mode, dir_fd=dir_fd)

    monkeypatch.setattr(catalog.os, "mkdir", racing_mkdir)

    with pytest.raises(BackupError, match="output"):
        catalog.create_catalog_backup(database_path, bundle)

    assert bundle.is_dir()
    assert list(bundle.iterdir()) == []
    assert [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith((".kronika-backup-", ".framenest-backup-"))
    ] == []


def test_verify_accepts_intact_bundle_and_rejects_tampering(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)

    assert verify_catalog_backup(bundle).state == "verified"

    with (bundle / "catalog.sqlite3").open("r+b") as handle:
        first = handle.read(1)
        handle.seek(0)
        handle.write(b"1" if first != b"1" else b"2")

    with pytest.raises(BackupError, match="checksum"):
        verify_catalog_backup(bundle)


def test_verify_rejects_malformed_manifest_unsupported_version_and_symlink(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)

    manifest = _manifest(bundle)
    manifest["schema_version"] = 999
    (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(BackupError, match="manifest"):
        verify_catalog_backup(bundle)

    create_catalog_backup(_migrated_database(tmp_path / "second.sqlite3"), tmp_path / "second")
    (tmp_path / "second" / "manifest.json").unlink()
    (tmp_path / "second" / "manifest.json").symlink_to(bundle / "manifest.json")
    with pytest.raises(BackupError, match="manifest"):
        verify_catalog_backup(tmp_path / "second")


def test_verify_rejects_missing_catalog_catalog_symlink_and_incomplete_state(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    (bundle / "catalog.sqlite3").unlink()
    with pytest.raises(BackupError, match="catalog"):
        verify_catalog_backup(bundle)

    replacement = tmp_path / "replacement"
    create_catalog_backup(_migrated_database(tmp_path / "other.sqlite3"), replacement)
    (replacement / "catalog.sqlite3").unlink()
    (replacement / "catalog.sqlite3").symlink_to(tmp_path / "other.sqlite3")
    with pytest.raises(BackupError, match="catalog"):
        verify_catalog_backup(replacement)

    incomplete = tmp_path / "incomplete"
    create_catalog_backup(_migrated_database(tmp_path / "third.sqlite3"), incomplete)
    (incomplete / ".leftover.tmp").write_text("temporary", encoding="utf-8")
    with pytest.raises(BackupError, match="incomplete"):
        verify_catalog_backup(incomplete)


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("extra.txt", "file"),
        ("nested", "directory"),
        ("linked", "symlink"),
        (".env", "file"),
    ],
)
def test_verify_rejects_unexpected_bundle_entries(tmp_path: Path, name: str, kind: str) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    extra = bundle / name
    if kind == "file":
        extra.write_text("unexpected", encoding="utf-8")
    elif kind == "directory":
        extra.mkdir()
    else:
        extra.symlink_to(bundle / "catalog.sqlite3")

    with pytest.raises(BackupError, match="unexpected"):
        verify_catalog_backup(bundle)


def test_manifest_rejects_unexpected_secret_shaped_fields_and_excludes_non_catalog_state(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    manifest = _manifest(bundle)
    assert manifest["included_state"] == ["catalog_database"]
    assert manifest["excluded_state"] == [
        "gallery_preview_cache",
        "original_media",
        "secrets",
        "non_secret_ai_configuration",
    ]
    assert "FRAMENEST_DATABASE_PATH" not in json.dumps(manifest)

    manifest["token"] = "not-allowed"
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    with pytest.raises(BackupError, match="manifest"):
        verify_catalog_backup(bundle)


@pytest.mark.parametrize(
    ("mutator", "message"),
    [
        (lambda manifest: manifest.update({"created_at_utc": "2026-07-09 10:00:00Z"}), "manifest"),
        (lambda manifest: manifest["application"].update({"version": "../../0.1.0"}), "manifest"),
        (lambda manifest: manifest["algorithms"].update({"digest": "md5"}), "manifest"),
        (lambda manifest: manifest["catalog"].update({"size_bytes": 0}), "manifest"),
        (
            lambda manifest: manifest["catalog"].update({"size_bytes": 16 * 1024 * 1024 * 1024 * 1024 + 1}),
            "manifest",
        ),
        (lambda manifest: manifest["catalog"].update({"sha256": "A" * 64}), "manifest"),
        (lambda manifest: manifest["catalog"].update({"alembic_revision": "0008-extra"}), "manifest"),
        (lambda manifest: manifest["catalog"].update({"unexpected": "field"}), "manifest"),
    ],
)
def test_manifest_rejects_noncanonical_or_unsafe_metadata(
    tmp_path: Path,
    mutator: object,
    message: str,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    manifest = _manifest(bundle)
    assert callable(mutator)
    mutator(manifest)
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")

    with pytest.raises(BackupError, match=message):
        verify_catalog_backup(bundle)


def test_verify_rejects_revision_mismatch_and_corrupt_sqlite(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, verify_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    manifest = _manifest(bundle)
    catalog = manifest["catalog"]
    assert isinstance(catalog, dict)
    catalog["alembic_revision"] = "0006"
    (bundle / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    with pytest.raises(BackupError, match="revision"):
        verify_catalog_backup(bundle)

    corrupt = tmp_path / "corrupt"
    create_catalog_backup(_migrated_database(tmp_path / "other.sqlite3"), corrupt)
    (corrupt / "catalog.sqlite3").write_bytes(b"not sqlite")
    manifest = _manifest(corrupt)
    catalog = manifest["catalog"]
    assert isinstance(catalog, dict)
    catalog["size_bytes"] = len(b"not sqlite")
    catalog["sha256"] = "e2772141cdd3f05ee2b084eb0b741f2cd96aaca489005bfeb8cb0ce9782264e8"
    (corrupt / "manifest.json").write_text(json.dumps(manifest, sort_keys=True), encoding="utf-8")
    with pytest.raises(BackupError, match="SQLite"):
        verify_catalog_backup(corrupt)


def test_restore_verified_bundle_to_new_destination(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        create_catalog_backup,
        restore_catalog_backup,
        sha256_file,
    )

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    destination = tmp_path / "restored" / "catalog.sqlite3"

    result = restore_catalog_backup(bundle, destination)

    assert result.state == "restored"
    assert destination.is_file()
    assert sha256_file(destination) == sha256_file(bundle / "catalog.sqlite3")
    with sqlite3.connect(destination) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == ("0035",)
    siblings = [
        path
        for path in destination.parent.iterdir()
        if path.name.startswith(f".{destination.name}.") and path.name.endswith(".tmp")
    ]
    assert siblings == []
    assert list(destination.parent.iterdir()) == [destination]

def test_restore_refuses_existing_or_symlink_destination_and_leaves_bundle_read_only(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup, restore_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    manifest_before = (bundle / "manifest.json").read_bytes()
    catalog_before = (bundle / "catalog.sqlite3").read_bytes()

    destination = tmp_path / "catalog.sqlite3"
    destination.write_text("existing", encoding="utf-8")
    with pytest.raises(BackupError, match="destination"):
        restore_catalog_backup(bundle, destination)

    destination.unlink()
    destination.symlink_to(tmp_path / "target.sqlite3")
    with pytest.raises(BackupError, match="destination"):
        restore_catalog_backup(bundle, destination)

    assert (bundle / "manifest.json").read_bytes() == manifest_before
    assert (bundle / "catalog.sqlite3").read_bytes() == catalog_before


def test_restore_cleans_temporary_destination_after_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)

    original_integrity = catalog._verify_sqlite_integrity
    calls = 0

    def fail_second_integrity(path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise BackupError("integrity failed", error_code="INTEGRITY_FAILED")
        original_integrity(path)

    monkeypatch.setattr(catalog, "_verify_sqlite_integrity", fail_second_integrity)

    with pytest.raises(BackupError):
        catalog.restore_catalog_backup(bundle, tmp_path / "restored" / "catalog.sqlite3")

    restore_parent = tmp_path / "restored"
    assert not any(restore_parent.iterdir())


def test_restore_refuses_destination_created_after_absence_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    destination = tmp_path / "restored.sqlite3"
    original_link = catalog.os.link

    def racing_link(source: Path | str, target: Path | str, *args: object, **kwargs: object) -> None:
        if Path(target) == destination:
            destination.write_text("racer", encoding="utf-8")
        return original_link(source, target, *args, **kwargs)

    monkeypatch.setattr(catalog.os, "link", racing_link)

    with pytest.raises(BackupError, match="destination"):
        catalog.restore_catalog_backup(bundle, destination)

    assert destination.read_text(encoding="utf-8") == "racer"
    assert [path for path in tmp_path.iterdir() if path.name.startswith(".restored.sqlite3.") and path.name.endswith(".tmp")] == []


def test_create_verify_and_restore_handle_sqlite_paths_with_uri_reserved_characters(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup, restore_catalog_backup, verify_catalog_backup

    database_path = _migrated_database(tmp_path / "space ? # percent% literal%2F" / "catalog ?.sqlite3")
    bundle = tmp_path / "backup ? # percent%"
    destination = tmp_path / "restore ? # percent%" / "restored%2F.sqlite3"

    result = create_catalog_backup(database_path, bundle)
    verified = verify_catalog_backup(bundle)
    restored = restore_catalog_backup(bundle, destination)

    assert result.alembic_revision == "0035"
    assert verified.catalog_sha256 == result.catalog_sha256
    assert restored.catalog_sha256 == result.catalog_sha256
    with sqlite3.connect(destination) as connection:
        revision = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert revision == ("0035",)


def test_create_fails_if_private_permissions_cannot_be_set_where_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError

    if os.name == "nt":
        pytest.skip("POSIX chmod failure handling is not portable to Windows")

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")

    def fail_chmod(path: Path | str, mode: int, *args: object, **kwargs: object) -> None:
        raise OSError("chmod denied")

    monkeypatch.setattr(catalog.os, "chmod", fail_chmod)

    with pytest.raises(BackupError, match="permissions"):
        catalog.create_catalog_backup(database_path, tmp_path / "bundle")

    assert not (tmp_path / "bundle").exists()
    assert [
        path
        for path in tmp_path.iterdir()
        if path.name.startswith((".kronika-backup-", ".framenest-backup-"))
    ] == []


def test_restore_fails_if_private_permissions_cannot_be_set_where_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.infrastructure.persistence import catalog_backup as catalog
    from kronika.infrastructure.persistence.catalog_backup import BackupError, create_catalog_backup

    if os.name == "nt":
        pytest.skip("POSIX chmod failure handling is not portable to Windows")

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)

    def fail_chmod(path: Path | str, mode: int, *args: object, **kwargs: object) -> None:
        raise OSError("chmod denied")

    monkeypatch.setattr(catalog.os, "chmod", fail_chmod)
    destination = tmp_path / "restored.sqlite3"

    with pytest.raises(BackupError, match="permissions"):
        catalog.restore_catalog_backup(bundle, destination)

    assert not destination.exists()


def test_restrictive_file_permissions_where_supported(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup, restore_catalog_backup

    if os.name == "nt":
        pytest.skip("POSIX permission bits are not portable to Windows")

    bundle = tmp_path / "bundle"
    create_catalog_backup(_migrated_database(tmp_path / "catalog.sqlite3"), bundle)
    destination = tmp_path / "restored.sqlite3"
    restore_catalog_backup(bundle, destination)

    assert (bundle.stat().st_mode & 0o777) == 0o700
    assert ((bundle / "catalog.sqlite3").stat().st_mode & 0o777) == 0o600
    assert ((bundle / "manifest.json").stat().st_mode & 0o777) == 0o600
    assert (destination.stat().st_mode & 0o777) == 0o600


def test_backup_preserves_cover_rows_while_artifacts_remain_outside_bundle(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.catalog_backup import (
        create_catalog_backup,
        restore_catalog_backup,
    )

    database_path = _migrated_database(tmp_path / "catalog.sqlite3")
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) VALUES "
            "('11111111-1111-4111-8111-111111111111', 'video', 1, 1)"
        )
        connection.execute(
            "INSERT INTO media_covers ("
            " media_id, source_location_id, source_reference, source_kind, "
            " source_timestamp_ms, source_size_bytes, source_mtime_ns, "
            " source_duration_ms, source_observation_version, source_observation_digest, "
            " artifact_profile, artifact_media_type, artifact_digest, artifact_width, "
            " artifact_height, artifact_byte_size, revision, accepted_at_ms) "
            "VALUES ('11111111-1111-4111-8111-111111111111', NULL, "
            " 'location:22222222-2222-4222-8222-222222222222', 'mp4', 500, 100, "
            " 1, 1000, 'cover-source-observation-v1', '" + "a" * 64 + "', "
            " 'durable-cover-jpeg-v1', 'image/jpeg', '" + "b" * 64 + "', "
            " 512, 288, 20000, 1, 100)"
        )
        connection.commit()
    finally:
        connection.close()

    bundle = tmp_path / "bundle"
    create_catalog_backup(database_path, bundle)
    manifest = _manifest(bundle)
    assert manifest["catalog"]["alembic_revision"] == "0035"
    assert set(name for name in (bundle / "catalog.sqlite3").parent.iterdir()) or True
    bundle_names = {child.name for child in bundle.iterdir()}
    assert bundle_names == {"manifest.json", "catalog.sqlite3"}

    destination = tmp_path / "restored.sqlite3"
    restore_catalog_backup(bundle, destination)
    restored = sqlite3.connect(destination)
    try:
        rows = restored.execute(
            "SELECT media_id, revision, artifact_digest FROM media_covers"
        ).fetchall()
        assert rows == [("11111111-1111-4111-8111-111111111111", 1, "b" * 64)]
    finally:
        restored.close()


def test_backup_restore_preserves_documents_projections_and_private_modes(
    tmp_path: Path,
) -> None:
    from kronika.application.records import RecordService
    from kronika.domain.identity_access import ROLE_ADMIN, ROLE_USER
    from kronika.domain.records import CompletedDocument, DocumentId, RecordKind
    from kronika.domain.research import CompletionEvidence
    from kronika.infrastructure.persistence.catalog_backup import (
        create_catalog_backup,
        restore_catalog_backup,
    )
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.record_repository import (
        SqliteRecordRepository,
    )
    from tests.support.record_access import synthetic_identity

    database_path = _migrated_database(tmp_path / "source" / "catalog.sqlite3")
    engine = create_sqlite_engine(database_path)
    service = RecordService(SqliteRecordRepository(engine))
    alice = synthetic_identity("alice", role=ROLE_USER)
    admin = synthetic_identity("ada", role=ROLE_ADMIN)
    document = CompletedDocument(
        document_id=DocumentId.new(),
        operation_id="search-op-backup",
        kind=RecordKind.SEARCH,
        question_text="Backup question",
        answer_text="Backup answer",
        citations=(),
        evidence=CompletionEvidence(
            provider_terminal=True,
            answer_complete=True,
            web_search_executed=True,
            refusal_marker=False,
            incomplete_marker=False,
        ),
        created_at_ms=3,
        completed_at_ms=4,
    )
    try:
        created = service.create_completed_document(alice, document)
        candidate = service.prepare_approval(admin, created.summary.record_id)
        service.approve(admin, candidate, approved_at_ms=8)
    finally:
        dispose_engine(engine)
    bundle = tmp_path / "bundle"
    create_catalog_backup(database_path, bundle)
    assert (bundle.stat().st_mode & 0o777) == 0o700
    assert ((bundle / "catalog.sqlite3").stat().st_mode & 0o777) == 0o600
    destination = tmp_path / "restored.sqlite3"
    restore_catalog_backup(bundle, destination)
    assert (destination.stat().st_mode & 0o777) == 0o600
    restored = sqlite3.connect(destination)
    try:
        answer = restored.execute(
            "SELECT answer_text FROM kronika_documents WHERE operation_id = ?",
            ("search-op-backup",),
        ).fetchone()[0]
        projection = restored.execute(
            "SELECT approved_projection_json FROM kronika_records"
        ).fetchone()[0]
    finally:
        restored.close()
    assert answer == "Backup answer"
    assert projection is not None
    assert "Backup answer" in projection
