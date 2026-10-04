"""Unit tests for searchable media catalog application queries."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kronika.application.media_catalog import (
    ListMediaCatalog,
    MediaCatalogValidationError,
)
from kronika.application.ports.media_catalog_repository import (
    MediaCatalogPage,
    MediaCatalogQuery,
)
from kronika.domain.media_metadata import CanonicalTagKey
from kronika.domain.record_access import RecordAccessScope

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_MODULE = REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_catalog.py"


class _FakeCatalogRepository:
    def __init__(self) -> None:
        self.queries: list[MediaCatalogQuery] = []

    def list_media(self, query: MediaCatalogQuery) -> MediaCatalogPage:
        self.queries.append(query)
        return MediaCatalogPage(
            items=(),
            total=0,
            limit=query.limit,
            offset=query.offset,
            q=query.q,
            tag_keys=query.tag_keys,
        )


def _execute(
    *,
    q: str | None = None,
    tag_keys: list[str] | None = None,
    limit: int = 24,
    offset: int = 0,
) -> tuple[MediaCatalogPage, MediaCatalogQuery]:
    repository = _FakeCatalogRepository()
    scope = RecordAccessScope.legacy_public()
    result = ListMediaCatalog(repository).execute(
        q=q,
        tag_keys=[] if tag_keys is None else tag_keys,
        limit=limit,
        offset=offset,
        access_scope=scope,
    )
    return result, repository.queries[-1]


def test_default_query_construction() -> None:
    result, query = _execute()

    assert query == MediaCatalogQuery(
        q=None,
        tag_keys=(),
        limit=24,
        offset=0,
        collection_key=None,
        published_only=True,
        access_scope=RecordAccessScope.legacy_public(),
    )
    assert result.q is None
    assert result.tag_keys == ()
    assert result.limit == 24
    assert result.offset == 0


def test_whitespace_only_title_query_normalizes_to_absent_filter() -> None:
    _, query = _execute(q=" \t\n ")

    assert query.q is None


def test_title_query_is_trimmed_and_length_limited() -> None:
    _, query = _execute(q="  Reinventing Entropy  ")

    assert query.q == "Reinventing Entropy"

    with pytest.raises(MediaCatalogValidationError):
        _execute(q="x" * 241)


@pytest.mark.parametrize("q", ["bad\x00query", "bad\nquery", "bad\tquery"])
def test_title_query_rejects_nul_and_control_characters(q: str) -> None:
    with pytest.raises(MediaCatalogValidationError):
        _execute(q=q)


@pytest.mark.parametrize("tag_key", ["Bad", "bad--key", "-bad", "bad_underscore"])
def test_canonical_tag_filter_validates_key_syntax(tag_key: str) -> None:
    with pytest.raises(MediaCatalogValidationError):
        _execute(tag_keys=[tag_key])


def test_duplicate_tag_filters_are_normalized_in_first_seen_order() -> None:
    _, query = _execute(tag_keys=["mathematics", "compression", "mathematics"])

    assert query.tag_keys == (CanonicalTagKey("mathematics"), CanonicalTagKey("compression"))


@pytest.mark.parametrize(
    ("limit", "offset"),
    [
        (0, 0),
        (101, 0),
        (24, -1),
        (True, 0),
        (24, False),
    ],
)
def test_pagination_validation(limit: int, offset: int) -> None:
    with pytest.raises(MediaCatalogValidationError):
        _execute(limit=limit, offset=offset)


def test_missing_scope_denies_without_repository_call() -> None:
    repository = _FakeCatalogRepository()
    result = ListMediaCatalog(repository).execute()
    assert repository.queries == []
    assert result.items == ()
    assert result.total == 0


def test_media_catalog_application_imports_no_framework_or_infrastructure() -> None:
    tree = ast.parse(APPLICATION_MODULE.read_text(encoding="utf-8"), filename=str(APPLICATION_MODULE))
    violations: list[str] = []
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "kronika.infrastructure",
        "kronika.adapters",
    }
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            module = node.names[0].name
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
        else:
            continue
        if any(module.startswith(root) for root in forbidden_roots):
            violations.append(module)
    assert violations == []
