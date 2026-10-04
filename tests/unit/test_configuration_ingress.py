"""Unit tests for the Tailscale UDS ingress configuration boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from kronika.configuration import KronikaSettings


def _tailscale_values(**overrides: object) -> dict[str, object]:
    values: dict[str, object] = {
        "ingress_mode": "tailscale_uds",
        "uds_path": "/run/framenest/framenest.sock",
        "external_origin": "https://nuc-1.tail247768.ts.net",
        "identity_map": {"aecrypto@gmail.com": "admin"},
    }
    values.update(overrides)
    return values


VALID_EXTENSION_ORIGIN = "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"


def test_default_ingress_mode_is_tcp_without_remote_fields() -> None:
    settings = KronikaSettings(_env_file=None)
    assert settings.ingress_mode == "tcp"
    assert settings.uds_path is None
    assert settings.external_origin is None
    assert settings.identity_map == {}
    assert settings.companion_extension_origins == []


def test_tailscale_uds_mode_accepts_exact_configuration() -> None:
    settings = KronikaSettings(**_tailscale_values(), _env_file=None)
    assert settings.ingress_mode == "tailscale_uds"
    assert settings.uds_path == Path("/run/framenest/framenest.sock")
    assert settings.external_origin == "https://nuc-1.tail247768.ts.net"
    assert settings.identity_map == {"aecrypto@gmail.com": "admin"}


def test_tailscale_uds_mode_rejects_explicit_port_origin() -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            **_tailscale_values(external_origin="https://nuc-1.example.ts.net:8443"),
            _env_file=None,
        )


@pytest.mark.parametrize(
    "field, value",
    [
        ("uds_path", None),
        ("external_origin", None),
        ("identity_map", {}),
        ("identity_map", {"user@example.com": "user"}),
    ],
)
def test_tailscale_uds_mode_requires_socket_origin_and_admin(
    field: str, value: object
) -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(**_tailscale_values(**{field: value}), _env_file=None)


@pytest.mark.parametrize(
    "origin",
    [
        "http://nuc-1.tail247768.ts.net",
        "https://NUC-1.tail247768.ts.net",
        "https://nuc-1.tail247768.ts.net/",
        "https://nuc-1.tail247768.ts.net/path",
        "https://nuc-1.tail247768.ts.net?query=1",
        "https://nuc-1.tail247768.ts.net#fragment",
        "https://user@nuc-1.tail247768.ts.net",
        "https://localhost",
        "https://nuc-1.tail247768.ts.net:443",
        "https://nuc-1.tail247768.ts.net:0",
        "https://nuc-1.tail247768.ts.net:99999",
        "https://nuc-1.tail247768.ts.net:abc",
        "https://nuc-1.tail247768.ts.net:",
        "nuc-1.tail247768.ts.net",
        "",
    ],
)
def test_external_origin_must_be_an_exact_https_origin(origin: str) -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            **_tailscale_values(external_origin=origin), _env_file=None
        )


def test_uds_path_must_be_absolute() -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            **_tailscale_values(uds_path="relative/framenest.sock"), _env_file=None
        )


def test_identity_map_rejects_unknown_roles() -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            **_tailscale_values(identity_map={"aecrypto@gmail.com": "superuser"}),
            _env_file=None,
        )


def test_tcp_mode_tolerates_absent_remote_fields() -> None:
    settings = KronikaSettings(ingress_mode="tcp", _env_file=None)
    assert settings.ingress_mode == "tcp"


@pytest.mark.parametrize("host", ["127.0.0.1", "::1"])
def test_tcp_mode_accepts_loopback_hosts(host: str) -> None:
    settings = KronikaSettings(ingress_mode="tcp", host=host, _env_file=None)
    assert settings.host == host


@pytest.mark.parametrize(
    "host",
    [
        "0.0.0.0",
        "192.168.1.50",
        "8.8.8.8",
        "::",
        "2001:db8::1",
        "fe80::1",
    ],
)
def test_tcp_mode_rejects_non_loopback_hosts(host: str) -> None:
    with pytest.raises(ValidationError, match="loopback"):
        KronikaSettings(ingress_mode="tcp", host=host, _env_file=None)


def test_tcp_mode_rejects_non_loopback_host_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRAMENEST_INGRESS_MODE", "tcp")
    monkeypatch.setenv("FRAMENEST_HOST", "0.0.0.0")
    with pytest.raises(ValidationError, match="loopback"):
        KronikaSettings(_env_file=None)


def test_uds_modes_do_not_constrain_tcp_host(tmp_path: Path) -> None:
    tailscale = KronikaSettings(**_tailscale_values(host="0.0.0.0"), _env_file=None)
    assert tailscale.ingress_mode == "tailscale_uds"
    public = KronikaSettings(
        ingress_mode="public_published_uds",
        database_path=tmp_path / "catalog.sqlite3",
        host="0.0.0.0",
        _env_file=None,
    )
    assert public.ingress_mode == "public_published_uds"


def test_unknown_ingress_mode_is_rejected() -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(ingress_mode="lan", _env_file=None)


def test_public_published_uds_mode_defaults_distinct_socket(tmp_path: Path) -> None:
    settings = KronikaSettings(
        ingress_mode="public_published_uds",
        database_path=tmp_path / "catalog.sqlite3",
        _env_file=None,
    )
    assert settings.ingress_mode == "public_published_uds"
    assert settings.uds_path == Path("/run/framenest/framenest-public.sock")
    assert settings.uds_path != Path("/run/framenest/framenest.sock")


def test_public_published_uds_mode_rejects_workspace_socket(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            ingress_mode="public_published_uds",
            database_path=tmp_path / "catalog.sqlite3",
            uds_path="/run/framenest/framenest.sock",
            _env_file=None,
        )


def test_public_published_uds_mode_accepts_explicit_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "public.sock"
    settings = KronikaSettings(
        ingress_mode="public_published_uds",
        database_path=tmp_path / "catalog.sqlite3",
        uds_path=socket_path,
        _env_file=None,
    )
    assert settings.uds_path == socket_path


def test_tailscale_settings_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRAMENEST_INGRESS_MODE", "tailscale_uds")
    monkeypatch.setenv("FRAMENEST_UDS_PATH", "/run/framenest/framenest.sock")
    monkeypatch.setenv(
        "FRAMENEST_EXTERNAL_ORIGIN", "https://nuc-1.tail247768.ts.net"
    )
    monkeypatch.setenv(
        "FRAMENEST_IDENTITY_MAP", '{"aecrypto@gmail.com": "admin"}'
    )
    settings = KronikaSettings(_env_file=None)
    assert settings.ingress_mode == "tailscale_uds"
    assert settings.uds_path == Path("/run/framenest/framenest.sock")
    assert settings.identity_map == {"aecrypto@gmail.com": "admin"}


def test_ingress_env_vars_do_not_leak_into_repr() -> None:
    settings = KronikaSettings(**_tailscale_values(), _env_file=None)
    rendered = repr(settings)
    assert "aecrypto@gmail.com" not in rendered
    assert "framenest.sock" not in rendered


def test_companion_extension_origins_accept_exact_chrome_ids() -> None:
    settings = KronikaSettings(
        **_tailscale_values(companion_extension_origins=[VALID_EXTENSION_ORIGIN]),
        _env_file=None,
    )
    assert settings.companion_extension_origins == [VALID_EXTENSION_ORIGIN]


@pytest.mark.parametrize(
    "origins",
    [
        ["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaQ"],
        ["chrome-extension://short"],
        ["https://evil.example"],
        ["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/path"],
        ["CHROME-EXTENSION://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"],
        [VALID_EXTENSION_ORIGIN, VALID_EXTENSION_ORIGIN],
        [VALID_EXTENSION_ORIGIN] * 5,
        "chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    ],
)
def test_companion_extension_origins_reject_inexact_values(origins: object) -> None:
    with pytest.raises(ValidationError):
        KronikaSettings(
            **_tailscale_values(companion_extension_origins=origins),
            _env_file=None,
        )


def test_companion_extension_origins_load_from_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("FRAMENEST_INGRESS_MODE", "tailscale_uds")
    monkeypatch.setenv("FRAMENEST_UDS_PATH", "/run/framenest/framenest.sock")
    monkeypatch.setenv(
        "FRAMENEST_EXTERNAL_ORIGIN", "https://nuc-1.tail247768.ts.net"
    )
    monkeypatch.setenv(
        "FRAMENEST_IDENTITY_MAP", '{"aecrypto@gmail.com": "admin"}'
    )
    monkeypatch.setenv(
        "FRAMENEST_COMPANION_EXTENSION_ORIGINS",
        '["chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"]',
    )
    settings = KronikaSettings(_env_file=None)
    assert settings.companion_extension_origins == [VALID_EXTENSION_ORIGIN]
