"""Add durable research requests, accounting and the active slot (0035)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0035"
down_revision = "0034"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "research_budget_holds",
    "research_operations",
    "research_requests",
)

_LIFECYCLE_STATES = (
    "'admitted', 'submitting', 'running', 'validating', 'saved', "
    "'refused', 'failed', 'incomplete', 'cancel_requested', 'cancelled', "
    "'timeout', 'submission_unknown'"
)
_CLEANUP_STATES = "'not_required', 'pending', 'deleted', 'failed', 'unknown'"
_ACCOUNTING_STATES = "'reserved', 'reconciled', 'unknown'"
_LOWER_HEX_64 = (
    "length({column}) = 64 "
    "AND {column} = lower({column}) "
    "AND {column} NOT GLOB '*[^0-9a-f]*'"
)


class ResearchDowngradeRefused(RuntimeError):
    """Raised before DDL when a 0035 table still contains rows."""

    def __init__(self) -> None:
        super().__init__("Research downgrade refused because stored rows exist.")


def _login_key_sql(column: str) -> str:
    return (
        f"length({column}) >= 1 AND length({column}) <= 254 "
        f"AND {column} = lower({column}) "
        f"AND instr({column}, ' ') = 0 "
        f"AND instr({column}, char(9)) = 0 "
        f"AND instr({column}, char(10)) = 0 "
        f"AND instr({column}, char(13)) = 0"
    )


def _byte_length(column: str, low: int, high: int) -> str:
    return f"length(CAST({column} AS BLOB)) >= {low} AND length(CAST({column} AS BLOB)) <= {high}"


def _not_uuid(column: str) -> str:
    return (
        f"NOT ({column} GLOB "
        f"'[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
        f"[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
        f"[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
        f"[0-9a-f][0-9a-f][0-9a-f][0-9a-f]-"
        f"[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]"
        f"[0-9a-f][0-9a-f][0-9a-f][0-9a-f]')"
    )


def upgrade() -> None:
    """Create durable research runtime tables without rewriting existing rows."""
    op.create_table(
        "research_requests",
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("owner_login_key", sa.Text(), nullable=False),
        sa.Column("client_request_id", sa.Text(), nullable=False),
        sa.Column("request_fingerprint", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("prompt_text", sa.Text(), nullable=False),
        sa.Column("prompt_utf8_bytes", sa.Integer(), nullable=False),
        sa.Column("lifecycle_state", sa.Text(), nullable=False),
        sa.Column("provider_id", sa.Text(), nullable=False),
        sa.Column("model_id", sa.Text(), nullable=False),
        sa.Column("configuration_version", sa.Text(), nullable=False),
        sa.Column("reasoning_effort", sa.Text(), nullable=False),
        sa.Column("max_tool_calls", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("deadline_seconds", sa.Integer(), nullable=False),
        sa.Column("reservation_micro_usd", sa.Integer(), nullable=False),
        sa.Column("tool_allowlist_json", sa.Text(), nullable=False),
        sa.Column("background", sa.Integer(), nullable=False),
        sa.Column("prompt_max_utf8_bytes", sa.Integer(), nullable=False),
        sa.Column("answer_max_utf8_bytes", sa.Integer(), nullable=False),
        sa.Column("citation_count_max", sa.Integer(), nullable=False),
        sa.Column("remote_handle_json", sa.Text(), nullable=True),
        sa.Column("checkpoint_json", sa.Text(), nullable=True),
        sa.Column("checkpoint_sha256", sa.Text(), nullable=True),
        sa.Column("record_id", sa.Text(), nullable=True),
        sa.Column("error_code", sa.Text(), nullable=True),
        sa.Column("cancel_requested_at_ms", sa.Integer(), nullable=True),
        sa.Column("cancellation_confirmed_at_ms", sa.Integer(), nullable=True),
        sa.Column("cleanup_state", sa.Text(), nullable=False),
        sa.Column("accounting_state", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("admitted_at_ms", sa.Integer(), nullable=False),
        sa.Column("submitted_at_ms", sa.Integer(), nullable=True),
        sa.Column("finished_at_ms", sa.Integer(), nullable=True),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("operation_id", name="pk_research_requests"),
        sa.UniqueConstraint(
            "record_id",
            name="uq_research_requests_record_id",
        ),
        sa.UniqueConstraint(
            "owner_login_key",
            "client_request_id",
            name="uq_research_requests_client",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["kronika_records.id"],
            name="fk_research_requests_record_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            f"{_byte_length('operation_id', 1, 128)} AND {_not_uuid('operation_id')}",
            name="ck_research_requests_operation_id",
        ),
        sa.CheckConstraint(
            _login_key_sql("owner_login_key"),
            name="ck_research_requests_owner_login_key",
        ),
        sa.CheckConstraint(
            _byte_length("client_request_id", 1, 128),
            name="ck_research_requests_client_request_id",
        ),
        sa.CheckConstraint(
            _LOWER_HEX_64.format(column="request_fingerprint"),
            name="ck_research_requests_fingerprint",
        ),
        sa.CheckConstraint(
            "kind IN ('search', 'research')",
            name="ck_research_requests_kind",
        ),
        sa.CheckConstraint(
            f"{_byte_length('prompt_text', 1, 16384)} AND length(trim(prompt_text)) > 0",
            name="ck_research_requests_prompt_text",
        ),
        sa.CheckConstraint(
            "prompt_utf8_bytes >= 1 AND prompt_utf8_bytes <= 16384 "
            "AND prompt_utf8_bytes = length(CAST(prompt_text AS BLOB))",
            name="ck_research_requests_prompt_bytes",
        ),
        sa.CheckConstraint(
            f"lifecycle_state IN ({_LIFECYCLE_STATES})",
            name="ck_research_requests_lifecycle_state",
        ),
        sa.CheckConstraint(
            _byte_length("provider_id", 1, 64),
            name="ck_research_requests_provider_id",
        ),
        sa.CheckConstraint(
            _byte_length("model_id", 1, 128),
            name="ck_research_requests_model_id",
        ),
        sa.CheckConstraint(
            _byte_length("configuration_version", 1, 16),
            name="ck_research_requests_configuration_version",
        ),
        sa.CheckConstraint(
            "reasoning_effort IN ('low', 'high')",
            name="ck_research_requests_reasoning_effort",
        ),
        sa.CheckConstraint(
            "max_tool_calls >= 1 AND max_tool_calls <= 20",
            name="ck_research_requests_max_tool_calls",
        ),
        sa.CheckConstraint(
            "max_output_tokens >= 1 AND max_output_tokens <= 32768",
            name="ck_research_requests_max_output_tokens",
        ),
        sa.CheckConstraint(
            "deadline_seconds >= 1 AND deadline_seconds <= 1800",
            name="ck_research_requests_deadline_seconds",
        ),
        sa.CheckConstraint(
            "reservation_micro_usd >= 1 AND reservation_micro_usd <= 5000000",
            name="ck_research_requests_reservation",
        ),
        sa.CheckConstraint(
            "tool_allowlist_json IN ('[\"web_search\"]')",
            name="ck_research_requests_tool_allowlist",
        ),
        sa.CheckConstraint(
            "background IN (0, 1)",
            name="ck_research_requests_background",
        ),
        sa.CheckConstraint(
            "prompt_max_utf8_bytes >= 1 AND prompt_max_utf8_bytes <= 16384",
            name="ck_research_requests_prompt_max",
        ),
        sa.CheckConstraint(
            "answer_max_utf8_bytes >= 1 AND answer_max_utf8_bytes <= 2097152",
            name="ck_research_requests_answer_max",
        ),
        sa.CheckConstraint(
            "citation_count_max >= 1 AND citation_count_max <= 200",
            name="ck_research_requests_citation_max",
        ),
        sa.CheckConstraint(
            "remote_handle_json IS NULL OR "
            "(length(remote_handle_json) >= 2 AND length(remote_handle_json) <= 65536)",
            name="ck_research_requests_remote_handle",
        ),
        sa.CheckConstraint(
            "checkpoint_json IS NULL OR "
            "(length(checkpoint_json) >= 2 AND length(checkpoint_json) <= 2097152)",
            name="ck_research_requests_checkpoint_json",
        ),
        sa.CheckConstraint(
            "checkpoint_sha256 IS NULL OR ("
            f"{_LOWER_HEX_64.format(column='checkpoint_sha256')})",
            name="ck_research_requests_checkpoint_sha256",
        ),
        sa.CheckConstraint(
            "record_id IS NULL OR length(record_id) = 36",
            name="ck_research_requests_record_id",
        ),
        sa.CheckConstraint(
            f"error_code IS NULL OR {_byte_length('error_code', 1, 64)}",
            name="ck_research_requests_error_code",
        ),
        sa.CheckConstraint(
            "cancel_requested_at_ms IS NULL OR cancel_requested_at_ms >= 0",
            name="ck_research_requests_cancel_requested_at_ms",
        ),
        sa.CheckConstraint(
            "cancellation_confirmed_at_ms IS NULL OR ("
            "cancel_requested_at_ms IS NOT NULL "
            "AND cancellation_confirmed_at_ms >= cancel_requested_at_ms)",
            name="ck_research_requests_cancellation_confirmed_at_ms",
        ),
        sa.CheckConstraint(
            f"cleanup_state IN ({_CLEANUP_STATES})",
            name="ck_research_requests_cleanup_state",
        ),
        sa.CheckConstraint(
            f"accounting_state IN ({_ACCOUNTING_STATES})",
            name="ck_research_requests_accounting_state",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_research_requests_created_at_ms",
        ),
        sa.CheckConstraint(
            "admitted_at_ms >= created_at_ms",
            name="ck_research_requests_admitted_at_ms",
        ),
        sa.CheckConstraint(
            "submitted_at_ms IS NULL OR submitted_at_ms >= admitted_at_ms",
            name="ck_research_requests_submitted_at_ms",
        ),
        sa.CheckConstraint(
            "finished_at_ms IS NULL OR finished_at_ms >= admitted_at_ms",
            name="ck_research_requests_finished_at_ms",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_research_requests_updated_at_ms",
        ),
    )
    op.execute(
        "CREATE INDEX ix_research_requests_state ON research_requests "
        "(lifecycle_state, updated_at_ms)"
    )
    op.execute(
        "CREATE INDEX ix_research_requests_owner_history ON research_requests "
        "(owner_login_key, created_at_ms DESC, operation_id ASC)"
    )
    op.create_table(
        "research_active_slot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=True),
        sa.Column("held_since_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_research_active_slot"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["research_requests.operation_id"],
            name="fk_research_active_slot_operation_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("id = 1", name="ck_research_active_slot_single_row"),
        sa.CheckConstraint(
            "(operation_id IS NULL AND held_since_ms IS NULL) OR ("
            "operation_id IS NOT NULL AND held_since_ms IS NOT NULL "
            "AND held_since_ms >= 0)",
            name="ck_research_active_slot_shape",
        ),
    )
    op.create_table(
        "research_operations",
        sa.Column("operation_row_id", sa.Text(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("parent_operation_id", sa.Text(), nullable=True),
        sa.Column("purpose", sa.Text(), nullable=False),
        sa.Column("operation", sa.Text(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("started_at_ms", sa.Integer(), nullable=False),
        sa.Column("finished_at_ms", sa.Integer(), nullable=True),
        sa.Column("terminal_classification", sa.Text(), nullable=True),
        sa.Column("remote_handle_reference", sa.Text(), nullable=True),
        sa.Column("transport_outcome", sa.Text(), nullable=True),
        sa.Column("reserved_usd_micros", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("reasoning_tokens_as_subset", sa.Integer(), nullable=True),
        sa.Column("web_tool_calls", sa.Integer(), nullable=True),
        sa.Column("calculated_cost_usd_micros", sa.Integer(), nullable=True),
        sa.Column("accounting_state", sa.Text(), nullable=False),
        sa.Column("cleanup_state", sa.Text(), nullable=True),
        sa.Column("cancellation_state", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("operation_row_id", name="pk_research_operations"),
        sa.ForeignKeyConstraint(
            ["request_id"],
            ["research_requests.operation_id"],
            name="fk_research_operations_request_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["parent_operation_id"],
            ["research_operations.operation_row_id"],
            name="fk_research_operations_parent",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(operation_row_id) = 36",
            name="ck_research_operations_row_id",
        ),
        sa.CheckConstraint(
            "parent_operation_id IS NULL OR length(parent_operation_id) = 36",
            name="ck_research_operations_parent_id",
        ),
        sa.CheckConstraint(
            "purpose IN ('search', 'research', 'synthetic_acceptance')",
            name="ck_research_operations_purpose",
        ),
        sa.CheckConstraint(
            "operation IN ('create', 'poll', 'cancel', 'delete')",
            name="ck_research_operations_operation",
        ),
        sa.CheckConstraint(
            "attempt_number >= 1",
            name="ck_research_operations_attempt_number",
        ),
        sa.CheckConstraint(
            "started_at_ms >= 0",
            name="ck_research_operations_started_at_ms",
        ),
        sa.CheckConstraint(
            "finished_at_ms IS NULL OR finished_at_ms >= started_at_ms",
            name="ck_research_operations_finished_at_ms",
        ),
        sa.CheckConstraint(
            f"terminal_classification IS NULL OR "
            f"{_byte_length('terminal_classification', 1, 64)}",
            name="ck_research_operations_terminal_classification",
        ),
        sa.CheckConstraint(
            f"remote_handle_reference IS NULL OR "
            f"{_byte_length('remote_handle_reference', 1, 256)}",
            name="ck_research_operations_remote_handle_reference",
        ),
        sa.CheckConstraint(
            f"transport_outcome IS NULL OR "
            f"{_byte_length('transport_outcome', 1, 64)}",
            name="ck_research_operations_transport_outcome",
        ),
        sa.CheckConstraint(
            "reserved_usd_micros IS NULL OR "
            "(reserved_usd_micros >= 0 AND reserved_usd_micros <= 5000000)",
            name="ck_research_operations_reserved",
        ),
        sa.CheckConstraint(
            "input_tokens IS NULL OR input_tokens >= 0",
            name="ck_research_operations_input_tokens",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR cached_input_tokens >= 0",
            name="ck_research_operations_cached_input_tokens",
        ),
        sa.CheckConstraint(
            "output_tokens IS NULL OR output_tokens >= 0",
            name="ck_research_operations_output_tokens",
        ),
        sa.CheckConstraint(
            "reasoning_tokens_as_subset IS NULL OR reasoning_tokens_as_subset >= 0",
            name="ck_research_operations_reasoning_tokens",
        ),
        sa.CheckConstraint(
            "web_tool_calls IS NULL OR web_tool_calls >= 0",
            name="ck_research_operations_web_tool_calls",
        ),
        sa.CheckConstraint(
            "calculated_cost_usd_micros IS NULL OR calculated_cost_usd_micros >= 0",
            name="ck_research_operations_calculated_cost",
        ),
        sa.CheckConstraint(
            "cached_input_tokens IS NULL OR input_tokens IS NULL "
            "OR cached_input_tokens <= input_tokens",
            name="ck_research_operations_cached_subset",
        ),
        sa.CheckConstraint(
            "reasoning_tokens_as_subset IS NULL OR ("
            "output_tokens IS NOT NULL "
            "AND reasoning_tokens_as_subset <= output_tokens)",
            name="ck_research_operations_reasoning_subset",
        ),
        sa.CheckConstraint(
            f"accounting_state IN ({_ACCOUNTING_STATES})",
            name="ck_research_operations_accounting_state",
        ),
        sa.CheckConstraint(
            f"cleanup_state IS NULL OR {_byte_length('cleanup_state', 1, 32)}",
            name="ck_research_operations_cleanup_state",
        ),
        sa.CheckConstraint(
            f"cancellation_state IS NULL OR {_byte_length('cancellation_state', 1, 32)}",
            name="ck_research_operations_cancellation_state",
        ),
    )
    op.execute(
        "CREATE INDEX ix_research_operations_request ON research_operations "
        "(request_id, started_at_ms)"
    )
    op.create_table(
        "research_budget_holds",
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("day_key", sa.Text(), nullable=False),
        sa.Column("month_key", sa.Text(), nullable=False),
        sa.Column("reserved_usd_micros", sa.Integer(), nullable=False),
        sa.Column("accounted_usd_micros", sa.Integer(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("reconciled_at_ms", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("operation_id", name="pk_research_budget_holds"),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["research_requests.operation_id"],
            name="fk_research_budget_holds_request",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "day_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'",
            name="ck_research_budget_holds_day_key",
        ),
        sa.CheckConstraint(
            "month_key GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'",
            name="ck_research_budget_holds_month_key",
        ),
        sa.CheckConstraint(
            "reserved_usd_micros >= 1 AND reserved_usd_micros <= 5000000",
            name="ck_research_budget_holds_reserved",
        ),
        sa.CheckConstraint(
            "accounted_usd_micros IS NULL OR accounted_usd_micros >= 0",
            name="ck_research_budget_holds_accounted",
        ),
        sa.CheckConstraint(
            f"state IN ({_ACCOUNTING_STATES})",
            name="ck_research_budget_holds_state",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND accounted_usd_micros IS NULL "
            "AND reconciled_at_ms IS NULL) OR ("
            "state IN ('reconciled', 'unknown') "
            "AND accounted_usd_micros IS NOT NULL AND reconciled_at_ms IS NOT NULL)",
            name="ck_research_budget_holds_shape",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_research_budget_holds_created_at_ms",
        ),
        sa.CheckConstraint(
            "reconciled_at_ms IS NULL OR reconciled_at_ms >= created_at_ms",
            name="ck_research_budget_holds_reconciled_at_ms",
        ),
    )
    op.execute(
        "CREATE INDEX ix_research_budget_holds_day ON research_budget_holds "
        "(day_key, state)"
    )
    op.execute(
        "CREATE INDEX ix_research_budget_holds_month ON research_budget_holds "
        "(month_key, state)"
    )


def downgrade() -> None:
    """Return to 0034 only when the research runtime tables are empty.

    The single-row active slot is operational state; a lone slot row (which by
    its foreign key always references a request row) does not block the
    downgrade when every request table is empty.
    """
    connection = op.get_bind()
    for table_name in _NEW_TABLES:
        count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        if int(count or 0) != 0:
            raise ResearchDowngradeRefused()
    op.execute("DROP INDEX ix_research_budget_holds_month")
    op.execute("DROP INDEX ix_research_budget_holds_day")
    op.drop_table("research_budget_holds")
    op.execute("DROP INDEX ix_research_operations_request")
    op.drop_table("research_operations")
    op.drop_table("research_active_slot")
    op.execute("DROP INDEX ix_research_requests_owner_history")
    op.execute("DROP INDEX ix_research_requests_state")
    op.drop_table("research_requests")
