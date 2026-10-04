"""Persistent plain-text media description."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable description column to media_metadata."""
    with op.batch_alter_table("media_metadata") as batch_op:
        batch_op.add_column(sa.Column("description", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_media_metadata_description_length",
            "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
        )


def downgrade() -> None:
    """Remove only the description column from media_metadata."""
    with op.batch_alter_table("media_metadata") as batch_op:
        batch_op.drop_constraint("ck_media_metadata_description_length", type_="check")
        batch_op.drop_column("description")
