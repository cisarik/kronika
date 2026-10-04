"""Separately composed local-only public published ASGI application."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from kronika.adapters.api.public_published_api import (
    NOT_FOUND_CODE,
    PublicPublishedApiDependencies,
    create_public_published_api_router,
    index_html_contains_companion_marker,
    public_not_found_response,
)
from kronika.application.gallery_preview import GalleryPreviewService
from kronika.application.media_catalog import GetMediaCatalogItem, ListMediaCatalog
from kronika.application.media_content import ResolveMediaContent
from kronika.application.media_cover import CoverService
from kronika.application.media_metadata import GetMediaMetadata
from kronika.configuration import (
    INGRESS_MODE_PUBLIC_PUBLISHED_UDS,
    KronikaSettings,
    load_settings,
)
from kronika.infrastructure.filesystem.cover_storage import (
    FilesystemCoverThumbnailCache,
    FilesystemDurableCoverStorage,
)
from kronika.infrastructure.filesystem.media_content import LocalMediaContentReader
from kronika.infrastructure.media_analysis.gallery_preview import (
    FilesystemGalleryPreviewCache,
)
from kronika.infrastructure.persistence.content_publication_repository import (
    SqliteContentPublicationRepository,
)
from kronika.infrastructure.persistence.engine import (
    create_sqlite_readonly_engine,
    dispose_engine,
)
from kronika.infrastructure.persistence.errors import FrameNestPersistenceError
from kronika.infrastructure.persistence.library_repository import SqliteLibraryRepository
from kronika.infrastructure.persistence.media_catalog_repository import (
    SqliteMediaCatalogRepository,
)
from kronika.infrastructure.persistence.media_cover_repository import (
    SqliteMediaCoverRepository,
)
from kronika.infrastructure.persistence.media_metadata_repository import (
    SqliteMediaMetadataRepository,
)
from kronika.infrastructure.persistence.media_repository import SqliteMediaRepository
from kronika.structured_logging import get_logger

REQUIRED_PUBLIC_SCHEMA_REVISION = "0035"
_SAFE_METHODS = frozenset({"GET", "HEAD"})
LOGGER = get_logger("public_published_application")


class PublicPublishedStartupError(ValueError):
    """Raised when the public published process cannot start fail-closed."""


class _ForbiddenPublicDerivative:
    """Fail-closed stand-in so the public process cannot generate derivatives."""

    def __getattr__(self, name: str) -> object:
        def _reject(*args: object, **kwargs: object) -> object:
            del args, kwargs
            raise PublicPublishedStartupError(
                "The public published process cannot generate derivatives."
            )

        return _reject


def create_public_published_app(
    settings: KronikaSettings | None = None,
) -> FastAPI:
    """Compose the local-only public published reader and fail closed on setup."""
    resolved_settings = settings if settings is not None else load_settings()
    if resolved_settings.ingress_mode != INGRESS_MODE_PUBLIC_PUBLISHED_UDS:
        raise PublicPublishedStartupError(
            "Public published composition requires public_published_uds ingress."
        )
    if resolved_settings.uds_path is None:
        raise PublicPublishedStartupError(
            "Public published composition requires a Unix socket path."
        )
    try:
        marker_present = index_html_contains_companion_marker()
    except OSError as exc:
        raise PublicPublishedStartupError(
            "Public published index asset is unavailable."
        ) from exc
    if not marker_present:
        raise PublicPublishedStartupError(
            "Public published index asset is missing the companion script "
            "marker; serving it would reference a companion asset the public "
            "allowlist can never answer."
        )
    try:
        engine = create_sqlite_readonly_engine(resolved_settings.database_path)
    except FrameNestPersistenceError as exc:
        raise PublicPublishedStartupError(
            "Public published catalog is not available read-only."
        ) from exc
    try:
        _require_schema_head(engine)
    except Exception:
        dispose_engine(engine)
        raise

    publication_repository = SqliteContentPublicationRepository(engine)
    media_catalog_repository = SqliteMediaCatalogRepository(engine)
    media_metadata_repository = SqliteMediaMetadataRepository(engine)
    media_repository = SqliteMediaRepository(engine)
    library_repository = SqliteLibraryRepository(engine)
    cover_repository = SqliteMediaCoverRepository(engine)
    cover_service = CoverService(
        media_repository,
        library_repository,
        _ForbiddenPublicDerivative(),  # type: ignore[arg-type]
        _ForbiddenPublicDerivative(),  # type: ignore[arg-type]
        FilesystemDurableCoverStorage(resolved_settings.cover_storage_root),
        FilesystemCoverThumbnailCache(resolved_settings.cover_thumbnail_cache_path),
        cover_repository,
    )
    preview_service = GalleryPreviewService(
        media_repository,
        library_repository,
        LocalMediaContentReader(),
        _ForbiddenPublicDerivative(),  # type: ignore[arg-type]
        _ForbiddenPublicDerivative(),  # type: ignore[arg-type]
        FilesystemGalleryPreviewCache(resolved_settings.gallery_preview_cache_path),
    )
    dependencies = PublicPublishedApiDependencies(
        catalog_available=lambda: resolved_settings.database_path.is_file(),
        is_published=publication_repository.is_published,
        list_media=ListMediaCatalog(
            media_catalog_repository,
            cover_states=cover_service.cover_ready_map,
        ),
        get_media=GetMediaCatalogItem(
            media_catalog_repository,
            cover_states=cover_service.cover_ready_map,
        ),
        list_published_tags=(
            media_metadata_repository.list_canonical_tags_for_published_media
        ),
        get_metadata=GetMediaMetadata(media_metadata_repository),
        resolve_content=ResolveMediaContent(
            media_repository,
            library_repository,
            LocalMediaContentReader(),
        ),
        open_gallery_preview=preview_service,
        open_cover_thumbnail=cover_service.open_thumbnail,
        cover_thumbnail_etag=cover_service.thumbnail_etag,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            dispose_engine(engine)

    app = FastAPI(
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.state.settings = resolved_settings
    app.state.public_published = True
    app.include_router(create_public_published_api_router(dependencies))

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "HEAD"],
        include_in_schema=False,
    )
    def reject_unlisted(full_path: str) -> JSONResponse:
        del full_path
        return public_not_found_response()

    @app.middleware("http")
    async def public_ingress_guard(request: Request, call_next):
        if request.method not in _SAFE_METHODS:
            return public_not_found_response()
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        for header_name in (
            "access-control-allow-origin",
            "access-control-allow-credentials",
            "access-control-allow-headers",
            "access-control-allow-methods",
        ):
            if header_name in response.headers:
                del response.headers[header_name]
        return response

    @app.exception_handler(RequestValidationError)
    async def request_validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        del request, exc
        LOGGER.emit(
            level="WARNING",
            event="public_request_validation_rejected",
            operation="dispatch",
            error_code=NOT_FOUND_CODE,
        )
        return public_not_found_response()

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        del request
        if exc.status_code in {401, 403, 404, 405, 406, 415}:
            return public_not_found_response()
        LOGGER.emit(
            level="WARNING",
            event="public_http_exception_rejected",
            operation="dispatch",
            error_code=f"HTTP_{exc.status_code}",
        )
        return public_not_found_response()

    @app.exception_handler(Exception)
    async def unexpected_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        del request
        LOGGER.emit(
            level="ERROR",
            event="public_unexpected_failure",
            operation="dispatch",
            error_code=NOT_FOUND_CODE,
            exception=exc,
        )
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "NOT_FOUND", "message": "Not found."}},
            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
        )

    return app


def _require_schema_head(engine: object) -> None:
    try:
        with engine.connect() as connection:
            rows = connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
    except Exception as exc:
        raise PublicPublishedStartupError(
            "Public published catalog schema is missing."
        ) from exc
    if len(rows) != 1 or str(rows[0][0]) != REQUIRED_PUBLIC_SCHEMA_REVISION:
        raise PublicPublishedStartupError(
            "Public published catalog schema is not at the required head."
        )
