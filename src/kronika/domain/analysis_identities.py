"""Accepted spellings of durable analysis identities.

Two identities are written into the catalog: the generic media-suggestion result
schema and the movie-identification result schema, each with a prompt version
beside it. Both spellings must stay readable, because the writer cut changes the
spelling the writers emit while the rows written before it keep the earlier one.

This module owns the acceptance rule itself so that every reader applies the
same one:

- an identity is accepted exactly when it is one of the two accepted spellings,
  so acceptance is symmetric - neither spelling is preferred and neither is a
  special case;
- an accepted spelling never implies the other, so a reader cannot widen itself
  into accepting an unknown identity;
- no writer is named here. This module changes no writer identity.

The vision-probe prompt version is deliberately absent: it is a request and
response identity for an administrative capability probe and is never persisted,
so it has no historical rows to keep readable.
"""

from __future__ import annotations


def accepted_durable_identity(current: str, canonical: str) -> frozenset[str]:
    """Return the two accepted spellings of one durable identity.

    The two spellings must differ, so a cut that forgets to change a writer
    cannot silently collapse the pair into one accepted value.
    """
    if not current or not canonical:
        raise ValueError("a durable identity spelling must not be empty")
    if current == canonical:
        raise ValueError("a durable identity needs two distinct spellings")
    return frozenset({current, canonical})


def is_accepted_durable_identity(value: object, accepted: frozenset[str]) -> bool:
    """Return whether one stored or supplied identity is an accepted spelling.

    This is the single symmetric membership test. It is deliberately not an
    equality comparison against a current spelling, because that is exactly the
    filter that hides historical rows once the writer changes.
    """
    return isinstance(value, str) and value in accepted