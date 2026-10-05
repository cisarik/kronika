"""Unit tests for the Uvicorn runtime composition boundary."""

from __future__ import annotations

import ast
import importlib
import os
import socket
import stat
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import uvicorn
from pydantic import SecretStr

from tests.support.kronika_identity import expected

from kronika.configuration import KronikaSettings, load_settings

FORBIDDEN_UVICORN_IMPORT_ROOT = "uvicorn"
ALLOWED_UVICORN_MODULE = Path("src/kronika/server.py")
SOURCE_ROOT = Path("src/kronika")
REPRESENTATIVE_SECRET = "runtime-unit-test-api-key-secret"


def _module_name_from_import(node: ast.Import | ast.ImportFrom) -> str | None:
    if isinstance(node, ast.Import):
        return node.names[0].name.split(".")[0]
    if node.module is None:
        return None
    return node.module.split(".")[0]


def _collect_uvicorn_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        root = _module_name_from_import(node)
        if root == FORBIDDEN_UVICORN_IMPORT_ROOT:
            violations.append(root)
    return violations


@pytest.fixture
def settings_with_secret() -> KronikaSettings:
    return KronikaSettings(
        host="127.0.0.1",
        port=8000,
        api_key=SecretStr(REPRESENTATIVE_SECRET),
        _env_file=None,
    )


def test_importing_server_module_has_no_runtime_side_effects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bind_attempts: list[tuple[Any, ...]] = []
    original_bind = socket.socket.bind

    def tracked_bind(self, address: tuple[Any, ...]) -> None:
        bind_attempts.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", tracked_bind)

    load_settings_mock = MagicMock(side_effect=AssertionError("load_settings must not run on import"))
    create_app_mock = MagicMock(side_effect=AssertionError("create_app must not run on import"))
    config_mock = MagicMock(side_effect=AssertionError("uvicorn.Config must not run on import"))
    server_init_attempts: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    class RaisingUvicornServerStub:
        def __init__(self, *args: object, **kwargs: object) -> None:
            server_init_attempts.append((args, kwargs))
            raise AssertionError("uvicorn.Server must not run on import")

    monkeypatch.setattr("kronika.server.load_settings", load_settings_mock)
    monkeypatch.setattr("kronika.server.create_app", create_app_mock)
    monkeypatch.setattr("uvicorn.Config", config_mock)
    monkeypatch.setattr("uvicorn.Server", RaisingUvicornServerStub)

    module_name = "kronika.server"
    sys.modules.pop(module_name, None)
    importlib.import_module(module_name)

    assert bind_attempts == []
    assert server_init_attempts == []
    load_settings_mock.assert_not_called()
    create_app_mock.assert_not_called()
    config_mock.assert_not_called()
    sys.modules.pop(module_name, None)


def test_create_server_returns_uvicorn_server(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert isinstance(server, uvicorn.Server)
    assert type(server) is uvicorn.Server


def test_supplied_settings_bypass_load_settings(
    settings_with_secret: KronikaSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import create_server

    load_settings_mock = MagicMock(side_effect=AssertionError("load_settings must not be called"))
    monkeypatch.setattr("kronika.server.load_settings", load_settings_mock)
    server = create_server(settings=settings_with_secret)
    load_settings_mock.assert_not_called()
    assert server.config.app.state.settings is settings_with_secret


def test_omitted_settings_invoke_load_settings_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import create_server

    expected_settings = KronikaSettings(host="127.0.0.1", port=8000, _env_file=None)
    load_settings_mock = MagicMock(return_value=expected_settings)
    monkeypatch.setattr("kronika.server.load_settings", load_settings_mock)
    server = create_server()
    load_settings_mock.assert_called_once_with()
    assert server.config.app.state.settings is expected_settings


def test_default_config_host_is_loopback(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert server.config.host == "127.0.0.1"


def test_default_config_port_is_8000(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert server.config.port == 8000


def test_framenest_host_and_port_overrides_propagate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import create_server

    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.5")
    monkeypatch.setenv("FRAMENEST_PORT", "8765")
    server = create_server()
    assert server.config.host == "127.0.0.5"
    assert server.config.port == 8765


def test_uvicorn_host_and_port_env_vars_do_not_override_framenest_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import create_server

    monkeypatch.setenv("FRAMENEST_HOST", "127.0.0.1")
    monkeypatch.setenv("FRAMENEST_PORT", "8000")
    monkeypatch.setenv("UVICORN_HOST", "0.0.0.0")
    monkeypatch.setenv("UVICORN_PORT", "9999")
    server = create_server()
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 8000


def test_create_server_passes_framenest_log_config_and_disables_access_log(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert isinstance(server.config.log_config, dict)
    assert server.config.log_config["formatters"]["kronika_json"]["()"].endswith(
        "FrameNestJsonFormatter"
    )
    assert server.config.access_log is False


def test_proxy_headers_are_disabled_without_wildcard_trust(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert server.config.proxy_headers is False
    assert server.config.forwarded_allow_ips != "*"
    assert "*" not in str(server.config.forwarded_allow_ips)


def test_reload_disabled_and_single_worker(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert server.config.reload is False
    assert server.config.workers == 1
    assert server.config.timeout_graceful_shutdown == 5


def test_api_secret_not_disclosed_in_server_representations(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    surfaces = (
        f"{server!r}",
        f"{server.config!r}",
        f"{server.config.app!r}",
        str(server.config),
    )
    for surface in surfaces:
        assert REPRESENTATIVE_SECRET not in surface


def test_run_server_invokes_server_run_once_without_real_listener(
    settings_with_secret: KronikaSettings,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import run_server

    run_mock = MagicMock()
    server_mock = MagicMock()
    server_mock.run = run_mock
    create_server_mock = MagicMock(return_value=server_mock)
    monkeypatch.setattr("kronika.server.create_server", create_server_mock)

    run_server(settings=settings_with_secret)

    create_server_mock.assert_called_once_with(settings=settings_with_secret)
    run_mock.assert_called_once_with()


def test_main_delegates_to_run_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import main

    run_server_mock = MagicMock()
    monkeypatch.setattr("kronika.server.run_server", run_server_mock)
    main()
    run_server_mock.assert_called_once_with()


def test_main_catches_keyboard_interrupt_without_propagation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import main

    run_server_mock = MagicMock(side_effect=KeyboardInterrupt)
    monkeypatch.setattr("kronika.server.run_server", run_server_mock)
    main()
    run_server_mock.assert_called_once_with()


def test_main_does_not_swallow_system_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import main

    run_server_mock = MagicMock(side_effect=SystemExit(3))
    monkeypatch.setattr("kronika.server.run_server", run_server_mock)
    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 3


def test_main_does_not_swallow_unexpected_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import main

    run_server_mock = MagicMock(side_effect=RuntimeError("startup failure"))
    monkeypatch.setattr("kronika.server.run_server", run_server_mock)
    with pytest.raises(RuntimeError, match="startup failure"):
        main()


def test_production_uvicorn_imports_are_confined_to_server_module() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        relative_path = path.relative_to(repository_root)
        if relative_path == ALLOWED_UVICORN_MODULE:
            continue
        forbidden_roots = _collect_uvicorn_imports(path)
        if forbidden_roots:
            violations.append(f"{relative_path}: {sorted(set(forbidden_roots))}")
    assert violations == []


def test_create_server_binds_only_unix_socket_in_tailscale_mode(
    tmp_path: Path,
) -> None:
    from kronika.server import UdsProvenanceVerifyingServer, create_server

    uds_path = tmp_path / "framenest.sock"
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=uds_path,
        external_origin="https://nuc-1.example.ts.net",
        identity_map={"admin@example.com": "admin"},
        _env_file=None,
    )
    server = create_server(settings=settings)
    assert isinstance(server, UdsProvenanceVerifyingServer)
    assert server.config.uds == str(uds_path)
    assert server.config.proxy_headers is False
    assert server.config.forwarded_allow_ips != "*"


def test_create_server_binds_only_unix_socket_in_public_published_mode(
    tmp_path: Path,
) -> None:
    from kronika.infrastructure.persistence.migrations import upgrade_database_to_head
    from kronika.server import UdsProvenanceVerifyingServer, create_server

    database_path = tmp_path / "catalog.sqlite3"
    settings = KronikaSettings(
        database_path=database_path,
        gallery_preview_cache_path=tmp_path / "previews",
        cover_storage_root=tmp_path / "covers",
        cover_thumbnail_cache_path=tmp_path / "thumbnails",
        ingress_mode="public_published_uds",
        uds_path=tmp_path / "public.sock",
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    server = create_server(settings=settings)
    assert isinstance(server, UdsProvenanceVerifyingServer)
    assert server.config.uds == str(tmp_path / "public.sock")
    assert server.config.host is None or server.config.uds is not None
    assert getattr(server.config, "port", None) in {None, 8000}


def test_create_server_binds_tcp_in_default_mode(
    settings_with_secret: KronikaSettings,
) -> None:
    from kronika.server import create_server

    server = create_server(settings=settings_with_secret)
    assert server.config.uds is None
    assert server.config.host == "127.0.0.1"
    assert server.config.port == 8000


def test_fastapi_imports_remain_confined_to_adapters_api() -> None:
    repository_root = Path(__file__).resolve().parents[2]
    forbidden_import_roots = frozenset({"fastapi", "starlette"})
    allowed_fastapi_package_root = Path("src/kronika/adapters/api")
    violations: list[str] = []
    for path in sorted((repository_root / SOURCE_ROOT).rglob("*.py")):
        relative_path = path.relative_to(repository_root)
        if allowed_fastapi_package_root in relative_path.parents:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        forbidden_roots: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            root = _module_name_from_import(node)
            if root in forbidden_import_roots:
                forbidden_roots.append(root)
        if forbidden_roots:
            violations.append(f"{relative_path}: {sorted(set(forbidden_roots))}")
    assert violations == []


def _create_bound_unix_socket(path: Path) -> None:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.bind(str(path))
    finally:
        sock.close()


def test_tighten_uds_socket_permissions_sets_owner_only_mode(tmp_path: Path) -> None:
    from kronika.server import _tighten_uds_socket_permissions

    socket_path = tmp_path / "seam-tighten.sock"
    _create_bound_unix_socket(socket_path)
    try:
        os.chmod(str(socket_path), 0o666)
        _tighten_uds_socket_permissions(socket_path)
        assert stat.S_IMODE(os.stat(socket_path).st_mode) == 0o600
    finally:
        socket_path.unlink(missing_ok=True)


def test_verify_uds_socket_provenance_accepts_owner_only_socket(tmp_path: Path) -> None:
    from kronika.server import _verify_uds_socket_provenance

    socket_path = tmp_path / "seam-accept.sock"
    _create_bound_unix_socket(socket_path)
    try:
        os.chmod(str(socket_path), 0o600)
        _verify_uds_socket_provenance(socket_path)
    finally:
        socket_path.unlink(missing_ok=True)


def test_verify_uds_socket_provenance_rejects_non_socket(tmp_path: Path) -> None:
    from kronika.server import UdsSocketProvenanceError, _verify_uds_socket_provenance

    plain_path = tmp_path / "seam-plain.sock"
    plain_path.write_bytes(b"")
    try:
        with pytest.raises(UdsSocketProvenanceError) as exc_info:
            _verify_uds_socket_provenance(plain_path)
        assert exc_info.value.reason == "not_a_socket"
    finally:
        plain_path.unlink(missing_ok=True)


def test_verify_uds_socket_provenance_rejects_group_or_other_bits(tmp_path: Path) -> None:
    from kronika.server import UdsSocketProvenanceError, _verify_uds_socket_provenance

    socket_path = tmp_path / "seam-bits.sock"
    _create_bound_unix_socket(socket_path)
    try:
        os.chmod(str(socket_path), 0o640)
        with pytest.raises(UdsSocketProvenanceError) as exc_info:
            _verify_uds_socket_provenance(socket_path)
        assert exc_info.value.reason == "permission_bits_not_owner_only"
    finally:
        socket_path.unlink(missing_ok=True)


def test_verify_uds_socket_provenance_rejects_foreign_owner(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.server import UdsSocketProvenanceError, _verify_uds_socket_provenance

    socket_path = tmp_path / "seam-owner.sock"
    _create_bound_unix_socket(socket_path)
    try:
        os.chmod(str(socket_path), 0o600)
        real_euid = os.geteuid()
        monkeypatch.setattr(os, "geteuid", lambda: real_euid + 1)
        with pytest.raises(UdsSocketProvenanceError) as exc_info:
            _verify_uds_socket_provenance(socket_path)
        assert exc_info.value.reason == "foreign_owner"
    finally:
        socket_path.unlink(missing_ok=True)


def test_server_main_reports_the_derived_brand_on_a_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The operator stderr line, reached by making `run_server` fail as designed."""
    from kronika import server
    from kronika.configuration import FrameNestConfigurationError

    def _fail() -> None:
        raise FrameNestConfigurationError("host is not loopback")

    monkeypatch.setattr(server, "run_server", _fail)

    with pytest.raises(SystemExit) as excinfo:
        server.main()

    assert excinfo.value.code == 1
    assert capsys.readouterr().err == (
        expected("{brand} configuration error: ") + "host is not loopback\n"
    )
