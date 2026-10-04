"""FastAPI routes for caller-private media alias overlays."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, UUID4

from framenest.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_allows,
)
from framenest.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from framenest.application.content_publication import ContentAudiencePolicy
from framenest.application.media_user_alias import EMPTY_ALIAS_VIEW, MediaUserAliasView
from framenest.application.ports.media_user_alias_repository import (
    AliasTagNotFoundError,
    FrameNestMediaUserAliasRepositoryError,
    MediaUserAliasMediaNotFoundError,
)
from framenest.domain import FrameNestIdentityError
from framenest.domain.identities import MediaId
from framenest.domain.identity_access import (
    CAPABILITY_GALLERY_READ,
    CAPABILITY_METADATA_ALIAS_WRITE,
    IdentityContext,
)
from framenest.domain.media_user_alias import FrameNestMediaUserAliasError

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
ALIAS_TAG_NOT_FOUND_CODE = "ALIAS_TAG_NOT_FOUND"
ALIAS_TAG_NOT_FOUND_MESSAGE = "Canonical tag not found."
ALIAS_INVALID_CODE = "ALIAS_INVALID"
ALIAS_INVALID_MESSAGE = "Invalid Kronika media user alias."
ALIAS_OPERATION_FAILED_CODE = "ALIAS_OPERATION_FAILED"
ALIAS_OPERATION_FAILED_MESSAGE = "Media user alias operation failed."


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class MediaAliasResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_title: str | None
    description: str | None
    tag_keys: list[str]


class MediaAliasSaveRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_title: str | None = None
    description: str | None = None
    tag_keys: list[str] | None = None


@dataclass(frozen=True, slots=True)
class MediaAliasApiDependencies:
    """Injected dependencies for caller-private alias overlay routes."""

    get_alias: object | None
    save_alias: object | None
    catalog_available: Callable[[], bool]
    audience_policy: ContentAudiencePolicy | None = None


def create_media_alias_api_router(dependencies: MediaAliasApiDependencies) -> APIRouter:
    """Create the caller-private media alias overlay API router."""
    router = APIRouter()

    @router.get(
        "/api/media/{media_id}/alias",
        response_model=MediaAliasResponse,
        responses={
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_alias(
        media_id: UUID4,
        request: Request,
    ) -> MediaAliasResponse | JSONResponse:
        identity = _require_identity(request, CAPABILITY_GALLERY_READ)
        if not dependencies.catalog_available() or dependencies.get_alias is None:
            return _catalog_unavailable_response()
        parsed_id = _parse_media_id(media_id)
        if parsed_id is None:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        try:
            if not content_audience_allows(
                request=request,
                media_id=parsed_id,
                policy=dependencies.audience_policy,
            ):
                return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except ContentAudienceUnavailableError:
            return _error_response(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        try:
            result = dependencies.get_alias.execute(
                str(media_id), identity.login_key
            )
        except MediaUserAliasMediaNotFoundError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except FrameNestIdentityError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except FrameNestMediaUserAliasRepositoryError:
            return _error_response(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        return _alias_response(result)

    @router.put(
        "/api/media/{media_id}/alias",
        response_model=MediaAliasResponse,
        responses={
            404: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def save_alias(
        media_id: UUID4,
        body: MediaAliasSaveRequest,
        request: Request,
    ) -> MediaAliasResponse | JSONResponse:
        identity = _require_identity(request, CAPABILITY_METADATA_ALIAS_WRITE)
        if not dependencies.catalog_available() or dependencies.save_alias is None:
            return _catalog_unavailable_response()
        parsed_id = _parse_media_id(media_id)
        if parsed_id is None:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        try:
            if not content_audience_allows(
                request=request,
                media_id=parsed_id,
                policy=dependencies.audience_policy,
            ):
                return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except ContentAudienceUnavailableError:
            return _error_response(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        try:
            result = dependencies.save_alias.execute(
                str(media_id),
                identity.login_key,
                body.display_title,
                body.description,
                body.tag_keys,
            )
        except MediaUserAliasMediaNotFoundError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except AliasTagNotFoundError:
            return _error_response(
                422, ALIAS_TAG_NOT_FOUND_CODE, ALIAS_TAG_NOT_FOUND_MESSAGE
            )
        except FrameNestMediaUserAliasError:
            return _error_response(422, ALIAS_INVALID_CODE, ALIAS_INVALID_MESSAGE)
        except FrameNestIdentityError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except FrameNestMediaUserAliasRepositoryError:
            return _error_response(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        return _alias_response(result)

    return router


def _require_identity(request: Request, capability: str) -> IdentityContext:
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        raise HTTPException(
            status_code=401, detail={"code": "IDENTITY_REQUIRED"}
        )
    if not identity.has_capability(capability):
        raise HTTPException(
            status_code=403, detail={"code": "CAPABILITY_DENIED"}
        )
    return identity


def _parse_media_id(media_id: UUID4) -> MediaId | None:
    try:
        return MediaId.from_string(str(media_id))
    except FrameNestIdentityError:
        return None


def _alias_response(view: MediaUserAliasView | object) -> MediaAliasResponse:
    if view is None:
        view = EMPTY_ALIAS_VIEW
    return MediaAliasResponse(
        display_title=getattr(view, "display_title", None),
        description=getattr(view, "description", None),
        tag_keys=list(getattr(view, "tag_keys", ())),
    )


def _catalog_unavailable_response() -> JSONResponse:
    return _error_response(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
    )
