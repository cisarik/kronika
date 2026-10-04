"""Add durable privileged-action security audit events."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None

_DOWNGRADE_AUDIT_MESSAGE = (
    "Cannot downgrade security audit storage while audit events exist."
)


def upgrade() -> None:
    """Create the append-only privileged-action audit table and indexes."""
    op.create_table(
        "security_audit_events",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("actor_login", sa.Text(), nullable=False),
        sa.Column("actor_key", sa.Text(), nullable=False),
        sa.Column("identity_provenance", sa.Text(), nullable=False),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("capability", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("target_type", sa.Text(), nullable=False),
        sa.Column("target_id", sa.Text(), nullable=True),
        sa.Column("outcome", sa.Text(), nullable=False),
        sa.Column("http_status", sa.Integer(), nullable=True),
        sa.CheckConstraint("length(id) = 36", name="ck_security_audit_events_id_length"),
        sa.CheckConstraint(
            "occurred_at_ms >= 0",
            name="ck_security_audit_events_occurred_at_non_negative",
        ),
        sa.CheckConstraint(
            "length(request_id) >= 1 AND length(request_id) <= 64",
            name="ck_security_audit_events_request_id_length",
        ),
        sa.CheckConstraint(
            "length(actor_login) >= 1 AND length(actor_login) <= 254",
            name="ck_security_audit_events_actor_login_length",
        ),
        sa.CheckConstraint(
            "length(actor_key) >= 1 AND length(actor_key) <= 254",
            name="ck_security_audit_events_actor_key_length",
        ),
        sa.CheckConstraint(
            "length(identity_provenance) >= 1 AND length(identity_provenance) <= 32",
            name="ck_security_audit_events_provenance_length",
        ),
        sa.CheckConstraint(
            "length(role) >= 1 AND length(role) <= 16",
            name="ck_security_audit_events_role_length",
        ),
        sa.CheckConstraint(
            "length(capability) >= 1 AND length(capability) <= 64",
            name="ck_security_audit_events_capability_length",
        ),
        sa.CheckConstraint(
            "length(action) >= 1 AND length(action) <= 64",
            name="ck_security_audit_events_action_length",
        ),
        sa.CheckConstraint(
            "length(target_type) >= 1 AND length(target_type) <= 64",
            name="ck_security_audit_events_target_type_length",
        ),
        sa.CheckConstraint(
            "target_id IS NULL OR "
            "(length(target_id) >= 1 AND length(target_id) <= 128)",
            name="ck_security_audit_events_target_id_length",
        ),
        sa.CheckConstraint(
            "outcome IN ('allowed', 'denied')",
            name="ck_security_audit_events_outcome",
        ),
        sa.CheckConstraint(
            "http_status IS NULL OR (http_status >= 100 AND http_status <= 599)",
            name="ck_security_audit_events_http_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_security_audit_events"),
    )
    op.create_index(
        "ix_security_audit_events_occurred_at",
        "security_audit_events",
        ["occurred_at_ms", "id"],
    )
    op.create_index(
        "ix_security_audit_events_actor_key",
        "security_audit_events",
        ["actor_key", "occurred_at_ms"],
    )
    op.create_index(
        "ix_security_audit_events_capability",
        "security_audit_events",
        ["capability", "occurred_at_ms"],
    )


def downgrade() -> None:
    """Refuse audit-history loss and remove only an empty audit table."""
    bind = op.get_bind()
    event_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM security_audit_events"
    ).scalar_one()
    if event_count:
        raise RuntimeError(_DOWNGRADE_AUDIT_MESSAGE)
    op.drop_index(
        "ix_security_audit_events_capability",
        table_name="security_audit_events",
    )
    op.drop_index(
        "ix_security_audit_events_actor_key",
        table_name="security_audit_events",
    )
    op.drop_index(
        "ix_security_audit_events_occurred_at",
        table_name="security_audit_events",
    )
    op.drop_table("security_audit_events")
