"""Support timeless still-image sources in the durable manual cover relation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None

_IMAGE_COVER_DOWNGRADE_MESSAGE = (
    "Cannot downgrade still-image covers while image-source covers exist."
)
_FK_FAILURE_MESSAGE = "Foreign key check failed after still-image cover migration."


def upgrade() -> None:
    """Accept the still-image source kind without rewriting existing rows.

    Still-image covers are timeless: they keep the canonical non-null
    ``source_timestamp_ms`` of ``0`` and a nullable ``source_duration_ms``, so
    the only schema change is widening the normalized ``source_kind`` check
    constraint to accept ``'image'``. Existing GIF and MP4 cover rows are
    preserved byte-for-byte at the logical-data level.
    """
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_covers") as batch_op:
            batch_op.drop_constraint("ck_media_covers_source_kind", type_="check")
            batch_op.create_check_constraint(
                "ck_media_covers_source_kind",
                "source_kind IN ('gif', 'mp4', 'image')",
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Restore the GIF/MP4-only source-kind constraint.

    The downgrade fails closed whenever an image-source cover row exists,
    because the restored constraint could not represent it safely.
    """
    connection = op.get_bind()
    image_cover_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM media_covers WHERE source_kind = 'image'")
    ).scalar_one()
    if image_cover_count:
        raise RuntimeError(_IMAGE_COVER_DOWNGRADE_MESSAGE)
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_covers") as batch_op:
            batch_op.drop_constraint("ck_media_covers_source_kind", type_="check")
            batch_op.create_check_constraint(
                "ck_media_covers_source_kind",
                "source_kind IN ('gif', 'mp4')",
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
