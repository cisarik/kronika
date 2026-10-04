"""Add durable automatic media analysis run lifecycle table."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create media_analysis_runs without backfilling historical media."""
    op.create_table(
        "media_analysis_runs",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("media_location_id", sa.Text(), nullable=False),
        sa.Column("analysis_definition", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=True),
        sa.Column("model_id", sa.Text(), nullable=True),
        sa.Column("prompt_version", sa.Text(), nullable=True),
        sa.Column("result_schema_version", sa.Text(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=True),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_media_analysis_runs_id_length"),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_media_analysis_runs_media_id_length",
        ),
        sa.CheckConstraint(
            "length(media_location_id) = 36",
            name="ck_media_analysis_runs_media_location_id_length",
        ),
        sa.CheckConstraint(
            "length(analysis_definition) >= 1 AND length(analysis_definition) <= 64",
            name="ck_media_analysis_runs_definition_length",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'analyzing', 'analyzed', 'failed')",
            name="ck_media_analysis_runs_state",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND attempt_count <= 100",
            name="ck_media_analysis_runs_attempt_count",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_media_analysis_runs_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "started_at_ms IS NULL OR started_at_ms >= 0",
            name="ck_media_analysis_runs_started_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "completed_at_ms IS NULL OR completed_at_ms >= 0",
            name="ck_media_analysis_runs_completed_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "version >= 1",
            name="ck_media_analysis_runs_version_positive",
        ),
        sa.CheckConstraint(
            "("
            "state = 'pending' AND started_at_ms IS NULL AND completed_at_ms IS NULL "
            "AND result_json IS NULL AND result_schema_version IS NULL "
            "AND error_code IS NULL AND error_message IS NULL"
            ") OR ("
            "state = 'analyzing' AND started_at_ms IS NOT NULL AND completed_at_ms IS NULL "
            "AND result_json IS NULL AND result_schema_version IS NULL "
            "AND error_code IS NULL AND error_message IS NULL "
            "AND attempt_count >= 1"
            ") OR ("
            "state = 'analyzed' AND started_at_ms IS NOT NULL AND completed_at_ms IS NOT NULL "
            "AND result_json IS NOT NULL AND result_schema_version IS NOT NULL "
            "AND error_code IS NULL AND error_message IS NULL "
            "AND provider_id IS NOT NULL AND model_id IS NOT NULL "
            "AND prompt_version IS NOT NULL AND attempt_count >= 1"
            ") OR ("
            "state = 'failed' AND started_at_ms IS NOT NULL AND completed_at_ms IS NOT NULL "
            "AND result_json IS NULL AND result_schema_version IS NULL "
            "AND error_code IS NOT NULL AND error_message IS NOT NULL "
            "AND attempt_count >= 1"
            ")",
            name="ck_media_analysis_runs_state_payload",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_media_analysis_runs_media_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_location_id"],
            ["physical_media_locations.id"],
            name="fk_media_analysis_runs_media_location_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "media_id",
            "analysis_definition",
            name="uq_media_analysis_runs_media_definition",
        ),
    )
    op.create_index(
        "ix_media_analysis_runs_unfinished",
        "media_analysis_runs",
        ["state", "created_at_ms", "id"],
    )


def downgrade() -> None:
    """Drop only the analysis-run table introduced by revision 0015."""
    op.drop_index("ix_media_analysis_runs_unfinished", table_name="media_analysis_runs")
    op.drop_table("media_analysis_runs")
