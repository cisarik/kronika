"""Process-local Alembic compatibility alias for the retired package spelling.

ADR-0085 moves the application package from ``framenest`` to ``kronika`` and
leaves every applied Alembic revision byte-identical. Eleven numbered revisions
import the shared batch helper through the retired spelling::

    from framenest.infrastructure.persistence.sqlite_batch_fk import ...

Those bytes are frozen by the retention ledger, so this module - not a revision
edit - is the only sanctioned mechanism that lets them load.

The alias is deliberately the narrowest possible bridge. It exposes exactly four
module names and nothing else:

- ``framenest`` - empty package parent
- ``framenest.infrastructure`` - empty package parent
- ``framenest.infrastructure.persistence`` - empty package parent
- ``framenest.infrastructure.persistence.sqlite_batch_fk`` - the canonical
  ``kronika.infrastructure.persistence.sqlite_batch_fk`` module object

There is no filesystem package alias, no import hook, no ``sys.path``
forwarding, and no general ``framenest.*`` to ``kronika.*`` delegation. Any
other ``framenest`` name stays unimportable, which is what makes a future
revision that reaches for an unrelated retired module fail loudly instead of
silently resolving.

Installation is idempotent and fails closed: a name that is already bound to a
different module object, or to a module whose ``__name__`` is not the expected
one, raises instead of being overwritten. That keeps a stale import, a partially
installed alias, or a genuine future ``framenest`` package from being masked.
"""

from __future__ import annotations

import sys
from types import ModuleType

CANONICAL_HELPER_MODULE = "kronika.infrastructure.persistence.sqlite_batch_fk"

#: The only retired names this alias owns, in binding order.
ALIASED_PARENT_NAMES = (
    "framenest",
    "framenest.infrastructure",
    "framenest.infrastructure.persistence",
)
ALIASED_HELPER_NAME = f"{ALIASED_PARENT_NAMES[-1]}.sqlite_batch_fk"
ALIASED_NAMES = (*ALIASED_PARENT_NAMES, ALIASED_HELPER_NAME)


class AlembicCompatibilityConflictError(RuntimeError):
    """A retired module name is already bound to something this alias must not replace."""


def _empty_package(name: str) -> ModuleType:
    """Return an empty package module carrying only its own name."""
    package = ModuleType(name)
    package.__doc__ = (
        "Empty package parent installed by the ADR-0085 Alembic compatibility alias."
    )
    package.__package__ = name
    return package


def _bind_parent(name: str) -> None:
    """Bind one empty package parent, idempotently, or fail closed."""
    existing = sys.modules.get(name)
    if existing is None:
        sys.modules[name] = _empty_package(name)
        return
    if getattr(existing, "__name__", None) == name:
        return
    raise AlembicCompatibilityConflictError(
        "the retired module name "
        f"{name!r} is already bound to {getattr(existing, '__name__', existing)!r}; "
        "the ADR-0085 Alembic compatibility alias refuses to replace it"
    )


def install_alembic_package_alias() -> None:
    """Install the four retired module names, idempotently, or fail closed.

    Safe to call before every script-directory load and at the start of the
    Alembic environment. A second call with nothing else touching ``sys.modules``
    is a no-op.
    """
    for name in ALIASED_PARENT_NAMES:
        _bind_parent(name)

    from kronika.infrastructure.persistence import sqlite_batch_fk

    helper = sys.modules.get(ALIASED_HELPER_NAME)
    if helper is None:
        sys.modules[ALIASED_HELPER_NAME] = sqlite_batch_fk
        return
    if helper is sqlite_batch_fk:
        return
    raise AlembicCompatibilityConflictError(
        "the retired module name "
        f"{ALIASED_HELPER_NAME!r} is already bound to "
        f"{getattr(helper, '__name__', helper)!r}; "
        "the ADR-0085 Alembic compatibility alias refuses to replace it"
    )
