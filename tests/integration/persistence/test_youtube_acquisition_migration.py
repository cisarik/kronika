"""Migration and repository evidence for durable YouTube acquisition claims."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.configuration import KronikaSettings
from kronika.domain.youtube_acquisition import (
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
)

VIDEO_ID = "AbCdEf123_-"


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


def test_empty_and_populated_0018_databases_upgrade_to_0019(
    tmp_path: Path,
) -> None:
    empty_path = tmp_path / "empty.sqlite3"
    populated_path = tmp_path / "populated.sqlite3"
    _migrate(empty_path, "0019")
    _migrate(populated_path, "0018")
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
    _migrate(populated_path, "0019")

    for database_path in (empty_path, populated_path):
        connection = _connect(database_path)
        try:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == ("0019",)
            assert connection.execute(
                "SELECT COUNT(*) FROM youtube_acquisition_claims"
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


def test_repository_selects_one_active_source_identity_winner(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.engine import (
        create_sqlite_engine,
        dispose_engine,
    )
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
    from kronika.infrastructure.persistence.youtube_acquisition_claim_repository import (
        SqliteYouTubeAcquisitionClaimRepository,
    )

    settings = _settings(tmp_path / "claims.sqlite3")
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    repository = SqliteYouTubeAcquisitionClaimRepository(engine)
    try:
        first = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=10,
        )
        second = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
            now_ms=11,
        )

        winner, created = repository.create_or_get_active(first)
        same_winner, second_created = repository.create_or_get_active(second)

        assert created is True
        assert second_created is False
        assert same_winner.id == winner.id == first.id
        assert repository.find_active_by_source_identity(
            extractor_key="Youtube",
            youtube_video_id=VIDEO_ID,
        ) == first

        inspecting = first.advance(
            YouTubeAcquisitionState.INSPECTING,
            updated_at_ms=12,
        )
        assert repository.save(
            inspecting,
            expected_state=YouTubeAcquisitionState.CLAIMED,
            expected_version=0,
        ) == inspecting
        assert repository.list_recovery_candidates(limit=10) == (inspecting,)
    finally:
        dispose_engine(engine)


def test_schema_constraints_and_foreign_keys_reject_invalid_provenance(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "constraints.sqlite3"
    _migrate(database_path, "0019")
    connection = _connect(database_path)
    try:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO youtube_acquisition_claims (
                    id, state, acquisition_source, submitted_url, canonical_url,
                    youtube_video_id, extractor_key, confirmation_method,
                    confirmed_at_ms, generated_filename, staging_key,
                    created_at_ms, updated_at_ms, cleanup_state, version
                ) VALUES (
                    ?, 'claimed', 'manual_upload', ?, ?, ?, 'Youtube', 'yes_flag',
                    1, ?, ?, 1, 1, 'pending', 0
                )
                """,
                (
                    "11111111-1111-4111-8111-111111111111",
                    f"https://youtu.be/{VIDEO_ID}",
                    f"https://www.youtube.com/watch?v={VIDEO_ID}",
                    VIDEO_ID,
                    f"youtube-{VIDEO_ID}.mp4",
                    "1" * 32,
                ),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO youtube_acquisition_claims (
                    id, state, acquisition_source, submitted_url, canonical_url,
                    youtube_video_id, extractor_key, upload_id, confirmation_method,
                    confirmed_at_ms, generated_filename, staging_key,
                    created_at_ms, updated_at_ms, cleanup_state, version
                ) VALUES (
                    ?, 'claimed', 'youtube_manual_claim', ?, ?, ?, 'Youtube', ?,
                    'yes_flag', 1, ?, ?, 1, 1, 'pending', 0
                )
                """,
                (
                    "22222222-2222-4222-8222-222222222222",
                    f"https://youtu.be/{VIDEO_ID}",
                    f"https://www.youtube.com/watch?v={VIDEO_ID}",
                    VIDEO_ID,
                    "33333333-3333-4333-8333-333333333333",
                    f"youtube-{VIDEO_ID}.mp4",
                    "2" * 32,
                ),
            )
    finally:
        connection.close()


def test_downgrade_refuses_provenance_loss_and_allows_empty_table(
    tmp_path: Path,
) -> None:
    populated = tmp_path / "populated.sqlite3"
    empty = tmp_path / "empty.sqlite3"
    _migrate(populated, "0019")
    _migrate(empty, "0019")
    connection = _connect(populated)
    try:
        claim = YouTubeAcquisitionClaim.new(
            submitted_url=f"https://youtu.be/{VIDEO_ID}",
            confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
            now_ms=10,
        )
        connection.execute(
            """
            INSERT INTO youtube_acquisition_claims (
                id, state, acquisition_source, submitted_url, canonical_url,
                youtube_video_id, extractor_key, confirmation_method,
                confirmed_at_ms, generated_filename, staging_key,
                created_at_ms, updated_at_ms, cleanup_state, version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                claim.id.to_string(),
                claim.state.value,
                claim.acquisition_source.value,
                claim.submitted_url,
                claim.canonical_url,
                claim.youtube_video_id,
                claim.extractor_key,
                claim.confirmation_method.value,
                claim.confirmed_at_ms,
                claim.generated_filename,
                claim.staging_key,
                claim.created_at_ms,
                claim.updated_at_ms,
                claim.cleanup_state.value,
                claim.version,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        RuntimeError,
        match="Cannot downgrade YouTube acquisition provenance",
    ):
        _migrate(populated, "0018", downgrade=True)

    _migrate(empty, "0018", downgrade=True)
    connection = _connect(empty)
    try:
        assert connection.execute(
            "SELECT version_num FROM alembic_version"
        ).fetchone() == ("0018",)
        assert connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='youtube_acquisition_claims'"
        ).fetchone() is None
    finally:
        connection.close()
