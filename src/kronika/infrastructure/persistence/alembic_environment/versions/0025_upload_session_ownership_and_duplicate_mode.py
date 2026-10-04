"""Add durable upload-session ownership and duplicate-resolution mode."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None

_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after upload-session ownership migration."
)


def upgrade() -> None:
    """Persist owner login key and creation-time duplicate privacy mode."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("upload_sessions") as batch_op:
            batch_op.add_column(
                sa.Column("created_by_login_key", sa.Text(), nullable=True)
            )
            batch_op.add_column(
                sa.Column(
                    "duplicate_resolution_mode",
                    sa.Text(),
                    nullable=False,
                    server_default="explicit",
                )
            )
            batch_op.create_check_constraint(
                "ck_upload_sessions_created_by_login_key",
                "created_by_login_key IS NULL OR ("
                "length(created_by_login_key) >= 1 "
                "AND length(created_by_login_key) <= 254 "
                "AND created_by_login_key = lower(created_by_login_key) "
                "AND instr(created_by_login_key, ' ') = 0 "
                "AND instr(created_by_login_key, char(9)) = 0 "
                "AND instr(created_by_login_key, char(10)) = 0 "
                "AND instr(created_by_login_key, char(13)) = 0)",
            )
            batch_op.create_check_constraint(
                "ck_upload_sessions_duplicate_resolution_mode",
                "duplicate_resolution_mode IN ('explicit', 'silent_keep_separate')",
            )
            batch_op.create_index(
                "ix_upload_sessions_created_by_login_key",
                ["created_by_login_key"],
                unique=False,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Remove only ownership and duplicate-mode fields introduced by 0025."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("upload_sessions") as batch_op:
            batch_op.drop_index("ix_upload_sessions_created_by_login_key")
            batch_op.drop_constraint(
                "ck_upload_sessions_duplicate_resolution_mode",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_upload_sessions_created_by_login_key",
                type_="check",
            )
            batch_op.drop_column("duplicate_resolution_mode")
            batch_op.drop_column("created_by_login_key")
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
