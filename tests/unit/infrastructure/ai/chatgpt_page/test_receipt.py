"""Receipt sanitization refuses planted canaries and emits no partial document."""

from __future__ import annotations

import pytest

from kronika.infrastructure.ai.chatgpt_page.errors import ReceiptRejected
from kronika.infrastructure.ai.chatgpt_page.receipt import ProbeReceipt, serialize_receipt

PACK = "ab" * 32
CANARIES = (
    "cookie=session",
    "Authorization: Bearer secret",
    "password=hunter2",
    "token=abc",
    "sk-live-secret",
    "https://chatgpt.com/c/private",
    "/home/agile/media/private.mp4",
    "<html>account dump</html>",
    "data:image/png;base64,AAAA",
    "Runtime.evaluate failed",
)


def _receipt() -> ProbeReceipt:
    return ProbeReceipt(
        pack_hash=PACK,
        tested_mime_types=("image/jpeg", "application/zip"),
        trial_count=2,
        accepted_count=1,
        byte_sizes=(1200, 3400),
        timings_ms=(15, 40),
        error_categories=("limit",),
        fixture_identities=(
            "fx-0000000b-still-jpeg-0123456789abcdef",
            "fx-0000000b-zip-frames-fedcba9876543210",
        ),
        semantic_pass_count=1,
        lzip=100000,
        ltotal=200000,
        r=16,
    )


def test_clean_receipt_is_deterministic_and_has_no_canaries() -> None:
    encoded = serialize_receipt(_receipt())

    assert encoded == serialize_receipt(_receipt())
    assert encoded.startswith('{"accepted_count":')
    lowered = encoded.lower()
    for canary in CANARIES:
        assert canary.lower() not in lowered


@pytest.mark.parametrize("canary", CANARIES)
def test_planted_canary_prevents_serialization(canary: str, tmp_path) -> None:
    receipt = ProbeReceipt(
        pack_hash=PACK,
        tested_mime_types=("image/jpeg",),
        trial_count=1,
        accepted_count=0,
        byte_sizes=(10,),
        timings_ms=(1,),
        error_categories=("rejected",),
        fixture_identities=(canary,),
        semantic_pass_count=0,
        lzip=None,
        ltotal=None,
        r=None,
    )
    target = tmp_path / "receipt.json"
    with pytest.raises(ReceiptRejected):
        target.write_text(serialize_receipt(receipt), encoding="ascii")
    assert not target.exists()
