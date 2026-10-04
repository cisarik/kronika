"""Add durable YouTube manual-acquisition provenance."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None

_DOWNGRADE_PROVENANCE_MESSAGE = (
    "Cannot downgrade YouTube acquisition provenance while claims exist."
)


def upgrade() -> None:
    """Create the source-specific durable acquisition claim and indexes."""
    op.create_table(
        "youtube_acquisition_claims",
        sa.Column("id", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("acquisition_source", sa.Text(), nullable=False),
        sa.Column("submitted_url", sa.Text(), nullable=False),
        sa.Column("canonical_url", sa.Text(), nullable=False),
        sa.Column("youtube_video_id", sa.Text(), nullable=False),
        sa.Column("extractor_key", sa.Text(), nullable=False),
        sa.Column("retry_of_claim_id", sa.Text(), nullable=True),
        sa.Column("resolved_claim_id", sa.Text(), nullable=True),
        sa.Column("upload_id", sa.Text(), nullable=True),
        sa.Column("media_id", sa.Text(), nullable=True),
        sa.Column("media_location_id", sa.Text(), nullable=True),
        sa.Column("confirmation_method", sa.Text(), nullable=False),
        sa.Column("confirmed_at_ms", sa.Integer(), nullable=False),
        sa.Column("upstream_title", sa.Text(), nullable=True),
        sa.Column("upstream_channel", sa.Text(), nullable=True),
        sa.Column("upstream_channel_id", sa.Text(), nullable=True),
        sa.Column("upstream_source_date", sa.Text(), nullable=True),
        sa.Column("downloader_name", sa.Text(), nullable=True),
        sa.Column("downloader_version", sa.Text(), nullable=True),
        sa.Column("extractor_version", sa.Text(), nullable=True),
        sa.Column("selected_video_format_id", sa.Text(), nullable=True),
        sa.Column("selected_audio_format_id", sa.Text(), nullable=True),
        sa.Column("remote_filename", sa.Text(), nullable=True),
        sa.Column("generated_filename", sa.Text(), nullable=False),
        sa.Column("staging_key", sa.Text(), nullable=False),
        sa.Column("downloaded_size_bytes", sa.Integer(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("downloaded_at_ms", sa.Integer(), nullable=True),
        sa.Column("completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("failure_stage", sa.Text(), nullable=True),
        sa.Column("failure_code", sa.Text(), nullable=True),
        sa.Column("cleanup_state", sa.Text(), nullable=False),
        sa.Column("cleanup_completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_youtube_claims_id_length"),
        sa.CheckConstraint(
            "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
            "'downloaded', 'handoff', 'handed_off', 'duplicate_resolved', "
            "'cataloged', 'failed')",
            name="ck_youtube_claims_state",
        ),
        sa.CheckConstraint(
            "acquisition_source = 'youtube_manual_claim'",
            name="ck_youtube_claims_acquisition_source",
        ),
        sa.CheckConstraint(
            "length(submitted_url) >= 1 AND length(submitted_url) <= 2048",
            name="ck_youtube_claims_submitted_url_length",
        ),
        sa.CheckConstraint(
            "canonical_url = 'https://www.youtube.com/watch?v=' || youtube_video_id",
            name="ck_youtube_claims_canonical_identity",
        ),
        sa.CheckConstraint(
            "length(youtube_video_id) = 11 "
            "AND youtube_video_id NOT GLOB '*[^A-Za-z0-9_-]*'",
            name="ck_youtube_claims_video_id",
        ),
        sa.CheckConstraint(
            "extractor_key = 'Youtube'",
            name="ck_youtube_claims_extractor_key",
        ),
        sa.CheckConstraint(
            "retry_of_claim_id IS NULL OR "
            "(length(retry_of_claim_id) = 36 AND retry_of_claim_id != id)",
            name="ck_youtube_claims_retry_lineage",
        ),
        sa.CheckConstraint(
            "resolved_claim_id IS NULL OR "
            "(length(resolved_claim_id) = 36 AND resolved_claim_id != id)",
            name="ck_youtube_claims_resolution_lineage",
        ),
        sa.CheckConstraint(
            "upload_id IS NULL OR length(upload_id) = 36",
            name="ck_youtube_claims_upload_id_length",
        ),
        sa.CheckConstraint(
            "(media_id IS NULL AND media_location_id IS NULL) "
            "OR (length(media_id) = 36 AND length(media_location_id) = 36)",
            name="ck_youtube_claims_catalog_linkage_pair",
        ),
        sa.CheckConstraint(
            "confirmation_method IN ('interactive', 'yes_flag')",
            name="ck_youtube_claims_confirmation_method",
        ),
        sa.CheckConstraint(
            "confirmed_at_ms >= created_at_ms",
            name="ck_youtube_claims_confirmed_at",
        ),
        sa.CheckConstraint(
            "upstream_title IS NULL OR "
            "(length(upstream_title) >= 1 AND length(upstream_title) <= 500)",
            name="ck_youtube_claims_upstream_title_length",
        ),
        sa.CheckConstraint(
            "upstream_channel IS NULL OR "
            "(length(upstream_channel) >= 1 AND length(upstream_channel) <= 200)",
            name="ck_youtube_claims_upstream_channel_length",
        ),
        sa.CheckConstraint(
            "upstream_channel_id IS NULL OR "
            "(length(upstream_channel_id) >= 1 "
            "AND length(upstream_channel_id) <= 128)",
            name="ck_youtube_claims_upstream_channel_id_length",
        ),
        sa.CheckConstraint(
            "upstream_source_date IS NULL OR "
            "(length(upstream_source_date) = 10 "
            "AND upstream_source_date GLOB "
            "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')",
            name="ck_youtube_claims_upstream_source_date",
        ),
        sa.CheckConstraint(
            "downloader_name IS NULL OR "
            "(length(downloader_name) >= 1 AND length(downloader_name) <= 120)",
            name="ck_youtube_claims_downloader_name_length",
        ),
        sa.CheckConstraint(
            "downloader_version IS NULL OR "
            "(length(downloader_version) >= 1 AND length(downloader_version) <= 120)",
            name="ck_youtube_claims_downloader_version_length",
        ),
        sa.CheckConstraint(
            "extractor_version IS NULL OR "
            "(length(extractor_version) >= 1 AND length(extractor_version) <= 120)",
            name="ck_youtube_claims_extractor_version_length",
        ),
        sa.CheckConstraint(
            "selected_video_format_id IS NULL OR "
            "(length(selected_video_format_id) >= 1 "
            "AND length(selected_video_format_id) <= 120)",
            name="ck_youtube_claims_video_format_id_length",
        ),
        sa.CheckConstraint(
            "selected_audio_format_id IS NULL OR "
            "(length(selected_audio_format_id) >= 1 "
            "AND length(selected_audio_format_id) <= 120)",
            name="ck_youtube_claims_audio_format_id_length",
        ),
        sa.CheckConstraint(
            "remote_filename IS NULL OR "
            "(length(remote_filename) >= 1 AND length(remote_filename) <= 500)",
            name="ck_youtube_claims_remote_filename_length",
        ),
        sa.CheckConstraint(
            "generated_filename = 'youtube-' || youtube_video_id || '.mp4'",
            name="ck_youtube_claims_generated_filename",
        ),
        sa.CheckConstraint(
            "length(staging_key) = 32 AND staging_key = lower(staging_key) "
            "AND staging_key NOT GLOB '*[^0-9a-f]*'",
            name="ck_youtube_claims_staging_key",
        ),
        sa.CheckConstraint(
            "downloaded_size_bytes IS NULL OR downloaded_size_bytes > 0",
            name="ck_youtube_claims_downloaded_size",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0 AND updated_at_ms >= created_at_ms",
            name="ck_youtube_claims_timestamps",
        ),
        sa.CheckConstraint(
            "downloaded_at_ms IS NULL OR downloaded_at_ms >= created_at_ms",
            name="ck_youtube_claims_downloaded_at",
        ),
        sa.CheckConstraint(
            "completed_at_ms IS NULL OR completed_at_ms >= created_at_ms",
            name="ck_youtube_claims_completed_at",
        ),
        sa.CheckConstraint(
            "(failure_stage IS NULL AND failure_code IS NULL) OR ("
            "failure_stage IN ('configuration', 'inspection', 'download', "
            "'staging', 'handoff', 'downstream', 'cleanup', 'internal') "
            "AND length(failure_code) >= 1 AND length(failure_code) <= 80 "
            "AND failure_code NOT GLOB '*[^A-Z0-9_]*')",
            name="ck_youtube_claims_failure_pair",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('pending', 'complete') "
            "AND ((cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
            "OR (cleanup_state = 'complete' "
            "AND cleanup_completed_at_ms IS NOT NULL))",
            name="ck_youtube_claims_cleanup_pair",
        ),
        sa.CheckConstraint(
            "version >= 0",
            name="ck_youtube_claims_version",
        ),
        sa.CheckConstraint(
            "(state IN ('downloaded', 'handoff', 'handed_off', 'cataloged') "
            "AND downloaded_size_bytes IS NOT NULL "
            "AND downloaded_at_ms IS NOT NULL) "
            "OR state NOT IN ('downloaded', 'handoff', 'handed_off', 'cataloged')",
            name="ck_youtube_claims_download_payload",
        ),
        sa.CheckConstraint(
            "(state IN ('handed_off', 'cataloged') AND upload_id IS NOT NULL) "
            "OR state NOT IN ('handed_off', 'cataloged')",
            name="ck_youtube_claims_handoff_linkage",
        ),
        sa.CheckConstraint(
            "(state = 'cataloged' AND media_id IS NOT NULL "
            "AND completed_at_ms IS NOT NULL AND failure_code IS NULL) "
            "OR (state = 'duplicate_resolved' "
            "AND media_id IS NOT NULL AND completed_at_ms IS NOT NULL "
            "AND failure_code IS NULL) "
            "OR (state = 'failed' AND failure_code IS NOT NULL "
            "AND completed_at_ms IS NOT NULL) "
            "OR (state NOT IN ('cataloged', 'duplicate_resolved', 'failed') "
            "AND completed_at_ms IS NULL AND failure_code IS NULL)",
            name="ck_youtube_claims_terminal_payload",
        ),
        sa.ForeignKeyConstraint(
            ["retry_of_claim_id"],
            ["youtube_acquisition_claims.id"],
            name="fk_youtube_claims_retry_of_claim_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["resolved_claim_id"],
            ["youtube_acquisition_claims.id"],
            name="fk_youtube_claims_resolved_claim_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["upload_id"],
            ["upload_sessions.id"],
            name="fk_youtube_claims_upload_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_youtube_claims_media_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["media_location_id"],
            ["physical_media_locations.id"],
            name="fk_youtube_claims_media_location_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_youtube_acquisition_claims"),
        sa.UniqueConstraint("staging_key", name="uq_youtube_claims_staging_key"),
        sa.UniqueConstraint("upload_id", name="uq_youtube_claims_upload_id"),
    )
    op.create_index(
        "uq_youtube_claims_active_source_identity",
        "youtube_acquisition_claims",
        ["extractor_key", "youtube_video_id"],
        unique=True,
        sqlite_where=sa.text(
            "state IN ('claimed', 'inspecting', 'download_pending', 'downloading', "
            "'downloaded', 'handoff', 'handed_off')"
        ),
    )
    op.create_index(
        "ix_youtube_claims_source_identity",
        "youtube_acquisition_claims",
        ["extractor_key", "youtube_video_id", "created_at_ms"],
    )
    op.create_index(
        "ix_youtube_claims_state",
        "youtube_acquisition_claims",
        ["state", "updated_at_ms", "id"],
    )
    op.create_index(
        "ix_youtube_claims_retry_of",
        "youtube_acquisition_claims",
        ["retry_of_claim_id"],
    )
    op.create_index(
        "ix_youtube_claims_resolved_claim",
        "youtube_acquisition_claims",
        ["resolved_claim_id"],
    )
    op.create_index(
        "ix_youtube_claims_media",
        "youtube_acquisition_claims",
        ["media_id", "media_location_id"],
    )


def downgrade() -> None:
    """Refuse provenance loss and remove only an empty claim table."""
    bind = op.get_bind()
    claim_count = bind.exec_driver_sql(
        "SELECT COUNT(*) FROM youtube_acquisition_claims"
    ).scalar_one()
    if claim_count:
        raise RuntimeError(_DOWNGRADE_PROVENANCE_MESSAGE)
    op.drop_index(
        "ix_youtube_claims_media",
        table_name="youtube_acquisition_claims",
    )
    op.drop_index(
        "ix_youtube_claims_resolved_claim",
        table_name="youtube_acquisition_claims",
    )
    op.drop_index(
        "ix_youtube_claims_retry_of",
        table_name="youtube_acquisition_claims",
    )
    op.drop_index(
        "ix_youtube_claims_state",
        table_name="youtube_acquisition_claims",
    )
    op.drop_index(
        "ix_youtube_claims_source_identity",
        table_name="youtube_acquisition_claims",
    )
    op.drop_index(
        "uq_youtube_claims_active_source_identity",
        table_name="youtube_acquisition_claims",
    )
    op.drop_table("youtube_acquisition_claims")
