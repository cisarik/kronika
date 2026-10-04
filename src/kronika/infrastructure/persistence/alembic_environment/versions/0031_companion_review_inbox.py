"""Add companion review inbox tables and publication origin (0031)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0031"
down_revision = "0030"
branch_labels = None
depends_on = None

_ORIGIN_SQL_0030 = "publication_origin IN ('legacy_backfill', 'admin_explicit')"
_ORIGIN_SQL_0031 = (
    "publication_origin IN "
    "('legacy_backfill', 'admin_explicit', 'companion_review')"
)
_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after companion-review inbox migration."
)
_DOWNGRADE_POPULATED_MESSAGE = (
    "Refusing companion-review inbox downgrade while review history or "
    "companion_review publications exist."
)


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
    """Create review tables, origin CHECK, and inbox/history indexes."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_content_publications") as batch_op:
            batch_op.drop_constraint(
                "ck_media_content_publications_origin",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_media_content_publications_origin",
                _ORIGIN_SQL_0031,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)

    op.create_table(
        "companion_review_open_states",
        sa.Column("actor_login_key", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("opened_run_id", sa.Text(), nullable=False),
        sa.Column("opened_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "actor_login_key",
            "media_id",
            name="pk_companion_review_open_states",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_companion_review_open_states_media_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["opened_run_id"],
            ["media_analysis_runs.id"],
            name="fk_companion_review_open_states_opened_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_companion_review_open_states_media_id_length",
        ),
        sa.CheckConstraint(
            "length(opened_run_id) = 36",
            name="ck_companion_review_open_states_opened_run_id_length",
        ),
        sa.CheckConstraint(
            _login_key_sql("actor_login_key"),
            name="ck_companion_review_open_states_actor_login_key",
        ),
        sa.CheckConstraint(
            "opened_at_ms >= 0",
            name="ck_companion_review_open_states_opened_at_ms_non_negative",
        ),
    )
    op.create_index(
        "ix_companion_review_open_states_opened_run_id",
        "companion_review_open_states",
        ["opened_run_id"],
    )

    op.create_table(
        "companion_review_field_sources",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("field_name", sa.Text(), nullable=False),
        sa.Column("analysis_run_id", sa.Text(), nullable=False),
        sa.Column("applied_by_login_key", sa.Text(), nullable=False),
        sa.Column("applied_at_ms", sa.Integer(), nullable=False),
        sa.Column("value_digest", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint(
            "media_id",
            "field_name",
            name="pk_companion_review_field_sources",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_companion_review_field_sources_media_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["analysis_run_id"],
            ["media_analysis_runs.id"],
            name="fk_companion_review_field_sources_analysis_run_id",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_companion_review_field_sources_media_id_length",
        ),
        sa.CheckConstraint(
            "length(analysis_run_id) = 36",
            name="ck_companion_review_field_sources_analysis_run_id_length",
        ),
        sa.CheckConstraint(
            "field_name IN ('display_title', 'tags', 'description')",
            name="ck_companion_review_field_sources_field_name",
        ),
        sa.CheckConstraint(
            _login_key_sql("applied_by_login_key"),
            name="ck_companion_review_field_sources_applied_by_login_key",
        ),
        sa.CheckConstraint(
            "applied_at_ms >= 0",
            name="ck_companion_review_field_sources_applied_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "length(value_digest) = 64 "
            "AND value_digest = lower(value_digest) "
            "AND value_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_companion_review_field_sources_value_digest",
        ),
    )
    op.create_index(
        "ix_companion_review_field_sources_analysis_run_id",
        "companion_review_field_sources",
        ["analysis_run_id"],
    )
    op.create_index(
        "ix_companion_review_successful_inbox",
        "media_analysis_runs",
        [
            "analysis_definition",
            "state",
            "analysis_profile",
            "completed_at_ms",
            "id",
            "media_id",
        ],
    )
    op.create_index(
        "ix_companion_review_per_media_history",
        "media_analysis_runs",
        [
            "media_id",
            "analysis_definition",
            "state",
            "analysis_profile",
            "completed_at_ms",
            "id",
        ],
    )


def downgrade() -> None:
    """Refuse populated review history; otherwise restore the 0030 contract."""
    bind = op.get_bind()
    open_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM companion_review_open_states"
    ).scalar_one()
    source_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM companion_review_field_sources"
    ).scalar_one()
    publication_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM media_content_publications "
        "WHERE publication_origin = 'companion_review'"
    ).scalar_one()
    if open_count or source_count or publication_count:
        raise RuntimeError(_DOWNGRADE_POPULATED_MESSAGE)

    op.drop_index(
        "ix_companion_review_per_media_history",
        table_name="media_analysis_runs",
    )
    op.drop_index(
        "ix_companion_review_successful_inbox",
        table_name="media_analysis_runs",
    )
    op.drop_index(
        "ix_companion_review_field_sources_analysis_run_id",
        table_name="companion_review_field_sources",
    )
    op.drop_table("companion_review_field_sources")
    op.drop_index(
        "ix_companion_review_open_states_opened_run_id",
        table_name="companion_review_open_states",
    )
    op.drop_table("companion_review_open_states")

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_content_publications") as batch_op:
            batch_op.drop_constraint(
                "ck_media_content_publications_origin",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_media_content_publications_origin",
                _ORIGIN_SQL_0030,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
