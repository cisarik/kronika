"""Add first-class still-image media kinds and formats."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from framenest.infrastructure.persistence.sqlite_batch_fk import (
    disable_sqlite_foreign_keys_for_batch_rebuild,
    enable_and_verify_sqlite_foreign_keys,
)

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None

_VALIDATION_EVIDENCE_PAIR_SQL = (
    "(validated_media_kind IS NULL AND validated_format IS NULL) "
    "OR (validated_media_kind = 'animated_image' AND validated_format = 'gif') "
    "OR (validated_media_kind = 'video' AND validated_format = 'mp4') "
    "OR (validated_media_kind = 'image' AND validated_format = 'jpg') "
    "OR (validated_media_kind = 'image' AND validated_format = 'png')"
)

_PUBLICATION_EVIDENCE_PAIR_SQL = (
    "(validated_media_kind = 'animated_image' AND validated_format = 'gif') "
    "OR (validated_media_kind = 'video' AND validated_format = 'mp4') "
    "OR (validated_media_kind = 'image' AND validated_format = 'jpg') "
    "OR (validated_media_kind = 'image' AND validated_format = 'png')"
)

_RELATIVE_TARGET_SQL = (
    "length(relative_target) = 36 "
    "AND relative_target = lower(relative_target) "
    "AND substr(relative_target, 1, 32) NOT GLOB '*[^0-9a-f]*' "
    "AND relative_target NOT GLOB '*/*' "
    "AND relative_target NOT GLOB '*\\*' "
    "AND substr(relative_target, 1, 32) = replace(publication_id, '-', '') "
    "AND ((validated_format = 'gif' AND substr(relative_target, 33) = '.gif') "
    "OR (validated_format = 'mp4' AND substr(relative_target, 33) = '.mp4') "
    "OR (validated_format = 'jpg' AND substr(relative_target, 33) = '.jpg') "
    "OR (validated_format = 'png' AND substr(relative_target, 33) = '.png'))"
)

_LEGACY_VALIDATION_EVIDENCE_PAIR_SQL = (
    "(validated_media_kind IS NULL AND validated_format IS NULL) "
    "OR (validated_media_kind = 'animated_image' AND validated_format = 'gif') "
    "OR (validated_media_kind = 'video' AND validated_format = 'mp4')"
)

_LEGACY_PUBLICATION_EVIDENCE_PAIR_SQL = (
    "(validated_media_kind = 'animated_image' AND validated_format = 'gif') "
    "OR (validated_media_kind = 'video' AND validated_format = 'mp4')"
)

_LEGACY_RELATIVE_TARGET_SQL = (
    "length(relative_target) = 36 "
    "AND relative_target = lower(relative_target) "
    "AND substr(relative_target, 1, 32) NOT GLOB '*[^0-9a-f]*' "
    "AND relative_target NOT GLOB '*/*' "
    "AND relative_target NOT GLOB '*\\*' "
    "AND substr(relative_target, 1, 32) = replace(publication_id, '-', '') "
    "AND ((validated_format = 'gif' AND substr(relative_target, 33) = '.gif') "
    "OR (validated_format = 'mp4' AND substr(relative_target, 33) = '.mp4'))"
)

_FK_FAILURE_MESSAGE = (
    "Foreign key check failed after still-image media-kind migration."
)


def upgrade() -> None:
    """Allow durable still-image kinds without rewriting existing rows."""
    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("logical_media") as batch_op:
            batch_op.drop_constraint("ck_logical_media_kind", type_="check")
            batch_op.create_check_constraint(
                "ck_logical_media_kind",
                "media_kind IN ('video', 'animated_image', 'image')",
            )

        with op.batch_alter_table("upload_sessions") as batch_op:
            batch_op.drop_constraint(
                "ck_upload_sessions_validation_evidence_pair",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_upload_sessions_validation_evidence_pair",
                _VALIDATION_EVIDENCE_PAIR_SQL,
            )

        with op.batch_alter_table("upload_publications") as batch_op:
            batch_op.drop_constraint(
                "ck_upload_publications_relative_target_opaque",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_upload_publications_validation_evidence_pair",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_upload_publications_relative_target_opaque",
                _RELATIVE_TARGET_SQL,
            )
            batch_op.create_check_constraint(
                "ck_upload_publications_validation_evidence_pair",
                _PUBLICATION_EVIDENCE_PAIR_SQL,
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)


def downgrade() -> None:
    """Restore pre-still-image kind and format constraints."""
    connection = op.get_bind()
    image_media_count = connection.execute(
        sa.text("SELECT COUNT(*) FROM logical_media WHERE media_kind = 'image'")
    ).scalar_one()
    image_upload_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM upload_sessions
            WHERE validated_media_kind = 'image'
               OR validated_format IN ('jpg', 'png')
            """
        )
    ).scalar_one()
    image_publication_count = connection.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM upload_publications
            WHERE validated_media_kind = 'image'
               OR validated_format IN ('jpg', 'png')
            """
        )
    ).scalar_one()
    if image_media_count or image_upload_count or image_publication_count:
        raise RuntimeError("Still-image downgrade blocked by existing image rows.")

    disable_sqlite_foreign_keys_for_batch_rebuild()
    try:
        with op.batch_alter_table("upload_publications") as batch_op:
            batch_op.drop_constraint(
                "ck_upload_publications_relative_target_opaque",
                type_="check",
            )
            batch_op.drop_constraint(
                "ck_upload_publications_validation_evidence_pair",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_upload_publications_relative_target_opaque",
                _LEGACY_RELATIVE_TARGET_SQL,
            )
            batch_op.create_check_constraint(
                "ck_upload_publications_validation_evidence_pair",
                _LEGACY_PUBLICATION_EVIDENCE_PAIR_SQL,
            )

        with op.batch_alter_table("upload_sessions") as batch_op:
            batch_op.drop_constraint(
                "ck_upload_sessions_validation_evidence_pair",
                type_="check",
            )
            batch_op.create_check_constraint(
                "ck_upload_sessions_validation_evidence_pair",
                _LEGACY_VALIDATION_EVIDENCE_PAIR_SQL,
            )

        with op.batch_alter_table("logical_media") as batch_op:
            batch_op.drop_constraint("ck_logical_media_kind", type_="check")
            batch_op.create_check_constraint(
                "ck_logical_media_kind",
                "media_kind IN ('video', 'animated_image')",
            )
    finally:
        enable_and_verify_sqlite_foreign_keys(failure_message=_FK_FAILURE_MESSAGE)
