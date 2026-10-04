"""Lifecycle-owned orchestration for durable upload validation."""

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
from kronika.application.ports.upload_sessions import (
    FrameNestUploadSessionRepositoryError,
    UploadSessionRepository,
)
from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.application.upload_validation import (
    UploadValidationConcurrencyError,
    UploadValidationError,
    UploadValidationInterruptedError,
    UploadValidationNotFoundError,
    UploadValidationResult,
    UploadValidationStateConflictError,
    ValidateReceivedUpload,
)
from kronika.domain.uploads import UploadSession, UploadSessionId, UploadSessionState
from kronika.structured_logging import get_logger

LOGGER = get_logger("upload_validation_coordinator")

DEFAULT_UPLOAD_VALIDATION_BATCH_SIZE = 32
DEFAULT_DISCOVERY_RETRY_INITIAL_DELAY_SECONDS = 0.25
DEFAULT_DISCOVERY_RETRY_MAX_DELAY_SECONDS = 5.0


class _DiscoveryMode(Enum):
    STARTUP = auto()
    RUNTIME = auto()


class _DrainOutcome(Enum):
    IDLE = auto()
    DISCOVERY_FAILED = auto()
    SHUTDOWN = auto()


class _CandidateOutcome(Enum):
    DURABLE_PROGRESS = auto()
    TERMINAL_OR_STALE = auto()
    NO_PROGRESS = auto()
    SHUTDOWN = auto()


_CandidateCursor = tuple[int, str] | None


class _UploadValidator(Protocol):
    def validate_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        """Validate a received upload while the caller owns the process-local lock."""

    def recover_abandoned_validating_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        """Validate a startup-abandoned validating upload without reclaiming it."""


class UploadValidationCoordinator:
    """Run durable upload validation through one application-owned consumer.

    The coordinator is intentionally scoped to one FrameNest process, one Uvicorn
    worker, and one validation consumer. Durable recovery handles forced process
    loss, but this class does not provide a lease, heartbeat, or multi-process lock.
    """

    def __init__(
        self,
        repository: UploadSessionRepository,
        validator: _UploadValidator | ValidateReceivedUpload,
        locks: UploadSessionLockRegistry,
        *,
        batch_size: int = DEFAULT_UPLOAD_VALIDATION_BATCH_SIZE,
        executor: ThreadPoolExecutor | None = None,
        discovery_retry_initial_delay_seconds: float = (
            DEFAULT_DISCOVERY_RETRY_INITIAL_DELAY_SECONDS
        ),
        discovery_retry_max_delay_seconds: float = DEFAULT_DISCOVERY_RETRY_MAX_DELAY_SECONDS,
        publication_coordinator: object | None = None,
        process_runner: object | None = None,
    ) -> None:
        if isinstance(batch_size, bool) or batch_size <= 0:
            raise ValueError("upload validation batch size must be positive")
        if (
            isinstance(discovery_retry_initial_delay_seconds, bool)
            or discovery_retry_initial_delay_seconds < 0
        ):
            raise ValueError("upload validation discovery retry delay must be non-negative")
        if (
            isinstance(discovery_retry_max_delay_seconds, bool)
            or discovery_retry_max_delay_seconds < discovery_retry_initial_delay_seconds
        ):
            raise ValueError("upload validation discovery retry max delay is invalid")
        self._repository = repository
        self._validator = validator
        self._locks = locks
        self._batch_size = batch_size
        self._executor = executor
        self._owns_executor = executor is None
        self._discovery_retry_initial_delay_seconds = discovery_retry_initial_delay_seconds
        self._discovery_retry_max_delay_seconds = discovery_retry_max_delay_seconds
        self._publication_coordinator = publication_coordinator
        self._process_runner = process_runner
        self._iteration_failures = IterationFailureLimiter()
        self._current_discovery_retry_delay_seconds = (
            discovery_retry_initial_delay_seconds
        )
        self._runner: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._startup_reconciliation_complete = False
        self._active_upload_ids: set[str] = set()

    async def start(self) -> None:
        """Start the lifecycle-owned runner and schedule startup reconciliation."""
        if self._runner is not None:
            if self._runner.done() and not self._stopping:
                raise RuntimeError("upload validation coordinator runner is not active")
            return
        self._stopping = False
        self._startup_reconciliation_complete = False
        self._current_discovery_retry_delay_seconds = (
            self._discovery_retry_initial_delay_seconds
        )
        self._ensure_executor()
        self._wake = asyncio.Event()
        self._runner = asyncio.create_task(
            self._run(),
            name="framenest-upload-validation-coordinator",
        )
        attach_unexpected_runner_observer(
            self._runner,
            is_expected=lambda: self._stopping,
            log_unexpected=_log_unexpected_runner_death,
        )
        self.notify()

    def notify(self) -> None:
        """Wake the runner to reconcile durable validation candidates."""
        runner = self._runner
        if runner is not None and runner.done() and not self._stopping:
            raise RuntimeError("upload validation coordinator runner is not active")
        wake = self._wake
        if wake is not None and not self._stopping:
            wake.set()

    async def drain(self) -> None:
        """Deterministically process currently discoverable validation work."""
        self._ensure_executor()
        mode = (
            _DiscoveryMode.RUNTIME
            if self._startup_reconciliation_complete
            else _DiscoveryMode.STARTUP
        )
        outcome = await self._drain_reconciliation(mode)
        if mode is _DiscoveryMode.STARTUP and outcome is not _DrainOutcome.DISCOVERY_FAILED:
            self._startup_reconciliation_complete = True

    async def shutdown(
        self,
        deadline: ShutdownDeadline | None = None,
    ) -> None:
        """Stop claiming new uploads and retain ownership until the runner exits."""
        self._stopping = True
        wake = self._wake
        if wake is not None:
            wake.set()
        runner = self._runner
        cancellation: asyncio.CancelledError | None = None
        try:
            cancellation = await shutdown_executor_backed_coordinator(
                runner=runner,
                executor=self._executor,
                owns_executor=self._owns_executor,
                deadline=deadline,
                interrupt_owned_work=lambda: self._interrupt_owned_work(deadline),
                log_runner_fault=lambda: _safe_log(
                    level="WARNING",
                    event="upload_validation_runner_shutdown_fault",
                    operation="upload_validation_shutdown",
                    error_code="UPLOAD_VALIDATION_RUNNER_SHUTDOWN_FAULT",
                    retryable=False,
                ),
                log_unresolved=lambda: _safe_log(
                    level="WARNING",
                    event="upload_validation_executor_unresolved",
                    operation="upload_validation_shutdown",
                    error_code="UPLOAD_VALIDATION_EXECUTOR_UNRESOLVED",
                    retryable=False,
                ),
            )
        finally:
            self._runner = None
            self._wake = None
            self._active_upload_ids.clear()
            self._current_discovery_retry_delay_seconds = (
                self._discovery_retry_initial_delay_seconds
            )
            if self._owns_executor:
                self._executor = None
        if cancellation is not None:
            raise cancellation

    def _interrupt_owned_work(self, deadline: ShutdownDeadline | None) -> None:
        remaining = None if deadline is None else deadline.remaining_seconds()
        runner = self._process_runner
        interrupt = getattr(runner, "interrupt", None) if runner is not None else None
        if callable(interrupt):
            try:
                interrupt(remaining_seconds=remaining)
            except TypeError:
                interrupt()
        request_stop = getattr(self._validator, "request_stop", None)
        if callable(request_stop):
            request_stop()

    @property
    def runner_done(self) -> bool:
        """Return whether the lifecycle runner has no pending asyncio task."""
        return self._runner is None or self._runner.done()

    @property
    def active_count(self) -> int:
        """Return active upload validations for deterministic tests."""
        return len(self._active_upload_ids)

    @property
    def executor_running(self) -> bool:
        """Return whether the owned execution boundary still has an executor."""
        return self._executor is not None

    async def _run(self) -> None:
        assert self._wake is not None
        while not self._stopping:
            await self._wake.wait()
            self._wake.clear()
            if self._stopping:
                break
            try:
                await self._reconcile_until_idle()
            except Exception:
                if self._iteration_failures.allow():
                    _safe_log(
                        level="ERROR",
                        event="upload_validation_runner_iteration_failed",
                        operation="upload_validation_run",
                        error_code="UPLOAD_VALIDATION_RUNNER_ITERATION_FAILED",
                        retryable=True,
                    )

    async def _reconcile_until_idle(self) -> None:
        while not self._stopping:
            mode = (
                _DiscoveryMode.RUNTIME
                if self._startup_reconciliation_complete
                else _DiscoveryMode.STARTUP
            )
            outcome = await self._drain_reconciliation(mode)
            if outcome is _DrainOutcome.SHUTDOWN:
                return
            if outcome is _DrainOutcome.DISCOVERY_FAILED:
                await self._wait_before_discovery_retry()
                self._increase_discovery_retry_delay()
                continue
            self._reset_discovery_retry_delay()
            if mode is _DiscoveryMode.STARTUP:
                self._startup_reconciliation_complete = True
            wake = self._wake
            if wake is not None and wake.is_set():
                wake.clear()
                continue
            return

    async def _drain_reconciliation(self, mode: _DiscoveryMode) -> _DrainOutcome:
        cursor: _CandidateCursor = None
        visited_upload_ids: set[str] = set()
        while not self._stopping:
            candidates = await self._discover_candidates(mode, after=cursor)
            if candidates is None:
                return _DrainOutcome.DISCOVERY_FAILED
            if not candidates:
                return _DrainOutcome.IDLE
            for candidate in candidates:
                cursor = (candidate.updated_at_ms, candidate.id.to_string())
                if self._stopping:
                    return _DrainOutcome.SHUTDOWN
                upload_id = candidate.id.to_string()
                if upload_id in visited_upload_ids:
                    continue
                visited_upload_ids.add(upload_id)
                outcome = await self._process_candidate(candidate, mode)
                if outcome is _CandidateOutcome.SHUTDOWN:
                    return _DrainOutcome.SHUTDOWN
        return _DrainOutcome.SHUTDOWN

    async def _discover_candidates(
        self,
        mode: _DiscoveryMode,
        *,
        after: _CandidateCursor,
    ) -> tuple[UploadSession, ...] | None:
        after_updated_at_ms = None if after is None else after[0]
        after_id = None if after is None else after[1]
        list_candidates = (
            self._repository.list_startup_validation_candidates
            if mode is _DiscoveryMode.STARTUP
            else self._repository.list_runtime_validation_candidates
        )
        try:
            return await self._run_blocking(
                list_candidates,
                limit=self._batch_size,
                after_updated_at_ms=after_updated_at_ms,
                after_id=after_id,
            )
        except FrameNestUploadSessionRepositoryError:
            _safe_log(
                level="WARNING",
                event="upload_validation_candidate_discovery_failed",
                operation="upload_validation_discovery",
                error_code="UPLOAD_VALIDATION_DISCOVERY_FAILED",
                retryable=True,
            )
            return None
        except Exception:
            _safe_log(
                level="WARNING",
                event="upload_validation_candidate_discovery_failed",
                operation="upload_validation_discovery",
                error_code="UPLOAD_VALIDATION_DISCOVERY_FAILED",
                retryable=True,
            )
            return None

    async def _process_candidate(
        self,
        candidate: UploadSession,
        mode: _DiscoveryMode,
    ) -> _CandidateOutcome:
        upload_id = candidate.id.to_string()
        if upload_id in self._active_upload_ids:
            return _CandidateOutcome.NO_PROGRESS
        if candidate.state not in _eligible_states_for_mode(mode):
            return _CandidateOutcome.TERMINAL_OR_STALE
        self._active_upload_ids.add(upload_id)
        try:
            async with self._locks.lease(candidate.id):
                if self._stopping:
                    return _CandidateOutcome.SHUTDOWN
                if candidate.state is UploadSessionState.VALIDATING:
                    if mode is not _DiscoveryMode.STARTUP:
                        return _CandidateOutcome.TERMINAL_OR_STALE
                    result = await self._run_blocking(
                        self._validator.recover_abandoned_validating_owned_blocking,
                        candidate.id,
                    )
                else:
                    result = await self._run_blocking(
                        self._validator.validate_owned_blocking,
                        candidate.id,
                    )
            if result.state == UploadSessionState.PUBLISH_PENDING.value:
                _notify_publication_coordinator(self._publication_coordinator)
            return _classify_validation_result(candidate, result)
        except UploadValidationInterruptedError:
            return _CandidateOutcome.SHUTDOWN
        except (
            UploadValidationConcurrencyError,
            UploadValidationNotFoundError,
            UploadValidationStateConflictError,
        ):
            return await self._classify_candidate_after_error(candidate, mode)
        except UploadValidationError:
            return await self._classify_candidate_after_error(candidate, mode)
        except Exception:
            _safe_log(
                level="WARNING",
                event="upload_validation_candidate_processing_failed",
                operation="upload_validation_candidate",
                error_code="UPLOAD_VALIDATION_CANDIDATE_FAILED",
                retryable=True,
            )
            return await self._classify_candidate_after_error(candidate, mode)
        finally:
            self._active_upload_ids.discard(upload_id)

    async def _classify_candidate_after_error(
        self,
        candidate: UploadSession,
        mode: _DiscoveryMode,
    ) -> _CandidateOutcome:
        try:
            current = await self._run_blocking(self._repository.get, candidate.id)
        except FrameNestUploadSessionRepositoryError:
            return _CandidateOutcome.NO_PROGRESS
        except Exception:
            return _CandidateOutcome.NO_PROGRESS
        if current is None:
            return _CandidateOutcome.TERMINAL_OR_STALE
        if current.state is not candidate.state or current.version != candidate.version:
            return _CandidateOutcome.DURABLE_PROGRESS
        if current.state not in _eligible_states_for_mode(mode):
            return _CandidateOutcome.TERMINAL_OR_STALE
        return _CandidateOutcome.NO_PROGRESS

    async def _wait_before_discovery_retry(self) -> None:
        delay = self._current_discovery_retry_delay_seconds
        if delay <= 0:
            await asyncio.sleep(0)
            return
        wake = self._wake
        if wake is None:
            await asyncio.sleep(delay)
            return
        try:
            await asyncio.wait_for(wake.wait(), timeout=delay)
        except TimeoutError:
            return
        finally:
            if wake.is_set():
                wake.clear()

    def _increase_discovery_retry_delay(self) -> None:
        if self._current_discovery_retry_delay_seconds <= 0:
            return
        self._current_discovery_retry_delay_seconds = min(
            self._current_discovery_retry_delay_seconds * 2,
            self._discovery_retry_max_delay_seconds,
        )

    def _reset_discovery_retry_delay(self) -> None:
        self._current_discovery_retry_delay_seconds = (
            self._discovery_retry_initial_delay_seconds
        )

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
                thread_name_prefix="framenest-upload-validation",
            )
        return self._executor


def _eligible_states_for_mode(mode: _DiscoveryMode) -> set[UploadSessionState]:
    if mode is _DiscoveryMode.STARTUP:
        return {UploadSessionState.RECEIVED, UploadSessionState.VALIDATING}
    return {UploadSessionState.RECEIVED}


def _classify_validation_result(
    candidate: UploadSession,
    result: UploadValidationResult,
) -> _CandidateOutcome:
    if result.state != candidate.state.value:
        return _CandidateOutcome.DURABLE_PROGRESS
    return _CandidateOutcome.NO_PROGRESS


def _safe_log(**fields: object) -> None:
    try:
        LOGGER.emit(**fields)
    except Exception:
        return


def _log_unexpected_runner_death() -> None:
    _safe_log(
        level="ERROR",
        event="upload_validation_runner_unexpected_death",
        operation="upload_validation_run",
        error_code="UPLOAD_VALIDATION_RUNNER_UNEXPECTED_DEATH",
        retryable=False,
    )


def _notify_publication_coordinator(coordinator: object | None) -> None:
    if coordinator is None:
        return
    try:
        notify = getattr(coordinator, "notify")
        notify()
    except Exception:
        _safe_log(
            level="WARNING",
            event="upload_publication_notification_failed",
            operation="upload_validation_candidate",
            error_code="UPLOAD_PUBLICATION_NOTIFICATION_FAILED",
            retryable=True,
        )
