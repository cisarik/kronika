"""Automatic Processed collection columns for media_metadata."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add collection_key and processed_at_ms columns to media_metadata."""
    with op.batch_alter_table("media_metadata") as batch_op:
        batch_op.add_column(sa.Column("collection_key", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("processed_at_ms", sa.Integer(), nullable=True))
        batch_op.create_check_constraint(
            "ck_media_metadata_collection_key_valid",
            "collection_key IS NULL OR collection_key = 'processed'",
        )
        batch_op.create_check_constraint(
            "ck_media_metadata_collection_paired",
            "(collection_key IS NULL AND processed_at_ms IS NULL) "
            "OR (collection_key = 'processed' AND processed_at_ms IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_media_metadata_processed_at_ms_non_negative",
            "processed_at_ms IS NULL OR processed_at_ms >= 0",
        )
        batch_op.create_index(
            "ix_media_metadata_collection",
            ["collection_key", "processed_at_ms", "media_id"],
        )


def downgrade() -> None:
    """Remove collection_key and processed_at_ms columns."""
    with op.batch_alter_table("media_metadata") as batch_op:
        batch_op.drop_index("ix_media_metadata_collection")
        batch_op.drop_constraint("ck_media_metadata_collection_key_valid", type_="check")
        batch_op.drop_constraint("ck_media_metadata_collection_paired", type_="check")
        batch_op.drop_constraint("ck_media_metadata_processed_at_ms_non_negative", type_="check")
        batch_op.drop_column("collection_key")
        batch_op.drop_column("processed_at_ms")
