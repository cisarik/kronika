"""Add analysis-run history lineage and active-run uniqueness."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None

_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after analysis-run history migration."
)
_DOWNGRADE_HISTORY_MESSAGE = (
    "Cannot downgrade analysis-run history while multiple runs exist for the "
    "same media and analysis definition."
)


def upgrade() -> None:
    """Preserve historical runs and constrain only active media/definition pairs."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_analysis_runs") as batch_op:
            batch_op.drop_constraint(
                "uq_media_analysis_runs_media_definition",
                type_="unique",
            )
            batch_op.add_column(
                sa.Column("supersedes_run_id", sa.Text(), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_media_analysis_runs_supersedes_run_id",
                "supersedes_run_id IS NULL OR ("
                "length(supersedes_run_id) = 36 AND supersedes_run_id != id"
                ")",
            )
            batch_op.create_foreign_key(
                "fk_media_analysis_runs_supersedes_run_id",
                "media_analysis_runs",
                ["supersedes_run_id"],
                ["id"],
                ondelete="RESTRICT",
            )

        op.execute(
            """
            CREATE UNIQUE INDEX uq_media_analysis_runs_active_media_definition
            ON media_analysis_runs (media_id, analysis_definition)
            WHERE state IN ('pending', 'analyzing')
            """
        )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Restore lifetime uniqueness only when history remains single-run."""
    bind = op.get_bind()
    duplicates = bind.exec_driver_sql(
        """
        SELECT media_id, analysis_definition, COUNT(*) AS run_count
        FROM media_analysis_runs
        GROUP BY media_id, analysis_definition
        HAVING COUNT(*) > 1
        """
    ).fetchall()
    if duplicates:
        raise RuntimeError(_DOWNGRADE_HISTORY_MESSAGE)

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        op.execute("DROP INDEX IF EXISTS uq_media_analysis_runs_active_media_definition")
        with op.batch_alter_table("media_analysis_runs") as batch_op:
            batch_op.drop_constraint(
                "fk_media_analysis_runs_supersedes_run_id",
                type_="foreignkey",
            )
            batch_op.drop_constraint(
                "ck_media_analysis_runs_supersedes_run_id",
                type_="check",
            )
            batch_op.drop_column("supersedes_run_id")
            batch_op.create_unique_constraint(
                "uq_media_analysis_runs_media_definition",
                ["media_id", "analysis_definition"],
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
