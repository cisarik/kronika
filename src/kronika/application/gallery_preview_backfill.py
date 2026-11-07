"""In-process background backfill of persistent gallery preview derivatives.

Gallery GET requests never generate previews. Media that enters the catalog
through upload, YouTube or X acquisition therefore needs a preview generated
out of band; otherwise the Gallery shows "Preview unavailable." until an
operator runs ``kronika-previews generate``. This coordinator performs the
same bounded generation inside the server process, one item at a time, on a
worker thread, and remembers items that could not be prepared so it does not
retry them on every pass.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from kronika.application.gallery_preview import (
    GalleryPreviewFailedError,
    GalleryPreviewGenerationPlan,
    GalleryPreviewNotFoundError,
    GalleryPreviewUnavailableError,
)
from kronika.application.in_process_lifecycle import (
    ShutdownDeadline,
    shutdown_executor_backed_coordinator,
)
from kronika.structured_logging import get_logger

LOGGER = get_logger("gallery_preview_backfill")

DEFAULT_INITIAL_DELAY_SECONDS = 5.0
DEFAULT_INTERVAL_SECONDS = 60.0
DEFAULT_ITEMS_PER_PASS = 12
DEFAULT_PLAN_WINDOW = 500
DEFAULT_FAILURE_RETRY_PASSES = 60


class _PreviewService(Protocol):
    def plan_generate(
        self,
        *,
        library_id: object,
        include_all: bool,
        max_items: int,
    ) -> GalleryPreviewGenerationPlan: ...

    def generate_one(self, media_id: object, location_id: object) -> object: ...


class GalleryPreviewBackfillCoordinator:
    """Periodically generate missing or stale gallery previews."""

    def __init__(
        self,
        service: _PreviewService,
        *,
        catalog_available: Callable[[], bool],
        initial_delay_seconds: float = DEFAULT_INITIAL_DELAY_SECONDS,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
        items_per_pass: int = DEFAULT_ITEMS_PER_PASS,
        plan_window: int = DEFAULT_PLAN_WINDOW,
        failure_retry_passes: int = DEFAULT_FAILURE_RETRY_PASSES,
        executor: ThreadPoolExecutor | None = None,
    ) -> None:
        self._service = service
        self._catalog_available = catalog_available
        self._initial_delay_seconds = initial_delay_seconds
        self._interval_seconds = interval_seconds
        self._items_per_pass = items_per_pass
        self._plan_window = plan_window
        self._failure_retry_passes = failure_retry_passes
        self._executor = executor
        self._owns_executor = executor is None
        self._runner: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._stopping = False
        self._pass_number = 0
        self._failed_until_pass: dict[tuple[str, str], int] = {}

    async def start(self) -> None:
        """Start the background loop on the running event loop."""
        if self._runner is not None:
            return
        if self._executor is None:
            self._executor = ThreadPoolExecutor(
                max_workers=1,
                thread_name_prefix="kronika-gallery-preview",
            )
        self._stopping = False
        self._wake = asyncio.Event()
        self._runner = asyncio.create_task(self._run())

    def notify(self) -> None:
        """Ask for an early pass, for example after new media was cataloged."""
        if self._wake is not None and not self._stopping:
            self._wake.set()

    async def drain(self) -> int:
        """Run one pass synchronously for deterministic tests."""
        if self._executor is None:
            self._executor = ThreadPoolExecutor(max_workers=1)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(self._executor, self._run_pass)

    async def _run(self) -> None:
        delay = self._initial_delay_seconds
        while not self._stopping:
            assert self._wake is not None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=delay)
            except asyncio.TimeoutError:
                pass
            if self._stopping:
                return
            self._wake.clear()
            try:
                loop = asyncio.get_running_loop()
                await loop.run_in_executor(self._executor, self._run_pass)
            except asyncio.CancelledError:
                raise
            except Exception:
                _safe_log(
                    level="WARNING",
                    event="gallery_preview_backfill_pass_fault",
                    error_code="GALLERY_PREVIEW_BACKFILL_PASS_FAULT",
                )
            delay = self._interval_seconds

    def _run_pass(self) -> int:
        """Generate up to ``items_per_pass`` previews and return how many succeeded."""
        self._pass_number += 1
        if self._stopping or not self._catalog_available():
            return 0
        try:
            plan = self._service.plan_generate(
                library_id=None,
                include_all=True,
                max_items=self._plan_window,
            )
        except (GalleryPreviewUnavailableError, GalleryPreviewNotFoundError):
            return 0
        generated = 0
        attempted = 0
        for item in plan.to_generate:
            if self._stopping or attempted >= self._items_per_pass:
                break
            key = (str(item.media_id), str(item.location_id))
            retry_at = self._failed_until_pass.get(key)
            if retry_at is not None and self._pass_number < retry_at:
                continue
            attempted += 1
            try:
                self._service.generate_one(item.media_id, item.location_id)
            except (
                GalleryPreviewUnavailableError,
                GalleryPreviewFailedError,
                GalleryPreviewNotFoundError,
            ):
                self._failed_until_pass[key] = self._pass_number + self._failure_retry_passes
                continue
            except Exception:
                self._failed_until_pass[key] = self._pass_number + self._failure_retry_passes
                continue
            self._failed_until_pass.pop(key, None)
            generated += 1
        if attempted:
            _safe_log(
                level="INFO",
                event="gallery_preview_backfill_pass",
                error_code=None,
                generated=generated,
                attempted=attempted,
            )
        return generated

    async def shutdown(self, deadline: ShutdownDeadline | None = None) -> None:
        """Stop the loop and wait for the current item without blocking past the deadline."""
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
                log_runner_fault=lambda: _safe_log(
                    level="WARNING",
                    event="gallery_preview_backfill_runner_shutdown_fault",
                    error_code="GALLERY_PREVIEW_BACKFILL_RUNNER_SHUTDOWN_FAULT",
                ),
                log_unresolved=lambda: _safe_log(
                    level="WARNING",
                    event="gallery_preview_backfill_executor_unresolved",
                    error_code="GALLERY_PREVIEW_BACKFILL_EXECUTOR_UNRESOLVED",
                ),
            )
        finally:
            self._runner = None
            self._wake = None
            if self._owns_executor:
                self._executor = None
        if cancellation is not None:
            raise cancellation


def _safe_log(*, level: str, event: str, error_code: str | None, **fields: object) -> None:
    try:
        LOGGER.emit(
            level=level,
            event=event,
            operation="gallery_preview_backfill",
            error_code=error_code,
            retryable=True,
            context=fields or None,
        )
    except Exception:
        return
