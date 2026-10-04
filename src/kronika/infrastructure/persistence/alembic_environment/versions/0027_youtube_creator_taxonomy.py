"""Add YouTube content category and structured creator attribution."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None

_PREVIOUS_CONTENT_CATEGORY_SQL = "content_category IN ('general', 'meme', 'movie')"
_CONTENT_CATEGORY_SQL = (
    "content_category IN ('general', 'meme', 'movie', 'youtube')"
)
_CREATOR_ATTRIBUTION_SQL = (
    "("
    "creator_attribution_kind IS NULL "
    "AND creator_stable_id IS NULL "
    "AND creator_handle IS NULL "
    "AND creator_display_name IS NULL"
    ") OR ("
    "creator_attribution_kind IS NOT NULL "
    "AND creator_attribution_kind IN ('youtube_channel', 'x_author') "
    "AND ("
    "creator_stable_id IS NOT NULL "
    "OR creator_handle IS NOT NULL "
    "OR creator_display_name IS NOT NULL"
    ") "
    "AND (creator_stable_id IS NULL OR ("
    "length(creator_stable_id) >= 1 AND length(creator_stable_id) <= 128"
    ")) "
    "AND (creator_handle IS NULL OR ("
    "length(creator_handle) >= 1 AND length(creator_handle) <= 64 "
    "AND creator_handle = lower(creator_handle) "
    "AND substr(creator_handle, 1, 1) != '@'"
    ")) "
    "AND (creator_display_name IS NULL OR ("
    "length(creator_display_name) >= 1 AND length(creator_display_name) <= 200"
    "))"
    ")"
)
_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after YouTube creator-taxonomy migration."
)
_DOWNGRADE_YOUTUBE_MESSAGE = (
    "Refusing creator-taxonomy downgrade while content_category = youtube "
    "or non-null creator attribution exists."
)


def upgrade() -> None:
    """Extend category constraint and add nullable creator attribution fields."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.add_column(
                sa.Column("creator_attribution_kind", sa.Text(), nullable=True)
            )
            batch_op.add_column(sa.Column("creator_stable_id", sa.Text(), nullable=True))
            batch_op.add_column(sa.Column("creator_handle", sa.Text(), nullable=True))
            batch_op.add_column(
                sa.Column("creator_display_name", sa.Text(), nullable=True)
            )
            batch_op.drop_constraint(
                "ck_media_metadata_content_category",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_content_category",
                _CONTENT_CATEGORY_SQL,
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_creator_attribution",
                _CREATOR_ATTRIBUTION_SQL,
            )
            batch_op.create_index(
                "ix_media_metadata_creator_stable",
                ["creator_attribution_kind", "creator_stable_id", "media_id"],
                unique=False,
            )
            batch_op.create_index(
                "ix_media_metadata_creator_handle",
                ["creator_attribution_kind", "creator_handle", "media_id"],
                unique=False,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Refuse data loss; otherwise remove additive creator taxonomy fields."""
    bind = op.get_bind()
    blocked_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM media_metadata WHERE "
        "content_category = 'youtube' "
        "OR creator_attribution_kind IS NOT NULL "
        "OR creator_stable_id IS NOT NULL "
        "OR creator_handle IS NOT NULL "
        "OR creator_display_name IS NOT NULL"
    ).scalar_one()
    if blocked_count:
        raise RuntimeError(_DOWNGRADE_YOUTUBE_MESSAGE)

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.drop_index("ix_media_metadata_creator_handle")
            batch_op.drop_index("ix_media_metadata_creator_stable")
            batch_op.drop_constraint(
                "ck_media_metadata_creator_attribution",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_media_metadata_content_category",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_content_category",
                _PREVIOUS_CONTENT_CATEGORY_SQL,
            )
            batch_op.drop_column("creator_display_name")
            batch_op.drop_column("creator_handle")
            batch_op.drop_column("creator_stable_id")
            batch_op.drop_column("creator_attribution_kind")
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
