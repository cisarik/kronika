"""Unit tests for production runtime commands."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock

import pytest

from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.errors import FrameNestMigrationError
from kronika.infrastructure.persistence.migrations import (
    MigrationStatus,
    upgrade_database_to_head,
)
from kronika.infrastructure.runtime import production


def _payload(output: str) -> dict[str, object]:
    lines = [line for line in output.splitlines() if line.strip()]
    assert len(lines) == 1
    parsed = json.loads(lines[0])
    assert isinstance(parsed, dict)
    return parsed


def _assert_no_traceback(output: str) -> None:
    assert "Traceback" not in output
    assert "traceback" not in output.lower()


def _database_revision(database_path: Path) -> str:
    with sqlite3.connect(database_path) as connection:
        row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    assert row is not None
    return str(row[0])


def test_check_database_ready_succeeds_only_at_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    upgrade_database_to_head(KronikaSettings(database_path=database_path))
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 0

    output = capsys.readouterr()
    assert output.err == ""
    assert _payload(output.out) == {
        "operation": "check-database-ready",
        "state": "ready",
        "current_revision": "0035",
    }
    assert str(database_path) not in output.out


def test_check_database_ready_rejects_missing_database_without_creating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "missing" / "catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 4

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["error_code"] == "FRAMENEST_DATABASE_NOT_READY"
    assert str(database_path) not in output.err
    assert not database_path.exists()


def test_check_database_ready_leaves_missing_parent_absent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "missing-parent" / "catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 4

    capsys.readouterr()
    assert not database_path.parent.exists()


def test_check_database_ready_rejects_empty_sqlite_database_without_schema_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "empty.sqlite3"
    with sqlite3.connect(database_path):
        pass
    before_tables = _table_names(database_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 4

    output = capsys.readouterr()
    assert _payload(output.err)["error_code"] == "FRAMENEST_DATABASE_NOT_READY"
    assert _table_names(database_path) == before_tables


def test_check_database_ready_rejects_behind_database_without_migrating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "behind.sqlite3"
    _write_revision(database_path, "0006")
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 4

    output = capsys.readouterr()
    assert output.out == ""
    assert _payload(output.err)["error_code"] == "FRAMENEST_DATABASE_NOT_READY"
    assert _database_revision(database_path) == "0006"
    assert str(database_path) not in output.err


def test_check_database_ready_rejects_unknown_ahead_database_without_mutating_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = tmp_path / "ahead.sqlite3"
    _write_revision(database_path, "9999")
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    assert production.main(["check-database-ready"]) == 4

    output = capsys.readouterr()
    assert _payload(output.err)["error_code"] == "FRAMENEST_DATABASE_NOT_READY"
    assert _database_revision(database_path) == "9999"
    assert "9999" not in output.err


def test_check_database_ready_sanitizes_inspection_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    private_path = "/private/catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", private_path)

    def fail_inspection(settings: KronikaSettings) -> MigrationStatus:
        raise FrameNestMigrationError(
            f"cannot open {settings.database_path}",
            error_code="OPEN_FAILED",
            retryable=False,
        )

    monkeypatch.setattr(production, "inspect_database_migration_status", fail_inspection)

    assert production.main(["check-database-ready"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["error_code"] == "FRAMENEST_PRODUCTION_COMMAND_FAILED"
    assert private_path not in output.err
    _assert_no_traceback(output.err)


def test_invalid_database_path_error_is_sanitized(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    supplied_path = "relative/private/catalog.sqlite3"
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", supplied_path)

    assert production.main(["check-database-ready"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["error_code"] == "FRAMENEST_PRODUCTION_COMMAND_FAILED"
    assert supplied_path not in output.err
    assert "relative/private" not in output.err
    _assert_no_traceback(output.err)


@pytest.mark.parametrize(
    ("argv", "expected_code"),
    [
        (["check-database-ready"], 4),
        (["unknown"], 2),
    ],
)
def test_expected_failures_do_not_emit_tracebacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    expected_code: int,
) -> None:
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(tmp_path / "missing.sqlite3"))

    assert production.main(argv) == expected_code

    output = capsys.readouterr()
    _assert_no_traceback(output.out + output.err)


def test_check_database_ready_explicitly_disables_dotenv_loading(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    load_settings = Mock(return_value=KronikaSettings(database_path=Path("/tmp/catalog.sqlite3")))
    monkeypatch.setattr(production, "load_settings", load_settings)
    monkeypatch.setattr(
        production,
        "inspect_database_migration_status",
        lambda settings: MigrationStatus(
            state="uninitialized",
            current_revision=None,
            head_revision="0009",
        ),
    )

    assert production.main(["check-database-ready"]) == 4

    capsys.readouterr()
    load_settings.assert_called_once_with(env_file=None)


def test_check_database_ready_never_calls_migration_operation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(tmp_path / "missing.sqlite3"))
    sentinel = Mock(side_effect=AssertionError("migration must not run"))
    monkeypatch.setattr(
        "kronika.infrastructure.persistence.migrations.upgrade_database_to_head",
        sentinel,
    )

    assert production.main(["check-database-ready"]) == 4

    capsys.readouterr()
    sentinel.assert_not_called()


def test_explicit_migrate_operation_remains_available(tmp_path: Path) -> None:
    database_path = tmp_path / "catalog.sqlite3"

    status = upgrade_database_to_head(KronikaSettings(database_path=database_path))

    assert status.state == "at_head"
    assert status.current_revision == "0035"


def test_serve_exists_and_runs_existing_server_runtime_with_no_dotenv(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = KronikaSettings(database_path=Path("/tmp/catalog.sqlite3"))
    load_settings = Mock(return_value=settings)
    run_server = Mock(return_value=None)
    monkeypatch.setattr(production, "load_settings", load_settings)
    monkeypatch.setattr(production, "run_server", run_server)
    monkeypatch.setattr(
        production,
        "inspect_database_migration_status",
        Mock(side_effect=AssertionError("readiness must not run")),
    )

    assert production.main(["serve"]) == 0

    load_settings.assert_called_once_with(env_file=None)
    run_server.assert_called_once_with(settings=settings)


def test_serve_does_not_open_browser_run_migrations_or_development_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(production, "load_settings", lambda env_file=None: KronikaSettings())
    run_server = Mock(return_value=None)
    monkeypatch.setattr(production, "run_server", run_server)
    monkeypatch.setattr(
        production,
        "inspect_database_migration_status",
        Mock(side_effect=AssertionError("readiness must not run")),
    )

    assert production.main(["serve"]) == 0

    assert run_server.call_count == 1


def test_serve_usage_errors_are_sanitized(capsys: pytest.CaptureFixture[str]) -> None:
    assert production.main(["serve", "--unknown"]) == 2

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["operation"] == "unknown"
    assert payload["error_code"] == "FRAMENEST_PRODUCTION_COMMAND_FAILED"
    _assert_no_traceback(output.err)


def test_serve_unexpected_failure_is_sanitized(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(production, "load_settings", lambda env_file=None: KronikaSettings())
    monkeypatch.setattr(production, "run_server", Mock(side_effect=RuntimeError("private failure")))

    assert production.main(["serve"]) == 1

    output = capsys.readouterr()
    assert output.out == ""
    payload = _payload(output.err)
    assert payload["operation"] == "serve"
    assert payload["error_code"] == "FRAMENEST_PRODUCTION_COMMAND_FAILED"
    assert "private failure" not in output.err
    _assert_no_traceback(output.err)


def _write_revision(database_path: Path, revision: str) -> None:
    with sqlite3.connect(database_path) as connection:
        connection.execute("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)")
        connection.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (revision,))


def _table_names(database_path: Path) -> set[str]:
    with sqlite3.connect(database_path) as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    return {str(row[0]) for row in rows}
