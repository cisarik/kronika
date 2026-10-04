"""Unit tests for companion Save X-tag seeding."""

from __future__ import annotations

import threading

from kronika.application.companion_x_tag import (
    COMPANION_X_TAG_DISPLAY_NAME,
    COMPANION_X_TAG_KEY,
    EnsureCompanionXTag,
)
from kronika.application.ports.media_metadata_repository import (
    CanonicalTagCreateResult,
    CanonicalTagDefinitionConflictError,
    FrameNestMediaMetadataRepositoryError,
)
from kronika.domain.media_metadata import (
    CanonicalTag,
    CanonicalTagDisplayName,
    CanonicalTagKey,
)

X_DISPLAY = "\N{MATHEMATICAL DOUBLE-STRUCK CAPITAL X}"


def _tag(key: str, display_name: str, now_ms: int = 10) -> CanonicalTag:
    return CanonicalTag(
        key=CanonicalTagKey(key),
        display_name=CanonicalTagDisplayName(display_name),
        created_at_ms=now_ms,
        updated_at_ms=now_ms,
    )


class _InMemoryRepository:
    def __init__(self) -> None:
        self.tags: dict[CanonicalTagKey, CanonicalTag] = {}
        self.create_calls = 0
        self.fail_create_with: Exception | None = None

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        self.create_calls += 1
        if self.fail_create_with is not None:
            raise self.fail_create_with
        if key in self.tags:
            existing = self.tags[key]
            if existing.display_name != display_name:
                raise CanonicalTagDefinitionConflictError()
            return CanonicalTagCreateResult(status="already_exists", tag=existing)
        tag = _tag(key.value, display_name.value, now_ms)
        self.tags[key] = tag
        return CanonicalTagCreateResult(status="created", tag=tag)

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        return self.tags.get(key)


class _LockedRepository(_InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self._lock = threading.Lock()

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        with self._lock:
            return super().create_canonical_tag(key, display_name, now_ms)

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        with self._lock:
            return super().get_canonical_tag(key)


class _IntegrityRaceRepository:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.created: CanonicalTag | None = None
        self.create_calls = 0

    def create_canonical_tag(
        self,
        key: CanonicalTagKey,
        display_name: CanonicalTagDisplayName,
        now_ms: int,
    ) -> CanonicalTagCreateResult:
        with self._lock:
            self.create_calls += 1
            if self.created is None:
                self.created = _tag(key.value, display_name.value, now_ms)
                return CanonicalTagCreateResult(status="created", tag=self.created)
            raise FrameNestMediaMetadataRepositoryError("unique constraint")

    def get_canonical_tag(self, key: CanonicalTagKey) -> CanonicalTag | None:
        del key
        with self._lock:
            return self.created


def test_fixed_pair_is_legal_under_current_validators() -> None:
    assert CanonicalTagKey("x").value == "x"
    assert CanonicalTagKey(COMPANION_X_TAG_KEY).value == "x"
    assert CanonicalTagDisplayName(X_DISPLAY).value == X_DISPLAY
    assert CanonicalTagDisplayName(COMPANION_X_TAG_DISPLAY_NAME).value == X_DISPLAY
    assert X_DISPLAY == "\U0001d54f"


def test_ensure_creates_fixed_pair() -> None:
    repository = _InMemoryRepository()
    result = EnsureCompanionXTag(repository, clock_ms=lambda: 50).execute()

    assert result is not None
    assert result.status == "created"
    assert result.tag.key.value == "x"
    assert result.tag.display_name.value == X_DISPLAY
    assert result.tag.created_at_ms == 50
    assert repository.create_calls == 1


def test_ensure_matching_definition_is_already_exists() -> None:
    repository = _InMemoryRepository()
    repository.tags[CanonicalTagKey("x")] = _tag("x", X_DISPLAY, 7)
    result = EnsureCompanionXTag(repository, clock_ms=lambda: 9).execute()

    assert result is not None
    assert result.status == "already_exists"
    assert result.tag.created_at_ms == 7
    assert repository.tags[CanonicalTagKey("x")].display_name.value == X_DISPLAY


def test_ensure_conflict_is_best_effort_and_does_not_overwrite() -> None:
    repository = _InMemoryRepository()
    repository.tags[CanonicalTagKey("x")] = _tag("x", "Twitter")
    result = EnsureCompanionXTag(repository, clock_ms=lambda: 1).execute()

    assert result is None
    assert repository.tags[CanonicalTagKey("x")].display_name.value == "Twitter"


def test_ensure_repository_error_is_best_effort() -> None:
    repository = _InMemoryRepository()
    repository.fail_create_with = FrameNestMediaMetadataRepositoryError("disk")
    result = EnsureCompanionXTag(repository, clock_ms=lambda: 1).execute()

    assert result is None
    assert CanonicalTagKey("x") not in repository.tags


def test_ensure_repository_error_after_winner_is_already_exists() -> None:
    repository = _IntegrityRaceRepository()
    first = EnsureCompanionXTag(repository, clock_ms=lambda: 3).execute()
    second = EnsureCompanionXTag(repository, clock_ms=lambda: 4).execute()

    assert first is not None
    assert first.status == "created"
    assert second is not None
    assert second.status == "already_exists"
    assert second.tag.display_name.value == X_DISPLAY


def test_ensure_concurrent_same_definition_is_idempotent() -> None:
    repository = _LockedRepository()
    use_case = EnsureCompanionXTag(repository, clock_ms=lambda: 11)
    results: list[CanonicalTagCreateResult | None] = []
    errors: list[BaseException] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=5)
            results.append(use_case.execute())
        except BaseException as exc:  # noqa: BLE001 — capture any thread failure
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert None not in results
    statuses = {item.status for item in results if item is not None}
    assert statuses <= {"created", "already_exists"}
    assert "created" in statuses
    stored = repository.get_canonical_tag(CanonicalTagKey("x"))
    assert stored is not None
    assert stored.display_name.value == X_DISPLAY
    assert stored.key.value == COMPANION_X_TAG_KEY
