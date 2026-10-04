"""Unit tests for the administrator runtime-settings JSON sidecar."""

from __future__ import annotations

import json
import os
from pathlib import Path
from stat import S_IMODE

import pytest

from kronika.application.media_analysis_coordinator import MediaAnalysisCoordinator
from kronika.application.media_analysis_lifecycle import (
    CatalogedAnalysisTarget,
    ScheduleAutomaticMediaAnalysis,
)
from kronika.configuration import (
    RUNTIME_SETTINGS_FILENAME,
    KronikaSettings,
    resolved_runtime_settings_path,
)
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.infrastructure.runtime_settings import (
    RuntimeSettingsError,
    RuntimeSettingsStore,
)

MEDIA_ID = MediaId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LOCATION_ID = MediaLocationId.from_string("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")


def test_candidate_source_provenance() -> None:
    import kronika

    expected = (
        Path(__file__).resolve().parents[2] / "src" / "kronika" / "__init__.py"
    )
    assert Path(kronika.__file__).resolve() == expected


class _CountingRepository:
    def __init__(self) -> None:
        self.calls = 0

    def create_pending(self, **kwargs: object) -> object:
        del kwargs
        self.calls += 1
        return {"queued": self.calls}


def test_missing_file_uses_settings_fallback(tmp_path: Path) -> None:
    store = RuntimeSettingsStore(tmp_path / "runtime-settings.json", fallback_enabled=True)
    assert store.is_enabled() is True
    off = RuntimeSettingsStore(tmp_path / "runtime-settings.json", fallback_enabled=False)
    assert off.is_enabled() is False
    assert not (tmp_path / "runtime-settings.json").exists()


def test_atomic_write_persists_bool_and_mode(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    store = RuntimeSettingsStore(path, fallback_enabled=False, now_ms=lambda: 42)
    assert store.set_enabled(True) is True
    assert store.is_enabled() is True
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload == {
        "automatic_media_analysis_enabled": True,
        "schema_version": 1,
        "updated_at_ms": 42,
    }
    assert S_IMODE(path.stat().st_mode) == 0o600


def test_json_overlay_precedes_fallback(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    store = RuntimeSettingsStore(path, fallback_enabled=False, now_ms=lambda: 1)
    store.set_enabled(True)
    still_off_fallback = RuntimeSettingsStore(path, fallback_enabled=False)
    assert still_off_fallback.is_enabled() is True
    store.set_enabled(False)
    env_true_fallback = RuntimeSettingsStore(path, fallback_enabled=True)
    assert env_true_fallback.is_enabled() is False


def test_malformed_json_fails_closed_to_fallback(tmp_path: Path) -> None:
    path = tmp_path / "runtime-settings.json"
    path.write_text("{not-json", encoding="utf-8")
    store = RuntimeSettingsStore(path, fallback_enabled=True)
    assert store.is_enabled() is True
    path.write_text('{"schema_version":1,"automatic_media_analysis_enabled":"yes"}\n')
    assert store.is_enabled() is True
    path.write_text('{"schema_version":2,"automatic_media_analysis_enabled":true}\n')
    assert RuntimeSettingsStore(path, fallback_enabled=False).is_enabled() is False


def test_symlink_write_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "target.json"
    target.write_text("{}\n", encoding="utf-8")
    symlink = tmp_path / "runtime-settings.json"
    symlink.symlink_to(target)
    store = RuntimeSettingsStore(symlink, fallback_enabled=False)
    with pytest.raises(RuntimeSettingsError, match="symlink"):
        store.set_enabled(True)
    assert store.is_enabled() is False
    assert json.loads(target.read_text(encoding="utf-8")) == {}


def test_resolved_path_defaults_beside_catalog_and_honors_override(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "state" / "catalog.sqlite3"
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbs",
        _env_file=None,
    )
    assert resolved_runtime_settings_path(settings) == (
        database_path.parent / RUNTIME_SETTINGS_FILENAME
    )
    override = tmp_path / "override" / "runtime-settings.json"
    overridden = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbs",
        runtime_settings_path=override,
        _env_file=None,
    )
    assert resolved_runtime_settings_path(overridden) == override
    store = RuntimeSettingsStore.from_settings(overridden, now_ms=lambda: 9)
    store.set_enabled(True)
    assert override.is_file()
    assert S_IMODE(override.stat().st_mode) == 0o600


def test_scheduler_callable_is_evaluated_per_execute() -> None:
    repository = _CountingRepository()
    enabled = {"value": False}
    scheduler = ScheduleAutomaticMediaAnalysis(
        repository,  # type: ignore[arg-type]
        enabled=lambda: enabled["value"],
        now_ms=lambda: 1,
    )
    target = CatalogedAnalysisTarget(media_id=MEDIA_ID, media_location_id=LOCATION_ID)
    assert scheduler.enabled is False
    assert scheduler.execute(target) is None
    assert repository.calls == 0
    enabled["value"] = True
    assert scheduler.enabled is True
    assert scheduler.execute(target) is not None
    assert repository.calls == 1


def test_notify_cataloged_follows_callable_flag() -> None:
    repository = _CountingRepository()
    enabled = {"value": False}
    scheduler = ScheduleAutomaticMediaAnalysis(
        repository,  # type: ignore[arg-type]
        enabled=lambda: enabled["value"],
        now_ms=lambda: 1,
    )
    coordinator = MediaAnalysisCoordinator(
        repository,  # type: ignore[arg-type]
        scheduler,
        object(),  # type: ignore[arg-type]
    )
    coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
    assert repository.calls == 0
    enabled["value"] = True
    coordinator.notify_cataloged(MEDIA_ID, LOCATION_ID)
    assert repository.calls == 1


def test_from_settings_uses_env_named_override(tmp_path: Path) -> None:
    override = tmp_path / "named.json"
    previous = os.environ.get("FRAMENEST_RUNTIME_SETTINGS_PATH")
    os.environ["FRAMENEST_RUNTIME_SETTINGS_PATH"] = str(override)
    try:
        settings = KronikaSettings(
            database_path=tmp_path / "catalog.sqlite3",
            gallery_preview_cache_path=tmp_path / "previews",
            cover_storage_root=tmp_path / "covers",
            cover_thumbnail_cache_path=tmp_path / "thumbs",
            automatic_media_analysis_enabled=False,
            _env_file=None,
        )
        store = RuntimeSettingsStore.from_settings(settings, now_ms=lambda: 3)
        store.set_enabled(True)
        assert override.is_file()
        assert json.loads(override.read_text(encoding="utf-8"))[
            "automatic_media_analysis_enabled"
        ] is True
    finally:
        if previous is None:
            del os.environ["FRAMENEST_RUNTIME_SETTINGS_PATH"]
        else:
            os.environ["FRAMENEST_RUNTIME_SETTINGS_PATH"] = previous
