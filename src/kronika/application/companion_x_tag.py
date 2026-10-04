"""Ensure the companion Save surface's fixed X canonical tag exists."""

from __future__ import annotations

from kronika.application.media_metadata import ClockMs, CreateCanonicalTag
from kronika.application.ports.media_metadata_repository import (
    CanonicalTagCreateResult,
    CanonicalTagDefinitionConflictError,
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataRepository,
)
from kronika.domain.media_metadata import CanonicalTagDisplayName, CanonicalTagKey
from kronika.structured_logging import get_logger

COMPANION_X_TAG_KEY = "x"
COMPANION_X_TAG_DISPLAY_NAME = "\N{MATHEMATICAL DOUBLE-STRUCK CAPITAL X}"

LOGGER = get_logger("companion_x_tag")


class EnsureCompanionXTag:
    """Create the fixed companion X tag idempotently; callers cannot choose key or display."""

    def __init__(
        self,
        repository: MediaMetadataRepository,
        *,
        clock_ms: ClockMs | None = None,
    ) -> None:
        if clock_ms is None:
            self._create = CreateCanonicalTag(repository)
        else:
            self._create = CreateCanonicalTag(repository, clock_ms=clock_ms)
        self._repository = repository

    def execute(self) -> CanonicalTagCreateResult | None:
        try:
            return self._create.execute(COMPANION_X_TAG_KEY, COMPANION_X_TAG_DISPLAY_NAME)
        except CanonicalTagDefinitionConflictError:
            _log_seed_failure(
                event="companion_x_tag_seed_conflict",
                error_code="COMPANION_X_TAG_DEFINITION_CONFLICT",
                reason="definition_conflict",
            )
            return None
        except FrameNestMediaMetadataRepositoryError:
            existing = _matching_existing_tag(self._repository)
            if existing is not None:
                return CanonicalTagCreateResult(status="already_exists", tag=existing)
            _log_seed_failure(
                event="companion_x_tag_seed_repository_error",
                error_code="COMPANION_X_TAG_REPOSITORY_ERROR",
                reason="repository_error",
            )
            return None


def _matching_existing_tag(repository: MediaMetadataRepository) -> object | None:
    try:
        existing = repository.get_canonical_tag(CanonicalTagKey(COMPANION_X_TAG_KEY))
    except FrameNestMediaMetadataRepositoryError:
        return None
    if existing is None:
        return None
    expected = CanonicalTagDisplayName(COMPANION_X_TAG_DISPLAY_NAME)
    if existing.display_name != expected:
        return None
    return existing


def _log_seed_failure(*, event: str, error_code: str, reason: str) -> None:
    LOGGER.emit(
        level="WARNING",
        event=event,
        operation="ensure_companion_x_tag",
        error_code=error_code,
        retryable=False,
        context={"reason": reason, "tag_key": COMPANION_X_TAG_KEY},
    )
