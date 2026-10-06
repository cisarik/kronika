"""Accepted spellings of durable analysis identities.

Two identities are written into the catalog: the generic media-suggestion result
schema and the movie-identification result schema, each with a prompt version
beside it. Both spellings must stay readable, because the writer cut changed the
spelling the writers emit while the rows written before it keep the earlier one.

This module owns the acceptance rule itself so that every reader applies the
same one:

- a writer's current spelling and every retained historical spelling are all
  accepted, so acceptance is symmetric - the current spelling is not a special
  case and no historical row is hidden;
- an accepted spelling never implies another that is not in the set, so a reader
  cannot widen itself into accepting an unknown identity;
- the current spelling must differ from every historical spelling, so a cut that
  changes a writer without restructuring the pair cannot silently collapse the
  accepted set into one value.

The vision-probe prompt version is deliberately absent: it is a request and
response identity for an administrative capability probe and is never persisted,
so it has no historical rows to keep readable.
"""

from __future__ import annotations


def accepted_durable_identity(
    current: str, historical: str, *retained_historical: str
) -> frozenset[str]:
    """Return the accepted spellings of one durable identity.

    ``current`` is the spelling the writers emit now; every later argument is a
    historical spelling that was written before the durable-writer cut and must
    stay readable. The current spelling must differ from every historical
    spelling, so a cut that forgets to restructure the pair cannot silently
    collapse the accepted set into one value.
    """
    spellings = (current, historical, *retained_historical)
    if any(not spelling for spelling in spellings):
        raise ValueError("a durable identity spelling must not be empty")
    if len(set(spellings)) != len(spellings):
        raise ValueError(
            "a durable identity needs a current spelling and distinct historical spellings"
        )
    return frozenset(spellings)


def is_accepted_durable_identity(value: object, accepted: frozenset[str]) -> bool:
    """Return whether one stored or supplied identity is an accepted spelling.

    This is the single symmetric membership test. It is deliberately not an
    equality comparison against a current spelling, because that is exactly the
    filter that hides historical rows once the writer changes.
    """
    return isinstance(value, str) and value in accepted
