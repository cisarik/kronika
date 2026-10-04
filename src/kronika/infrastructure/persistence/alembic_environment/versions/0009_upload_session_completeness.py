"""Upload-session completeness invariants."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

_COMPLETE_UPLOAD_SESSION_STATES = (
    "received",
    "validating",
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
    "rejected",
)


def upgrade() -> None:
    """Add upload-session completeness constraints after validating existing rows."""
    _fail_if_invalid_upload_sessions_exist()
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.create_check_constraint(
            "ck_upload_sessions_created_received_size_zero",
            sa.or_(sa.column("state") != "created", sa.column("received_size_bytes") == 0),
        )
        batch_op.create_check_constraint(
            "ck_upload_sessions_complete_states_received_size_exact",
            sa.or_(
                ~sa.column("state").in_(_COMPLETE_UPLOAD_SESSION_STATES),
                sa.column("received_size_bytes") == sa.column("declared_size_bytes"),
            ),
        )


def downgrade() -> None:
    """Remove only upload-session completeness constraints."""
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.drop_constraint(
            "ck_upload_sessions_complete_states_received_size_exact",
            type_="check",
        )
        batch_op.drop_constraint(
            "ck_upload_sessions_created_received_size_zero",
            type_="check",
        )


def _fail_if_invalid_upload_sessions_exist() -> None:
    connection = op.get_bind()
    invalid_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM upload_sessions
            WHERE (state = 'created' AND received_size_bytes != 0)
               OR (
                   state IN (
                       'received',
                       'validating',
                       'duplicate_pending',
                       'publish_pending',
                       'published',
                       'cataloged',
                       'rejected'
                   )
                   AND received_size_bytes != declared_size_bytes
               )
            """
        )
    ).scalar_one()
    if invalid_count:
        raise RuntimeError("Upload session completeness migration failed.")
