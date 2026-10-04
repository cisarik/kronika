"""Initial persistence foundation with no product schema."""

from __future__ import annotations

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Leave the initial foundation empty except for Alembic version tracking."""


def downgrade() -> None:
    """Downgrades are intentionally unsupported for the initial foundation."""
    raise NotImplementedError("FrameNest migration downgrades are not supported.")
