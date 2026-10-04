"""Add durable content-publication state with legacy visibility backfill."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None

_UNSAFE_DOWNGRADE_MESSAGE = (
    "Cannot downgrade content publication while unpublished logical media exist."
)


def upgrade() -> None:
    """Create publication state and preserve all existing Gallery visibility."""
    op.create_table(
        "media_content_publications",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("published_at_ms", sa.Integer(), nullable=False),
        sa.Column("publication_origin", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_media_content_publications_media_id_length",
        ),
        sa.CheckConstraint(
            "published_at_ms >= 0",
            name="ck_media_content_publications_published_at_non_negative",
        ),
        sa.CheckConstraint(
            "publication_origin IN ('legacy_backfill', 'admin_explicit')",
            name="ck_media_content_publications_origin",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_media_content_publications_media_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "media_id",
            name="pk_media_content_publications",
        ),
    )
    op.create_index(
        "ix_media_content_publications_published_at",
        "media_content_publications",
        ["published_at_ms", "media_id"],
    )
    op.execute(
        """
        INSERT INTO media_content_publications (
            media_id,
            published_at_ms,
            publication_origin
        )
        SELECT
            id,
            created_at_ms,
            'legacy_backfill'
        FROM logical_media
        """
    )


def downgrade() -> None:
    """Refuse a downgrade that would expose currently unpublished content."""
    bind = op.get_bind()
    unpublished_count = bind.exec_driver_sql(
        """
        SELECT COUNT(*)
        FROM logical_media AS media
        LEFT JOIN media_content_publications AS publication
          ON publication.media_id = media.id
        WHERE publication.media_id IS NULL
        """
    ).scalar_one()
    if unpublished_count:
        raise RuntimeError(_UNSAFE_DOWNGRADE_MESSAGE)
    op.drop_index(
        "ix_media_content_publications_published_at",
        table_name="media_content_publications",
    )
    op.drop_table("media_content_publications")
