"""Sanitized probe receipts. Forbidden content prevents any serialization."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from kronika.infrastructure.ai.chatgpt_page.errors import ReceiptRejected

RECEIPT_SCHEMA_VERSION = 1
TOOLING_VERSION = "chatgpt-page-probe-v1"
_FIXTURE_IDENTITY = re.compile(r"^fx-[0-9a-f]{8}-[a-z0-9-]+-[0-9a-f]{16}$")
_PACK_HASH = re.compile(r"^[0-9a-f]{64}$")
_MIME = re.compile(r"^(image/jpeg|image/png|image/gif|application/zip)$")
_ERROR_CATEGORIES = frozenset({"rejected", "timeout", "unavailable", "limit"})
_FORBIDDEN_SNIPPETS = (
    "chatgpt.com",
    "cookie",
    "authorization",
    "bearer ",
    "password",
    "token=",
    "sk-",
    "/home/",
    "/users/",
    "/var/",
    "/opt/",
    "/srv/",
    "<html",
    "<!doctype",
    "data:image",
    "devtools",
    "runtime.evaluate",
    "begin private",
)


@dataclass(frozen=True, slots=True)
class ProbeReceipt:
    """One sanitized probe receipt. Free-form text is not a field."""

    pack_hash: str
    tested_mime_types: tuple[str, ...]
    trial_count: int
    accepted_count: int
    byte_sizes: tuple[int, ...]
    timings_ms: tuple[int, ...]
    error_categories: tuple[str, ...]
    fixture_identities: tuple[str, ...]
    semantic_pass_count: int
    lzip: int | None
    ltotal: int | None
    r: int | None


def serialize_receipt(receipt: ProbeReceipt) -> str:
    """Return canonical JSON, or raise without producing a document."""

    if not isinstance(receipt, ProbeReceipt):
        raise ReceiptRejected("malformed")
    document = _document(receipt)
    _reject_forbidden(document)
    encoded = json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    _reject_forbidden(encoded)
    return encoded


def _document(receipt: ProbeReceipt) -> dict:
    if not isinstance(receipt.pack_hash, str) or _PACK_HASH.fullmatch(receipt.pack_hash) is None:
        raise ReceiptRejected("malformed")
    if not isinstance(receipt.tested_mime_types, tuple) or not receipt.tested_mime_types:
        raise ReceiptRejected("malformed")
    for mime in receipt.tested_mime_types:
        if not isinstance(mime, str) or _MIME.fullmatch(mime) is None:
            raise ReceiptRejected("malformed")
    for identity in receipt.fixture_identities:
        if not isinstance(identity, str) or _FIXTURE_IDENTITY.fullmatch(identity) is None:
            raise ReceiptRejected("forbidden")
    for category in receipt.error_categories:
        if category not in _ERROR_CATEGORIES:
            raise ReceiptRejected("malformed")
    _count("trial_count", receipt.trial_count)
    _count("accepted_count", receipt.accepted_count)
    _count("semantic_pass_count", receipt.semantic_pass_count)
    if receipt.accepted_count > receipt.trial_count:
        raise ReceiptRejected("malformed")
    for size in receipt.byte_sizes:
        _count("byte_size", size)
    for timing in receipt.timings_ms:
        _count("timing_ms", timing)
    for bound_name, bound in (("lzip", receipt.lzip), ("ltotal", receipt.ltotal), ("r", receipt.r)):
        if bound is not None:
            _count(bound_name, bound)
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "tooling_version": TOOLING_VERSION,
        "pack_hash": receipt.pack_hash,
        "tested_mime_types": list(receipt.tested_mime_types),
        "trial_count": receipt.trial_count,
        "accepted_count": receipt.accepted_count,
        "byte_sizes": list(receipt.byte_sizes),
        "timings_ms": list(receipt.timings_ms),
        "error_categories": list(receipt.error_categories),
        "fixture_identities": list(receipt.fixture_identities),
        "semantic_pass_count": receipt.semantic_pass_count,
        "bounds": {"lzip": receipt.lzip, "ltotal": receipt.ltotal, "r": receipt.r},
    }


def _count(label: str, value: object) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ReceiptRejected("malformed")


def _reject_forbidden(value: object) -> None:
    if isinstance(value, str):
        lowered = value.lower()
        if any(snippet in lowered for snippet in _FORBIDDEN_SNIPPETS):
            raise ReceiptRejected("forbidden")
        if re.search(r"(?:^|[\s\"'])/(?:home|users|var|opt|srv|tmp)/", lowered):
            raise ReceiptRejected("forbidden")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            _reject_forbidden(key)
            _reject_forbidden(item)
        return
    if isinstance(value, (list, tuple)):
        for item in value:
            _reject_forbidden(item)
