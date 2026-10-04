"""Sanitized persistence error types owned by FrameNest."""

from __future__ import annotations


class FrameNestPersistenceError(RuntimeError):
    """Base error for sanitized persistence failures."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        retryable: bool = False,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.error_code = error_code
        self.retryable = retryable
        if cause is not None:
            self.__cause__ = cause


class FrameNestMigrationError(FrameNestPersistenceError):
    """Sanitized migration failure."""
