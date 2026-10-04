"""Coordinator idempotency and recovery tests for automatic analysis."""

from __future__ import annotations

import asyncio

from kronika.application.media_analysis_coordinator import (
    InterruptAwareMediaAnalysisRunExecutor,
    MediaAnalysisCoordinator,
)
from kronika.application.in_process_lifecycle import ShutdownDeadline
from kronika.application.media_analysis_lifecycle import (
    CatalogedAnalysisTarget,
    ExecuteAutomaticMediaAnalysisRun,
    ScheduleAutomaticMediaAnalysis,
)
from kronika.application.media_suggestion import MediaSuggestion, PROMPT_VERSION
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    MediaAnalysisRun,
    MediaAnalysisRunId,
    MediaAnalysisRunState,
)
from dataclasses import replace
import threading
import time
import types


MEDIA_ID = MediaId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LOCATION_ID = MediaLocationId.from_string("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


class _FakeRepository:
    def __init__(self) -> None:
        self.run: MediaAnalysisRun | None = None
        self._lock = threading.Lock()

    def get_by_media_definition(self, media_id, analysis_definition):
        del media_id, analysis_definition
        return self.run

    def create_pending(self, *, media_id, media_location_id, analysis_definition, created_at_ms):
        with self._lock:
            if self.run is not None:
                return self.run
            self.run = MediaAnalysisRun(
                id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
                media_id=media_id,
                media_location_id=media_location_id,
                analysis_definition=analysis_definition,
                state=MediaAnalysisRunState.PENDING,
                attempt_count=0,
                provider_id=None,
                model_id=None,
                prompt_version=None,
                result_schema_version=None,
                result_json=None,
                error_code=None,
                error_message=None,
                created_at_ms=created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                version=1,
            )
            return self.run

    def create_manual_pending(
        self, *, media_id, media_location_id, analysis_definition, created_at_ms
    ):
        with self._lock:
            if self.run is not None and self.run.state in {
                MediaAnalysisRunState.PENDING,
                MediaAnalysisRunState.ANALYZING,
            }:
                return self.run
            superseded = self.run
            self.run = MediaAnalysisRun(
                id=MediaAnalysisRunId("22222222-2222-4222-8222-222222222222"),
                media_id=media_id,
                media_location_id=media_location_id,
                analysis_definition=analysis_definition,
                state=MediaAnalysisRunState.PENDING,
                attempt_count=0,
                provider_id=None,
                model_id=None,
                prompt_version=None,
                result_schema_version=None,
                result_json=None,
                error_code=None,
                error_message=None,
                created_at_ms=created_at_ms,
                started_at_ms=None,
                completed_at_ms=None,
                version=1,
                supersedes_run_id=None if superseded is None else superseded.id,
            )
            return self.run

    def claim_pending(self, *, run_id, expected_version, started_at_ms, max_attempts):
        del run_id, max_attempts
        with self._lock:
            assert self.run is not None
            assert self.run.version == expected_version
            self.run = replace(
                self.run,
                state=MediaAnalysisRunState.ANALYZING,
                attempt_count=self.run.attempt_count + 1,
                started_at_ms=started_at_ms,
                version=self.run.version + 1,
            )
            return self.run

    def requeue_for_retry(self, **kwargs):
        raise AssertionError("unexpected requeue")

    def record_analyzed(
        self,
        *,
        run_id,
        expected_version,
        provider_id,
        model_id,
        prompt_version,
        result_schema_version,
        result_json,
        completed_at_ms,
        analysis_profile=None,
        reasoning_enabled=None,
        derivative_strategy=None,
        derivative_count=None,
        provider_submission_occurred=None,
    ):
        del run_id
        del analysis_profile, reasoning_enabled, derivative_strategy
        del derivative_count, provider_submission_occurred
        with self._lock:
            assert self.run is not None
            assert self.run.version == expected_version
            self.run = replace(
                self.run,
                state=MediaAnalysisRunState.ANALYZED,
                provider_id=provider_id,
                model_id=model_id,
                prompt_version=prompt_version,
                result_schema_version=result_schema_version,
                result_json=result_json,
                completed_at_ms=completed_at_ms,
                version=self.run.version + 1,
            )
            return self.run

    def record_failed(
        self,
        *,
        run_id,
        expected_version,
        error_code,
        error_message,
        provider_id,
        model_id,
        prompt_version,
        completed_at_ms,
        provider_submission_occurred=None,
    ):
        del run_id, provider_id, model_id, prompt_version, provider_submission_occurred
        with self._lock:
            assert self.run is not None
            assert self.run.version == expected_version
            self.run = replace(
                self.run,
                state=MediaAnalysisRunState.FAILED,
                error_code=error_code,
                error_message=error_message,
                completed_at_ms=completed_at_ms,
                version=self.run.version + 1,
            )
            return self.run

    def reset_interrupted_analyzing(
        self,
        *,
        run_id,
        expected_version,
        max_attempts,
        updated_at_ms,
    ):
        del run_id, max_attempts
        with self._lock:
            assert self.run is not None
            assert self.run.version == expected_version
            self.run = replace(
                self.run,
                state=MediaAnalysisRunState.FAILED,
                error_code="ANALYSIS_OUTCOME_UNKNOWN",
                error_message=(
                    "Automatic analysis was interrupted and the provider "
                    "outcome cannot be determined safely."
                ),
                completed_at_ms=updated_at_ms,
                version=self.run.version + 1,
            )
            return self.run

    def list_unfinished(self, *, limit, after_created_at_ms=None, after_id=None):
        del limit, after_created_at_ms, after_id
        if self.run is None or self.run.state in {
            MediaAnalysisRunState.ANALYZED,
            MediaAnalysisRunState.FAILED,
        }:
            return ()
        return (self.run,)


class _Executor:
    def __init__(self) -> None:
        self.calls = 0
        self.block = threading.Event()
        self.entered = threading.Event()

    def execute(self, media_id, location_id):
        del media_id, location_id
        self.calls += 1
        self.entered.set()
        self.block.wait(timeout=2)
        return MediaSuggestion(
            title="Title",
            description="Description text for suggestion.",
            collection="Collection",
            tags=("tag-one",),
            suggested_filename="title.mp4",
            confidence=0.8,
            evidence=("frame evidence",),
            uncertainties=(),
            provider_id="nvidia-nim",
            model_id="test-model",
            prompt_version=PROMPT_VERSION,
        )


def test_notify_cataloged_disabled_does_not_create_run() -> None:
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=False)
    service = ExecuteAutomaticMediaAnalysisRun(repository, _Executor())
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        await coordinator.drain()
        await coordinator.shutdown()

    asyncio.run(scenario())
    assert repository.run is None


def test_request_manual_works_when_automatic_scheduling_disabled() -> None:
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=False)
    executor = _Executor()
    executor.block.set()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 2)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        run = coordinator.request_manual(MEDIA_ID, LOCATION_ID)
        assert run.state is MediaAnalysisRunState.PENDING
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()

    asyncio.run(scenario())
    assert repository.run is not None
    assert repository.run.state is MediaAnalysisRunState.ANALYZED
    assert executor.calls == 1


def test_notify_cataloged_enabled_is_idempotent_and_executes_once() -> None:
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=True, now_ms=lambda: 1)
    executor = _Executor()
    executor.block.set()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 2)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()

    asyncio.run(scenario())
    assert repository.run is not None
    assert repository.run.state is MediaAnalysisRunState.ANALYZED
    assert executor.calls == 1


def test_startup_reconciles_pending_without_inventing_historical_jobs() -> None:
    repository = _FakeRepository()
    repository.run = MediaAnalysisRun(
        id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        state=MediaAnalysisRunState.PENDING,
        attempt_count=0,
        provider_id=None,
        model_id=None,
        prompt_version=None,
        result_schema_version=None,
        result_json=None,
        error_code=None,
        error_message=None,
        created_at_ms=1,
        started_at_ms=None,
        completed_at_ms=None,
        version=1,
    )
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=False)
    executor = _Executor()
    executor.block.set()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 2)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.shutdown()

    asyncio.run(scenario())
    assert repository.run.state is MediaAnalysisRunState.ANALYZED
    assert executor.calls == 1


def test_shutdown_stops_new_work() -> None:
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=True, now_ms=lambda: 1)
    executor = _Executor()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 2)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        await coordinator.shutdown()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        assert repository.run is None

    asyncio.run(scenario())


def test_schedule_target_helper_round_trip() -> None:
    target = CatalogedAnalysisTarget(media_id=MEDIA_ID, media_location_id=LOCATION_ID)
    assert target.media_id == MEDIA_ID


def test_stale_analyzing_startup_does_not_invoke_provider() -> None:
    repository = _FakeRepository()
    repository.run = MediaAnalysisRun(
        id=MediaAnalysisRunId("11111111-1111-4111-8111-111111111111"),
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        state=MediaAnalysisRunState.ANALYZING,
        attempt_count=1,
        provider_id=None,
        model_id=None,
        prompt_version=None,
        result_schema_version=None,
        result_json=None,
        error_code=None,
        error_message=None,
        created_at_ms=1,
        started_at_ms=2,
        completed_at_ms=None,
        version=2,
    )
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=True, now_ms=lambda: 3)
    executor = _Executor()
    executor.block.set()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 4)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()
        # Repeated startup reconciliation must remain fail-closed.
        await coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        await asyncio.sleep(0.05)
        await coordinator.drain()

    asyncio.run(scenario())
    assert repository.run is not None
    assert repository.run.state is MediaAnalysisRunState.FAILED
    assert repository.run.error_code == "ANALYSIS_OUTCOME_UNKNOWN"
    assert executor.calls == 0


def test_crash_window_provider_success_without_persist_does_not_replay() -> None:
    """Exact ambiguous crash window: provider succeeded, result never persisted."""
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=True, now_ms=lambda: 1)
    pending = scheduler.execute(
        CatalogedAnalysisTarget(media_id=MEDIA_ID, media_location_id=LOCATION_ID)
    )
    assert pending is not None
    call_count = {"n": 0}

    class _CountingExecutor:
        def execute(self, media_id, location_id):
            del media_id, location_id
            call_count["n"] += 1
            return MediaSuggestion(
                title="Title",
                description="Description text for suggestion.",
                collection="Collection",
                tags=("tag-one",),
                suggested_filename="title.mp4",
                confidence=0.8,
                evidence=("frame evidence",),
                uncertainties=(),
                provider_id="nvidia-nim",
                model_id="test-model",
                prompt_version=PROMPT_VERSION,
            )

    service = ExecuteAutomaticMediaAnalysisRun(
        repository,
        _CountingExecutor(),
        now_ms=lambda: 2,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=2,
        max_attempts=3,
    )
    # Provider returns success once; durable analyzed persistence is interrupted.
    suggestion = service._executor.execute(claimed.media_id, claimed.media_location_id)
    assert suggestion.title == "Title"
    assert call_count["n"] == 1
    assert repository.run is not None
    assert repository.run.state is MediaAnalysisRunState.ANALYZING

    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def recover_twice() -> None:
        await coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()
        await coordinator.start()
        await asyncio.sleep(0.05)
        await coordinator.drain()
        await coordinator.shutdown()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        await asyncio.sleep(0.05)
        await coordinator.drain()

    asyncio.run(recover_twice())
    assert call_count["n"] == 1
    assert repository.run.state is MediaAnalysisRunState.FAILED
    assert repository.run.error_code == "ANALYSIS_OUTCOME_UNKNOWN"
    assert repository.run.result_json is None


def test_expired_deadline_does_not_block_on_executor_wait() -> None:
    repository = _FakeRepository()
    scheduler = ScheduleAutomaticMediaAnalysis(repository, enabled=True, now_ms=lambda: 1)
    executor = _Executor()
    service = ExecuteAutomaticMediaAnalysisRun(repository, executor, now_ms=lambda: 2)
    coordinator = MediaAnalysisCoordinator(repository, scheduler, service)

    async def scenario() -> None:
        await coordinator.start()
        coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
        await asyncio.sleep(0.05)
        began = time.monotonic()
        await coordinator.shutdown(ShutdownDeadline(0.0))
        elapsed = time.monotonic() - began
        executor.block.set()
        await asyncio.sleep(0.05)
        assert elapsed < 0.3
        assert coordinator.runner_done

    asyncio.run(scenario())


def test_interrupt_aware_executor_leaves_analyzing_for_restart_recovery() -> None:
    repository = _FakeRepository()
    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=1,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=2,
        max_attempts=3,
    )
    service = InterruptAwareMediaAnalysisRunExecutor(
        repository,
        _Executor(),
        process_runner=types.SimpleNamespace(interrupt_requested=True),
    )
    returned = service._persist_failure(claimed, RuntimeError("interrupted"))
    assert returned.state is MediaAnalysisRunState.ANALYZING
    assert repository.run is not None
    assert repository.run.state is MediaAnalysisRunState.ANALYZING
    assert repository.run.error_code is None
