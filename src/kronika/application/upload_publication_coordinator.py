"""Lifecycle-owned bounded coordinator for automatic upload publication."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from enum import Enum, auto
from typing import Protocol

from kronika.application.in_process_lifecycle import (
    IterationFailureLimiter,
    ShutdownDeadline,
    attach_unexpected_runner_observer,
    shutdown_executor_backed_coordinator,
)
from kronika.application.ports.upload_publications import (
    FrameNestUploadPublicationRepositoryError,
    UploadPublicationCandidate,
    UploadPublicationRepository,
)
from kronika.application.upload_publication import (
    UploadPublicationError,
    UploadPublicationResult,
)
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.domain.upload_publications import UploadPublicationCleanupState
from kronika.domain.uploads import UploadSessionId, UploadSessionState
from kronika.structured_logging import get_logger

LOGGER = get_logger("upload_publication_coordinator")

DEFAULT_UPLOAD_PUBLICATION_BATCH_SIZE = 32
DEFAULT_PUBLICATION_RETRY_INITIAL_DELAY_SECONDS = 0.25
DEFAULT_PUBLICATION_RETRY_MAX_DELAY_SECONDS = 5.0


class _DrainOutcome(Enum):
    IDLE = auto()
    RETRY = auto()
    SHUTDOWN = auto()


class _CandidateOutcome(Enum):
    PROGRESS = auto()
    RETRY = auto()
    STALE = auto()
    SHUTDOWN = auto()


class _UploadPublisher(Protocol):
    def publish_owned_blocking(
        self,
        upload_id: UploadSessionId,
    ) -> UploadPublicationResult:
        """Recover one upload while the caller owns its process-local lock."""


class UploadPublicationCoordinator:
    """Run publication through one consumer in the current single-process topology.

    SQLite guards and the shared per-upload lock converge work inside one process.
    Multiprocess leases and fencing remain explicitly deferred.
    """

    def __init__(
        self,
        repository: UploadPublicationRepository,
        publisher: _UploadPublisher,
        locks: UploadSessionLockRegistry,
        *,
        batch_size: int = DEFAULT_UPLOAD_PUBLICATION_BATCH_SIZE,
        executor: ThreadPoolExecutor | None = None,
        retry_initial_delay_seconds: float = (
            DEFAULT_PUBLICATION_RETRY_INITIAL_DELAY_SECONDS
        ),
        retry_max_delay_seconds: float = DEFAULT_PUBLICATION_RETRY_MAX_DELAY_SECONDS,
        catalog_coordinator: object | None = None,
    ) -> None:
        if isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("upload publication batch size must be positive")
        if (
            isinstance(retry_initial_delay_seconds, bool)
            or retry_initial_delay_seconds < 0
        ):
            raise ValueError("upload publication retry delay must be non-negative")
        if (
            isinstance(retry_max_delay_seconds, bool)
            or retry_max_delay_seconds < retry_initial_delay_seconds
        ):
            raise ValueError("upload publication retry max delay is invalid")
        self._repository = repository
        self._publisher = publisher
        self._locks = locks
        self._batch_size = batch_size
        self._executor = executor
        self._owns_executor = executor is None
        self._retry_initial_delay_seconds = retry_initial_delay_seconds
        self._retry_max_delay_seconds = retry_max_delay_seconds
        self._current_retry_delay_seconds = retry_initial_delay_seconds
        self._runner: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._active_upload_ids: set[str] = set()
        self._catalog_coordinator = catalog_coordinator
        self._iteration_failures = IterationFailureLimiter()

    async def start(self) -> None:
        """Start one runner and wake startup reconciliation."""
        if self._runner is not None:
            if self._runner.done() and not self._stopping:
                raise RuntimeError("upload publication coordinator runner is not active")
            return
        self._stopping = False
        self._current_retry_delay_seconds = self._retry_initial_delay_seconds
        self._ensure_executor()
        self._wake = asyncio.Event()
        self._runner = asyncio.create_task(
            self._run(),
            name="framenest-upload-publication-coordinator",
        )
        attach_unexpected_runner_observer(
            self._runner,
            is_expected=lambda: self._stopping,
            log_unexpected=_log_unexpected_runner_death,
        )
        self.notify()

    def notify(self) -> None:
        """Wake the runner after durable work becomes eligible."""
        runner = self._runner
        if runner is not None and runner.done() and not self._stopping:
            raise RuntimeError("upload publication coordinator runner is not active")
        if self._wake is not None and not self._stopping:
            self._wake.set()

    async def drain(self) -> None:
        """Process currently discoverable work once for deterministic tests."""
        self._ensure_executor()
        await self._drain_once()

    async def shutdown(
        self,
        deadline: ShutdownDeadline | None = None,
    ) -> None:
        """Stop claiming work and wait for owned blocking work to settle."""
        self._stopping = True
        if self._wake is not None:
            self._wake.set()
        runner = self._runner
        cancellation: asyncio.CancelledError | None = None
        try:
            cancellation = await shutdown_executor_backed_coordinator(
                runner=runner,
                executor=self._executor,
                owns_executor=self._owns_executor,
                deadline=deadline,
                interrupt_owned_work=self._interrupt_owned_work,
                log_runner_fault=lambda: _safe_log(
                    level="WARNING",
                    event="upload_publication_runner_shutdown_fault",
                    operation="upload_publication_shutdown",
                    error_code="UPLOAD_PUBLICATION_RUNNER_SHUTDOWN_FAULT",
                    retryable=False,
                ),
                log_unresolved=lambda: _safe_log(
                    level="WARNING",
                    event="upload_publication_executor_unresolved",
                    operation="upload_publication_shutdown",
                    error_code="UPLOAD_PUBLICATION_EXECUTOR_UNRESOLVED",
                    retryable=False,
                ),
            )
        finally:
            self._runner = None
            self._wake = None
            self._active_upload_ids.clear()
            self._current_retry_delay_seconds = self._retry_initial_delay_seconds
            if self._owns_executor:
                self._executor = None
        if cancellation is not None:
            raise cancellation

    def _interrupt_owned_work(self) -> None:
        request_stop = getattr(self._publisher, "request_stop", None)
        if callable(request_stop):
            request_stop()

    @property
    def runner_done(self) -> bool:
        return self._runner is None or self._runner.done()

    @property
    def active_count(self) -> int:
        return len(self._active_upload_ids)

    @property
    def executor_running(self) -> bool:
        return self._executor is not None

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            await self._wake.wait()
            self._wake.clear()
            if self._stopping:
                return
            try:
                await self._reconcile_until_idle()
            except Exception:
                if self._iteration_failures.allow():
                    _safe_log(
                        level="ERROR",
                        event="upload_publication_runner_iteration_failed",
                        operation="upload_publication_run",
                        error_code="UPLOAD_PUBLICATION_RUNNER_ITERATION_FAILED",
                        retryable=True,
                    )

    async def _reconcile_until_idle(self) -> None:
        while not self._stopping:
            outcome = await self._drain_once()
            if outcome is _DrainOutcome.SHUTDOWN:
                return
            if outcome is _DrainOutcome.RETRY:
                await self._wait_before_retry()
                self._increase_retry_delay()
                continue
            self._reset_retry_delay()
            if self._wake is not None and self._wake.is_set():
                self._wake.clear()
                continue
            return

    async def _drain_once(self) -> _DrainOutcome:
        cursor: tuple[int, str] | None = None
        visited: set[str] = set()
        retry_needed = False
        while not self._stopping:
            candidates = await self._discover(after=cursor)
            if candidates is None:
                return _DrainOutcome.RETRY
            if not candidates:
                return _DrainOutcome.RETRY if retry_needed else _DrainOutcome.IDLE
            for candidate in candidates:
                cursor = (
                    candidate.upload.updated_at_ms,
                    candidate.upload.id.to_string(),
                )
                if self._stopping:
                    return _DrainOutcome.SHUTDOWN
                upload_id = candidate.upload.id.to_string()
                if upload_id in visited:
                    continue
                visited.add(upload_id)
                outcome = await self._process_candidate(candidate)
                if outcome is _CandidateOutcome.SHUTDOWN:
                    return _DrainOutcome.SHUTDOWN
                if outcome is _CandidateOutcome.RETRY:
                    retry_needed = True
        return _DrainOutcome.SHUTDOWN

    async def _discover(
        self,
        *,
        after: tuple[int, str] | None,
    ) -> tuple[UploadPublicationCandidate, ...] | None:
        try:
            return await self._run_blocking(
                self._repository.list_candidates,
                limit=self._batch_size,
                after_updated_at_ms=None if after is None else after[0],
                after_id=None if after is None else after[1],
            )
        except Exception:
            _safe_log(
                level="WARNING",
                event="upload_publication_candidate_discovery_failed",
                operation="upload_publication_discovery",
                error_code="UPLOAD_PUBLICATION_DISCOVERY_FAILED",
                retryable=True,
            )
            return None

    async def _process_candidate(
        self,
        candidate: UploadPublicationCandidate,
    ) -> _CandidateOutcome:
        upload_id = candidate.upload.id.to_string()
        if upload_id in self._active_upload_ids or not _eligible(candidate):
            return _CandidateOutcome.STALE
        self._active_upload_ids.add(upload_id)
        try:
            async with self._locks.lease(candidate.upload.id):
                if self._stopping:
                    return _CandidateOutcome.SHUTDOWN
                await self._run_blocking(
                    self._publisher.publish_owned_blocking,
                    candidate.upload.id,
                )
            _notify_catalog_coordinator(self._catalog_coordinator)
            return _CandidateOutcome.PROGRESS
        except UploadPublicationError:
            return await self._classify_after_error(candidate)
        except Exception:
            _safe_log(
                level="WARNING",
                event="upload_publication_candidate_processing_failed",
                operation="upload_publication_candidate",
                error_code="UPLOAD_PUBLICATION_CANDIDATE_FAILED",
                retryable=True,
            )
            return await self._classify_after_error(candidate)
        finally:
            self._active_upload_ids.discard(upload_id)

    async def _classify_after_error(
        self,
        candidate: UploadPublicationCandidate,
    ) -> _CandidateOutcome:
        try:
            current = await self._run_blocking(
                self._repository.get_candidate,
                candidate.upload.id,
            )
        except FrameNestUploadPublicationRepositoryError:
            return _CandidateOutcome.RETRY
        except Exception:
            return _CandidateOutcome.RETRY
        if current is None or not _eligible(current):
            return _CandidateOutcome.STALE
        return _CandidateOutcome.RETRY

    async def _wait_before_retry(self) -> None:
        delay = self._current_retry_delay_seconds
        if delay <= 0:
            await asyncio.sleep(0)
            return
        if self._wake is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(self._wake.wait(), timeout=delay)
        except TimeoutError:
            return
        finally:
            if self._wake.is_set():
                self._wake.clear()

    def _increase_retry_delay(self) -> None:
        if self._current_retry_delay_seconds <= 0:
            return
        self._current_retry_delay_seconds = min(
            self._current_retry_delay_seconds * 2,
            self._retry_max_delay_seconds,
        )

    def _reset_retry_delay(self) -> None:
        self._current_retry_delay_seconds = self._retry_initial_delay_seconds

    async def _run_blocking(self, func, /, *args, **kwargs):
        loop = asyncio.get_running_loop()
        executor = self._ensure_executor()
        return await loop.run_in_executor(
            executor,
            lambda: func(*args, **kwargs),
        )

    def _ensure_executor(self) -> ThreadPoolExecutor:
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="framenest-upload-publication",
            )
        return self._executor


def _eligible(candidate: UploadPublicationCandidate) -> bool:
    if candidate.upload.state is UploadSessionState.PUBLISH_PENDING:
        return True
    return bool(
        candidate.upload.state is UploadSessionState.PUBLISHED
        and candidate.publication is not None
        and candidate.publication.cleanup_state
        is UploadPublicationCleanupState.PENDING
    )


def _notify_catalog_coordinator(coordinator: object | None) -> None:
    if coordinator is None:
        return
    try:
        notify = getattr(coordinator, "notify")
        notify()
    except Exception:
        _safe_log(
            level="WARNING",
            event="upload_catalog_notify_failed",
            operation="upload_publication_catalog_notify",
            error_code="UPLOAD_CATALOG_NOTIFY_FAILED",
            retryable=True,
        )


def _safe_log(**fields: object) -> None:
    try:
        LOGGER.emit(**fields)
    except Exception:
        return


def _log_unexpected_runner_death() -> None:
    _safe_log(
        level="ERROR",
        event="upload_publication_runner_unexpected_death",
        operation="upload_publication_run",
        error_code="UPLOAD_PUBLICATION_RUNNER_UNEXPECTED_DEATH",
        retryable=False,
    )
