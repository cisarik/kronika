"""Add durable per-tag companion review provenance (0032)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0032"
down_revision = "0031"
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
    """Create companion_review_tag_sources without historical backfill."""
    op.create_table(
        "companion_review_tag_sources",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("tag_key", sa.Text(), nullable=False),
        sa.Column("analysis_run_id", sa.Text(), nullable=False),
        sa.Column("applied_by_login_key", sa.Text(), nullable=False),
        sa.Column("applied_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "media_id",
            "tag_key",
            name="pk_companion_review_tag_sources",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["media_metadata.media_id"],
            name="fk_companion_review_tag_sources_media_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tag_key"],
            ["canonical_tags.key"],
            name="fk_companion_review_tag_sources_tag_key",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["media_analysis_runs.id"],
            name="fk_companion_review_tag_sources_analysis_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_companion_review_tag_sources_media_id_length",
        ),
        sa.CheckConstraint(
            "length(tag_key) >= 1 AND length(tag_key) <= 64",
            name="ck_companion_review_tag_sources_tag_key_length",
        ),
        sa.CheckConstraint(
            "tag_key = lower(tag_key)",
            name="ck_companion_review_tag_sources_tag_key_lowercase",
        ),
        sa.CheckConstraint(
            "tag_key GLOB '[a-z]*' "
            "AND tag_key NOT GLOB '*[^a-z0-9-]*' "
            "AND tag_key NOT LIKE '%--%' "
            "AND substr(tag_key, length(tag_key), 1) != '-'",
            name="ck_companion_review_tag_sources_tag_key_slug",
        ),
        sa.CheckConstraint(
            "length(analysis_run_id) = 36",
            name="ck_companion_review_tag_sources_analysis_run_id_length",
        ),
        sa.CheckConstraint(
            _login_key_sql("applied_by_login_key"),
            name="ck_companion_review_tag_sources_applied_by_login_key",
        ),
        sa.CheckConstraint(
            "applied_at_ms >= 0",
            name="ck_companion_review_tag_sources_applied_at_ms_non_negative",
        ),
    )
    op.create_index(
        "ix_companion_review_tag_sources_analysis_run_id",
        "companion_review_tag_sources",
        ["analysis_run_id"],
    )


def downgrade() -> None:
    """Drop per-tag provenance and restore the 0031 head."""
    op.drop_index(
        "ix_companion_review_tag_sources_analysis_run_id",
        table_name="companion_review_tag_sources",
    )
    op.drop_table("companion_review_tag_sources")
