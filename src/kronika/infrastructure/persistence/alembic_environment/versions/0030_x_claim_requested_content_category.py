"""Add nullable requested content category on X post claims (0030)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None

_CATEGORY_SQL = (
    "requested_content_category IS NULL OR "
    "requested_content_category IN ('general', 'meme', 'movie', 'youtube')"
)
_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after X claim requested-category migration."
)
_DOWNGRADE_CATEGORY_MESSAGE = (
    "Refusing X requested-category downgrade while non-null "
    "requested_content_category rows exist."
)


def upgrade() -> None:
    """Add nullable claim-level category with no backfill or server default."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("x_post_claims") as batch_op:
            batch_op.add_column(
                sa.Column("requested_content_category", sa.Text(), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_x_post_claims_requested_content_category",
                _CATEGORY_SQL,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Refuse populated category rows; otherwise restore the 0029 contract."""
    bind = op.get_bind()
    populated = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM x_post_claims "
        "WHERE requested_content_category IS NOT NULL"
    ).scalar_one()
    if populated:
        raise RuntimeError(_DOWNGRADE_CATEGORY_MESSAGE)

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("x_post_claims") as batch_op:
            batch_op.drop_constraint(
                "ck_x_post_claims_requested_content_category",
                type_="check",
            )
            batch_op.drop_column("requested_content_category")
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
