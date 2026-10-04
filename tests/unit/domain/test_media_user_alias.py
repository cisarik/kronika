"""Unit tests for per-user media alias overlay domain values."""

from __future__ import annotations

import pytest

from tests.support.kronika_identity import expected

from framenest.domain import MediaId
from framenest.domain.media_metadata import CanonicalTagKey, MediaDescription, MediaDisplayTitle
from framenest.domain.media_user_alias import (
    FrameNestMediaUserAliasError,
    MediaUserAlias,
    MediaUserAliasContent,
    PendingMediaUserAlias,
    parse_alias_content,
)
from framenest.domain.x_acquisition import XPostClaimId

MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
CLAIM_ID = XPostClaimId.from_string("22345678-1234-4234-9234-123456789abc")
LOGIN = "alice@example.com"


def test_empty_content_means_no_row() -> None:
    content = parse_alias_content(None, None, None)
    assert content.is_empty()
    content = parse_alias_content("", "   ", [])
    assert content.is_empty()


def test_parse_alias_content_accepts_title_description_and_tags() -> None:
    content = parse_alias_content("My title", "A description.", ["meme", "reaction"])
    assert content.display_title == MediaDisplayTitle("My title")
    assert content.description == MediaDescription("A description.")
    assert content.tag_keys == (CanonicalTagKey("meme"), CanonicalTagKey("reaction"))
    assert not content.is_empty()


def test_parse_alias_content_rejects_invalid_title_and_unknown_shape() -> None:
    with pytest.raises(FrameNestMediaUserAliasError):
        parse_alias_content(" Title", None, None)
    with pytest.raises(FrameNestMediaUserAliasError):
        parse_alias_content("ok", None, ["Not-A-Key"])


def test_rejected_alias_reports_the_derived_brand() -> None:
    with pytest.raises(FrameNestMediaUserAliasError) as excinfo:
        parse_alias_content(" Title", None, None)

    assert str(excinfo.value) == expected("Invalid {brand} media user alias.")


def test_persisted_alias_rejects_empty_content_and_unnormalized_login() -> None:
    content = parse_alias_content("Title", None, None)
    with pytest.raises(FrameNestMediaUserAliasError):
        MediaUserAlias(
            media_id=MEDIA_ID,
            login_key=LOGIN,
            content=MediaUserAliasContent(
                display_title=None, description=None, tag_keys=()
            ),
            created_at_ms=1,
            updated_at_ms=1,
        )
    with pytest.raises(FrameNestMediaUserAliasError):
        MediaUserAlias(
            media_id=MEDIA_ID,
            login_key="Alice@Example.com",
            content=content,
            created_at_ms=1,
            updated_at_ms=1,
        )
    alias = MediaUserAlias(
        media_id=MEDIA_ID,
        login_key=LOGIN,
        content=content,
        created_at_ms=1,
        updated_at_ms=2,
    )
    assert alias.login_key == LOGIN


def test_pending_alias_rejects_empty_content() -> None:
    content = parse_alias_content("Pending", None, None)
    pending = PendingMediaUserAlias(
        claim_id=CLAIM_ID,
        login_key=LOGIN,
        content=content,
        created_at_ms=1,
        updated_at_ms=1,
    )
    assert pending.claim_id == CLAIM_ID
    with pytest.raises(FrameNestMediaUserAliasError):
        PendingMediaUserAlias(
            claim_id=CLAIM_ID,
            login_key=LOGIN,
            content=parse_alias_content(None, None, None),
            created_at_ms=1,
            updated_at_ms=1,
        )
