"""Unit tests for lifecycle-owned upload-validation orchestration."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path

import pytest

from kronika.application.upload_transport import UploadSessionLockRegistry
from kronika.application.upload_validation import UploadValidationResult
from kronika.application.upload_validation_coordinator import UploadValidationCoordinator
from kronika.application.in_process_lifecycle import ShutdownDeadline
from kronika.domain.uploads import (
    UploadDisplayFilename,
    UploadSession,
    UploadSessionId,
    UploadSessionState,
    UploadStorageKey,
)


def _session(
    state: UploadSessionState = UploadSessionState.RECEIVED,
    *,
    storage_key: str = "validation-candidate-0001",
    updated_at_ms: int = 10,
) -> UploadSession:
    return UploadSession(
        id=UploadSessionId.new(),
        state=state,
        storage_key=UploadStorageKey(storage_key),
        display_filename=UploadDisplayFilename("example.mp4"),
        declared_size_bytes=10,
        received_size_bytes=10,
        checksum_algorithm=None,
        checksum_hex=None,
        created_at_ms=10,
        updated_at_ms=updated_at_ms,
        expires_at_ms=1000,
        failure_code=None,
        version=0,
    )


class _Repository:
    def __init__(self, batches: list[tuple[UploadSession, ...]]) -> None:
        self._batches = batches
        self._guard = threading.Lock()
        self.limits: list[int] = []

    def list_startup_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        return self._list(limit=limit)

    def list_runtime_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        return self._list(limit=limit)

    def _list(self, *, limit: int) -> tuple[UploadSession, ...]:
        with self._guard:
            self.limits.append(limit)
            if not self._batches:
                return ()
            return self._batches.pop(0)[:limit]

    def get(self, session_id: UploadSessionId) -> UploadSession | None:
        return None


class _CursorRepository:
    def __init__(self, sessions: list[UploadSession]) -> None:
        self.sessions = sessions
        self.calls = 0
        self.startup_calls = 0
        self.runtime_calls = 0
        self.fail_startup_once = False
        self.fail_runtime_once = False

    def list_startup_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        self.calls += 1
        self.startup_calls += 1
        if self.fail_startup_once:
            self.fail_startup_once = False
            from kronika.application.ports.upload_sessions import (
                FrameNestUploadSessionRepositoryError,
            )

            raise FrameNestUploadSessionRepositoryError("temporary")
        return self._list(
            states={UploadSessionState.RECEIVED, UploadSessionState.VALIDATING},
            limit=limit,
            after_updated_at_ms=after_updated_at_ms,
            after_id=after_id,
        )

    def list_runtime_validation_candidates(
        self,
        *,
        limit: int,
        after_updated_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[UploadSession, ...]:
        self.calls += 1
        self.runtime_calls += 1
        if self.fail_runtime_once:
            self.fail_runtime_once = False
            from kronika.application.ports.upload_sessions import (
                FrameNestUploadSessionRepositoryError,
            )

            raise FrameNestUploadSessionRepositoryError("temporary")
        return self._list(
            states={UploadSessionState.RECEIVED},
            limit=limit,
            after_updated_at_ms=after_updated_at_ms,
            after_id=after_id,
        )

    def get(self, session_id: UploadSessionId) -> UploadSession | None:
        for session in self.sessions:
            if session.id == session_id:
                return session
        return None

    def _list(
        self,
        *,
        states: set[UploadSessionState],
        limit: int,
        after_updated_at_ms: int | None,
        after_id: str | None,
    ) -> tuple[UploadSession, ...]:
        candidates = [
            session
            for session in sorted(
                self.sessions,
                key=lambda item: (item.updated_at_ms, item.id.to_string()),
            )
            if session.state in states
            and (
                after_updated_at_ms is None
                or (session.updated_at_ms, session.id.to_string())
                > (after_updated_at_ms, after_id or "")
            )
        ]
        return tuple(candidates[:limit])


class _BlockingValidator:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[tuple[str, str]] = []
        self.active = 0
        self.max_active = 0
        self._guard = threading.Lock()

    def validate_owned_blocking(self, session_id: UploadSessionId) -> UploadValidationResult:
        return self._run("received", session_id)

    def recover_abandoned_validating_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        return self._run("validating", session_id)

    def _run(self, mode: str, session_id: UploadSessionId) -> UploadValidationResult:
        with self._guard:
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.calls.append((mode, session_id.to_string()))
        self.started.set()
        try:
            assert self.release.wait(timeout=5)
            return UploadValidationResult(
                upload_id=session_id.to_string(),
                state="publish_pending",
                checksum_algorithm="sha256",
                checksum_hex="a" * 64,
                validated_media_kind="video",
                validated_format="mp4",
                failure_code=None,
            )
        finally:
            with self._guard:
                self.active -= 1


class _ScriptedValidator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.fail_next_received = False

    def validate_owned_blocking(self, session_id: UploadSessionId) -> UploadValidationResult:
        self.calls.append(("received", session_id.to_string()))
        if self.fail_next_received:
            self.fail_next_received = False
            raise RuntimeError("unexpected validator failure")
        return UploadValidationResult(
            upload_id=session_id.to_string(),
            state="publish_pending",
            checksum_algorithm="sha256",
            checksum_hex="a" * 64,
            validated_media_kind="video",
            validated_format="mp4",
            failure_code=None,
        )

    def recover_abandoned_validating_owned_blocking(
        self,
        session_id: UploadSessionId,
    ) -> UploadValidationResult:
        self.calls.append(("validating", session_id.to_string()))
        return UploadValidationResult(
            upload_id=session_id.to_string(),
            state="validating",
            checksum_algorithm=None,
            checksum_hex=None,
            validated_media_kind=None,
            validated_format=None,
            failure_code=None,
        )


class _PublicationNotifier:
    def __init__(self) -> None:
        self.notifications = 0

    def notify(self) -> None:
        self.notifications += 1


async def _wait_until(condition) -> None:
    deadline = asyncio.get_running_loop().time() + 1
    while not condition():
        if asyncio.get_running_loop().time() >= deadline:
            raise TimeoutError("condition was not met")
        await asyncio.sleep(0.001)


def _live_validation_threads() -> list[threading.Thread]:
    return [
        thread
        for thread in threading.enumerate()
        if thread.name.startswith("framenest-upload-validation") and thread.is_alive()
    ]


def test_validation_success_notifies_automatic_publication_after_publish_pending() -> None:
    async def scenario() -> None:
        session = _session()
        notifier = _PublicationNotifier()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            _ScriptedValidator(),
            UploadSessionLockRegistry(),
            publication_coordinator=notifier,
        )

        await coordinator.drain()
        await coordinator.shutdown()

        assert notifier.notifications == 1

    asyncio.run(scenario())


def test_drain_runs_blocking_validation_off_event_loop() -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        drain = asyncio.create_task(coordinator.drain())
        await asyncio.wait_for(asyncio.to_thread(validator.started.wait), timeout=1)

        marker_ran = False

        async def marker() -> None:
            nonlocal marker_ran
            marker_ran = True

        await asyncio.wait_for(marker(), timeout=1)
        assert marker_ran
        assert not drain.done()

        validator.release.set()
        await asyncio.wait_for(drain, timeout=1)
        await coordinator.shutdown()

    asyncio.run(scenario())


def test_duplicate_notifications_share_one_runner_and_do_not_validate_concurrently() -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.start()
        coordinator.notify()
        coordinator.notify()
        await asyncio.wait_for(asyncio.to_thread(validator.started.wait), timeout=1)
        assert validator.max_active == 1
        assert len(validator.calls) == 1

        validator.release.set()
        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert coordinator.runner_done

    asyncio.run(scenario())


def test_startup_reconciles_received_and_abandoned_validating_without_claiming_validating() -> None:
    async def scenario() -> None:
        received = _session(UploadSessionState.RECEIVED, storage_key="validation-candidate-0001")
        validating = _session(
            UploadSessionState.VALIDATING,
            storage_key="validation-candidate-0002",
        )
        validator = _BlockingValidator()
        validator.release.set()
        coordinator = UploadValidationCoordinator(
            _Repository([(received, validating), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.drain()
        await coordinator.shutdown()

        assert validator.calls == [
            ("received", received.id.to_string()),
            ("validating", validating.id.to_string()),
        ]

    asyncio.run(scenario())


def test_unexpected_validator_exception_does_not_permanently_kill_runner() -> None:
    async def scenario() -> None:
        failed = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0001",
            updated_at_ms=10,
        )
        later = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0002",
            updated_at_ms=20,
        )
        repository = _CursorRepository([failed, later])
        validator = _ScriptedValidator()
        validator.fail_next_received = True
        coordinator = UploadValidationCoordinator(
            repository,
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.start()
        await _wait_until(lambda: len(validator.calls) >= 2)

        assert not coordinator.runner_done
        assert validator.calls == [
            ("received", failed.id.to_string()),
            ("received", later.id.to_string()),
        ]

        coordinator.notify()
        await asyncio.sleep(0)
        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert coordinator.runner_done
        assert not coordinator.executor_running
        assert not [
            thread
            for thread in threading.enumerate()
            if thread.name.startswith("framenest-upload-validation") and thread.is_alive()
        ]

    asyncio.run(scenario())


def test_no_progress_candidate_does_not_spin_or_starve_later_candidates() -> None:
    async def scenario() -> None:
        no_progress = _session(
            UploadSessionState.VALIDATING,
            storage_key="validation-candidate-0001",
            updated_at_ms=10,
        )
        later = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0002",
            updated_at_ms=20,
        )
        validator = _ScriptedValidator()
        coordinator = UploadValidationCoordinator(
            _CursorRepository([no_progress, later]),
            validator,
            UploadSessionLockRegistry(),
            batch_size=1,
        )

        await coordinator.drain()
        await coordinator.shutdown()

        assert validator.calls == [
            ("validating", no_progress.id.to_string()),
            ("received", later.id.to_string()),
        ]

    asyncio.run(scenario())


def test_startup_reconciliation_processes_multiple_batches_once() -> None:
    async def scenario() -> None:
        no_progress = _session(
            UploadSessionState.VALIDATING,
            storage_key="validation-candidate-0001",
            updated_at_ms=10,
        )
        received = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0002",
            updated_at_ms=20,
        )
        abandoned = _session(
            UploadSessionState.VALIDATING,
            storage_key="validation-candidate-0003",
            updated_at_ms=30,
        )
        failed = _session(
            UploadSessionState.FAILED,
            storage_key="validation-candidate-0004",
            updated_at_ms=40,
        )
        validator = _ScriptedValidator()
        repository = _CursorRepository([failed, no_progress, received, abandoned])
        coordinator = UploadValidationCoordinator(
            repository,
            validator,
            UploadSessionLockRegistry(),
            batch_size=2,
        )

        await coordinator.drain()
        await coordinator.shutdown()

        assert validator.calls == [
            ("validating", no_progress.id.to_string()),
            ("received", received.id.to_string()),
            ("validating", abandoned.id.to_string()),
        ]
        assert repository.startup_calls == 3

    asyncio.run(scenario())


def test_discovery_failure_retries_without_another_notification_and_resets() -> None:
    async def scenario() -> None:
        startup_candidate = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0001",
            updated_at_ms=10,
        )
        runtime_candidate = _session(
            UploadSessionState.RECEIVED,
            storage_key="validation-candidate-0002",
            updated_at_ms=20,
        )
        repository = _CursorRepository([startup_candidate])
        repository.fail_startup_once = True
        validator = _ScriptedValidator()
        coordinator = UploadValidationCoordinator(
            repository,
            validator,
            UploadSessionLockRegistry(),
            discovery_retry_initial_delay_seconds=0.001,
            discovery_retry_max_delay_seconds=0.001,
        )

        await coordinator.start()
        await _wait_until(
            lambda: ("received", startup_candidate.id.to_string()) in validator.calls
        )
        repository.sessions.append(runtime_candidate)
        repository.fail_runtime_once = True
        coordinator.notify()
        await _wait_until(
            lambda: ("received", runtime_candidate.id.to_string()) in validator.calls
        )

        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert repository.startup_calls >= 2
        assert repository.runtime_calls >= 2

    asyncio.run(scenario())


def test_shutdown_interrupts_discovery_retry_delay() -> None:
    async def scenario() -> None:
        repository = _CursorRepository([])
        repository.fail_startup_once = True
        validator = _ScriptedValidator()
        coordinator = UploadValidationCoordinator(
            repository,
            validator,
            UploadSessionLockRegistry(),
            discovery_retry_initial_delay_seconds=60,
            discovery_retry_max_delay_seconds=60,
        )

        await coordinator.start()
        await _wait_until(lambda: repository.startup_calls == 1)
        await asyncio.wait_for(coordinator.shutdown(), timeout=1)

        assert coordinator.runner_done
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_runtime_notification_does_not_recover_preexisting_validating() -> None:
    async def scenario() -> None:
        validating = _session(
            UploadSessionState.VALIDATING,
            storage_key="validation-candidate-0001",
            updated_at_ms=10,
        )
        repository = _CursorRepository([])
        validator = _ScriptedValidator()
        coordinator = UploadValidationCoordinator(
            repository,
            validator,
            UploadSessionLockRegistry(),
            discovery_retry_initial_delay_seconds=0.001,
            discovery_retry_max_delay_seconds=0.001,
        )

        await coordinator.start()
        await _wait_until(lambda: repository.startup_calls >= 1)
        repository.sessions.append(validating)
        coordinator.notify()
        await asyncio.sleep(0.05)
        await coordinator.shutdown()

        assert validator.calls == []

        fresh_validator = _ScriptedValidator()
        fresh = UploadValidationCoordinator(
            repository,
            fresh_validator,
            UploadSessionLockRegistry(),
        )
        await fresh.drain()
        await fresh.shutdown()
        assert fresh_validator.calls == [("validating", validating.id.to_string())]

    asyncio.run(scenario())


def test_active_in_process_validating_work_is_not_recovered_concurrently() -> None:
    async def scenario() -> None:
        session = _session(UploadSessionState.VALIDATING)
        locks = UploadSessionLockRegistry()
        validator = _BlockingValidator()
        validator.release.set()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            locks,
        )

        async with locks.lease(session.id):
            drain = asyncio.create_task(coordinator.drain())
            await asyncio.sleep(0)
            assert validator.calls == []
            assert not drain.done()

        await asyncio.wait_for(drain, timeout=1)
        await coordinator.shutdown()
        assert validator.calls == [("validating", session.id.to_string())]

    asyncio.run(scenario())


def test_shutdown_waits_for_owned_runner_without_orphaning_task_or_thread(tmp_path: Path) -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.start()
        await asyncio.wait_for(asyncio.to_thread(validator.started.wait), timeout=1)
        shutdown = asyncio.create_task(coordinator.shutdown())
        await asyncio.sleep(0)
        assert not shutdown.done()
        assert coordinator.active_count == 1

        validator.release.set()
        await asyncio.wait_for(shutdown, timeout=1)

        assert coordinator.runner_done
        assert not coordinator.executor_running
        assert not _live_validation_threads()

    asyncio.run(scenario())


def test_shutdown_after_runner_cancellation_cleans_owned_resources_and_reraises() -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.start()
        await asyncio.wait_for(asyncio.to_thread(validator.started.wait), timeout=1)
        runner = coordinator._runner
        assert runner is not None
        assert coordinator._wake is not None
        assert coordinator._executor is not None
        assert _live_validation_threads()

        runner.cancel()
        validator.release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(coordinator.shutdown(), timeout=1)

        assert coordinator._runner is None
        assert coordinator._wake is None
        assert coordinator._executor is None
        assert coordinator.runner_done
        assert not coordinator.executor_running
        assert not _live_validation_threads()

        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert coordinator._runner is None
        assert coordinator._wake is None
        assert coordinator._executor is None
        assert not _live_validation_threads()

    asyncio.run(scenario())


def test_caller_cancellation_during_shutdown_still_cleans_owned_resources() -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )

        await coordinator.start()
        await asyncio.wait_for(asyncio.to_thread(validator.started.wait), timeout=1)
        assert coordinator._executor is not None
        assert _live_validation_threads()

        shutdown = asyncio.create_task(coordinator.shutdown())
        await _wait_until(lambda: coordinator._stopping)
        shutdown.cancel()
        validator.release.set()
        try:
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(shutdown, timeout=1)
        finally:
            validator.release.set()
            if coordinator._executor is not None:
                coordinator._executor.shutdown(wait=True, cancel_futures=False)
                coordinator._executor = None
        await _wait_until(lambda: not _live_validation_threads())

        assert coordinator._runner is None
        assert coordinator._wake is None
        assert coordinator._executor is None
        assert coordinator.runner_done
        assert not coordinator.executor_running
        assert not _live_validation_threads()

        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert not _live_validation_threads()

    asyncio.run(scenario())


def test_shutdown_after_ordinary_runner_fault_cleans_owned_resources_and_is_idempotent() -> None:
    async def scenario() -> None:
        coordinator = UploadValidationCoordinator(
            _Repository([]),
            _ScriptedValidator(),
            UploadSessionLockRegistry(),
        )
        executor = coordinator._ensure_executor()
        await asyncio.get_running_loop().run_in_executor(executor, lambda: None)
        assert _live_validation_threads()

        async def fail_runner() -> None:
            raise RuntimeError("synthetic runner fault")

        coordinator._wake = asyncio.Event()
        coordinator._runner = asyncio.create_task(fail_runner())
        await asyncio.sleep(0)
        await asyncio.wait_for(coordinator.shutdown(), timeout=1)

        assert coordinator._runner is None
        assert coordinator._wake is None
        assert coordinator._executor is None
        assert coordinator.runner_done
        assert not coordinator.executor_running
        assert not _live_validation_threads()

        await asyncio.wait_for(coordinator.shutdown(), timeout=1)
        assert not _live_validation_threads()

    asyncio.run(scenario())


def test_expired_deadline_does_not_block_on_executor_wait() -> None:
    async def scenario() -> None:
        session = _session()
        validator = _BlockingValidator()
        coordinator = UploadValidationCoordinator(
            _Repository([(session,), ()]),
            validator,
            UploadSessionLockRegistry(),
        )
        await coordinator.start()
        await _wait_until(lambda: validator.started.is_set())
        began = time.monotonic()
        await coordinator.shutdown(ShutdownDeadline(0.0))
        elapsed = time.monotonic() - began
        validator.release.set()
        await asyncio.sleep(0.05)
        assert elapsed < 0.2
        assert coordinator.runner_done
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_unexpected_runner_death_is_observable_once_without_private_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, object]] = []

    def capture(**fields: object) -> None:
        recorded.append(fields)

    monkeypatch.setattr(
        "kronika.application.upload_validation_coordinator._safe_log",
        capture,
    )

    async def scenario() -> None:
        coordinator = UploadValidationCoordinator(
            _Repository([()]),
            _ScriptedValidator(),
            UploadSessionLockRegistry(),
        )
        await coordinator.start()
        runner = coordinator._runner
        assert runner is not None
        runner.cancel()
        try:
            await runner
        except asyncio.CancelledError:
            pass
        await coordinator.shutdown()

    asyncio.run(scenario())
    unexpected = [
        item
        for item in recorded
        if item.get("event") == "upload_validation_runner_unexpected_death"
    ]
    assert len(unexpected) == 1
    serialized = repr(unexpected)
    assert "example.mp4" not in serialized
    assert "validation-candidate" not in serialized


def test_expected_shutdown_does_not_log_runner_death_as_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: list[dict[str, object]] = []
    monkeypatch.setattr(
        "kronika.application.upload_validation_coordinator._safe_log",
        lambda **fields: recorded.append(fields),
    )

    async def scenario() -> None:
        coordinator = UploadValidationCoordinator(
            _Repository([()]),
            _ScriptedValidator(),
            UploadSessionLockRegistry(),
        )
        await coordinator.start()
        await coordinator.shutdown()

    asyncio.run(scenario())
    assert all(
        item.get("event") != "upload_validation_runner_unexpected_death"
        for item in recorded
    )
