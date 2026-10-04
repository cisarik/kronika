"""Contract tests for the server-operator library CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from kronika.adapters.cli import library
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head


def _migrate(database_path: Path) -> None:
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))


def _counts(database_path: Path) -> tuple[int, int, int, int]:
    engine = create_sqlite_engine(database_path)
    try:
        devices = SqliteDeviceRepository(engine).list_all()
        libraries = SqliteLibraryRepository(engine).list_all()
        media_repository = SqliteMediaRepository(engine)
        return (
            len(devices),
            len(libraries),
            len(media_repository.list_media()),
            len(media_repository.list_all_locations()),
        )
    finally:
        dispose_engine(engine)


def _run(
    monkeypatch: pytest.MonkeyPatch,
    database_path: Path,
    argv: list[str],
) -> int:
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))
    return library.main(argv)


def test_status_is_network_free_scan_free_read_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)

    def fail_preview(*args: object, **kwargs: object) -> object:
        raise AssertionError("status must not scan")

    monkeypatch.setattr("kronika.infrastructure.filesystem.library_scanner.LocalLibraryScanner.preview", fail_preview)

    assert _run(monkeypatch, database_path, ["status"]) == 0

    output = capsys.readouterr().out
    assert "Registered devices: 0" in output
    assert "Registered libraries: 0" in output
    assert _counts(database_path) == (0, 0, 0, 0)


def test_add_rejects_relative_missing_and_non_directory_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)
    file_path = tmp_path / "not-directory.txt"
    file_path.write_text("not media", encoding="utf-8")

    for rejected in ["relative", str(tmp_path / "missing"), str(file_path)]:
        assert _run(monkeypatch, database_path, ["add", "--root", rejected, "--yes"]) == 2
        assert _counts(database_path) == (0, 0, 0, 0)


def test_confirmed_add_outputs_scan_summary_before_import_and_populates_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)
    root = tmp_path / "library"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"fake mp4")

    result = _run(
        monkeypatch,
        database_path,
        ["add", "--root", str(root), "--display-name", "Imported", "--yes"],
    )

    assert result == 0
    output = capsys.readouterr().out
    assert output.index("Scan summary:") < output.index("Import complete")
    assert "candidate files seen: 1" in output
    assert "New imports: 1" in output
    assert _counts(database_path) == (1, 1, 1, 1)


def test_declining_confirmation_leaves_catalog_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)
    root = tmp_path / "library"
    root.mkdir()
    (root / "clip.gif").write_bytes(b"fake gif")
    monkeypatch.setattr("builtins.input", lambda prompt: "no")

    result = _run(monkeypatch, database_path, ["add", "--root", str(root), "--display-name", "Imported"])

    assert result == 0
    assert "No durable changes made." in capsys.readouterr().out
    assert _counts(database_path) == (0, 0, 0, 0)


def test_repeated_add_and_refresh_are_idempotent_and_refresh_imports_only_new_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)
    root = tmp_path / "library"
    root.mkdir()
    (root / "clip.mp4").write_bytes(b"fake mp4")
    add_args = ["add", "--root", str(root), "--display-name", "Imported", "--yes"]

    assert _run(monkeypatch, database_path, add_args) == 0
    capsys.readouterr()
    assert _run(monkeypatch, database_path, add_args) == 0
    second = capsys.readouterr().out

    assert "Existing registered root detected" in second
    assert "New imports: 0" in second
    assert _counts(database_path) == (1, 1, 1, 1)

    (root / "new.gif").write_bytes(b"fake gif")
    assert _run(monkeypatch, database_path, ["refresh", "--yes"]) == 0
    refreshed = capsys.readouterr().out

    assert "New imports: 1" in refreshed
    assert "Already imported: 1" in refreshed
    assert _counts(database_path) == (1, 1, 2, 2)
