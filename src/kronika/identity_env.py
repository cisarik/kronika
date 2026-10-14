"""Canonical environment resolution for ADR-0085.

FRAMENEST_ inputs no longer configure the application. Direct readers require
case-exact names and treat empty values as unset. Settings fields additionally
accept case variants, with a non-empty case-exact canonical name taking priority.
"""

from __future__ import annotations

from collections.abc import Iterable, MutableMapping
import os
from typing import Mapping

PRIMARY_ENVIRONMENT_PREFIX = "KRONIKA_"
COMPATIBLE_ENVIRONMENT_PREFIX = "FRAMENEST_"

#: Process exit status a command line entry point uses for a fail-closed
#: identity-environment conflict.
EXIT_IDENTITY_ENVIRONMENT_CONFLICT = 2


class IdentityEnvironmentConflictFailure:
    """Marker mixin for a command's sanitized failure caused by a conflict.

    An in-package entry point translates :class:`IdentityEnvironmentConflictError`
    into its own sanitized error type and mixes this class in. The fail-closed
    process exit status then has exactly one in-package source instead of a
    literal repeated in every command, so two handlers cannot drift apart. The
    two standard-library-only deploy engines keep their own local constant,
    because a standard-library-only mirror cannot import this module.
    """

    @property
    def exit_status(self) -> int:
        """Return the fail-closed exit status for an identity-environment conflict."""
        return EXIT_IDENTITY_ENVIRONMENT_CONFLICT


class IdentityEnvironmentConflictError(Exception):
    """Both accepted prefixes set one setting name to different values.

    The exception carries the setting-name suffix only. Callers translate it
    into their own sanitized error type; the message text is already safe to
    print because it names the two variables and nothing about their values.
    """

    def __init__(self, suffix: str) -> None:
        self.suffix = suffix
        super().__init__(
            f"Conflicting environment variables {PRIMARY_ENVIRONMENT_PREFIX}{suffix} "
            f"and {COMPATIBLE_ENVIRONMENT_PREFIX}{suffix} are set to different values."
        )


def canonical_identity_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Keep canonical-prefixed file keys, normalized to upper case."""
    return {
        key.upper(): value
        for key, value in values.items()
        if key.upper().startswith(PRIMARY_ENVIRONMENT_PREFIX)
    }


def lookup_env(
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Read a case-exact canonical name; absent and empty values are unset."""
    env = os.environ if environ is None else environ
    return env.get(f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}") or None


def folded_identity_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Return one mapping whose names are upper-cased, later entries winning.

    ``pydantic-settings`` folds the mapping it reads from the process
    environment before it matches names against fields, so before this resolver
    existed a variable configured a field whatever its case. This reproduces
    that one rule, over the same mapping and with the same collision outcome: a
    later entry replaces an earlier one whose name differs only in case, exactly
    as the library's own comprehension did.

    Only names carrying an accepted identity prefix are ever consulted for a
    field, so folding does not widen the set of ambient names a field accepts.
    """
    return {name.upper(): value for name, value in values.items()}


def lookup_field_value(
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
    case_folded: Mapping[str, str] | None = None,
) -> str | None:
    """Read a canonical field name, preserving case and empty-value precedence.

    The non-empty case-exact name wins. Otherwise the case-folded mapping is
    consulted, with later case variants winning and an empty value unset.
    Unlike this field reader, lookup_env does not fold names.
    """
    values = os.environ if environ is None else environ
    resolved = lookup_env(suffix, environ=values)
    if resolved is not None:
        return resolved
    folded = folded_identity_environment(values) if case_folded is None else case_folded
    return folded.get(f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}") or None


def drop_identity_environment_spellings(
    environ: MutableMapping[str, str],
    suffixes: Iterable[str],
) -> None:
    """Remove every accepted and case-variant spelling of the given suffixes.

    A caller that writes one resolved value per suffix into a child environment
    calls this first, so the child cannot receive a conflicting pair no matter
    what spelling the parent environment carried. The mapping is mutated in
    place; the caller then writes exactly one name per suffix.
    """
    folded_names = {
        f"{prefix}{suffix}".upper()
        for suffix in suffixes
        for prefix in (PRIMARY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX)
    }
    for name in [name for name in environ if name.upper() in folded_names]:
        del environ[name]
