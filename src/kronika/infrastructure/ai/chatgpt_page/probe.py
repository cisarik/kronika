"""Cancellable offline probe harness. S2 runs it against an injected transport only."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from kronika.infrastructure.ai.chatgpt_page.budget import BudgetProfile
from kronika.infrastructure.ai.chatgpt_page.errors import ProbeHarnessError, ReceiptRejected
from kronika.infrastructure.ai.chatgpt_page.receipt import ProbeReceipt, serialize_receipt

_OWNED_DIR_NAME = "chatgpt-page-probe-owned"
_ERROR_CATEGORIES = frozenset({"rejected", "timeout", "unavailable", "limit"})


class CancellationToken:
    """Cooperative cancel flag checked by the harness, not by a live browser."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def is_cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True, slots=True)
class TrialSpec:
    """One offline trial. The payload stays in memory."""

    trial_id: str
    mime_type: str
    fixture_identity: str
    payload: bytes


@dataclass(frozen=True, slots=True)
class TrialObservation:
    """Numeric outcome from an injected transport. Free-form text is ignored."""

    accepted: bool
    byte_size: int
    elapsed_ms: int
    error_category: str | None = None


class ProbeTransport(Protocol):
    """Injected trial executor. S2 supplies a fake."""

    def execute(self, trial: TrialSpec) -> TrialObservation:
        """Run one trial and return a numeric observation."""


@dataclass(frozen=True, slots=True)
class ProbeOutcome:
    """Terminal harness result. A cancelled run has no output paths."""

    status: str
    summaries: tuple[dict[str, object], ...]
    receipt_path: Path | None
    profile_path: Path | None


def run_probe(
    trials: Sequence[TrialSpec],
    transport: ProbeTransport,
    cancel: CancellationToken,
    work_dir: Path | str,
    *,
    pack_hash: str,
    profile: BudgetProfile | None = None,
    on_trial_recorded: Callable[[], None] | None = None,
) -> ProbeOutcome:
    """Run trials one at a time. Cancellation skips the next trial and any write.

    The harness creates and later removes only ``work_dir / chatgpt-page-probe-owned``.
    A cancelled run writes neither a receipt nor a budget profile.
    """

    if not isinstance(cancel, CancellationToken):
        raise ProbeHarnessError("cancel token is required")
    if not isinstance(trials, Sequence) or isinstance(trials, (bytes, str)):
        raise ProbeHarnessError("trials must be a sequence")
    root = Path(work_dir)
    owned = root / _OWNED_DIR_NAME
    scratch = owned / "scratch"
    owned.mkdir(parents=False, exist_ok=False)
    scratch.mkdir()
    summaries: list[dict[str, object]] = []
    try:
        for trial in trials:
            if cancel.is_cancelled():
                return _cancelled(owned, summaries)
            observation = transport.execute(trial)
            if cancel.is_cancelled():
                return _cancelled(owned, summaries)
            summaries.append(_summary(trial, observation))
            if on_trial_recorded is not None:
                on_trial_recorded()
            if cancel.is_cancelled():
                return _cancelled(owned, summaries)
        if cancel.is_cancelled():
            return _cancelled(owned, summaries)
        receipt = _receipt(summaries, pack_hash)
        encoded = serialize_receipt(receipt)
        receipt_path = owned / "receipt.json"
        if cancel.is_cancelled():
            return _cancelled(owned, summaries)
        _write_bytes(receipt_path, encoded.encode("ascii"))
        profile_path = None
        if profile is not None:
            if cancel.is_cancelled():
                receipt_path.unlink(missing_ok=True)
                return _cancelled(owned, summaries)
            profile_path = owned / "budget.json"
            _write_bytes(profile_path, _profile_bytes(profile))
        return ProbeOutcome(
            status="completed",
            summaries=tuple(summaries),
            receipt_path=receipt_path,
            profile_path=profile_path,
        )
    finally:
        if scratch.exists() and not scratch.is_symlink():
            for child in list(scratch.iterdir()):
                if child.is_file() and not child.is_symlink():
                    child.unlink()
            scratch.rmdir()


def _cancelled(owned: Path, summaries: list[dict[str, object]]) -> ProbeOutcome:
    for name in ("receipt.json", "budget.json"):
        path = owned / name
        if path.is_file() and not path.is_symlink():
            path.unlink()
    return ProbeOutcome(
        status="cancelled",
        summaries=tuple(summaries),
        receipt_path=None,
        profile_path=None,
    )


def _summary(trial: TrialSpec, observation: TrialObservation) -> dict[str, object]:
    if not isinstance(trial, TrialSpec) or not isinstance(observation, TrialObservation):
        raise ProbeHarnessError("trial observation is malformed")
    if not isinstance(observation.accepted, bool):
        raise ProbeHarnessError("trial observation is malformed")
    if (
        not isinstance(observation.byte_size, int)
        or isinstance(observation.byte_size, bool)
        or observation.byte_size < 0
        or not isinstance(observation.elapsed_ms, int)
        or isinstance(observation.elapsed_ms, bool)
        or observation.elapsed_ms < 0
    ):
        raise ProbeHarnessError("trial observation is malformed")
    category = observation.error_category
    if observation.accepted:
        category = None
    elif category not in _ERROR_CATEGORIES:
        category = "unavailable"
    return {
        "fixture_identity": trial.fixture_identity,
        "mime_type": trial.mime_type,
        "accepted": observation.accepted,
        "byte_size": observation.byte_size,
        "elapsed_ms": observation.elapsed_ms,
        "error_category": category,
    }


def _receipt(summaries: list[dict[str, object]], pack_hash: str) -> ProbeReceipt:
    try:
        return ProbeReceipt(
            pack_hash=pack_hash,
            tested_mime_types=tuple(str(item["mime_type"]) for item in summaries),
            trial_count=len(summaries),
            accepted_count=sum(1 for item in summaries if item["accepted"] is True),
            byte_sizes=tuple(int(item["byte_size"]) for item in summaries),
            timings_ms=tuple(int(item["elapsed_ms"]) for item in summaries),
            error_categories=tuple(
                str(item["error_category"])
                for item in summaries
                if isinstance(item["error_category"], str)
            ),
            fixture_identities=tuple(str(item["fixture_identity"]) for item in summaries),
            semantic_pass_count=sum(1 for item in summaries if item["accepted"] is True),
            lzip=None,
            ltotal=None,
            r=None,
        )
    except (KeyError, TypeError, ValueError, ReceiptRejected) as exc:
        raise ProbeHarnessError("trial summary cannot be recorded") from exc


def _profile_bytes(profile: BudgetProfile) -> bytes:
    document = {
        "attachment_limit_failed": profile.attachment_limit_failed,
        "envelope_id": profile.envelope_id,
        "lzip": profile.lzip,
        "lzip_certified_lower_bound": profile.lzip_certified_lower_bound,
        "ltotal": profile.ltotal,
        "ltotal_certified_lower_bound": profile.ltotal_certified_lower_bound,
        "measured_at": profile.measured_at,
        "n": profile.n,
        "pack_identity": profile.pack_identity,
        "page_session_identity": profile.page_session_identity,
        "r": profile.r,
        "schema_version": profile.schema_version,
        "semantic_success_count": profile.semantic_success_count,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii")


def _write_bytes(path: Path, payload: bytes) -> None:
    temporary = path.with_name(f".{path.name}.partial")
    temporary.write_bytes(payload)
    temporary.replace(path)
