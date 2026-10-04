"""Contract tests for the dedicated portable media sidecar operator CLI."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kronika.application.media_sidecar import (
    FrameNestMediaSidecarApplicationError,
    SidecarCompareResult,
    SidecarExportResult,
)
from kronika.configuration import KronikaSettings
from kronika.domain import Device, DeviceId, Library, LibraryId, LibraryPathFlavor, LibraryRoot
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media import (
    LogicalMedia,
    MediaKind,
    MediaLocation,
    MediaLocationAvailability,
    MediaRelativePath,
)
from kronika.domain.media_classification import AcquisitionSource, ContentCategory
from kronika.domain.media_metadata import MediaDisplayTitle
from kronika.domain.media_sidecar import SidecarDocument, SidecarLocation, encode_media_sidecar
from kronika.infrastructure.persistence.device_repository import SqliteDeviceRepository
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_metadata_repository import SqliteMediaMetadataRepository
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
MEDIA_ID_TEXT = "12345678-1234-4234-9234-123456789abc"
LOCATION_ID_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
LIBRARY_ID_TEXT = "aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"
PRIVATE_MARKER = "/home/private/secret.mp4"
PAYLOAD_MARKER = "PAYLOAD_MARKER_9f3a"
TRACEBACK_MARKER = "Traceback (most recent call last)"


def _document() -> SidecarDocument:
    return SidecarDocument(
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
            relative_path=MediaRelativePath("movies/clip.mp4"),
        ),
    )


def _leaky_payload() -> bytes:
    return PAYLOAD_MARKER.encode("utf-8") + b" " + PRIVATE_MARKER.encode("utf-8")


def _export_result(status: str) -> SidecarExportResult:
    return SidecarExportResult(status=status, document=_document(), payload=_leaky_payload())


def _parse_single_json_line(output: str) -> dict[str, Any]:
    assert output.endswith("\n")
    lines = output.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert isinstance(payload, dict)
    return payload


def _assert_sanitized(*chunks: str) -> None:
    combined = "".join(chunks)
    assert PRIVATE_MARKER not in combined
    assert PAYLOAD_MARKER not in combined
    assert TRACEBACK_MARKER not in combined
    assert "sqlalchemy" not in combined.lower()


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, str, str]:
    from kronika.adapters.cli import sidecar

    code = sidecar.main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _stub_catalog_ready(monkeypatch: pytest.MonkeyPatch, *, dispose_calls: list[object] | None = None) -> None:
    from kronika.adapters.cli import sidecar

    monkeypatch.setattr(sidecar, "load_settings", lambda: SimpleNamespace(database_path=Path(PRIVATE_MARKER)))
    monkeypatch.setattr(
        sidecar,
        "inspect_database_migration_status",
        lambda settings: SimpleNamespace(state="at_head"),
    )
    monkeypatch.setattr(sidecar, "create_sqlite_engine", lambda path: object())
    monkeypatch.setattr(sidecar, "SqliteMediaRepository", lambda engine: object())
    monkeypatch.setattr(sidecar, "SqliteLibraryRepository", lambda engine: object())
    monkeypatch.setattr(sidecar, "SqliteMediaMetadataRepository", lambda engine: object())

    def _dispose(engine: object) -> None:
        if dispose_calls is not None:
            dispose_calls.append(engine)

    monkeypatch.setattr(sidecar, "dispose_engine", _dispose)


def _install_fake_service(
    monkeypatch: pytest.MonkeyPatch,
    *,
    export_result: SidecarExportResult | None = None,
    compare_result: SidecarCompareResult | None = None,
    export_error: Exception | None = None,
    compare_error: Exception | None = None,
    validate_error: Exception | None = None,
) -> list[tuple[object, ...]]:
    from kronika.adapters.cli import sidecar

    constructed: list[tuple[object, ...]] = []

    class _FakeService:
        def __init__(self, *args: object) -> None:
            constructed.append(args)

        def export(self, media_id: object, location_id: object) -> SidecarExportResult:
            del media_id, location_id
            if export_error is not None:
                raise export_error
            assert export_result is not None
            return export_result

        def compare(self, media_id: object, location_id: object) -> SidecarCompareResult:
            del media_id, location_id
            if compare_error is not None:
                raise compare_error
            assert compare_result is not None
            return compare_result

        def validate_path(self, path: str) -> SidecarDocument:
            del path
            if validate_error is not None:
                raise validate_error
            return _document()

    monkeypatch.setattr(sidecar, "MediaSidecarService", _FakeService)
    return constructed


def test_console_script_registration_points_to_sidecar_main() -> None:
    text = (REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'framenest-sidecar = "kronika.adapters.cli.sidecar:main"' in text


def test_missing_command_is_invalid_input(capsys: pytest.CaptureFixture[str]) -> None:
    code, stdout, stderr = _run([], capsys)
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload == {
        "error_code": "SIDECAR_INVALID_INPUT",
        "message": "Invalid sidecar command.",
        "operation": "unknown",
    }


def test_unknown_command_and_unknown_option_are_invalid_input(capsys: pytest.CaptureFixture[str]) -> None:
    code, stdout, stderr = _run(["repair"], capsys)
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload["operation"] == "unknown"
    assert payload["error_code"] == "SIDECAR_INVALID_INPUT"

    code, stdout, stderr = _run(["export", "--unexpected", "1"], capsys)
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload["operation"] == "export"
    assert payload["error_code"] == "SIDECAR_INVALID_INPUT"
    _assert_sanitized(stdout, stderr)


def test_missing_required_arguments_are_invalid_input(capsys: pytest.CaptureFixture[str]) -> None:
    code, stdout, stderr = _run(["export", "--media-id", MEDIA_ID_TEXT], capsys)
    assert code == 1
    assert stdout == ""
    assert _parse_single_json_line(stderr)["error_code"] == "SIDECAR_INVALID_INPUT"

    code, stdout, stderr = _run(["validate"], capsys)
    assert code == 1
    assert stdout == ""
    assert _parse_single_json_line(stderr)["operation"] == "validate"

    code, stdout, stderr = _run(["compare", "--location-id", LOCATION_ID_TEXT], capsys)
    assert code == 1
    assert stdout == ""
    assert _parse_single_json_line(stderr)["error_code"] == "SIDECAR_INVALID_INPUT"


def test_malformed_identities_and_unexpected_positionals_are_invalid_input(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, stdout, stderr = _run(
        ["export", "--media-id", "not-a-uuid", "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload["operation"] == "export"
    assert payload["error_code"] == "SIDECAR_INVALID_INPUT"
    assert payload["message"] == "Invalid sidecar command."

    code, stdout, stderr = _run(
        ["compare", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT, "extra"],
        capsys,
    )
    assert code == 1
    assert stdout == ""
    assert _parse_single_json_line(stderr)["error_code"] == "SIDECAR_INVALID_INPUT"


def test_cli_does_not_read_interactive_input(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("input")))
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\n"))
    code, stdout, stderr = _run([], capsys)
    assert code == 1
    assert stdout == ""
    assert _parse_single_json_line(stderr)["error_code"] == "SIDECAR_INVALID_INPUT"


@pytest.mark.parametrize(
    ("status", "result_code"),
    [
        ("created", "SIDECAR_EXPORT_CREATED"),
        ("replaced", "SIDECAR_EXPORT_REPLACED"),
        ("unchanged", "SIDECAR_EXPORT_UNCHANGED"),
    ],
)
def test_export_success_json_pairs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    result_code: str,
) -> None:
    _stub_catalog_ready(monkeypatch)
    _install_fake_service(monkeypatch, export_result=_export_result(status))
    code, stdout, stderr = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 0
    assert stderr == ""
    payload = _parse_single_json_line(stdout)
    assert payload == {
        "operation": "export",
        "result": status,
        "result_code": result_code,
    }
    _assert_sanitized(stdout, stderr)


def test_validate_success_json_does_not_print_document(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    sidecar_path = tmp_path / "clip.mp4.framenest.json"
    sidecar_path.write_bytes(encode_media_sidecar(_document()))
    code, stdout, stderr = _run(["validate", "--path", str(sidecar_path)], capsys)
    assert code == 0
    assert stderr == ""
    payload = _parse_single_json_line(stdout)
    assert payload == {
        "operation": "validate",
        "result": "valid",
        "result_code": "SIDECAR_VALIDATE_VALID",
    }
    assert "display_title" not in stdout
    assert MEDIA_ID_TEXT not in stdout
    assert str(sidecar_path) not in stdout
    assert str(sidecar_path) not in stderr
    _assert_sanitized(stdout, stderr)


@pytest.mark.parametrize(
    ("status", "result_code"),
    [
        ("match", "SIDECAR_COMPARE_MATCH"),
        ("stale", "SIDECAR_COMPARE_STALE"),
        ("mismatch", "SIDECAR_COMPARE_MISMATCH"),
        ("missing", "SIDECAR_COMPARE_MISSING"),
    ],
)
def test_compare_success_json_pairs_including_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    status: str,
    result_code: str,
) -> None:
    _stub_catalog_ready(monkeypatch)
    _install_fake_service(
        monkeypatch,
        compare_result=SidecarCompareResult(status=status, error_code=result_code),
    )
    code, stdout, stderr = _run(
        ["compare", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 0
    assert stderr == ""
    payload = _parse_single_json_line(stdout)
    assert payload == {
        "operation": "compare",
        "result": status,
        "result_code": result_code,
    }
    _assert_sanitized(stdout, stderr)


def test_application_error_codes_are_preserved_on_stderr(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    _stub_catalog_ready(monkeypatch)
    _install_fake_service(
        monkeypatch,
        export_error=FrameNestMediaSidecarApplicationError(
            "Media sidecar identity conflicts.",
            error_code="SIDECAR_IDENTITY_CONFLICT",
        ),
    )
    code, stdout, stderr = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload == {
        "error_code": "SIDECAR_IDENTITY_CONFLICT",
        "message": "Media sidecar identity conflicts.",
        "operation": "export",
    }
    _assert_sanitized(stdout, stderr)


def test_catalog_not_ready_and_unexpected_fallback(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from kronika.adapters.cli import sidecar

    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(tmp_path / "missing" / "catalog.sqlite3"))
    monkeypatch.setattr(sidecar, "load_settings", lambda: KronikaSettings(
        database_path=tmp_path / "missing" / "catalog.sqlite3",
        _env_file=None,
    ))
    code, stdout, stderr = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload["operation"] == "export"
    assert payload["error_code"] == "SIDECAR_CATALOG_NOT_READY"
    assert str(tmp_path) not in stderr
    _assert_sanitized(stdout, stderr)

    _stub_catalog_ready(monkeypatch)
    _install_fake_service(
        monkeypatch,
        export_error=RuntimeError(f"boom {PRIVATE_MARKER} {PAYLOAD_MARKER}"),
    )
    code, stdout, stderr = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 1
    assert stdout == ""
    payload = _parse_single_json_line(stderr)
    assert payload == {
        "error_code": "SIDECAR_COMMAND_FAILED",
        "message": "Media sidecar command failed.",
        "operation": "export",
    }
    _assert_sanitized(stdout, stderr)


def test_validate_does_not_load_or_require_catalog(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    from kronika.adapters.cli import sidecar

    sidecar_path = tmp_path / "clip.mp4.framenest.json"
    sidecar_path.write_bytes(encode_media_sidecar(_document()))

    def _forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("catalog composition must not run for validate")

    monkeypatch.setattr(sidecar, "load_settings", _forbidden)
    monkeypatch.setattr(sidecar, "inspect_database_migration_status", _forbidden)
    monkeypatch.setattr(sidecar, "create_sqlite_engine", _forbidden)
    monkeypatch.setattr(sidecar, "SqliteMediaRepository", _forbidden)
    monkeypatch.setattr(sidecar, "SqliteLibraryRepository", _forbidden)
    monkeypatch.setattr(sidecar, "SqliteMediaMetadataRepository", _forbidden)
    monkeypatch.setattr(sidecar, "dispose_engine", _forbidden)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(tmp_path / "absent" / "catalog.sqlite3"))

    code, stdout, stderr = _run(["validate", "--path", str(sidecar_path)], capsys)
    assert code == 0
    assert stderr == ""
    assert _parse_single_json_line(stdout)["result_code"] == "SIDECAR_VALIDATE_VALID"


def test_export_and_compare_dispose_engine(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dispose_calls: list[object] = []
    _stub_catalog_ready(monkeypatch, dispose_calls=dispose_calls)
    _install_fake_service(monkeypatch, export_result=_export_result("created"))
    code, stdout, _stderr = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 0
    assert len(dispose_calls) == 1
    assert _parse_single_json_line(stdout)["result"] == "created"

    dispose_calls.clear()
    _install_fake_service(
        monkeypatch,
        compare_result=SidecarCompareResult(status="missing", error_code="SIDECAR_COMPARE_MISSING"),
    )
    code, stdout, _stderr = _run(
        ["compare", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert code == 0
    assert len(dispose_calls) == 1
    assert _parse_single_json_line(stdout)["result"] == "missing"


def _seed_catalog(tmp_path: Path) -> Path:
    database_path = tmp_path / "catalog.sqlite3"
    library_root = tmp_path / "library"
    media_dir = library_root / "movies"
    media_dir.mkdir(parents=True)
    (media_dir / "clip.mp4").write_bytes(b"synthetic-media")
    upgrade_database_to_head(KronikaSettings(database_path=database_path, _env_file=None))
    engine = create_sqlite_engine(database_path)
    try:
        devices = SqliteDeviceRepository(engine)
        libraries = SqliteLibraryRepository(engine)
        media = SqliteMediaRepository(engine)
        metadata = SqliteMediaMetadataRepository(engine)
        device = Device(id=DeviceId.new(), display_name="Test device")
        devices.add(device)
        library = Library(
            id=LibraryId.from_string(LIBRARY_ID_TEXT),
            device_id=device.id,
            display_name="Test library",
            root=LibraryRoot(flavor=LibraryPathFlavor.POSIX, path=str(library_root)),
        )
        libraries.add(library)
        media.add_media(
            LogicalMedia(
                id=MediaId.from_string(MEDIA_ID_TEXT),
                kind=MediaKind.VIDEO,
                created_at_ms=10,
                updated_at_ms=20,
            )
        )
        media.add_location(
            MediaLocation(
                id=MediaLocationId.from_string(LOCATION_ID_TEXT),
                media_id=MediaId.from_string(MEDIA_ID_TEXT),
                library_id=library.id,
                relative_path=MediaRelativePath("movies/clip.mp4"),
                availability=MediaLocationAvailability.AVAILABLE,
                observed_size_bytes=15,
                observed_mtime_ns=1,
                created_at_ms=10,
                updated_at_ms=20,
            )
        )
        metadata.save_media_metadata(
            MediaId.from_string(MEDIA_ID_TEXT),
            MediaDisplayTitle("CLI Title"),
            None,
            (),
            now_ms=40,
            content_category=ContentCategory.GENERAL,
        )
    finally:
        dispose_engine(engine)
    return database_path


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_cli_round_trip_export_validate_compare_and_stale_without_repair(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    database_path = _seed_catalog(tmp_path)
    sidecar_path = tmp_path / "library" / "movies" / "clip.mp4.framenest.json"
    tables_before = _table_names(database_path)
    monkeypatch.setenv("FRAMENEST_DATABASE_PATH", str(database_path))

    created_code, created_out, created_err = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert created_code == 0
    assert created_err == ""
    assert _parse_single_json_line(created_out) == {
        "operation": "export",
        "result": "created",
        "result_code": "SIDECAR_EXPORT_CREATED",
    }
    assert sidecar_path.is_file()
    original_bytes = sidecar_path.read_bytes()

    valid_code, valid_out, valid_err = _run(["validate", "--path", str(sidecar_path)], capsys)
    assert valid_code == 0
    assert valid_err == ""
    assert _parse_single_json_line(valid_out)["result_code"] == "SIDECAR_VALIDATE_VALID"

    match_code, match_out, match_err = _run(
        ["compare", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert match_code == 0
    assert match_err == ""
    assert _parse_single_json_line(match_out) == {
        "operation": "compare",
        "result": "match",
        "result_code": "SIDECAR_COMPARE_MATCH",
    }

    inode_before = sidecar_path.stat().st_ino
    unchanged_code, unchanged_out, unchanged_err = _run(
        ["export", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert unchanged_code == 0
    assert unchanged_err == ""
    assert _parse_single_json_line(unchanged_out)["result"] == "unchanged"
    assert sidecar_path.stat().st_ino == inode_before
    assert sidecar_path.read_bytes() == original_bytes

    engine = create_sqlite_engine(database_path)
    try:
        SqliteMediaMetadataRepository(engine).save_media_metadata(
            MediaId.from_string(MEDIA_ID_TEXT),
            MediaDisplayTitle("Updated CLI Title"),
            None,
            (),
            now_ms=80,
            content_category=ContentCategory.GENERAL,
        )
    finally:
        dispose_engine(engine)

    stale_code, stale_out, stale_err = _run(
        ["compare", "--media-id", MEDIA_ID_TEXT, "--location-id", LOCATION_ID_TEXT],
        capsys,
    )
    assert stale_code == 0
    assert stale_err == ""
    assert _parse_single_json_line(stale_out) == {
        "operation": "compare",
        "result": "stale",
        "result_code": "SIDECAR_COMPARE_STALE",
    }
    assert sidecar_path.read_bytes() == original_bytes
    tables_after = _table_names(database_path)
    assert tables_after == tables_before
    assert not any("sidecar" in name for name in tables_after)
    _assert_sanitized(created_out, valid_out, match_out, unchanged_out, stale_out)
    assert str(database_path) not in created_out + stale_out
    assert str(tmp_path / "library") not in created_out + stale_out
