"""Contract tests for the bounded catalog identity-label maintenance command."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import insert, select, update

from kronika.adapters.cli import catalog
from kronika.infrastructure.persistence import identity_labels
from kronika.infrastructure.persistence.catalog_schema import (
    devices,
    libraries,
    logical_media,
    physical_media_locations,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_transaction,
)
from kronika.infrastructure.persistence.identity_labels import (
    CANONICAL_DEVICE_LABEL,
    RETIRED_BRAND,
    RETIRED_DEVICE_LABEL,
    IdentityLabelsAmbiguousCandidateError,
    IdentityLabelsCatalogBusyError,
    IdentityLabelsReceiptExistsError,
    IdentityLabelsReceiptInvalidError,
    IdentityLabelsSelectionNotApprovedError,
    IdentityLabelsStaleReceiptError,
    apply_identity_labels,
    check_identity_labels,
    read_receipt,
    receipt_path_for,
    rollback_identity_labels,
)
from tests.contract.test_catalog_cli import _run_db_migrate

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CATALOG_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "kronika-catalog"

DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
OTHER_DEVICE_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
EXTRA_DEVICE_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
LIBRARY_ID = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
SECOND_LIBRARY_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
MEDIA_ID = "ffffffff-ffff-4fff-8fff-ffffffffffff"
LOCATION_ID = "11111111-1111-4111-8111-111111111111"
SENTINEL_LIBRARY_NAME = "FrameNest Zeppelin Archive"
SENTINEL_MEDIA_PATH = "/srv/sentinel-media/clip.mp4"


def _migrate(tmp_path: Path, name: str = "catalog.sqlite3") -> Path:
    database_path = tmp_path / name
    result = _run_db_migrate(cwd=tmp_path, database_path=str(database_path))
    assert result.returncode == 0, result.stderr
    return database_path


def _seed(
    database_path: Path,
    *,
    device_rows: tuple[tuple[str, str], ...] = (),
    library_rows: tuple[tuple[str, str, str, str, str], ...] = (),
) -> None:
    def operation(connection: Any) -> None:
        for row_id, display_name in device_rows:
            connection.execute(
                insert(devices).values(id=row_id, display_name=display_name)
            )
        for row_id, device_id, display_name, flavor, root_path in library_rows:
            connection.execute(
                insert(libraries).values(
                    id=row_id,
                    device_id=device_id,
                    display_name=display_name,
                    path_flavor=flavor,
                    root_path=root_path,
                )
            )

    engine = create_sqlite_engine(database_path)
    try:
        run_in_transaction(engine, operation)
    finally:
        dispose_engine(engine)


def _seed_linked_media(database_path: Path, library_id: str) -> None:
    def operation(connection: Any) -> None:
        connection.execute(
            insert(logical_media).values(
                id=MEDIA_ID,
                media_kind="video",
                created_at_ms=1,
                updated_at_ms=1,
            )
        )
        connection.execute(
            insert(physical_media_locations).values(
                id=LOCATION_ID,
                media_id=MEDIA_ID,
                library_id=library_id,
                relative_path="clip.mp4",
                availability="available",
                created_at_ms=1,
                updated_at_ms=1,
            )
        )

    engine = create_sqlite_engine(database_path)
    try:
        run_in_transaction(engine, operation)
    finally:
        dispose_engine(engine)


def _set_label(database_path: Path, table: Any, row_id: str, label: str) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        run_in_transaction(
            engine,
            lambda connection: connection.execute(
                update(table)
                .where(table.c.id == row_id)
                .values(display_name=label)
            ),
        )
    finally:
        dispose_engine(engine)


def _insert_device(database_path: Path, row_id: str, label: str) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        run_in_transaction(
            engine,
            lambda connection: connection.execute(
                insert(devices).values(id=row_id, display_name=label)
            ),
        )
    finally:
        dispose_engine(engine)


def _snapshot(database_path: Path) -> dict[str, Any]:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            device_rows = tuple(
                (str(row[0]), str(row[1]))
                for row in connection.execute(
                    select(devices.c.id, devices.c.display_name).order_by(devices.c.id)
                )
            )
            library_rows = tuple(
                tuple(str(value) for value in row)
                for row in connection.execute(
                    select(
                        libraries.c.id,
                        libraries.c.device_id,
                        libraries.c.display_name,
                        libraries.c.path_flavor,
                        libraries.c.root_path,
                    ).order_by(libraries.c.id)
                )
            )
            location_rows = tuple(
                tuple(str(value) for value in row)
                for row in connection.execute(
                    select(
                        physical_media_locations.c.id,
                        physical_media_locations.c.media_id,
                        physical_media_locations.c.library_id,
                        physical_media_locations.c.relative_path,
                        physical_media_locations.c.availability,
                    ).order_by(physical_media_locations.c.id)
                )
            )
            revision = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar()
            foreign_key_violations = tuple(
                tuple(str(value) for value in row)
                for row in connection.exec_driver_sql(
                    "PRAGMA foreign_key_check"
                ).fetchall()
            )
    finally:
        dispose_engine(engine)
    return {
        "devices": device_rows,
        "libraries": library_rows,
        "locations": location_rows,
        "revision": revision,
        "foreign_keys": foreign_key_violations,
    }


def _run_in_process(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_path: Path,
    *args: str,
) -> tuple[int, str, str]:
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))
    exit_code = catalog.main(list(args))
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def _success_payload(stdout: str) -> dict[str, Any]:
    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["state"] == "ok"
    return payload


def _error_payload(stderr: str) -> dict[str, Any]:
    lines = [line for line in stderr.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["state"] == "error"
    return payload


def _expected_device_only_snapshot() -> dict[str, Any]:
    return {
        "devices": ((DEVICE_ID, CANONICAL_DEVICE_LABEL),),
        "libraries": (
            (LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),
            (
                SECOND_LIBRARY_ID,
                DEVICE_ID,
                SENTINEL_LIBRARY_NAME,
                "posix",
                SENTINEL_MEDIA_PATH,
            ),
        ),
        "locations": (
            (LOCATION_ID, MEDIA_ID, SECOND_LIBRARY_ID, "clip.mp4", "available"),
        ),
        "revision": "0035",
        "foreign_keys": (),
    }


def _seed_standard_catalog(database_path: Path) -> None:
    _seed(
        database_path,
        device_rows=((DEVICE_ID, RETIRED_DEVICE_LABEL),),
        library_rows=(
            (LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),
            (
                SECOND_LIBRARY_ID,
                DEVICE_ID,
                SENTINEL_LIBRARY_NAME,
                "posix",
                SENTINEL_MEDIA_PATH,
            ),
        ),
    )
    _seed_linked_media(database_path, SECOND_LIBRARY_ID)


def test_check_reports_sanitized_counts_and_writes_a_private_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _success_payload(stdout)
    assert payload["operation"] == "identity_labels.check"
    assert payload["device_candidate_count"] == 1
    assert payload["device_candidate_present"] is True
    assert payload["library_candidate_count"] == 1
    assert payload["library_brand_present"] is True

    receipt_file = receipt_path_for(database_path)
    assert receipt_file.is_file()
    assert not receipt_file.is_symlink()
    assert receipt_file.stat().st_mode & 0o7777 == 0o600
    assert payload["receipt_digest"] == hashlib.sha256(
        receipt_file.read_bytes()
    ).hexdigest()

    receipt, digest = read_receipt(receipt_file)
    assert digest == payload["receipt_digest"]
    assert receipt.device_candidate_count == 1
    assert receipt.device_selection is not None
    assert receipt.device_selection.device_id == DEVICE_ID
    assert receipt.device_selection.old_label == RETIRED_DEVICE_LABEL
    assert receipt.device_selection.new_label == CANONICAL_DEVICE_LABEL
    assert receipt.library_candidate_count == 1
    assert receipt.library_selections[0].library_id == SECOND_LIBRARY_ID
    assert receipt.library_selections[0].old_label == SENTINEL_LIBRARY_NAME
    assert receipt.library_selections[0].new_label == "Kronika Zeppelin Archive"

    combined = stdout + stderr
    for forbidden in (
        DEVICE_ID,
        SECOND_LIBRARY_ID,
        "Zeppelin",
        SENTINEL_MEDIA_PATH,
        RETIRED_DEVICE_LABEL,
        "SELECT",
        "UPDATE",
    ):
        assert forbidden not in combined


def test_check_is_read_only_about_the_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)

    def fail_writable_engine(*args: object, **kwargs: object) -> object:
        raise AssertionError("check must not open the catalog for writing")

    monkeypatch.setattr(
        identity_labels, "create_sqlite_engine", fail_writable_engine
    )

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 0
    assert stderr == ""
    assert _success_payload(stdout)["device_candidate_count"] == 1
    assert _snapshot(database_path) == before


def test_check_refuses_to_overwrite_an_existing_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    first = check_identity_labels(database_path)
    receipt_file = receipt_path_for(database_path)
    original_bytes = receipt_file.read_bytes()

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_RECEIPT_EXISTS"
    assert receipt_file.read_bytes() == original_bytes
    assert first.receipt_digest == hashlib.sha256(original_bytes).hexdigest()


def test_check_reports_an_absent_device_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=((DEVICE_ID, "Studio Mac"),),
        library_rows=((LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),),
    )

    exit_code, stdout, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 0
    payload = _success_payload(stdout)
    assert payload["device_candidate_count"] == 0
    assert payload["device_candidate_present"] is False
    assert payload["library_candidate_count"] == 0
    assert payload["library_brand_present"] is False
    receipt, _ = read_receipt(receipt_path_for(database_path))
    assert receipt.device_selection is None
    assert receipt.library_selections == ()


def test_check_reports_an_ambiguous_device_candidate_set(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=(
            (DEVICE_ID, RETIRED_DEVICE_LABEL),
            (OTHER_DEVICE_ID, RETIRED_DEVICE_LABEL),
        ),
    )

    exit_code, stdout, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 0
    payload = _success_payload(stdout)
    assert payload["device_candidate_count"] == 2
    assert payload["device_candidate_present"] is False
    receipt, _ = read_receipt(receipt_path_for(database_path))
    assert receipt.device_candidate_count == 2
    assert receipt.device_selection is None


def test_check_matches_the_device_label_exactly_and_is_case_sensitive(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=(
            (DEVICE_ID, "framenest nuc"),
            (OTHER_DEVICE_ID, RETIRED_DEVICE_LABEL + " 2"),
        ),
        library_rows=(
            (LIBRARY_ID, DEVICE_ID, "framenest videos", "posix", "/media/videos"),
        ),
    )

    exit_code, stdout, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )

    assert exit_code == 0
    payload = _success_payload(stdout)
    assert payload["device_candidate_count"] == 0
    assert payload["library_candidate_count"] == 0


def test_apply_requires_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply"
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_CONFIRMATION_REQUIRED"
    )
    assert _snapshot(database_path) == before


def test_apply_refuses_when_the_receipt_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_RECEIPT_NOT_FOUND"
    assert _snapshot(database_path) == before


def test_apply_refuses_a_receipt_that_is_not_privately_stored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    receipt_file = receipt_path_for(database_path)
    os.chmod(receipt_file, 0o644)
    before = _snapshot(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_RECEIPT_INVALID"
    assert _snapshot(database_path) == before


def test_read_receipt_refuses_a_symlink(
    tmp_path: Path,
) -> None:
    target = tmp_path / "elsewhere.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "catalog.sqlite3.identity-labels-receipt.json"
    os.symlink(target, link)

    with pytest.raises(IdentityLabelsReceiptInvalidError):
        read_receipt(link)


def test_apply_refuses_a_tampered_receipt_inverse_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    receipt_file = receipt_path_for(database_path)
    document = json.loads(receipt_file.read_text(encoding="utf-8"))
    document["library_selections"][0]["new_label"] = "Something Else"
    receipt_file.write_text(json.dumps(document), encoding="utf-8")
    before = _snapshot(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_RECEIPT_INVALID"
    assert _snapshot(database_path) == before


def test_apply_device_only_preserves_every_other_row_and_column(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _success_payload(stdout)
    assert payload["operation"] == "identity_labels.apply"
    assert payload["device_updated"] is True
    assert payload["libraries_updated"] == 0
    assert payload["device_transition_verified"] is True
    assert payload["libraries_transition_verified"] is True
    assert payload["foreign_keys_valid"] is True
    assert payload["schema_revision_at_head"] is True
    assert payload["selected_retired_labels_remaining"] == 0

    after = _snapshot(database_path)
    assert after["devices"] == ((DEVICE_ID, CANONICAL_DEVICE_LABEL),)
    assert after["revision"] == before["revision"] == "0035"
    assert after["foreign_keys"] == ()
    assert after["libraries"] == before["libraries"]
    assert after["locations"] == before["locations"]


def test_apply_includes_libraries_only_with_receipt_and_flag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _success_payload(stdout)
    assert payload["device_updated"] is True
    assert payload["libraries_updated"] == 1
    assert payload["libraries_transition_verified"] is True

    after = _snapshot(database_path)
    assert after["devices"] == ((DEVICE_ID, CANONICAL_DEVICE_LABEL),)
    assert after["libraries"] == (
        (LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),
        (
            SECOND_LIBRARY_ID,
            DEVICE_ID,
            "Kronika Zeppelin Archive",
            "posix",
            SENTINEL_MEDIA_PATH,
        ),
    )
    assert after["locations"] == (
        (LOCATION_ID, MEDIA_ID, SECOND_LIBRARY_ID, "clip.mp4", "available"),
    )
    assert RETIRED_BRAND not in json.dumps(after["libraries"])


def test_apply_refuses_libraries_without_a_positive_selection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=((DEVICE_ID, RETIRED_DEVICE_LABEL),),
        library_rows=((LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),),
    )
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_SELECTION_NOT_APPROVED"
    )
    assert _snapshot(database_path) == before


def test_apply_refuses_when_nothing_is_selected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=((DEVICE_ID, "Studio Mac"),),
    )
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_SELECTION_NOT_APPROVED"
    )
    assert _snapshot(database_path) == before


def test_apply_refuses_a_stale_receipt_when_the_selected_row_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    _set_label(database_path, devices, DEVICE_ID, "Renamed By Someone")

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_STALE_RECEIPT"
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, "Renamed By Someone"),
    )


def test_apply_refuses_a_stale_receipt_when_the_candidate_set_grew(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    _insert_device(database_path, EXTRA_DEVICE_ID, RETIRED_DEVICE_LABEL)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_STALE_RECEIPT"
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, RETIRED_DEVICE_LABEL),
        (EXTRA_DEVICE_ID, RETIRED_DEVICE_LABEL),
    )


def test_apply_refuses_a_stale_receipt_when_a_library_candidate_changed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    _set_label(database_path, libraries, SECOND_LIBRARY_ID, "Renamed Library")

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_STALE_RECEIPT"
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, RETIRED_DEVICE_LABEL),
    )


def test_apply_refuses_an_ambiguous_device_candidate_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=(
            (DEVICE_ID, RETIRED_DEVICE_LABEL),
            (OTHER_DEVICE_ID, RETIRED_DEVICE_LABEL),
        ),
    )
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_AMBIGUOUS_CANDIDATE"
    )
    assert _snapshot(database_path) == before


def test_apply_rolls_back_every_change_when_a_late_update_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    real_compare_and_set = identity_labels._compare_and_set

    def failing_compare_and_set(
        connection: Any,
        table: Any,
        row_id: str,
        expected: str,
        target: str,
    ) -> None:
        if table is libraries:
            raise RuntimeError("injected late update failure")
        real_compare_and_set(connection, table, row_id, expected, target)

    monkeypatch.setattr(
        identity_labels, "_compare_and_set", failing_compare_and_set
    )

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 1
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_COMMAND_FAILED"
    assert _snapshot(database_path) == before


def test_apply_reports_catalog_busy_while_a_writer_holds_the_catalog(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=((DEVICE_ID, RETIRED_DEVICE_LABEL),),
    )
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    blocker = sqlite3.connect(
        str(database_path),
        timeout=0.0,
        isolation_level=None,
    )
    try:
        blocker.execute("BEGIN IMMEDIATE")
        with pytest.raises(IdentityLabelsCatalogBusyError):
            apply_identity_labels(
                database_path,
                include_libraries=False,
                expected_revision="0035",
                busy_timeout_seconds=0.2,
            )
    finally:
        blocker.rollback()
        blocker.close()

    assert _snapshot(database_path) == before


def test_apply_maps_catalog_busy_to_exit_6(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    def busy(*args: object, **kwargs: object) -> object:
        raise IdentityLabelsCatalogBusyError()

    monkeypatch.setattr(catalog, "apply_identity_labels", busy)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 6
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_CATALOG_BUSY"
    assert _snapshot(database_path) == before


def test_apply_uses_exactly_one_immediate_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)

    calls: list[str] = []
    real_immediate = identity_labels.run_in_immediate_transaction

    def spy(engine: Any, operation: Any) -> Any:
        calls.append("immediate")
        return real_immediate(engine, operation)

    monkeypatch.setattr(identity_labels, "run_in_immediate_transaction", spy)

    exit_code, _, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 0
    assert calls == ["immediate"]


def test_apply_fails_loudly_when_a_writer_does_not_reach_the_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)

    def wrong_target(
        connection: Any,
        table: Any,
        row_id: str,
        expected: str,
        target: str,
    ) -> None:
        result = connection.execute(
            update(table)
            .where(table.c.id == row_id, table.c.display_name == expected)
            .values(display_name=target + "!")
        )
        assert result.rowcount == 1

    monkeypatch.setattr(identity_labels, "_compare_and_set", wrong_target)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )

    assert exit_code == 1
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_VERIFICATION_FAILED"
    )
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, CANONICAL_DEVICE_LABEL + "!"),
    )


def test_compare_and_set_refuses_a_mismatched_expected_value(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    _seed(database_path, device_rows=((DEVICE_ID, RETIRED_DEVICE_LABEL),))

    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(IdentityLabelsStaleReceiptError):
                    identity_labels._compare_and_set(
                        connection,
                        devices,
                        DEVICE_ID,
                        "Not The Stored Label",
                        CANONICAL_DEVICE_LABEL,
                    )
            finally:
                transaction.rollback()
    finally:
        dispose_engine(engine)

    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, RETIRED_DEVICE_LABEL),
    )


def test_apply_is_not_repeatable_after_verified_completion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)

    first_exit, _, _ = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )
    assert first_exit == 0
    completed = _snapshot(database_path)

    second_exit, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )

    assert second_exit == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_STALE_RECEIPT"
    assert _snapshot(database_path) == completed


def test_rollback_restores_the_exact_prior_labels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    apply_exit, _, _ = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )
    assert apply_exit == 0
    assert _snapshot(database_path) != before

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "rollback",
        "--yes",
        "--include-libraries",
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _success_payload(stdout)
    assert payload["operation"] == "identity_labels.rollback"
    assert payload["device_restored"] is True
    assert payload["libraries_restored"] == 1
    assert payload["device_restoration_verified"] is True
    assert payload["libraries_restoration_verified"] is True
    assert payload["foreign_keys_valid"] is True
    assert payload["schema_revision_at_head"] is True
    assert payload["selected_canonical_labels_remaining"] == 0
    assert _snapshot(database_path) == before


def test_rollback_requires_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback"
    )

    assert exit_code == 2
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_CONFIRMATION_REQUIRED"
    )
    assert _snapshot(database_path) == before


def test_rollback_refuses_when_the_receipt_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback", "--yes"
    )

    assert exit_code == 3
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_RECEIPT_NOT_FOUND"


def test_device_only_rollback_after_device_only_apply_restores_the_catalog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)
    check_identity_labels(database_path)
    apply_exit, _, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )
    assert apply_exit == 0
    after_apply = _snapshot(database_path)
    assert after_apply["devices"] == ((DEVICE_ID, CANONICAL_DEVICE_LABEL),)
    assert after_apply["libraries"] == before["libraries"]

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback", "--yes"
    )

    assert exit_code == 0
    assert stderr == ""
    payload = _success_payload(stdout)
    assert payload["device_restored"] is True
    assert payload["libraries_restored"] == 0
    assert payload["device_restoration_verified"] is True
    assert payload["libraries_restoration_verified"] is True
    assert _snapshot(database_path) == before


def test_library_only_apply_and_rollback_without_a_device_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed(
        database_path,
        device_rows=((DEVICE_ID, "Studio Mac"),),
        library_rows=(
            (LIBRARY_ID, DEVICE_ID, "Home Videos", "posix", "/media/home"),
            (
                SECOND_LIBRARY_ID,
                DEVICE_ID,
                SENTINEL_LIBRARY_NAME,
                "posix",
                SENTINEL_MEDIA_PATH,
            ),
        ),
    )
    _seed_linked_media(database_path, SECOND_LIBRARY_ID)
    before = _snapshot(database_path)

    check_exit, check_stdout, check_stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "check"
    )
    assert check_exit == 0
    assert check_stderr == ""
    check_payload = _success_payload(check_stdout)
    assert check_payload["device_candidate_count"] == 0
    assert check_payload["device_candidate_present"] is False
    assert check_payload["library_candidate_count"] == 1
    receipt, _ = read_receipt(receipt_path_for(database_path))
    assert receipt.device_selection is None
    assert receipt.library_candidate_count == 1
    assert receipt.library_selections[0].library_id == SECOND_LIBRARY_ID

    apply_exit, apply_stdout, apply_stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )
    assert apply_exit == 0
    assert apply_stderr == ""
    apply_payload = _success_payload(apply_stdout)
    assert apply_payload["device_updated"] is False
    assert apply_payload["libraries_updated"] == 1
    assert apply_payload["device_transition_verified"] is True
    assert apply_payload["libraries_transition_verified"] is True
    assert apply_payload["selected_retired_labels_remaining"] == 0

    after_apply = _snapshot(database_path)
    assert after_apply["devices"] == ((DEVICE_ID, "Studio Mac"),)
    assert after_apply["libraries"][0] == before["libraries"][0]
    assert after_apply["libraries"][1][2] == "Kronika Zeppelin Archive"
    assert after_apply["locations"] == before["locations"]
    assert after_apply["revision"] == before["revision"]
    assert after_apply["foreign_keys"] == ()

    rollback_exit, rollback_stdout, rollback_stderr = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "rollback",
        "--yes",
        "--include-libraries",
    )
    assert rollback_exit == 0
    assert rollback_stderr == ""
    rollback_payload = _success_payload(rollback_stdout)
    assert rollback_payload["device_restored"] is False
    assert rollback_payload["libraries_restored"] == 1
    assert rollback_payload["device_restoration_verified"] is True
    assert rollback_payload["libraries_restoration_verified"] is True
    assert _snapshot(database_path) == before

    combined = (
        check_stdout
        + check_stderr
        + apply_stdout
        + apply_stderr
        + rollback_stdout
        + rollback_stderr
    )
    for forbidden in (
        DEVICE_ID,
        SECOND_LIBRARY_ID,
        "Zeppelin",
        SENTINEL_MEDIA_PATH,
    ):
        assert forbidden not in combined


def test_rollback_without_library_authority_keeps_canonical_libraries(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    apply_exit, _, _ = _run_in_process(
        monkeypatch,
        capsys,
        database_path,
        "identity-labels",
        "apply",
        "--yes",
        "--include-libraries",
    )
    assert apply_exit == 0

    exit_code, stdout, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback", "--yes"
    )

    assert exit_code == 0
    payload = _success_payload(stdout)
    assert payload["device_restored"] is True
    assert payload["libraries_restored"] == 0
    after = _snapshot(database_path)
    assert after["devices"] == ((DEVICE_ID, RETIRED_DEVICE_LABEL),)
    assert after["libraries"][1][2] == "Kronika Zeppelin Archive"


def test_rollback_refuses_a_stale_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    apply_exit, _, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )
    assert apply_exit == 0
    _set_label(database_path, devices, DEVICE_ID, "Renamed After Apply")

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback", "--yes"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_IDENTITY_LABELS_STALE_RECEIPT"
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, "Renamed After Apply"),
    )


def test_rollback_refuses_a_label_collision_on_the_device(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    check_identity_labels(database_path)
    apply_exit, _, _ = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "apply", "--yes"
    )
    assert apply_exit == 0
    _insert_device(database_path, EXTRA_DEVICE_ID, RETIRED_DEVICE_LABEL)

    exit_code, stdout, stderr = _run_in_process(
        monkeypatch, capsys, database_path, "identity-labels", "rollback", "--yes"
    )

    assert exit_code == 5
    assert stdout == ""
    payload = _error_payload(stderr)
    assert (
        payload["error_code"]
        == "FRAMENEST_CATALOG_IDENTITY_LABELS_AMBIGUOUS_CANDIDATE"
    )
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, CANONICAL_DEVICE_LABEL),
        (EXTRA_DEVICE_ID, RETIRED_DEVICE_LABEL),
    )


def test_console_check_without_a_database_setting_is_not_ready(
    tmp_path: Path,
) -> None:
    env = os.environ.copy()
    for name in (
        "FRAMENEST_DATABASE_PATH",
        "KRONIKA_DATABASE_PATH",
        "FRAMENEST_ENV_FILE",
        "KRONIKA_ENV_FILE",
    ):
        env.pop(name, None)
    env["TMPDIR"] = str(tmp_path)

    result = subprocess.run(
        [str(CATALOG_CONSOLE_SCRIPT), "identity-labels", "check"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )

    assert result.returncode == 4
    assert result.stdout == ""
    payload = _error_payload(result.stderr)
    assert payload["error_code"] == "FRAMENEST_CATALOG_NOT_READY"
    assert not (tmp_path / "framenest-development").exists()


def test_console_script_help_lists_the_bounded_operations(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(CATALOG_CONSOLE_SCRIPT), "identity-labels", "--help"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )

    assert result.returncode == 0
    assert "check" in result.stdout
    assert "apply" in result.stdout
    assert "rollback" in result.stdout


def test_console_script_output_is_sanitized_and_matches_the_receipt(
    tmp_path: Path,
) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    env = os.environ.copy()
    env["FRAMENEST_DATABASE_PATH"] = str(database_path)

    result = subprocess.run(
        [str(CATALOG_CONSOLE_SCRIPT), "identity-labels", "check"],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Traceback" not in result.stdout + result.stderr
    payload = _success_payload(result.stdout)
    combined = result.stdout + result.stderr
    for forbidden in (
        DEVICE_ID,
        SECOND_LIBRARY_ID,
        "Zeppelin",
        SENTINEL_MEDIA_PATH,
        str(database_path),
        "SELECT",
        "UPDATE",
        "INSERT",
        "sqlite",
    ):
        assert forbidden not in combined
    receipt_file = receipt_path_for(database_path)
    assert payload["receipt_digest"] == hashlib.sha256(
        receipt_file.read_bytes()
    ).hexdigest()


def test_module_level_check_apply_rollback_roundtrip(tmp_path: Path) -> None:
    database_path = _migrate(tmp_path)
    _seed_standard_catalog(database_path)
    before = _snapshot(database_path)

    checked = check_identity_labels(database_path)
    assert checked.device_candidate_count == 1
    assert checked.library_candidate_count == 1

    applied = apply_identity_labels(
        database_path,
        include_libraries=True,
        expected_revision="0035",
    )
    assert applied.device_changed is True
    assert applied.libraries_changed == 1
    assert applied.receipt_digest == checked.receipt_digest
    assert _snapshot(database_path)["devices"] == (
        (DEVICE_ID, CANONICAL_DEVICE_LABEL),
    )

    rolled_back = rollback_identity_labels(
        database_path,
        include_libraries=True,
        expected_revision="0035",
    )
    assert rolled_back.device_changed is True
    assert rolled_back.libraries_changed == 1
    assert rolled_back.receipt_digest == checked.receipt_digest
    assert _snapshot(database_path) == before


def test_receipt_reader_rejects_malformed_documents(tmp_path: Path) -> None:
    receipt_file = tmp_path / "catalog.sqlite3.identity-labels-receipt.json"
    payloads = (
        b"",
        b"not json",
        b"[]",
        b'{"version":2}',
        b'{"version":1,"created_at_ms":1,"device_candidate_count":1,'
        b'"device_selection":null,"library_candidate_count":0,'
        b'"library_selections":[]}',
        b'{"version":1,"created_at_ms":1,"device_candidate_count":0,'
        b'"device_selection":null,"library_candidate_count":0,'
        b'"library_selections":[],"extra":1}',
    )
    for payload in payloads:
        descriptor = os.open(
            receipt_file,
            os.O_CREAT | os.O_TRUNC | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, payload)
        finally:
            os.close(descriptor)
        with pytest.raises(IdentityLabelsReceiptInvalidError):
            read_receipt(receipt_file)
