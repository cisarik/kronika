"""Domain tests for durable upload publication provenance."""

from __future__ import annotations

from dataclasses import replace
import uuid

import pytest

from kronika.domain.identities import LibraryId, MediaByteIdentityId
from kronika.domain.upload_publications import (
    FrameNestUploadPublicationError,
    UploadPublicationCleanupState,
    UploadPublicationId,
    UploadPublicationRelativePath,
    UploadPublicationState,
    ensure_publication_matches_upload,
    new_upload_publication_reservation,
)
from kronika.domain.uploads import (
    FrameNestUploadSessionTransitionError,
    UploadDisplayFilename,
    UploadDuplicateDisposition,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
    ensure_upload_session_transition_allowed,
)


def _uuid4(text: str) -> uuid.UUID:
    return uuid.UUID(text)


def _pending_upload(
    upload_id: str = "11111111-1111-4111-8111-111111111111",
    *,
    disposition: UploadDuplicateDisposition | None = None,
) -> UploadSession:
    return UploadSession(
        id=UploadSessionId(_uuid4(upload_id)),
        state=UploadSessionState.PUBLISH_PENDING,
        storage_key=UploadStorageKey(upload_id.replace("-", "")),
        display_filename=UploadDisplayFilename("client-name.mp4"),
        declared_size_bytes=8,
        received_size_bytes=8,
        checksum_algorithm="sha256",
        checksum_hex="a" * 64,
        validated_media_kind=UploadValidatedMediaKind.VIDEO,
        validated_format=UploadValidatedFormat.MP4,
        byte_identity_id=MediaByteIdentityId(
            _uuid4("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
        ),
        duplicate_disposition=disposition,
        created_at_ms=10,
        updated_at_ms=20,
        expires_at_ms=100,
        failure_code=None,
        version=4,
    )


def _destination() -> LibraryId:
    return LibraryId(_uuid4("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"))


def test_reservation_uses_one_opaque_server_target_and_exact_upload_evidence() -> None:
    upload = _pending_upload()
    publication = new_upload_publication_reservation(
        upload,
        destination_id=_destination(),
        now_ms=25,
    )

    assert publication.upload_id == upload.id
    assert publication.relative_path.value == f"{publication.publication_id.value.hex}.mp4"
    assert "client-name" not in publication.relative_path.value
    assert upload.checksum_hex not in publication.relative_path.value
    assert publication.byte_identity_id == upload.byte_identity_id
    assert publication.expected_size_bytes == upload.declared_size_bytes
    assert publication.state is UploadPublicationState.RESERVED
    assert publication.cleanup_state is UploadPublicationCleanupState.PENDING
    ensure_publication_matches_upload(publication, upload)


def test_kept_exact_duplicate_receives_a_distinct_per_upload_target() -> None:
    first = new_upload_publication_reservation(
        _pending_upload(),
        destination_id=_destination(),
        now_ms=25,
    )
    duplicate = new_upload_publication_reservation(
        _pending_upload(
            "22222222-2222-4222-8222-222222222222",
            disposition=UploadDuplicateDisposition.KEEP_SEPARATE,
        ),
        destination_id=_destination(),
        now_ms=25,
    )

    assert first.byte_identity_id == duplicate.byte_identity_id
    assert first.publication_id != duplicate.publication_id
    assert first.relative_path != duplicate.relative_path


def test_publication_rejects_path_like_or_identity_mismatched_targets() -> None:
    publication = new_upload_publication_reservation(
        _pending_upload(),
        destination_id=_destination(),
        now_ms=25,
    )

    for unsafe in (
        "../private.mp4",
        "/absolute.mp4",
        "client-name.mp4",
        f"{publication.publication_id.value.hex}.gif",
    ):
        with pytest.raises(FrameNestUploadPublicationError):
            replace(publication, relative_path=UploadPublicationRelativePath(unsafe))

    with pytest.raises(FrameNestUploadPublicationError):
        ensure_publication_matches_upload(
            publication,
            replace(_pending_upload(), checksum_hex="b" * 64),
        )


def test_progress_invariants_require_verified_ownership_before_cleanup() -> None:
    publication = new_upload_publication_reservation(
        _pending_upload(),
        destination_id=_destination(),
        now_ms=25,
    )

    with pytest.raises(FrameNestUploadPublicationError):
        replace(
            publication,
            cleanup_state=UploadPublicationCleanupState.COMPLETE,
            cleanup_completed_at_ms=30,
        )
    verified = replace(
        publication,
        state=UploadPublicationState.VERIFIED,
        verified_at_ms=30,
        updated_at_ms=30,
        version=1,
    )
    completed = replace(
        verified,
        cleanup_state=UploadPublicationCleanupState.COMPLETE,
        cleanup_completed_at_ms=31,
        updated_at_ms=31,
        version=2,
    )

    assert completed.cleanup_state is UploadPublicationCleanupState.COMPLETE


def test_generic_transition_authority_cannot_claim_external_effect_states() -> None:
    with pytest.raises(FrameNestUploadSessionTransitionError):
        ensure_upload_session_transition_allowed(
            UploadSessionState.PUBLISH_PENDING,
            UploadSessionState.PUBLISHED,
        )
    with pytest.raises(FrameNestUploadSessionTransitionError):
        ensure_upload_session_transition_allowed(
            UploadSessionState.PUBLISHED,
            UploadSessionState.CATALOGED,
        )
