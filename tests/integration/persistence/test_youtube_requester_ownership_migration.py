"""Migration evidence for YouTube requester ownership (0026)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.domain.youtube_acquisition import (
    YouTubeAcquisitionClaim,
    YouTubeConfirmationMethod,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
    SqliteYouTubeAcquisitionClaimRepository,
)


VIDEO_ID = "AbCdEf123_-"
ACTIVE_PREDICATE = (
    "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
    "'downloaded', 'handoff', 'handed_off')"
)


def _settings(database_path: Path) -> KronikaSettings:
    return KronikaSettings(database_path=database_path, _env_file=None)


def _migrate(database_path: Path, revision: str, *, downgrade: bool = False) -> None:
    from alembic import command
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


def test_upgrade_0025_to_0026_keeps_legacy_null_and_adds_indexes(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "claims.sqlite3"
    _migrate(database_path, "0025")
    _migrate(database_path, "0026")
    engine = create_sqlite_engine(database_path)
    try:
        repository = SqliteYouTubeAcquisitionClaimRepository(engine)
        claim = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=10,
        )
        assert claim.created_by_login_key is None
        repository.create(claim)
    finally:
        dispose_engine(engine)
    connection = _connect(database_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0026",)
        row = connection.execute(
            "SELECT created_by_login_key FROM youtube_acquisition_claims"
        ).fetchone()
        assert row == (None,)
        index_sql = {
            name: sql
            for name, sql in connection.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "uq_youtube_claims_active_source_identity" not in index_sql
        assert "uq_youtube_claims_active_source_admin" in index_sql
        assert "uq_youtube_claims_active_source_requester" in index_sql
        assert "ix_youtube_claims_created_by_login_key" in index_sql
        assert "ix_youtube_claims_owner_updated" in index_sql
        assert "ix_youtube_claims_media_requester_live" in index_sql
        assert ACTIVE_PREDICATE in (index_sql["uq_youtube_claims_active_source_admin"] or "")
    finally:
        connection.close()


def test_per_requester_active_uniqueness_and_admin_coexistence(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "owned.sqlite3"
    _migrate(database_path, "0026")
    engine = create_sqlite_engine(database_path)
    try:
        repository = SqliteYouTubeAcquisitionClaimRepository(engine)
        first = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
            now_ms=10,
            created_by_login_key="alice@example.com",
        )
        second = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
            now_ms=11,
            created_by_login_key="bob@example.com",
        )
        admin = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=12,
        )
        selected, created = repository.create_or_get_active(first)
        assert created is True
        selected_b, created_b = repository.create_or_get_active(second)
        assert created_b is True
        assert selected.id != selected_b.id
        reused, created_again = repository.create_or_get_active(
            YouTubeAcquisitionClaim.new(
                submitted_url=f"https://youtu.be/{VIDEO_ID}",
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
                now_ms=13,
                created_by_login_key="alice@example.com",
            )
        )
        assert created_again is False
        assert reused.id == selected.id
        repository.create(admin)
        assert repository.find_active_by_source_identity(
            extractor_key=admin.extractor_key,
            youtube_video_id=admin.youtube_video_id,
            created_by_login_key=None,
        ) is not None
    finally:
        dispose_engine(engine)


def test_downgrade_refuses_requester_rows_and_succeeds_when_empty(
    tmp_path: Path,
) -> None:
    owned_path = tmp_path / "owned-down.sqlite3"
    empty_path = tmp_path / "empty-down.sqlite3"
    _migrate(owned_path, "0026")
    engine = create_sqlite_engine(owned_path)
    try:
        repository = SqliteYouTubeAcquisitionClaimRepository(engine)
        repository.create(
            YouTubeAcquisitionClaim.new(
                submitted_url=f"https://youtu.be/{VIDEO_ID}",
                confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
                now_ms=10,
                created_by_login_key="alice@example.com",
            )
        )
    finally:
        dispose_engine(engine)
    with pytest.raises(RuntimeError, match="requester-owned"):
        _migrate(owned_path, "0025", downgrade=True)

    _migrate(empty_path, "0026")
    _migrate(empty_path, "0025", downgrade=True)
    connection = _connect(empty_path)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0025",)
        names = {
            name
            for (name,) in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='index'"
            ).fetchall()
        }
        assert "uq_youtube_claims_active_source_identity" in names
        assert "uq_youtube_claims_active_source_admin" not in names
    finally:
        connection.close()
