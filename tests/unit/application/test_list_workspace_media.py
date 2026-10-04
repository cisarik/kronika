"""Unit evidence for the workspace media list use-case bounds."""

from __future__ import annotations

import pytest

from kronika.application.ports.media_attribution import WorkspaceMediaPage
from kronika.application.workspace_media import (
    ListWorkspaceMedia,
    WorkspaceMediaValidationError,
)


class _Repo:
    def list_workspace_media(self, *, login_key: str, limit: int, offset: int) -> WorkspaceMediaPage:
        return WorkspaceMediaPage(items=(), total=0, limit=limit, offset=offset)


def test_list_workspace_media_rejects_empty_login_and_invalid_bounds() -> None:
    service = ListWorkspaceMedia(_Repo())
    with pytest.raises(WorkspaceMediaValidationError):
        service.execute(login_key="")
    with pytest.raises(WorkspaceMediaValidationError):
        service.execute(login_key="alice@example.com", limit=0)
    with pytest.raises(WorkspaceMediaValidationError):
        service.execute(login_key="alice@example.com", offset=-1)
    page = service.execute(login_key="alice@example.com")
    assert page.total == 0
    assert page.limit == 24
