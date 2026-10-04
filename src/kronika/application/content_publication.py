"""Application services for content audience and explicit publication."""

from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Iterable
import unicodedata

from kronika.application.ports.content_publication_repository import (
    AdminMediaPage,
    AdminMediaQuery,
    AnalysisFilter,
    ContentPublicationRepository,
    MediaWorkflowStatus,
    PublicationFilter,
    PublishContentResult,
    ReadinessFilter,
)
from kronika.application.upload_transport import default_now_ms
from kronika.domain.identities import MediaId
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_WORKFLOW_READ,
    FrameNestIdentityAccessError,
    IdentityContext,
    normalize_login,
)
from kronika.domain.record_access import (
    READ_CURRENT,
    READ_DENY,
    READ_LEGACY,
    decide_bound_read,
)
from kronika.domain.media_metadata import CanonicalTagKey

DEFAULT_ADMIN_MEDIA_LIMIT = 24
MAX_ADMIN_MEDIA_LIMIT = 100
MAX_ADMIN_MEDIA_QUERY_CODE_POINTS = 240


class ContentPublicationValidationError(ValueError):
    """Raised for invalid public workflow input."""


@dataclass(frozen=True, slots=True)
class ContentAudiencePolicy:
    """Shared item-level audience decision used by every direct media surface."""

    repository: ContentPublicationRepository
    youtube_requester_private_access: object | None = None
    x_requester_private_access: object | None = None
    upload_attributed_access: object | None = None
    record_bindings: object | None = None

    def may_read(self, media_id: MediaId, identity: object) -> bool:
        return self.read_decision(media_id, identity) != READ_DENY

    def read_decision(self, media_id: MediaId, identity: object) -> str:
        """Return deny, current, approved, or legacy for one media item."""
        binding = None
        if self.record_bindings is not None:
            binding = self.record_bindings.bound_record(media_id)
        if binding is not None:
            return decide_bound_read(identity, binding)
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            return READ_DENY
        exists = self.repository.media_exists(media_id)
        if identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ):
            return READ_CURRENT if exists else READ_DENY
        if exists and self.repository.is_published(media_id):
            return READ_LEGACY
        for access in self._requester_accesses():
            if access is None:
                continue
            if bool(
                access.has_live_requester_media_access(
                    media_id=media_id, login_key=identity.login_key
                )
            ):
                return READ_LEGACY
        return READ_DENY

    def _requester_accesses(self) -> tuple[object, ...]:
        return (
            self.youtube_requester_private_access,
            self.x_requester_private_access,
            self.upload_attributed_access,
        )



@dataclass(frozen=True, slots=True)
class ListAdminMedia:
    """Normalize and execute one admin publication-workflow query."""

    repository: ContentPublicationRepository

    def execute(
        self,
        *,
        q: str | None,
        tag_keys: Iterable[str],
        publication: str,
        readiness: str,
        analysis: str,
        limit: int,
        offset: int,
        contributor: str | None = None,
    ) -> AdminMediaPage:
        return self.repository.list_admin_media(
            AdminMediaQuery(
                q=_normalize_query(q),
                tag_keys=_normalize_tags(tag_keys),
                publication=_publication_filter(publication),
                readiness=_readiness_filter(readiness),
                analysis=_analysis_filter(analysis),
                limit=_bounded_int(limit, minimum=1, maximum=MAX_ADMIN_MEDIA_LIMIT),
                offset=_bounded_int(offset, minimum=0, maximum=None),
                contributor=_normalize_contributor(contributor),
            )
        )


@dataclass(frozen=True, slots=True)
class PublishContent:
    """Execute one atomic, conditional, idempotent publication or unpublication."""

    repository: ContentPublicationRepository
    now_ms: Callable[[], int] = default_now_ms

    def execute(self, media_id: str, *, published: bool = True) -> PublishContentResult:
        parsed = MediaId.from_string(media_id)
        if not published:
            return self.repository.unpublish(parsed)
        timestamp = self.now_ms()
        if (
            isinstance(timestamp, bool)
            or not isinstance(timestamp, int)
            or timestamp < 0
        ):
            raise ValueError("Content publication timestamp is invalid.")
        return self.repository.publish(parsed, timestamp)


@dataclass(frozen=True, slots=True)
class GetMediaWorkflowStatus:
    """Read bounded metadata and publication truth for one media item."""

    repository: ContentPublicationRepository

    def execute(self, media_id: str) -> MediaWorkflowStatus:
        try:
            parsed = MediaId.from_string(media_id)
        except Exception as exc:
            raise ContentPublicationValidationError() from exc
        return self.repository.get_media_workflow_status(parsed)


def _normalize_contributor(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContentPublicationValidationError()
    stripped = value.strip()
    if not stripped:
        return None
    try:
        return normalize_login(stripped)
    except FrameNestIdentityAccessError as exc:
        raise ContentPublicationValidationError() from exc


def _normalize_query(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ContentPublicationValidationError()
    normalized = value.strip()
    if not normalized:
        return None
    if (
        len(normalized) > MAX_ADMIN_MEDIA_QUERY_CODE_POINTS
        or any(unicodedata.category(character) == "Cc" for character in normalized)
    ):
        raise ContentPublicationValidationError()
    return normalized


def _normalize_tags(values: Iterable[str]) -> tuple[str, ...]:
    result: list[str] = []
    seen: set[str] = set()
    try:
        for value in values:
            key = CanonicalTagKey(value).value
            if key not in seen:
                seen.add(key)
                result.append(key)
    except Exception as exc:
        raise ContentPublicationValidationError() from exc
    return tuple(result)


def _publication_filter(value: str) -> PublicationFilter:
    if value not in {"unpublished", "published", "all"}:
        raise ContentPublicationValidationError()
    return value  # type: ignore[return-value]


def _readiness_filter(value: str) -> ReadinessFilter:
    if value not in {"all", "ready", "incomplete"}:
        raise ContentPublicationValidationError()
    return value  # type: ignore[return-value]


def _analysis_filter(value: str) -> AnalysisFilter:
    if value not in {
        "all",
        "not_requested",
        "pending",
        "analyzing",
        "analyzed",
        "failed",
    }:
        raise ContentPublicationValidationError()
    return value  # type: ignore[return-value]


def _bounded_int(value: int, *, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ContentPublicationValidationError()
    if maximum is not None and value > maximum:
        raise ContentPublicationValidationError()
    return value
