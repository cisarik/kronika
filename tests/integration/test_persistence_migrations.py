"""Integration tests for packaged Alembic migration operations."""

from __future__ import annotations

import importlib.resources
import sqlite3
import textwrap
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings

FORBIDDEN_PRODUCT_TABLE_FRAGMENTS = (
    "media",
    "library",
    "device",
    "location",
    "series",
    "tag",
    "cover",
    "sidecar",
    "user",
    "auth",
    "gallery",
)
PRODUCTION_VERSIONS_PACKAGE = (
    "kronika.infrastructure.persistence.alembic_environment.versions"
)


def _settings_for(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _table_names(database_path: Path) -> set[str]:
    connection = sqlite3.connect(database_path)
    try:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
        ).fetchall()
    finally:
        connection.close()
    return {row[0] for row in rows}


def test_nonexistent_database_status_reports_current_head_without_file_creation(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
    )

    database_path = tmp_path / "missing" / "catalog.sqlite3"
    status = inspect_database_migration_status(_settings_for(database_path))

    assert status.state == "uninitialized"
    assert status.current_revision is None
    assert status.head_revision == "0035"
    assert not database_path.exists()
    assert not database_path.parent.exists()


def test_empty_database_upgrades_to_current_head_revision(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "catalog.sqlite3")

    migration_status = upgrade_database_to_head(settings)
    inspected_status = inspect_database_migration_status(settings)

    assert migration_status.state == "at_head"
    assert migration_status.current_revision == "0035"
    assert migration_status.head_revision == "0035"
    assert inspected_status == migration_status
    assert settings.database_path.exists()


def test_repeated_migration_at_head_is_safe_and_stable(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "repeated.sqlite3")
    first = upgrade_database_to_head(settings)
    second = upgrade_database_to_head(settings)
    reopened = inspect_database_migration_status(settings)

    assert first == second == reopened
    assert reopened.state == "at_head"
    assert reopened.current_revision == reopened.head_revision == "0035"


def test_migration_status_is_stable_after_engine_close_and_reopen(tmp_path: Path) -> None:
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    settings = _settings_for(tmp_path / "reopen.sqlite3")
    upgrade_database_to_head(settings)

    assert inspect_database_migration_status(settings).state == "at_head"
    assert inspect_database_migration_status(settings).current_revision == "0035"


def test_initial_revision_creates_only_alembic_version_tracking(tmp_path: Path) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    settings = _settings_for(tmp_path / "schema.sqlite3")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0001")
    finally:
        dispose_engine(engine)

    assert _table_names(settings.database_path) == {"alembic_version"}


def test_initial_revision_creates_no_product_schema(tmp_path: Path) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
    from kronika.infrastructure.persistence.migrations import _alembic_config

    settings = _settings_for(tmp_path / "no-product-schema.sqlite3")
    settings.database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(settings.database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "0001")
    finally:
        dispose_engine(engine)

    table_names = _table_names(settings.database_path)
    assert table_names == {"alembic_version"}
    assert not any(
        fragment in table_name
        for table_name in table_names
        for fragment in FORBIDDEN_PRODUCT_TABLE_FRAGMENTS
    )


def test_initial_revision_downgrade_is_explicitly_unsupported() -> None:
    from kronika.infrastructure.persistence.migrations import load_script_directory

    script_directory = load_script_directory()
    revision = script_directory.get_revision("0001")

    assert revision is not None
    with pytest.raises(NotImplementedError, match="not supported"):
        revision.module.downgrade()


def test_failed_migration_is_sanitized_and_does_not_claim_head(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.infrastructure.persistence.errors import FrameNestMigrationError
    from kronika.infrastructure.persistence.migrations import (
        inspect_database_migration_status,
        upgrade_database_to_head,
    )

    production_revision = (
        importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
        .joinpath("0001_initial_foundation.py")
        .read_text(encoding="utf-8")
    )
    package_name = "broken_framenest_migrations"
    package_root = tmp_path / package_name
    versions = package_root / "versions"
    versions.mkdir(parents=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    (versions / "__init__.py").write_text("", encoding="utf-8")
    (package_root / "script.py.mako").write_text(
        "${up_revision} = ${repr(up_revision)}\n",
        encoding="utf-8",
    )
    (package_root / "env.py").write_text(
        textwrap.dedent(
            """
            from alembic import context


            def run_migrations_online():
                connection = context.config.attributes["connection"]
                context.configure(connection=connection, target_metadata=None)
                with context.begin_transaction():
                    context.run_migrations()


            run_migrations_online()
            """
        ),
        encoding="utf-8",
    )
    (versions / "0001_broken.py").write_text(
        textwrap.dedent(
            """
            from alembic import op

            revision = "0001"
            down_revision = None
            branch_labels = None
            depends_on = None


            def upgrade():
                op.execute("CREATE TABLE temporary_failure_probe (id INTEGER PRIMARY KEY)")
                raise RuntimeError("raw-private-migration-detail /Users/agile/private.sqlite3")


            def downgrade():
                raise NotImplementedError("not supported")
            """
        ),
        encoding="utf-8",
    )
    monkeypatch.syspath_prepend(str(tmp_path))

    settings = _settings_for(tmp_path / "failed.sqlite3")
    with pytest.raises(FrameNestMigrationError) as exc_info:
        upgrade_database_to_head(settings, migration_package=package_name)

    assert exc_info.value.error_code == "MIGRATION_FAILED"
    error_text = str(exc_info.value)
    assert "raw-private-migration-detail" not in error_text
    assert "/Users/agile" not in error_text

    status = inspect_database_migration_status(settings, migration_package=package_name)
    assert status.current_revision != status.head_revision
    assert status.state != "at_head"

    after_failure_revision = (
        importlib.resources.files(PRODUCTION_VERSIONS_PACKAGE)
        .joinpath("0001_initial_foundation.py")
        .read_text(encoding="utf-8")
    )
    assert after_failure_revision == production_revision


def test_missing_migration_resources_keep_resource_unavailable_classification(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.errors import FrameNestMigrationError
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

    settings = _settings_for(tmp_path / "missing-resources.sqlite3")
    with pytest.raises(FrameNestMigrationError) as exc_info:
        upgrade_database_to_head(settings, migration_package="missing_framenest_migrations")

    assert exc_info.value.error_code == "MIGRATION_RESOURCES_UNAVAILABLE"
    assert str(exc_info.value) == "Database migration resources are unavailable."
