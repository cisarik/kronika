"""Bounded in-process application lifecycle helpers.

External systemd TimeoutStopSec is 30 seconds. Uvicorn applies
timeout_graceful_shutdown=5 seconds before application lifespan shutdown.
The application owns one 20-second monotonic shutdown budget, leaving at
least 5 seconds of external reserve. Production values are code-owned
constants, not settings or environment fields.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import time
from typing import Protocol

from kronika.structured_logging import get_logger

APPLICATION_LIFESPAN_SHUTDOWN_BUDGET_SECONDS = 20.0
UVICORN_TIMEOUT_GRACEFUL_SHUTDOWN_SECONDS = 5
SYSTEMD_TIMEOUT_STOP_SECONDS = 30
MINIMUM_EXTERNAL_RESERVE_SECONDS = 5
DEFAULT_RUNNER_ITERATION_LOG_INTERVAL_SECONDS = 1.0

LOGGER = get_logger("in_process_lifecycle")


class LifecycleResource(Protocol):
    """Smallest start/shutdown surface used by composition-root cleanup."""

    async def start(self) -> None:
        """Begin lifecycle-owned work."""

    async def shutdown(self, deadline: ShutdownDeadline | None = None) -> None:
        """Stop claiming work and settle owned resources within the deadline."""


class ShutdownDeadline:
    """One monotonic absolute shutdown deadline shared by every cleanup step."""

    def __init__(
        self,
        budget_seconds: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        started_at: float | None = None,
    ) -> None:
        if (
            isinstance(budget_seconds, bool)
            or not isinstance(budget_seconds, (int, float))
            or budget_seconds < 0
            or budget_seconds != budget_seconds
        ):
            raise ValueError("shutdown budget must be a non-negative finite number")
        self._clock = clock
        origin = clock() if started_at is None else started_at
        self._deadline_at = origin + float(budget_seconds)
        self._budget_seconds = float(budget_seconds)

    @property
    def budget_seconds(self) -> float:
        return self._budget_seconds

    def remaining_seconds(self) -> float:
        remaining = self._deadline_at - self._clock()
        if remaining < 0:
            return 0.0
        return remaining

    def expired(self) -> bool:
        return self.remaining_seconds() <= 0


def create_application_shutdown_deadline(
    *,
    budget_seconds: float = APPLICATION_LIFESPAN_SHUTDOWN_BUDGET_SECONDS,
    clock: Callable[[], float] | None = None,
) -> ShutdownDeadline:
    """Create the single application lifespan shutdown deadline."""
    return ShutdownDeadline(
        budget_seconds,
        clock=time.monotonic if clock is None else clock,
    )


def split_termination_budget(remaining_seconds: float) -> tuple[float, float]:
    """Split remaining process-group budget into TERM then KILL slices."""
    if remaining_seconds <= 0:
        return 0.0, 0.0
    term_seconds = remaining_seconds * 0.6
    return term_seconds, remaining_seconds - term_seconds


async def wait_for_deadline(
    awaitable: Awaitable[object],
    deadline: ShutdownDeadline | None,
) -> object:
    """Await work using the shared remaining budget, never a fresh timeout."""
    if deadline is None:
        return await awaitable
    remaining = deadline.remaining_seconds()
    if remaining <= 0:
        if isinstance(awaitable, asyncio.Future) and awaitable.done():
            return await awaitable
        raise TimeoutError("shutdown deadline expired")
    return await asyncio.wait_for(awaitable, timeout=remaining)


def settle_owned_executor(
    executor: ThreadPoolExecutor | None,
    *,
    owns_executor: bool,
    deadline: ShutdownDeadline | None,
    work_settled: bool,
    log_unresolved: Callable[[], None] | None = None,
) -> None:
    """Shut down an owned executor without claiming false process-exit safety.

    wait=True is used only when work is already known to have settled and the
    shared deadline still has remaining time. wait=False with cancel_futures=True
    is bounded cleanup of pending work after the deadline is exhausted.
    """
    if executor is None or not owns_executor:
        return
    expired = deadline is not None and deadline.expired()
    if expired or not work_settled:
        if not work_settled and log_unresolved is not None:
            log_unresolved()
        executor.shutdown(wait=False, cancel_futures=True)
        return
    executor.shutdown(wait=True, cancel_futures=False)


def attach_unexpected_runner_observer(
    task: asyncio.Task[object],
    *,
    is_expected: Callable[[], bool],
    log_unexpected: Callable[[], None],
) -> None:
    """Emit one sanitized signal if a runner ends outside expected shutdown."""

    def _on_done(done_task: asyncio.Task[object]) -> None:
        if is_expected():
            return
        log_unexpected()
        del done_task

    task.add_done_callback(_on_done)


class IterationFailureLimiter:
    """Rate-limit repeated runner-iteration failure visibility."""

    def __init__(
        self,
        *,
        interval_seconds: float = DEFAULT_RUNNER_ITERATION_LOG_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if interval_seconds < 0:
            raise ValueError("iteration log interval must be non-negative")
        self._interval_seconds = float(interval_seconds)
        self._clock = clock
        self._last_emitted_at: float | None = None

    def allow(self) -> bool:
        now = self._clock()
        last = self._last_emitted_at
        if last is None or now - last >= self._interval_seconds:
            self._last_emitted_at = now
            return True
        return False


@dataclass(slots=True)
class StartedResource:
    """One successfully started lifespan resource."""

    name: str
    shutdown: Callable[..., Awaitable[object]]


async def shutdown_started_resources(
    resources: Sequence[StartedResource],
    deadline: ShutdownDeadline,
    *,
    log_fault: Callable[..., None] | None = None,
) -> asyncio.CancelledError | None:
    """Shut down started resources in reverse order, continuing after one fault."""
    cancelled: asyncio.CancelledError | None = None
    emit = log_fault if log_fault is not None else _default_shutdown_fault_log
    for resource in reversed(resources):
        try:
            try:
                await resource.shutdown(deadline)
            except TypeError:
                await resource.shutdown()
        except asyncio.CancelledError as exc:
            cancelled = exc
        except Exception:
            emit(resource_name=resource.name)
    return cancelled


def _default_shutdown_fault_log(*, resource_name: str) -> None:
    del resource_name
    try:
        LOGGER.emit(
            level="WARNING",
            event="lifecycle_resource_shutdown_fault",
            operation="lifecycle_shutdown",
            error_code="LIFECYCLE_RESOURCE_SHUTDOWN_FAULT",
            retryable=False,
        )
    except Exception:
        return


async def shutdown_executor_backed_coordinator(
    *,
    runner: asyncio.Task[object] | None,
    executor: ThreadPoolExecutor | None,
    owns_executor: bool,
    deadline: ShutdownDeadline | None,
    interrupt_owned_work: Callable[[], None] | None = None,
    log_runner_fault: Callable[[], None],
    log_unresolved: Callable[[], None],
) -> asyncio.CancelledError | None:
    """Stop waiting for an executor-backed runner without blocking past the deadline."""
    if interrupt_owned_work is not None:
        try:
            interrupt_owned_work()
        except Exception:
            pass
    work_settled = runner is None or runner.done()
    try:
        if runner is not None and not runner.done():
            try:
                await wait_for_deadline(runner, deadline)
                work_settled = True
            except TimeoutError:
                work_settled = runner.done()
            except Exception:
                log_runner_fault()
                work_settled = runner.done()
    finally:
        if runner is not None and runner.done():
            work_settled = True
        settle_owned_executor(
            executor,
            owns_executor=owns_executor,
            deadline=deadline,
            work_settled=work_settled,
            log_unresolved=None if work_settled else log_unresolved,
        )
    return None
