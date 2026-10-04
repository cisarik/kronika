"""FastAPI routes for persistent media metadata and canonical tags."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, UUID4, field_validator

from kronika.application.ports.media_metadata_repository import (
    OMITTED,
    AcquisitionSourceImmutableError,
    CanonicalTagDefinitionConflictError,
    CanonicalTagNotFoundError,
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataMediaNotFoundError,
    SourceDerivedMetadataImmutableError,
)
from kronika.adapters.api.content_audience_api import (
    ContentAudienceUnavailableError,
    content_audience_allows,
    content_audience_decision,
    load_approved_projection,
)
from kronika.application.content_publication import ContentAudiencePolicy
from kronika.domain import FrameNestIdentityError
from kronika.domain.identities import MediaId
from kronika.domain.record_access import READ_APPROVED, READ_DENY
from kronika.domain.media_classification import CreatorAttributionKind
from kronika.domain.media_metadata import (
    CanonicalTagDisplayName,
    CanonicalTagKey,
    FrameNestMediaMetadataError,
    MediaDescription,
    MediaDisplayTitle,
    normalize_creator_display_name,
    normalize_creator_handle,
    normalize_creator_stable_id,
)

CATALOG_UNAVAILABLE_CODE = "CATALOG_UNAVAILABLE"
CATALOG_UNAVAILABLE_MESSAGE = "The local catalog is not available."
MEDIA_NOT_FOUND_CODE = "MEDIA_NOT_FOUND"
MEDIA_NOT_FOUND_MESSAGE = "Media not found."
CANONICAL_TAG_NOT_FOUND_CODE = "CANONICAL_TAG_NOT_FOUND"
CANONICAL_TAG_NOT_FOUND_MESSAGE = "Canonical tag not found."
CANONICAL_TAG_DEFINITION_CONFLICT_CODE = "CANONICAL_TAG_DEFINITION_CONFLICT"
CANONICAL_TAG_DEFINITION_CONFLICT_MESSAGE = "Canonical tag definition conflicts."
CANONICAL_TAG_OPERATION_FAILED_CODE = "CANONICAL_TAG_OPERATION_FAILED"
CANONICAL_TAG_OPERATION_FAILED_MESSAGE = "Canonical tag operation failed."
MEDIA_METADATA_OPERATION_FAILED_CODE = "MEDIA_METADATA_OPERATION_FAILED"
MEDIA_METADATA_OPERATION_FAILED_MESSAGE = "Media metadata operation failed."
ACQUISITION_SOURCE_IMMUTABLE_CODE = "ACQUISITION_SOURCE_IMMUTABLE"
ACQUISITION_SOURCE_IMMUTABLE_MESSAGE = (
    "Acquisition source is immutable provenance and cannot be changed."
)
SOURCE_DERIVED_IMMUTABLE_CODE = "SOURCE_DERIVED_IMMUTABLE"
SOURCE_DERIVED_IMMUTABLE_MESSAGE = (
    "X source-derived values are immutable provenance and cannot be changed."
)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class CanonicalTagRequest(BaseModel):
    key: str
    display_name: str

    @field_validator("key")
    @classmethod
    def validate_key(cls, value: str) -> str:
        CanonicalTagKey(value)
        return value

    @field_validator("display_name")
    @classmethod
    def validate_display_name(cls, value: str) -> str:
        return CanonicalTagDisplayName(value).value


class CanonicalTagResponse(BaseModel):
    key: str
    display_name: str


class CanonicalTagCreateResponse(BaseModel):
    status: str
    tag: CanonicalTagResponse


class CanonicalTagListResponse(BaseModel):
    tags: list[CanonicalTagResponse]


class MediaMetadataResponse(BaseModel):
    persisted: bool
    display_title: str | None
    description: str | None
    tags: list[CanonicalTagResponse]
    collection_key: str | None
    processed_at_ms: int | None
    created_at_ms: int | None
    updated_at_ms: int | None
    content_category: str = "general"
    acquisition_source: str = "unknown"
    genres: list[str] = []
    creator_attribution_kind: str | None = None
    creator_stable_id: str | None = None
    creator_handle: str | None = None
    creator_display_name: str | None = None


class MediaMetadataSaveRequest(BaseModel):
    display_title: str | None
    description: str | None
    tag_keys: list[str]
    content_category: str | None | object = OMITTED
    acquisition_source: str | None = None
    genres: list[str] = []
    creator_attribution_kind: str | None | object = OMITTED
    creator_stable_id: str | None | object = OMITTED
    creator_handle: str | None | object = OMITTED
    creator_display_name: str | None | object = OMITTED

    @field_validator("display_title")
    @classmethod
    def validate_display_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        MediaDisplayTitle(value)
        return value

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        if not stripped:
            return None
        MediaDescription(value)
        return value

    @field_validator("tag_keys")
    @classmethod
    def validate_tag_keys(cls, value: list[str]) -> list[str]:
        keys = [CanonicalTagKey(key) for key in value]
        if len(keys) > 32 or len(set(keys)) != len(keys):
            raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
        return value

    @field_validator("content_category")
    @classmethod
    def validate_content_category(cls, value: str | None | object) -> str | None | object:
        if value is OMITTED or value is None:
            return value
        from kronika.domain.media_classification import ContentCategory

        return ContentCategory(value).value

    @field_validator("acquisition_source")
    @classmethod
    def validate_acquisition_source(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from kronika.domain.media_classification import AcquisitionSource

        return AcquisitionSource(value).value

    @field_validator("genres")
    @classmethod
    def validate_genres(cls, value: list[str]) -> list[str]:
        if len(value) > 8 or len(set(item.casefold() for item in value)) != len(value):
            raise ValueError(MEDIA_METADATA_OPERATION_FAILED_MESSAGE)
        return value

    @field_validator("creator_attribution_kind")
    @classmethod
    def validate_creator_attribution_kind(cls, value: str | None | object) -> str | None | object:
        if value is OMITTED or value is None:
            return value
        return CreatorAttributionKind(value).value

    @field_validator("creator_stable_id")
    @classmethod
    def validate_creator_stable_id(cls, value: str | None | object) -> str | None | object:
        if value is OMITTED:
            return value
        return normalize_creator_stable_id(value)

    @field_validator("creator_handle")
    @classmethod
    def validate_creator_handle(cls, value: str | None | object) -> str | None | object:
        if value is OMITTED:
            return value
        return normalize_creator_handle(value)

    @field_validator("creator_display_name")
    @classmethod
    def validate_creator_display_name(cls, value: str | None | object) -> str | None | object:
        if value is OMITTED:
            return value
        return normalize_creator_display_name(value)


class MediaMetadataSaveResponse(BaseModel):
    status: str
    metadata: MediaMetadataResponse


@dataclass(frozen=True, slots=True)
class MediaMetadataApiDependencies:
    """Injected dependencies for media metadata routes."""

    create_tag: object
    list_tags: object
    get_metadata: object
    save_metadata: object
    catalog_available: Callable[[], bool]
    audience_policy: ContentAudiencePolicy | None = None
    ensure_companion_x_tag: object | None = None


def create_media_metadata_api_router(dependencies: MediaMetadataApiDependencies) -> APIRouter:
    """Create the persistent media metadata API router."""
    router = APIRouter()

    @router.post(
        "/api/canonical-tags",
        response_model=CanonicalTagCreateResponse,
        responses={
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def create_tag(request: CanonicalTagRequest) -> CanonicalTagCreateResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        try:
            result = dependencies.create_tag.execute(request.key, request.display_name)
        except CanonicalTagDefinitionConflictError:
            return _error_response(
                409,
                CANONICAL_TAG_DEFINITION_CONFLICT_CODE,
                CANONICAL_TAG_DEFINITION_CONFLICT_MESSAGE,
            )
        except FrameNestMediaMetadataRepositoryError:
            return _error_response(
                500,
                CANONICAL_TAG_OPERATION_FAILED_CODE,
                CANONICAL_TAG_OPERATION_FAILED_MESSAGE,
            )
        except Exception:
            return _error_response(
                500,
                CANONICAL_TAG_OPERATION_FAILED_CODE,
                CANONICAL_TAG_OPERATION_FAILED_MESSAGE,
            )
        return JSONResponse(
            status_code=201 if result.status == "created" else 200,
            content={
                "status": result.status,
                "tag": _tag_response(result.tag).model_dump(),
            },
        )

    @router.get(
        "/api/canonical-tags",
        response_model=CanonicalTagListResponse,
        responses={500: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
    )
    def list_tags(
        surface: Literal["x-companion-save"] | None = Query(default=None),
    ) -> CanonicalTagListResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        if surface == "x-companion-save" and dependencies.ensure_companion_x_tag is not None:
            try:
                dependencies.ensure_companion_x_tag.execute()
            except Exception:
                pass
        try:
            result = dependencies.list_tags.execute()
        except Exception:
            return _error_response(
                500,
                CANONICAL_TAG_OPERATION_FAILED_CODE,
                CANONICAL_TAG_OPERATION_FAILED_MESSAGE,
            )
        return CanonicalTagListResponse(tags=[_tag_response(tag) for tag in result.tags])

    @router.get(
        "/api/media/{media_id}/metadata",
        response_model=MediaMetadataResponse,
        responses={
            404: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def get_metadata(
        media_id: UUID4,
        request: Request,
    ) -> MediaMetadataResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        parsed_id = MediaId.from_string(str(media_id))
        try:
            decision = content_audience_decision(
                request=request,
                media_id=parsed_id,
                policy=dependencies.audience_policy,
            )
        except ContentAudienceUnavailableError:
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        if decision == READ_DENY:
            return _error_response(
                404,
                MEDIA_NOT_FOUND_CODE,
                MEDIA_NOT_FOUND_MESSAGE,
            )
        if decision == READ_APPROVED:
            try:
                projection = load_approved_projection(
                    policy=dependencies.audience_policy,
                    media_id=parsed_id,
                )
            except Exception:
                return _error_response(
                    500,
                    MEDIA_METADATA_OPERATION_FAILED_CODE,
                    MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
                )
            if projection is None:
                return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
            return _approved_metadata_response(projection)
        try:
            result = dependencies.get_metadata.execute(str(media_id))
        except MediaMetadataMediaNotFoundError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except Exception:
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        return _metadata_response(result)

    @router.put(
        "/api/media/{media_id}/metadata",
        response_model=MediaMetadataSaveResponse,
        responses={
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            500: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def save_metadata(
        media_id: UUID4,
        request: MediaMetadataSaveRequest,
        http_request: Request,
    ) -> MediaMetadataSaveResponse | JSONResponse:
        if not dependencies.catalog_available():
            return _catalog_unavailable_response()
        try:
            if not content_audience_allows(
                request=http_request,
                media_id=MediaId.from_string(str(media_id)),
                policy=dependencies.audience_policy,
            ):
                return _error_response(
                    404,
                    MEDIA_NOT_FOUND_CODE,
                    MEDIA_NOT_FOUND_MESSAGE,
                )
        except ContentAudienceUnavailableError:
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        try:
            result = dependencies.save_metadata.execute(
                str(media_id),
                request.display_title,
                request.description,
                request.tag_keys,
                content_category=request.content_category,
                acquisition_source=request.acquisition_source,
                genres=request.genres,
                creator_attribution_kind=request.creator_attribution_kind,
                creator_stable_id=request.creator_stable_id,
                creator_handle=request.creator_handle,
                creator_display_name=request.creator_display_name,
            )
        except MediaMetadataMediaNotFoundError:
            return _error_response(404, MEDIA_NOT_FOUND_CODE, MEDIA_NOT_FOUND_MESSAGE)
        except AcquisitionSourceImmutableError:
            return _error_response(
                409,
                ACQUISITION_SOURCE_IMMUTABLE_CODE,
                ACQUISITION_SOURCE_IMMUTABLE_MESSAGE,
            )
        except SourceDerivedMetadataImmutableError:
            return _error_response(
                409,
                SOURCE_DERIVED_IMMUTABLE_CODE,
                SOURCE_DERIVED_IMMUTABLE_MESSAGE,
            )
        except CanonicalTagNotFoundError:
            return _error_response(
                409,
                CANONICAL_TAG_NOT_FOUND_CODE,
                CANONICAL_TAG_NOT_FOUND_MESSAGE,
            )
        except (FrameNestMediaMetadataRepositoryError, FrameNestIdentityError):
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        except (FrameNestMediaMetadataError, ValueError):
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        except Exception:
            return _error_response(
                500,
                MEDIA_METADATA_OPERATION_FAILED_CODE,
                MEDIA_METADATA_OPERATION_FAILED_MESSAGE,
            )
        return MediaMetadataSaveResponse(
            status=result.status,
            metadata=_metadata_response(result.metadata),
        )

    return router


def _catalog_unavailable_response() -> JSONResponse:
    return _error_response(503, CATALOG_UNAVAILABLE_CODE, CATALOG_UNAVAILABLE_MESSAGE)


def _error_response(status_code: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"error": {"code": code, "message": message}})


def _tag_response(tag: object) -> CanonicalTagResponse:
    return CanonicalTagResponse(
        key=tag.key.value,
        display_name=tag.display_name.value,
    )


def _approved_metadata_response(projection: object) -> MediaMetadataResponse:
    """Serialize the approved snapshot without the current working metadata row."""
    tags = getattr(projection, "tags", ())
    return MediaMetadataResponse(
        persisted=True,
        display_title=getattr(projection, "display_title", None),
        description=getattr(projection, "description", None),
        tags=[
            CanonicalTagResponse(key=str(tag.key), display_name=str(tag.display_name))
            for tag in tags
        ],
        collection_key=None,
        processed_at_ms=None,
        created_at_ms=None,
        updated_at_ms=None,
        content_category=getattr(projection, "content_category", None) or "general",
        acquisition_source=getattr(projection, "acquisition_source", None) or "unknown",
        genres=[],
        creator_attribution_kind=getattr(projection, "creator_attribution_kind", None),
        creator_stable_id=getattr(projection, "creator_stable_id", None),
        creator_handle=getattr(projection, "creator_handle", None),
        creator_display_name=getattr(projection, "creator_display_name", None),
    )


def _metadata_response(metadata: object) -> MediaMetadataResponse:
    display_title = metadata.display_title
    if display_title is not None and hasattr(display_title, "value"):
        display_title = display_title.value
    raw_description = getattr(metadata, "description", None)
    description = raw_description.value if raw_description is not None and hasattr(raw_description, "value") else raw_description
    tags = getattr(metadata, "tags", ())
    return MediaMetadataResponse(
        persisted=metadata.persisted,
        display_title=display_title,
        description=description,
        tags=[_tag_response(tag) for tag in tags],
        collection_key=metadata.collection_key,
        processed_at_ms=metadata.processed_at_ms,
        created_at_ms=metadata.created_at_ms,
        updated_at_ms=metadata.updated_at_ms,
        content_category=getattr(metadata, "content_category", "general"),
        acquisition_source=getattr(metadata, "acquisition_source", "unknown"),
        genres=list(getattr(metadata, "genres", ())),
        creator_attribution_kind=getattr(metadata, "creator_attribution_kind", None),
        creator_stable_id=getattr(metadata, "creator_stable_id", None),
        creator_handle=getattr(metadata, "creator_handle", None),
        creator_display_name=getattr(metadata, "creator_display_name", None),
    )
