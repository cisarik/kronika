"""Unit tests for persistent media metadata application use cases."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from kronika.application.media_metadata import (
    CreateCanonicalTag,
    GetMediaMetadata,
    ListCanonicalTags,
    SaveMediaMetadata,
)
from kronika.application.ports.media_metadata_repository import (
    OMITTED,
    AcquisitionSourceImmutableError,
    CanonicalTagCreateResult,
    CanonicalTagDefinitionConflictError,
    CanonicalTagNotFoundError,
    MediaMetadataMediaNotFoundError,
    MediaMetadataSaveResult,
    MediaMetadataSnapshot,
)
from kronika.domain import MediaId
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
    MediaDescription,
    MediaDisplayTitle,
    derive_collection_state,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
APPLICATION_MODULE = REPOSITORY_ROOT / "src" / "kronika" / "application" / "media_metadata.py"
MEDIA_ID = MediaId.from_string("12345678-1234-4234-9234-123456789abc")


def _tag(key: str, display_name: str = "Tag") -> CanonicalTag:
    return CanonicalTag(
        key=CanonicalTagKey(key),
        display_name=CanonicalTagDisplayName(display_name),
        created_at_ms=10,
        updated_at_ms=10,
    )


class _FakeRepository:
    def __init__(self) -> None:
        self.tags: dict[CanonicalTagKey, CanonicalTag] = {}
        self.snapshot = MediaMetadataSnapshot(
            media_id=MEDIA_ID,
            persisted=False,
            display_title=None,
            description=None,
            collection_key=None,
            processed_at_ms=None,
            tag_keys=(),
            created_at_ms=None,
            updated_at_ms=None,
        )
        self.save_calls: list[tuple[MediaId, MediaDisplayTitle | None, MediaDescription | None, tuple[CanonicalTagKey, ...], int]] = []

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        if key in self.tags:
            existing = self.tags[key]
            if existing.display_name != display_name:
                raise CanonicalTagDefinitionConflictError()
            return CanonicalTagCreateResult(status="already_exists", tag=existing)
        tag = CanonicalTag(key=key, display_name=display_name, created_at_ms=now_ms, updated_at_ms=now_ms)
        self.tags[key] = tag
        return CanonicalTagCreateResult(status="created", tag=tag)

    def list_canonical_tags(self) -> tuple[CanonicalTag, ...]:
        return tuple(sorted(self.tags.values(), key=lambda tag: (tag.display_name.value, tag.key.value)))

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        return self.tags.get(key)

    def get_media_metadata(self, media_id: MediaId) -> MediaMetadataSnapshot:
        if media_id != MEDIA_ID:
            raise MediaMetadataMediaNotFoundError()
        return self.snapshot

    def save_media_metadata(
        self,
        media_id: MediaId,
        display_title: MediaDisplayTitle | None,
        description: MediaDescription | None,
        tag_keys: tuple[CanonicalTagKey, ...],
        now_ms: int,
        *,
        content_category=None,
        acquisition_source=None,
        genre_keys=(),
        creator_attribution_kind=None,
        creator_stable_id=None,
        creator_handle=None,
        creator_display_name=None,
    ) -> MediaMetadataSaveResult:
        from kronika.domain.media_classification import (
            DEFAULT_ACQUISITION_SOURCE,
            DEFAULT_CONTENT_CATEGORY,
        )

        if content_category is None or content_category is OMITTED:
            content_category = DEFAULT_CONTENT_CATEGORY
        if creator_attribution_kind is OMITTED:
            creator_attribution_kind = None
        if creator_stable_id is OMITTED:
            creator_stable_id = None
        if creator_handle is OMITTED:
            creator_handle = None
        if creator_display_name is OMITTED:
            creator_display_name = None
        if self.snapshot.persisted:
            if acquisition_source is None:
                acquisition_source = self.snapshot.acquisition_source
            elif acquisition_source != self.snapshot.acquisition_source:
                raise AcquisitionSourceImmutableError(
                    "Acquisition source is immutable provenance and cannot be changed."
                )
        elif acquisition_source is None:
            acquisition_source = DEFAULT_ACQUISITION_SOURCE
        if media_id != MEDIA_ID:
            raise MediaMetadataMediaNotFoundError()
        for key in tag_keys:
            if key not in self.tags:
                raise CanonicalTagNotFoundError()
        self.save_calls.append((media_id, display_title, description, tag_keys, now_ms))
        status = "created" if not self.snapshot.persisted else "updated"
        if (
            self.snapshot.persisted
            and self.snapshot.display_title == display_title
            and self.snapshot.description == description
            and self.snapshot.tag_keys == tag_keys
            and self.snapshot.content_category == content_category
            and self.snapshot.acquisition_source == acquisition_source
            and self.snapshot.genre_keys == genre_keys
            and self.snapshot.creator_attribution_kind == creator_attribution_kind
            and self.snapshot.creator_stable_id == creator_stable_id
            and self.snapshot.creator_handle == creator_handle
            and self.snapshot.creator_display_name == creator_display_name
        ):
            status = "unchanged"
        created_at_ms = self.snapshot.created_at_ms if self.snapshot.created_at_ms is not None else now_ms
        updated_at_ms = self.snapshot.updated_at_ms if status == "unchanged" else now_ms
        collection_state = derive_collection_state(
            self.snapshot.collection_key,
            self.snapshot.processed_at_ms,
            tag_keys,
            now_ms,
        )
        self.snapshot = MediaMetadataSnapshot(
            media_id=media_id,
            persisted=True,
            display_title=display_title,
            description=description,
            collection_key=collection_state.collection_key,
            processed_at_ms=collection_state.processed_at_ms,
            tag_keys=tag_keys,
            created_at_ms=created_at_ms,
            updated_at_ms=updated_at_ms,
            content_category=content_category,
            acquisition_source=acquisition_source,
            genre_keys=genre_keys,
            creator_attribution_kind=creator_attribution_kind,
            creator_stable_id=creator_stable_id,
            creator_handle=creator_handle,
            creator_display_name=creator_display_name,
        )
        return MediaMetadataSaveResult(status=status, metadata=self.snapshot)


def test_create_and_list_canonical_tags_with_deterministic_clock() -> None:
    repository = _FakeRepository()
    create = CreateCanonicalTag(repository, clock_ms=lambda: 123)

    first = create.execute("mathematics", "Math")
    repeat = create.execute("mathematics", "Math")
    repository.create_canonical_tag(CanonicalTagKey("compression"), CanonicalTagDisplayName("Compression"), 125)

    assert first.status == "created"
    assert first.tag.created_at_ms == 123
    assert repeat.status == "already_exists"
    assert [tag.key.value for tag in ListCanonicalTags(repository).execute().tags] == [
        "compression",
        "mathematics",
    ]


def test_get_unsaved_metadata_and_save_statuses() -> None:
    repository = _FakeRepository()
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    get = GetMediaMetadata(repository)
    save = SaveMediaMetadata(repository, clock_ms=lambda: 500)

    unsaved = get.execute(MEDIA_ID.to_string())
    created = save.execute(MEDIA_ID.to_string(), "Reinventing Entropy", "A description.", ["mathematics"])
    unchanged = save.execute(MEDIA_ID.to_string(), "Reinventing Entropy", "A description.", ["mathematics"])
    cleared = save.execute(MEDIA_ID.to_string(), None, None, [])

    assert unsaved.persisted is False
    assert unsaved.display_title is None
    assert unsaved.tags == ()
    assert created.status == "created"
    assert created.metadata.display_title == "Reinventing Entropy"
    assert [tag.key.value for tag in created.metadata.tags] == ["mathematics"]
    assert unchanged.status == "unchanged"
    assert unchanged.metadata.updated_at_ms == created.metadata.updated_at_ms
    assert cleared.status == "updated"
    assert cleared.metadata.display_title is None
    assert cleared.metadata.tags == ()


def test_save_rejects_missing_media_missing_tag_and_duplicate_keys() -> None:
    repository = _FakeRepository()
    save = SaveMediaMetadata(repository, clock_ms=lambda: 1)

    with pytest.raises(MediaMetadataMediaNotFoundError):
        save.execute(MediaId.new().to_string(), None, None, [])
    with pytest.raises(CanonicalTagNotFoundError):
        save.execute(MEDIA_ID.to_string(), None, None, ["missing"])
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    with pytest.raises(ValueError):
        save.execute(MEDIA_ID.to_string(), None, None, ["mathematics", "mathematics"])


def test_sparse_metadata_returns_description_none() -> None:
    repository = _FakeRepository()
    get = GetMediaMetadata(repository)
    view = get.execute(MEDIA_ID.to_string())
    assert view.description is None


def test_description_only_update_returns_updated() -> None:
    repository = _FakeRepository()
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    save = SaveMediaMetadata(repository, clock_ms=lambda: 100)
    created = save.execute(MEDIA_ID.to_string(), "Title", None, ["mathematics"])
    assert created.status == "created"
    updated = save.execute(MEDIA_ID.to_string(), "Title", "New description.", ["mathematics"])
    assert updated.status == "updated"
    assert updated.metadata.description == "New description."


def test_clearing_description_returns_updated() -> None:
    repository = _FakeRepository()
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    save = SaveMediaMetadata(repository, clock_ms=lambda: 200)
    created = save.execute(MEDIA_ID.to_string(), "Title", "Some description.", ["mathematics"])
    assert created.status == "created"
    cleared = save.execute(MEDIA_ID.to_string(), "Title", None, ["mathematics"])
    assert cleared.status == "updated"
    assert cleared.metadata.description is None


def test_exact_noop_including_description_returns_unchanged() -> None:
    repository = _FakeRepository()
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    save = SaveMediaMetadata(repository, clock_ms=lambda: 300)
    created = save.execute(MEDIA_ID.to_string(), "Title", "Desc.", ["mathematics"])
    assert created.status == "created"
    unchanged = save.execute(MEDIA_ID.to_string(), "Title", "Desc.", ["mathematics"])
    assert unchanged.status == "unchanged"
    assert unchanged.metadata.updated_at_ms == created.metadata.updated_at_ms


def test_description_whitespace_only_normalized_to_none() -> None:
    repository = _FakeRepository()
    repository.tags[CanonicalTagKey("mathematics")] = _tag("mathematics", "Math")
    save = SaveMediaMetadata(repository, clock_ms=lambda: 400)
    save.execute(MEDIA_ID.to_string(), "Title", "  ", ["mathematics"])
    spy = repository.save_calls[-1]
    assert spy[2] is None


def test_media_metadata_application_imports_no_framework_infrastructure_or_media_tools() -> None:
    tree = ast.parse(APPLICATION_MODULE.read_text(encoding="utf-8"), filename=str(APPLICATION_MODULE))
    violations: list[str] = []
    forbidden_roots = {
        "fastapi",
        "sqlalchemy",
        "kronika.infrastructure",
        "kronika.adapters",
        "kronika.infrastructure.media_analysis",
        "kronika.infrastructure.ai",
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
