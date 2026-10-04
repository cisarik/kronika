"""SQLite adapter for durable media analysis runs."""

from __future__ import annotations

from collections.abc import Mapping
import uuid

from sqlalchemy import insert, or_, select, update
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from kronika.application.ports.media_analysis_runs import (
    FrameNestMediaAnalysisRunRepositoryError,
    MediaAnalysisRunConflictError,
    MediaAnalysisRunNotFoundError,
)
from kronika.domain.identities import FrameNestIdentityError, MediaId, MediaLocationId
from kronika.domain.media_analysis_runs import (
    ACTIVE_ANALYSIS_RUN_STATE_VALUES,
    MediaAnalysisRun,
    MediaAnalysisRunId,
    MediaAnalysisRunState,
    TERMINAL_ANALYSIS_RUN_STATES,
)
from kronika.infrastructure.persistence.catalog_schema import media_analysis_runs
from kronika.infrastructure.persistence.engine import (
    run_in_immediate_transaction,
    run_in_transaction,
)

_REPOSITORY_FAILURE_MESSAGE = "Media analysis run operation failed."


class SqliteMediaAnalysisRunRepository:
    """Synchronous SQLite adapter with short write transactions."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_by_media_definition(
        self,
        media_id: MediaId,
        analysis_definition: str,
    ) -> MediaAnalysisRun | None:
        def operation(connection: Connection) -> MediaAnalysisRun | None:
            return _latest_run(connection, media_id, analysis_definition)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def create_pending(
        self,
        *,
        media_id: MediaId,
        media_location_id: MediaLocationId,
        analysis_definition: str,
        created_at_ms: int,
    ) -> MediaAnalysisRun:
        """Automatic get-or-create: never invent a new run after terminal history."""

        def operation(connection: Connection) -> MediaAnalysisRun:
            active = _active_run(connection, media_id, analysis_definition)
            if active is not None:
                return active
            latest = _latest_run(connection, media_id, analysis_definition)
            if latest is not None:
                return latest
            try:
                return _insert_pending(
                    connection,
                    media_id=media_id,
                    media_location_id=media_location_id,
                    analysis_definition=analysis_definition,
                    created_at_ms=created_at_ms,
                    supersedes_run_id=None,
                )
            except IntegrityError:
                winner = _active_run(connection, media_id, analysis_definition)
                if winner is not None:
                    return winner
                raced = _latest_run(connection, media_id, analysis_definition)
                if raced is not None:
                    return raced
                raise

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except IntegrityError as exc:
            raced = self.get_by_media_definition(media_id, analysis_definition)
            if raced is not None:
                return raced
            raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def create_manual_pending(
        self,
        *,
        media_id: MediaId,
        media_location_id: MediaLocationId,
        analysis_definition: str,
        created_at_ms: int,
    ) -> MediaAnalysisRun:
        """Explicit reanalysis: preserve terminals and create a new pending run."""

        def operation(connection: Connection) -> MediaAnalysisRun:
            active = _active_run(connection, media_id, analysis_definition)
            if active is not None:
                return active
            latest = _latest_run(connection, media_id, analysis_definition)
            supersedes_run_id = None
            if latest is not None:
                if latest.state not in TERMINAL_ANALYSIS_RUN_STATES:
                    return latest
                supersedes_run_id = latest.id.to_string()
            try:
                return _insert_pending(
                    connection,
                    media_id=media_id,
                    media_location_id=media_location_id,
                    analysis_definition=analysis_definition,
                    created_at_ms=created_at_ms,
                    supersedes_run_id=supersedes_run_id,
                )
            except IntegrityError:
                winner = _active_run(connection, media_id, analysis_definition)
                if winner is not None:
                    return winner
                raise

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except IntegrityError as exc:
            raced = self.get_by_media_definition(media_id, analysis_definition)
            if raced is not None and raced.state in {
                MediaAnalysisRunState.PENDING,
                MediaAnalysisRunState.ANALYZING,
            }:
                return raced
            raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE) from exc
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def claim_pending(
        self,
        *,
        run_id: str,
        expected_version: int,
        started_at_ms: int,
        max_attempts: int,
    ) -> MediaAnalysisRun:
        def operation(connection: Connection) -> MediaAnalysisRun:
            row = _require_row(connection, run_id)
            if (
                row["state"] != MediaAnalysisRunState.PENDING.value
                or row["version"] != expected_version
            ):
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            next_attempt = int(row["attempt_count"]) + 1
            if next_attempt > max_attempts:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            updated = connection.execute(
                update(media_analysis_runs)
                .where(
                    media_analysis_runs.c.id == run_id,
                    media_analysis_runs.c.version == expected_version,
                    media_analysis_runs.c.state
                    == MediaAnalysisRunState.PENDING.value,
                )
                .values(
                    state=MediaAnalysisRunState.ANALYZING.value,
                    attempt_count=next_attempt,
                    started_at_ms=started_at_ms,
                    version=expected_version + 1,
                )
            )
            if updated.rowcount != 1:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            return _run_from_row(_require_row(connection, run_id))

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def requeue_for_retry(
        self,
        *,
        run_id: str,
        expected_version: int,
        error_code: str,
        error_message: str,
        updated_at_ms: int,
    ) -> MediaAnalysisRun:
        del error_code, error_message, updated_at_ms

        def operation(connection: Connection) -> MediaAnalysisRun:
            row = _require_row(connection, run_id)
            if (
                row["state"] != MediaAnalysisRunState.ANALYZING.value
                or row["version"] != expected_version
            ):
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            updated = connection.execute(
                update(media_analysis_runs)
                .where(
                    media_analysis_runs.c.id == run_id,
                    media_analysis_runs.c.version == expected_version,
                    media_analysis_runs.c.state
                    == MediaAnalysisRunState.ANALYZING.value,
                )
                .values(
                    state=MediaAnalysisRunState.PENDING.value,
                    started_at_ms=None,
                    version=expected_version + 1,
                )
            )
            if updated.rowcount != 1:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            return _run_from_row(_require_row(connection, run_id))

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

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
        def operation(connection: Connection) -> MediaAnalysisRun:
            row = _require_row(connection, run_id)
            if (
                row["state"] != MediaAnalysisRunState.ANALYZING.value
                or row["version"] != expected_version
            ):
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            values = {
                "state": MediaAnalysisRunState.ANALYZED.value,
                "provider_id": provider_id,
                "model_id": model_id,
                "prompt_version": prompt_version,
                "result_schema_version": result_schema_version,
                "result_json": result_json,
                "error_code": None,
                "error_message": None,
                "completed_at_ms": completed_at_ms,
                "version": expected_version + 1,
            }
            if analysis_profile is not None:
                values["analysis_profile"] = analysis_profile
            if reasoning_enabled is not None:
                values["reasoning_enabled"] = 1 if reasoning_enabled else 0
            if derivative_strategy is not None:
                values["derivative_strategy"] = derivative_strategy
            if derivative_count is not None:
                values["derivative_count"] = derivative_count
            if provider_submission_occurred is not None:
                values["provider_submission_occurred"] = (
                    1 if provider_submission_occurred else 0
                )
            updated = connection.execute(
                update(media_analysis_runs)
                .where(
                    media_analysis_runs.c.id == run_id,
                    media_analysis_runs.c.version == expected_version,
                    media_analysis_runs.c.state
                    == MediaAnalysisRunState.ANALYZING.value,
                )
                .values(**values)
            )
            if updated.rowcount != 1:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            from kronika.infrastructure.persistence.record_repository import (
                note_successful_analysis,
            )

            note_successful_analysis(connection, str(row["media_id"]), run_id)
            return _run_from_row(_require_row(connection, run_id))

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

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
        def operation(connection: Connection) -> MediaAnalysisRun:
            row = _require_row(connection, run_id)
            if (
                row["state"] != MediaAnalysisRunState.ANALYZING.value
                or row["version"] != expected_version
            ):
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            started_at_ms = row["started_at_ms"]
            if started_at_ms is None:
                started_at_ms = completed_at_ms
            values: dict[str, object] = {
                "state": MediaAnalysisRunState.FAILED.value,
                "provider_id": provider_id,
                "model_id": model_id,
                "prompt_version": prompt_version,
                "result_schema_version": None,
                "result_json": None,
                "error_code": error_code,
                "error_message": error_message,
                "started_at_ms": started_at_ms,
                "completed_at_ms": completed_at_ms,
                "version": expected_version + 1,
            }
            if provider_submission_occurred is not None:
                values["provider_submission_occurred"] = (
                    1 if provider_submission_occurred else 0
                )
            if analysis_profile is not None:
                values["analysis_profile"] = analysis_profile
            if reasoning_enabled is not None:
                values["reasoning_enabled"] = 1 if reasoning_enabled else 0
            if derivative_strategy is not None:
                values["derivative_strategy"] = derivative_strategy
            if derivative_count is not None:
                values["derivative_count"] = derivative_count
            updated = connection.execute(
                update(media_analysis_runs)
                .where(
                    media_analysis_runs.c.id == run_id,
                    media_analysis_runs.c.version == expected_version,
                    media_analysis_runs.c.state
                    == MediaAnalysisRunState.ANALYZING.value,
                )
                .values(**values)
            )
            if updated.rowcount != 1:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            return _run_from_row(_require_row(connection, run_id))

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def list_unfinished(
        self,
        *,
        limit: int,
        after_created_at_ms: int | None = None,
        after_id: str | None = None,
    ) -> tuple[MediaAnalysisRun, ...]:
        def operation(connection: Connection) -> tuple[MediaAnalysisRun, ...]:
            query = (
                select(media_analysis_runs)
                .where(
                    media_analysis_runs.c.state.in_(
                        (
                            MediaAnalysisRunState.PENDING.value,
                            MediaAnalysisRunState.ANALYZING.value,
                        )
                    )
                )
                .order_by(
                    media_analysis_runs.c.created_at_ms.asc(),
                    media_analysis_runs.c.id.asc(),
                )
                .limit(limit)
            )
            if after_created_at_ms is not None:
                assert after_id is not None
                query = query.where(
                    or_(
                        media_analysis_runs.c.created_at_ms > after_created_at_ms,
                        (
                            (media_analysis_runs.c.created_at_ms == after_created_at_ms)
                            & (media_analysis_runs.c.id > after_id)
                        ),
                    )
                )
            rows = connection.execute(query).mappings().all()
            return tuple(_run_from_row(row) for row in rows)

        try:
            return run_in_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc

    def reset_interrupted_analyzing(
        self,
        *,
        run_id: str,
        expected_version: int,
        max_attempts: int,
        updated_at_ms: int,
    ) -> MediaAnalysisRun:
        del max_attempts  # No durable pre-provider distinction; always fail closed.

        def operation(connection: Connection) -> MediaAnalysisRun:
            row = _require_row(connection, run_id)
            if (
                row["state"] != MediaAnalysisRunState.ANALYZING.value
                or row["version"] != expected_version
            ):
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            # Stale analyzing means the external provider outcome is unknown.
            # Prefer terminal failure over risking a duplicate paid call.
            updated = connection.execute(
                update(media_analysis_runs)
                .where(
                    media_analysis_runs.c.id == run_id,
                    media_analysis_runs.c.version == expected_version,
                    media_analysis_runs.c.state
                    == MediaAnalysisRunState.ANALYZING.value,
                )
                .values(
                    state=MediaAnalysisRunState.FAILED.value,
                    error_code="ANALYSIS_OUTCOME_UNKNOWN",
                    error_message=(
                        "Automatic analysis was interrupted and the provider "
                        "outcome cannot be determined safely."
                    ),
                    completed_at_ms=updated_at_ms,
                    version=expected_version + 1,
                )
            )
            if updated.rowcount != 1:
                raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
            return _run_from_row(_require_row(connection, run_id))

        try:
            return run_in_immediate_transaction(self._engine, operation)
        except FrameNestMediaAnalysisRunRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise FrameNestMediaAnalysisRunRepositoryError(
                _REPOSITORY_FAILURE_MESSAGE
            ) from exc


def _active_run(
    connection: Connection,
    media_id: MediaId,
    analysis_definition: str,
) -> MediaAnalysisRun | None:
    row = connection.execute(
        select(media_analysis_runs)
        .where(
            media_analysis_runs.c.media_id == media_id.to_string(),
            media_analysis_runs.c.analysis_definition == analysis_definition,
            media_analysis_runs.c.state.in_(tuple(ACTIVE_ANALYSIS_RUN_STATE_VALUES)),
        )
        .order_by(
            media_analysis_runs.c.created_at_ms.desc(),
            media_analysis_runs.c.id.desc(),
        )
        .limit(1)
    ).mappings().first()
    return None if row is None else _run_from_row(row)


def _latest_run(
    connection: Connection,
    media_id: MediaId,
    analysis_definition: str,
) -> MediaAnalysisRun | None:
    row = connection.execute(
        select(media_analysis_runs)
        .where(
            media_analysis_runs.c.media_id == media_id.to_string(),
            media_analysis_runs.c.analysis_definition == analysis_definition,
        )
        .order_by(
            media_analysis_runs.c.created_at_ms.desc(),
            media_analysis_runs.c.id.desc(),
        )
        .limit(1)
    ).mappings().first()
    return None if row is None else _run_from_row(row)


def _insert_pending(
    connection: Connection,
    *,
    media_id: MediaId,
    media_location_id: MediaLocationId,
    analysis_definition: str,
    created_at_ms: int,
    supersedes_run_id: str | None,
) -> MediaAnalysisRun:
    run_id = str(uuid.uuid4())
    if supersedes_run_id is not None:
        if supersedes_run_id == run_id:
            raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
        superseded = _require_row(connection, supersedes_run_id)
        if (
            str(superseded["media_id"]) != media_id.to_string()
            or str(superseded["analysis_definition"]) != analysis_definition
            or MediaAnalysisRunState(str(superseded["state"]))
            not in TERMINAL_ANALYSIS_RUN_STATES
        ):
            raise MediaAnalysisRunConflictError(_REPOSITORY_FAILURE_MESSAGE)
    connection.execute(
        insert(media_analysis_runs).values(
            id=run_id,
            media_id=media_id.to_string(),
            media_location_id=media_location_id.to_string(),
            analysis_definition=analysis_definition,
            state=MediaAnalysisRunState.PENDING.value,
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
            supersedes_run_id=supersedes_run_id,
        )
    )
    created = connection.execute(
        select(media_analysis_runs).where(media_analysis_runs.c.id == run_id)
    ).mappings().one()
    return _run_from_row(created)


def _require_row(connection: Connection, run_id: str) -> Mapping[str, object]:
    row = connection.execute(
        select(media_analysis_runs).where(media_analysis_runs.c.id == run_id)
    ).mappings().first()
    if row is None:
        raise MediaAnalysisRunNotFoundError(_REPOSITORY_FAILURE_MESSAGE)
    return row


def _run_from_row(row: Mapping[str, object]) -> MediaAnalysisRun:
    try:
        supersedes_raw = row.get("supersedes_run_id")
        return MediaAnalysisRun(
            id=MediaAnalysisRunId(str(row["id"])),
            media_id=MediaId.from_string(str(row["media_id"])),
            media_location_id=MediaLocationId.from_string(
                str(row["media_location_id"])
            ),
            analysis_definition=str(row["analysis_definition"]),
            state=MediaAnalysisRunState(str(row["state"])),
            attempt_count=int(row["attempt_count"]),
            provider_id=None if row["provider_id"] is None else str(row["provider_id"]),
            model_id=None if row["model_id"] is None else str(row["model_id"]),
            prompt_version=(
                None if row["prompt_version"] is None else str(row["prompt_version"])
            ),
            result_schema_version=(
                None
                if row["result_schema_version"] is None
                else str(row["result_schema_version"])
            ),
            result_json=None if row["result_json"] is None else str(row["result_json"]),
            error_code=None if row["error_code"] is None else str(row["error_code"]),
            error_message=(
                None if row["error_message"] is None else str(row["error_message"])
            ),
            created_at_ms=int(row["created_at_ms"]),
            started_at_ms=(
                None if row["started_at_ms"] is None else int(row["started_at_ms"])
            ),
            completed_at_ms=(
                None if row["completed_at_ms"] is None else int(row["completed_at_ms"])
            ),
            version=int(row["version"]),
            analysis_profile=(
                None
                if row.get("analysis_profile") is None
                else str(row["analysis_profile"])
            ),
            reasoning_enabled=(
                None
                if row.get("reasoning_enabled") is None
                else bool(int(row["reasoning_enabled"]))
            ),
            derivative_strategy=(
                None
                if row.get("derivative_strategy") is None
                else str(row["derivative_strategy"])
            ),
            derivative_count=(
                None
                if row.get("derivative_count") is None
                else int(row["derivative_count"])
            ),
            provider_submission_occurred=(
                None
                if row.get("provider_submission_occurred") is None
                else bool(int(row["provider_submission_occurred"]))
            ),
            supersedes_run_id=(
                None
                if supersedes_raw is None
                else MediaAnalysisRunId(str(supersedes_raw))
            ),
        )
    except (FrameNestIdentityError, TypeError, ValueError) as exc:
        raise FrameNestMediaAnalysisRunRepositoryError(
            _REPOSITORY_FAILURE_MESSAGE
        ) from exc
