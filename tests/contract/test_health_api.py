"""Contract tests for the FastAPI health presentation adapter."""

from __future__ import annotations

import asyncio
import json
import socket
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from kronika.adapters.api.application import create_app
from kronika.adapters.api.upload_api import UploadApiDependencies
from kronika.configuration import KronikaSettings, load_settings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

FRAMENEST_ENV_VARS = ("FRAMENEST_HOST", "FRAMENEST_PORT", "FRAMENEST_API_KEY")
REPRESENTATIVE_SECRET = "contract-test-api-key-secret"


class _LifecycleCoordinator:
    def __init__(self) -> None:
        self.starts = 0
        self.shutdowns = 0
        self.notifications = 0

    async def start(self) -> None:
        self.starts += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1

    def notify(self) -> None:
        self.notifications += 1


@pytest.fixture(autouse=True)
def isolate_framenest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in FRAMENEST_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def settings_with_secret() -> KronikaSettings:
    return KronikaSettings(
        host="127.0.0.1",
        api_key=SecretStr(REPRESENTATIVE_SECRET),
        _env_file=None,
    )


def test_create_app_returns_fastapi_application(
    settings_with_secret: KronikaSettings,
) -> None:
    app = create_app(settings=settings_with_secret)
    assert isinstance(app, FastAPI)


def test_supplied_settings_are_used_without_loading_env(
    settings_with_secret: KronikaSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_load_settings(*args: Any, **kwargs: Any) -> KronikaSettings:
        raise AssertionError("load_settings must not be called when settings are supplied")

    monkeypatch.setattr(
        "kronika.adapters.api.application.load_settings",
        fail_load_settings,
    )
    app = create_app(settings=settings_with_secret)
    assert app.state.settings is settings_with_secret


def test_omitted_settings_use_centralized_load_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_settings = KronikaSettings(host="127.0.0.1", _env_file=None)
    load_settings_mock = MagicMock(return_value=expected_settings)
    monkeypatch.setattr(
        "kronika.adapters.api.application.load_settings",
        load_settings_mock,
    )
    app = create_app()
    load_settings_mock.assert_called_once_with()
    assert app.state.settings is expected_settings


def test_health_endpoint_returns_ok_status(
    settings_with_secret: KronikaSettings,
) -> None:
    app = create_app(settings=settings_with_secret)
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_application_lifespan_owns_upload_validation_coordinator(tmp_path: Path) -> None:
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        upload_quarantine_root=quarantine_root,
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    coordinator = app.state.upload_validation_coordinator

    assert coordinator is not None
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert not coordinator.runner_done

    assert coordinator.runner_done
    assert not coordinator.executor_running


def test_application_lifespan_shuts_down_owned_coordinator_before_database_disposal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.adapters.api import application

    events: list[str] = []
    quarantine_root = tmp_path / "quarantine"
    quarantine_root.mkdir()
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        upload_quarantine_root=quarantine_root,
        _env_file=None,
    )
    upgrade_database_to_head(settings)

    original_dispose_engine = application.dispose_engine

    def tracked_dispose_engine(engine: object) -> None:
        events.append("dispose_engine")
        original_dispose_engine(engine)

    monkeypatch.setattr(application, "dispose_engine", tracked_dispose_engine)
    app = create_app(settings=settings)
    coordinator = app.state.upload_validation_coordinator
    original_shutdown = coordinator.shutdown

    async def tracked_shutdown(deadline=None) -> None:
        events.append("coordinator_shutdown_start")
        await original_shutdown(deadline)
        events.append("coordinator_shutdown_end")

    coordinator.shutdown = tracked_shutdown

    with TestClient(app) as client:
        assert client.get("/health").status_code == 200

    assert events == [
        "coordinator_shutdown_start",
        "coordinator_shutdown_end",
        "dispose_engine",
    ]


def test_application_lifespan_propagates_application_cancellation(tmp_path: Path) -> None:
    async def scenario() -> None:
        quarantine_root = tmp_path / "quarantine"
        quarantine_root.mkdir()
        settings = KronikaSettings(
            database_path=tmp_path / "catalog.sqlite3",
            upload_quarantine_root=quarantine_root,
            _env_file=None,
        )
        upgrade_database_to_head(settings)
        app = create_app(settings=settings)
        coordinator = app.state.upload_validation_coordinator

        with pytest.raises(asyncio.CancelledError):
            async with app.router.lifespan_context(app):
                raise asyncio.CancelledError

        assert coordinator.runner_done
        assert not coordinator.executor_running

    asyncio.run(scenario())


def test_application_lifespan_does_not_own_injected_upload_validation_coordinator() -> None:
    coordinator = _LifecycleCoordinator()
    dependencies = UploadApiDependencies(
        transport=object(),
        validation_coordinator=coordinator,
    )
    first = create_app(
        settings=KronikaSettings(_env_file=None),
        upload_api_dependencies=dependencies,
    )
    second = create_app(
        settings=KronikaSettings(_env_file=None),
        upload_api_dependencies=dependencies,
    )

    assert first.state.upload_validation_coordinator is coordinator
    assert second.state.upload_validation_coordinator is coordinator
    with TestClient(first) as first_client:
        assert first_client.get("/health").status_code == 200
        with TestClient(second) as second_client:
            assert second_client.get("/health").status_code == 200

    assert coordinator.starts == 0
    assert coordinator.shutdowns == 0


def test_cloud_status_endpoint_reports_sanitized_loopback_status(
    settings_with_secret: KronikaSettings,
) -> None:
    app = create_app(settings=settings_with_secret)
    response = TestClient(app).get("/api/status/cloud")

    assert response.status_code == 200
    assert response.json() == {
        "server": "connected",
        "connection": "loopback",
        "remote_access": None,
    }
    assert "127.0.0.1" not in response.text
    assert REPRESENTATIVE_SECRET not in response.text


def test_health_contract_is_present_in_openapi(
    settings_with_secret: KronikaSettings,
) -> None:
    app = create_app(settings=settings_with_secret)
    schema = app.openapi()
    health_operation = schema["paths"]["/health"]["get"]
    response_schema = health_operation["responses"]["200"]["content"]["application/json"]["schema"]
    assert response_schema == {"$ref": "#/components/schemas/HealthResponse"}
    assert schema["components"]["schemas"]["HealthResponse"] == {
        "properties": {"status": {"const": "ok", "title": "Status", "type": "string"}},
        "required": ["status"],
        "title": "HealthResponse",
        "type": "object",
    }


def test_api_key_not_disclosed_in_health_response_openapi_or_app_repr(
    settings_with_secret: KronikaSettings,
) -> None:
    app = create_app(settings=settings_with_secret)
    health_body = TestClient(app).get("/health").text
    openapi_output = json.dumps(app.openapi())
    application_repr = f"{app!r}"
    for surface in (health_body, openapi_output, application_repr):
        assert REPRESENTATIVE_SECRET not in surface


def test_import_and_factory_creation_do_not_bind_network_listener(
    settings_with_secret: KronikaSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_attempts: list[tuple[Any, ...]] = []
    original_bind = socket.socket.bind

    def tracked_bind(self, address: tuple[Any, ...]) -> None:
        bind_attempts.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", tracked_bind)
    app = create_app(settings=settings_with_secret)
    TestClient(app).get("/health")
    assert bind_attempts == []
