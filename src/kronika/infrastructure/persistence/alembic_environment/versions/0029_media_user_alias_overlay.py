"""Add per-user media alias overlay and X claim pending-alias tables (0029)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None

_LOGIN_KEY_SQL = (
    "length(login_key) >= 1 AND length(login_key) <= 254 "
    "AND login_key = lower(login_key) "
    "AND instr(login_key, ' ') = 0 "
    "AND instr(login_key, char(9)) = 0 "
    "AND instr(login_key, char(10)) = 0 "
    "AND instr(login_key, char(13)) = 0"
)


def upgrade() -> None:
    op.create_table(
        "media_user_aliases",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("login_key", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("media_id", "login_key", name="pk_media_user_aliases"),
        sa.ForeignKeyConstraint(
            ["media_id"],
            ["logical_media.id"],
            name="fk_media_user_aliases_media_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint("length(media_id) = 36", name="ck_media_user_aliases_media_id_length"),
        sa.CheckConstraint(_LOGIN_KEY_SQL, name="ck_media_user_aliases_login_key"),
        sa.CheckConstraint(
            "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
            name="ck_media_user_aliases_title_length",
        ),
        sa.CheckConstraint(
            "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
            name="ck_media_user_aliases_description_length",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_media_user_aliases_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_media_user_aliases_updated_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_media_user_aliases_updated_not_before_created",
        ),
    )
    op.create_index(
        "ix_media_user_aliases_login_key",
        "media_user_aliases",
        ["login_key", "updated_at_ms"],
    )

    op.create_table(
        "media_user_alias_tags",
        sa.Column("media_id", sa.Text(), nullable=False),
        sa.Column("login_key", sa.Text(), nullable=False),
        sa.Column("tag_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "media_id", "login_key", "tag_key", name="pk_media_user_alias_tags"
        ),
        sa.ForeignKeyConstraint(
            ["media_id", "login_key"],
            ["media_user_aliases.media_id", "media_user_aliases.login_key"],
            name="fk_media_user_alias_tags_alias",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_key"],
            ["canonical_tags.key"],
            name="fk_media_user_alias_tags_tag_key",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "media_id",
            "login_key",
            "position",
            name="uq_media_user_alias_tags_position",
        ),
        sa.CheckConstraint(
            "length(media_id) = 36", name="ck_media_user_alias_tags_media_id_length"
        ),
        sa.CheckConstraint(_LOGIN_KEY_SQL, name="ck_media_user_alias_tags_login_key"),
        sa.CheckConstraint(
            "length(tag_key) >= 1 AND length(tag_key) <= 64",
            name="ck_media_user_alias_tags_tag_key_length",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 32",
            name="ck_media_user_alias_tags_position_range",
        ),
    )

    op.create_table(
        "x_claim_pending_aliases",
        sa.Column("claim_id", sa.Text(), nullable=False),
        sa.Column("login_key", sa.Text(), nullable=False),
        sa.Column("display_title", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at_ms", sa.Integer(), nullable=False),
        sa.Column("updated_at_ms", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("claim_id", name="pk_x_claim_pending_aliases"),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["x_post_claims.id"],
            name="fk_x_claim_pending_aliases_claim_id",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "length(claim_id) = 36", name="ck_x_claim_pending_aliases_claim_id_length"
        ),
        sa.CheckConstraint(_LOGIN_KEY_SQL, name="ck_x_claim_pending_aliases_login_key"),
        sa.CheckConstraint(
            "display_title IS NULL OR (length(display_title) >= 1 AND length(display_title) <= 240)",
            name="ck_x_claim_pending_aliases_title_length",
        ),
        sa.CheckConstraint(
            "description IS NULL OR (length(description) >= 1 AND length(description) <= 10000)",
            name="ck_x_claim_pending_aliases_description_length",
        ),
        sa.CheckConstraint(
            "created_at_ms >= 0",
            name="ck_x_claim_pending_aliases_created_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= 0",
            name="ck_x_claim_pending_aliases_updated_at_ms_non_negative",
        ),
        sa.CheckConstraint(
            "updated_at_ms >= created_at_ms",
            name="ck_x_claim_pending_aliases_updated_not_before_created",
        ),
    )

    op.create_table(
        "x_claim_pending_alias_tags",
        sa.Column("claim_id", sa.Text(), nullable=False),
        sa.Column("tag_key", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "claim_id", "tag_key", name="pk_x_claim_pending_alias_tags"
        ),
        sa.ForeignKeyConstraint(
            ["claim_id"],
            ["x_claim_pending_aliases.claim_id"],
            name="fk_x_claim_pending_alias_tags_pending",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tag_key"],
            ["canonical_tags.key"],
            name="fk_x_claim_pending_alias_tags_tag_key",
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint(
            "claim_id", "position", name="uq_x_claim_pending_alias_tags_position"
        ),
        sa.CheckConstraint(
            "length(claim_id) = 36",
            name="ck_x_claim_pending_alias_tags_claim_id_length",
        ),
        sa.CheckConstraint(
            "length(tag_key) >= 1 AND length(tag_key) <= 64",
            name="ck_x_claim_pending_alias_tags_tag_key_length",
        ),
        sa.CheckConstraint(
            "position >= 0 AND position < 32",
            name="ck_x_claim_pending_alias_tags_position_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("x_claim_pending_alias_tags")
    op.drop_table("x_claim_pending_aliases")
    op.drop_table("media_user_alias_tags")
    op.drop_table("media_user_aliases")
