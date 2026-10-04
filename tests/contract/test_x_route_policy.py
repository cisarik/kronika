"""Route-policy and requester-private audience tests for the X candidate."""

from __future__ import annotations

import pytest

from kronika.adapters.api.tailscale_ingress import RoutePolicy, find_route_policy
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.domain.identity_access import (
    CAPABILITY_METADATA_ALIAS_TEAM_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    CAPABILITY_METADATA_CANONICAL_WRITE,
    CAPABILITY_MEDIA_CONTENT_PUBLISH,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_PROVIDER_OPERATE,
    CAPABILITY_X_ACQUIRE,
    CAPABILITY_X_REQUEST,
    IdentityContext,
    ROLE_ADMIN,
    ROLE_USER,
    CAPABILITIES_BY_ROLE,
)


def _identity(login: str, role: str) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role=role,
        capabilities=CAPABILITIES_BY_ROLE[role],
        provenance="tailscale-serve",
    )


def test_x_request_routes_require_x_request_capability() -> None:
    for method, path in [
        ("POST", "/api/x/requests"),
        ("GET", "/api/x/requests"),
        ("GET", "/api/x/requests/00000000-0000-4000-8000-000000000000"),
        ("POST", "/api/x/requests/00000000-0000-4000-8000-000000000000/retry"),
        ("GET", "/api/x/companion/media"),
    ]:
        policy, match = find_route_policy(method, path)
        assert match is not None
        assert policy.capability == CAPABILITY_X_REQUEST


def test_only_companion_mutations_are_companion_flagged() -> None:
    submit, submit_match = find_route_policy("POST", "/api/x/requests")
    retry, retry_match = find_route_policy(
        "POST", "/api/x/requests/00000000-0000-4000-8000-000000000000/retry"
    )
    opened, opened_match = find_route_policy(
        "POST",
        "/api/companion/review-inbox/00000000-0000-4000-8000-000000000000/opened",
    )
    apply, apply_match = find_route_policy(
        "POST",
        "/api/companion/review-inbox/00000000-0000-4000-8000-000000000000/apply",
    )
    settings_put, settings_put_match = find_route_policy(
        "PUT",
        "/api/admin/settings/automatic-analysis",
    )
    picker, picker_match = find_route_policy("GET", "/api/x/companion/media")
    tags, tags_match = find_route_policy("POST", "/api/canonical-tags")
    alias_get, alias_get_match = find_route_policy(
        "GET", "/api/media/00000000-0000-4000-8000-000000000000/alias"
    )
    alias_put, alias_put_match = find_route_policy(
        "PUT", "/api/media/00000000-0000-4000-8000-000000000000/alias"
    )
    assert submit_match is not None and submit.companion_mutation is True
    assert retry_match is not None and retry.companion_mutation is True
    assert opened_match is not None and opened.companion_mutation is True
    assert apply_match is not None and apply.companion_mutation is True
    assert settings_put_match is not None and settings_put.companion_mutation is True
    assert settings_put.capability == CAPABILITY_PROVIDER_OPERATE
    assert settings_put.audit_action == "settings.automatic_analysis.put"
    assert picker_match is not None and picker.companion_mutation is False
    assert tags_match is not None and tags.companion_mutation is False
    assert alias_get_match is not None and alias_get.companion_mutation is False
    assert alias_put_match is not None and alias_put.companion_mutation is False
    assert alias_put.capability == CAPABILITY_METADATA_ALIAS_WRITE
    suggestions, suggestions_match = find_route_policy(
        "GET",
        "/api/media/00000000-0000-4000-8000-000000000000/ai-suggestions",
    )
    assert suggestions_match is not None and suggestions.companion_mutation is False
    assert suggestions.capability == CAPABILITY_METADATA_ALIAS_WRITE
    inbox, inbox_match = find_route_policy("GET", "/api/companion/review-inbox")
    detail, detail_match = find_route_policy(
        "GET",
        "/api/companion/review-inbox/00000000-0000-4000-8000-000000000000",
    )
    assert inbox_match is not None and inbox.companion_mutation is False
    assert detail_match is not None and detail.companion_mutation is False
    assert inbox.capability == CAPABILITY_MEDIA_WORKFLOW_READ
    assert detail.capability == CAPABILITY_MEDIA_WORKFLOW_READ
    assert inbox.additional_capabilities == ()
    assert detail.additional_capabilities == ()
    assert opened.capability == CAPABILITY_X_REQUEST
    assert opened.additional_capabilities == ()
    assert apply.capability == CAPABILITY_MEDIA_CONTENT_PUBLISH
    assert apply.additional_capabilities == (CAPABILITY_METADATA_CANONICAL_WRITE,)
    from kronika.adapters.api.tailscale_ingress import ROUTE_POLICIES

    own, own_match = find_route_policy("GET", "/api/companion/own-history")
    assert own_match is not None and own.companion_mutation is False
    assert own.capability == CAPABILITY_X_REQUEST
    assert own.additional_capabilities == ()

    flagged = [policy for policy in ROUTE_POLICIES if policy.companion_mutation]
    assert {(policy.method, policy.pattern.pattern) for policy in flagged} == {
        (submit.method, submit.pattern.pattern),
        (retry.method, retry.pattern.pattern),
        (opened.method, opened.pattern.pattern),
        (apply.method, apply.pattern.pattern),
        (settings_put.method, settings_put.pattern.pattern),
    }


def test_route_policy_additional_capabilities_default_empty() -> None:
    from kronika.adapters.api.tailscale_ingress import ROUTE_POLICIES

    constructed = RoutePolicy(method="GET", template="/health", channel="any")
    assert constructed.additional_capabilities == ()
    assert constructed.capability is None
    apply_policy, apply_match = find_route_policy(
        "POST",
        "/api/companion/review-inbox/00000000-0000-4000-8000-000000000000/apply",
    )
    assert apply_match is not None
    assert apply_policy.additional_capabilities == (
        CAPABILITY_METADATA_CANONICAL_WRITE,
    )
    team_alias, team_alias_match = find_route_policy(
        "GET",
        "/api/admin/media/00000000-0000-4000-8000-000000000000/aliases",
    )
    assert team_alias_match is not None
    assert team_alias.additional_capabilities == (
        CAPABILITY_METADATA_ALIAS_TEAM_READ,
    )
    extras = {
        policy.audit_action: policy.additional_capabilities
        for policy in ROUTE_POLICIES
        if policy.additional_capabilities
    }
    assert extras == {
        "companion.review.apply_publish": (CAPABILITY_METADATA_CANONICAL_WRITE,),
        "metadata.alias.team.list": (CAPABILITY_METADATA_ALIAS_TEAM_READ,),
    }


def test_route_policy_rejects_additional_capabilities_without_primary() -> None:
    with pytest.raises(ValueError, match="additional capabilities require a primary"):
        RoutePolicy(
            method="POST",
            template="/api/synthetic",
            additional_capabilities=("metadata.canonical.write",),
        )


def test_x_admin_route_requires_x_acquire_capability() -> None:
    policy, _match = find_route_policy(
        "GET", "/api/admin/x/requests/00000000-0000-4000-8000-000000000000"
    )
    assert policy is not None
    assert policy.capability == CAPABILITY_X_ACQUIRE


def test_roles_carry_x_capabilities() -> None:
    assert CAPABILITY_X_REQUEST in _identity("user", ROLE_USER).capabilities
    assert CAPABILITY_X_REQUEST in _identity("admin", ROLE_ADMIN).capabilities
    assert CAPABILITY_X_ACQUIRE not in _identity("user", ROLE_USER).capabilities
    assert CAPABILITY_X_ACQUIRE in _identity("admin", ROLE_ADMIN).capabilities
    assert CAPABILITY_METADATA_ALIAS_WRITE in _identity("user", ROLE_USER).capabilities
    assert CAPABILITY_METADATA_ALIAS_WRITE in _identity("admin", ROLE_ADMIN).capabilities


class _Repo:
    def __init__(self, outcome: bool = False) -> None:
        self._outcome = outcome

    def media_exists(self, media_id: object) -> bool:
        return False

    def is_published(self, media_id: object) -> bool:
        return False

    def has_live_requester_media_access(
        self, *, media_id: object, login_key: str
    ) -> bool:
        return self._outcome


def test_requester_can_read_own_private_media() -> None:
    from kronika.domain.identities import MediaId

    policy = ContentAudiencePolicy(
        _Repo(outcome=True), x_requester_private_access=_Repo(outcome=True)
    )
    identity = _identity("alice", ROLE_USER)
    assert policy.may_read(MediaId.new(), identity)


def test_foreign_requester_cannot_read_private_media() -> None:
    from kronika.domain.identities import MediaId

    policy = ContentAudiencePolicy(
        _Repo(outcome=False), x_requester_private_access=_Repo(outcome=False)
    )
    assert not policy.may_read(MediaId.new(), _identity("alice", ROLE_USER))
