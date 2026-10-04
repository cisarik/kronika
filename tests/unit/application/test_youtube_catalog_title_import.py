"""Unit evidence for YouTube upstream title import at catalog handoff."""

from __future__ import annotations

from kronika.application.upload_catalog import CatalogUploadClassification
from kronika.application.youtube_acquisition import (
    _imported_display_title_from_upstream,
    youtube_classification_for_upload,
)
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.media_metadata import MAX_DISPLAY_TITLE_CODE_POINTS
from kronika.domain.uploads import UploadSessionId
from kronika.domain.youtube_acquisition import (
    YouTubeAcquisitionClaim,
    YouTubeAcquisitionState,
    YouTubeConfirmationMethod,
)


class _ClaimRepository:
    def __init__(self, claim: YouTubeAcquisitionClaim | None) -> None:
        self._claim = claim

    def find_by_upload_id(self, upload_id: UploadSessionId):
        if self._claim is None or self._claim.upload_id != upload_id:
            return None
        return self._claim


def _claim_with_upstream(
    *,
    upstream_title: str | None,
    upload_id: UploadSessionId,
) -> YouTubeAcquisitionClaim:
    claim = YouTubeAcquisitionClaim.new(
        submitted_url="https://youtu.be/AbCdEf123_-",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=10,
        created_by_login_key="owner@example.com",
    )
    inspecting = claim.advance(
        YouTubeAcquisitionState.INSPECTING,
        updated_at_ms=11,
    )
    pending = inspecting.advance(
        YouTubeAcquisitionState.DOWNLOAD_PENDING,
        updated_at_ms=12,
        upstream_title=upstream_title,
        upstream_channel="Channel",
        upstream_channel_id="channel",
        upstream_source_date="2026-01-02",
        downloader_name="yt-dlp",
        downloader_version="2026.07.23",
        extractor_version="2026.07.23",
        selected_video_format_id="18",
        remote_filename="remote.mp4",
    )
    return pending.evolve(updated_at_ms=13, upload_id=upload_id)


def test_classification_imports_upstream_title_as_initial_display_title() -> None:
    upload_id = UploadSessionId.new()
    claim = _claim_with_upstream(
        upstream_title="Imported Upstream Title",
        upload_id=upload_id,
    )
    result = youtube_classification_for_upload(_ClaimRepository(claim), upload_id)

    assert result == CatalogUploadClassification(
        content_category=ContentCategory.YOUTUBE,
        acquisition_source=AcquisitionSource.YOUTUBE_MANUAL_CLAIM,
        display_title=_imported_display_title_from_upstream("Imported Upstream Title"),
        creator_attribution_kind=CreatorAttributionKind.YOUTUBE_CHANNEL,
        creator_stable_id="channel",
        creator_handle=None,
        creator_display_name="Channel",
    )
    assert result is not None
    assert result.display_title is not None
    assert result.display_title.value == "Imported Upstream Title"


def test_classification_skips_when_upload_is_not_youtube_linked() -> None:
    result = youtube_classification_for_upload(
        _ClaimRepository(None),
        UploadSessionId.new(),
    )
    assert result is None


def test_imported_title_truncates_to_display_title_bound() -> None:
    long_title = "T" * 500
    imported = _imported_display_title_from_upstream(long_title)
    assert imported is not None
    assert len(imported.value) == MAX_DISPLAY_TITLE_CODE_POINTS
    assert imported.value == "T" * MAX_DISPLAY_TITLE_CODE_POINTS


def test_missing_upstream_title_does_not_invent_display_title() -> None:
    assert _imported_display_title_from_upstream(None) is None
    upload_id = UploadSessionId.new()
    claim = _claim_with_upstream(upstream_title=None, upload_id=upload_id)
    result = youtube_classification_for_upload(_ClaimRepository(claim), upload_id)
    assert result is not None
    assert result.display_title is None
    assert result.content_category is ContentCategory.YOUTUBE
    assert result.acquisition_source is AcquisitionSource.YOUTUBE_MANUAL_CLAIM
    assert result.creator_attribution_kind is CreatorAttributionKind.YOUTUBE_CHANNEL
    assert result.creator_stable_id == "channel"
    assert result.creator_display_name == "Channel"
    assert result.creator_handle is None


def test_missing_channel_leaves_creator_attribution_null() -> None:
    upload_id = UploadSessionId.new()
    claim = YouTubeAcquisitionClaim.new(
        submitted_url="https://youtu.be/AbCdEf123_-",
        confirmation_method=YouTubeConfirmationMethod.YES_FLAG,
        now_ms=10,
        created_by_login_key="owner@example.com",
    )
    inspecting = claim.advance(YouTubeAcquisitionState.INSPECTING, updated_at_ms=11)
    pending = inspecting.advance(
        YouTubeAcquisitionState.DOWNLOAD_PENDING,
        updated_at_ms=12,
        upstream_title="Title Only",
        upstream_channel=None,
        upstream_channel_id=None,
        upstream_source_date="2026-01-02",
        downloader_name="yt-dlp",
        downloader_version="2026.07.23",
        extractor_version="2026.07.23",
        selected_video_format_id="18",
        remote_filename="remote.mp4",
    )
    linked = pending.evolve(updated_at_ms=13, upload_id=upload_id)
    result = youtube_classification_for_upload(_ClaimRepository(linked), upload_id)
    assert result is not None
    assert result.content_category is ContentCategory.YOUTUBE
    assert result.creator_attribution_kind is None
    assert result.creator_stable_id is None
    assert result.creator_display_name is None
    assert result.creator_handle is None
    assert result.display_title is not None
    assert result.display_title.value == "Title Only"
