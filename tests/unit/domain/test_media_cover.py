"""Pure-domain invariants for the durable accepted-cover model."""

from __future__ import annotations

import pytest

from tests.support.kronika_identity import expected

from framenest.domain.identities import MediaId, MediaLocationId
from framenest.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    SOURCE_OBSERVATION_ALGORITHM,
    CoverSourceKind,
    CoverSourceObservation,
    FrameNestMediaCoverError,
    MediaCover,
    source_reference_for_location,
)

MEDIA_ID = MediaId.from_string("11111111-1111-4111-8111-111111111111")
LOCATION_ID = MediaLocationId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
DIGEST = "a" * 64
OBS = "b" * 64


def _base_observation() -> CoverSourceObservation:
    return CoverSourceObservation(
        source_location_id=LOCATION_ID,
        source_kind=CoverSourceKind.MP4,
        source_size_bytes=1234,
        source_mtime_ns=99,
        source_duration_ms=1000,
    )


def _base_cover(**overrides: object) -> MediaCover:
    values = dict(
        media_id=MEDIA_ID,
        source_location_id=LOCATION_ID,
        source_reference=source_reference_for_location(LOCATION_ID),
        source_kind=CoverSourceKind.MP4,
        source_timestamp_ms=250,
        source_size_bytes=1234,
        source_mtime_ns=99,
        source_duration_ms=1000,
        source_observation_version=SOURCE_OBSERVATION_ALGORITHM,
        source_observation_digest=OBS,
        artifact_profile=COVER_ARTIFACT_PROFILE,
        artifact_media_type=COVER_ARTIFACT_MEDIA_TYPE,
        artifact_digest=DIGEST,
        artifact_width=512,
        artifact_height=288,
        artifact_byte_size=15000,
        revision=1,
        accepted_at_ms=100,
    )
    values.update(overrides)
    return MediaCover(**values)


def test_valid_observation_and_cover_construct() -> None:
    observation = _base_observation()
    assert observation.source_kind == CoverSourceKind.MP4
    cover = _base_cover()
    assert cover.revision == 1
    assert cover.source_reference_matches_location is True


def test_source_location_can_be_absent_with_provenance_reference() -> None:
    cover = _base_cover(source_location_id=None)
    assert cover.source_location_id is None
    assert cover.source_reference_matches_location is False
    assert cover.source_reference == f"location:{LOCATION_ID.to_string()}"


def test_invalid_artifact_digest_is_rejected() -> None:
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_digest="not-a-hex")


def test_rejected_cover_reports_the_derived_brand() -> None:
    with pytest.raises(FrameNestMediaCoverError) as excinfo:
        _base_cover(artifact_digest="not-a-hex")

    assert str(excinfo.value) == expected("Invalid {brand} accepted cover.")


def test_rejected_cover_source_observation_reports_the_derived_brand() -> None:
    with pytest.raises(FrameNestMediaCoverError) as excinfo:
        CoverSourceObservation(
            source_location_id="not-a-location-id",
            source_kind=CoverSourceKind.MP4,
            source_size_bytes=1234,
            source_mtime_ns=99,
            source_duration_ms=1000,
        )

    assert str(excinfo.value) == expected(
        "Invalid {brand} cover source observation."
    )


def test_negative_or_zero_values_are_rejected() -> None:
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(source_timestamp_ms=-1)
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(source_size_bytes=0)
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_width=0)
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_byte_size=-5)
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(revision=0)
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(accepted_at_ms=-1)


def test_wrong_profiles_and_kinds_are_rejected() -> None:
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_profile="stale-profile")
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_media_type="image/png")
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(source_observation_version="stale-observation-v1")
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(artifact_width=-1)


def test_wrong_source_reference_shape_is_rejected() -> None:
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(source_reference="absolute/path/private.mp4")
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(source_reference="location:not-a-uuid")


def test_observation_rejects_negative_and_zero_observations() -> None:
    with pytest.raises(FrameNestMediaCoverError):
        CoverSourceObservation(
            source_location_id=LOCATION_ID,
            source_kind=CoverSourceKind.GIF,
            source_size_bytes=0,
            source_mtime_ns=None,
            source_duration_ms=None,
        )


def test_timeless_image_source_kind_is_explicit() -> None:
    image = CoverSourceObservation(
        source_location_id=LOCATION_ID,
        source_kind=CoverSourceKind.IMAGE,
        source_size_bytes=4321,
        source_mtime_ns=123456,
        source_duration_ms=None,
    )
    assert image.source_kind is CoverSourceKind.IMAGE
    assert image.source_duration_ms is None


def test_timeless_image_cover_uses_canonical_zero_timestamp() -> None:
    cover = _base_cover(
        source_kind=CoverSourceKind.IMAGE,
        source_timestamp_ms=0,
        source_duration_ms=None,
    )
    assert cover.source_kind is CoverSourceKind.IMAGE
    assert cover.source_timestamp_ms == 0
    assert cover.source_duration_ms is None


def test_timeless_image_cover_rejects_nonzero_timestamp() -> None:
    from framenest.domain.media_cover import TIMELESS_IMAGE_TIMESTAMP_MS

    assert TIMELESS_IMAGE_TIMESTAMP_MS == 0
    with pytest.raises(FrameNestMediaCoverError):
        _base_cover(
            source_kind=CoverSourceKind.IMAGE,
            source_timestamp_ms=-1,
            source_duration_ms=None,
        )


def test_source_reference_for_location_is_opaque_and_sanitized() -> None:
    reference = source_reference_for_location(LOCATION_ID)
    assert reference == f"location:{LOCATION_ID.to_string()}"
    assert "/" not in reference
    assert LOCATION_ID.to_string() in reference
