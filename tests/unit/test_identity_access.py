"""Unit tests for the verified-identity domain boundary."""

from __future__ import annotations

import pytest

from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_ANALYSIS_PROPOSE,
    CAPABILITY_MEDIA_WORKSPACE_READ,
    CAPABILITY_METADATA_ALIAS_TEAM_READ,
    CAPABILITY_RECORDS_APPROVE,
    CAPABILITY_RESEARCH_RUN,
    CAPABILITY_UPLOAD_MANAGE,
    CAPABILITY_UPLOAD_SUBMIT,
    CAPABILITY_YOUTUBE_ACQUIRE,
    CAPABILITY_YOUTUBE_REQUEST,
    IDENTITY_PROVENANCE_TAILSCALE_SERVE,
    MAPPING_PROVENANCE_CONFIG,
    MAX_IDENTITY_MAPPING_ENTRIES,
    ROLE_ADMIN,
    ROLE_USER,
    FrameNestIdentityAccessError,
    build_identity_mapping,
    decode_display_name,
    normalize_login,
    resolve_identity,
)


def test_normalize_login_strips_and_casefolds_deterministically() -> None:
    assert normalize_login("  Admin@Example.COM ") == "admin@example.com"
    assert normalize_login("aecrypto@gmail.com") == "aecrypto@gmail.com"


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a" * 255,
        "has space@example.com",
        "tab\t@example.com",
        "new\nline@example.com",
        "control@example.com",
        42,
        None,
    ],
)
def test_normalize_login_fails_closed_on_invalid_values(value: object) -> None:
    with pytest.raises(FrameNestIdentityAccessError):
        normalize_login(value)


def test_decode_display_name_handles_rfc2047_encoded_words() -> None:
    assert decode_display_name("=?utf-8?q?J=C3=A1n_Pou=C5=BE=C3=ADvate=C4=BE?=") == (
        "Ján Používateľ"
    )
    assert decode_display_name("=?UTF-8?B?YWUgY3J5cHRv?=") == "ae crypto"


def test_decode_display_name_never_raises_and_strips_controls() -> None:
    assert decode_display_name("=?invalid?=") != ""
    assert decode_display_name(None) == ""
    assert decode_display_name(42) == ""
    assert decode_display_name("  padded  ") == "padded"
    assert decode_display_name("evil\nname") == "evilname"
    assert len(decode_display_name("x" * 500)) == 200


def test_build_identity_mapping_normalizes_and_marks_config_provenance() -> None:
    mapping = build_identity_mapping(
        {"Admin@Example.com": "admin", "user@example.com": "user"}
    )
    assert set(mapping) == {"admin@example.com", "user@example.com"}
    entry = mapping["admin@example.com"]
    assert entry.role == ROLE_ADMIN
    assert entry.provenance == MAPPING_PROVENANCE_CONFIG


@pytest.mark.parametrize(
    "raw_mapping",
    [
        {"admin@example.com": "superuser"},
        {"admin@example.com": ""},
        {"Admin@example.com": "admin", "admin@example.com": "user"},
        {"bad login@example.com": "admin"},
        {"admin@example.com": 1},
    ],
)
def test_build_identity_mapping_rejects_invalid_entries(
    raw_mapping: dict[object, object],
) -> None:
    with pytest.raises(FrameNestIdentityAccessError):
        build_identity_mapping(raw_mapping)


def test_build_identity_mapping_enforces_bounded_size() -> None:
    oversized = {f"user{index}@example.com": "user" for index in range(
        MAX_IDENTITY_MAPPING_ENTRIES + 1
    )}
    with pytest.raises(FrameNestIdentityAccessError):
        build_identity_mapping(oversized)


def test_resolve_identity_maps_admin_with_full_capabilities() -> None:
    mapping = build_identity_mapping({"aecrypto@gmail.com": "admin"})
    identity = resolve_identity(
        login=" aecrypto@gmail.com ",
        display_name="ae crypto",
        mapping=mapping,
    )
    assert identity is not None
    assert identity.login == "aecrypto@gmail.com"
    assert identity.login_key == "aecrypto@gmail.com"
    assert identity.display_name == "ae crypto"
    assert identity.role == ROLE_ADMIN
    assert identity.capabilities == CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert identity.provenance == IDENTITY_PROVENANCE_TAILSCALE_SERVE
    assert identity.has_capability("metadata.canonical.write")
    assert identity.has_capability("metadata.alias.write")
    assert identity.has_capability("metadata.alias.team.read")
    assert identity.has_capability("upload.manage")
    assert identity.has_capability("upload.submit")
    assert identity.has_capability(CAPABILITY_YOUTUBE_ACQUIRE)
    assert identity.has_capability(CAPABILITY_YOUTUBE_REQUEST)


def test_resolve_identity_maps_ordinary_user_with_read_capabilities() -> None:
    mapping = build_identity_mapping({"user@example.com": "user"})
    identity = resolve_identity(
        login="user@example.com",
        display_name="Reader",
        mapping=mapping,
    )
    assert identity is not None
    assert identity.role == ROLE_USER
    assert identity.has_capability("gallery.read")
    assert identity.has_capability("media.original.read")
    assert identity.has_capability("media.download")
    assert identity.has_capability("upload.submit")
    assert identity.has_capability(CAPABILITY_YOUTUBE_REQUEST)
    assert identity.has_capability("media.workspace.read")
    assert identity.has_capability("analysis.propose")
    assert not identity.has_capability("metadata.canonical.write")
    assert identity.has_capability("metadata.alias.write")
    assert not identity.has_capability("metadata.alias.team.read")
    assert not identity.has_capability("upload.manage")
    assert not identity.has_capability("analysis.run")
    assert not identity.has_capability("media.workflow.read")
    assert not identity.has_capability("media.content.publish")
    assert not identity.has_capability(CAPABILITY_YOUTUBE_ACQUIRE)
    assert CAPABILITY_UPLOAD_SUBMIT in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_UPLOAD_MANAGE not in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_YOUTUBE_REQUEST in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_YOUTUBE_REQUEST in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_MEDIA_WORKSPACE_READ in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_MEDIA_WORKSPACE_READ in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_ANALYSIS_PROPOSE in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_ANALYSIS_PROPOSE in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_METADATA_ALIAS_TEAM_READ not in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_METADATA_ALIAS_TEAM_READ in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_YOUTUBE_ACQUIRE not in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_UPLOAD_SUBMIT in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_UPLOAD_MANAGE in CAPABILITIES_BY_ROLE[ROLE_ADMIN]


def test_resolve_identity_returns_none_for_unmapped_login() -> None:
    mapping = build_identity_mapping({"admin@example.com": "admin"})
    assert (
        resolve_identity(
            login="stranger@example.com",
            display_name="Stranger",
            mapping=mapping,
        )
        is None
    )


def test_display_name_change_never_alters_privilege() -> None:
    mapping = build_identity_mapping({"user@example.com": "user"})
    first = resolve_identity(
        login="user@example.com", display_name="First Name", mapping=mapping
    )
    second = resolve_identity(
        login="user@example.com", display_name="Renamed", mapping=mapping
    )
    assert first is not None and second is not None
    assert first.role == second.role
    assert first.capabilities == second.capabilities
    assert first.login_key == second.login_key
    assert second.display_name == "Renamed"


def test_resolve_identity_falls_back_to_login_for_empty_display_name() -> None:
    mapping = build_identity_mapping({"user@example.com": "user"})
    identity = resolve_identity(
        login="user@example.com", display_name="  ", mapping=mapping
    )
    assert identity is not None
    assert identity.display_name == "user@example.com"


def test_resolve_identity_fails_closed_on_malformed_login() -> None:
    mapping = build_identity_mapping({"admin@example.com": "admin"})
    with pytest.raises(FrameNestIdentityAccessError):
        resolve_identity(
            login="bad login@example.com",
            display_name="Bad",
            mapping=mapping,
        )
    with pytest.raises(FrameNestIdentityAccessError):
        resolve_identity(login=None, display_name="Bad", mapping=mapping)


def test_public_published_capabilities_are_exactly_the_two_read_capabilities() -> None:
    from kronika.domain.identity_access import (
        AUDIENCE_PUBLIC_PUBLISHED,
        CAPABILITY_ANALYSIS_PROPOSE,
        CAPABILITY_GALLERY_READ,
        CAPABILITY_MEDIA_ORIGINAL_READ,
        CAPABILITY_METADATA_ALIAS_TEAM_READ,
        PUBLIC_PUBLISHED_CAPABILITIES,
    )

    assert AUDIENCE_PUBLIC_PUBLISHED == "public_published"
    assert PUBLIC_PUBLISHED_CAPABILITIES == {
        CAPABILITY_GALLERY_READ,
        CAPABILITY_MEDIA_ORIGINAL_READ,
    }
    assert CAPABILITY_ANALYSIS_PROPOSE not in PUBLIC_PUBLISHED_CAPABILITIES
    assert CAPABILITY_METADATA_ALIAS_TEAM_READ not in PUBLIC_PUBLISHED_CAPABILITIES


def test_research_and_record_capabilities_are_mapped() -> None:
    assert CAPABILITY_RESEARCH_RUN in CAPABILITIES_BY_ROLE[ROLE_USER]
    assert CAPABILITY_RESEARCH_RUN in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_RECORDS_APPROVE in CAPABILITIES_BY_ROLE[ROLE_ADMIN]
    assert CAPABILITY_RECORDS_APPROVE not in CAPABILITIES_BY_ROLE[ROLE_USER]
