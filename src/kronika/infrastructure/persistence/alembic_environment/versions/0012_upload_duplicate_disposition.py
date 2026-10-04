"""Durable exact-upload duplicate dispositions."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable, constrained duplicate-resolution provenance."""
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.add_column(sa.Column("duplicate_disposition", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_upload_sessions_duplicate_disposition",
            "duplicate_disposition IS NULL OR ("
            "checksum_algorithm = 'sha256' "
            "AND checksum_hex IS NOT NULL "
            "AND validated_media_kind IS NOT NULL "
            "AND validated_format IS NOT NULL "
            "AND byte_identity_id IS NOT NULL "
            "AND ((duplicate_disposition = 'keep_separate' "
            "AND state IN ('publish_pending', 'published', 'cataloged', 'failed')) "
            "OR (duplicate_disposition = 'discard' AND state = 'cancelled')))",
        )


def downgrade() -> None:
    """Remove only duplicate-resolution provenance introduced by revision 0012."""
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.drop_constraint(
            "ck_upload_sessions_duplicate_disposition",
            type_="check",
        )
        batch_op.drop_column("duplicate_disposition")
