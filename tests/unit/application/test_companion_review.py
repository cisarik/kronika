"""Unit tests for companion review historical codec, mapping, and cursors."""

from __future__ import annotations

import json

import pytest

from kronika.application.companion_review import (
    CanonicalTagView,
    CompanionReviewCodecError,
    CompanionReviewQueryError,
    MappedTagStatus,
    decode_companion_review_inbox_cursor,
    decode_companion_review_cursor,
    decode_stored_suggestion_result,
    encode_companion_review_cursor,
    encode_companion_review_inbox_cursor,
    inbox_title,
    is_ordered_subsequence,
    map_suggested_tags,
    pending_inbox_title,
    validate_companion_review_apply_request,
)
from kronika.application.media_suggestion import (
    FrameNestMediaSuggestionError,
    MediaSuggestion,
    PROMPT_VERSION,
)


def _stored_json(*, tags: list[str], title: str = "Stored title") -> str:
    return json.dumps(
        {
            "collection": "memes",
            "confidence": 0.9,
            "description": "A description.",
            "evidence": ["visible subject"],
            "suggested_filename": "clip.gif",
            "tags": tags,
            "title": title,
            "uncertainties": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def test_historical_v3_json_with_twelve_tags_decodes() -> None:
    tags = [f"Tag {index}" for index in range(1, 13)]
    stored = decode_stored_suggestion_result(_stored_json(tags=tags, title="Twelve"))
    assert stored.title == "Twelve"
    assert stored.tags == tuple(tags)


def test_historical_codec_rejects_thirteen_tags_and_empty_tags() -> None:
    with pytest.raises(CompanionReviewCodecError):
        decode_stored_suggestion_result(
            _stored_json(tags=[f"Tag {index}" for index in range(1, 14)])
        )
    with pytest.raises(CompanionReviewCodecError):
        decode_stored_suggestion_result(_stored_json(tags=[]))


def test_historical_codec_rejects_corrupt_payloads() -> None:
    with pytest.raises(CompanionReviewCodecError):
        decode_stored_suggestion_result("{not-json")
    with pytest.raises(CompanionReviewCodecError):
        decode_stored_suggestion_result("[]")
    with pytest.raises(CompanionReviewCodecError):
        decode_stored_suggestion_result(
            json.dumps({"title": "T", "description": "D", "tags": "alpha"})
        )


def test_live_v4_media_suggestion_still_rejects_six_tags() -> None:
    with pytest.raises(FrameNestMediaSuggestionError):
        MediaSuggestion(
            title="Title",
            description="Valid description",
            collection="Home",
            tags=("a", "b", "c", "d", "e", "f"),
            suggested_filename="clip.mp4",
            confidence=0.5,
            evidence=("evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="model",
            prompt_version=PROMPT_VERSION,
        )


def test_tag_mapping_display_key_ambiguous_unknown_duplicate_and_legacy_limit() -> None:
    catalog = (
        CanonicalTagView(key="cats", display_name="Cats"),
        CanonicalTagView(key="dogs", display_name="Dogs"),
        CanonicalTagView(key="cat-a", display_name="Feline"),
        CanonicalTagView(key="cat-b", display_name="Feline"),
        CanonicalTagView(key="birds", display_name="Birds"),
        CanonicalTagView(key="fish", display_name="Fish"),
        CanonicalTagView(key="cars", display_name="Cars"),
        CanonicalTagView(key="trees", display_name="Trees"),
    )
    mapped = map_suggested_tags(
        (
            "Cats",
            "dogs",
            "Feline",
            "unknown-tag",
            "Birds",
            "Fish",
            "Cars",
            "Trees",
            "cats",
        ),
        catalog,
    )
    statuses = [(item.value, item.status, item.key) for item in mapped]
    assert statuses[0] == ("Cats", MappedTagStatus.MAPPED, "cats")
    assert statuses[1] == ("dogs", MappedTagStatus.MAPPED, "dogs")
    assert statuses[2] == ("Feline", MappedTagStatus.AMBIGUOUS, None)
    assert statuses[3] == ("unknown-tag", MappedTagStatus.UNKNOWN, None)
    assert statuses[4] == ("Birds", MappedTagStatus.MAPPED, "birds")
    assert statuses[5] == ("Fish", MappedTagStatus.MAPPED, "fish")
    assert statuses[6] == ("Cars", MappedTagStatus.MAPPED, "cars")
    assert statuses[7] == ("Trees", MappedTagStatus.LEGACY_LIMIT, "trees")
    assert statuses[8] == ("cats", MappedTagStatus.DUPLICATE, "cats")


def test_ambiguous_display_does_not_fall_back_to_key() -> None:
    catalog = (
        CanonicalTagView(key="wave", display_name="Wave"),
        CanonicalTagView(key="waves", display_name="Wave"),
    )
    mapped = map_suggested_tags(("Wave",), catalog)
    assert mapped[0].status is MappedTagStatus.AMBIGUOUS
    assert mapped[0].key is None


def test_inbox_title_prefers_nonblank_canonical() -> None:
    stored = decode_stored_suggestion_result(_stored_json(tags=["alpha"]))
    assert inbox_title(canonical_display_title="Canonical", stored=stored) == "Canonical"
    assert inbox_title(canonical_display_title="  ", stored=stored) == stored.title
    assert inbox_title(canonical_display_title=None, stored=stored) == stored.title
    assert (
        pending_inbox_title(
            canonical_display_title="Canonical X",
            claim_title="Claim title",
            x_post_id="123",
        )
        == "Canonical X"
    )
    assert (
        pending_inbox_title(
            canonical_display_title=" ",
            claim_title="Claim title",
            x_post_id="123",
        )
        == "Claim title"
    )
    assert (
        pending_inbox_title(
            canonical_display_title=None, claim_title=None, x_post_id="123"
        )
        == "X post 123"
    )


def test_cursor_round_trip_and_invalid_values() -> None:
    run_id = "11111111-1111-4111-8111-111111111111"
    encoded = encode_companion_review_cursor(
        completed_at_ms=42, analysis_run_id=run_id
    )
    assert decode_companion_review_cursor(encoded) == (42, run_id)
    assert decode_companion_review_inbox_cursor(encoded) == (42, True, run_id)
    mixed = encode_companion_review_inbox_cursor(
        activity_at_ms=41, analyzed=False, sort_id=run_id
    )
    assert decode_companion_review_inbox_cursor(mixed) == (41, False, run_id)
    with pytest.raises(CompanionReviewQueryError):
        decode_companion_review_cursor(mixed)
    assert decode_companion_review_cursor(None) is None
    assert decode_companion_review_inbox_cursor(None) is None
    with pytest.raises(CompanionReviewQueryError):
        decode_companion_review_cursor("%%%")
    with pytest.raises(CompanionReviewQueryError):
        decode_companion_review_cursor("not-base64")


def test_apply_request_validation_and_ordered_subsequence() -> None:
    validate_companion_review_apply_request(
        fields=("display_title", "tags"),
        tag_keys=("cats", "dogs"),
    )
    with pytest.raises(CompanionReviewQueryError):
        validate_companion_review_apply_request(fields=(), tag_keys=())
    with pytest.raises(CompanionReviewQueryError):
        validate_companion_review_apply_request(
            fields=("display_title", "display_title"),
            tag_keys=(),
        )
    with pytest.raises(CompanionReviewQueryError):
        validate_companion_review_apply_request(fields=("tags",), tag_keys=())
    with pytest.raises(CompanionReviewQueryError):
        validate_companion_review_apply_request(
            fields=("tags",),
            tag_keys=("a", "b", "c", "d", "e", "f"),
        )
    with pytest.raises(CompanionReviewQueryError):
        validate_companion_review_apply_request(
            fields=("display_title",), tag_keys=("cats",)
        )
    eligible = ("cats", "dogs", "birds")
    assert is_ordered_subsequence(("cats", "birds"), eligible) is True
    assert is_ordered_subsequence(("dogs", "cats"), eligible) is False
    assert is_ordered_subsequence(("trees",), eligible) is False
