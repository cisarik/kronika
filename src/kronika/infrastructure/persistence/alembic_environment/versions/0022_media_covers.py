"""Add durable manually selected accepted-cover state."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None

_UNSAFE_DOWNGRADE_MESSAGE = (
    "Cannot downgrade durable covers while accepted media covers exist."
)


def upgrade() -> None:
    """Create the sparse accepted-cover relation for logical media."""
    op.create_table(
        "media_covers",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("source_location_id", sa.Text(), nullable=True),
        sa.Column("source_reference", sa.Text(), nullable=False),
        sa.Column("source_kind", sa.Text(), nullable=False),
        sa.Column("source_timestamp_ms", sa.Integer(), nullable=False),
        sa.Column("source_size_bytes", sa.Integer(), nullable=False),
        sa.Column("source_mtime_ns", sa.Integer(), nullable=True),
        sa.Column("source_duration_ms", sa.Integer(), nullable=True),
        sa.Column("source_observation_version", sa.Text(), nullable=False),
        sa.Column("source_observation_digest", sa.Text(), nullable=False),
        sa.Column("artifact_profile", sa.Text(), nullable=False),
        sa.Column("artifact_media_type", sa.Text(), nullable=False),
        sa.Column("artifact_digest", sa.Text(), nullable=False),
        sa.Column("artifact_width", sa.Integer(), nullable=False),
        sa.Column("artifact_height", sa.Integer(), nullable=False),
        sa.Column("artifact_byte_size", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("accepted_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_media_covers_media_id_length",
        ),
        sa.CheckConstraint(
            "source_location_id IS NULL OR length(source_location_id) = 36",
            name="ck_media_covers_source_location_id_length",
        ),
        sa.CheckConstraint(
            "substr(source_reference, 1, 9) = 'location:' "
            "AND length(source_reference) = 45",
            name="ck_media_covers_source_reference_shape",
        ),
        sa.CheckConstraint(
            "source_kind IN ('gif', 'mp4')",
            name="ck_media_covers_source_kind",
        ),
        sa.CheckConstraint(
            "source_timestamp_ms >= 0",
            name="ck_media_covers_source_timestamp_non_negative",
        ),
        sa.CheckConstraint(
            "source_size_bytes > 0",
            name="ck_media_covers_source_size_positive",
        ),
        sa.CheckConstraint(
            "source_mtime_ns IS NULL OR source_mtime_ns >= 0",
            name="ck_media_covers_source_mtime_non_negative",
        ),
        sa.CheckConstraint(
            "source_duration_ms IS NULL OR source_duration_ms >= 0",
            name="ck_media_covers_source_duration_non_negative",
        ),
        sa.CheckConstraint(
            "source_observation_version = 'cover-source-observation-v1'",
            name="ck_media_covers_source_observation_version",
        ),
        sa.CheckConstraint(
            "length(source_observation_digest) = 64 "
            "AND source_observation_digest = lower(source_observation_digest) "
            "AND source_observation_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_media_covers_source_observation_digest",
        ),
        sa.CheckConstraint(
            "artifact_profile = 'durable-cover-jpeg-v1'",
            name="ck_media_covers_artifact_profile",
        ),
        sa.CheckConstraint(
            "artifact_media_type = 'image/jpeg'",
            name="ck_media_covers_artifact_media_type",
        ),
        sa.CheckConstraint(
            "length(artifact_digest) = 64 "
            "AND artifact_digest = lower(artifact_digest) "
            "AND artifact_digest NOT GLOB '*[^0-9a-f]*'",
            name="ck_media_covers_artifact_digest",
        ),
        sa.CheckConstraint(
            "artifact_width > 0",
            name="ck_media_covers_artifact_width_positive",
        ),
        sa.CheckConstraint(
            "artifact_height > 0",
            name="ck_media_covers_artifact_height_positive",
        ),
        sa.CheckConstraint(
            "artifact_byte_size > 0",
            name="ck_media_covers_artifact_byte_size_positive",
        ),
        sa.CheckConstraint(
            "revision >= 1",
            name="ck_media_covers_revision_positive",
        ),
        sa.CheckConstraint(
            "accepted_at_ms >= 0",
            name="ck_media_covers_accepted_at_non_negative",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_media_covers_media_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["source_location_id"],
            ["physical_media_locations.id"],
            name="fk_media_covers_source_location_id",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("media_id", name="pk_media_covers"),
    )
    op.create_index(
        "ix_media_covers_source_location_id",
        "media_covers",
        ["source_location_id"],
    )


def downgrade() -> None:
    """Refuse a downgrade that would silently discard accepted cover state."""
    bind = op.get_bind()
    accepted_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM media_covers"
    ).scalar_one()
    if accepted_count:
        raise RuntimeError(_UNSAFE_DOWNGRADE_MESSAGE)
    op.drop_index("ix_media_covers_source_location_id", table_name="media_covers")
    op.drop_table("media_covers")
