"""Canonical upload byte identities."""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

_VALIDATED_UPLOAD_SESSION_STATES = (
    "duplicate_pending",
    "publish_pending",
    "published",
    "cataloged",
)


def upgrade() -> None:
    """Create canonical byte identities and backfill coherent successful uploads."""
    _fail_if_incoherent_successful_upload_sessions_exist()
    op.create_table(
        "media_byte_identities",
        sa.Column("id", sa.Text(), primary_key=True, nullable=False),
        sa.Column("checksum_algorithm", sa.Text(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_hex", sa.Text(), nullable=False),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint("length(id) = 36", name="ck_media_byte_identities_id_length"),
        sa.CheckConstraint(
            "checksum_algorithm = 'sha256'",
            name="ck_media_byte_identities_algorithm_sha256",
        ),
        sa.CheckConstraint("size_bytes > 0", name="ck_media_byte_identities_size_positive"),
        sa.CheckConstraint(
            "length(checksum_hex) = 64 "
            "AND checksum_hex = lower(checksum_hex) "
            "AND checksum_hex NOT GLOB '*[^0-9a-f]*'",
            name="ck_media_byte_identities_checksum_hex",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_media_byte_identities_created_at_ms_non_negative",
        ),
        sa.UniqueConstraint(
            "checksum_algorithm",
            "size_bytes",
            "checksum_hex",
            name="uq_media_byte_identities_tuple",
        ),
    )
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.drop_constraint(
            "ck_upload_sessions_validated_states_have_evidence",
            type_="check",
        )
        batch_op.add_column(sa.Column("byte_identity_id", sa.Text(), nullable=True))
        batch_op.create_foreign_key(
            "fk_upload_sessions_byte_identity_id",
            "media_byte_identities",
            ["byte_identity_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index(
            "ix_upload_sessions_byte_identity_id",
            ["byte_identity_id"],
        )
    _backfill_successful_upload_byte_identities()
    with op.batch_alter_table("upload_sessions") as batch_op:
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
                    & sa.column("byte_identity_id").is_not(None)
                ),
            ),
        )


def downgrade() -> None:
    """Remove canonical byte identity links and table introduced by revision 0011."""
    with op.batch_alter_table("upload_sessions") as batch_op:
        batch_op.drop_index("ix_upload_sessions_byte_identity_id")
        batch_op.drop_constraint(
            "ck_upload_sessions_validated_states_have_evidence",
            type_="check",
        )
        batch_op.drop_constraint(
            "fk_upload_sessions_byte_identity_id",
            type_="foreignkey",
        )
        batch_op.drop_column("byte_identity_id")
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
    op.drop_table("media_byte_identities")


def _fail_if_incoherent_successful_upload_sessions_exist() -> None:
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
            AND (
                checksum_algorithm IS NULL
                OR checksum_algorithm != 'sha256'
                OR checksum_hex IS NULL
                OR length(checksum_hex) != 64
                OR checksum_hex != lower(checksum_hex)
                OR checksum_hex GLOB '*[^0-9a-f]*'
                OR declared_size_bytes IS NULL
                OR declared_size_bytes <= 0
                OR received_size_bytes IS NULL
                OR received_size_bytes <= 0
                OR declared_size_bytes != received_size_bytes
                OR validated_media_kind IS NULL
                OR validated_format IS NULL
                OR NOT (
                    (validated_media_kind = 'animated_image' AND validated_format = 'gif')
                    OR (validated_media_kind = 'video' AND validated_format = 'mp4')
                )
            )
            """
        )
    ).scalar_one()
    if invalid_count:
        raise RuntimeError("Upload byte identity migration failed.")


def _backfill_successful_upload_byte_identities() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT
                checksum_algorithm,
                declared_size_bytes AS size_bytes,
                checksum_hex,
                MIN(updated_at_ms) AS created_at_ms
            FROM upload_sessions
            WHERE state IN (
                'duplicate_pending',
                'publish_pending',
                'published',
                'cataloged'
            )
            GROUP BY checksum_algorithm, declared_size_bytes, checksum_hex
            ORDER BY checksum_algorithm, declared_size_bytes, checksum_hex
            """
        )
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(
                """
                INSERT INTO media_byte_identities (
                    id,
                    checksum_algorithm,
                    size_bytes,
                    checksum_hex,
                    created_at_ms
                ) VALUES (
                    :id,
                    :checksum_algorithm,
                    :size_bytes,
                    :checksum_hex,
                    :created_at_ms
                )
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "checksum_algorithm": row["checksum_algorithm"],
                "size_bytes": row["size_bytes"],
                "checksum_hex": row["checksum_hex"],
                "created_at_ms": row["created_at_ms"],
            },
        )
    connection.execute(
        sa.text(
            """
            UPDATE upload_sessions
            SET byte_identity_id = (
                SELECT media_byte_identities.id
                FROM media_byte_identities
                WHERE media_byte_identities.checksum_algorithm =
                    upload_sessions.checksum_algorithm
                AND media_byte_identities.size_bytes =
                    upload_sessions.declared_size_bytes
                AND media_byte_identities.checksum_hex =
                    upload_sessions.checksum_hex
            )
            WHERE state IN (
                'duplicate_pending',
                'publish_pending',
                'published',
                'cataloged'
            )
            """
        )
    )
