"""Unit evidence for immutable acquisition provenance on metadata Save."""

from __future__ import annotations

from kronika.application.media_metadata import SaveMediaMetadata
from kronika.application.ports.media_metadata_repository import (
    AcquisitionSourceImmutableError,
    MediaMetadataSaveResult,
    MediaMetadataSnapshot,
)
from kronika.domain.identities import MediaId
from kronika.domain.media_classification import (
    AcquisitionSource,
    ContentCategory,
    CreatorAttributionKind,
)
from kronika.domain.media_metadata import CanonicalTagKey, MediaDisplayTitle
import pytest


class _FakeRepository:
    def __init__(self, snapshot: MediaMetadataSnapshot) -> None:
        self.snapshot = snapshot
        self.saves: list[dict[str, object]] = []

    def get_canonical_tag(self, key: CanonicalTagKey):
        raise AssertionError("unexpected tag lookup")

    def get_media_metadata(self, media_id: MediaId) -> MediaMetadataSnapshot:
        assert media_id == self.snapshot.media_id
        return self.snapshot

    def save_media_metadata(self, media_id, display_title, description, tag_keys, now_ms, **kwargs):
        self.saves.append(
            {
                "media_id": media_id,
                "display_title": display_title,
                "description": description,
                "tag_keys": tag_keys,
                "now_ms": now_ms,
                **kwargs,
            }
        )
        acquisition_source = kwargs.get("acquisition_source")
        if self.snapshot.persisted:
            if acquisition_source is None:
                resolved = self.snapshot.acquisition_source
            elif acquisition_source == self.snapshot.acquisition_source:
                resolved = self.snapshot.acquisition_source
            else:
                raise AcquisitionSourceImmutableError(
                    "Acquisition source is immutable provenance and cannot be changed."
                )
        else:
            resolved = (
                AcquisitionSource.UNKNOWN
                if acquisition_source is None
                else acquisition_source
            )
        updated = MediaMetadataSnapshot(
            media_id=media_id,
            persisted=True,
            display_title=display_title,
            description=description,
            tag_keys=tag_keys,
            collection_key=self.snapshot.collection_key,
            processed_at_ms=self.snapshot.processed_at_ms,
            created_at_ms=self.snapshot.created_at_ms or now_ms,
            updated_at_ms=now_ms,
            content_category=kwargs["content_category"],
            acquisition_source=resolved,
            genre_keys=kwargs.get("genre_keys", ()),
            creator_attribution_kind=kwargs.get("creator_attribution_kind"),
            creator_stable_id=kwargs.get("creator_stable_id"),
            creator_handle=kwargs.get("creator_handle"),
            creator_display_name=kwargs.get("creator_display_name"),
        )
        self.snapshot = updated
        return MediaMetadataSaveResult(status="updated", metadata=updated)


def _snapshot(**overrides: object) -> MediaMetadataSnapshot:
    media_id = MediaId.new()
    values = {
        "media_id": media_id,
        "persisted": True,
        "display_title": MediaDisplayTitle("Original"),
        "description": None,
        "tag_keys": (),
        "collection_key": None,
        "processed_at_ms": None,
        "created_at_ms": 10,
        "updated_at_ms": 10,
        "content_category": ContentCategory.YOUTUBE,
        "acquisition_source": AcquisitionSource.YOUTUBE_MANUAL_CLAIM,
        "genre_keys": (),
        "creator_attribution_kind": CreatorAttributionKind.YOUTUBE_CHANNEL,
        "creator_stable_id": "UC123",
        "creator_handle": None,
        "creator_display_name": "Channel",
    }
    values.update(overrides)
    return MediaMetadataSnapshot(**values)  # type: ignore[arg-type]


def test_omitting_acquisition_source_preserves_stored_value() -> None:
    repository = _FakeRepository(_snapshot())
    result = SaveMediaMetadata(repository, clock_ms=lambda: 20).execute(
        repository.snapshot.media_id.to_string(),
        "Updated",
        None,
        [],
        content_category="youtube",
        acquisition_source=None,
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC123",
        creator_display_name="Channel",
    )
    assert result.metadata.acquisition_source == "youtube_manual_claim"
    assert repository.saves[0]["acquisition_source"] is None


def test_identical_acquisition_source_is_accepted() -> None:
    repository = _FakeRepository(_snapshot())
    result = SaveMediaMetadata(repository, clock_ms=lambda: 20).execute(
        repository.snapshot.media_id.to_string(),
        "Updated",
        None,
        [],
        content_category="meme",
        acquisition_source="youtube_manual_claim",
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC123",
        creator_display_name="Channel",
    )
    assert result.metadata.content_category == "meme"
    assert result.metadata.acquisition_source == "youtube_manual_claim"


def test_changing_acquisition_source_is_rejected() -> None:
    repository = _FakeRepository(_snapshot())
    with pytest.raises(AcquisitionSourceImmutableError):
        SaveMediaMetadata(repository, clock_ms=lambda: 20).execute(
            repository.snapshot.media_id.to_string(),
            "Updated",
            None,
            [],
            content_category="youtube",
            acquisition_source="unknown",
        )


def test_ai_draft_cannot_replace_provenance_with_unknown() -> None:
    repository = _FakeRepository(_snapshot())
    with pytest.raises(AcquisitionSourceImmutableError):
        SaveMediaMetadata(repository, clock_ms=lambda: 20).execute(
            repository.snapshot.media_id.to_string(),
            "AI Title",
            None,
            [],
            content_category="youtube",
            acquisition_source="unknown",
            creator_attribution_kind=None,
            creator_stable_id=None,
            creator_handle=None,
            creator_display_name=None,
        )
    assert repository.snapshot.acquisition_source is AcquisitionSource.YOUTUBE_MANUAL_CLAIM
    assert (
        repository.snapshot.creator_attribution_kind
        is CreatorAttributionKind.YOUTUBE_CHANNEL
    )


def test_explicit_admin_save_can_update_creator_attribution() -> None:
    repository = _FakeRepository(_snapshot())
    result = SaveMediaMetadata(repository, clock_ms=lambda: 20).execute(
        repository.snapshot.media_id.to_string(),
        "Updated",
        None,
        [],
        content_category="youtube",
        acquisition_source=None,
        creator_attribution_kind="youtube_channel",
        creator_stable_id="UC999",
        creator_display_name="Renamed",
    )
    assert result.metadata.creator_stable_id == "UC999"
    assert result.metadata.creator_display_name == "Renamed"
    assert result.metadata.acquisition_source == "youtube_manual_claim"
