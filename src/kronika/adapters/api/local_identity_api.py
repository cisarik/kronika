"""Configured local-owner identity for loopback TCP and the local operator channel.

Client headers cannot select the owner. The public composition never installs
this adapter.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping

from starlette.types import Message, Receive, Scope, Send

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.configuration import INGRESS_MODE_PUBLIC_PUBLISHED_UDS, KronikaSettings
from kronika.domain.identity_access import (
    IDENTITY_PROVENANCE_LOCAL_CONFIG,
    IdentityContext,
    IdentityMappingEntry,
    build_identity_mapping,
    identity_from_mapped_login,
)

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_OPERATOR_PREFIX = "/api/operator"


def configured_local_identity(settings: KronikaSettings) -> IdentityContext | None:
    """Return the mapped local owner, or None when local ownership is unset."""
    if settings.ingress_mode == INGRESS_MODE_PUBLIC_PUBLISHED_UDS:
        return None
    if not settings.local_owner_login:
        return None
    mapping = build_identity_mapping(settings.identity_map)
    return identity_from_mapped_login(
        login_key=settings.local_owner_login,
        mapping=mapping,
        provenance=IDENTITY_PROVENANCE_LOCAL_CONFIG,
    )


def local_identity_context(
    mapping: Mapping[str, IdentityMappingEntry],
    login_key: str | None,
) -> IdentityContext | None:
    if not login_key:
        return None
    return identity_from_mapped_login(
        login_key=login_key,
        mapping=mapping,
        provenance=IDENTITY_PROVENANCE_LOCAL_CONFIG,
    )


class LocalIdentityMiddleware:
    """Attach the configured owner only for an actual loopback client."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        identity: IdentityContext | None,
        origin: str | None,
    ) -> None:
        self._app = app
        self._identity = identity
        self._origin = origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http" or self._identity is None:
            await self._app(scope, receive, send)
            return
        if scope.get(SCOPE_IDENTITY) is not None:
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        host = client[0] if isinstance(client, tuple) and client else ""
        if host not in _LOOPBACK_HOSTS:
            await self._app(scope, receive, send)
            return
        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if (
            method in _UNSAFE_METHODS
            and not path.startswith(_OPERATOR_PREFIX)
            and not _origin_matches(scope, self._origin)
        ):
            await _send_forbidden(send)
            return
        scope[SCOPE_IDENTITY] = self._identity
        await self._app(scope, receive, send)


def _origin_matches(scope: Scope, expected: str | None) -> bool:
    if not expected:
        return False
    for name, value in scope.get("headers") or ():
        if bytes(name).lower() == b"origin" and bytes(value).decode(
            "latin-1"
        ) == expected:
            return True
    return False


async def _send_forbidden(send: Send) -> None:
    body = b'{"error":{"code":"MUTATION_ORIGIN_FORBIDDEN","message":"The mutation origin is forbidden."}}'
    await send(
        {
            "type": "http.response.start",
            "status": 403,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
