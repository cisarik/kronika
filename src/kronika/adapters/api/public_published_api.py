"""GET-only public published-reader routes and redacted projections."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from pydantic import BaseModel, UUID4

from kronika.adapters.api.media_content_api import (
    _full_content_response,
    _parse_byte_range,
    _partial_content_response,
    _range_not_satisfiable_response,
)
from kronika.application.gallery_preview import (
    GalleryPreviewFailedError,
    GalleryPreviewNotFoundError,
    GalleryPreviewUnavailableError,
)
from kronika.application.media_catalog import MediaCatalogValidationError
from kronika.application.media_content import (
    MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
    MediaContentFailedError,
    MediaContentNotFoundError,
    MediaContentUnavailableError,
    ResolvedMediaContent,
)
from kronika.application.media_cover import CoverFailedError, CoverMediaNotFoundError
from kronika.application.ports.content_publication_repository import (
    FrameNestContentPublicationRepositoryError,
)
from kronika.application.ports.library_repository import FrameNestLibraryRepositoryError
from kronika.application.ports.media_catalog_repository import (
    FrameNestMediaCatalogRepositoryError,
)
from kronika.application.ports.media_metadata_repository import (
    FrameNestMediaMetadataRepositoryError,
    MediaMetadataMediaNotFoundError,
)
from kronika.application.ports.media_repository import FrameNestMediaRepositoryError
from kronika.domain.identities import MediaId, MediaLocationId
from kronika.domain.identity_access import (
    AUDIENCE_PUBLIC_PUBLISHED,
    PUBLIC_PUBLISHED_CAPABILITIES,
)
from kronika.structured_logging import get_logger
import kronika.adapters.api.web as web_resources

NOT_FOUND_CODE = "NOT_FOUND"
NOT_FOUND_MESSAGE = "Not found."
NO_STORE_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
}
LOGGER = get_logger("public_published_api")

_PUBLIC_ASSET_MEDIA_TYPES = {
    "app.js": "text/javascript; charset=utf-8",
    "styles.css": "text/css; charset=utf-8",
}
_INDEX_COMPANION_SCRIPT = (
    '    <script src="/assets/companion_host.js" defer></script>\n'
)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class PublicTagResponse(BaseModel):
    key: str
    display_name: str
    position: int | None = None


class PublicLocationResponse(BaseModel):
    location_id: str
    availability: str


class PublicCatalogMediaResponse(BaseModel):
    media_id: str
    media_kind: str
    display_title: str | None
    description: str | None
    content_category: str
    acquisition_source: str
    cover_ready: bool
    creator_attribution_kind: str | None
    creator_handle: str | None
    creator_display_name: str | None
    tags: list[PublicTagResponse]
    locations: list[PublicLocationResponse]


class PublicCatalogResponse(BaseModel):
    items: list[PublicCatalogMediaResponse]
    total: int
    limit: int
    offset: int
    q: str | None
    tag_keys: list[str]
    content_category: str | None = None
    acquisition_source: str | None = None


class PublicCanonicalTagResponse(BaseModel):
    key: str
    display_name: str


class PublicCanonicalTagListResponse(BaseModel):
    tags: list[PublicCanonicalTagResponse]


class PublicMetadataResponse(BaseModel):
    display_title: str | None
    description: str | None
    tags: list[PublicCanonicalTagResponse]
    content_category: str
    acquisition_source: str
    genres: list[str]
    creator_attribution_kind: str | None
    creator_handle: str | None
    creator_display_name: str | None


class PublicAudienceResponse(BaseModel):
    audience: str
    identity: None
    capabilities: list[str]


@dataclass(frozen=True, slots=True)
class PublicPublishedApiDependencies:
    """Read-only collaborators for the public published composition."""

    catalog_available: Callable[[], bool]
    is_published: Callable[[MediaId], bool]
    list_media: object
    get_media: object
    list_published_tags: Callable[[], tuple[object, ...]]
    get_metadata: object
    resolve_content: object
    open_gallery_preview: object
    open_cover_thumbnail: object
    cover_thumbnail_etag: object


def public_not_found_response() -> JSONResponse:
    """Return the uniform sanitized public 404."""
    return JSONResponse(
        status_code=404,
        content={"error": {"code": NOT_FOUND_CODE, "message": NOT_FOUND_MESSAGE}},
        headers=dict(NO_STORE_HEADERS),
    )


def index_html_contains_companion_marker() -> bool:
    """Return whether the packaged index still carries the companion marker."""
    html = _read_web_resource("index.html").decode("utf-8")
    return _INDEX_COMPANION_SCRIPT in html


def create_public_published_api_router(
    dependencies: PublicPublishedApiDependencies,
) -> APIRouter:
    """Create the exact GET-only public published route allowlist."""
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def root() -> HTMLResponse:
        html = _read_web_resource("index.html").decode("utf-8")
        if _INDEX_COMPANION_SCRIPT not in html:
            raise RuntimeError(
                "Companion script marker missing from public index asset."
            )
        return HTMLResponse(
            content=html.replace(_INDEX_COMPANION_SCRIPT, ""),
            media_type="text/html; charset=utf-8",
            headers=dict(NO_STORE_HEADERS),
        )

    @router.get("/assets/app.js")
    def app_js() -> Response:
        return Response(
            content=_read_web_resource("app.js"),
            media_type=_PUBLIC_ASSET_MEDIA_TYPES["app.js"],
            headers=dict(NO_STORE_HEADERS),
        )

    @router.get("/assets/styles.css")
    def styles_css() -> Response:
        return Response(
            content=_read_web_resource("styles.css"),
            media_type=_PUBLIC_ASSET_MEDIA_TYPES["styles.css"],
            headers=dict(NO_STORE_HEADERS),
        )

    @router.get("/api/audience/me", response_model=PublicAudienceResponse)
    def audience_me() -> PublicAudienceResponse:
        return PublicAudienceResponse(
            audience=AUDIENCE_PUBLIC_PUBLISHED,
            identity=None,
            capabilities=sorted(PUBLIC_PUBLISHED_CAPABILITIES),
        )

    @router.get(
        "/api/media",
        response_model=PublicCatalogResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def list_media(
        q: str | None = None,
        tag: list[str] = Query(default=[]),
        limit: int = Query(default=24, ge=1, le=100),
        offset: int = Query(default=0, ge=0),
        content_category: str | None = None,
        acquisition_source: str | None = None,
    ) -> PublicCatalogResponse | JSONResponse:
        if not dependencies.catalog_available():
            return public_not_found_response()
        try:
            from kronika.domain.record_access import RecordAccessScope

            result = dependencies.list_media.execute(
                q=q,
                tag_keys=tag,
                limit=limit,
                offset=offset,
                content_category=content_category,
                acquisition_source=acquisition_source,
                access_scope=RecordAccessScope.legacy_public(),
            )
        except MediaCatalogValidationError:
            return public_not_found_response()
        except FrameNestMediaCatalogRepositoryError as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        return PublicCatalogResponse(
            items=[_redact_catalog_item(item) for item in result.items],
            total=result.total,
            limit=result.limit,
            offset=result.offset,
            q=result.q,
            tag_keys=[key.value for key in result.tag_keys],
            content_category=getattr(result, "content_category", None),
            acquisition_source=getattr(result, "acquisition_source", None),
        )

    @router.get(
        "/api/media/{media_id}",
        response_model=PublicCatalogMediaResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def get_media(media_id: UUID4) -> PublicCatalogMediaResponse | JSONResponse:
        parsed = _parse_media_id(media_id)
        if parsed is None or not _published(dependencies, parsed):
            return public_not_found_response()
        try:
            item = dependencies.get_media.execute(str(media_id))
        except MediaCatalogValidationError:
            return public_not_found_response()
        except FrameNestMediaCatalogRepositoryError as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        if item is None or not _published(dependencies, parsed):
            return public_not_found_response()
        return _redact_catalog_item(item)

    @router.get(
        "/api/canonical-tags",
        response_model=PublicCanonicalTagListResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def list_published_tags() -> PublicCanonicalTagListResponse | JSONResponse:
        if not dependencies.catalog_available():
            return public_not_found_response()
        try:
            tags = dependencies.list_published_tags()
        except FrameNestMediaMetadataRepositoryError as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        return PublicCanonicalTagListResponse(
            tags=[
                PublicCanonicalTagResponse(
                    key=tag.key.value if hasattr(tag.key, "value") else str(tag.key),
                    display_name=tag.display_name.value
                    if hasattr(tag.display_name, "value")
                    else str(tag.display_name),
                )
                for tag in tags
            ]
        )

    @router.get(
        "/api/media/{media_id}/metadata",
        response_model=PublicMetadataResponse,
        responses={404: {"model": ErrorResponse}},
    )
    def get_metadata(media_id: UUID4) -> PublicMetadataResponse | JSONResponse:
        parsed = _parse_media_id(media_id)
        if parsed is None or not _published(dependencies, parsed):
            return public_not_found_response()
        try:
            result = dependencies.get_metadata.execute(str(media_id))
        except MediaMetadataMediaNotFoundError:
            return public_not_found_response()
        except Exception as exc:
            return _failed_response(exc)
        if not _published(dependencies, parsed):
            return public_not_found_response()
        return PublicMetadataResponse(
            display_title=result.display_title,
            description=result.description,
            tags=[
                PublicCanonicalTagResponse(
                    key=tag.key.value if hasattr(tag.key, "value") else str(tag.key),
                    display_name=tag.display_name.value
                    if hasattr(tag.display_name, "value")
                    else str(tag.display_name),
                )
                for tag in result.tags
            ],
            content_category=result.content_category,
            acquisition_source=result.acquisition_source,
            genres=list(result.genres),
            creator_attribution_kind=result.creator_attribution_kind,
            creator_handle=result.creator_handle,
            creator_display_name=result.creator_display_name,
        )

    @router.get("/api/media/{media_id}/locations/{location_id}/content", response_model=None)
    def get_media_content(
        media_id: UUID4,
        location_id: UUID4,
        request: Request,
    ) -> StreamingResponse | JSONResponse:
        parsed = _parse_media_id(media_id)
        if parsed is None or not _published(dependencies, parsed):
            return public_not_found_response()
        try:
            resolved = dependencies.resolve_content.execute(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
            )
        except MediaContentNotFoundError:
            return public_not_found_response()
        except MediaContentUnavailableError:
            return _unavailable_response()
        except (
            FrameNestLibraryRepositoryError,
            FrameNestMediaRepositoryError,
            MediaContentFailedError,
        ) as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        if not _published(dependencies, parsed):
            if isinstance(resolved, ResolvedMediaContent):
                resolved.close()
            return public_not_found_response()
        range_header = request.headers.get("range")
        if range_header is None:
            return _full_content_response(resolved)
        parsed_range = _parse_byte_range(range_header, resolved.byte_size)
        if parsed_range is None:
            resolved.close()
            return _range_not_satisfiable_response(resolved.byte_size)
        start, end = parsed_range
        return _partial_content_response(resolved, start, end)

    @router.get("/api/media/{media_id}/locations/{location_id}/gallery-preview", response_model=None)
    def get_gallery_preview(
        media_id: UUID4,
        location_id: UUID4,
    ) -> Response | JSONResponse:
        parsed = _parse_media_id(media_id)
        if parsed is None or not _published(dependencies, parsed):
            return public_not_found_response()
        try:
            opened = dependencies.open_gallery_preview.open_ready(
                MediaId.from_string(str(media_id)),
                MediaLocationId.from_string(str(location_id)),
            )
        except GalleryPreviewNotFoundError:
            return public_not_found_response()
        except GalleryPreviewUnavailableError:
            return _unavailable_response()
        except (
            FrameNestLibraryRepositoryError,
            FrameNestMediaRepositoryError,
            GalleryPreviewFailedError,
        ) as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        if not _published(dependencies, parsed):
            opened.close()
            return public_not_found_response()
        try:
            return Response(
                content=opened.payload,
                status_code=200,
                media_type=opened.media_type,
                headers={
                    **NO_STORE_HEADERS,
                    "Content-Length": str(opened.byte_size),
                    "Content-Disposition": "inline",
                },
            )
        finally:
            opened.close()

    @router.get("/api/media/{media_id}/cover-thumbnail", response_model=None)
    def get_cover_thumbnail(
        media_id: UUID4,
        request: Request,
    ) -> Response | JSONResponse:
        parsed = _parse_media_id(media_id)
        if parsed is None or not _published(dependencies, parsed):
            return public_not_found_response()
        try:
            etag = dependencies.cover_thumbnail_etag(MediaId.from_string(str(media_id)))
            if etag is None:
                return public_not_found_response()
            opened = dependencies.open_cover_thumbnail(
                MediaId.from_string(str(media_id))
            )
        except CoverMediaNotFoundError:
            return public_not_found_response()
        except (
            FrameNestLibraryRepositoryError,
            FrameNestMediaRepositoryError,
            CoverFailedError,
        ) as exc:
            return _failed_response(exc)
        except Exception as exc:
            return _failed_response(exc)
        if not _published(dependencies, parsed):
            opened.close()
            return public_not_found_response()
        try:
            headers = {
                **NO_STORE_HEADERS,
                "ETag": etag,
                "Content-Type": opened.media_type,
                "Content-Length": str(opened.byte_size),
                "Content-Disposition": "inline",
            }
            if request.headers.get("if-none-match") == etag:
                return Response(status_code=304, headers=headers)
            return Response(
                content=opened.payload,
                status_code=200,
                media_type=opened.media_type,
                headers=headers,
            )
        finally:
            opened.close()

    return router


def _published(dependencies: PublicPublishedApiDependencies, media_id: MediaId) -> bool:
    if not dependencies.catalog_available():
        return False
    try:
        return bool(dependencies.is_published(media_id))
    except FrameNestContentPublicationRepositoryError:
        return False
    except Exception:
        return False


def _parse_media_id(media_id: UUID4) -> MediaId | None:
    try:
        return MediaId.from_string(str(media_id))
    except Exception:
        return None


def _redact_catalog_item(item: object) -> PublicCatalogMediaResponse:
    return PublicCatalogMediaResponse(
        media_id=item.media_id,
        media_kind=item.media_kind,
        display_title=item.display_title,
        description=getattr(item, "description", None),
        content_category=getattr(item, "content_category", "general"),
        acquisition_source=getattr(item, "acquisition_source", "unknown"),
        cover_ready=getattr(item, "cover_ready", False),
        creator_attribution_kind=getattr(item, "creator_attribution_kind", None),
        creator_handle=getattr(item, "creator_handle", None),
        creator_display_name=getattr(item, "creator_display_name", None),
        tags=[
            PublicTagResponse(
                key=tag.key,
                display_name=tag.display_name,
                position=tag.position,
            )
            for tag in item.tags
        ],
        locations=[
            PublicLocationResponse(
                location_id=location.location_id,
                availability=location.availability,
            )
            for location in item.locations
        ],
    )


def _read_web_resource(resource_name: str) -> bytes:
    resource = resources.files(web_resources).joinpath(resource_name)
    if not resource.is_file():
        raise FileNotFoundError(resource_name)
    return resource.read_bytes()


def _failed_response(exc: Exception | None = None) -> JSONResponse:
    if exc is not None:
        LOGGER.emit(
            level="ERROR",
            event="public_read_failed",
            operation="serve",
            error_code="PUBLIC_READ_FAILED",
            exception=exc,
        )
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "PUBLIC_READ_FAILED", "message": "Not found."}},
        headers=dict(NO_STORE_HEADERS),
    )


def _unavailable_response() -> JSONResponse:
    return JSONResponse(
        status_code=409,
        content={
            "error": {
                "code": "MEDIA_CONTENT_UNAVAILABLE",
                "message": MEDIA_CONTENT_UNAVAILABLE_MESSAGE,
            }
        },
        headers=dict(NO_STORE_HEADERS),
    )
