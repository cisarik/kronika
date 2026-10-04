"""Durable atomic upload publication provenance."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add empty publication provenance without inventing historical ownership."""
    op.create_table(
        "upload_publications",
        sa.Column(
            "upload_id",
            sa.Text(),
            sa.ForeignKey("upload_sessions.id", ondelete="RESTRICT"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("publication_id", sa.Text(), nullable=False),
        sa.Column(
            "destination_id",
            sa.Text(),
            sa.ForeignKey("libraries.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("relative_target", sa.Text(), nullable=False),
        sa.Column(
            "byte_identity_id",
            sa.Text(),
            sa.ForeignKey("media_byte_identities.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("expected_size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_algorithm", sa.Text(), nullable=False),
        sa.Column("checksum_hex", sa.Text(), nullable=False),
        sa.Column("validated_media_kind", sa.Text(), nullable=False),
        sa.Column("validated_format", sa.Text(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False),
        sa.Column("cleanup_state", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.Column("verified_at_ms", sa.Integer(), nullable=True),
        sa.Column("cleanup_completed_at_ms", sa.Integer(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "length(upload_id) = 36",
            name="ck_upload_publications_upload_id_length",
        ),
        sa.CheckConstraint(
            "length(publication_id) = 36",
            name="ck_upload_publications_publication_id_length",
        ),
        sa.CheckConstraint(
            "length(destination_id) = 36",
            name="ck_upload_publications_destination_id_length",
        ),
        sa.CheckConstraint(
            "length(byte_identity_id) = 36",
            name="ck_upload_publications_byte_identity_id_length",
        ),
        sa.CheckConstraint(
            "length(relative_target) = 36 "
            "AND relative_target = lower(relative_target) "
            "AND substr(relative_target, 1, 32) NOT GLOB '*[^0-9a-f]*' "
            "AND relative_target NOT GLOB '*/*' "
            "AND relative_target NOT GLOB '*\\*' "
            "AND substr(relative_target, 1, 32) = replace(publication_id, '-', '') "
            "AND ((validated_format = 'gif' AND substr(relative_target, 33) = '.gif') "
            "OR (validated_format = 'mp4' AND substr(relative_target, 33) = '.mp4'))",
            name="ck_upload_publications_relative_target_opaque",
        ),
        sa.CheckConstraint(
            "expected_size_bytes > 0",
            name="ck_upload_publications_expected_size_positive",
        ),
        sa.CheckConstraint(
            "checksum_algorithm = 'sha256'",
            name="ck_upload_publications_checksum_algorithm",
        ),
        sa.CheckConstraint(
            "length(checksum_hex) = 64 "
            "AND checksum_hex = lower(checksum_hex) "
            "AND checksum_hex NOT GLOB '*[^0-9a-f]*'",
            name="ck_upload_publications_checksum_hex",
        ),
        sa.CheckConstraint(
            "(validated_media_kind = 'animated_image' AND validated_format = 'gif') "
            "OR (validated_media_kind = 'video' AND validated_format = 'mp4')",
            name="ck_upload_publications_validation_evidence_pair",
        ),
        sa.CheckConstraint(
            "state IN ('reserved', 'verified')",
            name="ck_upload_publications_state",
        ),
        sa.CheckConstraint(
            "cleanup_state IN ('pending', 'complete')",
            name="ck_upload_publications_cleanup_state",
        ),
        sa.CheckConstraint(
            "(state = 'reserved' AND cleanup_state = 'pending' "
            "AND verified_at_ms IS NULL AND cleanup_completed_at_ms IS NULL) "
            "OR (state = 'verified' AND verified_at_ms IS NOT NULL "
            "AND ((cleanup_state = 'pending' AND cleanup_completed_at_ms IS NULL) "
            "OR (cleanup_state = 'complete' AND cleanup_completed_at_ms IS NOT NULL)))",
            name="ck_upload_publications_progress",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0 AND updated_at_ms >= created_at_ms",
            name="ck_upload_publications_timestamps",
        ),
        sa.CheckConstraint(
            "verified_at_ms IS NULL OR verified_at_ms >= created_at_ms",
            name="ck_upload_publications_verified_at_ms",
        ),
        sa.CheckConstraint(
            "cleanup_completed_at_ms IS NULL "
            "OR cleanup_completed_at_ms >= verified_at_ms",
            name="ck_upload_publications_cleanup_completed_at_ms",
        ),
        sa.CheckConstraint(
            "version >= 0",
            name="ck_upload_publications_version_non_negative",
        ),
        sa.UniqueConstraint(
            "publication_id",
            name="uq_upload_publications_publication_id",
        ),
        sa.UniqueConstraint(
            "destination_id",
            "relative_target",
            name="uq_upload_publications_destination_target",
        ),
    )
    op.create_index(
        "ix_upload_publications_state_cleanup",
        "upload_publications",
        ["state", "cleanup_state", "updated_at_ms", "upload_id"],
    )
    op.create_index(
        "ix_upload_publications_byte_identity_id",
        "upload_publications",
        ["byte_identity_id"],
    )


def downgrade() -> None:
    """Remove only publication provenance introduced by revision 0013."""
    op.drop_index(
        "ix_upload_publications_byte_identity_id",
        table_name="upload_publications",
    )
    op.drop_index(
        "ix_upload_publications_state_cleanup",
        table_name="upload_publications",
    )
    op.drop_table("upload_publications")
