"""Unit tests for YouTube category and creator attribution normalization."""

from __future__ import annotations

import unicodedata

import pytest

from kronika.domain.identities import MediaId
from kronika.domain.media_classification import ContentCategory, CreatorAttributionKind
from kronika.domain.media_metadata import (
    FrameNestMediaMetadataError,
    MediaMetadata,
    normalize_creator_display_name,
    normalize_creator_handle,
    normalize_creator_stable_id,
    validate_creator_attribution_fields,
)


def test_youtube_is_valid_content_category() -> None:
    assert ContentCategory.YOUTUBE.value == "youtube"
    assert ContentCategory("youtube") is ContentCategory.YOUTUBE


def test_invalid_content_categories_remain_rejected() -> None:
    with pytest.raises(ValueError):
        ContentCategory("tiktok")
    with pytest.raises(FrameNestMediaMetadataError):
        MediaMetadata(
            media_id=MediaId.new(),
            display_title=None,
            description=None,
            tag_keys=(),
            created_at_ms=1,
            updated_at_ms=1,
            content_category="youtube",  # type: ignore[arg-type]
        )


def test_creator_display_name_uses_nfc_and_trimming() -> None:
    decomposed = "Cafe\u0301"
    assert unicodedata.normalize("NFC", decomposed) == "Café"
    assert normalize_creator_display_name(f"  {decomposed}  ") == "Café"
    assert normalize_creator_display_name("   ") is None
    assert normalize_creator_display_name(None) is None


def test_creator_stable_id_preserves_exact_text_after_trimming() -> None:
    assert normalize_creator_stable_id("  UC_AbCdEf  ") == "UC_AbCdEf"
    assert normalize_creator_stable_id("   ") is None


def test_creator_handle_removes_at_lowercases_and_trims() -> None:
    assert normalize_creator_handle("  @@ExampleHandle  ") == "examplehandle"
    assert normalize_creator_handle("@MixedCASE") == "mixedcase"
    assert normalize_creator_handle("   ") is None


def test_blank_creator_values_become_null() -> None:
    kind, stable, handle, display = validate_creator_attribution_fields(
        CreatorAttributionKind.YOUTUBE_CHANNEL,
        "  UC123  ",
        "  ",
        "  Channel  ",
    )
    assert kind is CreatorAttributionKind.YOUTUBE_CHANNEL
    assert stable == "UC123"
    assert handle is None
    assert display == "Channel"


def test_invalid_kind_or_inconsistent_combinations_are_rejected() -> None:
    with pytest.raises(FrameNestMediaMetadataError):
        validate_creator_attribution_fields(None, "UC123", None, None)
    with pytest.raises(FrameNestMediaMetadataError):
        validate_creator_attribution_fields(
            CreatorAttributionKind.YOUTUBE_CHANNEL,
            None,
            None,
            None,
        )
    with pytest.raises(ValueError):
        CreatorAttributionKind("none")
    with pytest.raises(FrameNestMediaMetadataError):
        MediaMetadata(
            media_id=MediaId.new(),
            display_title=None,
            description=None,
            tag_keys=(),
            created_at_ms=1,
            updated_at_ms=1,
            creator_attribution_kind=CreatorAttributionKind.X_AUTHOR,
            creator_stable_id=None,
            creator_handle=None,
            creator_display_name=None,
        )


def test_media_metadata_accepts_youtube_category_and_creator() -> None:
    metadata = MediaMetadata(
        media_id=MediaId.new(),
        display_title=None,
        description=None,
        tag_keys=(),
        created_at_ms=1,
        updated_at_ms=1,
        content_category=ContentCategory.YOUTUBE,
        creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
        creator_stable_id="UC123",
        creator_display_name="Example Channel",
    )
    assert metadata.content_category is ContentCategory.YOUTUBE
    assert metadata.creator_handle is None
