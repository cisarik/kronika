"""Catalog-to-analysis notification boundary tests."""

from __future__ import annotations

import asyncio
import uuid

from kronika.application.ports.upload_publications import UploadPublicationCandidate
from kronika.application.upload_catalog import UploadCatalogResult
from kronika.application.upload_catalog_coordinator import UploadCatalogCoordinator
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.domain.identities import LibraryId, MediaByteIdentityId, MediaId, MediaLocationId
from kronika.domain.upload_publications import (
    UploadPublication,
    UploadPublicationCleanupState,
    UploadPublicationId,
    UploadPublicationRelativePath,
    UploadPublicationState,
)
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
    UploadValidatedFormat,
    UploadValidatedMediaKind,
)


class _Repo:
    def __init__(self, candidate: UploadPublicationCandidate) -> None:
        self._candidate = candidate
        self._done = False

    def get_candidate(self, upload_id):
        if self._candidate.upload.id != upload_id:
            return None
        return self._candidate

    def list_catalog_candidates(self, *, limit, after_updated_at_ms=None, after_id=None):
        del limit, after_updated_at_ms, after_id
        if self._done:
            return ()
        return (self._candidate,)

    def mark_done(self) -> None:
        self._done = True


class _Cataloger:
    def __init__(self, repo: _Repo, media_id: str, location_id: str) -> None:
        self._repo = repo
        self._media_id = media_id
        self._location_id = location_id
        self.calls = 0

    def catalog_owned_blocking(self, upload_id):
        self.calls += 1
        self._repo.mark_done()
        return UploadCatalogResult(
            upload_id.to_string(),
            "cataloged",
            self._media_id,
            self._location_id,
        )


class _Notifier:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def notify_cataloged(self, media_id: MediaId, media_location_id: MediaLocationId) -> None:
        self.calls.append((media_id.to_string(), media_location_id.to_string()))


def _candidate() -> UploadPublicationCandidate:
    upload_id = UploadSessionId(uuid.UUID("11111111-1111-4111-8111-111111111111"))
    publication_id = UploadPublicationId(
        uuid.UUID("22222222-2222-4222-8222-222222222222")
    )
    return UploadPublicationCandidate(
        upload=UploadSession(
            id=upload_id,
            state=UploadSessionState.PUBLISHED,
            storage_key=UploadStorageKey("synthetic-upload-0001"),
            display_filename=UploadDisplayFilename("synthetic.mp4"),
            declared_size_bytes=8,
            received_size_bytes=8,
            checksum_algorithm="sha256",
            checksum_hex="a" * 64,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            byte_identity_id=MediaByteIdentityId(
                uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            ),
            created_at_ms=10,
            updated_at_ms=20,
            expires_at_ms=100,
            failure_code=None,
            version=5,
        ),
        publication=UploadPublication(
            upload_id=upload_id,
            publication_id=publication_id,
            destination_id=LibraryId(
                uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
            ),
            relative_path=UploadPublicationRelativePath.for_publication(
                publication_id,
                UploadValidatedFormat.MP4,
            ),
            byte_identity_id=MediaByteIdentityId(
                uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
            ),
            expected_size_bytes=8,
            checksum_algorithm="sha256",
            checksum_hex="a" * 64,
            validated_media_kind=UploadValidatedMediaKind.VIDEO,
            validated_format=UploadValidatedFormat.MP4,
            state=UploadPublicationState.VERIFIED,
            cleanup_state=UploadPublicationCleanupState.COMPLETE,
            created_at_ms=21,
            updated_at_ms=31,
            verified_at_ms=30,
            cleanup_completed_at_ms=31,
            version=2,
            media_id=None,
            media_location_id=None,
        ),
    )


def test_successful_cataloging_notifies_analysis_once() -> None:
    media_id = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"
    location_id = "ffffffff-ffff-4fff-8fff-ffffffffffff"
    repo = _Repo(_candidate())
    cataloger = _Cataloger(repo, media_id, location_id)
    notifier = _Notifier()
    coordinator = UploadCatalogCoordinator(
        repo,
        cataloger,
        UploadSessionLockRegistry(),
        analysis_notifier=notifier,
    )

    async def scenario() -> None:
        await coordinator.drain()

    asyncio.run(scenario())
    assert cataloger.calls == 1
    assert notifier.calls == [(media_id, location_id)]
