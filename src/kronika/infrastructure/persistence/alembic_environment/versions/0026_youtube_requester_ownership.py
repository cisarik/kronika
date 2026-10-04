"""Add immutable requester ownership to YouTube acquisition claims."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None

_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after YouTube requester-ownership migration."
)
_DOWNGRADE_OWNERSHIP_MESSAGE = (
    "Refusing YouTube requester-ownership downgrade while requester-owned "
    "claims exist."
)
_ACTIVE_STATE_PREDICATE = (
    "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
    "'downloaded', 'handoff', 'handed_off')"
)
_LOGIN_KEY_CHECK = (
    "created_by_login_key IS NULL OR ("
    "length(created_by_login_key) >= 1 "
    "AND length(created_by_login_key) <= 254 "
    "AND created_by_login_key = lower(created_by_login_key) "
    "AND instr(created_by_login_key, ' ') = 0 "
    "AND instr(created_by_login_key, char(9)) = 0 "
    "AND instr(created_by_login_key, char(10)) = 0 "
    "AND instr(created_by_login_key, char(13)) = 0)"
)


def upgrade() -> None:
    """Stamp nullable requester ownership and per-audience active uniqueness."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("youtube_acquisition_claims") as batch_op:
            batch_op.add_column(
                sa.Column("created_by_login_key", sa.Text(), nullable=True)
            )
            batch_op.create_check_constraint(
                "ck_youtube_claims_created_by_login_key",
                _LOGIN_KEY_CHECK,
            )
            batch_op.drop_index("uq_youtube_claims_active_source_identity")
            batch_op.create_index(
                "ix_youtube_claims_created_by_login_key",
                ["created_by_login_key"],
                unique=False,
            )
            batch_op.create_index(
                "ix_youtube_claims_owner_updated",
                ["created_by_login_key", "updated_at_ms", "id"],
                unique=False,
            )
            batch_op.create_index(
                "ix_youtube_claims_media_requester_live",
                ["media_id", "created_by_login_key"],
                unique=False,
            )
            batch_op.create_index(
                "uq_youtube_claims_active_source_admin",
                ["extractor_key", "youtube_video_id"],
                unique=True,
                sqlite_where=sa.text(
                    f"{_ACTIVE_STATE_PREDICATE} AND created_by_login_key IS NULL"
                ),
            )
            batch_op.create_index(
                "uq_youtube_claims_active_source_requester",
                ["extractor_key", "youtube_video_id", "created_by_login_key"],
                unique=True,
                sqlite_where=sa.text(
                    f"{_ACTIVE_STATE_PREDICATE} AND created_by_login_key IS NOT NULL"
                ),
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Refuse ownership loss; restore global active-source uniqueness only when safe."""
    bind = op.get_bind()
    owned_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM youtube_acquisition_claims "
        "WHERE created_by_login_key IS NOT NULL"
    ).scalar_one()
    if owned_count:
        raise RuntimeError(_DOWNGRADE_OWNERSHIP_MESSAGE)

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("youtube_acquisition_claims") as batch_op:
            batch_op.drop_index("uq_youtube_claims_active_source_requester")
            batch_op.drop_index("uq_youtube_claims_active_source_admin")
            batch_op.drop_index("ix_youtube_claims_media_requester_live")
            batch_op.drop_index("ix_youtube_claims_owner_updated")
            batch_op.drop_index("ix_youtube_claims_created_by_login_key")
            batch_op.drop_constraint(
                "ck_youtube_claims_created_by_login_key",
                type_="check",
            )
            batch_op.drop_column("created_by_login_key")
            batch_op.create_index(
                "uq_youtube_claims_active_source_identity",
                ["extractor_key", "youtube_video_id"],
                unique=True,
                sqlite_where=sa.text(_ACTIVE_STATE_PREDICATE),
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
