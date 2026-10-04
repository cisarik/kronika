"""Tests for the persistent gallery preview operator CLI."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from kronika.adapters.cli import previews
from kronika.application.gallery_preview import (
    GalleryPreviewGenerationPlan,
    GalleryPreviewGenerationSummary,
    GalleryPreviewStatus,
)
from kronika.configuration import KronikaSettings
from kronika.domain import LibraryId


@dataclass
class _FakeService:
    plan: GalleryPreviewGenerationPlan | None = None
    generated: bool = False

    def status(self, *, library_id=None):
        return GalleryPreviewStatus(
            total_count=0,
            ready_count=0,
            missing_count=0,
            stale_count=0,
            unavailable_count=0,
            unsupported_count=0,
            generation_unavailable_count=0,
            libraries=(),
        )

    def plan_generate(self, *, library_id, include_all, max_items):
        selected = (library_id,) if library_id is not None else (LibraryId.new(),)
        self.plan = GalleryPreviewGenerationPlan(
            selected_library_ids=selected,
            total_considered=0,
            ready_count=0,
            to_generate=(),
            max_items=max_items,
        )
        return self.plan

    def generate(self, plan):
        self.generated = True
        return GalleryPreviewGenerationSummary(
            considered_count=plan.total_considered,
            ready_count=plan.ready_count,
            generated_count=0,
            failed_count=0,
            skipped_count=0,
        )


def _patch_service(monkeypatch, service: _FakeService) -> None:
    monkeypatch.setattr(previews, "load_settings", lambda: KronikaSettings(
        database_path=Path("/tmp/framenest-cli.sqlite3"),
        gallery_preview_cache_path=Path("/tmp/framenest-cli-previews"),
        _env_file=None,
    ))
    monkeypatch.setattr(previews, "_with_service", lambda settings, callback: callback(service))


def test_status_is_read_only_and_performs_no_generation(monkeypatch, capsys) -> None:
    service = _FakeService()
    _patch_service(monkeypatch, service)
    assert previews.main(["status"]) == 0
    assert service.generated is False
    assert "Imported physical locations considered: 0" in capsys.readouterr().out


def test_declining_interactive_generation_writes_nothing(monkeypatch, capsys) -> None:
    service = _FakeService()
    _patch_service(monkeypatch, service)
    monkeypatch.setattr("builtins.input", lambda prompt: "no")
    assert previews.main(["generate", "--all", "--max-items", "2"]) == 0
    assert service.plan is not None
    assert service.generated is False
    assert "No durable changes made." in capsys.readouterr().out


def test_generate_yes_executes_plan(monkeypatch) -> None:
    service = _FakeService()
    _patch_service(monkeypatch, service)
    assert previews.main(["generate", "--all", "--yes", "--max-items", "2"]) == 0
    assert service.generated is True


@pytest.mark.parametrize(
    "argv",
    [
        ["generate", "--yes"],
        ["generate", "--all", "--library-id", str(LibraryId.new()), "--yes"],
        ["generate", "--all", "--yes", "--max-items", "0"],
    ],
)
def test_generate_selection_and_max_item_boundaries(monkeypatch, argv) -> None:
    _patch_service(monkeypatch, _FakeService())
    assert previews.main(argv) == 2
