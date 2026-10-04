"""Contract tests for stable pure-domain identity values."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from framenest.domain import (
    DeviceId,
    FrameNestIdentityError,
    LibraryId,
    MediaByteIdentityId,
    MediaId,
    MediaLocationId,
    SeriesId,
    StorageVolumeId,
)

IDENTITY_TYPES = (
    MediaId,
    MediaByteIdentityId,
    MediaLocationId,
    DeviceId,
    LibraryId,
    StorageVolumeId,
    SeriesId,
)

CANONICAL_UUID4_TEXT = "12345678-1234-4234-9234-123456789abc"
SECOND_CANONICAL_UUID4_TEXT = "abcdefab-cdef-4abc-8def-abcdefabcdef"
UNSUPPORTED_VARIANT_UUID_TEXT = "12345678-1234-4234-1234-123456789abc"
UUIDV1_TEXT = "a8098c1a-f86e-11da-bd1a-00112444be1e"
UUIDV1 = uuid.UUID(UUIDV1_TEXT)
UUIDV3 = uuid.uuid3(uuid.NAMESPACE_DNS, "framenest.example")
UUIDV5 = uuid.uuid5(uuid.NAMESPACE_DNS, "framenest.example")
NON_RFC_VARIANT_UUID = uuid.UUID(UNSUPPORTED_VARIANT_UUID_TEXT)
EXPECTED_ERROR_MESSAGE = "Invalid Kronika identity."


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
def test_new_identity_is_uuid4_with_canonical_roundtrip(identity_type: type[MediaId]) -> None:
    identity = identity_type.new()
    other_identity = identity_type.new()

    assert isinstance(identity, identity_type)
    assert identity.value.version == 4
    assert identity.value.variant == uuid.RFC_4122
    assert other_identity != identity
    assert identity.to_string() == str(identity.value)
    assert identity.to_string() == identity.to_string().lower()
    assert len(identity.to_string()) == 36
    assert identity.to_string()[8] == "-"
    assert identity.to_string()[13] == "-"
    assert identity.to_string()[18] == "-"
    assert identity.to_string()[23] == "-"
    assert str(identity) == identity.to_string()
    assert identity_type.from_string(identity.to_string()) == identity


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
def test_same_category_identity_equality_hashing_and_collections(
    identity_type: type[MediaId],
) -> None:
    first = identity_type.from_string(CANONICAL_UUID4_TEXT)
    same = identity_type.from_string(CANONICAL_UUID4_TEXT)
    second = identity_type.from_string(SECOND_CANONICAL_UUID4_TEXT)

    assert same == first
    assert second != first
    assert hash(same) == hash(first)
    assert {first, same, second} == {first, second}
    assert {first: "found"}[same] == "found"


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
def test_direct_constructor_accepts_valid_uuid4(identity_type: type[MediaId]) -> None:
    value = uuid.uuid4()

    identity = identity_type(value)

    assert isinstance(identity, identity_type)
    assert identity.value is value
    assert identity.to_string() == str(value)
    assert identity_type(value) == identity
    assert hash(identity_type(value)) == hash(identity)


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
@pytest.mark.parametrize("rejected", [UUIDV1, UUIDV3, UUIDV5, NON_RFC_VARIANT_UUID])
def test_direct_constructor_rejects_invalid_uuid_values(
    identity_type: type[MediaId],
    rejected: uuid.UUID,
) -> None:
    with pytest.raises(FrameNestIdentityError) as exc_info:
        identity_type(rejected)

    message = str(exc_info.value)
    assert message == EXPECTED_ERROR_MESSAGE
    assert str(rejected) not in message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
@pytest.mark.parametrize(
    "rejected",
    [
        CANONICAL_UUID4_TEXT,
        "not-a-uuid",
        None,
        123,
        b"12345678-1234-4234-9234-123456789abc",
        MediaId.from_string(CANONICAL_UUID4_TEXT),
    ],
)
def test_direct_constructor_rejects_invalid_runtime_types(
    identity_type: type[MediaId],
    rejected: Any,
) -> None:
    with pytest.raises(FrameNestIdentityError) as exc_info:
        identity_type(rejected)  # type: ignore[arg-type]

    message = str(exc_info.value)
    assert message == EXPECTED_ERROR_MESSAGE
    if isinstance(rejected, str):
        assert rejected not in message
    assert "UUID" not in message
    assert "TypeError" not in message
    assert exc_info.value.__cause__ is None


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
def test_identity_objects_are_immutable(identity_type: type[MediaId]) -> None:
    identity = identity_type.from_string(CANONICAL_UUID4_TEXT)

    with pytest.raises((AttributeError, TypeError)):
        identity.value = uuid.uuid4()  # type: ignore[misc]
    with pytest.raises((AttributeError, TypeError)):
        identity.extra = "not allowed"  # type: ignore[attr-defined]


@pytest.mark.parametrize("identity_type", IDENTITY_TYPES)
def test_repr_is_safe_concise_and_debuggable(identity_type: type[MediaId]) -> None:
    identity = identity_type.from_string(CANONICAL_UUID4_TEXT)
    representation = repr(identity)

    assert representation == f"{identity_type.__name__}('{CANONICAL_UUID4_TEXT}')"
    assert "/Users/" not in representation
    assert "sqlite" not in representation.lower()
    assert "catalog" not in representation.lower()


def test_cross_category_identities_remain_distinct_with_same_uuid_text() -> None:
    media_id = MediaId.from_string(CANONICAL_UUID4_TEXT)
    other_categories = {
        MediaLocationId.from_string(CANONICAL_UUID4_TEXT),
        DeviceId.from_string(CANONICAL_UUID4_TEXT),
        LibraryId.from_string(CANONICAL_UUID4_TEXT),
        StorageVolumeId.from_string(CANONICAL_UUID4_TEXT),
        SeriesId.from_string(CANONICAL_UUID4_TEXT),
    }

    assert all(media_id != other for other in other_categories)
    assert len({media_id, *other_categories}) == 6


def test_logical_media_id_generation_and_roundtrip() -> None:
    media_id = MediaId.new()

    restored = MediaId.from_string(media_id.to_string())

    assert restored == media_id
    assert restored.value.version == 4


def test_physical_location_id_generation_and_roundtrip() -> None:
    location_id = MediaLocationId.new()

    restored = MediaLocationId.from_string(location_id.to_string())

    assert restored == location_id
    assert restored.value.version == 4


@pytest.mark.parametrize(
    "rejected",
    [
        "12345678-1234-4234-9234-123456789ABC",
        "12345678123442349234123456789abc",
        "{12345678-1234-4234-9234-123456789abc}",
        "urn:uuid:12345678-1234-4234-9234-123456789abc",
        " 12345678-1234-4234-9234-123456789abc",
        "12345678-1234-4234-9234-123456789abc ",
        "",
        "not-a-uuid",
        "12345678-1234-4234-9234-123456789ab",
        UUIDV1_TEXT,
        str(uuid.uuid3(uuid.NAMESPACE_DNS, "framenest.example")),
        str(uuid.uuid5(uuid.NAMESPACE_DNS, "framenest.example")),
    ],
)
def test_from_string_rejects_non_canonical_or_non_v4_input(rejected: str) -> None:
    with pytest.raises(FrameNestIdentityError) as exc_info:
        MediaId.from_string(rejected)

    message = str(exc_info.value)
    assert message == EXPECTED_ERROR_MESSAGE
    if rejected:
        assert rejected not in message


@pytest.mark.parametrize("rejected", [None, 123, uuid.uuid4(), b"12345678-1234-4234-9234-123456789abc"])
def test_from_string_rejects_non_string_input(rejected: Any) -> None:
    with pytest.raises(FrameNestIdentityError) as exc_info:
        MediaId.from_string(rejected)  # type: ignore[arg-type]

    assert str(exc_info.value) == EXPECTED_ERROR_MESSAGE


def test_from_string_rejects_unsupported_uuid_variant() -> None:
    rejected = UNSUPPORTED_VARIANT_UUID_TEXT

    with pytest.raises(FrameNestIdentityError) as exc_info:
        MediaId.from_string(rejected)

    message = str(exc_info.value)
    assert message == EXPECTED_ERROR_MESSAGE
    assert rejected not in message


def test_validation_error_is_sanitized_and_framenest_owned() -> None:
    rejected = "/Users/private/catalog.sqlite3/not-a-uuid"

    with pytest.raises(FrameNestIdentityError) as exc_info:
        MediaId.from_string(rejected)

    message = str(exc_info.value)
    assert message == EXPECTED_ERROR_MESSAGE
    assert rejected not in message
    assert "/Users/" not in message
    assert "badly formed hexadecimal UUID string" not in message
    assert "ValueError" not in message
    assert exc_info.value.__cause__ is None
