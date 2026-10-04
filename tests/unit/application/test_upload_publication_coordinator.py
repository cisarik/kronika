"""Lifecycle and isolation tests for automatic publication coordination."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import threading
import time
import uuid

from kronika.application.ports.upload_publications import UploadPublicationCandidate
from kronika.application.upload_publication import (
    UploadPublicationInfrastructureError,
    UploadPublicationResult,
)
from kronika.application.upload_publication_coordinator import (
    UploadPublicationCoordinator,
)
from kronika.application.in_process_lifecycle import ShutdownDeadline
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.domain.identities import MediaByteIdentityId
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
    return UploadPublicationCandidate(
        upload=UploadSession(
            id=upload_id,
            state=UploadSessionState.PUBLISH_PENDING,
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
            version=2,
        ),
        publication=None,
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

    def list_candidates(
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


class _Publisher:
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
        self.thread_ids: list[int] = []
        self.started = threading.Event()

    def publish_owned_blocking(self, upload_id: UploadSessionId):
        upload_id_text = upload_id.to_string()
        self.calls.append(upload_id_text)
        self.thread_ids.append(threading.get_ident())
        self.started.set()
        if self._block is not None:
            self._block.wait(timeout=2)
        if self._fail(upload_id_text):
            raise UploadPublicationInfrastructureError(
                "upload publication operation failed"
            )
        self._repository.complete(upload_id)
        return UploadPublicationResult(upload_id_text, "published", "complete")


async def _wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("condition was not reached")
        await asyncio.sleep(0.005)


def test_startup_reconciliation_and_runtime_notifications_have_no_lost_wakeup() -> None:
    async def scenario() -> None:
        first = _candidate(1)
        second = _candidate(2)
        repository = _Repository((first,))
        publisher = _Publisher(repository)
        coordinator = UploadPublicationCoordinator(
            repository,
            publisher,
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

        assert publisher.calls.count(first.upload.id.to_string()) == 1
        assert publisher.calls.count(second.upload.id.to_string()) == 1
        assert coordinator.runner_done
        assert coordinator.active_count == 0
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_one_failed_upload_does_not_block_later_work_and_retry_is_backed_off() -> None:
    async def scenario() -> None:
        first = _candidate(1)
        second = _candidate(2)
        repository = _Repository((first, second))
        publisher = _Publisher(
            repository,
            fail=lambda upload_id: upload_id == first.upload.id.to_string(),
        )
        coordinator = UploadPublicationCoordinator(
            repository,
            publisher,
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
        assert second.upload.id.to_string() in publisher.calls
        assert 2 <= publisher.calls.count(first.upload.id.to_string()) <= 4

    asyncio.run(scenario())


def test_blocking_filesystem_work_runs_off_event_loop_and_shutdown_waits_without_orphan() -> None:
    async def scenario() -> None:
        candidate = _candidate(1)
        repository = _Repository((candidate,))
        release = threading.Event()
        publisher = _Publisher(repository, block=release)
        coordinator = UploadPublicationCoordinator(
            repository,
            publisher,
            UploadSessionLockRegistry(),
        )
        event_loop_thread = threading.get_ident()
        await coordinator.start()
        await asyncio.to_thread(publisher.started.wait, 1)
        shutdown_task = asyncio.create_task(coordinator.shutdown())
        await asyncio.sleep(0)
        assert not shutdown_task.done()
        release.set()
        await shutdown_task

        assert publisher.thread_ids
        assert all(thread_id != event_loop_thread for thread_id in publisher.thread_ids)
        assert coordinator.runner_done
        assert coordinator.active_count == 0
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_drain_batches_candidates_and_shared_lock_prevents_same_upload_overlap() -> None:
    async def scenario() -> None:
        candidate = _candidate(1)
        repository = _Repository((candidate,))
        publisher = _Publisher(repository)
        coordinator = UploadPublicationCoordinator(
            repository,
            publisher,
            UploadSessionLockRegistry(),
            batch_size=1,
        )
        await asyncio.gather(coordinator.drain(), coordinator.drain())
        await coordinator.shutdown()

        assert 1 <= publisher.calls.count(candidate.upload.id.to_string()) <= 2
        assert repository.get_candidate(candidate.upload.id) is None

    asyncio.run(scenario())


def test_expired_deadline_does_not_block_on_executor_wait() -> None:
    async def scenario() -> None:
        candidate = _candidate(1)
        repository = _Repository((candidate,))
        release = threading.Event()
        publisher = _Publisher(repository, block=release)
        coordinator = UploadPublicationCoordinator(
            repository,
            publisher,
            UploadSessionLockRegistry(),
        )
        await coordinator.start()
        await asyncio.to_thread(publisher.started.wait, 1)
        began = time.monotonic()
        await coordinator.shutdown(ShutdownDeadline(0.0))
        elapsed = time.monotonic() - began
        release.set()
        await asyncio.sleep(0.05)
        assert elapsed < 0.2
        assert coordinator.runner_done
        assert not coordinator.executor_running

    asyncio.run(scenario())
