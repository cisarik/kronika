"""Pending alias last-write-wins evidence on X claims."""

from __future__ import annotations

from sqlalchemy import create_engine, text

from kronika.domain.media_user_alias import parse_alias_content
from kronika.domain.x_acquisition import XPostClaim
from kronika.infrastructure.persistence.catalog_schema import metadata
from kronika.infrastructure.persistence.x_acquisition_claim_repository import (
    SqliteXAcquisitionClaimRepository,
)

URL = "https://x.com/author/status/987654321"
ALICE = "alice@example.com"


def test_pending_alias_last_write_wins_and_empty_deletes() -> None:
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}
    )
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    metadata.create_all(connection)
    connection.execute(
        text(
            "INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) "
            "VALUES ('meme', 'Meme', 1, 1)"
        )
    )
    connection.commit()
    repository = SqliteXAcquisitionClaimRepository(engine)
    claim = XPostClaim.new(submitted_url=URL, now_ms=10, created_by_login_key=ALICE)
    stored, created = repository.create_or_get_active(claim)
    assert created is True
    first = parse_alias_content("First", None, ["meme"])
    second = parse_alias_content("Second", "Later description.", None)
    repository.upsert_pending_alias(stored.id, ALICE, first, 20)
    repository.upsert_pending_alias(stored.id, ALICE, second, 30)
    pending = repository.get_pending_alias(stored.id)
    assert pending is not None
    assert pending.content.display_title is not None
    assert pending.content.display_title.value == "Second"
    assert pending.content.description is not None
    assert pending.content.description.value == "Later description."
    assert pending.content.tag_keys == ()
    assert pending.created_at_ms == 20
    assert pending.updated_at_ms == 30
    empty = parse_alias_content(None, None, None)
    repository.upsert_pending_alias(stored.id, ALICE, empty, 40)
    assert repository.get_pending_alias(stored.id) is None
    connection.close()
