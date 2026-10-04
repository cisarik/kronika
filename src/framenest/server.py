"""Uvicorn ASGI runtime composition for FrameNest."""

from __future__ import annotations

import os
import socket
import stat
import sys
from pathlib import Path
from typing import NoReturn

import uvicorn

from framenest.adapters.api.application import create_app
from framenest.configuration import (
    INGRESS_MODE_PUBLIC_PUBLISHED_UDS,
    INGRESS_MODE_TAILSCALE_UDS,
    FrameNestConfigurationError,
    FrameNestSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from framenest.structured_logging import build_uvicorn_log_config, get_logger

_UDS_SOCKET_OWNER_ONLY_MODE = 0o600
_UDS_SOCKET_GROUP_OTHER_MODE_MASK = 0o077
LOGGER = get_logger("server")


class UdsSocketProvenanceError(RuntimeError):
    """Raised when a bound UDS socket fails owner-only provenance verification."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _tighten_uds_socket_permissions(uds_path: Path) -> None:
    os.chmod(str(uds_path), _UDS_SOCKET_OWNER_ONLY_MODE)


def _verify_uds_socket_provenance(uds_path: Path) -> None:
    stat_result = os.stat(uds_path)
    if not stat.S_ISSOCK(stat_result.st_mode):
        raise UdsSocketProvenanceError("not_a_socket")
    if stat_result.st_mode & _UDS_SOCKET_GROUP_OTHER_MODE_MASK:
        raise UdsSocketProvenanceError("permission_bits_not_owner_only")
    if stat_result.st_uid != os.geteuid():
        raise UdsSocketProvenanceError("foreign_owner")


def _fail_closed_uds_socket(
    reason: str,
    exception: BaseException | None = None,
) -> NoReturn:
    LOGGER.emit(
        level="CRITICAL",
        event="uds_socket_provenance_failure",
        operation="startup",
        error_code="UDS_SOCKET_PROVENANCE_FAILURE",
        retryable=False,
        exception=exception,
        context={"reason": reason},
    )
    raise UdsSocketProvenanceError(reason) from None


def _close_listening_servers(server: uvicorn.Server) -> None:
    for asyncio_server in getattr(server, "servers", None) or []:
        asyncio_server.close()


class UdsProvenanceVerifyingServer(uvicorn.Server):
    """Uvicorn server that tightens and verifies UDS socket provenance at startup.

    Regains control in the same event-loop step after uvicorn binds the UDS
    socket, tightens it to owner-only, asserts provenance, and exits
    fail-closed before any request can be served on violation.
    """

    async def startup(self, sockets: list[socket.socket] | None = None) -> None:
        await super().startup(sockets=sockets)
        if self.should_exit:
            return
        uds_path = self.config.uds
        if uds_path is None:
            return
        resolved_uds_path = Path(uds_path)
        try:
            _tighten_uds_socket_permissions(resolved_uds_path)
        except OSError as exc:
            _close_listening_servers(self)
            _fail_closed_uds_socket("chmod_failed", exc)
        try:
            _verify_uds_socket_provenance(resolved_uds_path)
        except UdsSocketProvenanceError as exc:
            _close_listening_servers(self)
            _fail_closed_uds_socket(exc.reason, exc)
        except OSError as exc:
            _close_listening_servers(self)
            _fail_closed_uds_socket("stat_failed", exc)


def create_server(
    settings: FrameNestSettings | None = None,
) -> uvicorn.Server:
    resolved_settings = settings if settings is not None else load_settings()
    server_class: type[uvicorn.Server] = uvicorn.Server
    config_kwargs: dict[str, object] = {}
    if resolved_settings.ingress_mode in {
        INGRESS_MODE_TAILSCALE_UDS,
        INGRESS_MODE_PUBLIC_PUBLISHED_UDS,
    }:
        assert resolved_settings.uds_path is not None
        config_kwargs["uds"] = str(resolved_settings.uds_path)
        server_class = UdsProvenanceVerifyingServer
    else:
        config_kwargs["host"] = resolved_settings.host
        config_kwargs["port"] = resolved_settings.port
    config = uvicorn.Config(
        app=create_app(settings=resolved_settings),
        reload=False,
        workers=1,
        proxy_headers=False,
        forwarded_allow_ips="",
        access_log=False,
        log_config=build_uvicorn_log_config(),
        timeout_graceful_shutdown=5,
        **config_kwargs,
    )
    return server_class(config)


def run_server(
    settings: FrameNestSettings | None = None,
) -> None:
    server = create_server(settings=settings)
    server.run()


def main() -> None:
    try:
        run_server()
    except KeyboardInterrupt:
        return
    except FrameNestConfigurationError as exc:
        print(f"Kronika configuration error: {exc}", file=sys.stderr)
        status = (
            exc.exit_status
            if isinstance(exc, IdentityEnvironmentConfigurationError)
            else 1
        )
        raise SystemExit(status) from None


if __name__ == "__main__":
    main()
