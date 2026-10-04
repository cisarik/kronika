"""Repository contract tests for the durable accepted-cover relation."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from kronika.application.ports.media_cover_repository import (
    MediaCoverConflictError,
    MediaCoverDraft,
    MediaCoverMediaNotFoundError,
)
from kronika.domain.identities import DeviceId, LibraryId, MediaId, MediaLocationId
from kronika.domain.media_cover import (
    COVER_ARTIFACT_MEDIA_TYPE,
    COVER_ARTIFACT_PROFILE,
    SOURCE_OBSERVATION_ALGORITHM,
    CoverSourceKind,
    source_reference_for_location,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.media_cover_repository import (
    SqliteMediaCoverRepository,
)
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

MEDIA_ID = MediaId.from_string("11111111-1111-4111-8111-111111111111")
LOCATION_ID = MediaLocationId.from_string("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
LIBRARY_ID = LibraryId.from_string("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
DEVICE_ID = DeviceId.from_string("cccccccc-cccc-4ccc-8ccc-cccccccccccc")

DIGEST_A = "a" * 64
DIGEST_B = "b" * 64
OBS_A = "c" * 64
OBS_B = "d" * 64


def _seed(database_path: Path) -> None:
    connection = sqlite3.connect(str(database_path))
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "INSERT INTO logical_media "
            "(id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', 1, 1)",
            (MEDIA_ID.to_string(),),
        )
        connection.execute(
            "INSERT INTO devices (id, display_name) VALUES (?, 'device')",
            (DEVICE_ID.to_string(),),
        )
        connection.execute(
            "INSERT INTO libraries "
            "(id, device_id, display_name, path_flavor, root_path) "
            "VALUES (?, ?, 'l', 'posix', '/media/x')",
            (LIBRARY_ID.to_string(), DEVICE_ID.to_string()),
        )
        connection.execute(
            "INSERT INTO physical_media_locations "
            "(id, media_id, library_id, relative_path, availability, "
            " observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
            "VALUES (?, ?, ?, 'clip.mp4', 'available', 1000, 1, 1, 1)",
            (LOCATION_ID.to_string(), MEDIA_ID.to_string(), LIBRARY_ID.to_string()),
        )
        connection.commit()
    finally:
        connection.close()


def _draft(
    *,
    digest: str = DIGEST_A,
    obs: str = OBS_A,
    timestamp_ms: int = 250,
    accepted_at_ms: int = 99,
) -> MediaCoverDraft:
    return MediaCoverDraft(
        media_id=MEDIA_ID,
        source_location_id=LOCATION_ID,
        source_reference=source_reference_for_location(LOCATION_ID),
        source_kind=CoverSourceKind.MP4,
        source_timestamp_ms=timestamp_ms,
        source_size_bytes=1000,
        source_mtime_ns=1,
        source_duration_ms=1000,
        source_observation_version=SOURCE_OBSERVATION_ALGORITHM,
        source_observation_digest=obs,
        artifact_profile=COVER_ARTIFACT_PROFILE,
        artifact_media_type=COVER_ARTIFACT_MEDIA_TYPE,
        artifact_digest=digest,
        artifact_width=512,
        artifact_height=288,
        artifact_byte_size=15000,
        accepted_at_ms=accepted_at_ms,
    )


@pytest.fixture
def repository(tmp_path: Path):
    database_path = tmp_path / "covers.sqlite3"
    upgrade_database_to_head_from_path(database_path)
    _seed(database_path)
    engine = create_sqlite_engine(database_path)
    try:
        yield SqliteMediaCoverRepository(engine)
    finally:
        dispose_engine(engine)


def upgrade_database_to_head_from_path(database_path: Path) -> None:
    from kronika.configuration import KronikaSettings

    upgrade_database_to_head(
        KronikaSettings(database_path=database_path, _env_file=None)
    )


def test_absent_cover_is_none_and_media_missing_is_rejected(repository) -> None:
    assert repository.get(MEDIA_ID) is None
    unknown_media = MediaId.from_string("22222222-2222-4222-8222-222222222222")
    with pytest.raises(MediaCoverMediaNotFoundError):
        repository.set_cover(
            MediaCoverDraft(
                media_id=unknown_media,
                source_location_id=LOCATION_ID,
                source_reference=source_reference_for_location(LOCATION_ID),
                source_kind=CoverSourceKind.MP4,
                source_timestamp_ms=250,
                source_size_bytes=1000,
                source_mtime_ns=1,
                source_duration_ms=1000,
                source_observation_version=SOURCE_OBSERVATION_ALGORITHM,
                source_observation_digest=OBS_A,
                artifact_profile=COVER_ARTIFACT_PROFILE,
                artifact_media_type=COVER_ARTIFACT_MEDIA_TYPE,
                artifact_digest=DIGEST_A,
                artifact_width=512,
                artifact_height=288,
                artifact_byte_size=15000,
                accepted_at_ms=99,
            ),
            0,
        )
    assert repository.get(unknown_media) is None
    assert repository.list_by_media((MEDIA_ID,)) == ()
    assert repository.list_all() == ()


def test_first_creation_and_idempotent_repeat(repository) -> None:
    created = repository.set_cover(_draft(), 0)
    assert created.outcome == "created"
    assert created.cover is not None
    assert created.cover.revision == 1
    assert created.cover.artifact_digest == DIGEST_A

    unchanged = repository.set_cover(_draft(), 1)
    assert unchanged.outcome == "unchanged"
    assert unchanged.cover is not None
    assert unchanged.cover.revision == 1

    assert repository.get(MEDIA_ID).revision == 1
    assert repository.list_by_media((MEDIA_ID,))[0].revision == 1
    assert repository.list_all()[0].revision == 1


def test_duplicate_create_without_cover_precondition_conflicts(repository) -> None:
    repository.set_cover(_draft(), 0)
    with pytest.raises(MediaCoverConflictError):
        repository.set_cover(_draft(digest=DIGEST_B, obs=OBS_B), 2)


def test_replacement_increments_revision_once(repository) -> None:
    repository.set_cover(_draft(), 0)
    replaced = repository.set_cover(
        _draft(digest=DIGEST_B, obs=OBS_B, timestamp_ms=750),
        1,
    )
    assert replaced.outcome == "replaced"
    assert replaced.cover is not None
    assert replaced.cover.revision == 2
    assert replaced.cover.artifact_digest == DIGEST_B
    assert repository.get(MEDIA_ID).revision == 2


def test_stale_revision_conflict_preserves_newer_cover(repository) -> None:
    repository.set_cover(_draft(), 0)
    repository.set_cover(_draft(digest=DIGEST_B, obs=OBS_B), 1)
    with pytest.raises(MediaCoverConflictError):
        repository.set_cover(_draft(digest=DIGEST_A, obs=OBS_A), 1)
    current = repository.get(MEDIA_ID)
    assert current.artifact_digest == DIGEST_B
    assert current.revision == 2


def test_physical_location_deletion_preserves_cover_through_repository(
    repository, tmp_path: Path
) -> None:
    repository.set_cover(_draft(), 0)
    repository.set_cover(_draft(digest=DIGEST_B, obs=OBS_B), 1)

    connection = sqlite3.connect(tmp_path / "covers.sqlite3")
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.execute(
            "DELETE FROM physical_media_locations WHERE id = ?",
            (LOCATION_ID.to_string(),),
        )
        connection.commit()
    finally:
        connection.close()

    cover = repository.get(MEDIA_ID)
    assert cover is not None
    assert cover.source_location_id is None
    assert cover.source_reference == f"location:{LOCATION_ID.to_string()}"
    assert cover.artifact_digest == DIGEST_B
    assert cover.revision == 2
