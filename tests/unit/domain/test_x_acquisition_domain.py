"""Pure-domain tests for requester-private X acquisition."""

from __future__ import annotations

import pytest

from kronika.domain.media_classification import AcquisitionSource, ContentCategory
from kronika.domain.x_acquisition import (
    MAX_ASSETS_PER_POST,
    FrameNestXAssetError,
    FrameNestXClaimError,
    FrameNestXTransitionError,
    FrameNestXUrlError,
    XAcquisitionState,
    XAsset,
    XAssetState,
    XFailureStage,
    XMediaType,
    XPostClaim,
    accept_x_post_url,
    default_x_category,
    derive_x_requester_phase,
    is_claim_wide_x_failure,
    is_retryable_x_failure,
    normalize_x_creator,
    x_title_from_post_post,
)


def _claim() -> XPostClaim:
    return XPostClaim.new(
        submitted_url="https://x.com/author/status/1234567890123456789",
        now_ms=1000,
        created_by_login_key="alice",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://x.com/handle/status/1234567890123456789",
        "https://www.x.com/handle/status/42",
        "https://twitter.com/handle/status/123",
        "https://www.twitter.com/handle/status/123",
    ],
)
def test_accepts_supported_forms(url: str) -> None:
    identity = accept_x_post_url(url)
    assert identity.post_id
    assert identity.extractor_key == "X"
    assert identity.canonical_url.endswith(f"/status/{identity.post_id}")


@pytest.mark.parametrize(
    "url",
    [
        "http://x.com/handle/status/123",
        "https://x.com/handle/status/abc",
        "https://x.com/handle/status/123/extra",
        "https://x.com/handle/photo/123",
        "https://x.com/handle",
        "https://x.com/",
        "https://example.com/handle/status/123",
        "https://x.com/handle/status/123?foo=1",
        "https://x.com/handle/status/123#frag",
        "https://user:pass@x.com/handle/status/123",
        "https://x.com/i/status/123",
        "https://m.x.com/handle/status/123",
        "",
        None,
        123,
    ],
)
def test_rejects_unsupported_forms(url: object) -> None:
    with pytest.raises(FrameNestXUrlError):
        accept_x_post_url(url)


def test_canonical_identity_is_numeric_post_id() -> None:
    identity = accept_x_post_url("https://twitter.com/author/status/987654321")
    assert identity.post_id == "987654321"
    # Handle is advisory; identity is the numeric post id.
    assert identity.canonical_url.startswith("https://x.com/")
    assert identity.canonical_url.endswith("/status/987654321")


def test_claim_initial_state_and_defaults() -> None:
    claim = _claim()
    assert claim.state is XAcquisitionState.SUBMITTED
    assert claim.acquisition_source is AcquisitionSource.X_MANUAL_CLAIM
    assert claim.created_by_login_key == "alice"
    assert claim.title == "X post 1234567890123456789"
    assert claim.requested_content_category is None


def test_claim_persists_requested_category() -> None:
    claim = XPostClaim.new(
        submitted_url="https://x.com/author/status/1234567890123456789",
        now_ms=1000,
        created_by_login_key="alice",
        requested_content_category=ContentCategory.MOVIE,
    )
    assert claim.requested_content_category is ContentCategory.MOVIE
    queued = claim.advance(XAcquisitionState.QUEUED, updated_at_ms=2000)
    assert queued.requested_content_category is ContentCategory.MOVIE


def test_invalid_requested_category_rejected() -> None:
    claim = _claim()
    with pytest.raises(FrameNestXClaimError):
        XPostClaim(
            id=claim.id,
            state=XAcquisitionState.SUBMITTED,
            submitted_url=claim.submitted_url,
            canonical_url=claim.canonical_url,
            x_post_id=claim.x_post_id,
            extractor_key=claim.extractor_key,
            created_at_ms=1000,
            updated_at_ms=1000,
            requested_content_category="tiktok",  # type: ignore[arg-type]
        )


def test_transition_graph_enforced() -> None:
    claim = _claim()
    with pytest.raises(FrameNestXTransitionError):
        claim.advance(XAcquisitionState.COMPLETED, updated_at_ms=2000)
    queued = claim.advance(XAcquisitionState.QUEUED, updated_at_ms=2000)
    assert queued.version == 1
    extracting = queued.advance(XAcquisitionState.EXTRACTING, updated_at_ms=3000)
    assert extracting.state is XAcquisitionState.EXTRACTING


def test_invalid_claim_rejected() -> None:
    claim = _claim()
    with pytest.raises(FrameNestXClaimError):
        XPostClaim(
            id=claim.id,
            state=XAcquisitionState.SUBMITTED,
            submitted_url=claim.submitted_url,
            canonical_url=claim.canonical_url,
            x_post_id=claim.x_post_id,
            extractor_key="NotX",
            created_at_ms=1000,
            updated_at_ms=1000,
        )


def test_asset_ordinal_and_type() -> None:
    claim = _claim()
    asset = XAsset.new(
        claim_id=claim.id,
        ordinal=0,
        media_type=XMediaType.VIDEO,
        expected_mime="video/mp4",
        now_ms=1000,
    )
    assert asset.state is XAssetState.PENDING
    assert len(asset.stage_key) == 32
    assert asset.media_type is XMediaType.VIDEO
    with pytest.raises(FrameNestXAssetError):
        XAsset.new(
            claim_id=claim.id,
            ordinal=MAX_ASSETS_PER_POST,
            media_type=XMediaType.IMAGE,
            expected_mime="image/jpeg",
            now_ms=1000,
        )


def test_asset_transition_and_cataloged_payload() -> None:
    claim = _claim()
    asset = XAsset.new(
        claim_id=claim.id,
        ordinal=0,
        media_type=XMediaType.IMAGE,
        expected_mime="image/jpeg",
        now_ms=1000,
    )
    extracted = asset.advance(XAssetState.EXTRACTED, updated_at_ms=2000)
    acquiring = extracted.advance(XAssetState.ACQUIRING, updated_at_ms=2500)
    staged = acquiring.advance(
        XAssetState.STAGED,
        updated_at_ms=3000,
        acquired_bytes=1024,
        acquired_sha256="a" * 64,
    )
    handing_off = staged.advance(XAssetState.HANDING_OFF, updated_at_ms=3500)
    # Cataloged requires media/location linkage which is absent here.
    with pytest.raises(FrameNestXAssetError):
        handing_off.advance(
            XAssetState.CATALOGED,
            updated_at_ms=4000,
            acquired_bytes=1024,
            acquired_sha256="a" * 64,
        )


def test_category_defaults() -> None:
    assert default_x_category(XMediaType.VIDEO) is ContentCategory.MEME
    assert default_x_category(XMediaType.ANIMATED_GIF) is ContentCategory.MEME
    assert default_x_category(XMediaType.IMAGE) is ContentCategory.GENERAL


def test_parse_requested_category_accepts_four_values() -> None:
    from kronika.domain.x_acquisition import parse_x_requested_content_category

    for value in ("general", "meme", "movie", "youtube"):
        assert parse_x_requested_content_category(value).value == value
    with pytest.raises(FrameNestXClaimError):
        parse_x_requested_content_category("tiktok")


def test_creator_normalization() -> None:
    stable, handle, display = normalize_x_creator(
        stable_id="  user_123  ",
        handle="@Author",
        display_name=" Author  Name ",
    )
    assert stable == "user_123"
    assert handle == "author"
    assert display == "Author  Name  ".strip() or False


def test_title_from_post_text_and_fallbacks() -> None:
    assert x_title_from_post_post(
        "This is a great clip", creator_handle="author", media_type_label="video", post_id="123", ordinal=0
    ) == "This is a great clip"
    # Link/hashtag-only text must not produce a useless title.
    assert x_title_from_post_post(
        "https://t.co/abc", creator_handle="author", media_type_label="video", post_id="123", ordinal=0
    ) == "author video"
    assert x_title_from_post_post(
        "@mention", creator_handle="author", media_type_label="video", post_id="123", ordinal=0
    ) == "author video"
    assert x_title_from_post_post(
        "", creator_handle=None, media_type_label="video", post_id="999", ordinal=0
    ) == "X post 999"
    multi = x_title_from_post_post(
        "Clip", creator_handle="author", media_type_label="image", post_id="123", ordinal=1
    )
    assert multi == "Clip (2)"


def test_requester_phase_mapping() -> None:
    claim = _claim()
    assert derive_x_requester_phase(claim) == "queued"
    completed = (
        claim.advance(XAcquisitionState.QUEUED, updated_at_ms=2000)
        .advance(XAcquisitionState.EXTRACTING, updated_at_ms=3000)
        .advance(XAcquisitionState.ACQUIRING, updated_at_ms=4000)
        .advance(XAcquisitionState.HANDING_OFF, updated_at_ms=5000)
        .advance(
            XAcquisitionState.COMPLETED, updated_at_ms=6000, completed_at_ms=6000
        )
    )
    assert derive_x_requester_phase(completed) == "completed"
    failed = claim.advance(
        XAcquisitionState.FAILED,
        updated_at_ms=3000,
        failure_stage=XFailureStage.EXTRACTION,
        failure_code="X_NO_SUPPORTED_MEDIA",
        completed_at_ms=3000,
    )
    assert derive_x_requester_phase(failed) == "failed"


def test_failure_retryability() -> None:
    assert is_retryable_x_failure("X_RATE_LIMITED")
    assert is_retryable_x_failure("X_DOWNLOAD_TIMEOUT")
    assert not is_retryable_x_failure("X_AUTHENTICATION_REQUIRED")
    assert not is_retryable_x_failure("X_NO_SUPPORTED_MEDIA")
    assert not is_retryable_x_failure("X_SOURCE_MEDIA_CHANGED")
    assert not is_retryable_x_failure("X_MULTI_ASSET_FAILED")
    assert is_claim_wide_x_failure("X_POST_DELETED")
    assert is_claim_wide_x_failure("X_MULTI_ASSET_FAILED")
    assert not is_claim_wide_x_failure("X_SOURCE_MEDIA_CHANGED")
    assert not is_claim_wide_x_failure("X_MEDIA_TYPE_UNSUPPORTED")
    assert not is_claim_wide_x_failure("X_DOWNLOAD_TIMEOUT")


def test_retry_reset_transitions_allowed() -> None:
    claim = _claim()
    asset = XAsset.new(
        claim_id=claim.id, ordinal=0, media_type=XMediaType.VIDEO,
        expected_mime="video/mp4", now_ms=1000,
    )
    failed = asset.advance(
        XAssetState.FAILED, updated_at_ms=2000,
        failure_stage=XFailureStage.ACQUISITION, failure_code="X_DOWNLOAD_TIMEOUT",
    )
    reset = failed.advance(
        XAssetState.PENDING, updated_at_ms=3000, failure_stage=None, failure_code=None,
    )
    assert reset.state is XAssetState.PENDING
    # Post retry transitions are legal on terminal partial/failed states.
    partial = claim.advance(XAcquisitionState.QUEUED, updated_at_ms=2000)
    partial = partial.advance(XAcquisitionState.EXTRACTING, updated_at_ms=3000)
    partial = partial.advance(XAcquisitionState.ACQUIRING, updated_at_ms=4000)
    partial = partial.advance(XAcquisitionState.HANDING_OFF, updated_at_ms=5000)
    partial = partial.advance(
        XAcquisitionState.COMPLETED_PARTIAL, updated_at_ms=6000, completed_at_ms=6000,
    )
    resumed = partial.advance(
        XAcquisitionState.ACQUIRING, updated_at_ms=7000,
        completed_at_ms=None, failure_stage=None, failure_code=None,
    )
    assert resumed.state is XAcquisitionState.ACQUIRING
    failed_post = _claim().advance(
        XAcquisitionState.FAILED, updated_at_ms=8000,
        failure_stage=XFailureStage.EXTRACTION, failure_code="X_NO_SUPPORTED_MEDIA",
        completed_at_ms=8000,
    )
    requeued = failed_post.advance(
        XAcquisitionState.QUEUED, updated_at_ms=9000,
        completed_at_ms=None, failure_stage=None, failure_code=None,
    )
    assert requeued.state is XAcquisitionState.QUEUED
