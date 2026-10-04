"""Focused tests for bounded HTTPS response metadata."""

from __future__ import annotations

from email.message import Message

from kronika.infrastructure.ai.transport import _response_content_type


class _Response:
    def __init__(self, content_type: str) -> None:
        headers = Message()
        headers["Content-Type"] = content_type
        self.headers = headers


def test_response_content_type_keeps_only_normalized_media_type() -> None:
    response = _Response("Application/JSON; charset=utf-8")

    assert _response_content_type(response) == "application/json"


def test_response_content_type_rejects_unbounded_value() -> None:
    response = _Response("application/" + ("x" * 120))

    assert _response_content_type(response) is None
