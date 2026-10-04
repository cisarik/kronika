"""Persistent media metadata and canonical tags."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Create canonical tag and sparse media metadata tables."""
    op.create_table(
        "canonical_tags",
        sa.Column("key", sa.Text(), primary_key=True, nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(key) >= 1 AND length(key) <= 64",
            name="ck_canonical_tags_key_length",
        ),
        sa.CheckConstraint("key = lower(key)", name="ck_canonical_tags_key_lowercase"),
        sa.CheckConstraint(
            "key GLOB '[a-z]*' "
            "AND key NOT GLOB '*[^a-z0-9-]*' "
            "AND key NOT LIKE '%--%' "
            "AND substr(key, length(key), 1) != '-'",
            name="ck_canonical_tags_key_slug",
        ),
        sa.CheckConstraint(
            "length(display_name) >= 1 AND length(display_name) <= 80",
            name="ck_canonical_tags_display_name_length",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_canonical_tags_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_canonical_tags_updated_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_canonical_tags_updated_not_before_created",
        ),
    )
    op.create_table(
        "media_metadata",
        sa.Column(
            "media_id",
            sa.Text(),
            sa.ForeignKey("logical_media.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(media_id) = 36", name="ck_media_metadata_media_id_length"),
        sa.CheckConstraint(
            "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
            name="ck_media_metadata_title_length",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_media_metadata_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_media_metadata_updated_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_media_metadata_updated_not_before_created",
        ),
    )
    op.create_table(
        "media_canonical_tags",
        sa.Column(
            "media_id",
            sa.Text(),
            sa.ForeignKey("media_metadata.media_id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "tag_key",
            sa.Text(),
            sa.ForeignKey("canonical_tags.key", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("media_id", "tag_key", name="pk_media_canonical_tags"),
        sa.UniqueConstraint("media_id", "position", name="uq_media_canonical_tags_media_position"),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_media_canonical_tags_media_id_length",
        ),
        sa.CheckConstraint(
            "length(tag_key) >= 1 AND length(tag_key) <= 64",
            name="ck_media_canonical_tags_tag_key_length",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 32",
            name="ck_media_canonical_tags_position_range",
        ),
    )


def downgrade() -> None:
    """Remove only metadata and canonical-tag objects introduced by revision 0005."""
    op.drop_table("media_canonical_tags")
    op.drop_table("media_metadata")
    op.drop_table("canonical_tags")
