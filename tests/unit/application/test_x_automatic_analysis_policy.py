"""Unit tests for administrator-owned X automatic generic analysis policy."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

from kronika.application.ports.x_acquisition import FrameNestXClaimRepositoryError
from kronika.application.x_acquisition import automatic_analysis_allowed_for_upload
from kronika.application.youtube_acquisition import (
    automatic_analysis_allowed_for_upload as youtube_automatic_analysis_allowed_for_upload,
)
from kronika.domain.identity_access import build_identity_mapping
from kronika.domain.uploads import UploadSessionId

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_MODULE = (
    REPOSITORY_ROOT / "src" / "kronika" / "adapters" / "api" / "application.py"
)
IDENTITY_MAPPING = build_identity_mapping(
    {
        "admin@example.com": "admin",
        "user@example.com": "user",
    }
)


@dataclass
class _FakeXRepository:
    asset: object | None = None
    claim: object | None = None
    error: Exception | None = None

    def find_asset_by_upload_id(self, upload_id: UploadSessionId) -> object | None:
        del upload_id
        if self.error is not None:
            raise self.error
        return self.asset

    def find_post_by_upload_id(self, upload_id: UploadSessionId) -> object | None:
        del upload_id
        if self.error is not None:
            raise self.error
        return self.claim


class _FakeYouTubeRepository:
    def __init__(self, claim: object | None) -> None:
        self._claim = claim

    def find_by_upload_id(self, upload_id: UploadSessionId) -> object | None:
        del upload_id
        return self._claim


def _upload_id() -> UploadSessionId:
    return UploadSessionId.new()


def test_helper_does_not_take_or_read_scheduler_flag() -> None:
    signature = inspect.signature(automatic_analysis_allowed_for_upload)
    assert list(signature.parameters) == [
        "repository",
        "upload_id",
        "identity_mapping",
    ]
    source = inspect.getsource(automatic_analysis_allowed_for_upload)
    assert "getenv" not in source
    assert "environ" not in source
    assert "enabled" not in signature.parameters
    # Scheduler `ScheduleAutomaticMediaAnalysis.enabled` remains the enqueue
    # gate; `test_schedule_disabled_creates_no_run` proves flag-off creates no run.


def test_administrator_mapped_x_upload_is_allowed() -> None:
    repository = _FakeXRepository(
        asset=object(),
        claim=SimpleNamespace(created_by_login_key="Admin@example.com"),
    )
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is True
    )


def test_ordinary_mapped_x_upload_is_denied() -> None:
    repository = _FakeXRepository(
        asset=object(),
        claim=SimpleNamespace(created_by_login_key="user@example.com"),
    )
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is False
    )


def test_null_owner_x_upload_is_denied() -> None:
    repository = _FakeXRepository(
        asset=object(),
        claim=SimpleNamespace(created_by_login_key=None),
    )
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is False
    )


def test_unmapped_login_x_upload_is_denied() -> None:
    repository = _FakeXRepository(
        asset=object(),
        claim=SimpleNamespace(created_by_login_key="other@example.com"),
    )
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is False
    )


def test_empty_identity_mapping_denies_linked_x_upload() -> None:
    repository = _FakeXRepository(
        asset=object(),
        claim=SimpleNamespace(created_by_login_key="admin@example.com"),
    )
    assert automatic_analysis_allowed_for_upload(repository, _upload_id(), {}) is False


def test_missing_x_post_for_linked_asset_is_denied() -> None:
    repository = _FakeXRepository(asset=object(), claim=None)
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is False
    )


def test_no_x_asset_is_allowed() -> None:
    repository = _FakeXRepository(asset=None, claim=None)
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is True
    )


def test_repository_error_fails_closed() -> None:
    repository = _FakeXRepository(
        error=FrameNestXClaimRepositoryError("X claim storage is unavailable.")
    )
    assert (
        automatic_analysis_allowed_for_upload(
            repository, _upload_id(), IDENTITY_MAPPING
        )
        is False
    )


def test_youtube_helper_denies_linked_claim() -> None:
    upload_id = _upload_id()
    assert (
        youtube_automatic_analysis_allowed_for_upload(
            _FakeYouTubeRepository(object()), upload_id
        )
        is False
    )
    assert (
        youtube_automatic_analysis_allowed_for_upload(
            _FakeYouTubeRepository(None), upload_id
        )
        is True
    )


def test_identity_mapping_is_built_before_catalog_coordinator() -> None:
    source = APPLICATION_MODULE.read_text(encoding="utf-8")
    mapping_at = source.index("identity_mapping = build_identity_mapping(")
    coordinator_at = source.index("UploadCatalogCoordinator(")
    assert mapping_at < coordinator_at
    combined_start = source.index("def _combined_analysis_allowed")
    combined = source[combined_start : source.index("owned_upload_catalog =", combined_start)]
    assert "x_automatic_analysis_allowed_for_upload(" in combined
    assert "identity_mapping" in combined
