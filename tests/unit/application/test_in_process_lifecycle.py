"""Deterministic tests for the bounded in-process lifecycle helper."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import time

import pytest

from kronika.application.in_process_lifecycle import (
    APPLICATION_LIFESPAN_SHUTDOWN_BUDGET_SECONDS,
    MINIMUM_EXTERNAL_RESERVE_SECONDS,
    SYSTEMD_TIMEOUT_STOP_SECONDS,
    StartedResource,
    ShutdownDeadline,
    UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS,
    attach_unexpected_runner_observer,
    create_application_shutdown_deadline,
    settle_owned_executor,
    shutdown_started_resources,
    split_termination_budget,
    wait_for_deadline,
)


class _Clock:
    def __init__(self, value: float = 100.0) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_production_budget_allocates_uvicorn_application_and_reserve() -> None:
    assert UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS == 5
    assert APPLICATION_LIFESPAN_SHUTDOWN_BUDGET_SECONDS == 20.0
    assert MINIMUM_EXTERNAL_RESERVE_SECONDS == 5
    assert SYSTEMD_TIMEOUT_STOP_SECONDS == 30
    assert (
        UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS
        + APPLICATION_LIFESPAN_SHUTDOWN_BUDGET_SECONDS
        + MINIMUM_EXTERNAL_RESERVE_SECONDS
        == SYSTEMD_TIMEOUT_STOP_SECONDS
    )


def test_one_monotonic_deadline_is_shared_across_shutdown_steps() -> None:
    clock = _Clock(10.0)
    deadline = ShutdownDeadline(0.020, clock=clock, started_at=10.0)
    seen: list[float] = []

    async def step(_deadline: ShutdownDeadline) -> None:
        seen.append(_deadline.remaining_seconds())
        clock.value += 0.004

    async def scenario() -> None:
        resources = [StartedResource(f"step-{index}", step) for index in range(6)]
        await shutdown_started_resources(resources, deadline)

    asyncio.run(scenario())
    assert seen == pytest.approx([0.020, 0.016, 0.012, 0.008, 0.004, 0.0])
    assert deadline.expired()


def test_six_sequential_shutdowns_do_not_each_receive_a_fresh_timeout() -> None:
    clock = _Clock()
    deadline = create_application_shutdown_deadline(budget_seconds=0.020, clock=clock)
    budgets: list[float] = []

    async def step(received: ShutdownDeadline) -> None:
        budgets.append(received.remaining_seconds())
        clock.value += 0.003
        assert received is deadline

    async def scenario() -> None:
        await shutdown_started_resources(
            [StartedResource(str(index), step) for index in range(6)],
            deadline,
        )

    asyncio.run(scenario())
    assert budgets[0] == pytest.approx(0.020)
    assert max(budgets) == pytest.approx(0.020)
    assert all(item <= budgets[0] + 1e-9 for item in budgets)
    assert min(budgets) < 0.010


def test_reverse_shutdown_order_is_preserved() -> None:
    order: list[str] = []

    async def named(name: str, _deadline: ShutdownDeadline) -> None:
        order.append(name)

    async def scenario() -> None:
        resources = [
            StartedResource("media_analysis", lambda deadline: named("media_analysis", deadline)),
            StartedResource("upload_catalog", lambda deadline: named("upload_catalog", deadline)),
            StartedResource(
                "upload_publication",
                lambda deadline: named("upload_publication", deadline),
            ),
            StartedResource(
                "upload_validation",
                lambda deadline: named("upload_validation", deadline),
            ),
            StartedResource(
                "youtube_acquisition",
                lambda deadline: named("youtube_acquisition", deadline),
            ),
            StartedResource("x_acquisition", lambda deadline: named("x_acquisition", deadline)),
        ]
        await shutdown_started_resources(
            resources,
            create_application_shutdown_deadline(budget_seconds=0.05),
        )

    asyncio.run(scenario())
    assert order == [
        "x_acquisition",
        "youtube_acquisition",
        "upload_validation",
        "upload_publication",
        "upload_catalog",
        "media_analysis",
    ]


def test_partial_startup_cleans_only_resources_that_started() -> None:
    class _Resource:
        def __init__(self, name: str, *, fail: bool = False) -> None:
            self.name = name
            self.fail = fail
            self.started = False
            self.shutdowns = 0

        async def start(self) -> None:
            if self.fail:
                raise RuntimeError("startup failed")
            self.started = True

        async def shutdown(self, deadline: ShutdownDeadline | None = None) -> None:
            del deadline
            self.shutdowns += 1

    analysis = _Resource("media_analysis")
    catalog = _Resource("upload_catalog")
    publication = _Resource("upload_publication", fail=True)
    validation = _Resource("upload_validation")
    started: list[StartedResource] = []

    async def scenario() -> None:
        try:
            for resource in (analysis, catalog, publication, validation):
                await resource.start()
                started.append(StartedResource(resource.name, resource.shutdown))
        except RuntimeError:
            pass
        await shutdown_started_resources(
            started,
            create_application_shutdown_deadline(budget_seconds=0.05),
        )

    asyncio.run(scenario())
    assert analysis.shutdowns == 1
    assert catalog.shutdowns == 1
    assert publication.shutdowns == 0
    assert validation.shutdowns == 0
    assert [resource.name for resource in started] == [
        "media_analysis",
        "upload_catalog",
    ]


def test_one_shutdown_exception_does_not_prevent_later_cleanup() -> None:
    order: list[str] = []
    faults: list[str] = []

    async def ok(name: str, _deadline: ShutdownDeadline) -> None:
        order.append(name)

    async def boom(_deadline: ShutdownDeadline) -> None:
        order.append("fault")
        raise RuntimeError("coordinator shutdown failed")

    async def scenario() -> None:
        await shutdown_started_resources(
            [
                StartedResource("first", lambda deadline: ok("first", deadline)),
                StartedResource("faulty", boom),
                StartedResource("last", lambda deadline: ok("last", deadline)),
            ],
            create_application_shutdown_deadline(budget_seconds=0.05),
            log_fault=lambda **fields: faults.append(str(fields.get("resource_name"))),
        )

    asyncio.run(scenario())
    assert order == ["last", "fault", "first"]
    assert faults == ["faulty"]


def test_engine_disposal_remains_in_the_final_cleanup_path() -> None:
    events: list[str] = []

    async def coordinator(_deadline: ShutdownDeadline) -> None:
        events.append("coordinator")

    async def scenario() -> None:
        await shutdown_started_resources(
            [StartedResource("coordinator", coordinator)],
            create_application_shutdown_deadline(budget_seconds=0.05),
        )
        events.append("engine")

    asyncio.run(scenario())
    assert events == ["coordinator", "engine"]


def test_systemd_timeout_stop_remains_thirty_seconds() -> None:
    service_path = Path(__file__).resolve().parents[3] / "deploy" / "systemd" / "framenest.service"
    text = service_path.read_text(encoding="utf-8")
    assert "TimeoutStopSec=30s" in text
    assert "KillSignal=SIGTERM" in text


def test_expired_deadline_does_not_block_on_executor_wait() -> None:
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lifecycle-expire")

    def blocked() -> None:
        started.set()
        release.wait(timeout=5)

    future = executor.submit(blocked)
    assert started.wait(timeout=1)
    deadline = ShutdownDeadline(0.0)
    began = time.monotonic()
    settle_owned_executor(
        executor,
        owns_executor=True,
        deadline=deadline,
        work_settled=False,
        log_unresolved=lambda: None,
    )
    elapsed = time.monotonic() - began
    release.set()
    future.result(timeout=2)
    assert elapsed < 0.2
    assert deadline.expired()


def test_wait_for_deadline_uses_remaining_budget_not_a_fresh_timeout() -> None:
    clock = _Clock(1.0)
    deadline = ShutdownDeadline(0.010, clock=clock, started_at=1.0)
    clock.value = 1.008

    async def scenario() -> None:
        with pytest.raises(TimeoutError):
            await wait_for_deadline(asyncio.sleep(1), deadline)

    started = time.monotonic()
    asyncio.run(scenario())
    assert time.monotonic() - started < 0.2


def test_split_termination_budget_stays_within_remaining_time() -> None:
    term_seconds, kill_seconds = split_termination_budget(0.05)
    assert term_seconds + kill_seconds == pytest.approx(0.05)
    assert term_seconds > kill_seconds
    assert split_termination_budget(0.0) == (0.0, 0.0)


def test_unexpected_runner_death_is_logged_once_without_private_payload() -> None:
    events: list[dict[str, object]] = []

    def log_unexpected() -> None:
        events.append(
            {
                "event": "runner_unexpected_death",
                "error_code": "RUNNER_UNEXPECTED_DEATH",
            }
        )

    async def boom() -> None:
        raise RuntimeError("private path /srv/media/secret.mp4 token=abcd")

    async def scenario() -> None:
        task = asyncio.create_task(boom())
        attach_unexpected_runner_observer(
            task,
            is_expected=lambda: False,
            log_unexpected=log_unexpected,
        )
        with pytest.raises(RuntimeError):
            await task

    asyncio.run(scenario())
    assert events == [
        {"event": "runner_unexpected_death", "error_code": "RUNNER_UNEXPECTED_DEATH"}
    ]
    serialized = repr(events)
    assert "/srv/media" not in serialized
    assert "secret.mp4" not in serialized
    assert "token=abcd" not in serialized


def test_expected_shutdown_cancellation_is_not_logged_as_error() -> None:
    events: list[str] = []
    stopping = False

    async def idle() -> None:
        await asyncio.Event().wait()

    async def scenario() -> None:
        nonlocal stopping
        task = asyncio.create_task(idle())
        attach_unexpected_runner_observer(
            task,
            is_expected=lambda: stopping,
            log_unexpected=lambda: events.append("unexpected"),
        )
        stopping = True
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(scenario())
    assert events == []
