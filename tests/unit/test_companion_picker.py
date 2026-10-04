"""Unit tests for the companion meme picker query boundary."""

from __future__ import annotations

import pytest

from kronika.application.companion_picker import (
    COMPANION_API_VERSION,
    CompanionPickerLocation,
    ListCompanionPickerMedia,
)
from kronika.application.media_catalog import MediaCatalogValidationError
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    CatalogMediaLocation,
    CatalogMediaTag,
    MediaCatalogPage,
    MediaCatalogQuery,
)
from kronika.domain.media_classification import ContentCategory


class _Repository:
    def __init__(self, page: MediaCatalogPage | None = None) -> None:
        self.page = page
        self.queries: list[MediaCatalogQuery] = []

    def list_media(self, query: MediaCatalogQuery) -> MediaCatalogPage:
        self.queries.append(query)
        assert self.page is not None
        return self.page

    def get_media_item(self, media_id: str) -> CatalogMediaItem | None:
        return None


def _item(*, media_id: str = "11111111-1111-4111-8111-111111111111") -> CatalogMediaItem:
    return CatalogMediaItem(
        media_id=media_id,
        media_kind="image",
        created_at_ms=50,
        updated_at_ms=50,
        display_title="Published JPEG meme",
        collection_key=None,
        processed_at_ms=None,
        tags=(CatalogMediaTag(key="reaction", display_name="Reaction", position=0),),
        locations=(
            CatalogMediaLocation(
                location_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                library_id="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                relative_path="memes/still.jpg",
                availability="available",
                observed_size_bytes=12,
                observed_mtime_ns=1,
            ),
        ),
        content_category=ContentCategory.MEME.value,
    )


def test_picker_query_uses_caller_audience_and_meme_category() -> None:
    page = MediaCatalogPage(
        items=(_item(),),
        total=1,
        limit=24,
        offset=0,
        q=None,
        tag_keys=(),
        next_cursor=None,
    )
    repository = _Repository(page)
    result = ListCompanionPickerMedia(repository).execute(login_key="alice@example.com")
    query = repository.queries[0]
    assert query.published_only is False
    assert query.companion_audience_login_key == "alice@example.com"
    assert query.content_category == ContentCategory.MEME.value
    assert query.offset == 0
    assert result.companion_api_version == COMPANION_API_VERSION
    assert result.items[0].location == CompanionPickerLocation(
        location_id="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        media_type="image/jpeg",
        observed_size_bytes=12,
    )
    assert result.items[0].display_title == "Published JPEG meme"


@pytest.mark.parametrize(
    "cursor",
    ["not-a-cursor", "abc:11111111-1111-4111-8111-111111111111", "1:not-uuid", ""],
)
def test_invalid_cursor_is_rejected(cursor: str) -> None:
    with pytest.raises(MediaCatalogValidationError):
        ListCompanionPickerMedia(_Repository()).execute(
            login_key="alice@example.com", cursor=cursor
        )


def test_kind_filter_must_be_a_supported_companion_kind() -> None:
    with pytest.raises(MediaCatalogValidationError):
        ListCompanionPickerMedia(_Repository()).execute(
            login_key="alice@example.com", kind="audio"
        )


def test_limit_is_capped_at_fifty() -> None:
    with pytest.raises(MediaCatalogValidationError):
        ListCompanionPickerMedia(_Repository()).execute(
            login_key="alice@example.com", limit=51
        )
