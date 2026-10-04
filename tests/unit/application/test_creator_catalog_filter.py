"""Unit evidence for creator catalog filtering identity rules."""

from __future__ import annotations

import pytest

from kronika.domain.record_access import RecordAccessScope
from kronika.application.media_catalog import (
    ListMediaCatalog,
    MediaCatalogValidationError,
)
from kronika.application.ports.media_catalog_repository import (
    CatalogMediaItem,
    MediaCatalogPage,
    MediaCatalogQuery,
)
from kronika.domain.media_metadata import CanonicalTagKey


class _FakeCatalogRepository:
    def __init__(self, items: tuple[CatalogMediaItem, ...]) -> None:
        self.items = items
        self.queries: list[MediaCatalogQuery] = []

    def list_media(self, query: MediaCatalogQuery) -> MediaCatalogPage:
        self.queries.append(query)
        filtered = []
        for item in self.items:
            if query.content_category and item.content_category != query.content_category:
                continue
            if query.creator_attribution_kind:
                if item.creator_attribution_kind != query.creator_attribution_kind:
                    continue
                if query.creator_stable_id is not None:
                    if item.creator_stable_id != query.creator_stable_id:
                        continue
                elif query.creator_handle is not None:
                    if item.creator_handle != query.creator_handle:
                        continue
            filtered.append(item)
        return MediaCatalogPage(
            items=tuple(filtered),
            total=len(filtered),
            limit=query.limit,
            offset=query.offset,
            q=query.q,
            tag_keys=query.tag_keys,
            content_category=query.content_category,
            acquisition_source=query.acquisition_source,
            creator_attribution_kind=query.creator_attribution_kind,
            creator_stable_id=query.creator_stable_id,
            creator_handle=query.creator_handle,
        )

    def get_media_item(self, media_id: str) -> CatalogMediaItem | None:
        for item in self.items:
            if item.media_id == media_id:
                return item
        return None


def _item(
    media_id: str,
    *,
    stable_id: str | None,
    handle: str | None,
    display_name: str | None,
) -> CatalogMediaItem:
    return CatalogMediaItem(
        media_id=media_id,
        media_kind="video",
        created_at_ms=1,
        updated_at_ms=1,
        display_title=media_id,
        collection_key=None,
        processed_at_ms=None,
        tags=(),
        locations=(),
        content_category="youtube",
        acquisition_source="youtube_manual_claim",
        creator_attribution_kind="youtube_channel",
        creator_stable_id=stable_id,
        creator_handle=handle,
        creator_display_name=display_name,
    )


def test_stable_id_filtering_returns_correct_creator_media() -> None:
    repository = _FakeCatalogRepository(
        (
            _item("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", stable_id="UC1", handle=None, display_name="A"),
            _item("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb", stable_id="UC2", handle=None, display_name="B"),
        )
    )
    page = ListMediaCatalog(repository).execute(
        access_scope=RecordAccessScope.legacy_public(),
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC1",
    )
    assert [item.media_id for item in page.items] == [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ]
    assert repository.queries[0].creator_handle is None
    assert repository.queries[0].published_only is True


def test_normalized_handle_fallback_works() -> None:
    repository = _FakeCatalogRepository(
        (
            _item(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                stable_id=None,
                handle="alice",
                display_name="Alice",
            ),
            _item(
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                stable_id=None,
                handle="bob",
                display_name="Bob",
            ),
        )
    )
    page = ListMediaCatalog(repository).execute(
        access_scope=RecordAccessScope.legacy_public(),
        creator_attribution_kind="youtube_channel",
        creator_handle="@@Alice",
    )
    assert [item.media_id for item in page.items] == [
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    ]
    assert repository.queries[0].creator_handle == "alice"


def test_display_name_collisions_do_not_merge_identities() -> None:
    repository = _FakeCatalogRepository(
        (
            _item(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                stable_id="UC1",
                handle=None,
                display_name="Same Name",
            ),
            _item(
                "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                stable_id="UC2",
                handle=None,
                display_name="Same Name",
            ),
        )
    )
    page = ListMediaCatalog(repository).execute(
        access_scope=RecordAccessScope.legacy_public(),
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC1",
    )
    assert len(page.items) == 1
    assert page.items[0].creator_stable_id == "UC1"


def test_renamed_display_names_do_not_break_stable_id_filtering() -> None:
    repository = _FakeCatalogRepository(
        (
            _item(
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
                stable_id="UC1",
                handle=None,
                display_name="Renamed Later",
            ),
        )
    )
    page = ListMediaCatalog(repository).execute(
        access_scope=RecordAccessScope.legacy_public(),
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC1",
    )
    assert page.items[0].creator_display_name == "Renamed Later"


def test_display_name_only_filter_is_rejected() -> None:
    repository = _FakeCatalogRepository(())
    with pytest.raises(MediaCatalogValidationError):
        ListMediaCatalog(repository).execute(
            access_scope=RecordAccessScope.legacy_public(),
            creator_attribution_kind="youtube_channel",
        )


def test_creator_filter_keeps_published_only_audience_gate() -> None:
    repository = _FakeCatalogRepository(())
    ListMediaCatalog(repository).execute(
        access_scope=RecordAccessScope.legacy_public(),
        creator_attribution_kind="x_author",
        creator_handle="someone",
    )
    assert repository.queries[0].published_only is True
    assert repository.queries[0].tag_keys == ()
    assert isinstance(CanonicalTagKey("alpha"), CanonicalTagKey)
