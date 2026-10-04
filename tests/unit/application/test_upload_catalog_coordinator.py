"""Lifecycle and isolation tests for published-to-cataloged coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import threading
import time
import uuid

from kronika.application.ports.upload_publications import UploadPublicationCandidate
from kronika.application.upload_catalog import (
    UploadCatalogInfrastructureError,
    UploadCatalogResult,
)
from kronika.application.upload_catalog_coordinator import UploadCatalogCoordinator
from kronika.application.in_process_lifecycle import ShutdownDeadline
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


def _candidate(index: int) -> UploadPublicationCandidate:
    upload_id = UploadSessionId(
        uuid.UUID(f"{index:08d}-1111-4111-8111-{index:012d}")
    )
    publication_id = UploadPublicationId(
        uuid.UUID(f"{index:08d}-2222-4222-8222-{index:012d}")
    )
    return UploadPublicationCandidate(
        upload=UploadSession(
            id=upload_id,
            state=UploadSessionState.PUBLISHED,
            storage_key=UploadStorageKey(f"synthetic-upload-{index:04d}"),
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
            updated_at_ms=10 + index,
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


class _Repository:
    def __init__(self, candidates: tuple[UploadPublicationCandidate, ...] = ()) -> None:
        self._candidates = {
            candidate.upload.id.to_string(): candidate for candidate in candidates
        }
        self._guard = threading.Lock()

    def add(self, candidate: UploadPublicationCandidate) -> None:
        with self._guard:
            self._candidates[candidate.upload.id.to_string()] = candidate

    def complete(self, upload_id: UploadSessionId) -> None:
        with self._guard:
            self._candidates.pop(upload_id.to_string(), None)

    def get_candidate(self, upload_id: UploadSessionId):
        with self._guard:
            return self._candidates.get(upload_id.to_string())

    def list_catalog_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ):
        with self._guard:
            ordered = sorted(
                self._candidates.values(),
                key=lambda candidate: (
                    candidate.upload.updated_at_ms,
                    candidate.upload.id.to_string(),
                ),
            )
        if after_updated_at_ms is not None:
            assert after_id is not None
            ordered = [
                candidate
                for candidate in ordered
                if (
                    candidate.upload.updated_at_ms,
                    candidate.upload.id.to_string(),
                )
                > (after_updated_at_ms, after_id)
            ]
        return tuple(ordered[:limit])


class _Cataloger:
    def __init__(
        self,
        repository: _Repository,
        *,
        fail: Callable[[str], bool] | None = None,
        block: threading.Event | None = None,
    ) -> None:
        self._repository = repository
        self._fail = fail or (lambda _: False)
        self._block = block
        self.calls: list[str] = []
        self.started = threading.Event()

    def catalog_owned_blocking(self, upload_id: UploadSessionId):
        upload_id_text = upload_id.to_string()
        self.calls.append(upload_id_text)
        self.started.set()
        if self._block is not None:
            self._block.wait(timeout=2)
        if self._fail(upload_id_text):
            raise UploadCatalogInfrastructureError("upload catalog operation failed")
        self._repository.complete(upload_id)
        return UploadCatalogResult(
            upload_id_text,
            "cataloged",
            MediaId.new().to_string(),
            MediaLocationId.new().to_string(),
        )


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.005)


def test_startup_reconciliation_and_duplicate_notifications_do_not_duplicate() -> None:
    async def scenario() -> None:
        first = _candidate(1)
        second = _candidate(2)
        repository = _Repository((first,))
        cataloger = _Cataloger(repository)
        coordinator = UploadCatalogCoordinator(
            repository,
            cataloger,
            UploadSessionLockRegistry(),
            retry_initial_delay_seconds=0.01,
            retry_max_delay_seconds=0.02,
        )
        await coordinator.start()
        await _wait_until(lambda: repository.get_candidate(first.upload.id) is None)
        repository.add(second)
        coordinator.notify()
        coordinator.notify()
        await _wait_until(lambda: repository.get_candidate(second.upload.id) is None)
        await coordinator.shutdown()

        assert cataloger.calls.count(first.upload.id.to_string()) == 1
        assert cataloger.calls.count(second.upload.id.to_string()) == 1
        assert coordinator.runner_done
        assert coordinator.active_count == 0
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_failure_leaves_published_retryable_and_does_not_block_later_work() -> None:
    async def scenario() -> None:
        first = _candidate(1)
        second = _candidate(2)
        repository = _Repository((first, second))
        cataloger = _Cataloger(
            repository,
            fail=lambda upload_id: upload_id == first.upload.id.to_string(),
        )
        coordinator = UploadCatalogCoordinator(
            repository,
            cataloger,
            UploadSessionLockRegistry(),
            batch_size=1,
            retry_initial_delay_seconds=0.05,
            retry_max_delay_seconds=0.1,
        )
        await coordinator.start()
        await _wait_until(lambda: repository.get_candidate(second.upload.id) is None)
        await asyncio.sleep(0.13)
        await coordinator.shutdown()

        assert repository.get_candidate(first.upload.id) is not None
        assert second.upload.id.to_string() in cataloger.calls
        assert 2 <= cataloger.calls.count(first.upload.id.to_string()) <= 4

    asyncio.run(scenario())


def test_in_process_lock_prevents_concurrent_duplicate_cataloging() -> None:
    async def scenario() -> None:
        candidate = _candidate(1)
        repository = _Repository((candidate,))
        block = threading.Event()
        cataloger = _Cataloger(repository, block=block)
        locks = UploadSessionLockRegistry()
        coordinator = UploadCatalogCoordinator(
            repository,
            cataloger,
            locks,
            retry_initial_delay_seconds=0.01,
            retry_max_delay_seconds=0.02,
        )
        await coordinator.start()
        await _wait_until(lambda: cataloger.started.is_set())
        assert coordinator.active_count == 1
        coordinator.notify()
        await asyncio.sleep(0.05)
        assert cataloger.calls.count(candidate.upload.id.to_string()) == 1
        block.set()
        await _wait_until(lambda: repository.get_candidate(candidate.upload.id) is None)
        await coordinator.shutdown()
        assert cataloger.calls.count(candidate.upload.id.to_string()) == 1

    asyncio.run(scenario())


def test_expired_deadline_does_not_block_on_executor_wait() -> None:
    async def scenario() -> None:
        candidate = _candidate(1)
        repository = _Repository((candidate,))
        block = threading.Event()
        cataloger = _Cataloger(repository, block=block)
        coordinator = UploadCatalogCoordinator(
            repository,
            cataloger,
            UploadSessionLockRegistry(),
        )
        await coordinator.start()
        await _wait_until(lambda: cataloger.started.is_set())
        began = time.monotonic()
        await coordinator.shutdown(ShutdownDeadline(0.0))
        elapsed = time.monotonic() - began
        block.set()
        await asyncio.sleep(0.05)
        assert elapsed < 0.2
        assert coordinator.runner_done
        assert not coordinator.executor_running

    asyncio.run(scenario())
