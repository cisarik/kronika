"""Unit tests for the in-process gallery preview backfill coordinator."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from kronika.application.gallery_preview import (
    GalleryPreviewGenerationPlan,
    GalleryPreviewUnavailableError,
)
from kronika.application.gallery_preview_backfill import (
    DEFAULT_FAILURE_RETRY_PASSES,
    DEFAULT_INTERVAL_SECONDS,
    DEFAULT_ITEMS_PER_PASS,
    GalleryPreviewBackfillCoordinator,
)


@dataclass(frozen=True)
class _Item:
    media_id: str
    location_id: str


@dataclass
class _FakeService:
    missing: list[str]
    broken: set[str] = field(default_factory=set)
    generated: list[str] = field(default_factory=list)
    attempts: list[str] = field(default_factory=list)

    def plan_generate(self, *, library_id, include_all, max_items):
        assert library_id is None
        assert include_all is True
        items = tuple(_Item(media_id=m, location_id=f"loc-{m}") for m in self.missing)[:max_items]
        return GalleryPreviewGenerationPlan(
            selected_library_ids=(),
            total_considered=len(items),
            ready_count=0,
            to_generate=items,
            max_items=max_items,
        )

    def generate_one(self, media_id, location_id):
        self.attempts.append(media_id)
        if media_id in self.broken:
            raise GalleryPreviewUnavailableError("unavailable")
        self.generated.append(media_id)
        self.missing.remove(media_id)
        return object()


def _run(coro):
    return asyncio.run(coro)


def test_default_bounds_are_twelve_items_sixty_seconds_and_sixty_pass_skip() -> None:
    assert DEFAULT_ITEMS_PER_PASS == 12
    assert DEFAULT_INTERVAL_SECONDS == 60.0
    assert DEFAULT_FAILURE_RETRY_PASSES == 60


def test_pass_generates_missing_previews_up_to_the_per_pass_bound() -> None:
    service = _FakeService(missing=["a", "b", "c"])
    coordinator = GalleryPreviewBackfillCoordinator(
        service, catalog_available=lambda: True, items_per_pass=2
    )
    assert _run(coordinator.drain()) == 2
    assert service.generated == ["a", "b"]
    assert _run(coordinator.drain()) == 1
    assert service.generated == ["a", "b", "c"]


def test_failed_items_are_skipped_on_following_passes() -> None:
    service = _FakeService(missing=["bad", "good"], broken={"bad"})
    coordinator = GalleryPreviewBackfillCoordinator(
        service, catalog_available=lambda: True, items_per_pass=1, failure_retry_passes=3
    )
    assert _run(coordinator.drain()) == 0
    assert _run(coordinator.drain()) == 1
    assert service.attempts == ["bad", "good"]
    _run(coordinator.drain())
    assert service.attempts == ["bad", "good"]
    _run(coordinator.drain())
    assert service.attempts == ["bad", "good", "bad"]


def test_unavailable_catalog_does_no_work() -> None:
    service = _FakeService(missing=["a"])
    coordinator = GalleryPreviewBackfillCoordinator(service, catalog_available=lambda: False)
    assert _run(coordinator.drain()) == 0
    assert service.attempts == []


def test_start_and_shutdown_run_a_pass_in_the_background() -> None:
    service = _FakeService(missing=["a"])

    async def scenario() -> None:
        coordinator = GalleryPreviewBackfillCoordinator(
            service,
            catalog_available=lambda: True,
            initial_delay_seconds=0.01,
            interval_seconds=60,
        )
        await coordinator.start()
        for _ in range(100):
            if service.generated:
                break
            await asyncio.sleep(0.01)
        await coordinator.shutdown()

    _run(scenario())
    assert service.generated == ["a"]
