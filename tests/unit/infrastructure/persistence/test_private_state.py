"""POSIX private-catalog creation and rejection tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from kronika.infrastructure.persistence.private_state import (
    PrivateCatalogError,
    prepare_readonly_catalog,
    prepare_writable_catalog,
    verify_private_catalog,
)


def test_new_catalog_is_private_under_permissive_umask(tmp_path: Path) -> None:
    database = tmp_path / "nested" / "catalog.sqlite3"
    previous = os.umask(0o022)
    try:
        prepare_writable_catalog(database)
    finally:
        os.umask(previous)
    assert database.parent.stat().st_mode & 0o777 == 0o700
    assert database.stat().st_mode & 0o777 == 0o600
    assert database.stat().st_nlink == 1
    verify_private_catalog(database)


def test_readonly_prepare_creates_nothing(tmp_path: Path) -> None:
    missing = tmp_path / "missing.sqlite3"
    with pytest.raises(PrivateCatalogError) as caught:
        prepare_readonly_catalog(missing)
    assert caught.value.error_code == "DATABASE_NOT_FOUND"
    assert not missing.exists()


def test_symlink_database_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "real.sqlite3"
    prepare_writable_catalog(target)
    link = tmp_path / "link.sqlite3"
    link.symlink_to(target)
    with pytest.raises(PrivateCatalogError):
        prepare_writable_catalog(link)


def test_hardlinked_database_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    prepare_writable_catalog(database)
    linked = tmp_path / "alias.sqlite3"
    os.link(database, linked)
    with pytest.raises(PrivateCatalogError) as caught:
        verify_private_catalog(database)
    assert caught.value.error_code == "UNSAFE_CATALOG_LINK"


def test_unsafe_existing_mode_is_not_chmodded(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    database.write_bytes(b"")
    os.chmod(database, 0o644)
    with pytest.raises(PrivateCatalogError):
        prepare_writable_catalog(database)
    assert database.stat().st_mode & 0o777 == 0o644


def test_auxiliary_world_readable_file_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "catalog.sqlite3"
    prepare_writable_catalog(database)
    wal = Path(f"{database}-wal")
    wal.write_bytes(b"")
    os.chmod(wal, 0o644)
    with pytest.raises(PrivateCatalogError) as caught:
        verify_private_catalog(database)
    assert caught.value.error_code == "UNSAFE_CATALOG_MODE"


def test_error_text_has_no_path(tmp_path: Path) -> None:
    database = tmp_path / "secret-catalog-name.sqlite3"
    database.write_bytes(b"secret-bytes")
    os.chmod(database, 0o644)
    with pytest.raises(PrivateCatalogError) as caught:
        prepare_readonly_catalog(database)
    text = str(caught.value)
    assert "secret" not in text
    assert str(tmp_path) not in text
