"""Application service for the contributor-scoped workspace media list."""

from __future__ import annotations

from dataclasses import dataclass

from kronika.application.ports.media_attribution import (
    FrameNestMediaAttributionRepositoryError,
    MediaAttributionRepository,
    WorkspaceMediaPage,
)

DEFAULT_WORKSPACE_MEDIA_LIMIT = 24
MAX_WORKSPACE_MEDIA_LIMIT = 100


class WorkspaceMediaValidationError(ValueError):
    """Raised for invalid workspace list input."""


@dataclass(frozen=True, slots=True)
class ListWorkspaceMedia:
    """Normalize and execute one caller-scoped workspace media query."""

    repository: MediaAttributionRepository

    def execute(
        self,
        *,
        login_key: str,
        limit: int = DEFAULT_WORKSPACE_MEDIA_LIMIT,
        offset: int = 0,
    ) -> WorkspaceMediaPage:
        if not isinstance(login_key, str) or not login_key.strip():
            raise WorkspaceMediaValidationError()
        bounded_limit = _bounded_int(
            limit,
            minimum=1,
            maximum=MAX_WORKSPACE_MEDIA_LIMIT,
        )
        bounded_offset = _bounded_int(offset, minimum=0, maximum=None)
        try:
            return self.repository.list_workspace_media(
                login_key=login_key,
                limit=bounded_limit,
                offset=bounded_offset,
            )
        except FrameNestMediaAttributionRepositoryError:
            raise


def _bounded_int(value: int, *, minimum: int, maximum: int | None) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise WorkspaceMediaValidationError()
    if maximum is not None and value > maximum:
        raise WorkspaceMediaValidationError()
    return value
