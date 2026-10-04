"""Cancellation before, during, and after a trial, with owned-path cleanup."""

from __future__ import annotations

from kronika.infrastructure.ai.chatgpt_page.budget import (
    LOCAL_ATTACHMENT_CEILING_BYTES,
    operating_count,
    validate_budget_profile,
)
from kronika.infrastructure.ai.chatgpt_page.probe import (
    CancellationToken,
    TrialObservation,
    TrialSpec,
    run_probe,
)

PACK = "ab" * 32
SESSION = "page-session-a"


class FakeTransport:
    def __init__(self, cancel: CancellationToken, *, cancel_on_call: int | None = None) -> None:
        self.cancel = cancel
        self.cancel_on_call = cancel_on_call
        self.calls: list[str] = []

    def execute(self, trial: TrialSpec) -> TrialObservation:
        self.calls.append(trial.trial_id)
        if self.cancel_on_call is not None and len(self.calls) == self.cancel_on_call:
            self.cancel.cancel()
        return TrialObservation(
            accepted=True,
            byte_size=len(trial.payload),
            elapsed_ms=5,
            error_category="cookie=must-not-leak",
        )


def _trial(index: int) -> TrialSpec:
    return TrialSpec(
        trial_id=f"t{index}",
        mime_type="image/jpeg",
        fixture_identity=f"fx-0000000b-still-jpeg-{index:016x}",
        payload=b"jpeg-bytes",
    )


def _profile():
    lzip = ltotal = LOCAL_ATTACHMENT_CEILING_BYTES
    document = {
        "schema_version": 1,
        "measured_at": "2026-09-22T18:00:00+00:00",
        "envelope_id": "480q60",
        "lzip": lzip,
        "ltotal": ltotal,
        "lzip_certified_lower_bound": False,
        "ltotal_certified_lower_bound": False,
        "r": 20,
        "n": operating_count(lzip, ltotal, 20),
        "pack_identity": PACK,
        "page_session_identity": SESSION,
        "semantic_success_count": 2,
        "attachment_limit_failed": False,
    }
    return validate_budget_profile(
        document,
        expected_pack_identity=PACK,
        expected_page_session_identity=SESSION,
    )


def test_cancel_before_the_first_trial_runs_nothing(tmp_path) -> None:
    keep = tmp_path / "keep.txt"
    keep.write_text("keep", encoding="ascii")
    cancel = CancellationToken()
    cancel.cancel()
    transport = FakeTransport(cancel)

    outcome = run_probe(
        (_trial(1), _trial(2)),
        transport,
        cancel,
        tmp_path,
        pack_hash=PACK,
        profile=_profile(),
    )

    assert outcome.status == "cancelled"
    assert outcome.summaries == ()
    assert transport.calls == []
    assert outcome.receipt_path is None
    assert not (tmp_path / "chatgpt-page-probe-owned" / "receipt.json").exists()
    assert not (tmp_path / "chatgpt-page-probe-owned" / "budget.json").exists()
    assert not (tmp_path / "chatgpt-page-probe-owned" / "scratch").exists()
    assert keep.read_text(encoding="ascii") == "keep"


def test_cancel_during_a_trial_keeps_only_earlier_summaries(tmp_path) -> None:
    cancel = CancellationToken()
    transport = FakeTransport(cancel, cancel_on_call=2)

    outcome = run_probe(
        (_trial(1), _trial(2)),
        transport,
        cancel,
        tmp_path,
        pack_hash=PACK,
        profile=_profile(),
    )

    assert outcome.status == "cancelled"
    assert transport.calls == ["t1", "t2"]
    assert [item["fixture_identity"] for item in outcome.summaries] == [_trial(1).fixture_identity]
    assert outcome.receipt_path is None
    assert not (tmp_path / "chatgpt-page-probe-owned" / "budget.json").exists()
    assert "cookie=must-not-leak" not in str(outcome.summaries)


def test_cancel_after_a_recorded_trial_writes_no_profile(tmp_path) -> None:
    cancel = CancellationToken()
    transport = FakeTransport(cancel)

    outcome = run_probe(
        (_trial(1), _trial(2)),
        transport,
        cancel,
        tmp_path,
        pack_hash=PACK,
        profile=_profile(),
        on_trial_recorded=cancel.cancel,
    )

    assert outcome.status == "cancelled"
    assert transport.calls == ["t1"]
    assert len(outcome.summaries) == 1
    assert outcome.profile_path is None
    assert not (tmp_path / "chatgpt-page-probe-owned" / "receipt.json").exists()
    assert not (tmp_path / "chatgpt-page-probe-owned" / "scratch").exists()


def test_completed_run_writes_a_receipt_and_profile_without_canaries(tmp_path) -> None:
    cancel = CancellationToken()
    transport = FakeTransport(cancel)
    profile = _profile()

    outcome = run_probe(
        (_trial(1),),
        transport,
        cancel,
        tmp_path,
        pack_hash=PACK,
        profile=profile,
    )

    assert outcome.status == "completed"
    assert outcome.receipt_path is not None and outcome.profile_path is not None
    receipt = outcome.receipt_path.read_text(encoding="ascii")
    budget = outcome.profile_path.read_text(encoding="ascii")
    assert "cookie=must-not-leak" not in receipt
    assert "cookie=must-not-leak" not in budget
    assert '"n":' in budget
    assert not (tmp_path / "chatgpt-page-probe-owned" / "scratch").exists()
