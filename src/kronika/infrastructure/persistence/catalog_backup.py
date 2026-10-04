"""Safe catalog backup, verification, and restore operations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
from typing import Literal

from importlib.metadata import PackageNotFoundError, version

MANIFEST_NAME = "manifest.json"
CATALOG_NAME = "catalog.sqlite3"
FORMAT_VERSION = 1
SHA256_PATTERN_LENGTH = 64
MAX_CATALOG_SIZE_BYTES = 16 * 1024 * 1024 * 1024 * 1024
TEMP_PREFIX = ".framenest-backup-"
EXPECTED_BUNDLE_NAMES = frozenset({MANIFEST_NAME, CATALOG_NAME})
APPLICATION_NAME = "framenest"
#: Accepted read-only application names. Only the writer above emits
#: ``APPLICATION_NAME`` until the durable-writer cut adopts the Kronika spelling.
COMPATIBLE_APPLICATION_NAME = "kronika"
ACCEPTED_APPLICATION_NAMES = frozenset({APPLICATION_NAME, COMPATIBLE_APPLICATION_NAME})
APPLICATION_VERSION_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,79}")
ALEMBIC_REVISION_PATTERN = re.compile(r"[0-9]{4}")

BackupState = Literal["created", "verified", "restored"]


class BackupError(RuntimeError):
    """Sanitized backup failure."""

    def __init__(self, message: str, *, error_code: str = "BACKUP_FAILED") -> None:
        super().__init__(message)
        self.error_code = error_code


@dataclass(frozen=True, slots=True)
class BackupResult:
    """Safe backup operation result."""

    state: BackupState
    catalog_size_bytes: int
    catalog_sha256: str
    alembic_revision: str


def create_catalog_backup(source_database: Path | str, output_bundle: Path | str) -> BackupResult:
    """Create and verify a catalog backup bundle without mutating the source."""
    source = _existing_regular_file(source_database, description="source database")
    bundle = _new_bundle_path(output_bundle)
    parent = _existing_directory(bundle.parent, description="output parent")
    temp_bundle = parent / f"{TEMP_PREFIX}{bundle.name}.{secrets.token_hex(8)}"
    try:
        temp_bundle.mkdir(mode=0o700)
        _private_directory(temp_bundle)
        snapshot_path = temp_bundle / CATALOG_NAME
        _sqlite_online_backup(source, snapshot_path)
        _private_file(snapshot_path)
        _verify_sqlite_integrity(snapshot_path)
        revision = _catalog_revision(snapshot_path)
        digest = sha256_file(snapshot_path)
        size = snapshot_path.stat().st_size
        manifest = _build_manifest(
            catalog_size_bytes=size,
            catalog_sha256=digest,
            alembic_revision=revision,
        )
        _atomic_write_manifest(temp_bundle / MANIFEST_NAME, manifest)
        result = verify_catalog_backup(temp_bundle)
        if bundle.exists() or bundle.is_symlink():
            raise BackupError("Backup output already exists.", error_code="OUTPUT_EXISTS")
        _publish_bundle_no_replace(temp_bundle, bundle)
        return BackupResult(
            state="created",
            catalog_size_bytes=result.catalog_size_bytes,
            catalog_sha256=result.catalog_sha256,
            alembic_revision=result.alembic_revision,
        )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Catalog backup could not be created.") from exc
    finally:
        if temp_bundle != Path():
            _remove_owned_temp_bundle(temp_bundle)


def verify_catalog_backup(bundle: Path | str) -> BackupResult:
    """Verify a catalog backup bundle without modifying it."""
    bundle_path = _existing_directory(bundle, description="backup bundle")
    _reject_unexpected_bundle_state(bundle_path)
    manifest_path = _existing_regular_file(bundle_path / MANIFEST_NAME, description="manifest")
    catalog_path = _existing_regular_file(bundle_path / CATALOG_NAME, description="catalog artifact")
    manifest = _load_manifest(manifest_path)
    catalog = manifest["catalog"]
    assert isinstance(catalog, dict)
    expected_name = catalog["logical_name"]
    if expected_name != CATALOG_NAME:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    expected_size = catalog["size_bytes"]
    expected_sha256 = catalog["sha256"]
    expected_revision = catalog["alembic_revision"]
    if catalog_path.stat().st_size != expected_size:
        raise BackupError("Backup catalog size mismatch.", error_code="CATALOG_SIZE_MISMATCH")
    observed_sha256 = sha256_file(catalog_path)
    if observed_sha256 != expected_sha256:
        raise BackupError("Backup catalog checksum mismatch.", error_code="CATALOG_CHECKSUM_MISMATCH")
    _verify_sqlite_integrity(catalog_path)
    observed_revision = _catalog_revision(catalog_path)
    if observed_revision != expected_revision:
        raise BackupError("Backup catalog revision mismatch.", error_code="CATALOG_REVISION_MISMATCH")
    return BackupResult(
        state="verified",
        catalog_size_bytes=int(expected_size),
        catalog_sha256=str(expected_sha256),
        alembic_revision=str(expected_revision),
    )


def restore_catalog_backup(bundle: Path | str, destination_database: Path | str) -> BackupResult:
    """Restore a verified catalog backup to a new destination file only."""
    verified = verify_catalog_backup(bundle)
    bundle_path = Path(bundle)
    source_catalog = bundle_path / CATALOG_NAME
    destination = _new_destination_path(destination_database)
    parent = _prepare_destination_parent(destination)
    fd = -1
    temp_name = ""
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=str(parent),
        )
        temp_path = Path(temp_name)
        _private_file(temp_path)
        with source_catalog.open("rb") as source, os.fdopen(fd, "wb") as target:
            fd = -1
            shutil.copyfileobj(source, target, length=1024 * 1024)
            target.flush()
            os.fsync(target.fileno())
        _private_file(temp_path)
        if sha256_file(temp_path) != verified.catalog_sha256:
            raise BackupError("Restored catalog checksum mismatch.", error_code="RESTORE_CHECKSUM_MISMATCH")
        _verify_sqlite_integrity(temp_path)
        if _catalog_revision(temp_path) != verified.alembic_revision:
            raise BackupError("Restored catalog revision mismatch.", error_code="RESTORE_REVISION_MISMATCH")
        if destination.exists() or destination.is_symlink():
            raise BackupError("Restore destination already exists.", error_code="DESTINATION_EXISTS")
        _publish_file_no_replace(temp_path, destination)
        # Hardlink publish leaves both names on the same inode. Remove the
        # implementation-private temporary sibling so a successful restore
        # leaves only the requested destination.
        _unlink_owned_temp_file(temp_path)
        if temp_path.exists():
            raise BackupError(
                "Restore temporary could not be removed.",
                error_code="RESTORE_TEMP_CLEANUP_FAILED",
            )
        temp_path = None
        return BackupResult(
            state="restored",
            catalog_size_bytes=verified.catalog_size_bytes,
            catalog_sha256=verified.catalog_sha256,
            alembic_revision=verified.alembic_revision,
        )
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("Catalog backup could not be restored.") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            _unlink_owned_temp_file(temp_path)


def sha256_file(path: Path) -> str:
    """Return a SHA-256 digest for a regular file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sqlite_online_backup(source: Path, destination: Path) -> None:
    try:
        with sqlite3.connect(_sqlite_readonly_uri(source), uri=True) as source_connection:
            with sqlite3.connect(destination) as destination_connection:
                source_connection.backup(destination_connection, pages=128, sleep=0.050)
                destination_connection.execute("PRAGMA wal_checkpoint(PASSIVE)")
    except sqlite3.Error as exc:
        raise BackupError("SQLite snapshot failed.", error_code="SQLITE_BACKUP_FAILED") from exc


def _verify_sqlite_integrity(path: Path) -> None:
    try:
        with sqlite3.connect(_sqlite_readonly_uri(path), uri=True) as connection:
            rows = connection.execute("PRAGMA integrity_check").fetchall()
            if rows != [("ok",)]:
                raise BackupError("SQLite integrity check failed.", error_code="SQLITE_INTEGRITY_FAILED")
            foreign_key_rows = connection.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_rows:
                raise BackupError("SQLite foreign-key check failed.", error_code="SQLITE_FOREIGN_KEY_FAILED")
    except BackupError:
        raise
    except sqlite3.Error as exc:
        raise BackupError("SQLite integrity check failed.", error_code="SQLITE_INTEGRITY_FAILED") from exc


def _catalog_revision(path: Path) -> str:
    try:
        with sqlite3.connect(_sqlite_readonly_uri(path), uri=True) as connection:
            row = connection.execute("SELECT version_num FROM alembic_version").fetchone()
    except sqlite3.Error as exc:
        raise BackupError("Catalog revision is unavailable.", error_code="CATALOG_REVISION_UNAVAILABLE") from exc
    if row is None or not isinstance(row[0], str) or not row[0]:
        raise BackupError("Catalog revision is unavailable.", error_code="CATALOG_REVISION_UNAVAILABLE")
    return row[0]


def _build_manifest(
    *,
    catalog_size_bytes: int,
    catalog_sha256: str,
    alembic_revision: str,
) -> dict[str, object]:
    return {
        "schema_version": FORMAT_VERSION,
        "created_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "application": {
            "name": APPLICATION_NAME,
            "version": _application_version(),
        },
        "algorithms": {
            "digest": "sha256",
            "sqlite_integrity": "pragma_integrity_check",
        },
        "catalog": {
            "logical_name": CATALOG_NAME,
            "size_bytes": catalog_size_bytes,
            "sha256": catalog_sha256,
            "alembic_revision": alembic_revision,
        },
        "included_state": ["catalog_database"],
        "excluded_state": ["gallery_preview_cache", "original_media", "secrets", "non_secret_ai_configuration"],
    }


def _application_version() -> str:
    try:
        return version("kronika")
    except PackageNotFoundError:
        return "unknown"


def _atomic_write_manifest(path: Path, payload: dict[str, object]) -> None:
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"
    fd = -1
    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
        temp_path = Path(temp_name)
        _private_file(temp_path)
        with os.fdopen(fd, "wb") as handle:
            fd = -1
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
        _private_file(path)
    except OSError as exc:
        raise BackupError("Backup manifest could not be written.", error_code="MANIFEST_WRITE_FAILED") from exc
    finally:
        if fd >= 0:
            os.close(fd)
        if temp_path is not None:
            _unlink_owned_temp_file(temp_path)


def _load_manifest(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED") from exc
    if not isinstance(payload, dict):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    _validate_manifest(payload)
    return payload


def _validate_manifest(payload: dict[str, object]) -> None:
    expected_keys = {
        "schema_version",
        "created_at_utc",
        "application",
        "algorithms",
        "catalog",
        "included_state",
        "excluded_state",
    }
    if set(payload) != expected_keys or payload.get("schema_version") != FORMAT_VERSION:
        raise BackupError("Backup manifest version is unsupported.", error_code="MANIFEST_UNSUPPORTED")
    if not _is_canonical_utc_timestamp(payload["created_at_utc"]):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    application = payload["application"]
    if not isinstance(application, dict) or set(application) != {"name", "version"}:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    for key in ("name", "version"):
        item = application[key]
        if not isinstance(item, str) or not item:
            raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if application["name"] not in ACCEPTED_APPLICATION_NAMES:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if not _is_application_version(application["version"]):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    _validate_string_map(
        payload["algorithms"],
        {"digest": "sha256", "sqlite_integrity": "pragma_integrity_check"},
    )
    catalog = payload["catalog"]
    if not isinstance(catalog, dict) or set(catalog) != {
        "logical_name",
        "size_bytes",
        "sha256",
        "alembic_revision",
    }:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if catalog["logical_name"] != CATALOG_NAME:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if (
        not isinstance(catalog["size_bytes"], int)
        or catalog["size_bytes"] <= 0
        or catalog["size_bytes"] > MAX_CATALOG_SIZE_BYTES
    ):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if not _is_sha256(catalog["sha256"]):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if not isinstance(catalog["alembic_revision"], str) or not ALEMBIC_REVISION_PATTERN.fullmatch(
        catalog["alembic_revision"],
    ):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if payload["included_state"] != ["catalog_database"]:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    if payload["excluded_state"] != [
        "gallery_preview_cache",
        "original_media",
        "secrets",
        "non_secret_ai_configuration",
    ]:
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")


def _validate_string_map(value: object, expected: dict[str, str | None]) -> None:
    if not isinstance(value, dict) or set(value) != set(expected):
        raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
    for key, expected_value in expected.items():
        item = value[key]
        if not isinstance(item, str) or not item:
            raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")
        if expected_value is not None and item != expected_value:
            raise BackupError("Backup manifest is malformed.", error_code="MANIFEST_MALFORMED")


def _is_sha256(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == SHA256_PATTERN_LENGTH
        and all(character in "0123456789abcdef" for character in value)
    )


def _is_canonical_utc_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
    except ValueError:
        return False
    return parsed.isoformat().replace("+00:00", "Z") == value


def _is_application_version(value: object) -> bool:
    return isinstance(value, str) and APPLICATION_VERSION_PATTERN.fullmatch(value) is not None


def _sqlite_readonly_uri(path: Path) -> str:
    return f"{path.as_uri()}?mode=ro"


def _existing_regular_file(path_like: Path | str, *, description: str) -> Path:
    path = Path(path_like).expanduser()
    if path.is_symlink():
        raise BackupError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    resolved = _absolute(path, description=description)
    if resolved.is_symlink() or not resolved.exists() or not resolved.is_file():
        raise BackupError(f"Invalid {description}.", error_code="INVALID_PATH")
    return resolved


def _existing_directory(path_like: Path | str, *, description: str) -> Path:
    path = Path(path_like).expanduser()
    if path.is_symlink():
        raise BackupError(f"Unsafe {description}.", error_code="UNSAFE_PATH")
    resolved = _absolute(path, description=description)
    if resolved.is_symlink() or not resolved.exists() or not resolved.is_dir():
        raise BackupError(f"Invalid {description}.", error_code="INVALID_PATH")
    return resolved


def _new_bundle_path(path_like: Path | str) -> Path:
    path = _absolute(Path(path_like).expanduser(), description="backup output")
    if path.exists() or path.is_symlink():
        raise BackupError("Backup output already exists.", error_code="OUTPUT_EXISTS")
    return path


def _new_destination_path(path_like: Path | str) -> Path:
    original = Path(path_like).expanduser()
    if original.is_symlink():
        raise BackupError("Restore destination is unsafe.", error_code="UNSAFE_PATH")
    path = _absolute(original, description="restore destination")
    if path.exists() or path.is_symlink():
        raise BackupError("Restore destination already exists.", error_code="DESTINATION_EXISTS")
    return path


def _prepare_destination_parent(destination: Path) -> Path:
    parent = destination.parent
    if parent.exists() and parent.is_symlink():
        raise BackupError("Restore destination parent is unsafe.", error_code="UNSAFE_PATH")
    if parent.exists() and not parent.is_dir():
        raise BackupError("Restore destination parent is invalid.", error_code="INVALID_PATH")
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    if parent.is_symlink():
        raise BackupError("Restore destination parent is unsafe.", error_code="UNSAFE_PATH")
    return parent


def _absolute(path: Path, *, description: str) -> Path:
    if not path.is_absolute():
        raise BackupError(f"{description.title()} must be absolute.", error_code="INVALID_PATH")
    return path.resolve(strict=False)


def _reject_unexpected_bundle_state(bundle: Path) -> None:
    observed_names = {child.name for child in bundle.iterdir()}
    if any(name.startswith(TEMP_PREFIX) or name.endswith(".tmp") for name in observed_names):
        raise BackupError("Backup bundle contains incomplete state.", error_code="INCOMPLETE_BUNDLE")
    if not observed_names.issubset(EXPECTED_BUNDLE_NAMES):
        raise BackupError("Backup bundle contains unexpected state.", error_code="UNEXPECTED_BUNDLE_STATE")


def _private_directory(path: Path) -> None:
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        if os.name != "nt":
            raise BackupError("Backup permissions could not be restricted.", error_code="PERMISSION_FAILED") from exc


def _private_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError as exc:
        if os.name != "nt":
            raise BackupError("Backup permissions could not be restricted.", error_code="PERMISSION_FAILED") from exc


def _publish_bundle_no_replace(temp_bundle: Path, bundle: Path) -> None:
    created_final = False
    try:
        os.mkdir(bundle, 0o700)
        created_final = True
        _private_directory(bundle)
        _publish_file_no_replace(temp_bundle / MANIFEST_NAME, bundle / MANIFEST_NAME)
        _publish_file_no_replace(temp_bundle / CATALOG_NAME, bundle / CATALOG_NAME)
        verify_catalog_backup(bundle)
    except FileExistsError as exc:
        raise BackupError("Backup output already exists.", error_code="OUTPUT_EXISTS") from exc
    except BackupError:
        if created_final:
            _remove_owned_final_bundle(bundle)
        raise
    except OSError as exc:
        if created_final:
            _remove_owned_final_bundle(bundle)
        raise BackupError("Backup output could not be published.", error_code="OUTPUT_PUBLISH_FAILED") from exc
    _remove_owned_temp_bundle(temp_bundle)


def _publish_file_no_replace(source: Path, destination: Path) -> None:
    try:
        os.link(source, destination, follow_symlinks=False)
        _private_file(destination)
    except FileExistsError as exc:
        raise BackupError("Restore destination already exists.", error_code="DESTINATION_EXISTS") from exc
    except BackupError:
        _unlink_created_output_file(destination)
        raise
    except OSError as exc:
        _unlink_created_output_file(destination)
        raise BackupError("Output file could not be published.", error_code="OUTPUT_PUBLISH_FAILED") from exc


def _remove_owned_final_bundle(path: Path) -> None:
    for name in EXPECTED_BUNDLE_NAMES:
        child = path / name
        _unlink_created_output_file(child)
    try:
        path.rmdir()
    except OSError:
        pass


def _unlink_created_output_file(path: Path) -> None:
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
    except OSError:
        pass


def _remove_owned_temp_bundle(path: Path) -> None:
    if not path.name.startswith(TEMP_PREFIX) or path.is_symlink() or not path.exists():
        return
    try:
        shutil.rmtree(path)
    except OSError:
        pass


def _unlink_owned_temp_file(path: Path) -> None:
    if path.is_symlink() or not path.name.startswith(".") or not path.name.endswith(".tmp"):
        return
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
