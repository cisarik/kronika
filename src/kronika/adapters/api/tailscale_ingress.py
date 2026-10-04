"""Trusted Tailscale Serve ingress, identity, authorization, CSRF, and audit.

This pure ASGI middleware is the only FrameNest component that trusts
Tailscale Serve identity headers, and it is installed only when the
application runs in the production ``tailscale_uds`` ingress mode. In that
mode the sole application listener is a permission-restricted Unix socket
whose only remote writer is the root-owned ``tailscaled`` HTTPS Serve proxy,
which strips and reinjects identity headers. Header trust is therefore bound
to ingress provenance, never to header names alone.
"""

from __future__ import annotations

import json
import re
from typing import Any, Awaitable, Callable, Mapping
import uuid

import anyio

from kronika.domain.identity_access import (
    CAPABILITY_ANALYSIS_PROPOSE,
    CAPABILITY_ANALYSIS_RUN,
    CAPABILITY_GALLERY_READ,
    CAPABILITY_LIBRARY_SCAN,
    CAPABILITY_MEDIA_CATALOG_REMOVE,
    CAPABILITY_MEDIA_DOWNLOAD,
    CAPABILITY_MEDIA_CONTENT_PUBLISH,
    CAPABILITY_MEDIA_IMPORT,
    CAPABILITY_MEDIA_ORIGINAL_READ,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_MEDIA_WORKSPACE_READ,
    CAPABILITY_METADATA_ALIAS_TEAM_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    CAPABILITY_METADATA_CANONICAL_WRITE,
    CAPABILITY_PROVIDER_OPERATE,
    CAPABILITY_RECORDS_APPROVE,
    CAPABILITY_RESEARCH_RUN,
    CAPABILITY_UPLOAD_SUBMIT,
    CAPABILITY_YOUTUBE_ACQUIRE,
    CAPABILITY_YOUTUBE_REQUEST,
    CAPABILITY_X_ACQUIRE,
    CAPABILITY_X_REQUEST,
    FrameNestIdentityAccessError,
    IdentityContext,
    IdentityMappingEntry,
    resolve_identity,
)
from kronika.domain.security_audit import (
    AUDIT_OUTCOME_ALLOWED,
    AUDIT_OUTCOME_DENIED,
    FrameNestSecurityAuditError,
    SecurityAuditEvent,
)
from kronika.structured_logging import get_logger

Scope = dict[str, Any]
Message = dict[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]

LOGGER = get_logger("tailscale_ingress")

CHANNEL_LOCAL = "local"
CHANNEL_LOCAL_OPERATOR = "local_operator"
CHANNEL_TAILSCALE = "tailscale"

SCOPE_REQUEST_ID = "kronika.request_id"
SCOPE_IDENTITY = "kronika.identity"
SCOPE_INGRESS_CHANNEL = "kronika.ingress_channel"
SCOPE_AUDIT_EVENT_ID = "kronika.audit_event_id"

HEADER_TAILSCALE_USER_LOGIN = b"tailscale-user-login"
HEADER_TAILSCALE_USER_NAME = b"tailscale-user-name"
HEADER_TAILSCALE_USER_PROFILE_PIC = b"tailscale-user-profile-pic"
HEADER_X_FORWARDED_FOR = b"x-forwarded-for"
HEADER_X_FORWARDED_PROTO = b"x-forwarded-proto"
HEADER_X_FORWARDED_HOST = b"x-forwarded-host"
HEADER_ORIGIN = b"origin"
HEADER_MUTATION = b"x-framenest-request"
HEADER_MUTATION_KRONIKA = b"x-kronika-request"
HEADER_REQUEST_ID = b"x-request-id"

#: Both accepted spellings of the mutation request header. A mutation gate only;
#: neither spelling is an origin, CORS, or companion-origin credential.
MUTATION_HEADERS = (HEADER_MUTATION, HEADER_MUTATION_KRONIKA)

_REMOTE_MARKER_HEADERS = frozenset(
    {
        HEADER_TAILSCALE_USER_LOGIN,
        HEADER_TAILSCALE_USER_NAME,
        HEADER_TAILSCALE_USER_PROFILE_PIC,
        HEADER_X_FORWARDED_FOR,
        HEADER_X_FORWARDED_PROTO,
        HEADER_X_FORWARDED_HOST,
    }
)
_SINGLETON_SECURITY_HEADERS = frozenset(
    {
        HEADER_TAILSCALE_USER_LOGIN,
        HEADER_TAILSCALE_USER_NAME,
        HEADER_X_FORWARDED_FOR,
        HEADER_X_FORWARDED_PROTO,
        HEADER_X_FORWARDED_HOST,
        HEADER_ORIGIN,
    }
)
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_OPERATOR_PATH_PREFIX = "/api/operator/youtube"

EXPECTED_MUTATION_HEADER_VALUE = b"1"
EXPECTED_FORWARDED_PROTO = "https"

ERROR_IDENTITY_REQUIRED = "IDENTITY_REQUIRED"
ERROR_IDENTITY_NOT_AUTHORIZED = "IDENTITY_NOT_AUTHORIZED"
ERROR_CAPABILITY_DENIED = "CAPABILITY_DENIED"
ERROR_INGRESS_HEADERS_CONFLICT = "INGRESS_HEADERS_CONFLICT"
ERROR_INGRESS_HEADERS_FORBIDDEN = "INGRESS_HEADERS_FORBIDDEN"
ERROR_MUTATION_ORIGIN_FORBIDDEN = "MUTATION_ORIGIN_FORBIDDEN"
ERROR_MUTATION_HEADER_REQUIRED = "MUTATION_HEADER_REQUIRED"
ERROR_AUDIT_UNAVAILABLE = "AUDIT_UNAVAILABLE"
ERROR_NOT_FOUND = "NOT_FOUND"

UNMAPPED_AUDIT_ROLE = "unmapped"


class RoutePolicy:
    """One immutable method+path authorization and audit policy."""

    __slots__ = (
        "method",
        "pattern",
        "channel",
        "capability",
        "additional_capabilities",
        "audit_action",
        "audit_target_type",
        "audit_target_group",
        "companion_mutation",
    )

    def __init__(
        self,
        *,
        method: str,
        template: str,
        channel: str = CHANNEL_TAILSCALE,
        capability: str | None = None,
        additional_capabilities: tuple[str, ...] = (),
        audit_action: str | None = None,
        audit_target_type: str | None = None,
        audit_target_group: str | None = None,
        companion_mutation: bool = False,
    ) -> None:
        if additional_capabilities and capability is None:
            raise ValueError(
                "additional capabilities require a primary capability"
            )
        self.method = method
        self.pattern = _compile_template(template)
        self.channel = channel
        self.capability = capability
        self.additional_capabilities = additional_capabilities
        self.audit_action = audit_action
        self.audit_target_type = audit_target_type
        self.audit_target_group = audit_target_group
        self.companion_mutation = companion_mutation

    def match(self, method: str, path: str) -> re.Match[str] | None:
        if method != self.method:
            return None
        return self.pattern.fullmatch(path)


_UPLOAD_ID_PATTERN = (
    "[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    "[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _compile_template(template: str) -> re.Pattern[str]:
    parts: list[str] = []
    cursor = 0
    for match in re.finditer(r"\{([a-z_]+)\}", template):
        parts.append(re.escape(template[cursor:match.start()]))
        name = match.group(1)
        segment = _UPLOAD_ID_PATTERN if name == "upload_id" else "[^/]+"
        parts.append(f"(?P<{name}>{segment})")
        cursor = match.end()
    parts.append(re.escape(template[cursor:]))
    return re.compile("".join(parts))


ROUTE_POLICIES: tuple[RoutePolicy, ...] = (
    RoutePolicy(method="GET", template="/health", channel="any"),
    RoutePolicy(method="GET", template="/"),
    RoutePolicy(method="GET", template="/assets/{asset_name}"),
    RoutePolicy(method="GET", template="/api/identity/me"),
    RoutePolicy(method="GET", template="/api/audience/me"),
    RoutePolicy(method="GET", template="/api/status/cloud"),
    RoutePolicy(
        method="GET", template="/api/libraries", capability=CAPABILITY_GALLERY_READ
    ),
    RoutePolicy(
        method="POST",
        template="/api/libraries/{library_id}/scan-preview",
        capability=CAPABILITY_LIBRARY_SCAN,
        audit_action="library.scan_preview",
        audit_target_type="library",
        audit_target_group="library_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/libraries/{library_id}/media-imports",
        capability=CAPABILITY_MEDIA_IMPORT,
        audit_action="media.import",
        audit_target_type="library",
        audit_target_group="library_id",
    ),
    RoutePolicy(
        method="GET", template="/api/media", capability=CAPABILITY_GALLERY_READ
    ),
    RoutePolicy(
        method="GET",
        template="/api/workspace/media",
        capability=CAPABILITY_MEDIA_WORKSPACE_READ,
    ),
    RoutePolicy(
        method="POST",
        template="/api/workspace/media/{media_id}/analysis-proposals",
        capability=CAPABILITY_ANALYSIS_PROPOSE,
        audit_action="analysis.propose",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/media",
        capability=CAPABILITY_MEDIA_WORKFLOW_READ,
        audit_action="media.workflow.list",
        audit_target_type="media_workflow",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/analysis-proposals",
        capability=CAPABILITY_MEDIA_WORKFLOW_READ,
        audit_action="analysis.proposals.list",
        audit_target_type="analysis_proposal",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/media/{media_id}/aliases",
        capability=CAPABILITY_MEDIA_WORKFLOW_READ,
        additional_capabilities=(CAPABILITY_METADATA_ALIAS_TEAM_READ,),
        audit_action="metadata.alias.team.list",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="PUT",
        template="/api/admin/media/{media_id}/content-publication",
        capability=CAPABILITY_MEDIA_CONTENT_PUBLISH,
        audit_action="media.content_publish",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/media/{media_id}/catalog-removal",
        capability=CAPABILITY_MEDIA_CATALOG_REMOVE,
        audit_action="media.catalog_removal_preview",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/media/{media_id}/catalog-removal",
        capability=CAPABILITY_MEDIA_CATALOG_REMOVE,
        audit_action="media.catalog_remove",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/catalog-removal-receipts/{receipt_id}/cleanup-retry",
        capability=CAPABILITY_MEDIA_CATALOG_REMOVE,
        audit_action="media.catalog_removal_cleanup_retry",
        audit_target_type="catalog_removal_receipt",
        audit_target_group="receipt_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/metadata",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/media/{media_id}/metadata",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
        audit_action="metadata.save",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/alias",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/ai-suggestions",
        capability=CAPABILITY_METADATA_ALIAS_WRITE,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/media/{media_id}/alias",
        capability=CAPABILITY_METADATA_ALIAS_WRITE,
        audit_action="metadata.alias.save",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/canonical-tags",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="POST",
        template="/api/canonical-tags",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
        audit_action="canonical_tag.create",
        audit_target_type="canonical_tag",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/locations/{location_id}/content",
        capability=CAPABILITY_MEDIA_ORIGINAL_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/locations/{location_id}/download",
        capability=CAPABILITY_MEDIA_DOWNLOAD,
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/locations/{location_id}/gallery-preview",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/locations/{location_id}/cover-timeline",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/locations/{location_id}/cover-frame",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/media/{media_id}/locations/{location_id}/cover",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
        audit_action="media.cover_set",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/cover-thumbnail",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/media/{media_id}/cover",
        capability=CAPABILITY_METADATA_CANONICAL_WRITE,
    ),
    RoutePolicy(
        method="POST",
        template="/api/libraries/{library_id}/media-analysis-preview",
        capability=CAPABILITY_ANALYSIS_RUN,
        audit_action="analysis.preview",
        audit_target_type="library",
        audit_target_group="library_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/libraries/{library_id}/media-suggestion-preview",
        capability=CAPABILITY_ANALYSIS_RUN,
        audit_action="analysis.suggestion_preview",
        audit_target_type="library",
        audit_target_group="library_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/media/{media_id}/locations/{location_id}/ai-suggestion-preview",
        capability=CAPABILITY_ANALYSIS_RUN,
        audit_action="analysis.suggestion_preview",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/ai/media-suggestion-capability",
        capability=CAPABILITY_PROVIDER_OPERATE,
    ),
    RoutePolicy(
        method="GET",
        template="/api/ai/automatic-analysis-capability",
        capability=CAPABILITY_PROVIDER_OPERATE,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/admin/settings/automatic-analysis",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="settings.automatic_analysis.put",
        audit_target_type="settings",
        companion_mutation=True,
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/ai/providers",
        capability=CAPABILITY_PROVIDER_OPERATE,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/admin/ai/providers/{provider_id}",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.provider.put",
        audit_target_type="ai_provider",
        audit_target_group="provider_id",
    ),
    RoutePolicy(
        method="DELETE",
        template="/api/admin/ai/providers/{provider_id}",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.provider.delete",
        audit_target_type="ai_provider",
        audit_target_group="provider_id",
    ),
    RoutePolicy(
        method="PUT",
        template="/api/admin/ai/active-selection",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.provider.activate",
        audit_target_type="ai_provider",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/ai/ping",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.provider.ping",
        audit_target_type="ai_provider",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/ai/pong",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.provider.pong",
        audit_target_type="ai_provider",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/ai/research-settings",
        capability=CAPABILITY_PROVIDER_OPERATE,
    ),
    RoutePolicy(
        method="PUT",
        template="/api/admin/ai/research-settings",
        capability=CAPABILITY_PROVIDER_OPERATE,
        audit_action="ai.research.settings.update",
        audit_target_type="research_settings",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/automatic-analysis",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="POST",
        template="/api/media/{media_id}/locations/{location_id}/durable-analysis",
        capability=CAPABILITY_ANALYSIS_RUN,
        audit_action="analysis.durable_request",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/media/{media_id}/movie-identification",
        capability=CAPABILITY_GALLERY_READ,
    ),
    RoutePolicy(
        method="POST",
        template="/api/media/{media_id}/locations/{location_id}/movie-identification",
        capability=CAPABILITY_ANALYSIS_RUN,
        audit_action="analysis.movie_identification_request",
        audit_target_type="media",
        audit_target_group="media_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/operator/youtube/claims/{claim_id}",
        channel=CHANNEL_LOCAL,
    ),
    RoutePolicy(
        method="POST",
        template="/api/operator/youtube/claims",
        channel=CHANNEL_LOCAL,
    ),
    RoutePolicy(
        method="POST",
        template="/api/operator/youtube/claims/{claim_id}/retry",
        channel=CHANNEL_LOCAL,
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/youtube/claims/{claim_id}",
        capability=CAPABILITY_YOUTUBE_ACQUIRE,
        audit_target_type="youtube_claim",
        audit_target_group="claim_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/youtube/claims",
        capability=CAPABILITY_YOUTUBE_ACQUIRE,
        audit_action="youtube.claim.submit",
        audit_target_type="youtube_claim",
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/youtube/claims/{claim_id}/retry",
        capability=CAPABILITY_YOUTUBE_ACQUIRE,
        audit_action="youtube.claim.retry",
        audit_target_type="youtube_claim",
        audit_target_group="claim_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/youtube/requests",
        capability=CAPABILITY_YOUTUBE_REQUEST,
    ),
    RoutePolicy(
        method="GET",
        template="/api/youtube/requests/{request_id}",
        capability=CAPABILITY_YOUTUBE_REQUEST,
        audit_target_type="youtube_request",
        audit_target_group="request_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/youtube/requests",
        capability=CAPABILITY_YOUTUBE_REQUEST,
        audit_action="youtube.request.submit",
        audit_target_type="youtube_request",
    ),
    RoutePolicy(
        method="POST",
        template="/api/youtube/requests/{request_id}/retry",
        capability=CAPABILITY_YOUTUBE_REQUEST,
        audit_action="youtube.request.retry",
        audit_target_type="youtube_request",
        audit_target_group="request_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/x/requests/{claim_id}",
        capability=CAPABILITY_X_ACQUIRE,
        audit_target_type="x_claim",
        audit_target_group="claim_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/x/requests",
        capability=CAPABILITY_X_REQUEST,
    ),
    RoutePolicy(
        method="GET",
        template="/api/x/requests/{claim_id}",
        capability=CAPABILITY_X_REQUEST,
        audit_target_type="x_request",
        audit_target_group="claim_id",
    ),
    RoutePolicy(
        method="GET",
        template="/api/x/companion/media",
        capability=CAPABILITY_X_REQUEST,
    ),
    RoutePolicy(
        method="GET",
        template="/api/companion/review-inbox",
        capability=CAPABILITY_MEDIA_WORKFLOW_READ,
    ),
    RoutePolicy(
        method="GET",
        template="/api/companion/own-history",
        capability=CAPABILITY_X_REQUEST,
    ),
    RoutePolicy(
        method="GET",
        template="/api/companion/review-inbox/{media_id}",
        capability=CAPABILITY_MEDIA_WORKFLOW_READ,
    ),
    RoutePolicy(
        method="POST",
        template="/api/companion/review-inbox/{media_id}/opened",
        capability=CAPABILITY_X_REQUEST,
        audit_action="companion.review.open",
        audit_target_type="media",
        audit_target_group="media_id",
        companion_mutation=True,
    ),
    RoutePolicy(
        method="POST",
        template="/api/companion/review-inbox/{media_id}/apply",
        capability=CAPABILITY_MEDIA_CONTENT_PUBLISH,
        additional_capabilities=(CAPABILITY_METADATA_CANONICAL_WRITE,),
        audit_action="companion.review.apply_publish",
        audit_target_type="media",
        audit_target_group="media_id",
        companion_mutation=True,
    ),
    RoutePolicy(
        method="POST",
        template="/api/x/requests",
        capability=CAPABILITY_X_REQUEST,
        audit_action="x.request.submit",
        audit_target_type="x_request",
        companion_mutation=True,
    ),
    RoutePolicy(
        method="POST",
        template="/api/x/requests/{claim_id}/retry",
        capability=CAPABILITY_X_REQUEST,
        audit_action="x.request.retry",
        audit_target_type="x_request",
        audit_target_group="claim_id",
        companion_mutation=True,
    ),
    RoutePolicy(
        method="POST",
        template="/api/uploads",
        capability=CAPABILITY_UPLOAD_SUBMIT,
        audit_action="upload.create",
        audit_target_type="upload_session",
    ),
    RoutePolicy(
        method="GET",
        template="/api/uploads/capability",
        capability=CAPABILITY_UPLOAD_SUBMIT,
    ),
    RoutePolicy(
        method="GET",
        template="/api/uploads/{upload_id}",
        capability=CAPABILITY_UPLOAD_SUBMIT,
    ),
    RoutePolicy(
        method="PATCH",
        template="/api/uploads/{upload_id}",
        capability=CAPABILITY_UPLOAD_SUBMIT,
        audit_action="upload.chunk",
        audit_target_type="upload_session",
        audit_target_group="upload_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/uploads/{upload_id}/complete",
        capability=CAPABILITY_UPLOAD_SUBMIT,
        audit_action="upload.complete",
        audit_target_type="upload_session",
        audit_target_group="upload_id",
    ),
    RoutePolicy(
        method="POST",
        template="/api/uploads/{upload_id}/duplicate-resolution",
        capability=CAPABILITY_UPLOAD_SUBMIT,
        audit_action="upload.duplicate_resolution",
        audit_target_type="upload_session",
        audit_target_group="upload_id",
    ),
    RoutePolicy(
        method="DELETE",
        template="/api/uploads/{upload_id}",
        capability=CAPABILITY_UPLOAD_SUBMIT,
        audit_action="upload.cancel",
        audit_target_type="upload_session",
        audit_target_group="upload_id",
    ),
    RoutePolicy(method="GET", template="/api/research/capabilities"),
    RoutePolicy(
        method="GET",
        template="/api/research-requests",
        capability=CAPABILITY_RESEARCH_RUN,
    ),
    RoutePolicy(
        method="POST",
        template="/api/research-requests",
        capability=CAPABILITY_RESEARCH_RUN,
        audit_action="research.request",
    ),
    RoutePolicy(
        method="GET",
        template="/api/research-requests/{operation_id}",
        capability=CAPABILITY_RESEARCH_RUN,
    ),
    RoutePolicy(
        method="POST",
        template="/api/research-requests/{operation_id}/cancel",
        capability=CAPABILITY_RESEARCH_RUN,
        audit_action="research.cancel",
    ),
    RoutePolicy(
        method="GET",
        template="/api/admin/research-requests",
        capability=CAPABILITY_RECORDS_APPROVE,
    ),
    RoutePolicy(method="GET", template="/api/my/records"),
    RoutePolicy(method="GET", template="/api/timeline"),
    RoutePolicy(method="GET", template="/api/records/{record_id}"),
    RoutePolicy(method="GET", template="/api/records/{record_id}/render"),
    RoutePolicy(
        method="GET",
        template="/api/admin/records",
        capability=CAPABILITY_RECORDS_APPROVE,
    ),
    RoutePolicy(
        method="POST",
        template="/api/admin/records/{record_id}/approval",
        capability=CAPABILITY_RECORDS_APPROVE,
        audit_action="records.approval",
    ),
)

_UNCLASSIFIED_FALLBACK_POLICY = RoutePolicy(method="", template="/_fallback")


def find_route_policy(
    method: str, path: str
) -> tuple[RoutePolicy, re.Match[str] | None]:
    """Return the single governing policy and its path match, or the fallback.

    The fallback is fail-closed: a route without an explicit policy is never
    served through the remote channel, regardless of identity or role.
    """
    for policy in ROUTE_POLICIES:
        match = policy.match(method, path)
        if match is not None:
            return policy, match
    return _UNCLASSIFIED_FALLBACK_POLICY, None


class TailscaleIngressMiddleware:
    """Bind header trust to the production UDS ingress and enforce access."""

    def __init__(
        self,
        app: Callable[[Scope, Receive, Send], Awaitable[None]],
        *,
        identity_mapping: Mapping[str, IdentityMappingEntry],
        external_origin: str,
        audit_recorder: object,
        companion_extension_origins: tuple[str, ...] = (),
        local_identity: IdentityContext | None = None,
    ) -> None:
        if audit_recorder is None:
            raise TypeError("security audit recorder is required")
        self._app = app
        self._identity_mapping = identity_mapping
        self._external_origin = external_origin
        self._external_host = external_origin.removeprefix("https://")
        self._audit_recorder = audit_recorder
        self._companion_extension_origins = frozenset(companion_extension_origins)
        self._local_identity = local_identity

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope[SCOPE_REQUEST_ID] = request_id
        method = str(scope.get("method", "")).upper()
        path = str(scope.get("path", ""))
        header_map = _collect_headers(scope)
        policy, policy_match = find_route_policy(method, path)

        if not _has_remote_markers(header_map):
            await self._handle_local_channel(
                scope, receive, send, method=method, path=path, request_id=request_id
            )
            return

        conflict = _singleton_conflict(header_map)
        if conflict is not None:
            await _send_error(
                send,
                status=400,
                code=ERROR_INGRESS_HEADERS_CONFLICT,
                message="The ingress request headers are conflicting.",
                request_id=request_id,
            )
            return
        if not self._forwarded_values_valid(header_map):
            await _send_error(
                send,
                status=403,
                code=ERROR_INGRESS_HEADERS_FORBIDDEN,
                message="The ingress request headers are forbidden.",
                request_id=request_id,
            )
            return

        if policy_match is None:
            await _send_error(
                send,
                status=404,
                code=ERROR_NOT_FOUND,
                message="Not found.",
                request_id=request_id,
            )
            return

        login = _decode_text(_single_value(header_map, HEADER_TAILSCALE_USER_LOGIN))
        display_name = _decode_text(_single_value(header_map, HEADER_TAILSCALE_USER_NAME))
        if not login:
            await _send_error(
                send,
                status=401,
                code=ERROR_IDENTITY_REQUIRED,
                message="A verified Tailscale identity is required.",
                request_id=request_id,
            )
            return

        if policy.channel == CHANNEL_LOCAL:
            await _send_error(
                send,
                status=404,
                code=ERROR_NOT_FOUND,
                message="Not found.",
                request_id=request_id,
            )
            return

        if method in _UNSAFE_METHODS:
            origin = _decode_text(_single_value(header_map, HEADER_ORIGIN))
            if not _mutation_origin_allowed(
                origin=origin,
                external_origin=self._external_origin,
                companion_mutation=policy.companion_mutation,
                companion_origins=self._companion_extension_origins,
            ):
                await _send_error(
                    send,
                    status=403,
                    code=ERROR_MUTATION_ORIGIN_FORBIDDEN,
                    message="The mutation origin is forbidden.",
                    request_id=request_id,
                )
                return
            if not _mutation_header_authorized(header_map):
                await _send_error(
                    send,
                    status=403,
                    code=ERROR_MUTATION_HEADER_REQUIRED,
                    message=(
                        "The FrameNest or Kronika mutation header is required "
                        "and must be exactly 1."
                    ),
                    request_id=request_id,
                )
                return

        try:
            identity = resolve_identity(
                login=login,
                display_name=display_name,
                mapping=self._identity_mapping,
            )
        except FrameNestIdentityAccessError:
            await _send_error(
                send,
                status=401,
                code=ERROR_IDENTITY_REQUIRED,
                message="A verified Tailscale identity is required.",
                request_id=request_id,
            )
            return
        if identity is None:
            await self._record_denial(
                policy=policy,
                policy_match=policy_match,
                request_id=request_id,
                actor_login=login,
                actor_key=_normalized_key_or_fallback(login),
                role=UNMAPPED_AUDIT_ROLE,
            )
            await _send_error(
                send,
                status=403,
                code=ERROR_IDENTITY_NOT_AUTHORIZED,
                message="The verified Tailscale identity is not authorized.",
                request_id=request_id,
            )
            return

        missing_capability = (
            policy.capability is not None
            and not identity.has_capability(policy.capability)
        ) or any(
            not identity.has_capability(extra)
            for extra in policy.additional_capabilities
        )
        if missing_capability:
            await self._record_denial(
                policy=policy,
                policy_match=policy_match,
                request_id=request_id,
                actor_login=identity.login,
                actor_key=identity.login_key,
                role=identity.role,
                identity=identity,
            )
            await _send_error(
                send,
                status=403,
                code=ERROR_CAPABILITY_DENIED,
                message=(
                    "The verified Tailscale identity is not authorized "
                    "for this action."
                ),
                request_id=request_id,
            )
            return

        allowed_event_id: str | None = None
        if policy.audit_action is not None:
            allowed_event_id = await self._record_allowed_attempt(
                policy=policy,
                policy_match=policy_match,
                request_id=request_id,
                identity=identity,
            )
            if allowed_event_id is None:
                await _send_error(
                    send,
                    status=500,
                    code=ERROR_AUDIT_UNAVAILABLE,
                    message="The privileged action could not be recorded.",
                    request_id=request_id,
                )
                return

        scope[SCOPE_INGRESS_CHANNEL] = CHANNEL_TAILSCALE
        scope[SCOPE_IDENTITY] = identity
        if allowed_event_id is not None:
            scope[SCOPE_AUDIT_EVENT_ID] = allowed_event_id

        async def audited_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                message = _with_request_id_header(message, request_id)
                if allowed_event_id is not None:
                    await self._finalize_allowed(
                        allowed_event_id, int(message["status"])
                    )
            await send(message)

        await self._app(scope, receive, audited_send)

    async def _handle_local_channel(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        *,
        method: str,
        path: str,
        request_id: str,
    ) -> None:
        if method == "GET" and path == "/health":
            scope[SCOPE_INGRESS_CHANNEL] = CHANNEL_LOCAL
            await self._app(scope, receive, send)
            return
        operator = path == _OPERATOR_PATH_PREFIX or path.startswith(
            _OPERATOR_PATH_PREFIX + "/"
        )
        if operator:
            scope[SCOPE_INGRESS_CHANNEL] = CHANNEL_LOCAL_OPERATOR
        else:
            scope[SCOPE_INGRESS_CHANNEL] = CHANNEL_LOCAL
        if self._local_identity is not None:
            scope[SCOPE_IDENTITY] = self._local_identity
            await self._app(scope, receive, send)
            return
        if operator:
            await self._app(scope, receive, send)
            return
        await _send_error(
            send,
            status=401,
            code=ERROR_IDENTITY_REQUIRED,
            message="A verified Tailscale identity is required.",
            request_id=request_id,
        )

    def _forwarded_values_valid(self, header_map: dict[bytes, list[bytes]]) -> bool:
        proto = _decode_text(_single_value(header_map, HEADER_X_FORWARDED_PROTO))
        if proto is not None and proto != EXPECTED_FORWARDED_PROTO:
            return False
        host = _decode_text(_single_value(header_map, HEADER_X_FORWARDED_HOST))
        if host is not None and host != self._external_host:
            return False
        return True

    async def _record_allowed_attempt(
        self,
        *,
        policy: RoutePolicy,
        policy_match: re.Match[str] | None,
        request_id: str,
        identity: IdentityContext,
    ) -> str | None:
        """Record the authorized attempt before the mutation may execute."""
        assert policy.audit_action is not None
        assert policy.capability is not None
        try:
            event = SecurityAuditEvent.new(
                request_id=request_id,
                actor_login=identity.login,
                actor_key=identity.login_key,
                identity_provenance=identity.provenance,
                role=identity.role,
                capability=policy.capability,
                action=policy.audit_action,
                target_type=policy.audit_target_type or "request",
                target_id=_audit_target_id(policy, policy_match),
                outcome=AUDIT_OUTCOME_ALLOWED,
                http_status=None,
            )
        except FrameNestSecurityAuditError:
            _log_audit_write_failure("record_allowed_attempt")
            return None
        try:
            await anyio.to_thread.run_sync(self._record_event, event)
        except Exception:
            _log_audit_write_failure("record_allowed_attempt")
            return None
        return event.id

    async def _finalize_allowed(self, event_id: str, http_status: int) -> None:
        """Best-effort HTTP status stamp for one already-recorded event."""

        def finalize() -> None:
            record_http_status = getattr(self._audit_recorder, "record_http_status")
            record_http_status(event_id, http_status)

        try:
            await anyio.to_thread.run_sync(finalize)
        except Exception:
            _log_audit_write_failure("finalize_allowed")

    async def _record_denial(
        self,
        *,
        policy: RoutePolicy,
        policy_match: re.Match[str] | None,
        request_id: str,
        actor_login: str,
        actor_key: str,
        role: str,
        identity: IdentityContext | None = None,
    ) -> None:
        if policy.audit_action is None or policy.capability is None:
            return
        try:
            event = SecurityAuditEvent.new(
                request_id=request_id,
                actor_login=actor_login,
                actor_key=actor_key,
                identity_provenance=(
                    identity.provenance if identity is not None else "tailscale-serve"
                ),
                role=role,
                capability=policy.capability,
                action=policy.audit_action,
                target_type=policy.audit_target_type or "request",
                target_id=_audit_target_id(policy, policy_match),
                outcome=AUDIT_OUTCOME_DENIED,
                http_status=403,
            )
        except FrameNestSecurityAuditError:
            _log_audit_write_failure("record_denial")
            return
        try:
            await anyio.to_thread.run_sync(self._record_event, event)
        except Exception:
            _log_audit_write_failure("record_denial")

    def _record_event(self, event: SecurityAuditEvent) -> None:
        record = getattr(self._audit_recorder, "record")
        record(event)


def _collect_headers(scope: Scope) -> dict[bytes, list[bytes]]:
    collected: dict[bytes, list[bytes]] = {}
    raw_headers = scope.get("headers") or ()
    for raw_name, raw_value in raw_headers:
        name = bytes(raw_name).lower()
        collected.setdefault(name, []).append(bytes(raw_value))
    return collected


def _has_remote_markers(header_map: dict[bytes, list[bytes]]) -> bool:
    return any(header_map.get(marker) for marker in _REMOTE_MARKER_HEADERS)


def _singleton_conflict(header_map: dict[bytes, list[bytes]]) -> bytes | None:
    for name in _SINGLETON_SECURITY_HEADERS:
        values = header_map.get(name)
        if values is not None and len(values) > 1:
            return name
    return None


def _single_value(header_map: dict[bytes, list[bytes]], name: bytes) -> bytes | None:
    values = header_map.get(name)
    if not values:
        return None
    return values[0]


def _mutation_header_authorized(header_map: dict[bytes, list[bytes]]) -> bool:
    """Return True when the mutation gate accepts the request headers.

    Either accepted spelling authorizes a mutation on its own, and a spelling
    that is present must carry exactly ``EXPECTED_MUTATION_HEADER_VALUE``. When
    both spellings are present, both must carry it, so a request cannot present
    one authorized spelling alongside a rejected one. A header that is absent
    entirely never authorizes, exactly as before.
    """
    present = False
    for name in MUTATION_HEADERS:
        value = _single_value(header_map, name)
        if value is None:
            continue
        present = True
        if value != EXPECTED_MUTATION_HEADER_VALUE:
            return False
    return present


def _decode_text(value: bytes | None) -> str | None:
    if value is None:
        return None
    return value.decode("utf-8", errors="replace")


def _mutation_origin_allowed(
    *,
    origin: str | None,
    external_origin: str,
    companion_mutation: bool,
    companion_origins: frozenset[str],
) -> bool:
    if origin is None:
        return False
    if origin == external_origin:
        return True
    return companion_mutation and origin in companion_origins


def _normalized_key_or_fallback(login: str) -> str:
    try:
        from kronika.domain.identity_access import normalize_login

        return normalize_login(login)
    except FrameNestIdentityAccessError:
        return "invalid"


def _audit_target_id(
    policy: RoutePolicy, policy_match: re.Match[str] | None
) -> str | None:
    if policy.audit_target_group is None or policy_match is None:
        return None
    return policy_match.group(policy.audit_target_group)


def _with_request_id_header(message: Message, request_id: str) -> Message:
    headers = list(message.get("headers") or [])
    headers.append((HEADER_REQUEST_ID, request_id.encode("ascii")))
    return {**message, "headers": headers}


async def _send_error(
    send: Send,
    *,
    status: int,
    code: str,
    message: str,
    request_id: str,
) -> None:
    body = json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"cache-control", b"no-store"),
                (HEADER_REQUEST_ID, request_id.encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


def _log_audit_write_failure(operation: str) -> None:
    LOGGER.emit(
        level="CRITICAL",
        event="security_audit_write_failed",
        operation=operation,
        error_code="SECURITY_AUDIT_WRITE_FAILED",
        retryable=False,
    )
