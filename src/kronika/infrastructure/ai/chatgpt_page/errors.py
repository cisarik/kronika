"""Typed failures for offline chatgpt-page preparation and probe tooling."""

from __future__ import annotations


class BoundedPreparationError(Exception):
    """The fixed envelope cannot fit the applicable byte bound."""

    code = "PREPARATION_BOUNDED"


class BudgetProfileRejected(Exception):
    """A budget profile is missing, inconsistent, stale, or below the video floor."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ReceiptRejected(Exception):
    """A probe receipt contains a forbidden content class."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ProbeHarnessError(Exception):
    """The offline probe harness refused a trial specification."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason
