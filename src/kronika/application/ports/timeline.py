"""Timeline read values. The chronicle is a projection, not a stored entity."""

from __future__ import annotations

from dataclasses import dataclass


class TimelineUnavailableError(RuntimeError):
    """Sanitized Timeline persistence or integrity failure."""

    def __init__(self) -> None:
        super().__init__("The Timeline is unavailable.")


@dataclass(frozen=True, slots=True)
class TimelinePageQuery:
    """One validated page request. Caller identity is never a field here."""

    limit: int
    offset: int
    kind: str | None = None
    content_category: str | None = None
    q: str | None = None
    status: str | None = None
    visibility: str | None = None
    publication: str | None = None


@dataclass(frozen=True, slots=True)
class TimelineItem:
    """One authorized chronicle row. It carries no answer, path, or identity."""

    item_id: str
    kind: str
    media_kind: str | None
    record_id: str | None
    media_id: str | None
    operation_id: str | None
    display_title: str | None
    content_category: str | None
    created_at_ms: int
    status: str
    visibility: str
    publication_state: str
    read_decision: str
    preview_location_id: str | None = None


@dataclass(frozen=True, slots=True)
class TimelinePage:
    """One filtered page and the unpaginated authorized total."""

    items: tuple[TimelineItem, ...]
    total: int
    limit: int
    offset: int
