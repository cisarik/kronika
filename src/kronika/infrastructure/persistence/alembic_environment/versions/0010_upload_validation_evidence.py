"""Upload validation evidence invariants."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

_VALIDATED_UPLOAD_SESSION_STATES = (
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
)


def upgrade() -> None:
    """Add normalized upload-validation evidence after validating existing rows."""
    _fail_if_incompatible_advanced_upload_sessions_exist()
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.add_column(sa.Column("validated_media_kind", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("validated_format", sa.Text(), nullable=True))
        batch_op.create_check_constraint(
            "ck_upload_sessions_validation_evidence_pair",
            "(validated_media_kind IS NULL AND validated_format IS NULL) "
            "OR (validated_media_kind = 'animated_image' AND validated_format = 'gif') "
            "OR (validated_media_kind = 'video' AND validated_format = 'mp4')",
        )
        batch_op.create_check_constraint(
            "ck_upload_sessions_validated_states_have_evidence",
            sa.or_(
                ~sa.column("state").in_(_VALIDATED_UPLOAD_SESSION_STATES),
                (
                    (sa.column("received_size_bytes") == sa.column("declared_size_bytes"))
                    & (sa.column("checksum_algorithm") == "sha256")
                    & sa.column("checksum_hex").is_not(None)
                    & sa.column("validated_media_kind").is_not(None)
                    & sa.column("validated_format").is_not(None)
                ),
            ),
        )


def downgrade() -> None:
    """Remove only upload-validation evidence constraints and columns."""
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.drop_constraint(
            "ck_upload_sessions_validated_states_have_evidence",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_upload_sessions_validation_evidence_pair",
            type_="check",
        )
        batch_op.drop_column("validated_format")
        batch_op.drop_column("validated_media_kind")


def _fail_if_incompatible_advanced_upload_sessions_exist() -> None:
    connection = op.get_bind()
    invalid_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM upload_sessions
            WHERE state IN (
                'duplicate_pending',
                'publish_pending',
                'published',
                'cataloged'
            )
            """
        )
    ).scalar_one()
    if invalid_count:
        raise RuntimeError("Upload validation evidence migration failed.")
