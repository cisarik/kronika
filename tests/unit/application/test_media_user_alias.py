"""Unit tests for caller-private media alias overlay use cases."""

from __future__ import annotations

import pytest

from kronika.application.media_user_alias import (
    GetMediaUserAlias,
    ListMediaUserAliasesForLogin,
    ListTeamMediaAliases,
    SaveMediaUserAlias,
)
from kronika.application.ports.media_user_alias_repository import AliasTagNotFoundError
from kronika.domain import MediaId
from kronika.domain.media_metadata import CanonicalTagKey
from kronika.domain.media_user_alias import (
    FrameNestMediaUserAliasError,
    MediaUserAlias,
    MediaUserAliasContent,
    parse_alias_content,
)

MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")
LOGIN = "alice@example.com"


class _FakeRepository:
    def __init__(self) -> None:
        self.aliases: dict[tuple[str, str], MediaUserAlias] = {}
        self.known_tags = {CanonicalTagKey("meme")}
        self.upserts: list[object] = []
        self.media_exists = True

    def get_alias(self, media_id: MediaId, login_key: str) -> MediaUserAlias | None:
        return self.aliases.get((media_id.to_string(), login_key))

    def list_aliases_for_media(self, media_id: MediaId) -> tuple[MediaUserAlias, ...]:
        from kronika.application.ports.media_user_alias_repository import (
            MediaUserAliasMediaNotFoundError,
        )

        if not self.media_exists:
            raise MediaUserAliasMediaNotFoundError()
        matches = [
            alias
            for (stored_id, _), alias in self.aliases.items()
            if stored_id == media_id.to_string()
        ]
        return tuple(sorted(matches, key=lambda item: item.login_key))

    def upsert_alias(
        self,
        media_id: MediaId,
        login_key: str,
        content: MediaUserAliasContent,
        now_ms: int,
    ) -> MediaUserAlias | None:
        self.upserts.append((media_id, login_key, content, now_ms))
        key = (media_id.to_string(), login_key)
        if content.is_empty():
            self.aliases.pop(key, None)
            return None
        current = self.aliases.get(key)
        created = now_ms if current is None else current.created_at_ms
        alias = MediaUserAlias(
            media_id=media_id,
            login_key=login_key,
            content=content,
            created_at_ms=created,
            updated_at_ms=now_ms,
        )
        self.aliases[key] = alias
        return alias

    def delete_alias(self, media_id: MediaId, login_key: str) -> None:
        self.aliases.pop((media_id.to_string(), login_key), None)

    def list_aliases_for_login(
        self, login_key: str, media_ids: tuple[MediaId, ...]
    ) -> dict[str, MediaUserAlias]:
        result: dict[str, MediaUserAlias] = {}
        for media_id in media_ids:
            alias = self.aliases.get((media_id.to_string(), login_key))
            if alias is not None:
                result[media_id.to_string()] = alias
        return result

    def canonical_tag_display_names(self, tag_keys: tuple[str, ...]) -> dict[str, str]:
        return {key: key for key in tag_keys}

    def canonical_tag_keys_exist(self, tag_keys: tuple[CanonicalTagKey, ...]) -> bool:
        return all(key in self.known_tags for key in tag_keys)


def test_get_alias_returns_empty_view_when_absent() -> None:
    view = GetMediaUserAlias(_FakeRepository()).execute(MEDIA_ID.to_string(), LOGIN)
    assert view.display_title is None
    assert view.description is None
    assert view.tag_keys == ()
    assert view.persisted is False


def test_save_alias_persists_and_empty_deletes() -> None:
    repository = _FakeRepository()
    save = SaveMediaUserAlias(repository, clock_ms=lambda: 50)
    view = save.execute(MEDIA_ID.to_string(), LOGIN, "Alias title", None, ["meme"])
    assert view.persisted is True
    assert view.display_title == "Alias title"
    assert view.tag_keys == ("meme",)
    empty = save.execute(MEDIA_ID.to_string(), LOGIN, None, None, None)
    assert empty.persisted is False
    assert repository.get_alias(MEDIA_ID, LOGIN) is None


def test_save_alias_rejects_unknown_tag() -> None:
    repository = _FakeRepository()
    save = SaveMediaUserAlias(repository, clock_ms=lambda: 50)
    with pytest.raises(AliasTagNotFoundError):
        save.execute(MEDIA_ID.to_string(), LOGIN, "Title", None, ["missing"])


def test_save_alias_rejects_invalid_content() -> None:
    repository = _FakeRepository()
    save = SaveMediaUserAlias(repository, clock_ms=lambda: 50)
    with pytest.raises(FrameNestMediaUserAliasError):
        save.execute(MEDIA_ID.to_string(), LOGIN, " Title", None, None)
    assert parse_alias_content("ok", None, None).display_title is not None


def test_list_team_aliases_aggregates_by_login_and_unknown_media_is_not_found() -> None:
    from kronika.application.ports.media_user_alias_repository import (
        MediaUserAliasMediaNotFoundError,
    )

    repository = _FakeRepository()
    save = SaveMediaUserAlias(repository, clock_ms=lambda: 50)
    save.execute(MEDIA_ID.to_string(), "bob@example.com", "Bob title", None, None)
    save.execute(MEDIA_ID.to_string(), LOGIN, "Alice title", None, ["meme"])
    listed = ListTeamMediaAliases(repository).execute(MEDIA_ID.to_string())
    assert [entry.login_key for entry in listed] == [LOGIN, "bob@example.com"]
    assert listed[0].display_title == "Alice title"
    assert listed[1].display_title == "Bob title"
    own = GetMediaUserAlias(repository).execute(MEDIA_ID.to_string(), LOGIN)
    assert own.display_title == "Alice title"
    repository.media_exists = False
    with pytest.raises(MediaUserAliasMediaNotFoundError):
        ListTeamMediaAliases(repository).execute(MEDIA_ID.to_string())


def test_list_aliases_for_login_does_not_leak_another_caller_overlay() -> None:
    repository = _FakeRepository()
    save = SaveMediaUserAlias(repository, clock_ms=lambda: 50)
    save.execute(MEDIA_ID.to_string(), LOGIN, "Alice title", None, ["meme"])
    save.execute(MEDIA_ID.to_string(), "bob@example.com", "Bob title", None, None)
    listed = ListMediaUserAliasesForLogin(repository)
    alice = listed.execute(LOGIN, [MEDIA_ID.to_string()])
    bob = listed.execute("bob@example.com", [MEDIA_ID.to_string()])
    assert alice.overlays[MEDIA_ID.to_string()].display_title == "Alice title"
    assert bob.overlays[MEDIA_ID.to_string()].display_title == "Bob title"
    assert "Bob title" not in {
        view.display_title for view in alice.overlays.values()
    }
    assert alice.tag_display_names["meme"] == "meme"
