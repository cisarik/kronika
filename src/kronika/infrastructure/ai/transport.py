"""Bounded HTTPS JSON transport for AI provider adapters."""

from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Mapping

TRANSPORT_AUTH_REJECTED_MESSAGE = "Provider authentication was rejected."
TRANSPORT_RATE_LIMITED_MESSAGE = "Provider rate limit was reached."
TRANSPORT_MODEL_UNAVAILABLE_MESSAGE = "Provider model is not available."
TRANSPORT_UNAVAILABLE_MESSAGE = "Provider is not available."
TRANSPORT_INVALID_RESPONSE_MESSAGE = "Provider response was invalid."
TRANSPORT_FAILED_MESSAGE = "Provider request failed."
TRANSPORT_RESPONSE_TOO_LARGE_MESSAGE = "Provider response exceeded the allowed limit."
TRANSPORT_REQUEST_TOO_LARGE_MESSAGE = "Provider request exceeded the allowed limit."
TRANSPORT_REDIRECT_REJECTED_MESSAGE = "Provider redirect was rejected."

_READ_CHUNK_SIZE = 65_536


class HttpsTransportError(RuntimeError):
    """Sanitized transport failure."""


@dataclass(frozen=True, slots=True)
class HttpsJsonResponse:
    """Bounded HTTPS JSON response metadata."""

    status_code: int
    body: bytes
    content_type: str | None = None


class HttpsJsonTransport:
    """Standard-library HTTPS JSON transport with bounded response reads."""

    def __init__(self, *, timeout_seconds: float, max_response_bytes: int) -> None:
        self._timeout_seconds = timeout_seconds
        self._max_response_bytes = max_response_bytes

    def post_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
        body: bytes,
        max_request_bytes: int,
    ) -> HttpsJsonResponse:
        if len(body) > max_request_bytes:
            raise HttpsTransportError(TRANSPORT_REQUEST_TOO_LARGE_MESSAGE)
        request = urllib.request.Request(
            url,
            data=body,
            headers=dict(headers),
            method="POST",
        )
        return self._execute(request)

    def get_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> HttpsJsonResponse:
        request = urllib.request.Request(
            url,
            headers=dict(headers),
            method="GET",
        )
        return self._execute(request)

    def delete_json(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> HttpsJsonResponse:
        request = urllib.request.Request(
            url,
            headers=dict(headers),
            method="DELETE",
        )
        return self._execute(request)

    def _execute(self, request: urllib.request.Request) -> HttpsJsonResponse:
        try:
            with urllib.request.urlopen(
                request,
                timeout=self._timeout_seconds,
                context=ssl.create_default_context(),
            ) as response:
                if response.status in {301, 302, 303, 307, 308}:
                    raise HttpsTransportError(TRANSPORT_REDIRECT_REJECTED_MESSAGE)
                body_bytes = self._read_bounded(response)
                return HttpsJsonResponse(
                    status_code=response.status,
                    body=body_bytes,
                    content_type=_response_content_type(response),
                )
        except HttpsTransportError:
            raise
        except urllib.error.HTTPError as exc:
            body_bytes = self._read_bounded(exc)
            status = exc.code
            if status in {401, 403}:
                raise HttpsTransportError(TRANSPORT_AUTH_REJECTED_MESSAGE) from None
            if status == 429:
                raise HttpsTransportError(TRANSPORT_RATE_LIMITED_MESSAGE) from None
            if status == 404:
                raise HttpsTransportError(TRANSPORT_MODEL_UNAVAILABLE_MESSAGE) from None
            if status >= 500:
                raise HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE) from None
            raise HttpsTransportError(TRANSPORT_INVALID_RESPONSE_MESSAGE) from None
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                raise HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE) from None
            raise HttpsTransportError(TRANSPORT_UNAVAILABLE_MESSAGE) from None
        except Exception:
            raise HttpsTransportError(TRANSPORT_FAILED_MESSAGE) from None

    def _read_bounded(self, response: object) -> bytes:
        read = getattr(response, "read", None)
        if read is None:
            return b""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = read(_READ_CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > self._max_response_bytes:
                raise HttpsTransportError(TRANSPORT_RESPONSE_TOO_LARGE_MESSAGE)
            chunks.append(chunk)
        return b"".join(chunks)

    def __repr__(self) -> str:
        return "HttpsJsonTransport(<redacted>)"


def _response_content_type(response: object) -> str | None:
    """Return only a bounded normalized media type from response headers."""
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    get_content_type = getattr(headers, "get_content_type", None)
    if callable(get_content_type):
        value = get_content_type()
    else:
        get = getattr(headers, "get", None)
        value = get("Content-Type") if callable(get) else None
        if isinstance(value, str):
            value = value.split(";", maxsplit=1)[0].strip()
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 100:
        return None
    if not all(character.isalnum() or character in "!#$&^_.+-/" for character in normalized):
        return None
    return normalized
