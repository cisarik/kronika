"""Pure-domain evidence for YouTube manual-acquisition policy."""

from __future__ import annotations

import pytest

from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.uploads import UploadSessionId
from kronika.domain.youtube_acquisition import (
    FrameNestYouTubeClaimError,
    FrameNestYouTubeTransitionError,
    FrameNestYouTubeUrlError,
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
    YouTubeFailureStage,
    canonicalize_youtube_url,
)

VIDEO_ID = "AbCdEf123_-"


@pytest.mark.parametrize(
    "submitted",
    [
        f"https://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://youtube.com/watch?v={VIDEO_ID}&t=12",
        f"https://m.youtube.com/shorts/{VIDEO_ID}?si=synthetic",
        f"https://youtu.be/{VIDEO_ID}",
        f"https://youtu.be/{VIDEO_ID}/?t=1",
    ],
)
def test_supported_url_variants_converge_on_one_identity(submitted: str) -> None:
    identity = canonicalize_youtube_url(submitted)

    assert identity.video_id == VIDEO_ID
    assert identity.canonical_url == (
        f"https://www.youtube.com/watch?v={VIDEO_ID}"
    )
    assert identity.extractor_key == "Youtube"


@pytest.mark.parametrize(
    "submitted",
    [
        f"http://www.youtube.com/watch?v={VIDEO_ID}",
        f"https://user@www.youtube.com/watch?v={VIDEO_ID}",
        f"https://www.youtube.com:444/watch?v={VIDEO_ID}",
        f"https://evil.example/watch?v={VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&list=PL123",
        f"https://www.youtube.com/channel/{VIDEO_ID}",
        f"https://www.youtube.com/live/{VIDEO_ID}",
        f"https://www.youtube.com/watch?v={VIDEO_ID}&v=Other123456",
        "https://youtu.be/not-an-id",
        f"https://www.youtube.com/watch?v={VIDEO_ID}#fragment",
    ],
)
def test_url_policy_rejects_non_single_public_video_targets(
    submitted: str,
) -> None:
    with pytest.raises(
        FrameNestYouTubeUrlError,
        match="Invalid public YouTube video URL",
    ):
        canonicalize_youtube_url(submitted)


def test_claim_lifecycle_carries_durable_provenance_to_catalog() -> None:
    claim = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=10,
    )
    inspecting = claim.advance(
        YouTubeAcquisitionState.INSPECTING,
        updated_at_ms=11,
    )
    pending = inspecting.advance(
        YouTubeAcquisitionState.DOWNLOAD_PENDING,
        updated_at_ms=12,
        upstream_title="Synthetic title",
        upstream_channel="Synthetic channel",
        upstream_channel_id="channel-id",
        upstream_source_date="2026-01-02",
        downloader_name="yt-dlp",
        downloader_version="2026.07.23",
        extractor_version="2026.07.23",
        selected_video_format_id="137",
        selected_audio_format_id="140",
        remote_filename="Synthetic remote.mp4",
    )
    downloading = pending.advance(
        YouTubeAcquisitionState.DOWNLOADING,
        updated_at_ms=13,
    )
    downloaded = downloading.advance(
        YouTubeAcquisitionState.DOWNLOADED,
        updated_at_ms=14,
        downloaded_size_bytes=123,
        downloaded_at_ms=14,
    )
    handoff = downloaded.advance(
        YouTubeAcquisitionState.HANDOFF,
        updated_at_ms=15,
    )
    handed_off = handoff.advance(
        YouTubeAcquisitionState.HANDED_OFF,
        updated_at_ms=16,
        upload_id=UploadSessionId.new(),
    )
    cataloged = handed_off.advance(
        YouTubeAcquisitionState.CATALOGED,
        updated_at_ms=17,
        completed_at_ms=17,
        media_id=MediaId.new(),
        media_location_id=MediaLocationId.new(),
    )

    assert cataloged.state is YouTubeAcquisitionState.CATALOGED
    assert cataloged.version == 7
    assert cataloged.youtube_video_id == VIDEO_ID
    assert cataloged.generated_filename == f"youtube-{VIDEO_ID}.mp4"
    assert cataloged.upstream_title == "Synthetic title"
    assert cataloged.failure_code is None


def test_cataloged_source_identity_can_be_reused_without_download() -> None:
    original = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=20,
    )
    reuse = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=30,
    ).advance(
        YouTubeAcquisitionState.DUPLICATE_RESOLVED,
        updated_at_ms=31,
        completed_at_ms=31,
        resolved_claim_id=original.id,
        media_id=MediaId.new(),
        media_location_id=MediaLocationId.new(),
    )

    assert reuse.downloaded_size_bytes is None
    assert reuse.upload_id is None
    assert reuse.resolved_claim_id == original.id


def test_failed_claim_requires_paired_sanitized_failure_evidence() -> None:
    claim = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=40,
    )

    failed = claim.advance(
        YouTubeAcquisitionState.FAILED,
        updated_at_ms=41,
        completed_at_ms=41,
        failure_stage=YouTubeFailureStage.INSPECTION,
        failure_code="UNSUPPORTED_MEDIA",
    )
    assert failed.failure_code == "UNSUPPORTED_MEDIA"

    with pytest.raises(FrameNestYouTubeClaimError):
        claim.advance(
            YouTubeAcquisitionState.FAILED,
            updated_at_ms=41,
            completed_at_ms=41,
            failure_stage=YouTubeFailureStage.INSPECTION,
            failure_code="raw error /private/path",
        )


def test_cataloged_claim_can_become_catalog_removed() -> None:
    media_id = MediaId.new()
    location_id = MediaLocationId.new()
    original = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://www.youtube.com/watch?v={VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=20,
    )
    cataloged = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=30,
    ).advance(
        YouTubeAcquisitionState.DUPLICATE_RESOLVED,
        updated_at_ms=31,
        completed_at_ms=31,
        resolved_claim_id=original.id,
        media_id=media_id,
        media_location_id=location_id,
    )

    removed = cataloged.mark_catalog_removed(now_ms=32)

    assert removed.state is YouTubeAcquisitionState.CATALOG_REMOVED
    assert removed.media_id is None
    assert removed.media_location_id is None
    assert removed.catalog_removed_at_ms == 32
    assert removed.completed_at_ms == 31
    with pytest.raises(FrameNestYouTubeTransitionError):
        removed.mark_catalog_removed(now_ms=33)
    with pytest.raises(FrameNestYouTubeClaimError):
        cataloged.mark_catalog_removed(now_ms=30)


def test_illegal_transition_is_rejected() -> None:
    claim = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=50,
    )

    with pytest.raises(FrameNestYouTubeTransitionError):
        claim.advance(
            YouTubeAcquisitionState.DOWNLOADING,
            updated_at_ms=51,
        )


def test_requester_ownership_normalizes_and_is_immutable() -> None:
    claim = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=10,
        created_by_login_key="User@Example.COM",
    )
    assert claim.created_by_login_key == "user@example.com"
    with pytest.raises(FrameNestYouTubeClaimError):
        claim.evolve(updated_at_ms=11, created_by_login_key="other@example.com")
    advanced = claim.advance(YouTubeAcquisitionState.INSPECTING, updated_at_ms=11)
    assert advanced.created_by_login_key == "user@example.com"



def test_legacy_null_ownership_and_retry_preserves_owner() -> None:
    legacy = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=10,
    )
    assert legacy.created_by_login_key is None
    owned = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=20,
        created_by_login_key="owner@example.com",
    )
    retry = YouTubeAcquisitionClaim.new(
        submitted_url=owned.submitted_url,
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=30,
        retry_of_claim_id=owned.id,
        created_by_login_key=owned.created_by_login_key,
    )
    assert retry.created_by_login_key == "owner@example.com"
    assert retry.retry_of_claim_id == owned.id


def test_derive_requester_phase_mapping() -> None:
    from kronika.domain.youtube_acquisition import derive_requester_phase

    claim = YouTubeAcquisitionClaim.new(
        submitted_url=f"https://youtu.be/{VIDEO_ID}",
        confirmation_method=YouTubeConfirmationMethod.INTERACTIVE,
        now_ms=10,
        created_by_login_key="owner@example.com",
    )
    assert derive_requester_phase(claim, media_is_published=None) == "queued"
    inspecting = claim.advance(YouTubeAcquisitionState.INSPECTING, updated_at_ms=11)
    assert derive_requester_phase(inspecting, media_is_published=None) == "processing"
    pending = inspecting.advance(
        YouTubeAcquisitionState.DOWNLOAD_PENDING,
        updated_at_ms=12,
        upstream_title="Title",
        upstream_channel="Channel",
        upstream_channel_id="cid",
        upstream_source_date="2026-01-02",
        downloader_name="yt-dlp",
        downloader_version="2026.07.23",
        extractor_version="2026.07.23",
        selected_video_format_id="137",
        selected_audio_format_id="140",
    )
    assert derive_requester_phase(pending, media_is_published=None) == "downloading"
    cataloged = (
        pending.advance(YouTubeAcquisitionState.DOWNLOADING, updated_at_ms=13)
        .advance(
            YouTubeAcquisitionState.DOWNLOADED,
            updated_at_ms=14,
            downloaded_size_bytes=100,
            downloaded_at_ms=14,
        )
        .advance(YouTubeAcquisitionState.HANDOFF, updated_at_ms=15)
        .advance(
            YouTubeAcquisitionState.HANDED_OFF,
            updated_at_ms=16,
            upload_id=UploadSessionId.new(),
        )
        .advance(
            YouTubeAcquisitionState.CATALOGED,
            updated_at_ms=17,
            completed_at_ms=17,
            media_id=MediaId.new(),
            media_location_id=MediaLocationId.new(),
        )
    )
    assert derive_requester_phase(cataloged, media_is_published=False) == "completed_private"
    assert derive_requester_phase(cataloged, media_is_published=True) == "completed"
    removed = cataloged.mark_catalog_removed(now_ms=18)
    assert derive_requester_phase(removed, media_is_published=None) == "unavailable"
