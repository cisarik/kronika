"""Focused create-race and mixed-catalog category conflict tests."""

from __future__ import annotations

import types

import pytest
from sqlalchemy import create_engine, text

from tests.support.kronika_identity import expected

from framenest.application.x_acquisition import (
    XAcquisitionCategoryConflictError,
    XAcquisitionRequestService,
    XRequestLimits,
)
from framenest.domain.identities import MediaId
from framenest.domain.media_classification import ContentCategory
from framenest.domain.x_acquisition import XAssetState, XPostClaim
from framenest.infrastructure.persistence.catalog_schema import metadata
from framenest.infrastructure.persistence.x_acquisition_claim_repository import (
    SqliteXAcquisitionClaimRepository,
)

URL = "https://x.com/author/status/987654321"


class _RaceRepository:
    def __init__(self, inner: SqliteXAcquisitionClaimRepository, winner: XPostClaim) -> None:
        self._inner = inner
        self._winner = winner

    def find_owned_successful_by_post_id(self, **kwargs):
        return None

    def find_active_by_post_id(self, **kwargs):
        return None

    def create_or_get_active(self, claim: XPostClaim):
        return self._winner, False

    def count_active_for_requester(self, **kwargs):
        return 0

    def count_global_active_ordinary(self):
        return 0

    def count_submits_since(self, **kwargs):
        return 0

    def count_failed_transitions_since(self, **kwargs):
        return 0

    def __getattr__(self, name: str):
        return getattr(self._inner, name)


class _SuccessfulMixedRepository:
    def __init__(self, claim: XPostClaim, categories: dict[str, ContentCategory]) -> None:
        self._claim = claim
        self._categories = categories

    def find_owned_successful_by_post_id(self, **kwargs):
        return self._claim

    def list_assets_for_post(self, claim_id):
        assets = []
        for index, media_id in enumerate(self._categories):
            assets.append(
                types.SimpleNamespace(
                    state=XAssetState.CATALOGED,
                    media_id=MediaId.from_string(media_id),
                    ordinal=index,
                )
            )
        return tuple(assets)


class _MixedMetadata:
    def __init__(self, mapping: dict[str, ContentCategory]) -> None:
        self._mapping = mapping

    def get_media_metadata(self, media_id: MediaId):
        category = self._mapping.get(media_id.to_string())
        if category is None:
            return types.SimpleNamespace(persisted=False, content_category=None)
        return types.SimpleNamespace(persisted=True, content_category=category)


def _limits() -> XRequestLimits:
    return XRequestLimits(
        max_active_per_requester=8,
        max_global_active=8,
        max_submits_per_hour=20,
        max_failed_per_24h=20,
        free_space_bytes=lambda: 10_737_418_240,
    )


def test_create_race_compares_winner_category_before_active_reuse() -> None:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    metadata.create_all(connection)
    connection.commit()
    try:
        inner = SqliteXAcquisitionClaimRepository(engine)
        winner = XPostClaim.new(
            submitted_url=URL,
            now_ms=10,
            created_by_login_key="alice",
            requested_content_category=ContentCategory.MEME,
        )
        inner.create_post(winner)
        service = XAcquisitionRequestService(
            _RaceRepository(inner, winner),
            limits=_limits(),
        )
        with pytest.raises(XAcquisitionCategoryConflictError):
            service.submit(URL, login_key="alice", content_category=ContentCategory.MOVIE)
        same = service.submit(URL, login_key="alice", content_category=ContentCategory.MEME)
        assert same.submission_result == "active_reuse"
        assert same.request_id == winner.id.to_string()
    finally:
        connection.close()


def test_mixed_live_catalog_categories_conflict() -> None:
    first = MediaId.new().to_string()
    second = MediaId.new().to_string()
    claim = XPostClaim.new(
        submitted_url=URL,
        now_ms=10,
        created_by_login_key="alice",
        requested_content_category=ContentCategory.MEME,
    )
    mapping = {
        first: ContentCategory.MEME,
        second: ContentCategory.MOVIE,
    }
    service = XAcquisitionRequestService(
        _SuccessfulMixedRepository(claim, mapping),
        limits=_limits(),
        metadata_repository=_MixedMetadata(mapping),
    )
    with pytest.raises(XAcquisitionCategoryConflictError):
        service.submit(URL, login_key="alice", content_category=ContentCategory.MEME)
    with pytest.raises(XAcquisitionCategoryConflictError):
        service.submit(URL, login_key="alice", content_category=ContentCategory.MOVIE)


# ---------------------------------------------------------------------------
# Per-occurrence agreement guards for the conflict sentence
#
# `_reject_category_conflict` raises the same sentence from three distinct
# branches and the retention ledger cannot tell which one a rename touched. Each
# test below drives exactly one branch and asserts the complete message against
# the brand derived in `tests.support.kronika_identity`, so a change to any single
# occurrence fails its own assertion and leaves the other two green.
# ---------------------------------------------------------------------------

CONFLICT_SENTENCE = "Requested category conflicts with the existing {brand} save."


def _successful_claim_service(
    categories: dict[str, ContentCategory],
    *,
    metadata_repository: object | None,
) -> XAcquisitionRequestService:
    claim = XPostClaim.new(
        submitted_url=URL,
        now_ms=10,
        created_by_login_key="alice",
        requested_content_category=ContentCategory.MEME,
    )
    return XAcquisitionRequestService(
        _SuccessfulMixedRepository(claim, categories),
        limits=_limits(),
        metadata_repository=metadata_repository,
    )


def test_unconfirmable_live_catalog_conflict_reports_the_derived_brand() -> None:
    """The `live is None` branch: the catalog cannot be confirmed.

    Reached with a cataloged asset and no metadata repository, which is exactly
    the condition `_live_catalog_categories` reports as unconfirmable.
    """
    media_id = MediaId.new().to_string()
    service = _successful_claim_service(
        {media_id: ContentCategory.MEME}, metadata_repository=None
    )

    with pytest.raises(XAcquisitionCategoryConflictError) as excinfo:
        service.submit(URL, login_key="alice", content_category=ContentCategory.MOVIE)

    assert str(excinfo.value) == expected(CONFLICT_SENTENCE)


def test_mixed_live_catalog_conflict_reports_the_derived_brand() -> None:
    """The `len(live) != 1` branch: two live categories cannot match a request."""
    mapping = {
        MediaId.new().to_string(): ContentCategory.MEME,
        MediaId.new().to_string(): ContentCategory.MOVIE,
    }
    service = _successful_claim_service(
        mapping, metadata_repository=_MixedMetadata(mapping)
    )

    with pytest.raises(XAcquisitionCategoryConflictError) as excinfo:
        service.submit(URL, login_key="alice", content_category=ContentCategory.MOVIE)

    assert str(excinfo.value) == expected(CONFLICT_SENTENCE)


def test_create_race_conflict_reports_the_derived_brand() -> None:
    """The active-claim branch: the winning claim requested another category."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    connection = engine.connect()
    connection.execute(text("PRAGMA foreign_keys=ON"))
    metadata_for_schema = metadata.create_all(connection)
    connection.commit()
    try:
        assert metadata_for_schema is None
        inner = SqliteXAcquisitionClaimRepository(engine)
        winner = XPostClaim.new(
            submitted_url=URL,
            now_ms=10,
            created_by_login_key="alice",
            requested_content_category=ContentCategory.MEME,
        )
        inner.create_post(winner)
        service = XAcquisitionRequestService(
            _RaceRepository(inner, winner),
            limits=_limits(),
        )

        with pytest.raises(XAcquisitionCategoryConflictError) as excinfo:
            service.submit(URL, login_key="alice", content_category=ContentCategory.MOVIE)

        assert str(excinfo.value) == expected(CONFLICT_SENTENCE)
    finally:
        connection.close()
