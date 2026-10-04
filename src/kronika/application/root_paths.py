"""Shared server-owned root path overlap checks."""

from __future__ import annotations

from pathlib import Path


def roots_overlap(left: Path, right: Path) -> bool:
    """Return whether either canonical path is equal to or contains the other."""
    left_resolved = left.resolve(strict=False)
    right_resolved = right.resolve(strict=False)
    return _contains(left_resolved, right_resolved) or _contains(
        right_resolved,
        left_resolved,
    )


def _contains(parent: Path, child: Path) -> bool:
    if parent == child:
        return True
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
