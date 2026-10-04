"""Unit tests for canonical exact-byte media identities."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from kronika.domain import (
    FrameNestMediaByteIdentityError,
    MediaByteIdentity,
    MediaByteIdentityId,
    validate_media_byte_identity_evidence,
)


def test_valid_media_byte_identity_construction_succeeds() -> None:
    identity = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="a" * 64,
        created_at_ms=10,
    )

    assert identity.checksum_algorithm == "sha256"
    assert identity.size_bytes == 100
    assert identity.checksum_hex == "a" * 64
    assert validate_media_byte_identity_evidence(
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="a" * 64,
    ) == ("sha256", 100, "a" * 64)


@pytest.mark.parametrize(
    "overrides",
    [
        {"checksum_algorithm": "md5"},
        {"size_bytes": 0},
        {"size_bytes": -1},
        {"size_bytes": True},
        {"checksum_hex": "A" * 64},
        {"checksum_hex": "a" * 63},
        {"checksum_hex": "g" * 64},
        {"created_at_ms": -1},
    ],
)
def test_invalid_media_byte_identity_evidence_fails(overrides: dict[str, object]) -> None:
    values: dict[str, object] = {
        "id": MediaByteIdentityId.new(),
        "checksum_algorithm": "sha256",
        "size_bytes": 100,
        "checksum_hex": "a" * 64,
        "created_at_ms": 10,
    }
    values.update(overrides)

    with pytest.raises(FrameNestMediaByteIdentityError):
        MediaByteIdentity(**values)  # type: ignore[arg-type]


def test_media_byte_identity_is_immutable() -> None:
    identity = MediaByteIdentity(
        id=MediaByteIdentityId.new(),
        checksum_algorithm="sha256",
        size_bytes=100,
        checksum_hex="a" * 64,
        created_at_ms=10,
    )

    with pytest.raises((AttributeError, FrozenInstanceError)):
        identity.checksum_hex = "b" * 64  # type: ignore[misc]
