"""Initial local library registry table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create the initial libraries table."""
    op.create_table(
        "libraries",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "device_id",
            sa.Text(),
            sa.ForeignKey("devices.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("path_flavor", sa.Text(), nullable=False),
        sa.Column("root_path", sa.Text(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_libraries_id_length"),
        sa.CheckConstraint(
            "length(device_id) = 36",
            name="ck_libraries_device_id_length",
        ),
        sa.CheckConstraint(
            "length(display_name) >= 1 AND length(display_name) <= 120",
            name="ck_libraries_display_name_length",
        ),
        sa.CheckConstraint(
            "path_flavor IN ('posix', 'windows')",
            name="ck_libraries_path_flavor",
        ),
        sa.CheckConstraint(
            "length(root_path) >= 1 AND length(root_path) <= 4096",
            name="ck_libraries_root_path_length",
        ),
        sa.UniqueConstraint(
            "device_id",
            "path_flavor",
            "root_path",
            name="uq_libraries_device_root",
        ),
    )


def downgrade() -> None:
    """Downgrades are intentionally unsupported."""
    raise NotImplementedError("FrameNest migration downgrades are not supported.")
