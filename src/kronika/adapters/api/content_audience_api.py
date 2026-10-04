"""Shared application-level audience gate for direct media API surfaces."""

from __future__ import annotations

from fastapi import Request

from kronika.application.content_publication import ContentAudiencePolicy
from kronika.application.ports.content_publication_repository import (
    FrameNestContentPublicationRepositoryError,
)
from kronika.domain.identities import MediaId
from kronika.domain.identity_access import IdentityContext
from kronika.domain.record_access import READ_DENY
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY


class ContentAudienceUnavailableError(RuntimeError):
    """Raised when the durable audience decision cannot be established."""


def content_audience_decision(
    *,
    request: Request,
    media_id: MediaId,
    policy: ContentAudiencePolicy | None,
) -> str:
    """Return deny, current, approved, or legacy. Missing policy or identity denies."""
    if policy is None:
        return READ_DENY
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext) or not identity.login_key:
        return READ_DENY
    try:
        decider = getattr(policy, "read_decision", None)
        if decider is None:
            return "current" if policy.may_read(media_id, identity) else READ_DENY
        decision = decider(media_id, identity)
    except FrameNestContentPublicationRepositoryError as exc:
        raise ContentAudienceUnavailableError() from exc
    except Exception as exc:
        raise ContentAudienceUnavailableError() from exc
    if decision not in {"deny", "current", "approved", "legacy"}:
        return READ_DENY
    return str(decision)


def load_approved_projection(*, policy: ContentAudiencePolicy | None, media_id: MediaId):
    """Return the frozen projection for one media id, or None when it is absent.

    Callers that already decided ``approved`` must not fall back to working state
    when this returns None or raises.
    """
    if policy is None:
        return None
    bindings = getattr(policy, "record_bindings", None)
    loader = getattr(bindings, "approved_projection_for_media", None)
    if not callable(loader):
        return None
    return loader(media_id.to_string())


def content_audience_allows(
    *,
    request: Request,
    media_id: MediaId,
    policy: ContentAudiencePolicy | None,
) -> bool:
    """Return whether the caller may read the media item at all."""
    return (
        content_audience_decision(
            request=request,
            media_id=media_id,
            policy=policy,
        )
        != READ_DENY
    )
