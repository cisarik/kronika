"""Persistence tests for durable automatic media analysis runs."""

from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command

from kronika.application.ports.media_analysis_runs import MediaAnalysisRunConflictError
from kronika.configuration import KronikaSettings
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.media_analysis_runs import (
    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    MediaAnalysisRunState,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.media_analysis_run_repository import (
    SqliteMediaAnalysisRunRepository,
)
from kronika.infrastructure.persistence.migrations import _alembic_config


MEDIA_ID = MediaId.from_string("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee")
LOCATION_ID = MediaLocationId.from_string("ffffffff-ffff-4fff-8fff-ffffffffffff")


def _migrate(database_path: Path) -> None:
    engine = create_sqlite_engine(database_path)
    try:
        with engine.connect() as connection:
            with _alembic_config(
                "kronika.infrastructure.persistence.alembic_environment"
            ) as config:
                config.attributes["connection"] = connection
                command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO devices (id, display_name) VALUES (?, 'Synthetic')"
                ,
                ("dddddddd-dddd-4ddd-8ddd-dddddddddddd",),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path)
                VALUES (?, ?, 'Library', 'posix', '/synthetic')
                """,
                (
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                    "dddddddd-dddd-4ddd-8ddd-dddddddddddd",
                ),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms)
                VALUES (?, 'video', 1, 1)
                """,
                (MEDIA_ID.to_string(),),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO physical_media_locations (
                    id, media_id, library_id, relative_path, availability,
                    observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, 'clip.mp4', 'available', 8, NULL, 1, 1)
                """,
                (
                    LOCATION_ID.to_string(),
                    MEDIA_ID.to_string(),
                    "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
                ),
            )
    finally:
        dispose_engine(engine)


@pytest.fixture
def repository(tmp_path: Path) -> SqliteMediaAnalysisRunRepository:
    database_path = tmp_path / "catalog.sqlite3"
    _migrate(database_path)
    engine = create_sqlite_engine(database_path)
    repo = SqliteMediaAnalysisRunRepository(engine)
    yield repo
    dispose_engine(engine)


def test_create_pending_is_idempotent(repository: SqliteMediaAnalysisRunRepository) -> None:
    first = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    second = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=20,
    )
    assert first.id.to_string() == second.id.to_string()
    assert first.state is MediaAnalysisRunState.PENDING
    assert second.created_at_ms == 10


def test_claim_and_record_analyzed_outside_contradictory_states(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=11,
        max_attempts=3,
    )
    assert claimed.state is MediaAnalysisRunState.ANALYZING
    assert claimed.attempt_count == 1
    analyzed = repository.record_analyzed(
        run_id=claimed.id.to_string(),
        expected_version=claimed.version,
        provider_id="nvidia-nim",
        model_id="model",
        prompt_version="framenest-media-suggestion-v3",
        result_schema_version="framenest-media-suggestion-result-v1",
        result_json='{"title":"T","description":"D","collection":"C","tags":["a"],'
        '"suggested_filename":"t.mp4","confidence":0.5,"evidence":["e"],'
        '"uncertainties":[]}',
        completed_at_ms=12,
    )
    assert analyzed.state is MediaAnalysisRunState.ANALYZED
    assert analyzed.result_json is not None
    with pytest.raises(MediaAnalysisRunConflictError):
        repository.claim_pending(
            run_id=analyzed.id.to_string(),
            expected_version=analyzed.version,
            started_at_ms=13,
            max_attempts=3,
        )


def test_requeue_and_terminal_failure(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=11,
        max_attempts=3,
    )
    requeued = repository.requeue_for_retry(
        run_id=claimed.id.to_string(),
        expected_version=claimed.version,
        error_code="PROVIDER_UNAVAILABLE",
        error_message="temporary",
        updated_at_ms=12,
    )
    assert requeued.state is MediaAnalysisRunState.PENDING
    assert requeued.started_at_ms is None
    claimed_again = repository.claim_pending(
        run_id=requeued.id.to_string(),
        expected_version=requeued.version,
        started_at_ms=13,
        max_attempts=3,
    )
    failed = repository.record_failed(
        run_id=claimed_again.id.to_string(),
        expected_version=claimed_again.version,
        error_code="PROVIDER_AUTH",
        error_message="AI provider authentication failed.",
        provider_id=None,
        model_id=None,
        prompt_version="framenest-media-suggestion-v3",
        completed_at_ms=14,
        provider_submission_occurred=True,
    )
    assert failed.state is MediaAnalysisRunState.FAILED
    assert failed.error_code == "PROVIDER_AUTH"
    assert failed.result_json is None
    assert failed.provider_submission_occurred is True


def test_reset_interrupted_analyzing_fails_closed_as_outcome_unknown(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=11,
        max_attempts=3,
    )
    assert claimed.state is MediaAnalysisRunState.ANALYZING
    assert claimed.attempt_count == 1
    failed = repository.reset_interrupted_analyzing(
        run_id=claimed.id.to_string(),
        expected_version=claimed.version,
        max_attempts=3,
        updated_at_ms=12,
    )
    assert failed.state is MediaAnalysisRunState.FAILED
    assert failed.error_code == "ANALYSIS_OUTCOME_UNKNOWN"
    assert failed.result_json is None
    assert "ANALYSIS_OUTCOME_UNKNOWN" == failed.error_code
    assert failed.error_message is not None
    assert "/home/" not in failed.error_message
    assert "sk-" not in failed.error_message
    # Must not return to pending under remaining attempts.
    assert failed.state is not MediaAnalysisRunState.PENDING
    listed = repository.list_unfinished(limit=8)
    assert listed == ()


def test_crash_window_provider_success_then_interrupt_does_not_replay_provider(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    """Reproduce ambiguous crash: provider OK, analyzed row never written."""
    from kronika.application.media_analysis_lifecycle import (
        ExecuteAutomaticMediaAnalysisRun,
    )
    from kronika.application.media_suggestion import MediaSuggestion, PROMPT_VERSION

    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    calls = {"n": 0}

    class _CountingProvider:
        def execute(self, media_id, location_id):
            del media_id, location_id
            calls["n"] += 1
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
        _CountingProvider(),
        max_attempts=3,
        now_ms=lambda: 20,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=11,
        max_attempts=3,
    )
    # External provider succeeds once; process dies before record_analyzed.
    _ = service._executor.execute(claimed.media_id, claimed.media_location_id)
    assert calls["n"] == 1
    surviving = repository.get_by_media_definition(
        MEDIA_ID,
        AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    )
    assert surviving is not None
    assert surviving.state is MediaAnalysisRunState.ANALYZING

    recovered = service.execute(surviving)
    assert recovered.state is MediaAnalysisRunState.FAILED
    assert recovered.error_code == "ANALYSIS_OUTCOME_UNKNOWN"
    assert calls["n"] == 1

    second_restart = service.execute(recovered)
    assert second_restart.state is MediaAnalysisRunState.FAILED
    assert calls["n"] == 1
    assert repository.list_unfinished(limit=8) == ()

    # Repeated catalog-equivalent create must not invent another paid job.
    again = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=30,
    )
    assert again.id.to_string() == recovered.id.to_string()
    assert again.state is MediaAnalysisRunState.FAILED
    assert calls["n"] == 1


def _fail_run(
    repository: SqliteMediaAnalysisRunRepository,
    *,
    error_code: str,
    provider_submission_occurred: bool,
    created_at_ms: int,
) -> object:
    pending = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=created_at_ms,
    )
    claimed = repository.claim_pending(
        run_id=pending.id.to_string(),
        expected_version=pending.version,
        started_at_ms=created_at_ms + 1,
        max_attempts=3,
    )
    return repository.record_failed(
        run_id=claimed.id.to_string(),
        expected_version=claimed.version,
        error_code=error_code,
        error_message=f"{error_code} message",
        provider_id=None,
        model_id=None,
        prompt_version="framenest-media-suggestion-v3",
        completed_at_ms=created_at_ms + 2,
        provider_submission_occurred=provider_submission_occurred,
    )


def test_manual_pending_preserves_provider_failed_historical_run(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    failed = _fail_run(
        repository,
        error_code="PROVIDER_UNAVAILABLE",
        provider_submission_occurred=True,
        created_at_ms=10,
    )
    snapshot = (
        failed.id.to_string(),
        failed.state,
        failed.error_code,
        failed.provider_submission_occurred,
        failed.version,
        failed.result_json,
    )
    manual = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=20,
    )
    assert manual.id.to_string() != failed.id.to_string()
    assert manual.state is MediaAnalysisRunState.PENDING
    assert manual.supersedes_run_id is not None
    assert manual.supersedes_run_id.to_string() == failed.id.to_string()
    preserved = repository.get_by_media_definition(
        MEDIA_ID,
        AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
    )
    assert preserved is not None
    assert preserved.id.to_string() == manual.id.to_string()
    # Historical row remains queryable by identity through supersession target.
    from sqlalchemy import select
    from kronika.infrastructure.persistence.catalog_schema import media_analysis_runs
    from kronika.infrastructure.persistence.engine import run_in_transaction

    def load_failed(connection):
        row = connection.execute(
            select(media_analysis_runs).where(
                media_analysis_runs.c.id == failed.id.to_string()
            )
        ).mappings().one()
        return (
            row["id"],
            row["state"],
            row["error_code"],
            row["provider_submission_occurred"],
            row["version"],
            row["result_json"],
        )

    loaded = run_in_transaction(repository._engine, load_failed)
    assert loaded == (
        snapshot[0],
        "failed",
        "PROVIDER_UNAVAILABLE",
        1,
        snapshot[4],
        None,
    )


def test_manual_pending_preserves_local_failed_and_analyzed_history(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    local_failed = _fail_run(
        repository,
        error_code="ANALYSIS_FAILED",
        provider_submission_occurred=False,
        created_at_ms=10,
    )
    first_manual = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=20,
    )
    assert first_manual.supersedes_run_id is not None
    assert first_manual.supersedes_run_id.to_string() == local_failed.id.to_string()
    claimed = repository.claim_pending(
        run_id=first_manual.id.to_string(),
        expected_version=first_manual.version,
        started_at_ms=21,
        max_attempts=3,
    )
    analyzed = repository.record_analyzed(
        run_id=claimed.id.to_string(),
        expected_version=claimed.version,
        provider_id="nvidia-nim",
        model_id="model",
        prompt_version="framenest-media-suggestion-v3",
        result_schema_version="framenest-media-suggestion-result-v1",
        result_json='{"title":"T","description":"D","collection":"C","tags":["a"],'
        '"suggested_filename":"t.mp4","confidence":0.5,"evidence":["e"],'
        '"uncertainties":[]}',
        completed_at_ms=22,
        provider_submission_occurred=True,
    )
    analyzed_json = analyzed.result_json
    second_manual = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=30,
    )
    assert second_manual.id.to_string() != analyzed.id.to_string()
    assert second_manual.supersedes_run_id is not None
    assert second_manual.supersedes_run_id.to_string() == analyzed.id.to_string()
    from sqlalchemy import select
    from kronika.infrastructure.persistence.catalog_schema import media_analysis_runs
    from kronika.infrastructure.persistence.engine import run_in_transaction

    def load_analyzed(connection):
        return connection.execute(
            select(media_analysis_runs).where(
                media_analysis_runs.c.id == analyzed.id.to_string()
            )
        ).mappings().one()

    row = run_in_transaction(repository._engine, load_analyzed)
    assert row["state"] == "analyzed"
    assert row["result_json"] == analyzed_json

    claimed_second = repository.claim_pending(
        run_id=second_manual.id.to_string(),
        expected_version=second_manual.version,
        started_at_ms=31,
        max_attempts=3,
    )
    second_done = repository.record_analyzed(
        run_id=claimed_second.id.to_string(),
        expected_version=claimed_second.version,
        provider_id="nvidia-nim",
        model_id="model",
        prompt_version="framenest-media-suggestion-v3",
        result_schema_version="framenest-media-suggestion-result-v1",
        result_json='{"title":"U","description":"D","collection":"C","tags":["a"],'
        '"suggested_filename":"u.mp4","confidence":0.5,"evidence":["e"],'
        '"uncertainties":[]}',
        completed_at_ms=32,
        provider_submission_occurred=True,
    )
    third = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=40,
    )
    assert third.id.to_string() != second_done.id.to_string()
    assert third.supersedes_run_id is not None
    assert third.supersedes_run_id.to_string() == second_done.id.to_string()


def test_create_pending_after_terminal_remains_idempotent(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    failed = _fail_run(
        repository,
        error_code="PROVIDER_UNAVAILABLE",
        provider_submission_occurred=True,
        created_at_ms=10,
    )
    again = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=20,
    )
    assert again.id.to_string() == failed.id.to_string()
    assert again.state is MediaAnalysisRunState.FAILED


def test_duplicate_manual_while_pending_and_analyzing_returns_active(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    first = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    second = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=11,
    )
    assert first.id.to_string() == second.id.to_string()
    claimed = repository.claim_pending(
        run_id=first.id.to_string(),
        expected_version=first.version,
        started_at_ms=12,
        max_attempts=3,
    )
    third = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=13,
    )
    assert third.id.to_string() == claimed.id.to_string()
    assert third.state is MediaAnalysisRunState.ANALYZING


def test_concurrent_manual_requests_share_one_active_run(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    import threading

    barrier = threading.Barrier(8)
    results: list[str] = []
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            run = repository.create_manual_pending(
                media_id=MEDIA_ID,
                media_location_id=LOCATION_ID,
                analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
                created_at_ms=10,
            )
            results.append(run.id.to_string())
        except BaseException as exc:  # noqa: BLE001 - collect race evidence
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert errors == []
    assert len(results) == 8
    assert len(set(results)) == 1
    unfinished = repository.list_unfinished(limit=8)
    assert len(unfinished) == 1


def test_active_partial_unique_index_rejects_second_active_insert(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    import sqlite3

    first = repository.create_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    assert first.state is MediaAnalysisRunState.PENDING
    raw = sqlite3.connect(repository._engine.url.database)
    try:
        raw.execute("PRAGMA foreign_keys=ON")
        with pytest.raises(sqlite3.IntegrityError):
            raw.execute(
                """
                INSERT INTO media_analysis_runs (
                    id, media_id, media_location_id, analysis_definition, state,
                    attempt_count, created_at_ms, version
                ) VALUES (?, ?, ?, ?, 'pending', 0, 11, 1)
                """,
                (
                    "22222222-2222-4222-8222-222222222222",
                    MEDIA_ID.to_string(),
                    LOCATION_ID.to_string(),
                    AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
                ),
            )
        raw.rollback()
        # Terminal duplicates are allowed after history migration.
        claimed = repository.claim_pending(
            run_id=first.id.to_string(),
            expected_version=first.version,
            started_at_ms=12,
            max_attempts=3,
        )
        repository.record_failed(
            run_id=claimed.id.to_string(),
            expected_version=claimed.version,
            error_code="PROVIDER_UNAVAILABLE",
            error_message="unavailable",
            provider_id=None,
            model_id=None,
            prompt_version="v",
            completed_at_ms=13,
            provider_submission_occurred=True,
        )
        raw.execute(
            """
            INSERT INTO media_analysis_runs (
                id, media_id, media_location_id, analysis_definition, state,
                attempt_count, error_code, error_message,
                created_at_ms, started_at_ms, completed_at_ms, version
            ) VALUES (?, ?, ?, ?, 'failed', 1, 'ANALYSIS_FAILED', 'local',
                      14, 14, 14, 1)
            """,
            (
                "33333333-3333-4333-8333-333333333333",
                MEDIA_ID.to_string(),
                LOCATION_ID.to_string(),
                AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
            ),
        )
        raw.commit()
    finally:
        raw.close()


def test_create_manual_pending_makes_no_provider_call(
    repository: SqliteMediaAnalysisRunRepository,
) -> None:
    calls = {"n": 0}

    class _ForbiddenProvider:
        def execute(self, *args, **kwargs):
            del args, kwargs
            calls["n"] += 1
            raise AssertionError("provider must not be called")

    _ = _ForbiddenProvider()
    pending = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=10,
    )
    again = repository.create_manual_pending(
        media_id=MEDIA_ID,
        media_location_id=LOCATION_ID,
        analysis_definition=AUTOMATIC_POST_CATALOG_ANALYSIS_DEFINITION,
        created_at_ms=11,
    )
    assert pending.id.to_string() == again.id.to_string()
    assert calls["n"] == 0
