"""Add requester-private X manual-claim acquisition tables (0028)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None

_PREVIOUS_MEDIA_ACQUISITION_SQL = (
    "acquisition_source IN ("
    "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim')"
)
_MEDIA_ACQUISITION_SQL = (
    "acquisition_source IN ("
    "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim', "
    "'x_manual_claim')"
)
_PREVIOUS_RECEIPT_ACQUISITION_SQL = (
    "acquisition_source IN ("
    "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim')"
)
_RECEIPT_ACQUISITION_SQL = (
    "acquisition_source IN ("
    "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim', "
    "'x_manual_claim')"
)

_FK_FAILURE_MESSAGE = "Foreign-key enforcement failed during 0028 migration."

_ACTIVE_STATES_SQL = (
    "state IN ('submitted', 'queued', 'extracting', 'acquiring', 'handing_off')"
)


def upgrade() -> None:
    op.create_table(
        "x_post_claims",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("acquisition_source", sa.Text(), nullable=False),
        sa.Column("submitted_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("x_post_id", sa.Text(), nullable=False),
        sa.Column("extractor_key", sa.Text(), nullable=False),
        sa.Column("created_by_login_key", sa.Text(), nullable=True),
        sa.Column(
            "retry_of_claim_id",
            sa.Text(),
            sa.ForeignKey(
                "x_post_claims.id", ondelete="RESTRICT",
                name="fk_x_post_claims_retry_of_claim_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "resolved_claim_id",
            sa.Text(),
            sa.ForeignKey(
                "x_post_claims.id", ondelete="RESTRICT",
                name="fk_x_post_claims_resolved_claim_id",
            ),
            nullable=True,
        ),
        sa.Column("source_author_stable_id", sa.Text(), nullable=True),
        sa.Column("source_author_handle", sa.Text(), nullable=True),
        sa.Column("source_author_display_name", sa.Text(), nullable=True),
        sa.Column("source_post_text", sa.Text(), nullable=True),
        sa.Column("source_posted_at_ms", sa.Integer(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column("discovered_asset_count", sa.Integer(), nullable=False),
        sa.Column("success_count", sa.Integer(), nullable=False),
        sa.Column("failure_count", sa.Integer(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("catalog_removed_at_ms", sa.Integer(), nullable=True),
        sa.Column("failure_stage", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("cleanup_state", sa.Text(), nullable=False),
        sa.Column("cleanup_completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(id) = 36", name="ck_x_post_claims_id_length"
        ),
        sa.CheckConstraint(
            "created_by_login_key IS NULL OR ("
            "length(created_by_login_key) >= 1 "
            "AND length(created_by_login_key) <= 254 "
            "AND created_by_login_key = lower(created_by_login_key) "
            "AND instr(created_by_login_key, ' ') = 0 "
            "AND instr(created_by_login_key, char(9)) = 0 "
            "AND instr(created_by_login_key, char(10)) = 0 "
            "AND instr(created_by_login_key, char(13)) = 0)",
            name="ck_x_post_claims_created_by_login_key",
        ),
        sa.CheckConstraint(
            "state IN ('submitted', 'queued', 'extracting', 'acquiring', "
            "'handing_off', 'completed', 'completed_partial', 'failed', "
            "'duplicate_resolved', 'catalog_removed')",
            name="ck_x_post_claims_state",
        ),
        sa.CheckConstraint(
            "acquisition_source = 'x_manual_claim'",
            name="ck_x_post_claims_acquisition_source",
        ),
        sa.CheckConstraint(
            "length(submitted_url) >= 1 AND length(submitted_url) <= 2048",
            name="ck_x_post_claims_submitted_url_length",
        ),
        sa.CheckConstraint(
            "length(canonical_url) >= 1 AND length(canonical_url) <= 2048",
            name="ck_x_post_claims_canonical_url_length",
        ),
        sa.CheckConstraint(
            "length(x_post_id) >= 1 AND length(x_post_id) <= 19 "
            "AND x_post_id NOT GLOB '*[^0-9]*'",
            name="ck_x_post_claims_post_id",
        ),
        sa.CheckConstraint(
            "extractor_key = 'X'", name="ck_x_post_claims_extractor_key"
        ),
        sa.CheckConstraint(
            "source_author_handle IS NULL OR ("
            "length(source_author_handle) >= 1 "
            "AND length(source_author_handle) <= 64 "
            "AND source_author_handle = lower(source_author_handle) "
            "AND substr(source_author_handle, 1, 1) != '@')",
            name="ck_x_post_claims_author_handle",
        ),
        sa.CheckConstraint(
            "source_posted_at_ms IS NULL OR source_posted_at_ms >= 0",
            name="ck_x_post_claims_source_posted_at",
        ),
        sa.CheckConstraint(
            "discovered_asset_count >= 0 AND discovered_asset_count <= 4",
            name="ck_x_post_claims_discovered_count",
        ),
        sa.CheckConstraint(
            "success_count >= 0 AND success_count <= 4 "
            "AND failure_count >= 0 AND failure_count <= 4",
            name="ck_x_post_claims_outcome_counts",
        ),
        sa.CheckConstraint(
            "success_count + failure_count <= discovered_asset_count",
            name="ck_x_post_claims_outcome_bounded",
        ),
        sa.CheckConstraint(
            "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
            name="ck_x_post_claims_completed_at",
        ),
        sa.CheckConstraint(
            "catalog_removed_at_ms IS NULL OR completed_at_ms IS NULL "
            "OR catalog_removed_at_ms >= completed_at_ms",
            name="ck_x_post_claims_removed_at",
        ),
        sa.CheckConstraint(
            "(failure_stage IS NULL AND failure_code IS NULL) "
            "OR (failure_stage IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_x_post_claims_failure_pair",
        ),
        sa.CheckConstraint(
            "failure_stage IS NULL OR failure_stage IN ("
            "'configuration', 'extraction', 'acquisition', 'staging', "
            "'handoff', 'downstream', 'cleanup', 'internal')",
            name="ck_x_post_claims_failure_stage",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('pending', 'complete')",
            name="ck_x_post_claims_cleanup_state",
        ),
        sa.CheckConstraint(
            "(cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
            "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)",
            name="ck_x_post_claims_cleanup_pair",
        ),
    )
    op.create_index(
        "uq_x_post_claims_active_requester",
        "x_post_claims",
        ["x_post_id", "created_by_login_key"],
        unique=True,
        sqlite_where=sa.text(
            _ACTIVE_STATES_SQL + " AND created_by_login_key IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_x_post_claims_state",
        "x_post_claims",
        ["state", "updated_at_ms", "id"],
    )
    op.create_index(
        "ix_x_post_claims_post_id",
        "x_post_claims",
        ["x_post_id", "created_at_ms"],
    )
    op.create_index(
        "ix_x_post_claims_retry_of", "x_post_claims", ["retry_of_claim_id"]
    )
    op.create_index(
        "ix_x_post_claims_resolved_claim",
        "x_post_claims",
        ["resolved_claim_id"],
    )
    op.create_index(
        "ix_x_post_claims_created_by_login_key",
        "x_post_claims",
        ["created_by_login_key"],
    )
    op.create_index(
        "ix_x_post_claims_owner_updated",
        "x_post_claims",
        ["created_by_login_key", "updated_at_ms", "id"],
    )

    op.create_table(
        "x_assets",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column(
            "claim_id",
            sa.Text(),
            sa.ForeignKey(
                "x_post_claims.id", ondelete="RESTRICT",
                name="fk_x_assets_claim_id",
            ),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("media_type", sa.Text(), nullable=False),
        sa.Column("expected_mime", sa.Text(), nullable=False),
        sa.Column("source_media_key", sa.Text(), nullable=True),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("selected_variant", sa.Text(), nullable=True),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("stage_key", sa.Text(), nullable=False),
        sa.Column("acquired_bytes", sa.Integer(), nullable=True),
        sa.Column("acquired_sha256", sa.Text(), nullable=True),
        sa.Column(
            "media_id",
            sa.Text(),
            sa.ForeignKey(
                "logical_media.id", ondelete="RESTRICT",
                name="fk_x_assets_media_id",
            ),
            nullable=True,
        ),
        sa.Column(
            "media_location_id",
            sa.Text(),
            sa.ForeignKey(
                "physical_media_locations.id", ondelete="RESTRICT",
                name="fk_x_assets_media_location_id",
            ),
            nullable=True,
        ),
        sa.Column("upload_asset_key", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("failure_stage", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("cleanup_state", sa.Text(), nullable=False),
        sa.Column("cleanup_completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(id) = 36", name="ck_x_assets_id_length"
        ),
        sa.CheckConstraint(
            "length(claim_id) = 36", name="ck_x_assets_claim_id_length"
        ),
        sa.CheckConstraint(
            "ordinal >= 0 AND ordinal <= 3", name="ck_x_assets_ordinal"
        ),
        sa.CheckConstraint(
            "media_type IN ('video', 'animated_gif', 'image')",
            name="ck_x_assets_media_type",
        ),
        sa.CheckConstraint(
            "length(expected_mime) >= 1 AND length(expected_mime) <= 120",
            name="ck_x_assets_mime_length",
        ),
        sa.CheckConstraint(
            "width IS NULL OR width >= 0", name="ck_x_assets_width"
        ),
        sa.CheckConstraint(
            "height IS NULL OR height >= 0", name="ck_x_assets_height"
        ),
        sa.CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds <= 300",
            name="ck_x_assets_duration",
        ),
        sa.CheckConstraint(
            "state IN ('pending', 'extracted', 'acquiring', 'staged', "
            "'handing_off', 'cataloged', 'failed')",
            name="ck_x_assets_state",
        ),
        sa.CheckConstraint(
            "length(stage_key) = 32 "
            "AND stage_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_x_assets_stage_key",
        ),
        sa.CheckConstraint(
            "acquired_bytes IS NULL OR acquired_bytes > 0",
            name="ck_x_assets_acquired_bytes",
        ),
        sa.CheckConstraint(
            "acquired_sha256 IS NULL OR ("
            "length(acquired_sha256) = 64 "
            "AND acquired_sha256 = lower(acquired_sha256) "
            "AND acquired_sha256 NOT GLOB '*[^0-9a-f]*')",
            name="ck_x_assets_sha256",
        ),
        sa.CheckConstraint(
            "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
            name="ck_x_assets_completed_at",
        ),
        sa.CheckConstraint(
            "(failure_stage IS NULL AND failure_code IS NULL) "
            "OR (failure_stage IS NOT NULL AND failure_code IS NOT NULL)",
            name="ck_x_assets_failure_pair",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('pending', 'complete')",
            name="ck_x_assets_cleanup_state",
        ),
        sa.CheckConstraint(
            "(cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
            "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)",
            name="ck_x_assets_cleanup_pair",
        ),
        sa.UniqueConstraint("stage_key", name="uq_x_assets_stage_key"),
        sa.UniqueConstraint("claim_id", "ordinal", name="uq_x_assets_claim_ordinal"),
    )
    op.create_index("ix_x_assets_claim_id", "x_assets", ["claim_id", "ordinal"])
    op.create_index(
        "ix_x_assets_state", "x_assets", ["state", "updated_at_ms", "id"]
    )
    op.create_index("ix_x_assets_media", "x_assets", ["media_id", "media_location_id"])

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.drop_constraint(
                "ck_media_metadata_acquisition_source", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_acquisition_source", _MEDIA_ACQUISITION_SQL
            )
        with op.batch_alter_table("media_catalog_removal_receipts") as batch_op:
            batch_op.drop_constraint(
                "ck_catalog_removal_receipts_acquisition_source", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_catalog_removal_receipts_acquisition_source",
                _RECEIPT_ACQUISITION_SQL,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    _reject_if_x_manual_claim_used()
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        op.drop_table("x_assets")
        op.drop_table("x_post_claims")
        with op.batch_alter_table("media_metadata") as batch_op:
            batch_op.drop_constraint(
                "ck_media_metadata_acquisition_source", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_media_metadata_acquisition_source",
                _PREVIOUS_MEDIA_ACQUISITION_SQL,
            )
        with op.batch_alter_table("media_catalog_removal_receipts") as batch_op:
            batch_op.drop_constraint(
                "ck_catalog_removal_receipts_acquisition_source", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_catalog_removal_receipts_acquisition_source",
                _PREVIOUS_RECEIPT_ACQUISITION_SQL,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def _reject_if_x_manual_claim_used() -> None:
    connection = op.get_bind()
    blocked = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM media_metadata "
            "WHERE acquisition_source = 'x_manual_claim'"
        )
    ).scalar_one()
    if blocked:
        raise RuntimeError("Refusing 0028 downgrade while X media exists.")