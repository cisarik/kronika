"""Pure-domain per-user media alias overlay values.

Caller-private aliases are distinct from canonical ``media_metadata``. Empty
content (no title, no description, no tags) means no persisted row.
"""

from __future__ import annotations

from dataclasses import dataclass

from kronika.domain.identities import MediaId
from kronika.domain.identity_access import FrameNestIdentityAccessError, normalize_login
from kronika.domain.media_metadata import (
    CanonicalTagKey,
    FrameNestMediaMetadataError,
    MAX_MEDIA_TAGS,
    MediaDescription,
    MediaDisplayTitle,
)
from kronika.domain.x_acquisition import XPostClaimId

INVALID_MEDIA_USER_ALIAS_MESSAGE = "Invalid Kronika media user alias."


class FrameNestMediaUserAliasError(ValueError):
    """Sanitized error raised when alias construction is invalid."""


@dataclass(frozen=True, slots=True)
class MediaUserAliasContent:
    """Title, description, and canonical tag keys for one caller-private alias."""

    display_title: MediaDisplayTitle | None
    description: MediaDescription | None
    tag_keys: tuple[CanonicalTagKey, ...]

    def __post_init__(self) -> None:
        if self.display_title is not None and not isinstance(
            self.display_title, MediaDisplayTitle
        ):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if self.description is not None and not isinstance(
            self.description, MediaDescription
        ):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if not isinstance(self.tag_keys, tuple):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if len(self.tag_keys) > MAX_MEDIA_TAGS:
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if len(set(self.tag_keys)) != len(self.tag_keys):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        for key in self.tag_keys:
            if not isinstance(key, CanonicalTagKey):
                raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)

    def is_empty(self) -> bool:
        return (
            self.display_title is None
            and self.description is None
            and not self.tag_keys
        )


@dataclass(frozen=True, slots=True)
class MediaUserAlias:
    """Persisted per-user overlay for one logical media item."""

    media_id: MediaId
    login_key: str
    content: MediaUserAliasContent
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.media_id, MediaId):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        _validate_login_key(self.login_key)
        if not isinstance(self.content, MediaUserAliasContent):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if self.content.is_empty():
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        _validate_non_negative_ms(self.created_at_ms)
        _validate_non_negative_ms(self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)


@dataclass(frozen=True, slots=True)
class PendingMediaUserAlias:
    """Requester-private pending alias stored on an X claim before catalog identity."""

    claim_id: XPostClaimId
    login_key: str
    content: MediaUserAliasContent
    created_at_ms: int
    updated_at_ms: int

    def __post_init__(self) -> None:
        if not isinstance(self.claim_id, XPostClaimId):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        _validate_login_key(self.login_key)
        if not isinstance(self.content, MediaUserAliasContent):
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        if self.content.is_empty():
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
        _validate_non_negative_ms(self.created_at_ms)
        _validate_non_negative_ms(self.updated_at_ms)
        if self.updated_at_ms < self.created_at_ms:
            raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)


def parse_alias_content(
    display_title: str | None,
    description: str | None,
    tag_keys: list[str] | tuple[str, ...] | None,
) -> MediaUserAliasContent:
    """Parse untrusted alias fields into domain content or raise sanitized invalid."""
    try:
        parsed_title = _parse_title(display_title)
        parsed_description = _parse_description(description)
        raw_keys = () if tag_keys is None else tuple(tag_keys)
        parsed_keys = tuple(CanonicalTagKey(key) for key in raw_keys)
    except FrameNestMediaMetadataError as exc:
        raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE) from exc
    return MediaUserAliasContent(
        display_title=parsed_title,
        description=parsed_description,
        tag_keys=parsed_keys,
    )


def _parse_title(value: str | None) -> MediaDisplayTitle | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return MediaDisplayTitle(value)


def _parse_description(value: str | None) -> MediaDescription | None:
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return MediaDescription(value)


def _validate_login_key(value: object) -> None:
    try:
        normalized = normalize_login(value)
    except FrameNestIdentityAccessError as exc:
        raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE) from exc
    if not isinstance(value, str) or normalized != value:
        raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)


def _validate_non_negative_ms(value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FrameNestMediaUserAliasError(INVALID_MEDIA_USER_ALIAS_MESSAGE)
