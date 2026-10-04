"""Migration and isolation evidence for per-user media alias overlay (0029)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, text

from kronika.domain import MediaId
from kronika.domain.media_user_alias import parse_alias_content
from kronika.infrastructure.persistence.catalog_removal_repository import (
    _delete_metadata_graph,
)
from kronika.infrastructure.persistence.catalog_schema import metadata
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
    run_in_transaction,
)
from kronika.infrastructure.persistence.media_user_alias_repository import (
    SqliteMediaUserAliasRepository,
)

MEDIA_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
ALICE = "alice@example.com"
BOB = "bob@example.com"


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


def test_head_is_0030() -> None:
    from kronika.infrastructure.persistence.migrations import _alembic_config

    with _alembic_config(
        "kronika.infrastructure.persistence.alembic_environment"
    ) as config:
        from alembic.script import ScriptDirectory

        scripts = ScriptDirectory.from_config(config)
        assert scripts.get_current_head() == "0035"


def test_upgrade_0028_to_0029_creates_overlay_tables_and_rollback_drops_them(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path, "0028")
    _migrate(database_path, "0029")
    connection = _connect(database_path)
    try:
        tables = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "media_user_aliases" in tables
        assert "media_user_alias_tags" in tables
        assert "x_claim_pending_aliases" in tables
        assert "x_claim_pending_alias_tags" in tables
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("PRAGMA integrity_check").fetchall() == [("ok",)]
    finally:
        connection.close()

    _migrate(database_path, "0028", downgrade=True)
    connection = _connect(database_path)
    try:
        tables = {
            item[0]
            for item in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        assert "media_user_aliases" not in tables
        assert "media_user_alias_tags" not in tables
        assert "x_claim_pending_aliases" not in tables
        assert "x_claim_pending_alias_tags" not in tables
        assert "x_post_claims" in tables
    finally:
        connection.close()


def test_overlay_rows_are_isolated_by_login_key(tmp_path: Path) -> None:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    metadata.create_all(connection)
    media_id = MediaId.from_string(MEDIA_ID)
    connection.execute(
        text(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', 1, 1)"
        ),
        {"id": MEDIA_ID},
    )
    connection.execute(
        text(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('meme', 'Meme', 1, 1)"
        )
    )
    connection.commit()
    repository = SqliteMediaUserAliasRepository(engine)
    alice = parse_alias_content("Alice title", "Alice desc", ["meme"])
    bob = parse_alias_content("Bob title", None, None)
    repository.upsert_alias(media_id, ALICE, alice, 10)
    repository.upsert_alias(media_id, BOB, bob, 11)
    loaded_alice = repository.get_alias(media_id, ALICE)
    loaded_bob = repository.get_alias(media_id, BOB)
    assert loaded_alice is not None and loaded_alice.content.display_title is not None
    assert loaded_alice.content.display_title.value == "Alice title"
    assert loaded_bob is not None and loaded_bob.content.display_title is not None
    assert loaded_bob.content.display_title.value == "Bob title"
    assert loaded_alice.content.tag_keys[0].value == "meme"
    assert loaded_bob.content.tag_keys == ()
    listed = repository.list_aliases_for_media(media_id)
    assert [item.login_key for item in listed] == [ALICE, BOB]
    connection.close()


def test_catalog_removal_deletes_overlay_tags_then_rows() -> None:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    metadata.create_all(connection)
    connection.execute(
        text(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', 1, 1)"
        ),
        {"id": MEDIA_ID},
    )
    connection.execute(
        text(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('meme', 'Meme', 1, 1)"
        )
    )
    connection.execute(
        text(
            "INSERT INTO media_user_aliases "
            "(media_id, login_key, display_title, description, created_at_ms, updated_at_ms) "
            "VALUES (:media, :login, 'Alias', NULL, 30, 30)"
        ),
        {"media": MEDIA_ID, "login": ALICE},
    )
    connection.execute(
        text(
            "INSERT INTO media_user_alias_tags "
            "(media_id, login_key, tag_key, position) "
            "VALUES (:media, :login, 'meme', 0)"
        ),
        {"media": MEDIA_ID, "login": ALICE},
    )
    connection.commit()

    def operation(active) -> None:
        _delete_metadata_graph(active, MediaId.from_string(MEDIA_ID))

    run_in_transaction(engine, operation)
    remaining_tags = connection.execute(
        text("SELECT count(*) FROM media_user_alias_tags WHERE media_id = :id"),
        {"id": MEDIA_ID},
    ).scalar_one()
    remaining_rows = connection.execute(
        text("SELECT count(*) FROM media_user_aliases WHERE media_id = :id"),
        {"id": MEDIA_ID},
    ).scalar_one()
    assert remaining_tags == 0
    assert remaining_rows == 0
    connection.close()
