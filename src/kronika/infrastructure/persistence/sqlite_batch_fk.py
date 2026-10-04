"""SQLite helpers for Alembic batch rebuilds that drop referenced parent tables."""

from __future__ import annotations

from alembic import op


def disable_sqlite_foreign_keys_for_batch_rebuild() -> None:
    """Disable FK enforcement outside a transaction for SQLite table rebuilds.

    SQLite ignores ``PRAGMA foreign_keys`` changes inside an open transaction.
    Alembic ``batch_alter_table`` rebuilds via ``DROP TABLE``, which fails when
    child rows reference the rebuilt parent while foreign keys remain enabled.
    """
    with op.get_context().autocommit_block():
        op.get_bind().exec_driver_sql("PRAGMA foreign_keys=OFF")


def enable_and_verify_sqlite_foreign_keys(
    *,
    failure_message: str,
) -> None:
    """Re-enable FK enforcement and fail closed if relationships are broken."""
    bind = op.get_bind()
    with op.get_context().autocommit_block():
        bind.exec_driver_sql("PRAGMA foreign_keys=ON")
    violations = bind.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
    if violations:
        raise RuntimeError(failure_message)
