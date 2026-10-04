"""Durable content-publication state and server-owned readiness."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

MISSING_DISPLAY_TITLE = "display_title"
MISSING_DESCRIPTION = "description"
MISSING_TAGS = "tags"
MISSING_FIELD_ORDER = (
    MISSING_DISPLAY_TITLE,
    MISSING_DESCRIPTION,
    MISSING_TAGS,
)


class ContentPublicationOrigin(str, Enum):
    """Allowed provenance for one durable content-publication row."""

    LEGACY_BACKFILL = "legacy_backfill"
    ADMIN_EXPLICIT = "admin_explicit"
    COMPANION_REVIEW = "companion_review"


@dataclass(frozen=True, slots=True)
class ContentPublicationReadiness:
    """Publication eligibility derived only from persisted canonical metadata."""

    ready: bool
    missing_fields: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ContentPublication:
    """One durable publication row; absence represents unpublished content."""

    media_id: str
    published_at_ms: int
    publication_origin: ContentPublicationOrigin


def derive_content_publication_readiness(
    *,
    display_title: object,
    description: object,
    canonical_tag_count: int,
) -> ContentPublicationReadiness:
    """Return readiness and stable ordered missing fields."""
    missing: list[str] = []
    if not isinstance(display_title, str) or not display_title.strip():
        missing.append(MISSING_DISPLAY_TITLE)
    if not isinstance(description, str) or not description.strip():
        missing.append(MISSING_DESCRIPTION)
    if (
        isinstance(canonical_tag_count, bool)
        or not isinstance(canonical_tag_count, int)
        or canonical_tag_count < 1
    ):
        missing.append(MISSING_TAGS)
    return ContentPublicationReadiness(
        ready=not missing,
        missing_fields=tuple(missing),
    )
