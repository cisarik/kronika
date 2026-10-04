"""Add Kronika common records and approved projections (0034)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0034"
down_revision = "0033"
branch_labels = None
depends_on = None

_NEW_TABLES = (
    "kronika_approved_media_tags",
    "kronika_approved_media_locations",
    "kronika_approved_media",
    "kronika_records",
    "kronika_documents",
)


class KronikaDowngradeRefused(RuntimeError):
    """Raised before DDL when a 0034 table still contains rows."""

    def __init__(self) -> None:
        super().__init__("Kronika downgrade refused because stored rows exist.")


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
    """Create Kronika tables and indexes without rewriting existing rows."""
    op.create_table(
        "kronika_documents",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("operation_id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("question_text", sa.Text(), nullable=False),
        sa.Column("answer_text", sa.Text(), nullable=False),
        sa.Column("citations_json", sa.Text(), nullable=False),
        sa.Column("completion_evidence_json", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_kronika_documents"),
        sa.UniqueConstraint("operation_id", name="uq_kronika_documents_operation_id"),
        sa.UniqueConstraint(
            "id",
            "operation_id",
            "kind",
            name="uq_kronika_documents_id_operation_kind",
        ),
        sa.CheckConstraint("length(id) = 36", name="ck_kronika_documents_id_length"),
        sa.CheckConstraint(
            f"{_byte_length('operation_id', 1, 128)} AND {_not_uuid('operation_id')}",
            name="ck_kronika_documents_operation_id",
        ),
        sa.CheckConstraint(
            "kind IN ('search', 'research')",
            name="ck_kronika_documents_kind",
        ),
        sa.CheckConstraint(
            f"{_byte_length('question_text', 1, 16384)} AND length(trim(question_text)) > 0",
            name="ck_kronika_documents_question_text",
        ),
        sa.CheckConstraint(
            f"{_byte_length('answer_text', 1, 2097152)} AND length(trim(answer_text)) > 0",
            name="ck_kronika_documents_answer_text",
        ),
        sa.CheckConstraint(
            "length(citations_json) >= 2",
            name="ck_kronika_documents_citations_json",
        ),
        sa.CheckConstraint(
            "length(completion_evidence_json) >= 2",
            name="ck_kronika_documents_completion_evidence_json",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_kronika_documents_created_at_ms",
        ),
        sa.CheckConstraint(
            "completed_at_ms >= created_at_ms",
            name="ck_kronika_documents_completed_at_ms",
        ),
    )
    op.create_table(
        "kronika_records",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("owner_login_key", sa.Text(), nullable=False),
        sa.Column(
            "visibility",
            sa.Text(),
            nullable=False,
            server_default="private",
        ),
        sa.Column("media_id", sa.Text(), nullable=True),
        sa.Column("document_id", sa.Text(), nullable=True),
        sa.Column("final_operation_id", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("timeline_entered_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("latest_successful_analysis_run_id", sa.Text(), nullable=True),
        sa.Column("approved_analysis_run_id", sa.Text(), nullable=True),
        sa.Column("approved_by_login_key", sa.Text(), nullable=True),
        sa.Column("approved_at_ms", sa.Integer(), nullable=True),
        sa.Column("approved_record_version", sa.Integer(), nullable=True),
        sa.Column("approved_projection_json", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_kronika_records"),
        sa.UniqueConstraint("media_id", name="uq_kronika_records_media_id"),
        sa.UniqueConstraint("document_id", name="uq_kronika_records_document_id"),
        sa.UniqueConstraint(
            "final_operation_id",
            name="uq_kronika_records_final_operation_id",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_kronika_records_media_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "final_operation_id", "kind"],
            ["kronika_documents.id", "kronika_documents.operation_id", "kronika_documents.kind"],
            name="fk_kronika_records_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["latest_successful_analysis_run_id"],
            ["media_analysis_runs.id"],
            name="fk_kronika_records_latest_analysis_run_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approved_analysis_run_id"],
            ["media_analysis_runs.id"],
            name="fk_kronika_records_approved_analysis_run_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(id) = 36", name="ck_kronika_records_id_length"),
        sa.CheckConstraint(
            "kind IN ('media', 'search', 'research')",
            name="ck_kronika_records_kind",
        ),
        sa.CheckConstraint(
            _login_key_sql("owner_login_key"),
            name="ck_kronika_records_owner_login_key",
        ),
        sa.CheckConstraint(
            "visibility IN ('private', 'family')",
            name="ck_kronika_records_visibility",
        ),
        sa.CheckConstraint(
            "("
            "kind = 'media' AND media_id IS NOT NULL AND length(media_id) = 36 "
            "AND document_id IS NULL AND final_operation_id IS NULL"
            ") OR ("
            "kind IN ('search', 'research') AND media_id IS NULL "
            "AND document_id IS NOT NULL AND length(document_id) = 36 "
            "AND final_operation_id IS NOT NULL "
            f"AND {_byte_length('final_operation_id', 1, 128)} "
            f"AND {_not_uuid('final_operation_id')} "
            "AND completed_at_ms IS NOT NULL "
            "AND latest_successful_analysis_run_id IS NULL "
            "AND approved_analysis_run_id IS NULL"
            ")",
            name="ck_kronika_records_shape",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_kronika_records_created_at_ms",
        ),
        sa.CheckConstraint(
            "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
            name="ck_kronika_records_completed_at_ms",
        ),
        sa.CheckConstraint(
            "timeline_entered_at_ms IS NULL OR ("
            "completed_at_ms IS NOT NULL AND timeline_entered_at_ms >= completed_at_ms"
            ")",
            name="ck_kronika_records_timeline_entered_at_ms",
        ),
        sa.CheckConstraint("version >= 1", name="ck_kronika_records_version"),
        sa.CheckConstraint(
            "latest_successful_analysis_run_id IS NULL "
            "OR length(latest_successful_analysis_run_id) = 36",
            name="ck_kronika_records_latest_analysis_length",
        ),
        sa.CheckConstraint(
            "approved_analysis_run_id IS NULL OR length(approved_analysis_run_id) = 36",
            name="ck_kronika_records_approved_analysis_length",
        ),
        sa.CheckConstraint(
            "("
            "approved_by_login_key IS NULL AND approved_at_ms IS NULL "
            "AND approved_record_version IS NULL AND approved_projection_json IS NULL"
            ") OR ("
            "approved_by_login_key IS NOT NULL "
            f"AND {_login_key_sql('approved_by_login_key')} "
            "AND approved_at_ms IS NOT NULL AND approved_record_version IS NOT NULL "
            "AND approved_projection_json IS NOT NULL "
            "AND completed_at_ms IS NOT NULL AND timeline_entered_at_ms IS NOT NULL "
            "AND approved_at_ms >= completed_at_ms "
            "AND approved_at_ms >= timeline_entered_at_ms "
            "AND approved_record_version >= 1 AND approved_record_version <= version "
            "AND ("
            "(kind = 'media' AND approved_analysis_run_id IS NOT NULL) "
            "OR (kind IN ('search', 'research') AND approved_analysis_run_id IS NULL)"
            ")"
            ")",
            name="ck_kronika_records_approval",
        ),
        sa.CheckConstraint(
            "visibility = 'private' OR ("
            "visibility = 'family' AND approved_by_login_key IS NOT NULL"
            ")",
            name="ck_kronika_records_family_requires_approval",
        ),
    )
    op.execute(
        "CREATE INDEX ix_kronika_records_owner_history ON kronika_records "
        "(owner_login_key, created_at_ms DESC, id ASC)"
    )
    op.execute(
        "CREATE INDEX ix_kronika_records_admin_history ON kronika_records "
        "(created_at_ms DESC, id ASC)"
    )
    op.execute(
        "CREATE INDEX ix_kronika_records_timeline ON kronika_records "
        "(visibility, timeline_entered_at_ms DESC, id ASC)"
    )
    op.create_index(
        "ix_kronika_records_latest_analysis_run_id",
        "kronika_records",
        ["latest_successful_analysis_run_id"],
    )
    op.create_index(
        "ix_kronika_records_approved_analysis_run_id",
        "kronika_records",
        ["approved_analysis_run_id"],
    )
    op.create_table(
        "kronika_approved_media",
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("approval_version", sa.Integer(), nullable=False),
        sa.Column("content_category", sa.Text(), nullable=False),
        sa.Column("acquisition_source", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("creator_attribution_kind", sa.Text(), nullable=True),
        sa.Column("creator_stable_id", sa.Text(), nullable=True),
        sa.Column("creator_handle", sa.Text(), nullable=True),
        sa.Column("creator_display_name", sa.Text(), nullable=True),
        sa.Column("cover_artifact_digest", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("record_id", name="pk_kronika_approved_media"),
        sa.UniqueConstraint("media_id", name="uq_kronika_approved_media_media_id"),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["kronika_records.id"],
            name="fk_kronika_approved_media_record_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_kronika_approved_media_media_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(record_id) = 36",
            name="ck_kronika_approved_media_record_id_length",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_kronika_approved_media_media_id_length",
        ),
        sa.CheckConstraint(
            "approval_version >= 1",
            name="ck_kronika_approved_media_approval_version",
        ),
        sa.CheckConstraint(
            "content_category IN ('general', 'meme', 'movie', 'youtube')",
            name="ck_kronika_approved_media_content_category",
        ),
        sa.CheckConstraint(
            "length(display_title) >= 1 AND length(display_title) <= 240",
            name="ck_kronika_approved_media_title",
        ),
        sa.CheckConstraint(
            "length(description) >= 1 AND length(description) <= 10000",
            name="ck_kronika_approved_media_description",
        ),
        sa.CheckConstraint(
            "cover_artifact_digest IS NULL OR ("
            "length(cover_artifact_digest) = 64 "
            "AND cover_artifact_digest = lower(cover_artifact_digest) "
            "AND cover_artifact_digest NOT GLOB '*[^0-9a-f]*')",
            name="ck_kronika_approved_media_cover_digest",
        ),
    )
    op.create_table(
        "kronika_approved_media_tags",
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("tag_key", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "record_id",
            "tag_key",
            name="pk_kronika_approved_media_tags",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["kronika_records.id"],
            name="fk_kronika_approved_media_tags_record_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(record_id) = 36",
            name="ck_kronika_approved_media_tags_record_id",
        ),
        sa.CheckConstraint(
            "length(tag_key) >= 1 AND length(tag_key) <= 64",
            name="ck_kronika_approved_media_tags_key",
        ),
        sa.CheckConstraint(
            "length(display_name) >= 1 AND length(display_name) <= 80",
            name="ck_kronika_approved_media_tags_display_name",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 32",
            name="ck_kronika_approved_media_tags_position",
        ),
    )
    op.create_index(
        "ix_kronika_approved_media_tags_record_position",
        "kronika_approved_media_tags",
        ["record_id", "position"],
    )
    op.create_table(
        "kronika_approved_media_locations",
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("location_id", sa.Text(), nullable=False),
        sa.Column("library_id", sa.Text(), nullable=False),
        sa.Column("relative_path", sa.Text(), nullable=False),
        sa.Column("availability", sa.Text(), nullable=False),
        sa.Column("observed_size_bytes", sa.Integer(), nullable=True),
        sa.Column("observed_mtime_ns", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint(
            "record_id",
            "location_id",
            name="pk_kronika_approved_media_locations",
        ),
        sa.ForeignKeyConstraint(
            ["record_id"],
            ["kronika_records.id"],
            name="fk_kronika_approved_media_locations_record_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["location_id"],
            ["physical_media_locations.id"],
            name="fk_kronika_approved_media_locations_location_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(record_id) = 36 AND length(location_id) = 36 AND length(library_id) = 36",
            name="ck_kronika_approved_media_locations_ids",
        ),
        sa.CheckConstraint(
            "length(relative_path) >= 1 AND length(relative_path) <= 4096",
            name="ck_kronika_approved_media_locations_path",
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'offline', 'missing', 'unverified', 'archived')",
            name="ck_kronika_approved_media_locations_availability",
        ),
    )


def downgrade() -> None:
    """Return to 0033 only when every new table is empty."""
    connection = op.get_bind()
    for table_name in _NEW_TABLES:
        count = connection.execute(sa.text(f"SELECT COUNT(*) FROM {table_name}")).scalar()
        if int(count or 0) != 0:
            raise KronikaDowngradeRefused()
    op.drop_table("kronika_approved_media_locations")
    op.drop_index(
        "ix_kronika_approved_media_tags_record_position",
        table_name="kronika_approved_media_tags",
    )
    op.drop_table("kronika_approved_media_tags")
    op.drop_table("kronika_approved_media")
    op.drop_index(
        "ix_kronika_records_approved_analysis_run_id",
        table_name="kronika_records",
    )
    op.drop_index(
        "ix_kronika_records_latest_analysis_run_id",
        table_name="kronika_records",
    )
    op.execute("DROP INDEX ix_kronika_records_timeline")
    op.execute("DROP INDEX ix_kronika_records_admin_history")
    op.execute("DROP INDEX ix_kronika_records_owner_history")
    op.drop_table("kronika_records")
    op.drop_table("kronika_documents")
