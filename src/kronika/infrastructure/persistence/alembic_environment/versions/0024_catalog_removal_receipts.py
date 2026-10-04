"""Add YouTube catalog_removed provenance and catalog-removal receipts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after catalog-removal migration."
)

_TERMINAL_PAYLOAD = (
    "(state = 'cataloged' AND media_id IS NOT NULL "
    "AND media_location_id IS NOT NULL "
    "AND completed_at_ms IS NOT NULL AND failure_code IS NULL "
    "AND catalog_removed_at_ms IS NULL) "
    "OR (state = 'duplicate_resolved' "
    "AND media_id IS NOT NULL AND media_location_id IS NOT NULL "
    "AND completed_at_ms IS NOT NULL AND failure_code IS NULL "
    "AND catalog_removed_at_ms IS NULL) "
    "OR (state = 'catalog_removed' AND media_id IS NULL "
    "AND media_location_id IS NULL AND completed_at_ms IS NOT NULL "
    "AND catalog_removed_at_ms IS NOT NULL "
    "AND catalog_removed_at_ms >= completed_at_ms "
    "AND failure_code IS NULL) "
    "OR (state = 'failed' AND failure_code IS NOT NULL "
    "AND completed_at_ms IS NOT NULL AND catalog_removed_at_ms IS NULL) "
    "OR (state NOT IN ('cataloged', 'duplicate_resolved', 'catalog_removed', "
    "'failed') "
    "AND completed_at_ms IS NULL AND failure_code IS NULL "
    "AND catalog_removed_at_ms IS NULL)"
)


def upgrade() -> None:
    """Preserve existing claims while adding catalog-removal semantics."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("youtube_acquisition_claims") as batch_op:
            batch_op.add_column(
                sa.Column("catalog_removed_at_ms", sa.Integer(), nullable=True)
            )
            batch_op.drop_constraint("ck_youtube_claims_state", type_="check")
            batch_op.create_check_constraint(
                "ck_youtube_claims_state",
                "state IN ('claimed', 'inspecting', 'download_pending', "
                "'downloading', 'downloaded', 'handoff', 'handed_off', "
                "'duplicate_resolved', 'cataloged', 'catalog_removed', 'failed')",
            )
            batch_op.drop_constraint(
                "ck_youtube_claims_terminal_payload", type_="check"
            )
            batch_op.create_check_constraint(
                "ck_youtube_claims_terminal_payload",
                _TERMINAL_PAYLOAD,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)

    op.create_table(
        "media_catalog_removal_receipts",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("occurred_at_ms", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.Text(), nullable=False),
        sa.Column("actor_key", sa.Text(), nullable=False),
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("display_title_snapshot", sa.Text(), nullable=True),
        sa.Column("acquisition_source", sa.Text(), nullable=False),
        sa.Column("storage_class", sa.Text(), nullable=False),
        sa.Column("was_published", sa.Integer(), nullable=False),
        sa.Column("published_at_ms", sa.Integer(), nullable=True),
        sa.Column("consequence_fingerprint", sa.Text(), nullable=False),
        sa.Column("catalog_outcome", sa.Text(), nullable=False),
        sa.Column("original_bytes_policy", sa.Text(), nullable=False),
        sa.Column("original_bytes_outcome", sa.Text(), nullable=False),
        sa.Column("youtube_claims_transitioned", sa.Integer(), nullable=False),
        sa.Column("upload_publications_detached", sa.Integer(), nullable=False),
        sa.Column("analysis_run_count", sa.Integer(), nullable=False),
        sa.Column("provider_submission_count", sa.Integer(), nullable=False),
        sa.Column("cover_artifact_digest", sa.Text(), nullable=True),
        sa.Column("preview_location_ids_json", sa.Text(), nullable=True),
        sa.Column("cover_cleanup_state", sa.Text(), nullable=False),
        sa.Column("preview_cleanup_state", sa.Text(), nullable=False),
        sa.Column("cleanup_updated_at_ms", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "length(id) = 36", name="ck_catalog_removal_receipts_id_length"
        ),
        sa.CheckConstraint(
            "occurred_at_ms >= 0",
            name="ck_catalog_removal_receipts_occurred_non_negative",
        ),
        sa.CheckConstraint(
            "length(request_id) >= 1 AND length(request_id) <= 64",
            name="ck_catalog_removal_receipts_request_id_length",
        ),
        sa.CheckConstraint(
            "length(actor_key) >= 1 AND length(actor_key) <= 254",
            name="ck_catalog_removal_receipts_actor_key_length",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36",
            name="ck_catalog_removal_receipts_media_id_length",
        ),
        sa.CheckConstraint(
            "display_title_snapshot IS NULL OR ("
            "length(display_title_snapshot) >= 1 "
            "AND length(display_title_snapshot) <= 240)",
            name="ck_catalog_removal_receipts_title_length",
        ),
        sa.CheckConstraint(
            "acquisition_source IN ("
            "'unknown', 'manual_upload', 'library_scan', 'youtube_manual_claim')",
            name="ck_catalog_removal_receipts_acquisition_source",
        ),
        sa.CheckConstraint(
            "storage_class IN ("
            "'operator_managed', 'server_managed_upload', 'unknown')",
            name="ck_catalog_removal_receipts_storage_class",
        ),
        sa.CheckConstraint(
            "was_published IN (0, 1)",
            name="ck_catalog_removal_receipts_was_published",
        ),
        sa.CheckConstraint(
            "published_at_ms IS NULL OR published_at_ms >= 0",
            name="ck_catalog_removal_receipts_published_at",
        ),
        sa.CheckConstraint(
            "length(consequence_fingerprint) = 64 "
            "AND consequence_fingerprint = lower(consequence_fingerprint) "
            "AND consequence_fingerprint NOT GLOB '*[^0-9a-f]*'",
            name="ck_catalog_removal_receipts_fingerprint",
        ),
        sa.CheckConstraint(
            "catalog_outcome = 'removed'",
            name="ck_catalog_removal_receipts_catalog_outcome",
        ),
        sa.CheckConstraint(
            "original_bytes_policy = 'retain_all'",
            name="ck_catalog_removal_receipts_bytes_policy",
        ),
        sa.CheckConstraint(
            "original_bytes_outcome IN ("
            "'retained_operator_managed', 'retained_server_managed', "
            "'retained_already_missing', 'retained_unknown')",
            name="ck_catalog_removal_receipts_bytes_outcome",
        ),
        sa.CheckConstraint(
            "youtube_claims_transitioned >= 0",
            name="ck_catalog_removal_receipts_youtube_count",
        ),
        sa.CheckConstraint(
            "upload_publications_detached >= 0",
            name="ck_catalog_removal_receipts_upload_count",
        ),
        sa.CheckConstraint(
            "analysis_run_count >= 0",
            name="ck_catalog_removal_receipts_analysis_count",
        ),
        sa.CheckConstraint(
            "provider_submission_count >= 0",
            name="ck_catalog_removal_receipts_provider_count",
        ),
        sa.CheckConstraint(
            "cover_artifact_digest IS NULL OR ("
            "length(cover_artifact_digest) = 64 "
            "AND cover_artifact_digest = lower(cover_artifact_digest) "
            "AND cover_artifact_digest NOT GLOB '*[^0-9a-f]*')",
            name="ck_catalog_removal_receipts_cover_digest",
        ),
        sa.CheckConstraint(
            "cover_cleanup_state IN ('none', 'pending', 'complete', 'failed')",
            name="ck_catalog_removal_receipts_cover_cleanup",
        ),
        sa.CheckConstraint(
            "preview_cleanup_state IN ('none', 'pending', 'complete', 'failed')",
            name="ck_catalog_removal_receipts_preview_cleanup",
        ),
        sa.CheckConstraint(
            "cleanup_updated_at_ms IS NULL OR cleanup_updated_at_ms >= occurred_at_ms",
            name="ck_catalog_removal_receipts_cleanup_updated",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_catalog_removal_receipts_occurred",
        "media_catalog_removal_receipts",
        ["occurred_at_ms", "id"],
    )
    op.create_index(
        "ix_catalog_removal_receipts_media_id",
        "media_catalog_removal_receipts",
        ["media_id", "occurred_at_ms"],
    )


def downgrade() -> None:
    """Refuse downgrade; catalog-removal semantics are forward-only."""
    raise RuntimeError(
        "Cannot downgrade catalog-removal receipts and YouTube catalog_removed "
        "semantics."
    )
