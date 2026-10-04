"""Application coordinator for the provider-neutral research runtime.

It supervises one durable operation at a time: admission, submission, polling,
cancellation, remote cleanup, accounting reconciliation and startup recovery.
Network I/O happens outside database transactions. There is one generation
attempt per request; polling and cleanup may retry.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import time
from collections.abc import Callable
from dataclasses import replace

from kronika.application.ports.research import (
    ResearchAdmissionReceipt,
    ResearchProvider,
    ResearchRequestRow,
    ResearchResultCompletion,
    ResearchRuntimeRepository,
    ResearchSelectionError,
    ResearchSelectionSnapshot,
    ResearchStoreError,
)
from kronika.domain.research import (
    BudgetReconciliation,
    BudgetReservation,
    CompletionEvidence,
    ProviderHandle,
    ProviderObservation,
    ProviderObservationKind,
    ProviderRequest,
    ResearchAccountingState,
    ResearchAnswer,
    ResearchCitation,
    ResearchErrorCode,
    ResearchLifecycleState,
    ResearchOperationKind,
    ResearchRemoteCleanupState,
    ResearchRequestRecord,
    ResearchUsage,
    ResultCompletion,
    UsagePriceSchedule,
    completion_error,
    is_terminal_lifecycle,
    usage_cost_micro_usd_or_none,
)

SelectResearchProvider = Callable[[ResearchOperationKind], ResearchSelectionSnapshot]
ResolveUsagePriceSchedule = Callable[[str, str, str], UsagePriceSchedule | None]
AdmissionGuard = Callable[[ResearchSelectionSnapshot, int], ResearchErrorCode | None]

FINGERPRINT_VERSION = 2
LEGACY_RESEARCH_PROFILE_VERSION = "3"

_TERMINAL_BY_ERROR = {
    ResearchErrorCode.REFUSED: ResearchLifecycleState.REFUSED,
    ResearchErrorCode.INCOMPLETE_RESULT: ResearchLifecycleState.INCOMPLETE,
}


def research_request_fingerprint(
    *,
    owner_login_key: str,
    kind: ResearchOperationKind,
    prompt: str,
    consent_version: str,
) -> str:
    """Version-2 content fingerprint over owner, kind, prompt and consent.

    Provider, model, budgets, resource limits and settings revision are
    deliberately excluded so an identical replay still matches after any of
    them change.
    """
    payload = json.dumps(
        {
            "fingerprint_version": FINGERPRINT_VERSION,
            "owner": owner_login_key,
            "kind": kind.value,
            "prompt": prompt,
            "consent_version": consent_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _answer_checkpoint(answer: ResearchAnswer) -> str:
    return json.dumps(
        {
            "text": answer.text,
            "citations": [
                {"url": citation.url, "title": citation.title}
                for citation in answer.citations
            ],
            "evidence": {
                "provider_terminal": answer.evidence.provider_terminal,
                "answer_complete": answer.evidence.answer_complete,
                "web_search_executed": answer.evidence.web_search_executed,
                "refusal_marker": answer.evidence.refusal_marker,
                "incomplete_marker": answer.evidence.incomplete_marker,
            },
            "usage": (
                None
                if answer.usage is None
                else {
                    "input_tokens": answer.usage.input_tokens,
                    "cached_input_tokens": answer.usage.cached_input_tokens,
                    "output_tokens": answer.usage.output_tokens,
                    "reasoning_tokens": answer.usage.reasoning_tokens,
                    "web_tool_calls": answer.usage.web_tool_calls,
                    "cache_write_input_tokens": answer.usage.cache_write_input_tokens,
                }
            ),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )


class ResearchCoordinator:
    """Lifecycle-owned research supervisor over injected ports."""

    def __init__(
        self,
        *,
        provider: ResearchProvider,
        requests: ResearchRuntimeRepository,
        ledger: object,
        completion: ResearchResultCompletion,
        select: SelectResearchProvider,
        resolve_price_schedule: ResolveUsagePriceSchedule | None = None,
        admission_guard: AdmissionGuard | None = None,
        accounting_blocker: Callable[[], ResearchErrorCode | None] | None = None,
        submission_enabled: Callable[[], bool] | None = None,
        clock_ms: Callable[[], int] | None = None,
        new_operation_id: Callable[[], str] | None = None,
    ) -> None:
        self._provider = provider
        self._requests = requests
        self._ledger = ledger
        self._completion = completion
        self._select = select
        self._resolve_price_schedule = resolve_price_schedule
        self._admission_guard = admission_guard
        self._accounting_blocker = accounting_blocker
        self._submission_enabled = submission_enabled
        self._clock_ms = clock_ms or (lambda: time.time_ns() // 1_000_000)
        self._new_operation_id = new_operation_id or (
            lambda: f"op-{secrets.token_hex(16)}"
        )

    # -- admission ---------------------------------------------------------

    def admit(
        self,
        *,
        owner_login_key: str,
        client_request_id: str,
        kind: ResearchOperationKind,
        prompt: str,
        consent_version: str = "",
    ) -> ResearchAdmissionReceipt:
        if not isinstance(kind, ResearchOperationKind):
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        if not isinstance(prompt, str) or not prompt.strip():
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        if not isinstance(owner_login_key, str) or not owner_login_key:
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        if not isinstance(client_request_id, str) or not client_request_id:
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        if not isinstance(consent_version, str):
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        # Idempotency lookup precedes configuration, enablement and credential
        # checks so an identical replay succeeds even after those change.
        existing = self._requests.find_by_client(owner_login_key, client_request_id)
        if existing is not None:
            if self._replay_matches(
                existing,
                owner_login_key=owner_login_key,
                kind=kind,
                prompt=prompt,
                consent_version=consent_version,
            ):
                return ResearchAdmissionReceipt(row=existing, newly_admitted=False)
            raise ResearchStoreError(ResearchErrorCode.IDEMPOTENCY_CONFLICT)
        blocker = self._accounting_blocker() if self._accounting_blocker else None
        if blocker is not None:
            raise ResearchStoreError(blocker)
        try:
            snapshot = self._select(kind)
        except ResearchSelectionError as exc:
            raise ResearchStoreError(exc.code) from None
        now_ms = self._clock_ms()
        if self._admission_guard is not None:
            denial = self._admission_guard(snapshot, now_ms)
            if denial is not None:
                raise ResearchStoreError(denial)
        fingerprint = research_request_fingerprint(
            owner_login_key=owner_login_key,
            kind=kind,
            prompt=prompt,
            consent_version=consent_version,
        )
        record = ResearchRequestRecord(
            operation_id=self._new_operation_id(),
            kind=kind,
            prompt=prompt,
            profile=snapshot.profile,
            deadline_seconds=snapshot.deadline_seconds,
            resource_limits=snapshot.resource_limits,
            state=ResearchLifecycleState.ADMITTED,
            cleanup_state=ResearchRemoteCleanupState.NOT_REQUIRED,
            accounting_state=ResearchAccountingState.RESERVED,
            remote_handle=None,
            error_code=None,
        )
        row = ResearchRequestRow(
            record=record,
            owner_login_key=owner_login_key,
            client_request_id=client_request_id,
            request_fingerprint=fingerprint,
            checkpoint_json=None,
            checkpoint_sha256=None,
            record_id=None,
            created_at_ms=now_ms,
            admitted_at_ms=now_ms,
            submitted_at_ms=None,
            finished_at_ms=None,
            updated_at_ms=now_ms,
            cancel_requested_at_ms=None,
            cancellation_confirmed_at_ms=None,
        )
        reservation = BudgetReservation(
            operation_id=record.operation_id,
            kind=kind,
            reserved_usd_micros=record.profile.budget_reservation_usd_micros,
            daily_limit_usd_micros=snapshot.daily_budget_usd_micros,
            monthly_limit_usd_micros=snapshot.monthly_budget_usd_micros,
        )
        stored = self._requests.admit(row, reservation)
        newly_admitted = (
            stored.record.operation_id == record.operation_id
            and stored.request_fingerprint == fingerprint
        )
        return ResearchAdmissionReceipt(row=stored, newly_admitted=newly_admitted)

    def _replay_matches(
        self,
        existing: ResearchRequestRow,
        *,
        owner_login_key: str,
        kind: ResearchOperationKind,
        prompt: str,
        consent_version: str,
    ) -> bool:
        """Return whether a stored request is an identical replay.

        Legacy version-3 rows are compared on persisted owner, kind and prompt
        because their fingerprint predates the version-2 object and their
        consent value was never persisted.
        """
        if existing.owner_login_key != owner_login_key:
            return False
        if existing.record.kind is not kind or existing.record.prompt != prompt:
            return False
        if existing.record.profile.configuration_version == LEGACY_RESEARCH_PROFILE_VERSION:
            return True
        fingerprint = research_request_fingerprint(
            owner_login_key=owner_login_key,
            kind=kind,
            prompt=prompt,
            consent_version=consent_version,
        )
        return existing.request_fingerprint == fingerprint

    # -- submission --------------------------------------------------------

    def submit_pending(self) -> ResearchRequestRow | None:
        """Claim and submit the admitted request, if one is waiting.

        Disabling research prevents new submission claims. The atomic
        ``ADMITTED`` to ``SUBMITTING`` claim guarantees only one winner issues
        provider creation even under concurrent nudges.
        """
        if self._submission_enabled is not None and not self._submission_enabled():
            return None
        row = self._active_row()
        if row is None or row.record.state is not ResearchLifecycleState.ADMITTED:
            return None
        claimed = self._requests.claim_submission(row.record.operation_id)
        if claimed is None:
            return None
        provider_request = ProviderRequest(
            operation_id=claimed.record.operation_id,
            kind=claimed.record.kind,
            prompt=claimed.record.prompt,
            profile=claimed.record.profile,
            deadline_seconds=claimed.record.deadline_seconds,
            resource_limits=claimed.record.resource_limits,
        )
        try:
            observation = self._provider.submit(provider_request)
        except Exception:
            return self._finish(
                claimed,
                ResearchLifecycleState.SUBMISSION_UNKNOWN,
                ResearchErrorCode.SUBMISSION_UNKNOWN,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        return self._apply_submit_observation(claimed, observation)

    def recover(self) -> ResearchRequestRow | None:
        """Classify the active slot after a restart. Never resubmits blindly."""
        row = self._active_row()
        if row is None:
            return None
        if row.record.state is ResearchLifecycleState.SUBMITTING:
            return self._finish(
                row,
                ResearchLifecycleState.SUBMISSION_UNKNOWN,
                ResearchErrorCode.SUBMISSION_UNKNOWN,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        return row

    def _apply_submit_observation(
        self,
        row: ResearchRequestRow,
        observation: ProviderObservation,
    ) -> ResearchRequestRow:
        now_ms = self._clock_ms()
        if observation.kind in (
            ProviderObservationKind.PENDING,
            ProviderObservationKind.RUNNING,
        ):
            handle = observation.remote_handle or row.record.remote_handle
            record = replace(
                row.record,
                state=ResearchLifecycleState.RUNNING,
                remote_handle=handle,
            )
            return self._requests.save(
                replace(
                    row,
                    record=record,
                    submitted_at_ms=row.submitted_at_ms or now_ms,
                    updated_at_ms=now_ms,
                )
            )
        if observation.kind is ProviderObservationKind.COMPLETE:
            return self._accept_completion(row, observation)
        if observation.kind is ProviderObservationKind.REFUSED:
            return self._finish(
                row,
                ResearchLifecycleState.REFUSED,
                observation.error_code or ResearchErrorCode.REFUSED,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        if observation.kind is ProviderObservationKind.FAILED:
            code = observation.error_code or ResearchErrorCode.PROVIDER_UNAVAILABLE
            state = (
                ResearchLifecycleState.INCOMPLETE
                if code is ResearchErrorCode.INCOMPLETE_RESULT
                else ResearchLifecycleState.FAILED
            )
            return self._finish(
                row,
                state,
                code,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        if observation.kind is ProviderObservationKind.CANCELLED:
            return self._finish(
                row,
                ResearchLifecycleState.CANCELLED,
                ResearchErrorCode.CANCELLED,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        return self._finish(
            row,
            ResearchLifecycleState.SUBMISSION_UNKNOWN,
            ResearchErrorCode.SUBMISSION_UNKNOWN,
            accounting=ResearchAccountingState.UNKNOWN,
        )

    # -- polling and completion -------------------------------------------

    def poll_once(self) -> ResearchRequestRow | None:
        """Advance the active running or cancelling request."""
        row = self._active_row()
        if row is None:
            return None
        state = row.record.state
        if state in (ResearchLifecycleState.ADMITTED, ResearchLifecycleState.SUBMITTING):
            return None
        if state is ResearchLifecycleState.VALIDATING:
            answer = self._answer_from_checkpoint(row)
            if answer is None:
                return row
            return self._complete_from_checkpoint(row, answer)
        handle = row.record.remote_handle
        if handle is None:
            return None
        now_ms = self._clock_ms()
        if now_ms - row.admitted_at_ms >= row.record.deadline_seconds * 1000:
            try:
                self._provider.cancel(handle)
            except Exception:
                pass
            return self._finish(
                row,
                ResearchLifecycleState.TIMEOUT,
                ResearchErrorCode.TIMEOUT,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        try:
            observation = self._provider.poll(handle)
        except Exception:
            # Unresolved transport trouble: keep the request for the next poll.
            return row
        if observation.kind in (
            ProviderObservationKind.PENDING,
            ProviderObservationKind.RUNNING,
        ):
            return self._requests.save(
                replace(row, updated_at_ms=now_ms)
            )
        if observation.kind is ProviderObservationKind.COMPLETE:
            return self._accept_completion(row, observation)
        if observation.kind is ProviderObservationKind.REFUSED:
            return self._finish(
                row,
                ResearchLifecycleState.REFUSED,
                observation.error_code or ResearchErrorCode.REFUSED,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        if observation.kind is ProviderObservationKind.FAILED:
            code = observation.error_code or ResearchErrorCode.PROVIDER_UNAVAILABLE
            state = (
                ResearchLifecycleState.INCOMPLETE
                if code is ResearchErrorCode.INCOMPLETE_RESULT
                else ResearchLifecycleState.FAILED
            )
            return self._finish(
                row,
                state,
                code,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        if observation.kind is ProviderObservationKind.CANCELLED:
            return self._finish(
                row,
                ResearchLifecycleState.CANCELLED,
                ResearchErrorCode.CANCELLED,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        return row

    def _accept_completion(
        self,
        row: ResearchRequestRow,
        observation: ProviderObservation,
    ) -> ResearchRequestRow:
        now_ms = self._clock_ms()
        if row.cancel_requested_at_ms is not None:
            # A cancellation committed before result-save prevents finalization.
            return self._finish(
                row,
                ResearchLifecycleState.CANCELLED,
                ResearchErrorCode.CANCELLED,
                accounting=ResearchAccountingState.UNKNOWN,
                keep_handle=True,
            )
        assert observation.answer is not None and observation.remote_handle is not None
        error = completion_error(observation.answer.evidence)
        if error is not None:
            state = _TERMINAL_BY_ERROR.get(error, ResearchLifecycleState.FAILED)
            return self._finish(
                row,
                state,
                error,
                accounting=ResearchAccountingState.UNKNOWN,
                keep_handle=True,
            )
        checkpoint = _answer_checkpoint(observation.answer)
        record = replace(
            row.record,
            state=ResearchLifecycleState.VALIDATING,
            remote_handle=observation.remote_handle,
        )
        validating = self._requests.save(
            replace(
                row,
                record=record,
                checkpoint_json=checkpoint,
                checkpoint_sha256=hashlib.sha256(checkpoint.encode("utf-8")).hexdigest(),
                updated_at_ms=now_ms,
            )
        )
        return self._complete_from_checkpoint(validating, observation.answer)

    def _complete_from_checkpoint(
        self,
        row: ResearchRequestRow,
        answer: ResearchAnswer | None = None,
    ) -> ResearchRequestRow:
        handle = row.record.remote_handle
        if handle is None:
            return row
        if answer is None:
            return row
        try:
            receipt = self._completion.complete(
                ResultCompletion(
                    operation_id=row.record.operation_id,
                    answer=answer,
                    remote_handle=handle,
                )
            )
        except Exception:
            # Keep the validating checkpoint for a later completion retry.
            return self._requests.save(
                replace(row, updated_at_ms=self._clock_ms())
            )
        if receipt.state is not ResearchLifecycleState.SAVED:
            return row
        # The completion port owns the record binding; reload so the terminal
        # save cannot clobber storage fields it wrote.
        current = self._requests.get_request(row.record.operation_id) or row
        schedule = self._schedule_for(current)
        cost = usage_cost_micro_usd_or_none(answer.usage, schedule)
        if cost is None:
            accounting = ResearchAccountingState.UNKNOWN
            reconciled_usage: ResearchUsage | None = None
        else:
            accounting = ResearchAccountingState.RECONCILED
            reconciled_usage = answer.usage
        return self._finish(
            current,
            ResearchLifecycleState.SAVED,
            None,
            accounting=accounting,
            usage=reconciled_usage,
            cost=cost,
            keep_handle=True,
        )

    def _schedule_for(self, row: ResearchRequestRow) -> UsagePriceSchedule | None:
        """Resolve pricing from the persisted request identity, never settings."""
        if self._resolve_price_schedule is None:
            return None
        profile = row.record.profile
        return self._resolve_price_schedule(
            profile.provider_id,
            profile.model_id,
            profile.configuration_version,
        )

    # -- cancellation and cleanup -----------------------------------------

    def cancel(self, operation_id: str) -> ResearchRequestRow:
        row = self._requests.get_request(operation_id)
        if row is None:
            raise ResearchStoreError(ResearchErrorCode.INVALID_REQUEST)
        if is_terminal_lifecycle(row.record.state):
            return row
        now_ms = self._clock_ms()
        if row.cancel_requested_at_ms is None:
            record = replace(
                row.record,
                state=ResearchLifecycleState.CANCEL_REQUESTED,
            )
            row = self._requests.save(
                replace(
                    row,
                    record=record,
                    cancel_requested_at_ms=now_ms,
                    updated_at_ms=now_ms,
                )
            )
        handle = row.record.remote_handle
        if handle is None:
            return row
        try:
            observation = self._provider.cancel(handle)
        except Exception:
            return row
        if observation.kind is ProviderObservationKind.CANCELLED:
            confirmed = self._finish(
                row,
                ResearchLifecycleState.CANCELLED,
                ResearchErrorCode.CANCELLED,
                accounting=ResearchAccountingState.UNKNOWN,
                keep_handle=True,
            )
            return self._requests.save(
                replace(
                    confirmed,
                    cancellation_confirmed_at_ms=self._clock_ms(),
                    updated_at_ms=self._clock_ms(),
                )
            )
        if observation.kind is ProviderObservationKind.COMPLETE:
            return self._finish(
                row,
                ResearchLifecycleState.CANCELLED,
                ResearchErrorCode.CANCELLED,
                accounting=ResearchAccountingState.UNKNOWN,
                keep_handle=True,
            )
        if observation.kind is ProviderObservationKind.REFUSED:
            return self._finish(
                row,
                ResearchLifecycleState.REFUSED,
                observation.error_code or ResearchErrorCode.REFUSED,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        if observation.kind is ProviderObservationKind.FAILED:
            code = observation.error_code or ResearchErrorCode.PROVIDER_UNAVAILABLE
            state = (
                ResearchLifecycleState.INCOMPLETE
                if code is ResearchErrorCode.INCOMPLETE_RESULT
                else ResearchLifecycleState.FAILED
            )
            return self._finish(
                row,
                state,
                code,
                accounting=ResearchAccountingState.UNKNOWN,
            )
        return row

    def release_remote_pending(
        self,
        *,
        limit: int = 20,
    ) -> tuple[ResearchRequestRow, ...]:
        """Delete remote responses for terminal requests that still hold one."""
        released: list[ResearchRequestRow] = []
        for row in self._requests.list_cleanup_pending(limit):
            handle = row.record.remote_handle
            if handle is None:
                continue
            try:
                outcome = self._provider.release_remote(handle)
            except Exception:
                continue
            if outcome.state is ResearchRemoteCleanupState.DELETED:
                cleanup = ResearchRemoteCleanupState.DELETED
            elif outcome.state is ResearchRemoteCleanupState.FAILED:
                cleanup = ResearchRemoteCleanupState.FAILED
            else:
                cleanup = ResearchRemoteCleanupState.UNKNOWN
            released.append(
                self._requests.save(
                    replace(
                        row,
                        record=replace(row.record, cleanup_state=cleanup),
                        updated_at_ms=self._clock_ms(),
                    )
                )
            )
        return tuple(released)

    # -- shared helpers ----------------------------------------------------

    def _active_row(self) -> ResearchRequestRow | None:
        operation_id = self._requests.active_slot_operation_id()
        if operation_id is None:
            return None
        return self._requests.get_request(operation_id)

    def _finish(
        self,
        row: ResearchRequestRow,
        state: ResearchLifecycleState,
        code: ResearchErrorCode | None,
        *,
        accounting: ResearchAccountingState,
        usage: object = None,
        cost: int | None = None,
        keep_handle: bool = True,
    ) -> ResearchRequestRow:
        handle = row.record.remote_handle if keep_handle else None
        cleanup = (
            ResearchRemoteCleanupState.PENDING
            if handle is not None
            else ResearchRemoteCleanupState.NOT_REQUIRED
        )
        now_ms = self._clock_ms()
        record = replace(
            row.record,
            state=state,
            error_code=code,
            cleanup_state=cleanup,
            accounting_state=accounting,
        )
        saved = self._requests.save(
            replace(row, record=record, finished_at_ms=now_ms, updated_at_ms=now_ms)
        )
        self._reconcile(saved, accounting, usage=usage, cost=cost)
        return saved

    def _reconcile(
        self,
        row: ResearchRequestRow,
        accounting: ResearchAccountingState,
        *,
        usage: object,
        cost: int | None,
    ) -> None:
        if (
            accounting is ResearchAccountingState.RECONCILED
            and usage is not None
            and cost is not None
        ):
            reconciliation = BudgetReconciliation(
                operation_id=row.record.operation_id,
                usage=usage,
                calculated_cost_usd_micros=cost,
                state=ResearchAccountingState.RECONCILED,
            )
        else:
            reconciliation = BudgetReconciliation(
                operation_id=row.record.operation_id,
                usage=None,
                calculated_cost_usd_micros=None,
                state=ResearchAccountingState.UNKNOWN,
            )
        self._ledger.reconcile(reconciliation)

    def _answer_from_checkpoint(
        self, row: ResearchRequestRow
    ) -> ResearchAnswer | None:
        payload = row.checkpoint_json
        if not payload:
            return None
        try:
            data = json.loads(payload)
            citations = tuple(
                ResearchCitation(url=str(item["url"]), title=str(item["title"]))
                for item in data.get("citations", [])
            )
            evidence = data["evidence"]
            usage_payload = data.get("usage")
            usage: ResearchUsage | None = None
            if usage_payload is not None:
                cache_write = usage_payload.get("cache_write_input_tokens")
                usage = ResearchUsage(
                    input_tokens=int(usage_payload["input_tokens"]),
                    cached_input_tokens=int(usage_payload["cached_input_tokens"]),
                    output_tokens=int(usage_payload["output_tokens"]),
                    reasoning_tokens=int(usage_payload["reasoning_tokens"]),
                    web_tool_calls=int(usage_payload["web_tool_calls"]),
                    cache_write_input_tokens=(
                        None if cache_write is None else int(cache_write)
                    ),
                )
            return ResearchAnswer(
                text=str(data["text"]),
                citations=citations,
                evidence=CompletionEvidence(
                    provider_terminal=bool(evidence["provider_terminal"]),
                    answer_complete=bool(evidence["answer_complete"]),
                    web_search_executed=bool(evidence["web_search_executed"]),
                    refusal_marker=bool(evidence["refusal_marker"]),
                    incomplete_marker=bool(evidence["incomplete_marker"]),
                ),
                usage=usage,
            )
        except (KeyError, TypeError, ValueError):
            return None
