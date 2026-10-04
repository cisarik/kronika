"""Byte-cap boundaries and budget-profile accept/reject results."""

from __future__ import annotations

import pytest

from kronika.infrastructure.ai.chatgpt_page.archive import zip_overhead_for_count
from kronika.infrastructure.ai.chatgpt_page.budget import (
    LOCAL_ATTACHMENT_CEILING_BYTES,
    VIDEO_MINIMUM_FRAME_COUNT,
    byte_cap,
    deployment_budget_path,
    nbytes,
    operating_count,
    qualifies_for_video,
    validate_budget_profile,
)
from kronika.infrastructure.ai.chatgpt_page.envelope import PER_FRAME_ACCOUNTING_BYTES
from kronika.infrastructure.ai.chatgpt_page.errors import BudgetProfileRejected

PACK = "ab" * 32
SESSION = "page-session-a"


def _document(**overrides: object) -> dict:
    lzip = LOCAL_ATTACHMENT_CEILING_BYTES
    ltotal = LOCAL_ATTACHMENT_CEILING_BYTES
    semantic_r = 20
    document = {
        "schema_version": 1,
        "measured_at": "2026-09-22T18:00:00+00:00",
        "envelope_id": "480q60",
        "lzip": lzip,
        "ltotal": ltotal,
        "lzip_certified_lower_bound": False,
        "ltotal_certified_lower_bound": True,
        "r": semantic_r,
        "n": operating_count(lzip, ltotal, semantic_r),
        "pack_identity": PACK,
        "page_session_identity": SESSION,
        "semantic_success_count": 2,
        "attachment_limit_failed": False,
    }
    document.update(overrides)
    return document


def test_byte_cap_applies_the_eighty_percent_factor_and_ceiling() -> None:
    assert byte_cap(1000, 5000) == 800
    assert byte_cap(5000, 1001) == 800
    assert byte_cap(1001, 1001) == 800
    ceiling = LOCAL_ATTACHMENT_CEILING_BYTES
    assert byte_cap(ceiling * 4, ceiling * 4) == int(0.8 * ceiling)


def test_nbytes_is_the_largest_count_that_fits_and_excludes_the_next() -> None:
    lzip = ltotal = LOCAL_ATTACHMENT_CEILING_BYTES
    count = nbytes(lzip, ltotal)
    cap = byte_cap(lzip, ltotal)

    assert count * PER_FRAME_ACCOUNTING_BYTES + zip_overhead_for_count(count) <= cap
    assert (count + 1) * PER_FRAME_ACCOUNTING_BYTES + zip_overhead_for_count(count + 1) > cap
    assert nbytes(1000, 1000) == 0


def test_operating_count_uses_floor_of_eighty_percent_of_r() -> None:
    lzip = ltotal = LOCAL_ATTACHMENT_CEILING_BYTES

    assert operating_count(lzip, ltotal, 15) == 12
    assert qualifies_for_video(operating_count(lzip, ltotal, 15))
    assert operating_count(lzip, ltotal, 14) == 11
    assert not qualifies_for_video(11)
    assert VIDEO_MINIMUM_FRAME_COUNT == 12


def test_consistent_profile_is_accepted() -> None:
    profile = validate_budget_profile(
        _document(),
        expected_pack_identity=PACK,
        expected_page_session_identity=SESSION,
    )

    assert profile.n >= 12
    assert profile.envelope_id == "480q60"
    assert deployment_budget_path() == "/var/lib/framenest/chatgpt-page/budget.json"


@pytest.mark.parametrize(
    ("overrides", "reason"),
    [
        ({"measured_at": None}, "missing"),
        ({"schema_version": 2}, "malformed"),
        ({"n": 1}, "inconsistent"),
        ({"semantic_success_count": 0}, "inconsistent"),
        ({}, "stale_pack"),
        ({}, "stale_session"),
        ({"attachment_limit_failed": True}, "attachment_limit_failure"),
        ({"r": 14, "n": 11}, "below_video_minimum"),
    ],
)
def test_profile_rejections(overrides: dict, reason: str) -> None:
    document = _document(**overrides)
    pack = "cd" * 32 if reason == "stale_pack" else PACK
    session = "other-session" if reason == "stale_session" else SESSION
    if "measured_at" in overrides and overrides["measured_at"] is None:
        del document["measured_at"]
    with pytest.raises(BudgetProfileRejected) as caught:
        validate_budget_profile(
            document,
            expected_pack_identity=pack,
            expected_page_session_identity=session,
        )
    assert caught.value.reason == reason
