"""Thin loopback operator CLI for YouTube manual acquisition."""

from __future__ import annotations

import argparse
from collections.abc import Callable, Mapping, Sequence
import http.client
from ipaddress import ip_address
import json
import math
import sys
import time
from typing import NoReturn, Protocol

from kronika.configuration import (
    FrameNestConfigurationError,
    KronikaSettings,
    IdentityEnvironmentConfigurationError,
    load_settings,
)
from kronika.domain.identities import (
    FrameNestIdentityError,
    YouTubeAcquisitionClaimId,
)
from kronika.domain.youtube_acquisition import (
    FrameNestYouTubeUrlError,
    canonicalize_youtube_url,
)

DEFAULT_WAIT_TIMEOUT_SECONDS = 7_500.0
DEFAULT_POLL_INTERVAL_SECONDS = 1.0
DEFAULT_REQUEST_TIMEOUT_SECONDS = 10.0
MAX_RESPONSE_BYTES = 1_048_576

TERMINAL_STATES = frozenset({"cataloged", "duplicate_resolved", "failed"})


class _UsageError(Exception):
    pass


class _DeclinedError(Exception):
    pass


class _NotConfiguredError(Exception):
    pass


class _TerminalFailureError(Exception):
    def __init__(self, claim_id: str) -> None:
        super().__init__("YouTube acquisition failed.")
        self.claim_id = claim_id


class _WaitTimeoutError(Exception):
    def __init__(self, claim_id: str) -> None:
        super().__init__("YouTube acquisition continues on the server.")
        self.claim_id = claim_id


class _ProtocolError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        raise _UsageError("Invalid YouTube operator command.")


class _Client(Protocol):
    def post(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        ...

    def get(self, path: str) -> dict[str, object]:
        ...


class _LoopbackHttpClient:
    def __init__(self, settings: KronikaSettings) -> None:
        try:
            if not ip_address(settings.host).is_loopback:
                raise _NotConfiguredError()
        except ValueError:
            raise _NotConfiguredError() from None
        self._host = settings.host
        self._port = settings.port

    def post(self, path: str, payload: Mapping[str, object]) -> dict[str, object]:
        body = json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self._request(
            "POST",
            path,
            body=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
        )

    def get(self, path: str) -> dict[str, object]:
        return self._request(
            "GET",
            path,
            body=None,
            headers={"Accept": "application/json"},
        )

    def _request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None,
        headers: Mapping[str, str],
    ) -> dict[str, object]:
        connection = http.client.HTTPConnection(
            self._host,
            self._port,
            timeout=DEFAULT_REQUEST_TIMEOUT_SECONDS,
        )
        try:
            connection.request(method, path, body=body, headers=dict(headers))
            response = connection.getresponse()
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise _ProtocolError()
            media_type = response.getheader("Content-Type")
            if media_type != "application/json":
                raise _ProtocolError()
            try:
                payload = json.loads(response_body)
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise _ProtocolError() from None
            if not isinstance(payload, dict):
                raise _ProtocolError()
            if response.status >= 400:
                _raise_api_error(response.status, payload)
            return payload
        except (_NotConfiguredError, _UsageError, _ProtocolError):
            raise
        except (OSError, http.client.HTTPException) as exc:
            raise _ProtocolError() from exc
        finally:
            connection.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Dispatch one loopback-only operator command."""
    try:
        return _run(
            argv,
            settings_loader=load_settings,
            client_factory=_LoopbackHttpClient,
            input_reader=lambda: sys.stdin.readline(),
            sleep=time.sleep,
            clock=time.monotonic,
        )
    except _UsageError:
        _write_error(
            "YOUTUBE_INVALID_COMMAND",
            "Invalid YouTube operator command.",
        )
        return 2
    except _DeclinedError:
        _write_json_line({"event": "declined"})
        return 1
    except _NotConfiguredError:
        _write_error(
            "YOUTUBE_NOT_CONFIGURED",
            "YouTube operator ingestion is not configured.",
        )
        return 3
    except IdentityEnvironmentConfigurationError as exc:
        _write_error(
            "YOUTUBE_CONFIGURATION_FAILED",
            str(exc),
        )
        return exc.exit_status
    except FrameNestConfigurationError:
        _write_error(
            "YOUTUBE_CONFIGURATION_FAILED",
            "Kronika configuration could not be loaded.",
        )
        return 6
    except _TerminalFailureError as exc:
        _write_error(
            "YOUTUBE_ACQUISITION_FAILED",
            "YouTube acquisition failed.",
            claim_id=exc.claim_id,
        )
        return 4
    except _WaitTimeoutError as exc:
        _write_error(
            "YOUTUBE_WAIT_TIMEOUT",
            "The durable claim continues on the server.",
            claim_id=exc.claim_id,
        )
        return 124
    except KeyboardInterrupt:
        _write_error(
            "YOUTUBE_CLIENT_INTERRUPTED",
            "The durable claim continues on the server.",
        )
        return 130
    except _ProtocolError:
        _write_error(
            "YOUTUBE_LOOPBACK_UNAVAILABLE",
            "The loopback Kronika operator API is unavailable.",
        )
        return 5
    except Exception:
        _write_error(
            "YOUTUBE_LOOPBACK_UNAVAILABLE",
            "The loopback Kronika operator API is unavailable.",
        )
        return 5


def _run(
    argv: Sequence[str] | None,
    *,
    settings_loader: Callable[[], KronikaSettings],
    client_factory: Callable[[KronikaSettings], _Client],
    input_reader: Callable[[], str],
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    settings = settings_loader()
    try:
        if not ip_address(settings.host).is_loopback:
            raise _NotConfiguredError()
    except ValueError:
        raise _NotConfiguredError() from None
    client = client_factory(settings)
    wait_timeout = _wait_timeout(args.wait_timeout)
    if args.operation == "ingest":
        try:
            identity = canonicalize_youtube_url(args.url)
        except FrameNestYouTubeUrlError:
            raise _UsageError() from None
        _print_confirmation_summary(
            subject=identity.canonical_url,
            confirmed=args.confirmed,
            input_reader=input_reader,
        )
        snapshot = client.post(
            "/api/operator/youtube/claims",
            {
                "url": args.url,
                "confirmation_method": (
                    "yes_flag" if args.confirmed else "interactive"
                ),
            },
        )
        return _wait_for_terminal(
            client,
            snapshot,
            wait_timeout=wait_timeout,
            sleep=sleep,
            clock=clock,
        )
    if args.operation == "status":
        claim_id = _parse_claim_id(args.claim_id)
        snapshot = client.get(
            f"/api/operator/youtube/claims/{claim_id}"
        )
        if not args.wait:
            _write_status(snapshot)
            return _terminal_exit(snapshot, claim_id, fail_on_failed=False)
        return _wait_for_terminal(
            client,
            snapshot,
            wait_timeout=wait_timeout,
            sleep=sleep,
            clock=clock,
        )
    if args.operation == "retry":
        claim_id = _parse_claim_id(args.claim_id)
        _print_confirmation_summary(
            subject=f"failed claim {claim_id}",
            confirmed=args.confirmed,
            input_reader=input_reader,
        )
        snapshot = client.post(
            f"/api/operator/youtube/claims/{claim_id}/retry",
            {
                "confirmation_method": (
                    "yes_flag" if args.confirmed else "interactive"
                )
            },
        )
        return _wait_for_terminal(
            client,
            snapshot,
            wait_timeout=wait_timeout,
            sleep=sleep,
            clock=clock,
        )
    raise _UsageError()


def _build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="framenest-youtube")
    commands = parser.add_subparsers(dest="operation", required=True)

    ingest = commands.add_parser("ingest")
    ingest.add_argument("url")
    ingest.add_argument("--yes", action="store_true", dest="confirmed")
    ingest.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        dest="wait_timeout",
    )

    status = commands.add_parser("status")
    status.add_argument("claim_id")
    status.add_argument("--wait", action="store_true")
    status.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        dest="wait_timeout",
    )

    retry = commands.add_parser("retry")
    retry.add_argument("claim_id")
    retry.add_argument("--yes", action="store_true", dest="confirmed")
    retry.add_argument(
        "--wait-timeout",
        type=float,
        default=DEFAULT_WAIT_TIMEOUT_SECONDS,
        dest="wait_timeout",
    )
    return parser


def _print_confirmation_summary(
    *,
    subject: str,
    confirmed: bool,
    input_reader: Callable[[], str],
) -> None:
    _write_json_line(
        {
            "event": "confirmation",
            "target": subject,
            "policy": "cookie_free_public_single_video",
            "cookies": False,
            "playlists": False,
            "transcoding": False,
            "method": "yes_flag" if confirmed else "interactive",
        }
    )
    if confirmed:
        return
    print("Proceed? [y/N] ", end="", file=sys.stderr, flush=True)
    answer = input_reader()
    if answer.strip().lower() not in {"y", "yes"}:
        raise _DeclinedError()


def _wait_for_terminal(
    client: _Client,
    initial: dict[str, object],
    *,
    wait_timeout: float,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> int:
    claim_id = _snapshot_claim_id(initial)
    deadline = clock() + wait_timeout
    previous_key: tuple[object, ...] | None = None
    snapshot = initial
    while True:
        key = _status_key(snapshot)
        if key != previous_key:
            _write_status(snapshot)
            previous_key = key
        state = snapshot.get("state")
        if state in TERMINAL_STATES:
            return _terminal_exit(
                snapshot,
                claim_id,
                fail_on_failed=True,
            )
        if clock() >= deadline:
            raise _WaitTimeoutError(claim_id)
        sleep(DEFAULT_POLL_INTERVAL_SECONDS)
        snapshot = client.get(
            f"/api/operator/youtube/claims/{claim_id}"
        )


def _terminal_exit(
    snapshot: Mapping[str, object],
    claim_id: str,
    *,
    fail_on_failed: bool,
) -> int:
    state = snapshot.get("state")
    if state == "failed" and fail_on_failed:
        raise _TerminalFailureError(claim_id)
    if state in {"cataloged", "duplicate_resolved"}:
        _write_json_line(
            {
                "event": "success",
                "claim_id": claim_id,
                "state": state,
                "result": snapshot.get("result"),
                "media_id": snapshot.get("media_id"),
                "media_location_id": snapshot.get("media_location_id"),
            }
        )
    return 0


def _write_status(snapshot: Mapping[str, object]) -> None:
    claim_id = _snapshot_claim_id(snapshot)
    _write_json_line(
        {
            "event": "status",
            "claim_id": claim_id,
            "state": snapshot.get("state"),
            "upload_state": snapshot.get("upload_state"),
            "downloaded_size_bytes": snapshot.get("downloaded_size_bytes"),
            "cleanup_state": snapshot.get("cleanup_state"),
            "failure_stage": snapshot.get("failure_stage"),
            "failure_code": snapshot.get("failure_code"),
        }
    )


def _snapshot_claim_id(snapshot: Mapping[str, object]) -> str:
    value = snapshot.get("id")
    if not isinstance(value, str):
        raise _ProtocolError()
    return _parse_claim_id(value)


def _status_key(snapshot: Mapping[str, object]) -> tuple[object, ...]:
    return (
        snapshot.get("state"),
        snapshot.get("upload_state"),
        snapshot.get("downloaded_size_bytes"),
        snapshot.get("cleanup_state"),
        snapshot.get("failure_stage"),
        snapshot.get("failure_code"),
        snapshot.get("media_id"),
        snapshot.get("media_location_id"),
    )


def _parse_claim_id(value: object) -> str:
    if not isinstance(value, str):
        raise _UsageError()
    try:
        return YouTubeAcquisitionClaimId.from_string(value).to_string()
    except FrameNestIdentityError:
        raise _UsageError() from None


def _wait_timeout(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        or value <= 0
        or value > 86_400
    ):
        raise _UsageError()
    return float(value)


def _raise_api_error(status: int, payload: Mapping[str, object]) -> NoReturn:
    error = payload.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    if status == 503 and code == "YOUTUBE_OPERATOR_NOT_CONFIGURED":
        raise _NotConfiguredError()
    if status in {400, 404, 409, 413, 415, 422}:
        raise _UsageError()
    raise _ProtocolError()


def _write_json_line(payload: Mapping[str, object]) -> None:
    print(
        json.dumps(
            dict(payload),
            sort_keys=True,
            separators=(",", ":"),
        ),
        file=sys.stdout,
    )


def _write_error(
    error_code: str,
    message: str,
    *,
    claim_id: str | None = None,
) -> None:
    payload: dict[str, object] = {
        "event": "error",
        "error_code": error_code,
        "message": message,
    }
    if claim_id is not None:
        payload["claim_id"] = claim_id
    print(
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
        file=sys.stderr,
    )


if __name__ == "__main__":
    raise SystemExit(main())
