"""Unit tests for catalog backup transfer protocol and identity primitives."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _migrated_database(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    upgrade_database_to_head(KronikaSettings(database_path=path, _env_file=None))
    return path


def _bundle(tmp_path: Path) -> Path:
    from kronika.infrastructure.persistence.catalog_backup import create_catalog_backup

    source = _migrated_database(tmp_path / "catalog.sqlite3")
    output = tmp_path / "bundle"
    create_catalog_backup(source, output)
    return output


def test_protocol_round_trip_success(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        PROTOCOL_MAGIC,
        assert_stream_eof,
        capture_bundle_identity,
        read_protocol_v1_preamble,
        receive_file_bytes,
        write_protocol_v1_stream,
    )

    bundle = _bundle(tmp_path)
    identity = capture_bundle_identity(bundle, bundle_id="auto-20260808T031700Z-abcd1234")
    semantic = {"alembic_revision": identity.alembic_revision, "counts": {"logical_media_count": 0}}
    buffer = io.BytesIO()
    write_protocol_v1_stream(
        buffer,
        identity=identity,
        semantic=semantic,
        manifest_path=bundle / "manifest.json",
        catalog_path=bundle / "catalog.sqlite3",
    )
    buffer.seek(0)
    assert buffer.read(len(PROTOCOL_MAGIC)) == PROTOCOL_MAGIC
    buffer.seek(0)
    header = read_protocol_v1_preamble(buffer)
    assert header["bundle_id"] == identity.bundle_id
    dest = tmp_path / "recv"
    dest.mkdir()
    receive_file_bytes(
        buffer,
        dest / "manifest.json",
        expected_size=identity.manifest_size_bytes,
        expected_sha256=identity.manifest_sha256,
    )
    receive_file_bytes(
        buffer,
        dest / "catalog.sqlite3",
        expected_size=identity.catalog_size_bytes,
        expected_sha256=identity.catalog_sha256,
    )
    assert_stream_eof(buffer)


def test_protocol_wrong_magic(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        TransferError,
        read_protocol_v1_preamble,
    )

    with pytest.raises(TransferError) as exc:
        read_protocol_v1_preamble(io.BytesIO(b"BADMAGIC" + b"\x00" * 20))
    assert exc.value.error_code == "TRANSFER_MAGIC_MISMATCH"


def test_protocol_unsupported_version(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        PROTOCOL_MAGIC,
        TransferError,
        parse_protocol_v1_header,
        read_protocol_v1_preamble,
    )

    payload = {
        "protocol_version": 99,
        "bundle_id": "auto-20260808T031700Z-abcd1234",
        "manifest_filename": "manifest.json",
        "catalog_filename": "catalog.sqlite3",
        "manifest_size_bytes": 1,
        "manifest_sha256": "a" * 64,
        "catalog_size_bytes": 1,
        "catalog_sha256": "b" * 64,
        "alembic_revision": "0028",
        "semantic": {"alembic_revision": "0028", "counts": {}},
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    stream = io.BytesIO(PROTOCOL_MAGIC + len(encoded).to_bytes(4, "big") + encoded)
    with pytest.raises(TransferError) as exc:
        read_protocol_v1_preamble(stream)
    assert exc.value.error_code == "TRANSFER_PROTOCOL_UNSUPPORTED"
    with pytest.raises(TransferError):
        parse_protocol_v1_header(encoded)


def test_protocol_oversized_header() -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        PROTOCOL_MAGIC,
        TransferError,
        read_protocol_v1_preamble,
    )

    stream = io.BytesIO(PROTOCOL_MAGIC + (9000).to_bytes(4, "big") + b"x" * 10)
    with pytest.raises(TransferError) as exc:
        read_protocol_v1_preamble(stream)
    assert exc.value.error_code == "TRANSFER_HEADER_TOO_LARGE"


def test_protocol_unknown_and_missing_fields() -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        TransferError,
        parse_protocol_v1_header,
    )

    base = {
        "protocol_version": 1,
        "bundle_id": "auto-20260808T031700Z-abcd1234",
        "manifest_filename": "manifest.json",
        "catalog_filename": "catalog.sqlite3",
        "manifest_size_bytes": 1,
        "manifest_sha256": "a" * 64,
        "catalog_size_bytes": 1,
        "catalog_sha256": "b" * 64,
        "alembic_revision": "0028",
        "semantic": {"alembic_revision": "0028", "counts": {}},
    }
    unknown = dict(base)
    unknown["extra"] = True
    with pytest.raises(TransferError) as exc:
        parse_protocol_v1_header(json.dumps(unknown).encode("utf-8"))
    assert exc.value.error_code == "TRANSFER_HEADER_UNKNOWN_FIELD"
    missing = dict(base)
    del missing["bundle_id"]
    with pytest.raises(TransferError) as exc:
        parse_protocol_v1_header(json.dumps(missing).encode("utf-8"))
    assert exc.value.error_code == "TRANSFER_HEADER_MISSING_FIELD"


def test_protocol_filename_mismatch() -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        TransferError,
        parse_protocol_v1_header,
    )

    payload = {
        "protocol_version": 1,
        "bundle_id": "auto-20260808T031700Z-abcd1234",
        "manifest_filename": "other.json",
        "catalog_filename": "catalog.sqlite3",
        "manifest_size_bytes": 1,
        "manifest_sha256": "a" * 64,
        "catalog_size_bytes": 1,
        "catalog_sha256": "b" * 64,
        "alembic_revision": "0028",
        "semantic": {"alembic_revision": "0028", "counts": {}},
    }
    with pytest.raises(TransferError) as exc:
        parse_protocol_v1_header(json.dumps(payload).encode("utf-8"))
    assert exc.value.error_code == "TRANSFER_FILENAME_MISMATCH"


def test_protocol_truncated_and_trailing(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        TransferError,
        assert_stream_eof,
        capture_bundle_identity,
        read_protocol_v1_preamble,
        receive_file_bytes,
        write_protocol_v1_stream,
    )

    bundle = _bundle(tmp_path)
    identity = capture_bundle_identity(bundle, bundle_id="auto-20260808T031700Z-abcd1234")
    semantic = {"alembic_revision": identity.alembic_revision, "counts": {}}
    full = io.BytesIO()
    write_protocol_v1_stream(
        full,
        identity=identity,
        semantic=semantic,
        manifest_path=bundle / "manifest.json",
        catalog_path=bundle / "catalog.sqlite3",
    )
    data = full.getvalue()
    with pytest.raises(TransferError) as exc:
        read_protocol_v1_preamble(io.BytesIO(data[:12]))
    assert exc.value.error_code == "TRANSFER_PREMATURE_EOF"

    stream = io.BytesIO(data)
    header = read_protocol_v1_preamble(stream)
    dest = tmp_path / "cut"
    dest.mkdir()
    with pytest.raises(TransferError) as exc:
        receive_file_bytes(
            io.BytesIO(b"short"),
            dest / "manifest.json",
            expected_size=header["manifest_size_bytes"],
            expected_sha256=header["manifest_sha256"],
        )
    assert exc.value.error_code == "TRANSFER_PREMATURE_EOF"

    trailing = io.BytesIO(data + b"X")
    read_protocol_v1_preamble(trailing)
    receive_file_bytes(
        trailing,
        dest / "m.json",
        expected_size=identity.manifest_size_bytes,
        expected_sha256=identity.manifest_sha256,
    )
    receive_file_bytes(
        trailing,
        dest / "c.sqlite3",
        expected_size=identity.catalog_size_bytes,
        expected_sha256=identity.catalog_sha256,
    )
    with pytest.raises(TransferError) as exc:
        assert_stream_eof(trailing)
    assert exc.value.error_code == "TRANSFER_TRAILING_BYTES"


def test_protocol_digest_mismatch(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.catalog_backup_transfer import (
        TransferError,
        receive_file_bytes,
    )

    dest = tmp_path / "bad"
    with pytest.raises(TransferError) as exc:
        receive_file_bytes(
            io.BytesIO(b"abcd"),
            dest,
            expected_size=4,
            expected_sha256="0" * 64,
        )
    assert exc.value.error_code == "TRANSFER_DIGEST_MISMATCH"
