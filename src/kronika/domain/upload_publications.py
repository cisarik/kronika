"""Pure-domain invariants for durable upload publication ownership."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import re
import uuid

from kronika.domain.identities import (
    LibraryId,
    MediaByteIdentityId,
    MediaId,
    MediaLocationId,
)
from kronika.domain.uploads import (
    ALLOWED_UPLOAD_VALIDATION_EVIDENCE_PAIRS,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    validate_sha256_checksum_hex,
)

INVALID_UPLOAD_PUBLICATION_MESSAGE = "Invalid upload publication."
INVALID_UPLOAD_PUBLICATION_TRANSITION_MESSAGE = "Invalid upload publication transition."

_RELATIVE_TARGET_PATTERN = re.compile(r"[0-9a-f]{32}\.(gif|mp4|jpg|png)")


class FrameNestUploadPublicationError(ValueError):
    """Sanitized error raised for invalid publication provenance."""


class FrameNestUploadPublicationTransitionError(FrameNestUploadPublicationError):
    """Sanitized error raised for invalid publication progress."""


class UploadPublicationState(StrEnum):
    """Durable ownership progress for one reserved original."""

    RESERVED = "reserved"
    VERIFIED = "verified"


class UploadPublicationCleanupState(StrEnum):
    """Durable cleanup progress for the exact quarantine object."""

    PENDING = "pending"
    COMPLETE = "complete"


@dataclass(frozen=True, slots=True, repr=False)
class UploadPublicationId:
    """Server-generated opaque UUIDv4 publication identity."""

    value: uuid.UUID

    def __post_init__(self) -> None:
        if not isinstance(self.value, uuid.UUID):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if self.value.variant != uuid.RFC_4122 or self.value.version != 4:
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)

    @classmethod
    def new(cls) -> "UploadPublicationId":
        return cls(uuid.uuid4())

    @classmethod
    def from_string(cls, value: str) -> "UploadPublicationId":
        if not isinstance(value, str):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, TypeError, ValueError):
            raise FrameNestUploadPublicationError(
                INVALID_UPLOAD_PUBLICATION_MESSAGE
            ) from None
        if str(parsed) != value:
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        return cls(parsed)

    def to_string(self) -> str:
        return str(self.value)

    def __str__(self) -> str:
        return self.to_string()

    def __repr__(self) -> str:
        return f"UploadPublicationId('{self.to_string()}')"


@dataclass(frozen=True, slots=True)
class UploadPublicationRelativePath:
    """Normalized server-owned target basename, never client supplied."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _RELATIVE_TARGET_PATTERN.fullmatch(
            self.value
        ):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)

    @classmethod
    def for_publication(
        cls,
        publication_id: UploadPublicationId,
        media_format: UploadValidatedFormat,
    ) -> "UploadPublicationRelativePath":
        if not isinstance(publication_id, UploadPublicationId) or not isinstance(
            media_format,
            UploadValidatedFormat,
        ):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        return cls(f"{publication_id.value.hex}.{media_format.value}")


@dataclass(frozen=True, slots=True)
class UploadPublication:
    """Durable publication reservation and verified ownership provenance."""

    upload_id: UploadSessionId
    publication_id: UploadPublicationId
    destination_id: LibraryId
    relative_path: UploadPublicationRelativePath
    byte_identity_id: MediaByteIdentityId
    expected_size_bytes: int
    checksum_algorithm: str
    checksum_hex: str
    validated_media_kind: UploadValidatedMediaKind
    validated_format: UploadValidatedFormat
    state: UploadPublicationState
    cleanup_state: UploadPublicationCleanupState
    created_at_ms: int
    updated_at_ms: int
    verified_at_ms: int | None
    cleanup_completed_at_ms: int | None
    version: int
    media_id: MediaId | None = None
    media_location_id: MediaLocationId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.upload_id, UploadSessionId):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.publication_id, UploadPublicationId):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.destination_id, LibraryId):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.relative_path, UploadPublicationRelativePath):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        expected_path = UploadPublicationRelativePath.for_publication(
            self.publication_id,
            self.validated_format,
        )
        if self.relative_path != expected_path:
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.byte_identity_id, MediaByteIdentityId):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        _validate_positive(self.expected_size_bytes)
        if self.checksum_algorithm != "sha256":
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        try:
            validate_sha256_checksum_hex(self.checksum_hex)
        except ValueError as exc:
            raise FrameNestUploadPublicationError(
                INVALID_UPLOAD_PUBLICATION_MESSAGE
            ) from exc
        if not isinstance(self.validated_media_kind, UploadValidatedMediaKind):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.validated_format, UploadValidatedFormat):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if (
            self.validated_media_kind,
            self.validated_format,
        ) not in ALLOWED_UPLOAD_VALIDATION_EVIDENCE_PAIRS:
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if not isinstance(self.state, UploadPublicationState) or not isinstance(
            self.cleanup_state,
            UploadPublicationCleanupState,
        ):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        _validate_non_negative(self.created_at_ms)
        _validate_non_negative(self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        _validate_optional_non_negative(self.verified_at_ms)
        _validate_optional_non_negative(self.cleanup_completed_at_ms)
        _validate_non_negative(self.version)
        if self.state is UploadPublicationState.RESERVED:
            if (
                self.cleanup_state is not UploadPublicationCleanupState.PENDING
                or self.verified_at_ms is not None
                or self.cleanup_completed_at_ms is not None
            ):
                raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        else:
            if self.verified_at_ms is None or self.verified_at_ms < self.created_at_ms:
                raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
            if self.cleanup_state is UploadPublicationCleanupState.PENDING:
                if self.cleanup_completed_at_ms is not None:
                    raise FrameNestUploadPublicationError(
                        INVALID_UPLOAD_PUBLICATION_MESSAGE
                    )
            elif (
                self.cleanup_completed_at_ms is None
                or self.cleanup_completed_at_ms < self.verified_at_ms
            ):
                raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if (self.media_id is None) != (self.media_location_id is None):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if self.media_id is not None and not isinstance(self.media_id, MediaId):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)
        if self.media_location_id is not None and not isinstance(
            self.media_location_id,
            MediaLocationId,
        ):
            raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)


def new_upload_publication_reservation(
    upload: UploadSession,
    *,
    destination_id: LibraryId,
    now_ms: int,
) -> UploadPublication:
    """Create one opaque reservation from complete durable upload evidence."""
    ensure_upload_is_publication_candidate(upload)
    _validate_non_negative(now_ms)
    publication_id = UploadPublicationId.new()
    assert upload.byte_identity_id is not None
    assert upload.checksum_hex is not None
    assert upload.validated_media_kind is not None
    assert upload.validated_format is not None
    return UploadPublication(
        upload_id=upload.id,
        publication_id=publication_id,
        destination_id=destination_id,
        relative_path=UploadPublicationRelativePath.for_publication(
            publication_id,
            upload.validated_format,
        ),
        byte_identity_id=upload.byte_identity_id,
        expected_size_bytes=upload.declared_size_bytes,
        checksum_algorithm="sha256",
        checksum_hex=upload.checksum_hex,
        validated_media_kind=upload.validated_media_kind,
        validated_format=upload.validated_format,
        state=UploadPublicationState.RESERVED,
        cleanup_state=UploadPublicationCleanupState.PENDING,
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
        verified_at_ms=None,
        cleanup_completed_at_ms=None,
        version=0,
        media_id=None,
        media_location_id=None,
    )


def ensure_upload_is_publication_candidate(upload: UploadSession) -> None:
    """Require complete authoritative evidence before target reservation."""
    if upload.state is not UploadSessionState.PUBLISH_PENDING:
        raise FrameNestUploadPublicationTransitionError(
            INVALID_UPLOAD_PUBLICATION_TRANSITION_MESSAGE
        )
    if (
        upload.received_size_bytes != upload.declared_size_bytes
        or upload.checksum_algorithm != "sha256"
        or upload.checksum_hex is None
        or upload.validated_media_kind is None
        or upload.validated_format is None
        or upload.byte_identity_id is None
    ):
        raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)


def ensure_publication_matches_upload(
    publication: UploadPublication,
    upload: UploadSession,
) -> None:
    """Require reservation provenance to match the upload's exact evidence."""
    if (
        publication.upload_id != upload.id
        or publication.byte_identity_id != upload.byte_identity_id
        or publication.expected_size_bytes != upload.declared_size_bytes
        or publication.checksum_algorithm != upload.checksum_algorithm
        or publication.checksum_hex != upload.checksum_hex
        or publication.validated_media_kind != upload.validated_media_kind
        or publication.validated_format != upload.validated_format
    ):
        raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)


def _validate_positive(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)


def _validate_non_negative(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestUploadPublicationError(INVALID_UPLOAD_PUBLICATION_MESSAGE)


def _validate_optional_non_negative(value: object) -> None:
    if value is not None:
        _validate_non_negative(value)
