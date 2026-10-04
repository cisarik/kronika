"""Repository evidence for companion review inbox and history reads."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import text

from kronika.application.companion_review import (
    MappedTagStatus,
    decode_companion_review_inbox_cursor,
)
from kronika.application.ports.companion_review_repository import (
    CompanionReviewMediaNotFoundError,
    CompanionReviewMovieExcludedError,
    CompanionReviewRunNotEligibleError,
    CompanionReviewStaleMappingError,
    CompanionReviewStoredResultError,
    CompanionReviewTagLimitConflictError,
    FrameNestCompanionReviewRepositoryError,
)
from kronika.configuration import KronikaSettings
from kronika.domain.content_publication import ContentPublicationOrigin
from kronika.domain.identities import MediaId
from kronika.infrastructure.persistence.companion_review_repository import (
    SqliteCompanionReviewRepository,
)
from kronika.infrastructure.persistence.engine import create_sqlite_engine, dispose_engine
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DEVICE_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LIBRARY_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
ADMIN_KEY = "admin@example.com"
OTHER_KEY = "other@example.com"

GENERIC = "aaaaaaaa-aaaa-4aaa-8aaa-000000000001"
GENERIC_LOC = "aaaaaaaa-aaaa-4aaa-8aaa-000000000011"
GENERIC_RUN = "aaaaaaaa-aaaa-4aaa-8aaa-000000000021"
GENERIC_FAIL = "aaaaaaaa-aaaa-4aaa-8aaa-000000000031"
GENERIC_NEWER = "aaaaaaaa-aaaa-4aaa-8aaa-000000000041"

MOVIE = "bbbbbbbb-bbbb-4bbb-8bbb-000000000002"
MOVIE_LOC = "bbbbbbbb-bbbb-4bbb-8bbb-000000000012"
MOVIE_RUN = "bbbbbbbb-bbbb-4bbb-8bbb-000000000022"

MOVIE_ID_MEDIA = "cccccccc-cccc-4ccc-8ccc-000000000003"
MOVIE_ID_LOC = "cccccccc-cccc-4ccc-8ccc-000000000013"
MOVIE_ID_RUN = "cccccccc-cccc-4ccc-8ccc-000000000023"

WEBSITE = "dddddddd-dddd-4ddd-8ddd-000000000004"
WEBSITE_LOC = "dddddddd-dddd-4ddd-8ddd-000000000014"
WEBSITE_RUN = "dddddddd-dddd-4ddd-8ddd-000000000024"

HISTORICAL = "eeeeeeee-eeee-4eee-8eee-000000000005"
HISTORICAL_LOC = "eeeeeeee-eeee-4eee-8eee-000000000015"
HISTORICAL_RUN = "eeeeeeee-eeee-4eee-8eee-000000000025"
HISTORICAL_NEWER = "eeeeeeee-eeee-4eee-8eee-000000000045"

NULL_PROFILE = "ffffffff-ffff-4fff-8fff-000000000006"
NULL_PROFILE_LOC = "ffffffff-ffff-4fff-8fff-000000000016"
NULL_PROFILE_RUN = "ffffffff-ffff-4fff-8fff-000000000026"

PENDING = "12121212-1212-4212-8212-121212121212"
PENDING_LOC = "13131313-1313-4313-8313-131313131313"
PENDING_CLAIM = "14141414-1414-4414-8414-141414141414"
PENDING_ASSET = "15151515-1515-4515-8515-151515151515"
OTHER_PENDING = "16161616-1616-4616-8616-161616161616"
OTHER_PENDING_LOC = "17171717-1717-4717-8717-171717171717"
OTHER_PENDING_CLAIM = "18181818-1818-4818-8818-181818181818"
OTHER_PENDING_ASSET = "19191919-1919-4919-8919-191919191919"
DEDUP_CLAIM = "20202020-2020-4020-8020-202020202020"
DEDUP_ASSET = "21212121-2121-4121-8121-212121212121"
PENDING_FAIL = "22222222-2222-4222-8222-222222222229"
MOVIE_PENDING_CLAIM = "23232323-2323-4323-8323-232323232323"
MOVIE_PENDING_ASSET = "24242424-2424-4424-8424-242424242424"
OMITTED = "25252525-2525-4252-8252-252525252525"
OMITTED_LOC = "26262626-2626-4262-8262-262626262626"
OMITTED_CLAIM = "27272727-2727-4272-8272-272727272727"
OMITTED_ASSET = "28282828-2828-4282-8282-282828282828"
CLAIM_MOVIE = "29292929-2929-4292-8292-292929292929"
CLAIM_MOVIE_LOC = "30303030-3030-4303-8303-303030303030"
CLAIM_MOVIE_CLAIM = "31313131-3131-4313-8313-313131313131"
CLAIM_MOVIE_ASSET = "32323232-3232-4323-8323-323232323232"
OTHER_OMITTED = "33333333-3333-4333-8333-333333333333"
OTHER_OMITTED_LOC = "34343434-3434-4343-8343-343434343434"
OTHER_OMITTED_CLAIM = "35353535-3535-4353-8353-353535353535"
OTHER_OMITTED_ASSET = "36363636-3636-4363-8363-363636363636"
SUGGESTION_READY = "37373737-3737-4373-8373-373737373737"
SUGGESTION_READY_LOC = "38383838-3838-4383-8383-383838383838"
SUGGESTION_READY_RUN = "39393939-3939-4393-8393-393939393939"
SUGGESTION_READY_CLAIM = "40404040-4040-4404-8404-404040404040"
SUGGESTION_READY_ASSET = "41414141-4141-4414-8414-414141414141"
UNDECODABLE = "42424242-4242-4424-8424-424242424242"
UNDECODABLE_LOC = "43434343-4343-4434-8434-434343434343"
UNDECODABLE_RUN = "44444444-4444-4444-8444-444444444444"
UNDECODABLE_CLAIM = "45454545-4545-4454-8454-454545454545"
UNDECODABLE_ASSET = "46464646-4646-4464-8464-464646464646"


def _result_json(*, title: str, tags: list[str]) -> str:
    return json.dumps(
        {
            "collection": "memes",
            "confidence": 0.9,
            "description": "A description.",
            "evidence": ["visible subject"],
            "suggested_filename": "clip.gif",
            "tags": tags,
            "title": title,
            "uncertainties": [],
        },
        separators=(",", ":"),
        sort_keys=True,
    )


def _repository(tmp_path: Path) -> tuple[SqliteCompanionReviewRepository, object]:
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3", _env_file=None
    )
    upgrade_database_to_head(settings)
    engine = create_sqlite_engine(settings.database_path)
    _seed_catalog(engine)
    return SqliteCompanionReviewRepository(engine), engine


def _seed_catalog(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO devices (id, display_name) VALUES (:id, 'Dev')"),
            {"id": DEVICE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO libraries "
                "(id, device_id, display_name, path_flavor, root_path) "
                "VALUES (:id, :device, 'Lib', 'posix', '/tmp/synthetic')"
            ),
            {"id": LIBRARY_ID, "device": DEVICE_ID},
        )
        connection.execute(
            text(
                "INSERT INTO canonical_tags "
                "(key, display_name, created_at_ms, updated_at_ms) VALUES "
                "('cats', 'Cats', 1, 1), "
                "('dogs', 'Dogs', 1, 1), "
                "('birds', 'Birds', 1, 1), "
                "('fish', 'Fish', 1, 1), "
                "('cars', 'Cars', 1, 1), "
                "('trees', 'Trees', 1, 1), "
                "('cat-a', 'Feline', 1, 1), "
                "('cat-b', 'Feline', 1, 1)"
            )
        )
        _insert_media(connection, GENERIC, GENERIC_LOC, "general", "Canonical generic")
        _insert_media(connection, MOVIE, MOVIE_LOC, "movie", "A movie")
        _insert_media(connection, MOVIE_ID_MEDIA, MOVIE_ID_LOC, "general", "Has movie run")
        _insert_media(connection, WEBSITE, WEBSITE_LOC, "meme", None)
        _insert_media(connection, HISTORICAL, HISTORICAL_LOC, "general", None)
        _insert_media(connection, NULL_PROFILE, NULL_PROFILE_LOC, "general", "Null profile")
        _insert_analyzed_run(
            connection,
            GENERIC_RUN,
            GENERIC,
            GENERIC_LOC,
            completed_at_ms=100,
            title="Older success",
            tags=["Cats"],
            prompt_version="framenest-media-suggestion-v4",
        )
        _insert_failed_run(
            connection,
            GENERIC_FAIL,
            GENERIC,
            GENERIC_LOC,
            completed_at_ms=200,
        )
        _insert_analyzed_run(
            connection,
            MOVIE_RUN,
            MOVIE,
            MOVIE_LOC,
            completed_at_ms=150,
            title="Movie suggestion",
            tags=["Cats"],
        )
        _insert_analyzed_run(
            connection,
            MOVIE_ID_RUN,
            MOVIE_ID_MEDIA,
            MOVIE_ID_LOC,
            completed_at_ms=160,
            title="Movie identification",
            tags=["Cats"],
            analysis_definition="movie_identification",
            analysis_profile="movie_identification",
            prompt_version="framenest-movie-identification-prompt-v2",
        )
        _insert_analyzed_run(
            connection,
            WEBSITE_RUN,
            WEBSITE,
            WEBSITE_LOC,
            completed_at_ms=180,
            title="Website Analyze-by-AI",
            tags=["Dogs"],
        )
        historical_tags = [
            "Cats",
            "Dogs",
            "Birds",
            "Fish",
            "Cars",
            "Trees",
            "unknown-tag",
            "Feline",
        ]
        _insert_analyzed_run(
            connection,
            HISTORICAL_RUN,
            HISTORICAL,
            HISTORICAL_LOC,
            completed_at_ms=90,
            title="Historical v3",
            tags=historical_tags,
            prompt_version="framenest-media-suggestion-v3",
        )
        _insert_analyzed_run(
            connection,
            NULL_PROFILE_RUN,
            NULL_PROFILE,
            NULL_PROFILE_LOC,
            completed_at_ms=170,
            title="Null profile success",
            tags=["Cats"],
            analysis_profile=None,
        )


def _insert_media(
    connection,
    media_id: str,
    location_id: str,
    category: str,
    title: str | None,
    *,
    created_at_ms: int = 10,
) -> None:
    connection.execute(
        text(
            "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) "
            "VALUES (:id, 'video', :created, :created)"
        ),
        {"id": media_id, "created": created_at_ms},
    )
    connection.execute(
        text(
            "INSERT INTO physical_media_locations ("
            "id, media_id, library_id, relative_path, availability, "
            "observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms"
            ") VALUES (:id, :media, :library, :path, 'available', 8, NULL, 10, 10)"
        ),
        {
            "id": location_id,
            "media": media_id,
            "library": LIBRARY_ID,
            "path": f"{media_id}.mp4",
        },
    )
    connection.execute(
        text(
            "INSERT INTO media_metadata ("
            "media_id, display_title, description, created_at_ms, updated_at_ms, "
            "content_category, acquisition_source"
            ") VALUES (:id, :title, 'Desc', 10, 10, :category, 'manual_upload')"
        ),
        {"id": media_id, "title": title, "category": category},
    )


def _insert_x_save(
    connection,
    *,
    claim_id: str,
    asset_id: str,
    media_id: str,
    location_id: str,
    owner: str,
    post_id: str,
    title: str | None,
    stage_key: str,
    requested_content_category: str | None = "meme",
) -> None:
    connection.execute(
        text(
            "INSERT INTO x_post_claims ("
            "id, state, acquisition_source, submitted_url, canonical_url, "
            "x_post_id, extractor_key, created_by_login_key, title, "
            "discovered_asset_count, success_count, failure_count, "
            "created_at_ms, updated_at_ms, completed_at_ms, cleanup_state, "
            "cleanup_completed_at_ms, requested_content_category, version"
            ") VALUES ("
            ":id, 'completed', 'x_manual_claim', :url, :url, :post_id, 'X', "
            ":owner, :title, 1, 1, 0, 10, 20, 20, 'complete', 20, :category, 1)"
        ),
        {
            "id": claim_id,
            "url": f"https://x.com/a/status/{post_id}",
            "post_id": post_id,
            "owner": owner,
            "title": title,
            "category": requested_content_category,
        },
    )
    connection.execute(
        text(
            "INSERT INTO x_assets ("
            "id, claim_id, ordinal, media_type, expected_mime, state, stage_key, "
            "media_id, media_location_id, created_at_ms, updated_at_ms, "
            "completed_at_ms, cleanup_state, cleanup_completed_at_ms, version"
            ") VALUES ("
            ":id, :claim, 0, 'video', 'video/mp4', 'cataloged', :stage, "
            ":media, :location, 10, 20, 20, 'complete', 20, 1)"
        ),
        {
            "id": asset_id,
            "claim": claim_id,
            "stage": stage_key,
            "media": media_id,
            "location": location_id,
        },
    )


def _insert_analyzed_run(
    connection,
    run_id: str,
    media_id: str,
    location_id: str,
    *,
    completed_at_ms: int,
    title: str,
    tags: list[str],
    analysis_definition: str = "automatic_post_catalog",
    analysis_profile: str | None = "generic_media",
    prompt_version: str = "framenest-media-suggestion-v4",
    result_schema_version: str = "framenest-media-suggestion-result-v1",
    result_json: str | None = None,
) -> None:
    connection.execute(
        text(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "error_code, error_message, analysis_profile, created_at_ms, started_at_ms, "
            "completed_at_ms, version"
            ") VALUES ("
            ":id, :media, :location, :definition, 'analyzed', 1, "
            "'nvidia-nim', 'test-model', :prompt, "
            ":schema, :result, "
            "NULL, NULL, :profile, :completed, :completed, :completed, 2)"
        ),
        {
            "id": run_id,
            "media": media_id,
            "location": location_id,
            "definition": analysis_definition,
            "prompt": prompt_version,
            "schema": result_schema_version,
            "result": (
                _result_json(title=title, tags=tags)
                if result_json is None
                else result_json
            ),
            "profile": analysis_profile,
            "completed": completed_at_ms,
        },
    )


def _seed_assigned_extra_tags(
    connection, media_id: str, count: int, *, start_position: int = 0
) -> list[str]:
    keys: list[str] = []
    for index in range(1, count + 1):
        key = f"extra-{index:02d}"
        keys.append(key)
        connection.execute(
            text(
                "INSERT OR IGNORE INTO canonical_tags "
                "(key, display_name, created_at_ms, updated_at_ms) "
                "VALUES (:key, :name, 1, 1)"
            ),
            {"key": key, "name": f"Extra {index:02d}"},
        )
        connection.execute(
            text(
                "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                "VALUES (:media, :key, :position)"
            ),
            {
                "media": media_id,
                "key": key,
                "position": start_position + index - 1,
            },
        )
    return keys


def _insert_failed_run(
    connection, run_id: str, media_id: str, location_id: str, *, completed_at_ms: int
) -> None:
    connection.execute(
        text(
            "INSERT INTO media_analysis_runs ("
            "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
            "provider_id, model_id, prompt_version, result_schema_version, result_json, "
            "error_code, error_message, analysis_profile, created_at_ms, started_at_ms, "
            "completed_at_ms, version"
            ") VALUES ("
            ":id, :media, :location, 'automatic_post_catalog', 'failed', 1, "
            "NULL, NULL, 'framenest-media-suggestion-v4', NULL, NULL, "
            "'PROVIDER_UNAVAILABLE', 'unavailable', 'generic_media', "
            ":completed, :completed, :completed, 2)"
        ),
        {
            "id": run_id,
            "media": media_id,
            "location": location_id,
            "completed": completed_at_ms,
        },
    )


def test_latest_successful_generic_exclusions_and_unopened_empty_table(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        page = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        ids = [item.media_id for item in page.items]
        assert WEBSITE in ids
        assert GENERIC in ids
        assert NULL_PROFILE in ids
        assert HISTORICAL in ids
        assert MOVIE not in ids
        assert MOVIE_ID_MEDIA not in ids
        generic = next(item for item in page.items if item.media_id == GENERIC)
        assert generic.analysis_run_id == GENERIC_RUN
        assert generic.title == "Canonical generic"
        assert generic.unopened is True
        assert page.unopened_count == len(page.items)
        assert all(item.unopened for item in page.items)
        website = next(item for item in page.items if item.media_id == WEBSITE)
        assert website.title == "Website Analyze-by-AI"
    finally:
        dispose_engine(engine)


def test_mixed_inbox_includes_only_owned_pending_and_analyzed_wins(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            _insert_media(
                connection,
                PENDING,
                PENDING_LOC,
                "meme",
                None,
                created_at_ms=180,
            )
            _insert_x_save(
                connection,
                claim_id=PENDING_CLAIM,
                asset_id=PENDING_ASSET,
                media_id=PENDING,
                location_id=PENDING_LOC,
                owner=ADMIN_KEY,
                post_id="123456789",
                title="Pending claim title",
                stage_key="a" * 32,
            )
            _insert_failed_run(
                connection,
                PENDING_FAIL,
                PENDING,
                PENDING_LOC,
                completed_at_ms=190,
            )
            _insert_media(
                connection,
                OTHER_PENDING,
                OTHER_PENDING_LOC,
                "meme",
                "Other private title",
                created_at_ms=300,
            )
            _insert_x_save(
                connection,
                claim_id=OTHER_PENDING_CLAIM,
                asset_id=OTHER_PENDING_ASSET,
                media_id=OTHER_PENDING,
                location_id=OTHER_PENDING_LOC,
                owner=OTHER_KEY,
                post_id="987654321",
                title="Other claim title",
                stage_key="b" * 32,
            )
            _insert_x_save(
                connection,
                claim_id=DEDUP_CLAIM,
                asset_id=DEDUP_ASSET,
                media_id=GENERIC,
                location_id=GENERIC_LOC,
                owner=ADMIN_KEY,
                post_id="111222333",
                title="Duplicate pending title",
                stage_key="c" * 32,
            )
            _insert_x_save(
                connection,
                claim_id=MOVIE_PENDING_CLAIM,
                asset_id=MOVIE_PENDING_ASSET,
                media_id=MOVIE,
                location_id=MOVIE_LOC,
                owner=ADMIN_KEY,
                post_id="444555666",
                title="Private movie title",
                stage_key="d" * 32,
            )

        page = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        ids = [item.media_id for item in page.items]
        assert ids.count(GENERIC) == 1
        assert PENDING not in ids
        assert OTHER_PENDING not in ids
        assert MOVIE not in ids
        generic = next(item for item in page.items if item.media_id == GENERIC)
        assert generic.analyzed is True
        assert generic.analysis_run_id == GENERIC_RUN
        assert WEBSITE in ids
        assert GENERIC in ids
        assert page.unopened_count == sum(item.analyzed for item in page.items)

        own = repository.list_own_history(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        own_ids = [item.media_id for item in own.items]
        assert PENDING in own_ids
        assert OTHER_PENDING not in own_ids
        assert MOVIE not in own_ids
        assert WEBSITE not in own_ids
        pending = next(item for item in own.items if item.media_id == PENDING)
        assert pending.title == "Pending claim title"
        assert pending.created_at_ms == 180
        assert pending.analyzed is False
        assert pending.analysis_run_id is None
        assert pending.completed_at_ms is None
        assert pending.unopened is False
        assert own.unopened_count == sum(
            item.analyzed and item.unopened for item in own.items
        )

        other_inbox = repository.list_inbox(
            actor_login_key=OTHER_KEY, limit=25, cursor=None
        )
        other_inbox_ids = [item.media_id for item in other_inbox.items]
        assert GENERIC in other_inbox_ids
        assert PENDING not in other_inbox_ids
        assert OTHER_PENDING not in other_inbox_ids
        assert other_inbox.unopened_count == page.unopened_count

        other_own = repository.list_own_history(
            actor_login_key=OTHER_KEY, limit=25, cursor=None
        )
        other_own_ids = [item.media_id for item in other_own.items]
        assert OTHER_PENDING in other_own_ids
        assert PENDING not in other_own_ids
        assert GENERIC not in other_own_ids
        assert WEBSITE not in other_own_ids
    finally:
        dispose_engine(engine)


def test_mixed_inbox_includes_omitted_category_owned_general_saves(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            _insert_media(
                connection,
                OMITTED,
                OMITTED_LOC,
                "general",
                None,
                created_at_ms=210,
            )
            _insert_x_save(
                connection,
                claim_id=OMITTED_CLAIM,
                asset_id=OMITTED_ASSET,
                media_id=OMITTED,
                location_id=OMITTED_LOC,
                owner=ADMIN_KEY,
                post_id="555666777",
                title="Omitted category title",
                stage_key="e" * 32,
                requested_content_category=None,
            )
            _insert_media(
                connection,
                CLAIM_MOVIE,
                CLAIM_MOVIE_LOC,
                "general",
                None,
                created_at_ms=220,
            )
            _insert_x_save(
                connection,
                claim_id=CLAIM_MOVIE_CLAIM,
                asset_id=CLAIM_MOVIE_ASSET,
                media_id=CLAIM_MOVIE,
                location_id=CLAIM_MOVIE_LOC,
                owner=ADMIN_KEY,
                post_id="666777888",
                title="Movie claim title",
                stage_key="f" * 32,
                requested_content_category="movie",
            )
            _insert_media(
                connection,
                OTHER_OMITTED,
                OTHER_OMITTED_LOC,
                "general",
                None,
                created_at_ms=230,
            )
            _insert_x_save(
                connection,
                claim_id=OTHER_OMITTED_CLAIM,
                asset_id=OTHER_OMITTED_ASSET,
                media_id=OTHER_OMITTED,
                location_id=OTHER_OMITTED_LOC,
                owner=OTHER_KEY,
                post_id="777888999",
                title="Other omitted title",
                stage_key="1" * 32,
                requested_content_category=None,
            )
            _insert_x_save(
                connection,
                claim_id=DEDUP_CLAIM,
                asset_id=DEDUP_ASSET,
                media_id=GENERIC,
                location_id=GENERIC_LOC,
                owner=ADMIN_KEY,
                post_id="111222334",
                title="Duplicate omitted title",
                stage_key="2" * 32,
                requested_content_category=None,
            )
            _insert_x_save(
                connection,
                claim_id=MOVIE_PENDING_CLAIM,
                asset_id=MOVIE_PENDING_ASSET,
                media_id=MOVIE,
                location_id=MOVIE_LOC,
                owner=ADMIN_KEY,
                post_id="444555667",
                title="Private movie omitted title",
                stage_key="3" * 32,
                requested_content_category=None,
            )

        page = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        ids = [item.media_id for item in page.items]
        assert OMITTED not in ids
        assert CLAIM_MOVIE not in ids
        assert OTHER_OMITTED not in ids
        assert MOVIE not in ids
        assert ids.count(GENERIC) == 1
        generic = next(item for item in page.items if item.media_id == GENERIC)
        assert generic.analyzed is True
        assert generic.analysis_run_id == GENERIC_RUN
        assert page.unopened_count == sum(item.analyzed for item in page.items)

        own = repository.list_own_history(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        own_ids = [item.media_id for item in own.items]
        assert OMITTED in own_ids
        assert CLAIM_MOVIE not in own_ids
        assert OTHER_OMITTED not in own_ids
        assert MOVIE not in own_ids
        assert own_ids.count(GENERIC) == 1
        omitted = next(item for item in own.items if item.media_id == OMITTED)
        assert omitted.title == "Omitted category title"
        assert omitted.created_at_ms == 210
        assert omitted.analyzed is False
        assert omitted.analysis_run_id is None
        assert omitted.completed_at_ms is None
        assert omitted.unopened is False

        other_own = repository.list_own_history(
            actor_login_key=OTHER_KEY, limit=25, cursor=None
        )
        other_ids = [item.media_id for item in other_own.items]
        assert OTHER_OMITTED in other_ids
        assert OMITTED not in other_ids
        assert CLAIM_MOVIE not in other_ids
    finally:
        dispose_engine(engine)


def test_actor_opened_rows_are_isolated(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companion_review_open_states "
                    "(actor_login_key, media_id, opened_run_id, opened_at_ms) "
                    "VALUES (:actor, :media, :run, 300)"
                ),
                {"actor": ADMIN_KEY, "media": GENERIC, "run": GENERIC_RUN},
            )
        admin_page = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        other_page = repository.list_inbox(
            actor_login_key=OTHER_KEY, limit=25, cursor=None
        )
        admin_generic = next(
            item for item in admin_page.items if item.media_id == GENERIC
        )
        other_generic = next(
            item for item in other_page.items if item.media_id == GENERIC
        )
        assert admin_generic.unopened is False
        assert other_generic.unopened is True
        assert admin_page.unopened_count == other_page.unopened_count - 1
    finally:
        dispose_engine(engine)


def test_own_history_opened_isolation_does_not_use_global_unopened_count(
    tmp_path: Path,
) -> None:
    alice = "alice@example.com"
    bob = "bob@example.com"
    alice_media = "51515151-5151-4515-8515-515151515151"
    alice_loc = "52525252-5252-4525-8525-525252525252"
    alice_run = "53535353-5353-4535-8535-535353535353"
    alice_claim = "54545454-5454-4545-8545-545454545454"
    alice_asset = "55555555-5555-4555-8555-555555555555"
    bob_media = "56565656-5656-4565-8565-565656565656"
    bob_loc = "57575757-5757-4575-8575-575757575757"
    bob_run = "58585858-5858-4585-8585-585858585858"
    bob_claim = "59595959-5959-4595-8595-595959595959"
    bob_asset = "60606060-6060-4606-8606-606060606060"
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            _insert_media(connection, alice_media, alice_loc, "meme", "Alice title")
            _insert_x_save(
                connection,
                claim_id=alice_claim,
                asset_id=alice_asset,
                media_id=alice_media,
                location_id=alice_loc,
                owner=alice,
                post_id="111000111",
                title="Alice claim",
                stage_key="a" * 32,
            )
            _insert_analyzed_run(
                connection,
                alice_run,
                alice_media,
                alice_loc,
                completed_at_ms=500,
                title="Alice analyzed",
                tags=["Cats"],
            )
            _insert_media(connection, bob_media, bob_loc, "meme", "Bob title")
            _insert_x_save(
                connection,
                claim_id=bob_claim,
                asset_id=bob_asset,
                media_id=bob_media,
                location_id=bob_loc,
                owner=bob,
                post_id="111000222",
                title="Bob claim",
                stage_key="b" * 32,
            )
            _insert_analyzed_run(
                connection,
                bob_run,
                bob_media,
                bob_loc,
                completed_at_ms=510,
                title="Bob analyzed",
                tags=["Dogs"],
            )
        alice_before = repository.list_own_history(
            actor_login_key=alice, limit=25, cursor=None
        )
        bob_before = repository.list_own_history(
            actor_login_key=bob, limit=25, cursor=None
        )
        admin_before = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        assert {item.media_id for item in alice_before.items} == {alice_media}
        assert {item.media_id for item in bob_before.items} == {bob_media}
        alice_item = alice_before.items[0]
        assert alice_item.analyzed is True
        assert alice_item.unopened is True
        assert alice_before.unopened_count == 1
        assert bob_before.unopened_count == 1
        admin_alice = next(
            item for item in admin_before.items if item.media_id == alice_media
        )
        assert admin_alice.unopened is True

        opened = repository.mark_opened(
            media_id=MediaId.from_string(alice_media),
            actor_login_key=alice,
            analysis_run_id=MediaId.from_string(alice_run),
            now_ms=600,
            require_owner=True,
        )
        assert opened.unopened is False
        alice_after = repository.list_own_history(
            actor_login_key=alice, limit=25, cursor=None
        )
        bob_after = repository.list_own_history(
            actor_login_key=bob, limit=25, cursor=None
        )
        admin_after = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        assert alice_after.items[0].unopened is False
        assert alice_after.unopened_count == 0
        assert bob_after.items[0].unopened is True
        assert bob_after.unopened_count == 1
        admin_alice_after = next(
            item for item in admin_after.items if item.media_id == alice_media
        )
        assert admin_alice_after.unopened is True

        admin_opened = repository.mark_opened(
            media_id=MediaId.from_string(alice_media),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(alice_run),
            now_ms=700,
        )
        alice_after_admin = repository.list_own_history(
            actor_login_key=alice, limit=25, cursor=None
        )
        assert alice_after_admin.items[0].unopened is False
        assert alice_after_admin.unopened_count == 0

        with pytest.raises(CompanionReviewMediaNotFoundError):
            repository.mark_opened(
                media_id=MediaId.from_string(bob_media),
                actor_login_key=alice,
                analysis_run_id=MediaId.from_string(bob_run),
                now_ms=800,
                require_owner=True,
            )
        with pytest.raises(CompanionReviewMediaNotFoundError):
            repository.mark_opened(
                media_id=MediaId.from_string(
                    "99999999-9999-4999-8999-999999999999"
                ),
                actor_login_key=alice,
                analysis_run_id=MediaId.from_string(alice_run),
                now_ms=800,
                require_owner=True,
            )
    finally:
        dispose_engine(engine)


def test_keyset_pagination_is_stable(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            for index in range(30):
                media_id = f"{index + 10:08x}-0000-4000-8000-{index + 10:012x}"
                location_id = f"{index + 110:08x}-0000-4000-8000-{index + 110:012x}"
                run_id = f"{index + 210:08x}-0000-4000-8000-{index + 210:012x}"
                _insert_media(
                    connection, media_id, location_id, "general", f"Page {index}"
                )
                _insert_analyzed_run(
                    connection,
                    run_id,
                    media_id,
                    location_id,
                    completed_at_ms=1000 + index,
                    title=f"Page {index}",
                    tags=["Cats"],
                )
        first = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        assert len(first.items) == 25
        assert first.next_cursor is not None
        second = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=_decode_cursor_tuple(first.next_cursor)
        )
        first_ids = {item.media_id for item in first.items}
        second_ids = {item.media_id for item in second.items}
        assert first_ids.isdisjoint(second_ids)
        assert first.unopened_count == second.unopened_count
        assert first.unopened_count > 25
    finally:
        dispose_engine(engine)


def test_history_maps_historical_tags_and_movie_detail_is_excluded(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        detail = repository.get_detail(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            limit=25,
            cursor=None,
        )
        assert detail.suggestions
        tags = detail.suggestions[0].tags
        statuses = [item.status for item in tags]
        assert MappedTagStatus.MAPPED in statuses
        assert MappedTagStatus.LEGACY_LIMIT in statuses
        assert MappedTagStatus.UNKNOWN in statuses
        assert MappedTagStatus.AMBIGUOUS in statuses
        assert all(detail.field_sources[name] is None for name in detail.field_sources)
        assert detail.tag_sources == {}
        with pytest.raises(CompanionReviewMovieExcludedError):
            repository.get_detail(
                media_id=MediaId.from_string(MOVIE),
                actor_login_key=ADMIN_KEY,
                limit=25,
                cursor=None,
            )
    finally:
        dispose_engine(engine)


def test_corrupt_result_json_does_not_drop_inbox_page(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE media_analysis_runs SET result_json = '{not-json' "
                    "WHERE id = :id"
                ),
                {"id": WEBSITE_RUN},
            )
        page = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        ids = [item.media_id for item in page.items]
        assert WEBSITE in ids
        assert GENERIC in ids
        website = next(item for item in page.items if item.media_id == WEBSITE)
        assert website.analyzed is True
        assert website.unopened is True
        assert website.title == "Untitled media"
        assert website.analysis_run_id == WEBSITE_RUN
        generic = next(item for item in page.items if item.media_id == GENERIC)
        assert generic.title == "Canonical generic"
        with pytest.raises(CompanionReviewStoredResultError):
            repository.get_detail(
                media_id=MediaId.from_string(WEBSITE),
                actor_login_key=ADMIN_KEY,
                limit=25,
                cursor=None,
            )
    finally:
        dispose_engine(engine)


def test_suggestion_ready_lists_without_v1_schema_and_survives_decode_failure(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        baseline = repository.list_inbox(
            actor_login_key=ADMIN_KEY, limit=25, cursor=None
        )
        baseline_ids = {item.media_id for item in baseline.items}
        baseline_unopened = baseline.unopened_count
        with engine.begin() as connection:
            _insert_media(
                connection,
                SUGGESTION_READY,
                SUGGESTION_READY_LOC,
                "general",
                None,
                created_at_ms=400,
            )
            _insert_x_save(
                connection,
                claim_id=SUGGESTION_READY_CLAIM,
                asset_id=SUGGESTION_READY_ASSET,
                media_id=SUGGESTION_READY,
                location_id=SUGGESTION_READY_LOC,
                owner=ADMIN_KEY,
                post_id="555000111",
                title="Owned X alias",
                stage_key="c" * 32,
                requested_content_category=None,
            )
            _insert_analyzed_run(
                connection,
                SUGGESTION_READY_RUN,
                SUGGESTION_READY,
                SUGGESTION_READY_LOC,
                completed_at_ms=410,
                title="Stored non-v1 title",
                tags=["Cats"],
                result_schema_version="not-v1",
            )
            _insert_media(
                connection,
                UNDECODABLE,
                UNDECODABLE_LOC,
                "meme",
                "Canonical undecodable",
                created_at_ms=420,
            )
            _insert_x_save(
                connection,
                claim_id=UNDECODABLE_CLAIM,
                asset_id=UNDECODABLE_ASSET,
                media_id=UNDECODABLE,
                location_id=UNDECODABLE_LOC,
                owner=ADMIN_KEY,
                post_id="555000222",
                title="Undecodable alias",
                stage_key="d" * 32,
            )
            _insert_analyzed_run(
                connection,
                UNDECODABLE_RUN,
                UNDECODABLE,
                UNDECODABLE_LOC,
                completed_at_ms=430,
                title="Ignored corrupt title",
                tags=["Dogs"],
                result_json=json.dumps(
                    {
                        "title": "Would fail tags",
                        "description": "A description.",
                        "tags": {"no": "list"},
                    }
                ),
            )
        page = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        ids = [item.media_id for item in page.items]
        assert SUGGESTION_READY in ids
        assert UNDECODABLE in ids
        assert MOVIE not in ids
        assert MOVIE_ID_MEDIA not in ids
        assert baseline_ids.issubset(set(ids))
        ready = next(item for item in page.items if item.media_id == SUGGESTION_READY)
        assert ready.analyzed is True
        assert ready.unopened is True
        assert ready.analysis_run_id == SUGGESTION_READY_RUN
        assert ready.title == "Stored non-v1 title"
        undecodable = next(item for item in page.items if item.media_id == UNDECODABLE)
        assert undecodable.analyzed is True
        assert undecodable.unopened is True
        assert undecodable.analysis_run_id == UNDECODABLE_RUN
        assert undecodable.title == "Canonical undecodable"
        assert page.unopened_count == baseline_unopened + 2
        other = repository.list_inbox(
            actor_login_key=OTHER_KEY, limit=25, cursor=None
        )
        other_ids = [item.media_id for item in other.items]
        assert SUGGESTION_READY in other_ids
        assert UNDECODABLE in other_ids
    finally:
        dispose_engine(engine)


def _decode_cursor_tuple(cursor: str) -> tuple[int, bool, str]:
    parsed = decode_companion_review_inbox_cursor(cursor)
    assert parsed is not None
    return parsed


def test_mark_opened_is_actor_scoped_monotonic_and_idempotent(tmp_path: Path) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            _insert_analyzed_run(
                connection,
                GENERIC_NEWER,
                GENERIC,
                GENERIC_LOC,
                completed_at_ms=300,
                title="Newer success",
                tags=["Cats"],
            )
        first = repository.mark_opened(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_NEWER),
            now_ms=400,
        )
        assert first.opened_run_id == GENERIC_NEWER
        assert first.unopened is False
        second = repository.mark_opened(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_RUN),
            now_ms=500,
        )
        assert second.opened_run_id == GENERIC_NEWER
        assert second.opened_at_ms == 400
        assert second.unopened is False
        refreshed = repository.mark_opened(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_NEWER),
            now_ms=600,
        )
        assert refreshed.opened_run_id == GENERIC_NEWER
        assert refreshed.opened_at_ms == 600
        other = repository.list_inbox(actor_login_key=OTHER_KEY, limit=25, cursor=None)
        other_generic = next(item for item in other.items if item.media_id == GENERIC)
        assert other_generic.unopened is True
        admin = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        admin_generic = next(item for item in admin.items if item.media_id == GENERIC)
        assert admin_generic.unopened is False
        older_only = repository.mark_opened(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            now_ms=700,
        )
        assert older_only.unopened is False
        with pytest.raises(CompanionReviewRunNotEligibleError):
            repository.mark_opened(
                media_id=MediaId.from_string(GENERIC),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(GENERIC_FAIL),
                now_ms=800,
            )
        with pytest.raises(CompanionReviewMovieExcludedError):
            repository.mark_opened(
                media_id=MediaId.from_string(MOVIE),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(MOVIE_RUN),
                now_ms=800,
            )
    finally:
        dispose_engine(engine)


def test_apply_review_preserves_unselected_fields_and_unions_tags(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        title_only = repository.apply_review(
            media_id=MediaId.from_string(WEBSITE),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(WEBSITE_RUN),
            fields=("display_title",),
            tag_keys=(),
            now_ms=400,
        )
        assert title_only.metadata_status == "updated"
        assert title_only.canonical.display_title == "Website Analyze-by-AI"
        assert title_only.canonical.description == "Desc"
        assert title_only.canonical.tags == ()
        assert title_only.canonical.tag_sources == {}
        assert title_only.publication.status == "not_ready"
        assert title_only.publication.state == "unpublished"
        assert title_only.publication.ready is False
        with engine.connect() as connection:
            website_publications = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_content_publications "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
        assert int(website_publications) == 0
        inbox = repository.list_inbox(actor_login_key=ADMIN_KEY, limit=25, cursor=None)
        website = next(item for item in inbox.items if item.media_id == WEBSITE)
        assert website.unopened is True
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                    "VALUES (:media, 'dogs', 0)"
                ),
                {"media": GENERIC},
            )
        replaced = repository.apply_review(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_RUN),
            fields=("tags",),
            tag_keys=("cats",),
            now_ms=401,
        )
        assert [tag.key for tag in replaced.canonical.tags] == ["dogs", "cats"]
        assert replaced.canonical.display_title == "Canonical generic"
        assert replaced.canonical.description == "Desc"
        assert replaced.publication.status == "requires_administrator_publish"
        assert replaced.publication.state == "unpublished"
        assert replaced.publication.origin is None
        assert replaced.publication.published_at_ms is None
        assert replaced.publication.ready is True
        assert replaced.canonical.field_sources["tags"] is not None
        assert replaced.canonical.field_sources["display_title"] is None
        assert set(replaced.canonical.tag_sources) == {"cats"}
        assert "dogs" not in replaced.canonical.tag_sources
        assert replaced.canonical.tag_sources["cats"].analysis_run_id == GENERIC_RUN
        with engine.connect() as connection:
            category = connection.execute(
                text(
                    "SELECT content_category, acquisition_source FROM media_metadata "
                    "WHERE media_id = :media"
                ),
                {"media": GENERIC},
            ).first()
            tag_count = connection.execute(text("SELECT COUNT(*) FROM canonical_tags")).scalar_one()
            publications = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_content_publications "
                    "WHERE media_id = :media"
                ),
                {"media": GENERIC},
            ).scalar_one()
        assert category is not None
        assert category[0] == "general"
        assert category[1] == "manual_upload"
        assert int(tag_count) == 8
        assert int(publications) == 0
        repeat = repository.apply_review(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_RUN),
            fields=("tags",),
            tag_keys=("cats",),
            now_ms=402,
        )
        assert repeat.publication.status == "requires_administrator_publish"
        assert repeat.publication.state == "unpublished"
        assert repeat.publication.origin is None
        assert [tag.key for tag in repeat.canonical.tags] == ["dogs", "cats"]
        assert set(repeat.canonical.tag_sources) == {"cats"}
        assert repeat.canonical.field_sources["tags"] is not None
        assert repeat.canonical.field_sources["tags"].applied_at_ms == 402
    finally:
        dispose_engine(engine)


def test_apply_review_leaves_existing_publication_and_loads_historical_origin(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) "
                    "VALUES (:media, 77, 'admin_explicit')"
                ),
                {"media": GENERIC},
            )
            connection.execute(
                text(
                    "INSERT INTO media_content_publications "
                    "(media_id, published_at_ms, publication_origin) "
                    "VALUES (:media, 88, 'companion_review')"
                ),
                {"media": HISTORICAL},
            )
        already = repository.apply_review(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_RUN),
            fields=("display_title",),
            tag_keys=(),
            now_ms=500,
        )
        assert already.metadata_status == "updated"
        assert already.canonical.display_title == "Older success"
        assert already.publication.status == "already_published"
        assert already.publication.state == "published"
        assert already.publication.origin == ContentPublicationOrigin.ADMIN_EXPLICIT.value
        assert already.publication.published_at_ms == 77
        historical_detail = repository.get_detail(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            limit=25,
            cursor=None,
        )
        assert historical_detail.publication is not None
        assert (
            historical_detail.publication.publication_origin
            is ContentPublicationOrigin.COMPANION_REVIEW
        )
        assert historical_detail.publication.published_at_ms == 88
        historical_apply = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            fields=("tags",),
            tag_keys=("cats", "birds"),
            now_ms=501,
        )
        assert historical_apply.publication.status == "already_published"
        assert (
            historical_apply.publication.origin
            == ContentPublicationOrigin.COMPANION_REVIEW.value
        )
        assert historical_apply.publication.published_at_ms == 88
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT media_id, published_at_ms, publication_origin "
                    "FROM media_content_publications ORDER BY media_id"
                )
            ).all()
        assert [(row[0], int(row[1]), row[2]) for row in rows] == [
            (GENERIC, 77, "admin_explicit"),
            (HISTORICAL, 88, "companion_review"),
        ]
    finally:
        dispose_engine(engine)


def test_apply_review_rejects_stale_mapping_zero_union_and_movie_race(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with pytest.raises(CompanionReviewStaleMappingError):
            repository.apply_review(
                media_id=MediaId.from_string(HISTORICAL),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
                fields=("tags",),
                tag_keys=("dogs", "cats"),
                now_ms=10,
            )
        with pytest.raises(CompanionReviewStaleMappingError):
            repository.apply_review(
                media_id=MediaId.from_string(HISTORICAL),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
                fields=("tags",),
                tag_keys=("trees",),
                now_ms=10,
            )
        accepted = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            fields=("tags",),
            tag_keys=("cats", "birds"),
            now_ms=11,
        )
        assert [tag.key for tag in accepted.canonical.tags] == ["cats", "birds"]
        with engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE media_metadata SET content_category = 'movie' "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            )
        with pytest.raises(CompanionReviewMovieExcludedError):
            repository.apply_review(
                media_id=MediaId.from_string(WEBSITE),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(WEBSITE_RUN),
                fields=("display_title",),
                tag_keys=(),
                now_ms=12,
            )
        with engine.connect() as connection:
            title = connection.execute(
                text("SELECT display_title FROM media_metadata WHERE media_id = :media"),
                {"media": WEBSITE},
            ).scalar_one()
            receipts = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_field_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            publications = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_content_publications "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
        assert title is None
        assert int(receipts) == 0
        assert int(publications) == 0
    finally:
        dispose_engine(engine)


def test_apply_review_same_value_newer_receipt_and_atomic_rollback(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        first = repository.apply_review(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_RUN),
            fields=("display_title",),
            tag_keys=(),
            now_ms=20,
        )
        assert first.canonical.field_sources["display_title"] is not None
        assert first.canonical.field_sources["display_title"].analysis_run_id == GENERIC_RUN
        with engine.begin() as connection:
            _insert_analyzed_run(
                connection,
                GENERIC_NEWER,
                GENERIC,
                GENERIC_LOC,
                completed_at_ms=350,
                title="Older success",
                tags=["Cats"],
            )
        second = repository.apply_review(
            media_id=MediaId.from_string(GENERIC),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(GENERIC_NEWER),
            fields=("display_title",),
            tag_keys=(),
            now_ms=21,
        )
        receipt = second.canonical.field_sources["display_title"]
        assert receipt is not None
        assert receipt.analysis_run_id == GENERIC_NEWER
        assert receipt.applied_at_ms == 21
        assert second.canonical.display_title == "Older success"
        from unittest.mock import patch

        from sqlalchemy.exc import SQLAlchemyError

        def _boom(*args, **kwargs):
            raise SQLAlchemyError("forced failure")

        with patch(
            "kronika.infrastructure.persistence.companion_review_repository._upsert_field_source",
            side_effect=_boom,
        ):
            with pytest.raises(FrameNestCompanionReviewRepositoryError):
                repository.apply_review(
                    media_id=MediaId.from_string(WEBSITE),
                    actor_login_key=ADMIN_KEY,
                    analysis_run_id=MediaId.from_string(WEBSITE_RUN),
                    fields=("display_title", "description", "tags"),
                    tag_keys=("dogs",),
                    now_ms=22,
                )
        with engine.connect() as connection:
            title = connection.execute(
                text("SELECT display_title FROM media_metadata WHERE media_id = :media"),
                {"media": WEBSITE},
            ).scalar_one()
            receipts = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_field_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            publications = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_content_publications "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
        assert title is None
        assert int(receipts) == 0
        assert int(publications) == 0
    finally:
        dispose_engine(engine)


def test_apply_review_unions_tags_and_records_new_tag_sources_only(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO media_canonical_tags (media_id, tag_key, position) "
                    "VALUES (:media, 'trees', 0)"
                ),
                {"media": HISTORICAL},
            )
        first = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            fields=("tags",),
            tag_keys=("cats", "dogs", "birds", "fish", "cars"),
            now_ms=50,
        )
        assert [tag.key for tag in first.canonical.tags] == [
            "trees",
            "cats",
            "dogs",
            "birds",
            "fish",
            "cars",
        ]
        assert first.canonical.field_sources["tags"] is not None
        assert set(first.canonical.tag_sources) == {
            "cats",
            "dogs",
            "birds",
            "fish",
            "cars",
        }
        assert "trees" not in first.canonical.tag_sources
        duplicate = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            fields=("tags",),
            tag_keys=("cats", "birds"),
            now_ms=51,
        )
        assert [tag.key for tag in duplicate.canonical.tags] == [
            "trees",
            "cats",
            "dogs",
            "birds",
            "fish",
            "cars",
        ]
        assert duplicate.canonical.tag_sources["cats"].applied_at_ms == 50
        assert duplicate.canonical.field_sources["tags"] is not None
        assert duplicate.canonical.field_sources["tags"].applied_at_ms == 51
        with engine.begin() as connection:
            _insert_analyzed_run(
                connection,
                HISTORICAL_NEWER,
                HISTORICAL,
                HISTORICAL_LOC,
                completed_at_ms=91,
                title="Newer historical",
                tags=["Cats", "Dogs", "Birds", "Fish", "Cars"],
                prompt_version="framenest-media-suggestion-v4",
            )
        second = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_NEWER),
            fields=("tags",),
            tag_keys=("cats", "dogs"),
            now_ms=52,
        )
        assert second.canonical.tag_sources["cats"].analysis_run_id == HISTORICAL_RUN
        assert second.canonical.tag_sources["dogs"].analysis_run_id == HISTORICAL_RUN
        detail = repository.get_detail(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            limit=25,
            cursor=None,
        )
        assert set(detail.tag_sources) == set(first.canonical.tag_sources)
        assert detail.field_sources["tags"] is not None
    finally:
        dispose_engine(engine)


def test_apply_review_tag_limit_success_and_atomic_overflow(
    tmp_path: Path,
) -> None:
    repository, engine = _repository(tmp_path)
    try:
        with engine.begin() as connection:
            _seed_assigned_extra_tags(connection, HISTORICAL, 27)
        exact = repository.apply_review(
            media_id=MediaId.from_string(HISTORICAL),
            actor_login_key=ADMIN_KEY,
            analysis_run_id=MediaId.from_string(HISTORICAL_RUN),
            fields=("tags",),
            tag_keys=("cats", "dogs", "birds", "fish", "cars"),
            now_ms=60,
        )
        assert len(exact.canonical.tags) == 32
        assert exact.canonical.field_sources["tags"] is not None
        assert len(exact.canonical.tag_sources) == 5
        with engine.begin() as connection:
            _seed_assigned_extra_tags(connection, WEBSITE, 32)
        with engine.connect() as connection:
            before_tags = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_canonical_tags WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            before_sources = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_tag_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            before_receipts = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_field_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            before_title = connection.execute(
                text("SELECT display_title FROM media_metadata WHERE media_id = :media"),
                {"media": WEBSITE},
            ).scalar_one()
        assert int(before_tags) == 32
        assert int(before_sources) == 0
        assert int(before_receipts) == 0
        with pytest.raises(CompanionReviewTagLimitConflictError):
            repository.apply_review(
                media_id=MediaId.from_string(WEBSITE),
                actor_login_key=ADMIN_KEY,
                analysis_run_id=MediaId.from_string(WEBSITE_RUN),
                fields=("tags",),
                tag_keys=("dogs",),
                now_ms=61,
            )
        with engine.connect() as connection:
            after_tags = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_canonical_tags WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            after_sources = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_tag_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            after_receipts = connection.execute(
                text(
                    "SELECT COUNT(*) FROM companion_review_field_sources "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
            after_title = connection.execute(
                text("SELECT display_title FROM media_metadata WHERE media_id = :media"),
                {"media": WEBSITE},
            ).scalar_one()
            publications = connection.execute(
                text(
                    "SELECT COUNT(*) FROM media_content_publications "
                    "WHERE media_id = :media"
                ),
                {"media": WEBSITE},
            ).scalar_one()
        assert int(after_tags) == 32
        assert int(after_sources) == 0
        assert int(after_receipts) == 0
        assert after_title == before_title
        assert int(publications) == 0
    finally:
        dispose_engine(engine)
