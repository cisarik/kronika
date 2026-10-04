"""Byte accounting and the nonsecret chatgpt-page budget profile."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from kronika.infrastructure.ai.chatgpt_page.archive import zip_overhead_for_count
from kronika.infrastructure.ai.chatgpt_page.envelope import ENVELOPE_IDS, PER_FRAME_ACCOUNTING_BYTES
from kronika.infrastructure.ai.chatgpt_page.errors import BoundedPreparationError, BudgetProfileRejected

SCHEMA_VERSION = 1
LOCAL_ATTACHMENT_CEILING_BYTES = 32 * 1024 * 1024
VIDEO_MINIMUM_FRAME_COUNT = 12
DEPLOYMENT_STATE_DIR = "/var/lib/framenest/chatgpt-page"
DEPLOYMENT_BUDGET_FILENAME = "budget.json"
_PACK_IDENTITY = re.compile(r"^[0-9a-f]{64}$")
_SESSION_IDENTITY = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_REQUIRED = (
    "schema_version",
    "measured_at",
    "envelope_id",
    "lzip",
    "ltotal",
    "lzip_certified_lower_bound",
    "ltotal_certified_lower_bound",
    "r",
    "n",
    "pack_identity",
    "page_session_identity",
    "semantic_success_count",
    "attachment_limit_failed",
)


@dataclass(frozen=True, slots=True)
class BudgetProfile:
    """One accepted nonsecret video budget."""

    schema_version: int
    measured_at: str
    envelope_id: str
    lzip: int
    ltotal: int
    lzip_certified_lower_bound: bool
    ltotal_certified_lower_bound: bool
    r: int
    n: int
    pack_identity: str
    page_session_identity: str
    semantic_success_count: int
    attachment_limit_failed: bool


def deployment_budget_path() -> str:
    """NUC state path. S2 does not create or write this file."""

    return f"{DEPLOYMENT_STATE_DIR}/{DEPLOYMENT_BUDGET_FILENAME}"


def budget_path(state_dir: Path | str) -> Path:
    """Return the budget file path inside an explicit state directory."""

    return Path(state_dir) / DEPLOYMENT_BUDGET_FILENAME


def byte_cap(lzip: int, ltotal: int) -> int:
    """``B = floor(0.8 × min(Lzip, Ltotal, 32 MiB))``."""

    _positive(lzip, "lzip")
    _positive(ltotal, "ltotal")
    return math.floor(0.8 * min(lzip, ltotal, LOCAL_ATTACHMENT_CEILING_BYTES))


def nbytes(lzip: int, ltotal: int, *, per_frame_bytes: int = PER_FRAME_ACCOUNTING_BYTES) -> int:
    """Largest count whose worst-case payloads and exact ZIP overhead fit ``B``."""

    if (
        not isinstance(per_frame_bytes, int)
        or isinstance(per_frame_bytes, bool)
        or per_frame_bytes < 1
    ):
        raise BoundedPreparationError("per-frame bound must be a positive integer")
    cap = byte_cap(lzip, ltotal)
    highest = 0
    count = 0
    while True:
        nxt = count + 1
        try:
            overhead = zip_overhead_for_count(nxt)
        except BoundedPreparationError:
            break
        total = nxt * per_frame_bytes + overhead
        if total > cap:
            break
        highest = nxt
        count = nxt
    return highest


def operating_count(lzip: int, ltotal: int, r: int) -> int:
    """``N = min(Nbytes, floor(0.8 × R))``."""

    if not isinstance(r, int) or isinstance(r, bool) or r < 1:
        raise BoundedPreparationError("R must be a positive integer")
    return min(nbytes(lzip, ltotal), math.floor(0.8 * r))


def qualifies_for_video(frame_count: int) -> bool:
    return isinstance(frame_count, int) and not isinstance(frame_count, bool) and frame_count >= VIDEO_MINIMUM_FRAME_COUNT


def validate_budget_profile(
    document: object,
    *,
    expected_pack_identity: str,
    expected_page_session_identity: str,
) -> BudgetProfile:
    """Accept one consistent video profile or raise a typed rejection."""

    if not isinstance(document, dict):
        raise BudgetProfileRejected("malformed")
    if any(key not in document for key in _REQUIRED):
        raise BudgetProfileRejected("missing")
    if any(key not in _REQUIRED for key in document):
        raise BudgetProfileRejected("malformed")
    try:
        profile = _parse(document)
    except BudgetProfileRejected:
        raise
    except (TypeError, ValueError) as exc:
        raise BudgetProfileRejected("malformed") from exc
    expected_n = operating_count(profile.lzip, profile.ltotal, profile.r)
    if profile.n != expected_n or profile.envelope_id not in ENVELOPE_IDS:
        raise BudgetProfileRejected("inconsistent")
    if profile.semantic_success_count < 1:
        raise BudgetProfileRejected("inconsistent")
    if profile.pack_identity != expected_pack_identity:
        raise BudgetProfileRejected("stale_pack")
    if profile.page_session_identity != expected_page_session_identity:
        raise BudgetProfileRejected("stale_session")
    if profile.attachment_limit_failed:
        raise BudgetProfileRejected("attachment_limit_failure")
    if not qualifies_for_video(profile.n):
        raise BudgetProfileRejected("below_video_minimum")
    return profile


def _parse(document: dict) -> BudgetProfile:
    measured_at = document["measured_at"]
    if not isinstance(measured_at, str):
        raise BudgetProfileRejected("malformed")
    try:
        datetime.fromisoformat(measured_at)
    except ValueError as exc:
        raise BudgetProfileRejected("malformed") from exc
    pack_identity = document["pack_identity"]
    session_identity = document["page_session_identity"]
    if not isinstance(pack_identity, str) or _PACK_IDENTITY.fullmatch(pack_identity) is None:
        raise BudgetProfileRejected("malformed")
    if not isinstance(session_identity, str) or _SESSION_IDENTITY.fullmatch(session_identity) is None:
        raise BudgetProfileRejected("malformed")
    envelope_id = document["envelope_id"]
    if not isinstance(envelope_id, str) or envelope_id not in ENVELOPE_IDS:
        raise BudgetProfileRejected("malformed")
    numbers = ("schema_version", "lzip", "ltotal", "r", "n", "semantic_success_count")
    for key in numbers:
        value = document[key]
        if not isinstance(value, int) or isinstance(value, bool):
            raise BudgetProfileRejected("malformed")
    for key in ("lzip_certified_lower_bound", "ltotal_certified_lower_bound", "attachment_limit_failed"):
        if not isinstance(document[key], bool):
            raise BudgetProfileRejected("malformed")
    if document["schema_version"] != SCHEMA_VERSION:
        raise BudgetProfileRejected("malformed")
    if document["lzip"] < 1 or document["ltotal"] < 1 or document["r"] < 1 or document["n"] < 0:
        raise BudgetProfileRejected("malformed")
    if document["semantic_success_count"] < 0:
        raise BudgetProfileRejected("malformed")
    return BudgetProfile(
        schema_version=document["schema_version"],
        measured_at=measured_at,
        envelope_id=envelope_id,
        lzip=document["lzip"],
        ltotal=document["ltotal"],
        lzip_certified_lower_bound=document["lzip_certified_lower_bound"],
        ltotal_certified_lower_bound=document["ltotal_certified_lower_bound"],
        r=document["r"],
        n=document["n"],
        pack_identity=pack_identity,
        page_session_identity=session_identity,
        semantic_success_count=document["semantic_success_count"],
        attachment_limit_failed=document["attachment_limit_failed"],
    )


def _positive(value: int, label: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise BoundedPreparationError(f"{label} must be a positive integer")
