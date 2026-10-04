"""Administrator read-only team-alias aggregation. Performs no overlay writes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, UUID4

from kronika.adapters.api.tailscale_ingress import SCOPE_IDENTITY
from kronika.application.media_user_alias import TeamAliasEntry
from kronika.application.ports.media_user_alias_repository import (
    FrameNestMediaUserAliasRepositoryError,
    MediaUserAliasMediaNotFoundError,
)
from kronika.domain import FrameNestIdentityError
from kronika.domain.identity_access import (
    CAPABILITY_MEDIA_WORKFLOW_READ,
    CAPABILITY_METADATA_ALIAS_TEAM_READ,
    IdentityContext,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
ALIAS_OPERATION_FAILED_CODE = "ALIAS_OPERATION_FAILED"
ALIAS_OPERATION_FAILED_MESSAGE = "Media user alias operation failed."
IDENTITY_REQUIRED_CODE = "IDENTITY_REQUIRED"
IDENTITY_REQUIRED_MESSAGE = "A verified application identity is required."
CAPABILITY_DENIED_CODE = "CAPABILITY_DENIED"
CAPABILITY_DENIED_MESSAGE = "The verified identity is not authorized."

_NO_STORE_HEADERS = {"Cache-Control": "no-store"}


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class TeamAliasEntryResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_key: str
    display_title: str | None
    description: str | None
    tag_keys: list[str]
    created_at_ms: int
    updated_at_ms: int


class TeamAliasListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    media_id: str
    items: list[TeamAliasEntryResponse]


@dataclass(frozen=True, slots=True)
class TeamAliasApiDependencies:
    """Injected read-only team-alias query."""

    list_team_aliases: object | None
    catalog_available: Callable[[], bool]


def create_team_alias_api_router(dependencies: TeamAliasApiDependencies) -> APIRouter:
    """Create the dual-capability administrator team-alias read route."""
    router = APIRouter()

    @router.get(
        "/api/admin/media/{media_id}/aliases",
        response_model=TeamAliasListResponse,
        responses={
            401: {"model": ErrorResponse},
            403: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def list_team_aliases(
        media_id: UUID4,
        request: Request,
    ) -> TeamAliasListResponse | JSONResponse:
        identity = request.scope.get(SCOPE_IDENTITY)
        if not isinstance(identity, IdentityContext):
            return _error(401, IDENTITY_REQUIRED_CODE, IDENTITY_REQUIRED_MESSAGE)
        if not identity.has_capability(CAPABILITY_MEDIA_WORKFLOW_READ) or not (
            identity.has_capability(CAPABILITY_METADATA_ALIAS_TEAM_READ)
        ):
            return _error(403, CAPABILITY_DENIED_CODE, CAPABILITY_DENIED_MESSAGE)
        if not dependencies.catalog_available() or dependencies.list_team_aliases is None:
            return _error(
                503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE
            )
        try:
            items = dependencies.list_team_aliases.execute(str(media_id))
        except MediaUserAliasMediaNotFoundError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except FrameNestIdentityError:
            return _error(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except FrameNestMediaUserAliasRepositoryError:
            return _error(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        except Exception:
            return _error(
                500, ALIAS_OPERATION_FAILED_CODE, ALIAS_OPERATION_FAILED_MESSAGE
            )
        return JSONResponse(
            content=TeamAliasListResponse(
                media_id=str(media_id),
                items=[_entry_response(item) for item in items],
            ).model_dump(),
            headers=_NO_STORE_HEADERS,
        )

    return router


def _entry_response(item: TeamAliasEntry | object) -> TeamAliasEntryResponse:
    return TeamAliasEntryResponse(
        login_key=getattr(item, "login_key"),
        display_title=getattr(item, "display_title", None),
        description=getattr(item, "description", None),
        tag_keys=list(getattr(item, "tag_keys", ())),
        created_at_ms=int(getattr(item, "created_at_ms")),
        updated_at_ms=int(getattr(item, "updated_at_ms")),
    )


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=_NO_STORE_HEADERS,
    )
