"""Opt-in synthetic identities and fixture-scoped policies for tests.

These helpers are not production bypasses. Authorization evidence uses the
real record policy and SQLite.
"""

from __future__ import annotations

from kronika.application.content_publication import ContentAudiencePolicy
from kronika.domain.identities import MediaId
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    ROLE_USER,
    IdentityContext,
    normalize_login,
)


def synthetic_identity(
    login: str,
    *,
    role: str = ROLE_USER,
    provenance: str = "tailscale-serve",
) -> IdentityContext:
    """Build one verified synthetic caller. The login is normalized."""
    login_key = normalize_login(login)
    return IdentityContext(
        login=login_key,
        login_key=login_key,
        display_name=login_key,
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance=provenance,
    )


class FixtureScopedAudience:
    """Allow only the concrete media ids supplied by an isolated test."""

    def __init__(self, media_ids: set[str]) -> None:
        self._media_ids = set(media_ids)

    def may_read(self, media_id: MediaId, identity: object) -> bool:
        if not isinstance(identity, IdentityContext) or not identity.login_key:
            return False
        return media_id.to_string() in self._media_ids

    def read_decision(self, media_id: MediaId, identity: object) -> str:
        return "current" if self.may_read(media_id, identity) else "deny"


def install_synthetic_caller(app, login: str = "alice", *, role: str = ROLE_USER):
    """Attach one verified caller to HTTP scopes that do not already have one."""
    from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY

    identity = synthetic_identity(login, role=role)

    class _Caller:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope.get("type") == "http" and SCOPE_IDENTITY not in scope:
                scope[SCOPE_IDENTITY] = identity
            await self.inner(scope, receive, send)

    app.add_middleware(_Caller)
    return app


def scoped_policy(media_ids: set[str]) -> ContentAudiencePolicy:
    """Return a real policy object whose repository allows only fixture ids."""

    class _Repository:
        def media_exists(self, media_id: MediaId) -> bool:
            return media_id.to_string() in media_ids

        def is_published(self, media_id: MediaId) -> bool:
            return media_id.to_string() in media_ids

    return ContentAudiencePolicy(_Repository())
