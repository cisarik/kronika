"""Contract tests for the canonical-only mutation-header gate.

Only ``X-Kronika-Request: 1`` authorizes. Retired-only requests are refused,
and unrelated headers cannot replace or override the canonical value.
"""

from __future__ import annotations

from pathlib import Path
import uuid

import pytest
from fastapi.testclient import TestClient

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import (
    ERROR_MUTATION_HEADER_REQUIRED,
    EXPECTED_MUTATION_HEADER_VALUE,
    HEADER_MUTATION_KRONIKA,
    MUTATION_HEADERS,
    _mutation_header_authorized,
)
from kronika.configuration import KronikaSettings
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

EXTERNAL_ORIGIN = "https://nuc-1.example.ts.net"
EXTERNAL_HOST = "nuc-1.example.ts.net"
ADMIN_LOGIN = "admin@example.com"

RETIRED_HEADER = b"x-framenest-request"

CANONICAL_TAG_ENDPOINT = "/api/canonical-tags"


def _error_code(response) -> str:
    return response.json()["error"]["code"]


def _serve_headers() -> dict[str, str]:
    return {
        "Tailscale-User-Login": ADMIN_LOGIN,
        "Tailscale-User-Name": "Admin User",
        "X-Forwarded-Proto": "https",
        "X-Forwarded-Host": EXTERNAL_HOST,
        "Origin": EXTERNAL_ORIGIN,
    }


def _mutation_headers(**overrides: str) -> dict[str, str]:
    headers = {**_serve_headers(), "X-Kronika-Request": "1"}
    headers.update(overrides)
    return headers


def _fresh_tag_key() -> str:
    return f"canonical-read-{uuid.uuid4().hex}"


@pytest.fixture
def tailscale_client(tmp_path: Path):
    settings = KronikaSettings(
        database_path=tmp_path / "catalog.sqlite3",
        gallery_preview_cache_path=tmp_path / "previews",
        ingress_mode="tailscale_uds",
        uds_path=tmp_path / "framenest.sock",
        external_origin=EXTERNAL_ORIGIN,
        identity_map={ADMIN_LOGIN: "admin"},
        _env_file=None,
    )
    upgrade_database_to_head(settings)
    app = create_app(settings=settings)
    with TestClient(app) as client:
        yield client, settings


# ---------------------------------------------------------------------------
# Pure gate behaviour
# ---------------------------------------------------------------------------


def test_only_the_canonical_spelling_is_accepted() -> None:
    assert MUTATION_HEADERS == (b"x-kronika-request",)
    assert HEADER_MUTATION_KRONIKA == b"x-kronika-request"


def test_expected_value_expectation_is_unchanged() -> None:
    assert EXPECTED_MUTATION_HEADER_VALUE == b"1"


def _header_map(headers: dict[bytes, bytes]) -> dict[bytes, list[bytes]]:
    return {name: [value] for name, value in headers.items()}


def test_absent_header_does_not_authorize() -> None:
    assert _mutation_header_authorized({}) is False


def test_canonical_authorizes_and_retired_is_refused() -> None:
    assert _mutation_header_authorized(_header_map({RETIRED_HEADER: b"1"})) is False
    assert _mutation_header_authorized(_header_map({HEADER_MUTATION_KRONIKA: b"1"})) is True


def test_wrong_value_on_the_canonical_spelling_is_rejected() -> None:
    for value in (b"0", b"true", b"", b"11", b"1 "):
        assert (
            _mutation_header_authorized(_header_map({HEADER_MUTATION_KRONIKA: value})) is False
        )


def test_wrong_value_on_the_retired_spelling_is_rejected() -> None:
    for value in (b"0", b"true", b"", b"11", b"1 "):
        assert _mutation_header_authorized(_header_map({RETIRED_HEADER: value})) is False


def test_only_the_canonical_value_controls_when_both_are_present() -> None:
    assert (
        _mutation_header_authorized(
            _header_map({RETIRED_HEADER: b"1", HEADER_MUTATION_KRONIKA: b"0"})
        )
        is False
    )
    assert (
        _mutation_header_authorized(
            _header_map({RETIRED_HEADER: b"0", HEADER_MUTATION_KRONIKA: b"1"})
        )
        is True
    )


def test_both_present_and_correct_authorizes() -> None:
    assert (
        _mutation_header_authorized(
            _header_map({RETIRED_HEADER: b"1", HEADER_MUTATION_KRONIKA: b"1"})
        )
        is True
    )


# ---------------------------------------------------------------------------
# End-to-end through the ingress
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header_name", "expected_status"),
    [("X-FrameNest-Request", 403), ("X-Kronika-Request", 201)],
)
def test_canonical_and_retired_header_refusal_matrix(
    tailscale_client,
    header_name: str,
    expected_status: int,
) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers[header_name] = "1"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == expected_status, response.text
    if expected_status == 403:
        assert _error_code(response) == ERROR_MUTATION_HEADER_REQUIRED
        assert response.json()["error"]["message"] == (
            "The X-Kronika-Request mutation header is required and must be exactly 1."
        )


def test_both_spellings_together_authorize_a_real_mutation(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-FrameNest-Request"] = "1"
    headers["X-Kronika-Request"] = "1"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == 201, response.text


def test_canonical_spelling_with_a_wrong_value_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-Kronika-Request"] = "0"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == 403
    assert _error_code(response) == ERROR_MUTATION_HEADER_REQUIRED


def test_retired_spelling_with_a_wrong_value_is_still_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-FrameNest-Request"] = "yes"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == 403
    assert _error_code(response) == ERROR_MUTATION_HEADER_REQUIRED


def test_both_spellings_with_one_wrong_value_is_rejected(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-FrameNest-Request"] = "1"
    headers["X-Kronika-Request"] = "0"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == 403
    assert _error_code(response) == ERROR_MUTATION_HEADER_REQUIRED


def test_rejection_response_carries_no_header_derived_value(tailscale_client) -> None:
    client, _ = tailscale_client
    headers = _serve_headers()
    headers["X-Kronika-Request"] = "0"

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers=headers,
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    body = response.text
    assert "0" not in response.json()["error"]["message"]
    assert "Kronika" in response.json()["error"]["message"]
    assert response.status_code == 403


def test_canonical_spelling_is_not_an_origin_gate(tailscale_client) -> None:
    """The canonical spelling must not appear in any origin or CORS acceptance path."""
    client, settings = tailscale_client

    response = client.post(
        CANONICAL_TAG_ENDPOINT,
        headers={
            **_serve_headers(),
            "Origin": "https://evil.example",
            "X-Kronika-Request": "1",
        },
        json={"key": _fresh_tag_key(), "display_name": "Canonical Read"},
    )

    assert response.status_code == 403
    assert _error_code(response) == "MUTATION_ORIGIN_FORBIDDEN"
    assert HEADER_MUTATION_KRONIKA not in settings.companion_extension_origins
