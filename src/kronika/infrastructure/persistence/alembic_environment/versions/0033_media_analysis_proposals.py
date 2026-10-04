"""Add durable ordinary-user analysis proposals (0033)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _login_key_sql(column: str) -> str:
    return (
        f"length({column}) >= 1 AND length({column}) <= 254 "
        f"AND {column} = lower({column}) "
        f"AND instr({column}, ' ') = 0 "
        f"AND instr({column}, char(9)) = 0 "
        f"AND instr({column}, char(10)) = 0 "
        f"AND instr({column}, char(13)) = 0"
    )


def upgrade() -> None:
    """Create media_analysis_proposals without touching existing catalog rows."""
    op.create_table(
        "media_analysis_proposals",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("proposed_by_login_key", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_media_analysis_proposals"),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_media_analysis_proposals_media_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(id) = 36",
            name="ck_media_analysis_proposals_id_length",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_media_analysis_proposals_media_id_length",
        ),
        sa.CheckConstraint(
            _login_key_sql("proposed_by_login_key"),
            name="ck_media_analysis_proposals_proposed_by_login_key",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_media_analysis_proposals_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'dismissed', 'completed')",
            name="ck_media_analysis_proposals_status",
        ),
    )
    op.create_index(
        "ix_media_analysis_proposals_created_at",
        "media_analysis_proposals",
        ["created_at_ms", "id"],
    )
    op.create_index(
        "ix_media_analysis_proposals_status_created",
        "media_analysis_proposals",
        ["status", "created_at_ms", "id"],
    )


def downgrade() -> None:
    """Drop analysis proposals and restore the 0032 head."""
    op.drop_index(
        "ix_media_analysis_proposals_status_created",
        table_name="media_analysis_proposals",
    )
    op.drop_index(
        "ix_media_analysis_proposals_created_at",
        table_name="media_analysis_proposals",
    )
    op.drop_table("media_analysis_proposals")
