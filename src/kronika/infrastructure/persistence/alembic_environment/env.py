"""Alembic environment loaded only by explicit Kronika migration commands."""

from __future__ import annotations

from alembic import context

from kronika.infrastructure.persistence.alembic_compat import (
    install_alembic_package_alias,
)

# Alembic loads this module by file path, outside the package, so the ADR-0085
# compatibility alias has to be installed here as well as in the migration
# loader. Eleven applied revisions import the shared batch helper through the
# retired package spelling and their bytes are frozen; this alias is the only
# bridge they will ever have. See `alembic_compat`.
install_alembic_package_alias()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is None:
        raise RuntimeError("Kronika migration connection is unavailable.")
    context.configure(
        connection=connection,
        target_metadata=None,
        transactional_ddl=True,
    )
    with context.begin_transaction():
        context.run_migrations()


run_migrations_online()
