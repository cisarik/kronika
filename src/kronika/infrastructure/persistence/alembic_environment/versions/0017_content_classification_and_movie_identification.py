"""Add content classification, acquisition source, genres, and analysis provenance."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None

_CONTENT_CATEGORY_SQL = "content_category IN ('general', 'meme', 'movie')"
_ACQUISITION_SOURCE_SQL = (
    "acquisition_source IN ("
    "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim'"
    ")"
)
_GENRE_KEY_SQL = (
    "genre_key IN ("
    "'drama', 'comedy', 'sci-fi', 'thriller', 'horror', 'action', 'adventure', "
    "'documentary', 'animation', 'family', 'romance', 'crime', 'fantasy', 'mystery'"
    ")"
)
_ANALYSIS_PROFILE_SQL = (
    "analysis_profile IS NULL OR analysis_profile IN "
    "('generic_media', 'movie_identification')"
)
_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after content-classification migration."
)


def upgrade() -> None:
    """Add orthogonal classification fields and analysis-profile provenance."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.add_column(
                sa.Column(
                    "content_category",
                    sa.Text(),
                    nullable=False,
                    server_default="general",
                )
            )
            batch_op.add_column(
                sa.Column(
                    "acquisition_source",
                    sa.Text(),
                    nullable=False,
                    server_default="unknown",
                )
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_content_category",
                _CONTENT_CATEGORY_SQL,
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_acquisition_source",
                _ACQUISITION_SOURCE_SQL,
            )

        op.create_table(
            "media_genres",
            sa.Column("media_id", sa.Text(), nullable=False),
            sa.Column("genre_key", sa.Text(), nullable=False),
            sa.Column("position", sa.Integer(), nullable=False),
            sa.CheckConstraint(
                "length(media_id) = 36",
                name="ck_media_genres_media_id_length",
            ),
            sa.CheckConstraint(_GENRE_KEY_SQL, name="ck_media_genres_genre_key"),
            sa.CheckConstraint(
                "position >= 0 AND position < 8",
                name="ck_media_genres_position_range",
            ),
            sa.ForeignKeyConstraint(
                ["media_id"],
                ["media_metadata.media_id"],
                name="fk_media_genres_media_id",
                ondelete="RESTRICT",
            ),
            sa.PrimaryKeyConstraint("media_id", "genre_key", name="pk_media_genres"),
            sa.UniqueConstraint(
                "media_id",
                "position",
                name="uq_media_genres_media_position",
            ),
        )
        op.create_index("ix_media_genres_genre_key", "media_genres", ["genre_key"])

        with op.batch_alter_table("media_analysis_runs") as batch_op:
            batch_op.add_column(sa.Column("analysis_profile", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("reasoning_enabled", sa.Integer(), nullable=True))
            batch_op.add_column(sa.Column("derivative_strategy", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("derivative_count", sa.Integer(), nullable=True))
            batch_op.add_column(
                sa.Column("provider_submission_occurred", sa.Integer(), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_media_analysis_runs_analysis_profile",
                _ANALYSIS_PROFILE_SQL,
            )
            batch_op.create_check_constraint(
                "ck_media_analysis_runs_reasoning_enabled",
                "reasoning_enabled IS NULL OR reasoning_enabled IN (0, 1)",
            )
            batch_op.create_check_constraint(
                "ck_media_analysis_runs_provider_submission",
                "provider_submission_occurred IS NULL OR provider_submission_occurred IN (0, 1)",
            )
            batch_op.create_check_constraint(
                "ck_media_analysis_runs_derivative_count",
                "derivative_count IS NULL OR (derivative_count >= 0 AND derivative_count <= 16)",
            )

        # Preserve existing analyzed runs as generic media with reasoning off.
        # Do not invent movie category or YouTube source for any historical row.
        op.execute(
            """
            UPDATE media_analysis_runs
            SET analysis_profile = 'generic_media',
                reasoning_enabled = 0,
                derivative_strategy = 'representative_frames_jpeg_v1',
                provider_submission_occurred = CASE
                    WHEN state = 'analyzed' THEN 1
                    WHEN state = 'failed' AND error_code NOT IN (
                        'PREPARATION_UNAVAILABLE', 'PREPARATION_FAILED'
                    ) THEN 1
                    ELSE 0
                END
            WHERE analysis_definition = 'automatic_post_catalog'
            """
        )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Remove classification and provenance columns introduced by revision 0017."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_analysis_runs") as batch_op:
            batch_op.drop_constraint(
                "ck_media_analysis_runs_derivative_count", type_="check"
            )
            batch_op.drop_constraint(
                "ck_media_analysis_runs_provider_submission", type_="check"
            )
            batch_op.drop_constraint(
                "ck_media_analysis_runs_reasoning_enabled", type_="check"
            )
            batch_op.drop_constraint(
                "ck_media_analysis_runs_analysis_profile", type_="check"
            )
            batch_op.drop_column("provider_submission_occurred")
            batch_op.drop_column("derivative_count")
            batch_op.drop_column("derivative_strategy")
            batch_op.drop_column("reasoning_enabled")
            batch_op.drop_column("analysis_profile")

        op.drop_index("ix_media_genres_genre_key", table_name="media_genres")
        op.drop_table("media_genres")

        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.drop_constraint(
                "ck_media_metadata_acquisition_source", type_="check"
            )
            batch_op.drop_constraint(
                "ck_media_metadata_content_category", type_="check"
            )
            batch_op.drop_column("acquisition_source")
            batch_op.drop_column("content_category")
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
