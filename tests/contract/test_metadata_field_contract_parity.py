"""Cross-language metadata field contract parity: fixture against real server validation.

The shared acceptance fixture ``tests/support/metadata_field_contract_cases.json``
is executed by this test against FrameNest's real request/domain validation and by
``tests/metadata_form_contract.test.js`` against the real client validators. This
test is the authority check: if the fixture disagrees with actual server behavior,
it fails.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError
import pytest

from kronika.adapters.api.media_metadata_api import (
    CanonicalTagRequest,
    MediaMetadataSaveRequest,
)
from kronika.application.media_metadata import _parse_genres

FIXTURE_PATH = (
    Path(__file__).resolve().parents[1]
    / "support"
    / "metadata_field_contract_cases.json"
)


def _load_cases() -> list[dict[str, Any]]:
    payload = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(payload["cases"])


def _expand(value: Any) -> Any:
    """Expand the fixture ``{"repeat": [unit, count]}`` value form."""
    if isinstance(value, dict) and set(value) == {"repeat"}:
        unit, count = value["repeat"]
        return unit * count
    return value


def _server_accepts_title(raw_title: str) -> bool:
    # Empty and whitespace-only titles clear the field; the client submits null.
    value = raw_title if raw_title.strip() else None
    try:
        MediaMetadataSaveRequest(display_title=value, description=None, tag_keys=[])
    except ValidationError:
        return False
    return True


def _server_accepts_description(raw_description: str) -> bool:
    # Empty and whitespace-only descriptions clear the field; the client submits null.
    value = raw_description if raw_description.strip() else None
    try:
        MediaMetadataSaveRequest(display_title=None, description=value, tag_keys=[])
    except ValidationError:
        return False
    return True


def _server_accepts_tag_display_name(raw_display_name: str) -> bool:
    try:
        CanonicalTagRequest(key="fixture-tag", display_name=raw_display_name)
    except ValidationError:
        return False
    return True


def _server_accepts_genre_set(genres: list[str]) -> bool:
    try:
        MediaMetadataSaveRequest(
            display_title=None,
            description=None,
            tag_keys=[],
            content_category="movie",
            genres=list(genres),
        )
    except ValidationError:
        return False
    try:
        # The same production mapping SaveMediaMetadata.execute applies.
        _parse_genres(list(genres))
    except ValueError:
        return False
    return True


_SERVER_EXECUTORS = {
    "title": _server_accepts_title,
    "description": _server_accepts_description,
    "tag_display_name": _server_accepts_tag_display_name,
    "genre_set": _server_accepts_genre_set,
}


_CASES = _load_cases()


@pytest.mark.parametrize("case", _CASES, ids=[case["id"] for case in _CASES])
def test_fixture_matches_server_metadata_field_contract(case: dict[str, Any]) -> None:
    field = case["field"]
    assert field in _SERVER_EXECUTORS, f"unknown fixture field {field!r}"
    value = _expand(case["value"])
    accepted = _SERVER_EXECUTORS[field](value)
    assert accepted == (case["expected"] == "accepted"), (
        f"fixture case {case['id']} ({field}) expected {case['expected']} "
        f"but the server contract returned {'accepted' if accepted else 'rejected'}"
    )
