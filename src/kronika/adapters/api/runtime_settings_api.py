"""Administrator runtime-settings mutation API."""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from kronika.infrastructure.runtime_settings import (
    RuntimeSettingsError,
    RuntimeSettingsStore,
)


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody


class AutomaticAnalysisSettingsPutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_media_analysis_enabled: bool
    confirm_cloud_upload: bool | None = None


class AutomaticAnalysisSettingsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    automatic_media_analysis_enabled: bool


@dataclass(frozen=True, slots=True)
class RuntimeSettingsApiDependencies:
    """Injected dependencies for administrator runtime-settings routes."""

    store: RuntimeSettingsStore


def create_runtime_settings_api_router(
    dependencies: RuntimeSettingsApiDependencies,
) -> APIRouter:
    """Create the administrator runtime-settings router."""
    router = APIRouter()

    @router.put(
        "/api/admin/settings/automatic-analysis",
        response_model=AutomaticAnalysisSettingsResponse,
        responses={
            422: {"model": ErrorResponse},
            503: {"model": ErrorResponse},
        },
    )
    def put_automatic_analysis_settings(
        request: AutomaticAnalysisSettingsPutRequest,
    ) -> AutomaticAnalysisSettingsResponse | JSONResponse:
        if (
            request.automatic_media_analysis_enabled is True
            and request.confirm_cloud_upload is not True
        ):
            return _error(
                "CLOUD_CONFIRMATION_REQUIRED",
                "Cloud frame upload confirmation is required.",
                422,
            )
        try:
            enabled = dependencies.store.set_enabled(
                request.automatic_media_analysis_enabled
            )
        except RuntimeSettingsError:
            return _error(
                "SETTINGS_UNAVAILABLE",
                "Automatic analysis settings could not be saved.",
                503,
            )
        return AutomaticAnalysisSettingsResponse(
            automatic_media_analysis_enabled=enabled
        )

    return router


def _error(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=ErrorResponse(error=ErrorBody(code=code, message=message)).model_dump(),
    )
