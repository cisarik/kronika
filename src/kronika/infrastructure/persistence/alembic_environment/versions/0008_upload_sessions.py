"""Durable upload-session foundation."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None

_UPLOAD_SESSION_STATES = (
    "created",
    "receiving",
    "received",
    "validating",
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
    "rejected",
    "cancelled",
    "expired",
    "failed",
)


def upgrade() -> None:
    """Create durable upload sessions without media publication state."""
    op.create_table(
        "upload_sessions",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("storage_key", sa.Text(), nullable=False),
        sa.Column("display_filename", sa.Text(), nullable=False),
        sa.Column("declared_size_bytes", sa.Integer(), nullable=False),
        sa.Column("received_size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_algorithm", sa.Text(), nullable=True),
        sa.Column("checksum_hex", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("expires_at_ms", sa.Integer(), nullable=False),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_upload_sessions_id_length"),
        sa.CheckConstraint(
            "state IN (" + ", ".join(f"'{state}'" for state in _UPLOAD_SESSION_STATES) + ")",
            name="ck_upload_sessions_state",
        ),
        sa.CheckConstraint(
            "length(storage_key) >= 16 AND length(storage_key) <= 128",
            name="ck_upload_sessions_storage_key_length",
        ),
        sa.CheckConstraint(
            "storage_key = lower(storage_key) "
            "AND storage_key NOT GLOB '*[^a-z0-9._-]*' "
            "AND storage_key NOT GLOB '*/*' "
            "AND storage_key NOT GLOB '*\\*'",
            name="ck_upload_sessions_storage_key_opaque",
        ),
        sa.CheckConstraint(
            "length(display_filename) >= 1 AND length(display_filename) <= 255",
            name="ck_upload_sessions_display_filename_length",
        ),
        sa.CheckConstraint(
            "display_filename NOT GLOB '*/*' AND display_filename NOT GLOB '*\\*'",
            name="ck_upload_sessions_display_filename_not_path",
        ),
        sa.CheckConstraint(
            "declared_size_bytes > 0",
            name="ck_upload_sessions_declared_size_positive",
        ),
        sa.CheckConstraint(
            "received_size_bytes >= 0",
            name="ck_upload_sessions_received_size_non_negative",
        ),
        sa.CheckConstraint(
            "received_size_bytes <= declared_size_bytes",
            name="ck_upload_sessions_received_size_not_over_declared",
        ),
        sa.CheckConstraint(
            "(checksum_algorithm IS NULL AND checksum_hex IS NULL) "
            "OR (checksum_algorithm = 'sha256' AND checksum_hex IS NOT NULL)",
            name="ck_upload_sessions_checksum_pair",
        ),
        sa.CheckConstraint(
            "checksum_hex IS NULL OR ("
            "length(checksum_hex) = 64 "
            "AND checksum_hex = lower(checksum_hex) "
            "AND checksum_hex NOT GLOB '*[^0-9a-f]*')",
            name="ck_upload_sessions_checksum_hex",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_upload_sessions_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_upload_sessions_updated_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_upload_sessions_updated_not_before_created",
        ),
        sa.CheckConstraint(
            "expires_at_ms > created_at_ms",
            name="ck_upload_sessions_expires_after_created",
        ),
        sa.CheckConstraint(
            "failure_code IS NULL OR ("
            "length(failure_code) >= 1 "
            "AND length(failure_code) <= 80 "
            "AND failure_code NOT GLOB '*[^A-Z0-9_]*')",
            name="ck_upload_sessions_failure_code_sanitized",
        ),
        sa.CheckConstraint("version >= 0", name="ck_upload_sessions_version_non_negative"),
        sa.UniqueConstraint("storage_key", name="uq_upload_sessions_storage_key"),
    )
    op.create_index("ix_upload_sessions_state", "upload_sessions", ["state"])
    op.create_index("ix_upload_sessions_expires_at_ms", "upload_sessions", ["expires_at_ms"])
    op.create_index(
        "ix_upload_sessions_state_expires_at_ms",
        "upload_sessions",
        ["state", "expires_at_ms"],
    )


def downgrade() -> None:
    """Remove only upload-session objects introduced by revision 0008."""
    op.drop_index("ix_upload_sessions_state_expires_at_ms", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_expires_at_ms", table_name="upload_sessions")
    op.drop_index("ix_upload_sessions_state", table_name="upload_sessions")
    op.drop_table("upload_sessions")
