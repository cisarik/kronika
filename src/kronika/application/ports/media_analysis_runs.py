"""Persistence port for durable media analysis runs."""

from __future__ import annotations

from typing import Protocol

from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media_analysis_runs import MediaAnalysisRun


class FrameNestMediaAnalysisRunRepositoryError(RuntimeError):
    """Sanitized persistence failure for analysis runs."""


class MediaAnalysisRunConflictError(FrameNestMediaAnalysisRunRepositoryError):
    """Raised when an analysis-run write races or violates uniqueness."""


class MediaAnalysisRunNotFoundError(FrameNestMediaAnalysisRunRepositoryError):
    """Raised when the targeted analysis run is absent."""


class MediaAnalysisRunRepository(Protocol):
    """Short-transaction repository for analysis lifecycle rows."""

    def get_by_media_definition(
        self,
        media_id: MediaId,
        analysis_definition: str,
    ) -> MediaAnalysisRun | None:
        """Return the latest run for media and definition when present."""

    def create_pending(
        self,
        *,
        media_id: MediaId,
        media_location_id: MediaLocationId,
        analysis_definition: str,
        created_at_ms: int,
    ) -> MediaAnalysisRun:
        """Idempotently create the first run or return any existing matching run.

        Automatic/catalog replay must not invent a new attempt after a terminal
        historical run. Duplicate requests while active return the active run.
        """

    def create_manual_pending(
        self,
        *,
        media_id: MediaId,
        media_location_id: MediaLocationId,
        analysis_definition: str,
        created_at_ms: int,
    ) -> MediaAnalysisRun:
        """Create a new pending run after a terminal history, or return active.

        Explicit manual reanalysis preserves every terminal run unchanged and
        records durable ``supersedes_run_id`` lineage to the prior latest run.
        """

    def claim_pending(
        self,
        *,
        run_id: str,
        expected_version: int,
        started_at_ms: int,
        max_attempts: int,
    ) -> MediaAnalysisRun:
        """Claim one pending run for execution outside the provider call."""

    def requeue_for_retry(
        self,
        *,
        run_id: str,
        expected_version: int,
        error_code: str,
        error_message: str,
        updated_at_ms: int,
    ) -> MediaAnalysisRun:
        """Return an analyzing run to pending after a retryable failure."""

    def record_analyzed(
        self,
        *,
        run_id: str,
        expected_version: int,
        provider_id: str,
        model_id: str,
        prompt_version: str,
        result_schema_version: str,
        result_json: str,
        completed_at_ms: int,
        analysis_profile: str | None = None,
        reasoning_enabled: bool | None = None,
        derivative_strategy: str | None = None,
        derivative_count: int | None = None,
        provider_submission_occurred: bool | None = True,
    ) -> MediaAnalysisRun:
        """Persist a successful normalized result as analyzed."""

    def record_failed(
        self,
        *,
        run_id: str,
        expected_version: int,
        error_code: str,
        error_message: str,
        provider_id: str | None,
        model_id: str | None,
        prompt_version: str | None,
        completed_at_ms: int,
        provider_submission_occurred: bool | None = None,
        analysis_profile: str | None = None,
        reasoning_enabled: bool | None = None,
        derivative_strategy: str | None = None,
        derivative_count: int | None = None,
    ) -> MediaAnalysisRun:
        """Persist a sanitized terminal failure."""

    def list_unfinished(
        self,
        *,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[MediaAnalysisRun, ...]:
        """List durable pending or analyzing runs for reconciliation."""

    def reset_interrupted_analyzing(
        self,
        *,
        run_id: str,
        expected_version: int,
        max_attempts: int,
        updated_at_ms: int,
    ) -> MediaAnalysisRun:
        """Fail-closed reconcile for ambiguous interrupted analyzing rows."""
