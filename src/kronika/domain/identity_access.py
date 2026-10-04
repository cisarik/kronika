"""Verified Tailscale Serve ingress identity, roles, and capabilities.

This module is the domain boundary for remote identity. It never performs I/O
and never imports framework or persistence modules. Identity enters only as
plain strings already extracted from the trusted ingress adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from email.header import decode_header, make_header
from typing import Mapping

ROLE_ADMIN = "admin"
ROLE_USER = "user"
ROLES = frozenset({ROLE_ADMIN, ROLE_USER})

IDENTITY_PROVENANCE_TAILSCALE_SERVE = "tailscale-serve"
IDENTITY_PROVENANCE_LOCAL_CONFIG = "local-config"
MAPPING_PROVENANCE_CONFIG = "config"

CAPABILITY_GALLERY_READ = "gallery.read"
CAPABILITY_MEDIA_ORIGINAL_READ = "media.original.read"
CAPABILITY_MEDIA_DOWNLOAD = "media.download"
CAPABILITY_METADATA_CANONICAL_WRITE = "metadata.canonical.write"
CAPABILITY_METADATA_ALIAS_WRITE = "metadata.alias.write"
CAPABILITY_METADATA_ALIAS_TEAM_READ = "metadata.alias.team.read"
CAPABILITY_UPLOAD_SUBMIT = "upload.submit"
CAPABILITY_UPLOAD_MANAGE = "upload.manage"
CAPABILITY_ANALYSIS_RUN = "analysis.run"
CAPABILITY_ANALYSIS_PROPOSE = "analysis.propose"
CAPABILITY_PROVIDER_OPERATE = "provider.operate"
CAPABILITY_MEDIA_IMPORT = "media.import"
CAPABILITY_LIBRARY_SCAN = "library.scan"
CAPABILITY_MEDIA_WORKFLOW_READ = "media.workflow.read"
CAPABILITY_MEDIA_WORKSPACE_READ = "media.workspace.read"
CAPABILITY_MEDIA_CONTENT_PUBLISH = "media.content.publish"
CAPABILITY_YOUTUBE_REQUEST = "youtube.request"
CAPABILITY_YOUTUBE_ACQUIRE = "youtube.acquire"
CAPABILITY_X_REQUEST = "x.request"
CAPABILITY_X_ACQUIRE = "x.acquire"
CAPABILITY_MEDIA_CATALOG_REMOVE = "media.catalog.remove"
CAPABILITY_RESEARCH_RUN = "research.run"
CAPABILITY_RECORDS_APPROVE = "records.approve"

AUDIENCE_PUBLIC_PUBLISHED = "public_published"
AUDIENCE_TAILSCALE_WORKSPACE = "tailscale_workspace"
AUDIENCE_TRUSTED_LOOPBACK = "trusted_loopback"

PUBLIC_PUBLISHED_CAPABILITIES = frozenset(
    {
        CAPABILITY_GALLERY_READ,
        CAPABILITY_MEDIA_ORIGINAL_READ,
    }
)

_ORDINARY_CAPABILITIES = frozenset(
    {
        CAPABILITY_GALLERY_READ,
        CAPABILITY_MEDIA_ORIGINAL_READ,
        CAPABILITY_MEDIA_DOWNLOAD,
        CAPABILITY_MEDIA_WORKSPACE_READ,
        CAPABILITY_ANALYSIS_PROPOSE,
        CAPABILITY_UPLOAD_SUBMIT,
        CAPABILITY_YOUTUBE_REQUEST,
        CAPABILITY_X_REQUEST,
        CAPABILITY_METADATA_ALIAS_WRITE,
        CAPABILITY_RESEARCH_RUN,
    }
)
_ADMIN_ONLY_CAPABILITIES = frozenset(
    {
        CAPABILITY_METADATA_CANONICAL_WRITE,
        CAPABILITY_UPLOAD_MANAGE,
        CAPABILITY_ANALYSIS_RUN,
        CAPABILITY_PROVIDER_OPERATE,
        CAPABILITY_MEDIA_IMPORT,
        CAPABILITY_LIBRARY_SCAN,
        CAPABILITY_MEDIA_WORKFLOW_READ,
        CAPABILITY_MEDIA_CONTENT_PUBLISH,
        CAPABILITY_YOUTUBE_ACQUIRE,
        CAPABILITY_X_ACQUIRE,
        CAPABILITY_MEDIA_CATALOG_REMOVE,
        CAPABILITY_METADATA_ALIAS_TEAM_READ,
        CAPABILITY_RECORDS_APPROVE,
    }
)
CAPABILITIES_BY_ROLE: dict[str, frozenset[str]] = {
    ROLE_USER: _ORDINARY_CAPABILITIES,
    ROLE_ADMIN: _ORDINARY_CAPABILITIES | _ADMIN_ONLY_CAPABILITIES,
}

MAX_IDENTITY_MAPPING_ENTRIES = 64
MAX_LOGIN_LENGTH = 254
MAX_DISPLAY_NAME_CODE_POINTS = 200


class FrameNestIdentityAccessError(Exception):
    """Sanitized identity or mapping failure safe for fail-closed handling."""


@dataclass(frozen=True, slots=True)
class IdentityMappingEntry:
    """One explicit configuration-backed login-to-role mapping."""

    login_key: str
    role: str
    provenance: str


@dataclass(frozen=True, slots=True)
class IdentityContext:
    """Immutable verified identity for one request."""

    login: str
    login_key: str
    display_name: str
    role: str
    capabilities: frozenset[str]
    provenance: str

    def has_capability(self, capability: str) -> bool:
        return capability in self.capabilities


def normalize_login(value: object) -> str:
    """Return the deterministic normalized login key or fail closed."""
    if not isinstance(value, str):
        raise FrameNestIdentityAccessError("Identity login is invalid.")
    stripped = value.strip()
    if not stripped or len(stripped) > MAX_LOGIN_LENGTH:
        raise FrameNestIdentityAccessError("Identity login is invalid.")
    if any(_is_forbidden_login_character(character) for character in stripped):
        raise FrameNestIdentityAccessError("Identity login is invalid.")
    return stripped.casefold()


def decode_display_name(value: object) -> str:
    """Decode an RFC 2047 aware display name without ever raising."""
    if not isinstance(value, str):
        return ""
    candidate = value.strip()
    if not candidate:
        return ""
    decoded = candidate
    if "=?" in candidate:
        try:
            decoded = str(make_header(decode_header(candidate)))
        except Exception:
            decoded = candidate
    sanitized = "".join(
        character for character in decoded if not _is_control_character(character)
    ).strip()
    if len(sanitized) > MAX_DISPLAY_NAME_CODE_POINTS:
        sanitized = sanitized[:MAX_DISPLAY_NAME_CODE_POINTS]
    return sanitized


def build_identity_mapping(
    raw_mapping: Mapping[object, object],
) -> dict[str, IdentityMappingEntry]:
    """Validate explicit login-to-role configuration into normalized entries."""
    if len(raw_mapping) > MAX_IDENTITY_MAPPING_ENTRIES:
        raise FrameNestIdentityAccessError("Identity mapping is invalid.")
    mapping: dict[str, IdentityMappingEntry] = {}
    for raw_login, raw_role in raw_mapping.items():
        login_key = normalize_login(raw_login)
        if not isinstance(raw_role, str):
            raise FrameNestIdentityAccessError("Identity mapping is invalid.")
        role = raw_role.strip()
        if role not in ROLES:
            raise FrameNestIdentityAccessError("Identity mapping is invalid.")
        if login_key in mapping:
            raise FrameNestIdentityAccessError("Identity mapping is invalid.")
        mapping[login_key] = IdentityMappingEntry(
            login_key=login_key,
            role=role,
            provenance=MAPPING_PROVENANCE_CONFIG,
        )
    return mapping


def identity_from_mapped_login(
    *,
    login_key: str,
    mapping: Mapping[str, IdentityMappingEntry],
    provenance: str,
) -> IdentityContext:
    """Build an identity from an already configured login. Never invents a role."""
    normalized = normalize_login(login_key)
    entry = mapping.get(normalized)
    if entry is None or provenance not in {
        IDENTITY_PROVENANCE_TAILSCALE_SERVE,
        IDENTITY_PROVENANCE_LOCAL_CONFIG,
        MAPPING_PROVENANCE_CONFIG,
    }:
        raise FrameNestIdentityAccessError("Identity login is invalid.")
    return IdentityContext(
        login=normalized,
        login_key=normalized,
        display_name=normalized,
        role=entry.role,
        capabilities=CAPABILITIES_BY_ROLE[entry.role],
        provenance=provenance,
    )


def resolve_identity(
    *,
    login: object,
    display_name: object,
    mapping: Mapping[str, IdentityMappingEntry],
) -> IdentityContext | None:
    """Resolve a verified Serve login into an identity or ``None`` when unmapped.

    The login is already attested by the trusted ingress. Mapping decides
    authorization; the display name never influences privilege.
    """
    if not isinstance(login, str):
        raise FrameNestIdentityAccessError("Identity login is invalid.")
    original_login = login.strip()
    login_key = normalize_login(original_login)
    entry = mapping.get(login_key)
    if entry is None:
        return None
    resolved_display_name = decode_display_name(display_name)
    return IdentityContext(
        login=original_login,
        login_key=login_key,
        display_name=resolved_display_name if resolved_display_name else original_login,
        role=entry.role,
        capabilities=CAPABILITIES_BY_ROLE[entry.role],
        provenance=IDENTITY_PROVENANCE_TAILSCALE_SERVE,
    )


def mapped_role_has_capability(
    mapping: Mapping[str, IdentityMappingEntry],
    login: str,
    capability: str,
) -> bool:
    """Return whether a mapped login's role holds one capability.

    An unmapped or invalid login is false. This is the same role-capability
    table ``IdentityContext.has_capability`` uses.
    """
    try:
        login_key = normalize_login(login)
    except FrameNestIdentityAccessError:
        return False
    entry = mapping.get(login_key)
    if entry is None:
        return False
    return capability in CAPABILITIES_BY_ROLE.get(entry.role, frozenset())


def _is_forbidden_login_character(character: str) -> bool:
    return character.isspace() or _is_control_character(character)


def _is_control_character(character: str) -> bool:
    code_point = ord(character)
    return code_point < 0x20 or code_point == 0x7F
