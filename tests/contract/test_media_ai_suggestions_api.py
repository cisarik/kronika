"""Contract tests for the additive website AI suggestion list GET."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from kronika.adapters.api.media_analysis_lifecycle_api import (
    MediaAnalysisLifecycleApiDependencies,
    create_media_analysis_lifecycle_api_router,
)
from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY, find_route_policy
from kronika.application.companion_review import (
    CompanionReviewDetail,
    CompanionReviewSuggestion,
    MappedSuggestedTag,
    MappedTagStatus,
)
from kronika.application.ports.companion_review_repository import (
    CompanionReviewMovieExcludedError,
)
from kronika.domain.content_publication import ContentPublicationReadiness
from kronika.domain.identity_access import (
    CAPABILITIES_BY_ROLE,
    CAPABILITY_GALLERY_READ,
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    IdentityContext,
    ROLE_USER,
)

MEDIA_ID = "12345678-1234-4234-9234-123456789abc"
RUN_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"


class _FakeListSuggestions:
    def __init__(self, *, detail: CompanionReviewDetail | None = None, error: Exception | None = None) -> None:
        self.detail = detail
        self.error = error
        self.calls: list[dict[str, object]] = []

    def execute(self, **kwargs: object) -> CompanionReviewDetail:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.detail is not None
        return self.detail


def _identity(login: str, *, capabilities: frozenset[str] | None = None) -> IdentityContext:
    return IdentityContext(
        login=login,
        login_key=login,
        display_name=login,
        role="user",
        capabilities=capabilities if capabilities is not None else CAPABILITIES_BY_ROLE[ROLE_USER],
        provenance="tailscale-serve",
    )


def _detail() -> CompanionReviewDetail:
    return CompanionReviewDetail(
        media_id=MEDIA_ID,
        display_title="Canonical title",
        description="Canonical description",
        tags=(),
        field_sources={},
        tag_sources={},
        publication=None,
        readiness=ContentPublicationReadiness(ready=False, missing_fields=("display_title",)),
        suggestions=(
            CompanionReviewSuggestion(
                analysis_run_id=RUN_ID,
                completed_at_ms=20,
                provider_id="nvidia-nim",
                model_id="test-model",
                prompt_version="v1",
                title="Suggested title",
                description="Suggested description",
                tags=(
                    MappedSuggestedTag(
                        value="meme",
                        status=MappedTagStatus.MAPPED,
                        key="meme",
                        display_name="Meme",
                    ),
                ),
                suggested_filename="suggested.gif",
            ),
        ),
        next_cursor=None,
    )


def _router_client(
    *,
    identity: IdentityContext | None,
    list_suggestions: _FakeListSuggestions,
) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_media_analysis_lifecycle_api_router(
            MediaAnalysisLifecycleApiDependencies(
                read_analysis=None,
                automatic_analysis_enabled=False,
                provider_configured=False,
                list_suggestions=list_suggestions,  # type: ignore[arg-type]
                audience_policy=__import__(
                    "tests.support.record_access", fromlist=["scoped_policy"]
                ).scoped_policy({MEDIA_ID}),
            )
        )
    )

    @app.middleware("http")
    async def inject_identity(request: Request, call_next):
        if identity is not None:
            request.scope[SCOPE_IDENTITY] = identity
        return await call_next(request)

    return TestClient(app)


def test_ordinary_list_returns_filename_and_run_id() -> None:
    fake = _FakeListSuggestions(detail=_detail())
    client = _router_client(
        identity=_identity("alice@example.com"),
        list_suggestions=fake,
    )
    response = client.get(f"/api/media/{MEDIA_ID}/ai-suggestions?limit=100")
    assert response.status_code == 200
    payload = response.json()
    assert payload["suggestions"][0]["analysis_run_id"] == RUN_ID
    assert payload["suggestions"][0]["title"] == "Suggested title"
    assert payload["suggestions"][0]["description"] == "Suggested description"
    assert payload["suggestions"][0]["suggested_filename"] == "suggested.gif"
    assert payload["suggestions"][0]["provider_id"] == "nvidia-nim"
    assert payload["suggestions"][0]["model_id"] == "test-model"
    assert payload["suggestions"][0]["tags"][0]["key"] == "meme"
    assert fake.calls[0]["media_id"] == MEDIA_ID
    assert fake.calls[0]["actor_login_key"] == "alice@example.com"
    assert fake.calls[0]["limit"] == 100


def test_missing_identity_requires_login() -> None:
    client = _router_client(
        identity=None,
        list_suggestions=_FakeListSuggestions(detail=_detail()),
    )
    response = client.get(f"/api/media/{MEDIA_ID}/ai-suggestions")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "IDENTITY_REQUIRED"


def test_movie_media_is_conflict() -> None:
    client = _router_client(
        identity=_identity("alice@example.com"),
        list_suggestions=_FakeListSuggestions(error=CompanionReviewMovieExcludedError()),
    )
    response = client.get(f"/api/media/{MEDIA_ID}/ai-suggestions")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "COMPANION_REVIEW_MOVIE_EXCLUDED"


def test_public_identity_without_alias_write_is_denied_by_route_policy() -> None:
    public = _identity(
        "public@example.com",
        capabilities=frozenset({CAPABILITY_GALLERY_READ}),
    )
    policy, match = find_route_policy("GET", f"/api/media/{MEDIA_ID}/ai-suggestions")
    assert match is not None
    assert policy.capability == CAPABILITY_METADATA_ALIAS_WRITE
    assert policy.companion_mutation is False
    assert not public.has_capability(policy.capability)
    ordinary = _identity("alice@example.com")
    assert ordinary.has_capability(policy.capability)


def test_ordinary_inbox_detail_stays_workflow_read() -> None:
    ordinary = _identity("alice@example.com")
    policy, match = find_route_policy(
        "GET",
        f"/api/companion/review-inbox/{MEDIA_ID}",
    )
    assert match is not None
    assert policy.capability == CAPABILITY_MEDIA_WORKFLOW_READ
    assert not ordinary.has_capability(policy.capability)
    assert ordinary.has_capability(CAPABILITY_METADATA_ALIAS_WRITE)
