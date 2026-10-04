"""Migration and repository evidence for the security audit foundation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.domain.security_audit import (
    AUDIT_OUTCOME_ALLOWED,
    AUDIT_OUTCOME_DENIED,
    SecurityAuditEvent,
)


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _migrate(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic import command
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.migrations import _alembic_config

    database_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                if downgrade:
                    command.downgrade(config, revision)
                else:
                    command.upgrade(config, revision)
    finally:
        dispose_engine(engine)


def _connect(database_path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database_path)
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _event(**overrides: object) -> SecurityAuditEvent:
    values = {
        "request_id": "request-1",
        "actor_login": "admin@example.com",
        "actor_key": "admin@example.com",
        "identity_provenance": "tailscale-serve",
        "role": "admin",
        "capability": "metadata.canonical.write",
        "action": "canonical_tag.create",
        "target_type": "canonical_tag",
        "target_id": None,
        "outcome": AUDIT_OUTCOME_ALLOWED,
        "http_status": 201,
        "now_ms": 10,
    }
    values.update(overrides)
    return SecurityAuditEvent.new(**values)  # type: ignore[arg-type]


def test_empty_and_populated_0019_databases_upgrade_to_0020(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    populated_path = tmp_path / "populated.sqlite3"
    _migrate(empty_path, "0020")
    _migrate(populated_path, "0019")
    connection = _connect(populated_path)
    try:
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, ?)",
            (
                "11111111-1111-4111-8111-111111111111",
                "Preserved device",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    _migrate(populated_path, "0020")

    for database_path in (empty_path, populated_path):
        connection = _connect(database_path)
        try:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0020",)
            assert connection.execute(
                "SELECT COUNT(*) FROM security_audit_events"
            ).fetchone() == (0,)
            assert connection.execute("PRAGMA integrity_check").fetchone() == (
                "ok",
            )
            assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        finally:
            connection.close()
    connection = _connect(populated_path)
    try:
        assert connection.execute(
            "SELECT display_name FROM devices"
        ).fetchone() == ("Preserved device",)
    finally:
        connection.close()


def test_downgrade_refuses_to_drop_existing_audit_history(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "audit.sqlite3"
    settings = _settings(database_path)
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
    from kronika.infrastructure.persistence.security_audit_repository import (
        SqliteSecurityAuditRepository,
    )

    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    try:
        SqliteSecurityAuditRepository(engine).record(_event())
    finally:
        dispose_engine(engine)

    with pytest.raises(RuntimeError, match="Cannot downgrade catalog-removal"):
        _migrate(database_path, "0019", downgrade=True)

    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT COUNT(*) FROM security_audit_events"
        ).fetchone() == (1,)
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0025",)
    finally:
        connection.close()


def test_empty_audit_table_downgrades_back_to_0019(tmp_path: Path) -> None:
    database_path = tmp_path / "empty-audit.sqlite3"
    _migrate(database_path, "0020")
    _migrate(database_path, "0019", downgrade=True)
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0019",)
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE name = 'security_audit_events'"
        ).fetchone() is None
    finally:
        connection.close()
    _migrate(database_path, "0020")


def test_repository_records_and_counts_exact_audit_events(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
    from kronika.infrastructure.persistence.security_audit_repository import (
        SqliteSecurityAuditRepository,
    )

    settings = _settings(tmp_path / "events.sqlite3")
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteSecurityAuditRepository(engine)
    try:
        allowed = _event(request_id="request-allowed")
        denied = _event(
            request_id="request-denied",
            actor_login="user@example.com",
            actor_key="user@example.com",
            role="user",
            outcome=AUDIT_OUTCOME_DENIED,
            http_status=403,
        )
        repository.record(allowed)
        repository.record(denied)
        assert repository.count_events() == 2
    finally:
        dispose_engine(engine)

    connection = _connect(settings.database_path)
    try:
        rows = connection.execute(
            "SELECT request_id, actor_login, actor_key, identity_provenance, role,"
            " capability, action, target_type, target_id, outcome, http_status"
            " FROM security_audit_events ORDER BY request_id"
        ).fetchall()
        assert rows == [
            (
                "request-allowed",
                "admin@example.com",
                "admin@example.com",
                "tailscale-serve",
                "admin",
                "metadata.canonical.write",
                "canonical_tag.create",
                "canonical_tag",
                None,
                "allowed",
                201,
            ),
            (
                "request-denied",
                "user@example.com",
                "user@example.com",
                "tailscale-serve",
                "user",
                "metadata.canonical.write",
                "canonical_tag.create",
                "canonical_tag",
                None,
                "denied",
                403,
            ),
        ]
        assert connection.execute("PRAGMA integrity_check").fetchone() == ("ok",)
    finally:
        connection.close()
