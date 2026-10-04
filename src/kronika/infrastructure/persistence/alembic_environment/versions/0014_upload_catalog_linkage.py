"""Link verified upload publications to catalog media and locations."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add nullable catalog linkage without inventing historical ownership."""
    with op.batch_alter_table("upload_publications") as batch_op:
        batch_op.add_column(sa.Column("media_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("media_location_id", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_upload_publications_catalog_linkage",
            "(media_id IS NULL AND media_location_id IS NULL) "
            "OR (media_id IS NOT NULL AND media_location_id IS NOT NULL)",
        )
        batch_op.create_check_constraint(
            "ck_upload_publications_media_id_length",
            "media_id IS NULL OR length(media_id) = 36",
        )
        batch_op.create_check_constraint(
            "ck_upload_publications_media_location_id_length",
            "media_location_id IS NULL OR length(media_location_id) = 36",
        )
        batch_op.create_foreign_key(
            "fk_upload_publications_media_id",
            "logical_media",
            ["media_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_upload_publications_media_location_id",
            "physical_media_locations",
            ["media_location_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_upload_publications_media_id",
            ["media_id"],
        )
        batch_op.create_unique_constraint(
            "uq_upload_publications_media_location_id",
            ["media_location_id"],
        )


def downgrade() -> None:
    """Remove only catalog linkage columns introduced by revision 0014."""
    with op.batch_alter_table("upload_publications") as batch_op:
        batch_op.drop_constraint(
            "uq_upload_publications_media_location_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_upload_publications_media_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_upload_publications_media_location_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_upload_publications_media_id",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "ck_upload_publications_media_location_id_length",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_upload_publications_media_id_length",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_upload_publications_catalog_linkage",
            type_="check",
        )
        batch_op.drop_column("media_location_id")
        batch_op.drop_column("media_id")
