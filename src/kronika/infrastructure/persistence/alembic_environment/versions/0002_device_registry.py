"""Initial local device registry table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial devices table."""
    op.create_table(
        "devices",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_devices_id_length"),
        sa.CheckConstraint(
            "length(display_name) >= 1 AND length(display_name) <= 120",
            name="ck_devices_display_name_length",
        ),
    )


def downgrade() -> None:
    """Downgrades are intentionally unsupported."""
    raise NotImplementedError("FrameNest migration downgrades are not supported.")
