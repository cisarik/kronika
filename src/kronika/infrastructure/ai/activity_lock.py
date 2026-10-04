"""Exclusive activity locks for server-side AI provider operations."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

AI_ACTIVITY_LOCK_BUSY_MESSAGE = "Another AI provider operation is already running."
AI_ACTIVITY_LOCK_FAILED_MESSAGE = "AI provider operation lock could not be created."


class AiActivityLockError(RuntimeError):
    """Sanitized activity-lock acquisition failure."""


@dataclass(slots=True)
class AiActivityLock:
    """One held exclusive AI provider activity lock."""

    lock_path: Path
    descriptor: int

    def release(self) -> None:
        """Close the descriptor and remove the owned lock file."""
        try:
            os.close(self.descriptor)
        except OSError:
            pass
        try:
            self.lock_path.unlink(missing_ok=True)
        except OSError:
            pass


def acquire_ai_activity_lock(lock_path: Path) -> AiActivityLock | None:
    """Acquire one exclusive non-blocking activity lock, or return None when busy."""
    lock_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        descriptor = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return None
    except OSError as exc:
        raise AiActivityLockError(AI_ACTIVITY_LOCK_FAILED_MESSAGE) from exc
    return AiActivityLock(lock_path=lock_path, descriptor=descriptor)
