"""Bounded migration of owned local development and AI state to canonical paths.

This module implements the ``migrate-identity-paths check|apply`` maintenance
operation for the ordered identity cut sequence. It resolves the legacy local
state locations the current defaults still use -- the macOS Application Support
and Logs trees, the Linux/XDG data, state and configuration trees, and the
temporary development root -- against their canonical counterparts, and it
copies owned durable state forward without ever overwriting an occupied
destination, reviving a managed-process record, or touching capture state,
browser profiles, credentials or source media.

The defaults themselves do not move in this cut. Each canonical destination is
computed here from the same platform, XDG and temporary-root rules the current
defaults use, so the two never depend on each other, and every operation
records the verified source/destination mapping in a private receipt outside
the repository. ``refuse_silent_legacy_state_replacement`` is the reusable
safeguard the later default switch retains: a canonical destination that is
absent while its matching legacy state exists refuses to become the silent
empty replacement.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import stat
import sys
import tempfile
import time
from typing import Any, Callable, Literal, Mapping

from kronika.identity_env import (
    IdentityEnvironmentConflictError,
    IdentityEnvironmentConflictFailure,
    lookup_env,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    create_sqlite_readonly_engine,
    dispose_engine,
)
from kronika.infrastructure.runtime.development import (
    LOCK_FILENAME,
    STATE_FILENAME,
    DevelopmentRuntime,
    DevelopmentRuntimeError,
)

MIGRATION_RECEIPT_SCHEMA = 1

#: The canonical and legacy directory-name spellings this cut owns. Only these
#: three pairs may differ between a source and its destination, so the mapping
#: cannot silently rename an unrelated path component and a path outside the
#: owned classes cannot be expressed as an owned mapping.
CANONICAL_APP_DIRECTORY_NAME = "Kronika"
CANONICAL_LOWER_DIRECTORY_NAME = "kronika"
LEGACY_APP_DIRECTORY_NAME = "FrameNest"
LEGACY_LOWER_DIRECTORY_NAME = "framenest"
LEGACY_TEMP_DIRECTORY_NAME = "framenest-development"
CANONICAL_TEMP_DIRECTORY_NAME = "kronika-development"

_ALLOWED_BRAND_COMPONENT_PAIRS = frozenset(
    {
        (LEGACY_APP_DIRECTORY_NAME, CANONICAL_APP_DIRECTORY_NAME),
        (LEGACY_LOWER_DIRECTORY_NAME, CANONICAL_LOWER_DIRECTORY_NAME),
        (LEGACY_TEMP_DIRECTORY_NAME, CANONICAL_TEMP_DIRECTORY_NAME),
    }
)

DATABASE_PATH_SUFFIX = "DATABASE_PATH"
RUNTIME_DIRECTORY_SUFFIX = "DEVELOPMENT_RUNTIME_DIR"
LOG_DIRECTORY_SUFFIX = "DEVELOPMENT_LOG_DIR"
RUNTIME_SETTINGS_PATH_SUFFIX = "RUNTIME_SETTINGS_PATH"
GALLERY_PREVIEW_CACHE_PATH_SUFFIX = "GALLERY_PREVIEW_CACHE_PATH"
COVER_STORAGE_ROOT_SUFFIX = "COVER_STORAGE_ROOT"
COVER_THUMBNAIL_CACHE_PATH_SUFFIX = "COVER_THUMBNAIL_CACHE_PATH"
AI_CONFIG_PATH_SUFFIX = "AI_CONFIG_PATH"

DATABASE_FILENAME = "catalog.sqlite3"
RUNTIME_SETTINGS_FILENAME = "runtime-settings.json"
GALLERY_PREVIEW_DIRECTORY_NAME = "gallery-previews"
COVER_STORAGE_DIRECTORY_NAME = "covers"
COVER_THUMBNAILS_DIRECTORY_NAME = "cover-thumbnails"
AI_CONFIG_FILENAME = "config.json"
AI_TEST_STATE_FILENAME = "test-state.json"
AI_STATUS_SNAPSHOT_FILENAME = "status-snapshot.json"
DEVELOPMENT_DIRECTORY_NAME = "development"
DEVELOPMENT_RUNTIME_DIRECTORY_NAME = "runtime"
DEVELOPMENT_LOG_DIRECTORY_NAME = "logs"
AI_DIRECTORY_NAME = "ai"

RECEIPT_DIRECTORY_NAME = "local-state-migration"
CHECK_RECEIPT_FILENAME = "identity-paths-check.json"
APPLY_RECEIPT_FILENAME = "identity-paths-apply.json"

OVERRIDE_STATUS = "explicit-override"
SOURCE_ABSENT_STATUS = "source-absent"
COPY_READY_STATUS = "copy-ready"
DESTINATION_OCCUPIED_STATUS = "destination-occupied"
DESTINATION_OCCUPIED_REFUSED_STATUS = "destination-occupied-refused"
SOURCE_SYMLINK_REFUSED_STATUS = "source-symlink-refused"
COPY_REFUSED_STATUS = "copy-refused"
NO_DURABLE_CONTENT_STATUS = "no-durable-content"
COPIED_VERIFIED_STATUS = "copied-verified"
ALREADY_VERIFIED_STATUS = "already-verified"

_VERIFIED_STATUSES = frozenset({COPIED_VERIFIED_STATUS, ALREADY_VERIFIED_STATUS})

_MACOS_CLASS_KEYS = frozenset(
    {
        "macos_development_database",
        "macos_development_runtime",
        "macos_development_logs",
        "macos_development_runtime_settings",
        "macos_ai_configuration",
        "macos_ai_test_state",
        "macos_ai_status_snapshot",
        "temporary_development_database",
        "temporary_development_runtime_settings",
        "temporary_development_gallery_previews",
        "temporary_development_covers",
        "temporary_development_cover_thumbnails",
    }
)

_XDG_CLASS_KEYS = frozenset(
    {
        "xdg_development_database",
        "xdg_development_runtime",
        "xdg_development_logs",
        "xdg_development_runtime_settings",
        "xdg_ai_configuration",
        "xdg_ai_test_state",
        "xdg_ai_status_snapshot",
        "temporary_development_database",
        "temporary_development_runtime_settings",
        "temporary_development_gallery_previews",
        "temporary_development_covers",
        "temporary_development_cover_thumbnails",
    }
)


class LocalStateMigrationError(Exception):
    """Sanitized identity-path migration failure safe for operator output."""


class LocalStateMigrationIdentityError(
    IdentityEnvironmentConflictFailure,
    LocalStateMigrationError,
):
    """Migration failure caused by one setting name set under both prefixes.

    The message is the resolver's sanitized conflict sentence; the class
    carries the single fail-closed exit status so the command line maps it the
    same way every other entry point does.
    """


class ManagedDevelopmentServerActiveError(LocalStateMigrationError):
    """Migration refused while a live managed development process may write."""


class LegacyStateReplacementRefusal(LocalStateMigrationError):
    """Canonical destination absent while its matching legacy state exists."""


class _EntryCopyRefused(Exception):
    """Internal signal that one entry cannot be copied safely."""

    def __init__(self, status: str) -> None:
        super().__init__(status)
        self.status = status


EntryKind = Literal["database", "file", "directory"]


@dataclass(frozen=True, slots=True)
class MigrationEntry:
    """One owned source/destination pair and its copy policy."""

    key: str
    kind: EntryKind
    source: Path
    destination: Path
    override_suffix: str
    excluded_names: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class MigrationPlan:
    """The complete owned mapping for one platform, validated on construction."""

    platform: str
    entries: tuple[MigrationEntry, ...]

    def validate(self) -> "MigrationPlan":
        """Fail loudly on ambiguity, duplication or an unowned mapping.

        Every check names only the class or the rule, never a source or
        destination path, so a bad plan can never leak a private location
        through the raised message.
        """
        keys = [entry.key for entry in self.entries]
        if len(keys) != len(set(keys)):
            raise LocalStateMigrationError("The migration plan duplicates a class key.")
        sources = [str(entry.source) for entry in self.entries]
        if len(sources) != len(set(sources)):
            raise LocalStateMigrationError(
                "The migration plan duplicates a source location."
            )
        destinations = [str(entry.destination) for entry in self.entries]
        if len(destinations) != len(set(destinations)):
            raise LocalStateMigrationError(
                "The migration plan duplicates a destination location."
            )
        if set(keys) != _owned_class_keys(self.platform):
            raise LocalStateMigrationError(
                "The migration plan carries a class outside the owned local "
                "development and AI state."
            )
        for entry in self.entries:
            if not entry.source.is_absolute() or not entry.destination.is_absolute():
                raise LocalStateMigrationError(
                    "Migration locations must be absolute paths."
                )
            source_parts = entry.source.parts
            destination_parts = entry.destination.parts
            if len(source_parts) != len(destination_parts):
                raise LocalStateMigrationError(
                    "A migration mapping changes the path depth of an owned class."
                )
            differences = [
                (source_part, destination_part)
                for source_part, destination_part in zip(source_parts, destination_parts)
                if source_part != destination_part
            ]
            if len(differences) != 1 or differences[0] not in _ALLOWED_BRAND_COMPONENT_PAIRS:
                raise LocalStateMigrationError(
                    "A migration mapping does not replace exactly one owned "
                    "brand component."
                )
        return self

    def by_key(self, key: str) -> MigrationEntry:
        for entry in self.entries:
            if entry.key == key:
                return entry
        raise KeyError(key)


@dataclass(frozen=True, slots=True)
class EntryOutcome:
    """One class's resolved state for a check or apply operation."""

    key: str
    status: str
    source: Path
    destination: Path
    evidence: Mapping[str, Any] | None = None


@dataclass(frozen=True, slots=True)
class MigrationReport:
    """The sanitized result of one check or apply operation."""

    operation: str
    platform: str
    outcomes: tuple[EntryOutcome, ...]
    receipt_path: Path

    @property
    def refused_keys(self) -> tuple[str, ...]:
        return tuple(
            outcome.key for outcome in self.outcomes if "refused" in outcome.status
        )

    @property
    def counts(self) -> dict[str, int]:
        statuses = [outcome.status for outcome in self.outcomes]
        overridden = statuses.count(OVERRIDE_STATUS)
        sources_absent = statuses.count(SOURCE_ABSENT_STATUS)
        return {
            "classes": len(statuses),
            "overridden": overridden,
            "sources_present": len(statuses) - overridden - sources_absent,
            "sources_absent": sources_absent,
            "destinations_absent": statuses.count(COPY_READY_STATUS),
            "destinations_occupied": statuses.count(DESTINATION_OCCUPIED_STATUS),
            "copied_verified": statuses.count(COPIED_VERIFIED_STATUS),
            "already_verified": statuses.count(ALREADY_VERIFIED_STATUS),
            "no_durable_content": statuses.count(NO_DURABLE_CONTENT_STATUS),
            "refused": sum(1 for status in statuses if "refused" in status),
        }


def _flavor(platform: str) -> str:
    return "darwin" if platform == "darwin" else "xdg"


def _owned_class_keys(platform: str) -> frozenset[str]:
    return _MACOS_CLASS_KEYS if _flavor(platform) == "darwin" else _XDG_CLASS_KEYS


def _resolved(path: Path) -> Path:
    return path.resolve(strict=False)


def _lexists(path: Path) -> bool:
    return os.path.lexists(path)


def _xdg_directory(env: Mapping[str, str], name: str, default: Path) -> Path:
    """Resolve one XDG base directory exactly as the runtime defaults do."""
    value = env.get(name)
    if not value:
        return _resolved(default)
    path = Path(value)
    if not path.is_absolute():
        raise LocalStateMigrationError(f"{name} must be an absolute path.")
    return _resolved(path)


def _config_home(env: Mapping[str, str], home_directory: Path) -> Path:
    """Resolve the AI configuration home exactly as the default does."""
    value = env.get("XDG_CONFIG_HOME")
    if value:
        path = Path(value).expanduser()
        if not path.is_absolute():
            raise LocalStateMigrationError("XDG_CONFIG_HOME must be an absolute path.")
        return _resolved(path)
    return home_directory / ".config"


def _temporary_root(temp_root: Path | None) -> Path:
    if temp_root is None:
        return _resolved(Path(tempfile.gettempdir()))
    return _resolved(Path(temp_root).expanduser())


def _macos_entries(home_directory: Path) -> list[MigrationEntry]:
    application_support = home_directory / "Library" / "Application Support"
    development_root = (
        application_support / LEGACY_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    )
    development_destination = (
        application_support / CANONICAL_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    )
    logs_root = (
        home_directory
        / "Library"
        / "Logs"
        / LEGACY_APP_DIRECTORY_NAME
        / DEVELOPMENT_DIRECTORY_NAME
    )
    logs_destination = (
        home_directory
        / "Library"
        / "Logs"
        / CANONICAL_APP_DIRECTORY_NAME
        / DEVELOPMENT_DIRECTORY_NAME
    )
    ai_root = application_support / LEGACY_APP_DIRECTORY_NAME / AI_DIRECTORY_NAME
    ai_destination = application_support / CANONICAL_APP_DIRECTORY_NAME / AI_DIRECTORY_NAME
    return [
        MigrationEntry(
            key="macos_development_database",
            kind="database",
            source=_resolved(development_root / DATABASE_FILENAME),
            destination=_resolved(development_destination / DATABASE_FILENAME),
            override_suffix=DATABASE_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="macos_development_runtime",
            kind="directory",
            source=_resolved(development_root / DEVELOPMENT_RUNTIME_DIRECTORY_NAME),
            destination=_resolved(
                development_destination / DEVELOPMENT_RUNTIME_DIRECTORY_NAME
            ),
            override_suffix=RUNTIME_DIRECTORY_SUFFIX,
            excluded_names=(STATE_FILENAME, LOCK_FILENAME),
        ),
        MigrationEntry(
            key="macos_development_logs",
            kind="directory",
            source=_resolved(logs_root),
            destination=_resolved(logs_destination),
            override_suffix=LOG_DIRECTORY_SUFFIX,
        ),
        MigrationEntry(
            key="macos_development_runtime_settings",
            kind="file",
            source=_resolved(development_root / RUNTIME_SETTINGS_FILENAME),
            destination=_resolved(development_destination / RUNTIME_SETTINGS_FILENAME),
            override_suffix=RUNTIME_SETTINGS_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="macos_ai_configuration",
            kind="file",
            source=ai_root / AI_CONFIG_FILENAME,
            destination=ai_destination / AI_CONFIG_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="macos_ai_test_state",
            kind="file",
            source=ai_root / AI_TEST_STATE_FILENAME,
            destination=ai_destination / AI_TEST_STATE_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="macos_ai_status_snapshot",
            kind="file",
            source=ai_root / AI_STATUS_SNAPSHOT_FILENAME,
            destination=ai_destination / AI_STATUS_SNAPSHOT_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
    ]


def _xdg_entries(
    env: Mapping[str, str],
    home_directory: Path,
) -> list[MigrationEntry]:
    data_root = _xdg_directory(env, "XDG_DATA_HOME", home_directory / ".local" / "share")
    state_root = _xdg_directory(env, "XDG_STATE_HOME", home_directory / ".local" / "state")
    config_root = _config_home(env, home_directory)
    development_data_root = data_root / LEGACY_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    development_data_destination = (
        data_root / CANONICAL_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    )
    development_state_root = state_root / LEGACY_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    development_state_destination = (
        state_root / CANONICAL_APP_DIRECTORY_NAME / DEVELOPMENT_DIRECTORY_NAME
    )
    ai_root = config_root / LEGACY_LOWER_DIRECTORY_NAME / AI_DIRECTORY_NAME
    ai_destination = config_root / CANONICAL_LOWER_DIRECTORY_NAME / AI_DIRECTORY_NAME
    return [
        MigrationEntry(
            key="xdg_development_database",
            kind="database",
            source=_resolved(development_data_root / DATABASE_FILENAME),
            destination=_resolved(development_data_destination / DATABASE_FILENAME),
            override_suffix=DATABASE_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="xdg_development_runtime",
            kind="directory",
            source=_resolved(development_state_root / DEVELOPMENT_RUNTIME_DIRECTORY_NAME),
            destination=_resolved(
                development_state_destination / DEVELOPMENT_RUNTIME_DIRECTORY_NAME
            ),
            override_suffix=RUNTIME_DIRECTORY_SUFFIX,
            excluded_names=(STATE_FILENAME, LOCK_FILENAME),
        ),
        MigrationEntry(
            key="xdg_development_logs",
            kind="directory",
            source=_resolved(development_state_root / DEVELOPMENT_LOG_DIRECTORY_NAME),
            destination=_resolved(
                development_state_destination / DEVELOPMENT_LOG_DIRECTORY_NAME
            ),
            override_suffix=LOG_DIRECTORY_SUFFIX,
        ),
        MigrationEntry(
            key="xdg_development_runtime_settings",
            kind="file",
            source=_resolved(development_data_root / RUNTIME_SETTINGS_FILENAME),
            destination=_resolved(development_data_destination / RUNTIME_SETTINGS_FILENAME),
            override_suffix=RUNTIME_SETTINGS_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="xdg_ai_configuration",
            kind="file",
            source=ai_root / AI_CONFIG_FILENAME,
            destination=ai_destination / AI_CONFIG_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="xdg_ai_test_state",
            kind="file",
            source=ai_root / AI_TEST_STATE_FILENAME,
            destination=ai_destination / AI_TEST_STATE_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="xdg_ai_status_snapshot",
            kind="file",
            source=ai_root / AI_STATUS_SNAPSHOT_FILENAME,
            destination=ai_destination / AI_STATUS_SNAPSHOT_FILENAME,
            override_suffix=AI_CONFIG_PATH_SUFFIX,
        ),
    ]


def _temporary_entries(temporary_root: Path) -> list[MigrationEntry]:
    development_root = temporary_root / LEGACY_TEMP_DIRECTORY_NAME
    development_destination = temporary_root / CANONICAL_TEMP_DIRECTORY_NAME
    return [
        MigrationEntry(
            key="temporary_development_database",
            kind="database",
            source=_resolved(development_root / DATABASE_FILENAME),
            destination=_resolved(development_destination / DATABASE_FILENAME),
            override_suffix=DATABASE_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="temporary_development_runtime_settings",
            kind="file",
            source=_resolved(development_root / RUNTIME_SETTINGS_FILENAME),
            destination=_resolved(development_destination / RUNTIME_SETTINGS_FILENAME),
            override_suffix=RUNTIME_SETTINGS_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="temporary_development_gallery_previews",
            kind="directory",
            source=_resolved(development_root / GALLERY_PREVIEW_DIRECTORY_NAME),
            destination=_resolved(
                development_destination / GALLERY_PREVIEW_DIRECTORY_NAME
            ),
            override_suffix=GALLERY_PREVIEW_CACHE_PATH_SUFFIX,
        ),
        MigrationEntry(
            key="temporary_development_covers",
            kind="directory",
            source=_resolved(development_root / COVER_STORAGE_DIRECTORY_NAME),
            destination=_resolved(development_destination / COVER_STORAGE_DIRECTORY_NAME),
            override_suffix=COVER_STORAGE_ROOT_SUFFIX,
        ),
        MigrationEntry(
            key="temporary_development_cover_thumbnails",
            kind="directory",
            source=_resolved(development_root / COVER_THUMBNAILS_DIRECTORY_NAME),
            destination=_resolved(
                development_destination / COVER_THUMBNAILS_DIRECTORY_NAME
            ),
            override_suffix=COVER_THUMBNAIL_CACHE_PATH_SUFFIX,
        ),
    ]


def build_migration_plan(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
    temp_root: Path | None = None,
) -> MigrationPlan:
    """Resolve the complete owned mapping for one host surface.

    Both sides are computed from the platform, XDG and temporary-root rules,
    never from the current default path functions, so a later default switch
    cannot silently mutate what the migration believes the legacy side is.
    """
    env = os.environ if environ is None else environ
    home_directory = Path.home() if home is None else Path(home)
    platform = sys.platform if platform_name is None else platform_name
    temporary_root = _temporary_root(temp_root)
    if _flavor(platform) == "darwin":
        entries = _macos_entries(home_directory)
    else:
        entries = _xdg_entries(env, home_directory)
    entries.extend(_temporary_entries(temporary_root))
    return MigrationPlan(platform=platform, entries=tuple(entries)).validate()


def _resolved_override_value(
    env: Mapping[str, str],
    suffix: str,
) -> str | None:
    try:
        return lookup_env(suffix, environ=env)
    except IdentityEnvironmentConflictError as exc:
        raise LocalStateMigrationIdentityError(str(exc)) from exc


def default_receipt_directory(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
) -> Path:
    """Return the private per-user receipt directory outside the repository."""
    env = os.environ if environ is None else environ
    home_directory = Path.home() if home is None else Path(home)
    platform = sys.platform if platform_name is None else platform_name
    if _flavor(platform) == "darwin":
        return (
            home_directory
            / "Library"
            / "Application Support"
            / CANONICAL_APP_DIRECTORY_NAME
            / RECEIPT_DIRECTORY_NAME
        )
    state_root = _xdg_directory(
        env, "XDG_STATE_HOME", home_directory / ".local" / "state"
    )
    return state_root / CANONICAL_APP_DIRECTORY_NAME / RECEIPT_DIRECTORY_NAME


def _repository_root(anchor: Path) -> Path | None:
    current = _resolved(anchor)
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _assert_receipt_directory_outside_repository(receipt_directory: Path) -> None:
    resolved = _resolved(receipt_directory)
    anchors: list[Path] = [Path(__file__)]
    try:
        anchors.append(Path.cwd())
    except OSError:
        pass
    for anchor in anchors:
        root = _repository_root(anchor)
        if root is None:
            continue
        if resolved == root or root in resolved.parents:
            raise LocalStateMigrationError(
                "Migration receipts must be stored outside the repository."
            )


def _remove_path(path: Path) -> None:
    try:
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            shutil.rmtree(path)
    except OSError:
        return


def _cleanup_stale_partials(destination: Path) -> None:
    """Remove only this destination's exact owned partial artifacts."""
    parent = destination.parent
    if not parent.is_dir():
        return
    for stale in parent.glob(f".{destination.name}.migration-partial-*"):
        _remove_path(stale)


def _ensure_private_parent(parent: Path) -> None:
    """Create missing destination parents with the application's private mode."""
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _manifest_row(root: Path, path: Path) -> dict[str, Any]:
    relative = path.relative_to(root).as_posix()
    file_stat = path.stat()
    if path.is_dir():
        return {
            "path": relative,
            "type": "directory",
            "mode": stat.S_IMODE(file_stat.st_mode),
        }
    return {
        "path": relative,
        "type": "file",
        "mode": stat.S_IMODE(file_stat.st_mode),
        "size": file_stat.st_size,
        "sha256": _file_sha256(path),
    }


def _directory_manifest(root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for current_root, directory_names, file_names in os.walk(root):
        directory_names.sort()
        file_names.sort()
        for name in directory_names:
            path = Path(current_root) / name
            if path.is_symlink():
                raise _EntryCopyRefused(SOURCE_SYMLINK_REFUSED_STATUS)
            rows.append(_manifest_row(root, path))
        for name in file_names:
            path = Path(current_root) / name
            if path.is_symlink():
                raise _EntryCopyRefused(SOURCE_SYMLINK_REFUSED_STATUS)
            rows.append(_manifest_row(root, path))
    return rows


def _manifest_digest(rows: list[dict[str, Any]]) -> str:
    body = json.dumps(rows, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(body).hexdigest()


def _directory_manifest_digest(root: Path) -> str:
    return _manifest_digest(_directory_manifest(root))


def _copy_file(source: Path, target: Path) -> None:
    with source.open("rb") as source_stream, target.open("wb") as target_stream:
        shutil.copyfileobj(source_stream, target_stream)
        target_stream.flush()
        os.fsync(target_stream.fileno())
    try:
        os.chmod(target, stat.S_IMODE(source.stat().st_mode))
    except OSError:
        pass


def _copy_directory_contents(
    source: Path,
    partial: Path,
    excluded_names: tuple[str, ...],
) -> int:
    """Copy one owned directory tree; refuse shared symlinks loudly.

    Excluded names are matched against the first relative component only, so a
    managed-process liveness record never travels while nested operator data
    with a coincidental name still does.
    """
    copied_files = 0
    for current_root, directory_names, file_names in os.walk(source):
        directory_names.sort()
        file_names.sort()
        relative_root = Path(current_root).relative_to(source)
        if relative_root == Path("."):
            directory_names[:] = [
                name for name in directory_names if name not in excluded_names
            ]
            file_names = [name for name in file_names if name not in excluded_names]
        for name in directory_names:
            source_directory = Path(current_root) / name
            if source_directory.is_symlink():
                raise _EntryCopyRefused(SOURCE_SYMLINK_REFUSED_STATUS)
            target_directory = partial / relative_root / name
            target_directory.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(target_directory, stat.S_IMODE(source_directory.stat().st_mode))
            except OSError:
                pass
        for name in file_names:
            source_file = Path(current_root) / name
            if source_file.is_symlink():
                raise _EntryCopyRefused(SOURCE_SYMLINK_REFUSED_STATUS)
            target_file = partial / relative_root / name
            target_file.parent.mkdir(parents=True, exist_ok=True)
            _copy_file(source_file, target_file)
            copied_files += 1
    return copied_files


def _sqlite_backup_and_verify(source: Path, partial: Path) -> dict[str, Any]:
    """Copy one database WAL-safely and return its verified logical evidence.

    Both sides are opened through the application engine boundary, and the
    consistent snapshot is taken with the SQLite online backup mechanism, so
    committed WAL content travels with the copy. Source and destination are
    then read for identical bounded evidence before the copy is accepted, and
    every connection is disposed before the caller hashes or renames the file.
    """
    source_engine = create_sqlite_readonly_engine(source)
    try:
        destination_engine = create_sqlite_engine(partial)
        try:
            with source_engine.connect() as source_connection:
                with destination_engine.connect() as destination_connection:
                    source_raw = source_connection.connection.driver_connection
                    destination_raw = destination_connection.connection.driver_connection
                    source_raw.backup(destination_raw, pages=128, sleep=0.050)
                    destination_raw.execute("PRAGMA wal_checkpoint(PASSIVE)")
                    source_evidence = _connection_evidence(source_connection)
                    destination_evidence = _connection_evidence(destination_connection)
        finally:
            dispose_engine(destination_engine)
    finally:
        dispose_engine(source_engine)
    if source_evidence != destination_evidence:
        raise LocalStateMigrationError("The database copy verification failed.")
    return destination_evidence


def _database_auxiliary_state_is_clear(path: Path) -> bool:
    """True when no populated journal or WAL sibling would be lost by a rename."""
    for suffix in ("-wal", "-journal"):
        auxiliary = Path(f"{path}{suffix}")
        try:
            if auxiliary.exists() and auxiliary.stat().st_size > 0:
                return False
        except OSError:
            return False
    return True


def _connection_evidence(connection: Any) -> dict[str, Any]:
    """Read bounded logical evidence from one open database connection."""
    integrity = [
        tuple(row)
        for row in connection.exec_driver_sql("PRAGMA integrity_check").fetchall()
    ]
    if integrity != [("ok",)]:
        raise LocalStateMigrationError("The database integrity check failed.")
    user_version = int(
        connection.exec_driver_sql("PRAGMA user_version").scalar() or 0
    )
    schema_rows = [
        tuple(row)
        for row in connection.exec_driver_sql(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    ]
    schema_digest = hashlib.sha256(
        json.dumps(schema_rows, default=str, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    table_names = [
        str(row[0])
        for row in connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
    ]
    table_counts: dict[str, int] = {}
    for name in table_names:
        quoted = name.replace('"', '""')
        table_counts[name] = int(
            connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{quoted}"').scalar()
            or 0
        )
    alembic_version: str | None = None
    if "alembic_version" in table_counts:
        version_row = connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version LIMIT 1"
        ).fetchone()
        if version_row is not None:
            alembic_version = str(version_row[0])
    return {
        "integrity_check": "ok",
        "user_version": user_version,
        "schema_sha256": schema_digest,
        "table_counts": table_counts,
        "alembic_version": alembic_version,
    }


def _prior_evidence(
    prior: Mapping[str, Any] | None,
    entry: MigrationEntry,
) -> Mapping[str, Any] | None:
    if not isinstance(prior, Mapping):
        return None
    if prior.get("class") != entry.key:
        return None
    if prior.get("status") not in _VERIFIED_STATUSES:
        return None
    if prior.get("source") != str(entry.source):
        return None
    if prior.get("destination") != str(entry.destination):
        return None
    evidence = prior.get("evidence")
    if not isinstance(evidence, Mapping):
        return None
    return evidence


class LocalStateMigration:
    """Owns the bounded identity-path check and copy operation."""

    def __init__(
        self,
        *,
        environ: Mapping[str, str] | None = None,
        platform_name: str | None = None,
        home: Path | None = None,
        temp_root: Path | None = None,
        receipt_dir: Path | None = None,
        runtime_factory: Callable[..., Any] | None = None,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._environ: Mapping[str, str] = os.environ if environ is None else environ
        self._platform_name = sys.platform if platform_name is None else platform_name
        self._home = Path.home() if home is None else Path(home)
        self._temp_root = temp_root
        self._now = now
        self._runtime_factory = (
            DevelopmentRuntime if runtime_factory is None else runtime_factory
        )
        self._plan = build_migration_plan(
            environ=self._environ,
            platform_name=self._platform_name,
            home=self._home,
            temp_root=self._temp_root,
        )
        if receipt_dir is None:
            receipt_dir = default_receipt_directory(
                environ=self._environ,
                platform_name=self._platform_name,
                home=self._home,
            )
        self._receipt_directory = Path(receipt_dir).expanduser()
        _assert_receipt_directory_outside_repository(self._receipt_directory)

    @property
    def plan(self) -> MigrationPlan:
        return self._plan

    @property
    def receipt_directory(self) -> Path:
        return self._receipt_directory

    def check(self) -> MigrationReport:
        """Resolve the mapping read-only for state and record a private receipt."""
        self._assert_managed_server_stopped()
        overrides = self._resolved_overrides()
        outcomes = tuple(
            self._assess_entry(
                entry,
                overridden=overrides[entry.override_suffix] is not None,
            )
            for entry in self._plan.entries
        )
        report = MigrationReport(
            operation="check",
            platform=self._plan.platform,
            outcomes=outcomes,
            receipt_path=self._receipt_directory / CHECK_RECEIPT_FILENAME,
        )
        self._write_receipt(CHECK_RECEIPT_FILENAME, report)
        return report

    def apply(self) -> MigrationReport:
        """Copy every eligible source to its absent destination, verified.

        An occupied destination is never overwritten: a destination recorded by
        this operation's own verified receipt is re-verified and treated as a
        no-op, and any other occupied destination is refused and reported.
        """
        self._assert_managed_server_stopped()
        overrides = self._resolved_overrides()
        prior_entries = self._load_prior_apply_receipt_entries()
        outcomes = tuple(
            self._apply_entry(
                entry,
                overridden=overrides[entry.override_suffix] is not None,
                prior=prior_entries.get(entry.key),
            )
            for entry in self._plan.entries
        )
        report = MigrationReport(
            operation="apply",
            platform=self._plan.platform,
            outcomes=outcomes,
            receipt_path=self._receipt_directory / APPLY_RECEIPT_FILENAME,
        )
        self._write_apply_receipt(report, prior_entries)
        return report

    # -- environment and process guards ------------------------------------

    def _resolved_overrides(self) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        for entry in self._plan.entries:
            if entry.override_suffix not in resolved:
                resolved[entry.override_suffix] = _resolved_override_value(
                    self._environ, entry.override_suffix
                )
        return resolved

    def _assert_managed_server_stopped(self) -> None:
        try:
            runtime = self._runtime_factory(
                environ=self._environ,
                platform_name=self._platform_name,
                home=self._home,
            )
            status = runtime.status()
        except DevelopmentRuntimeError as exc:
            raise LocalStateMigrationError(
                "The managed development server state could not be inspected."
            ) from exc
        kind = getattr(status, "kind", None)
        if kind in {"running", "unhealthy"}:
            raise ManagedDevelopmentServerActiveError(
                "Identity-path migration refused: stop the managed development "
                "server first."
            )
        if kind == "conflict":
            raise ManagedDevelopmentServerActiveError(
                "Identity-path migration refused: the managed development server "
                "state cannot be proven stopped."
            )

    # -- check helpers ------------------------------------------------------

    def _assess_entry(
        self,
        entry: MigrationEntry,
        *,
        overridden: bool,
    ) -> EntryOutcome:
        if overridden:
            return EntryOutcome(entry.key, OVERRIDE_STATUS, entry.source, entry.destination)
        if not _lexists(entry.source):
            return EntryOutcome(
                entry.key, SOURCE_ABSENT_STATUS, entry.source, entry.destination
            )
        if _lexists(entry.destination):
            return EntryOutcome(
                entry.key, DESTINATION_OCCUPIED_STATUS, entry.source, entry.destination
            )
        return EntryOutcome(entry.key, COPY_READY_STATUS, entry.source, entry.destination)

    # -- apply helpers ------------------------------------------------------

    def _apply_entry(
        self,
        entry: MigrationEntry,
        *,
        overridden: bool,
        prior: Mapping[str, Any] | None,
    ) -> EntryOutcome:
        if overridden:
            return EntryOutcome(entry.key, OVERRIDE_STATUS, entry.source, entry.destination)
        if not _lexists(entry.source):
            return EntryOutcome(
                entry.key, SOURCE_ABSENT_STATUS, entry.source, entry.destination
            )
        if entry.source.is_symlink():
            return EntryOutcome(
                entry.key, SOURCE_SYMLINK_REFUSED_STATUS, entry.source, entry.destination
            )
        if _lexists(entry.destination):
            evidence = _prior_evidence(prior, entry)
            if evidence is not None and self._destination_matches_evidence(entry, evidence):
                return EntryOutcome(
                    entry.key,
                    ALREADY_VERIFIED_STATUS,
                    entry.source,
                    entry.destination,
                    evidence=evidence,
                )
            return EntryOutcome(
                entry.key,
                DESTINATION_OCCUPIED_REFUSED_STATUS,
                entry.source,
                entry.destination,
            )
        try:
            if entry.kind == "database":
                return self._apply_database_entry(entry)
            if entry.kind == "directory":
                return self._apply_directory_entry(entry)
            return self._apply_file_entry(entry)
        except OSError:
            return EntryOutcome(
                entry.key, COPY_REFUSED_STATUS, entry.source, entry.destination
            )

    def _destination_matches_evidence(
        self,
        entry: MigrationEntry,
        evidence: Mapping[str, Any],
    ) -> bool:
        if entry.kind == "directory":
            recorded = evidence.get("manifest_sha256")
            if not isinstance(recorded, str):
                return False
            try:
                return _directory_manifest_digest(entry.destination) == recorded
            except (OSError, _EntryCopyRefused):
                return False
        recorded = evidence.get("sha256")
        if not isinstance(recorded, str):
            return False
        try:
            return _file_sha256(entry.destination) == recorded
        except OSError:
            return False

    def _refused_with_cleanup(
        self,
        entry: MigrationEntry,
        partial: Path,
        status: str,
    ) -> EntryOutcome:
        _remove_path(partial)
        return EntryOutcome(entry.key, status, entry.source, entry.destination)

    def _apply_file_entry(self, entry: MigrationEntry) -> EntryOutcome:
        destination = entry.destination
        parent = destination.parent
        _ensure_private_parent(parent)
        _cleanup_stale_partials(destination)
        descriptor, partial_name = tempfile.mkstemp(
            prefix=f".{destination.name}.migration-partial-",
            dir=parent,
        )
        os.close(descriptor)
        partial = Path(partial_name)
        try:
            _copy_file(entry.source, partial)
            source_digest = _file_sha256(entry.source)
            if _file_sha256(partial) != source_digest:
                return self._refused_with_cleanup(entry, partial, COPY_REFUSED_STATUS)
            self._adopt_source_mode(entry.source, partial)
            if _lexists(destination):
                return self._refused_with_cleanup(
                    entry, partial, DESTINATION_OCCUPIED_REFUSED_STATUS
                )
            os.replace(partial, destination)
            if _file_sha256(destination) != source_digest:
                destination.unlink(missing_ok=True)
                return EntryOutcome(
                    entry.key, COPY_REFUSED_STATUS, entry.source, entry.destination
                )
            destination_stat = destination.stat()
            return EntryOutcome(
                entry.key,
                COPIED_VERIFIED_STATUS,
                entry.source,
                destination,
                evidence={
                    "sha256": source_digest,
                    "size": destination_stat.st_size,
                    "mode": stat.S_IMODE(destination_stat.st_mode),
                },
            )
        finally:
            if _lexists(partial):
                _remove_path(partial)

    def _apply_database_entry(self, entry: MigrationEntry) -> EntryOutcome:
        destination = entry.destination
        parent = destination.parent
        _ensure_private_parent(parent)
        _cleanup_stale_partials(destination)
        partial = parent / (
            f".{destination.name}.migration-partial-"
            f"{os.getpid()}-{secrets.token_hex(8)}"
        )
        partial_family = (
            partial,
            Path(f"{partial}-wal"),
            Path(f"{partial}-shm"),
            Path(f"{partial}-journal"),
        )
        try:
            try:
                destination_evidence = _sqlite_backup_and_verify(
                    entry.source, partial
                )
                if not _database_auxiliary_state_is_clear(partial):
                    return self._refused_with_cleanup(
                        entry, partial, COPY_REFUSED_STATUS
                    )
            except Exception:
                return self._refused_with_cleanup(entry, partial, COPY_REFUSED_STATUS)
            digest = _file_sha256(partial)
            if _lexists(destination):
                return self._refused_with_cleanup(
                    entry, partial, DESTINATION_OCCUPIED_REFUSED_STATUS
                )
            os.replace(partial, destination)
            if _file_sha256(destination) != digest:
                destination.unlink(missing_ok=True)
                return EntryOutcome(
                    entry.key, COPY_REFUSED_STATUS, entry.source, entry.destination
                )
            evidence = {"sha256": digest, **destination_evidence}
            return EntryOutcome(
                entry.key,
                COPIED_VERIFIED_STATUS,
                entry.source,
                destination,
                evidence=evidence,
            )
        finally:
            for candidate in partial_family:
                if _lexists(candidate):
                    _remove_path(candidate)

    def _apply_directory_entry(self, entry: MigrationEntry) -> EntryOutcome:
        destination = entry.destination
        parent = destination.parent
        _ensure_private_parent(parent)
        _cleanup_stale_partials(destination)
        partial = Path(
            tempfile.mkdtemp(
                prefix=f".{destination.name}.migration-partial-",
                dir=parent,
            )
        )
        try:
            try:
                copied_files = _copy_directory_contents(
                    entry.source, partial, entry.excluded_names
                )
            except _EntryCopyRefused as refused:
                return self._refused_with_cleanup(entry, partial, refused.status)
            if copied_files == 0:
                return self._refused_with_cleanup(
                    entry, partial, NO_DURABLE_CONTENT_STATUS
                )
            manifest = _directory_manifest(partial)
            manifest_digest = _manifest_digest(manifest)
            if _directory_manifest_digest(partial) != manifest_digest:
                return self._refused_with_cleanup(entry, partial, COPY_REFUSED_STATUS)
            if _lexists(destination):
                return self._refused_with_cleanup(
                    entry, partial, DESTINATION_OCCUPIED_REFUSED_STATUS
                )
            os.rename(partial, destination)
            if _directory_manifest_digest(destination) != manifest_digest:
                _remove_path(destination)
                return EntryOutcome(
                    entry.key, COPY_REFUSED_STATUS, entry.source, entry.destination
                )
            return EntryOutcome(
                entry.key,
                COPIED_VERIFIED_STATUS,
                entry.source,
                destination,
                evidence={
                    "manifest_sha256": manifest_digest,
                    "file_count": copied_files,
                },
            )
        finally:
            if _lexists(partial):
                _remove_path(partial)

    def _adopt_source_mode(self, source: Path, target: Path) -> None:
        try:
            os.chmod(target, stat.S_IMODE(source.stat().st_mode))
        except OSError:
            pass

    # -- receipts -----------------------------------------------------------

    def _load_prior_apply_receipt_entries(self) -> dict[str, dict[str, Any]]:
        path = self._receipt_directory / APPLY_RECEIPT_FILENAME
        if not _lexists(path) or path.is_symlink():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict) or payload.get("schema") != MIGRATION_RECEIPT_SCHEMA:
            return {}
        if payload.get("platform") != self._plan.platform:
            return {}
        entries = payload.get("entries")
        if not isinstance(entries, list):
            return {}
        resolved: dict[str, dict[str, Any]] = {}
        for item in entries:
            if not isinstance(item, dict):
                continue
            class_key = item.get("class")
            if isinstance(class_key, str):
                resolved[class_key] = item
        return resolved

    def _receipt_entry(self, outcome: EntryOutcome) -> dict[str, Any]:
        return {
            "class": outcome.key,
            "status": outcome.status,
            "source": str(outcome.source),
            "destination": str(outcome.destination),
            "evidence": dict(outcome.evidence) if outcome.evidence is not None else None,
        }

    def _write_receipt(self, filename: str, report: MigrationReport) -> None:
        payload = {
            "schema": MIGRATION_RECEIPT_SCHEMA,
            "operation": report.operation,
            "platform": report.platform,
            "recorded_at_ms": int(self._now() * 1000),
            "entries": [self._receipt_entry(outcome) for outcome in report.outcomes],
            "counts": report.counts,
        }
        self._write_private_json(filename, payload)

    def _write_apply_receipt(
        self,
        report: MigrationReport,
        prior_entries: Mapping[str, dict[str, Any]],
    ) -> None:
        merged: dict[str, dict[str, Any]] = dict(prior_entries)
        for outcome in report.outcomes:
            merged[outcome.key] = self._receipt_entry(outcome)
        payload = {
            "schema": MIGRATION_RECEIPT_SCHEMA,
            "operation": report.operation,
            "platform": report.platform,
            "recorded_at_ms": int(self._now() * 1000),
            "entries": [merged[key] for key in sorted(merged)],
            "counts": report.counts,
        }
        self._write_private_json(APPLY_RECEIPT_FILENAME, payload)

    def _write_private_json(self, filename: str, payload: Mapping[str, Any]) -> None:
        directory = self._receipt_directory
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        except OSError as exc:
            raise LocalStateMigrationError(
                "The private migration receipt directory could not be created."
            ) from exc
        if directory.is_symlink() or not directory.is_dir():
            raise LocalStateMigrationError(
                "The private migration receipt directory is invalid."
            )
        body = json.dumps(payload, sort_keys=True, indent=2).encode("utf-8") + b"\n"
        descriptor = -1
        temporary: Path | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".receipt-",
                suffix=".tmp",
                dir=directory,
            )
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "wb") as stream:
                descriptor = -1
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, directory / filename)
        except OSError as exc:
            raise LocalStateMigrationError(
                "The private migration receipt could not be written."
            ) from exc
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary is not None and _lexists(temporary):
                _remove_path(temporary)


def refuse_silent_legacy_state_replacement(
    *,
    environ: Mapping[str, str] | None = None,
    platform_name: str | None = None,
    home: Path | None = None,
    temp_root: Path | None = None,
) -> None:
    """Refuse to silently create an empty replacement over existing state.

    For every owned class whose canonical destination is absent while its
    matching legacy source exists -- and that no explicit override redirects --
    this raises :class:`LegacyStateReplacementRefusal` naming only the class.
    The protective state is the pair itself: with no legacy state present there
    is nothing to preserve and the call returns, so the refusal cannot pass
    vacuously on an empty host.
    """
    env = os.environ if environ is None else environ
    plan = build_migration_plan(
        environ=env,
        platform_name=platform_name,
        home=home,
        temp_root=temp_root,
    )
    offenders: list[str] = []
    for entry in plan.entries:
        if _resolved_override_value(env, entry.override_suffix) is not None:
            continue
        if not _lexists(entry.source):
            continue
        if _lexists(entry.destination):
            continue
        offenders.append(entry.key)
    if offenders:
        raise LegacyStateReplacementRefusal(
            "Legacy local state exists for "
            + ", ".join(sorted(offenders))
            + " while the canonical destination is absent; refusing to create "
            "an empty replacement."
        )
