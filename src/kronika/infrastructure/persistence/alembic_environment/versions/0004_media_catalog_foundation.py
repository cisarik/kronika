"""Minimum persistent media catalog foundation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create logical media and physical media-location tables."""
    op.create_table(
        "logical_media",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("media_kind", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_logical_media_id_length"),
        sa.CheckConstraint(
            "media_kind IN ('video', 'animated_image')",
            name="ck_logical_media_kind",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_logical_media_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_logical_media_updated_at_ms_non_negative",
        ),
    )
    op.create_table(
        "physical_media_locations",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "media_id",
            sa.Text(),
            sa.ForeignKey("logical_media.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "library_id",
            sa.Text(),
            sa.ForeignKey("libraries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("availability", sa.Text(), nullable=False),
        sa.Column("observed_size_bytes", sa.Integer(), nullable=True),
        sa.Column("observed_mtime_ns", sa.Integer(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(id) = 36",
            name="ck_physical_media_locations_id_length",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_physical_media_locations_media_id_length",
        ),
        sa.CheckConstraint(
            "length(library_id) = 36",
            name="ck_physical_media_locations_library_id_length",
        ),
        sa.CheckConstraint(
            "length(relative_path) >= 1 AND length(relative_path) <= 4096",
            name="ck_physical_media_locations_relative_path_length",
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'offline', 'missing', 'unverified', 'archived')",
            name="ck_physical_media_locations_availability",
        ),
        sa.CheckConstraint(
            "observed_size_bytes IS NULL OR observed_size_bytes >= 0",
            name="ck_physical_media_locations_observed_size_non_negative",
        ),
        sa.CheckConstraint(
            "observed_mtime_ns IS NULL OR observed_mtime_ns >= 0",
            name="ck_physical_media_locations_observed_mtime_non_negative",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_physical_media_locations_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_physical_media_locations_updated_at_ms_non_negative",
        ),
        sa.UniqueConstraint(
            "library_id",
            "relative_path",
            name="uq_physical_media_locations_library_path",
        ),
    )
    op.create_index(
        "ix_physical_media_locations_media_id",
        "physical_media_locations",
        ["media_id"],
    )


def downgrade() -> None:
    """Remove only the media catalog objects introduced by revision 0004."""
    op.drop_index(
        "ix_physical_media_locations_media_id",
        table_name="physical_media_locations",
    )
    op.drop_table("physical_media_locations")
    op.drop_table("logical_media")
