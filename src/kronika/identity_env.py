"""Dual-prefix environment identity resolver for ADR-0085.

One setting name may be spelled with either accepted prefix during the ordered
Kronika identity cut sequence:

- ``KRONIKA_<SUFFIX>`` is the identity prefix recorded by ADR-0085.
- ``FRAMENEST_<SUFFIX>`` is the compatible spelling that every installed
  release, the installed ``/etc/framenest/framenest.env`` file, and the
  installed systemd units still use.

This module is the only place that reads either spelling, so readers learn the
new spelling while writers keep emitting the old one until the matching reader
is the installed release.

Precedence, exactly:

===========================  =========================  =========================
``KRONIKA_<SUFFIX>``         ``FRAMENEST_<SUFFIX>``     Result
===========================  =========================  =========================
set                          unset                      the ``KRONIKA_`` value
unset                        set                        the ``FRAMENEST_`` value
unset                        unset                      ``None``; caller default
set                          set, identical             that value
set                          set, different             fail closed
===========================  =========================  =========================

A variable set to the empty string counts as unset, so the installed
environment file and existing systemd ``Environment=`` handling keep their
current meaning. That rule exists for the ``ENV_FILE`` selector, which reads a
path rather than a field value, and for the direct reader call sites.

The table is a rule about **one mapping**, and the conflict check is
**per channel**. :func:`lookup_env` is called once per source over that source's
mapping only: the process environment is one channel and the environment file is
the other. ``KRONIKA_<SUFFIX>`` in the process environment together with
``FRAMENEST_<SUFFIX>`` in the environment file therefore raises no conflict, and
the process-environment source is ordered first, so the process-environment
value wins; the reverse arrangement resolves the same way.

There is deliberately no cross-channel conflict check. A global rule would fail
closed on the ordinary case of an environment file that supplies a value and a
process environment that overrides it, which the installed ``EnvironmentFile=``
and ``Environment=`` handling treats as normal operation rather than a
misconfiguration. This is a deliberate silent resolution, not an oversight, and
it is a divergence from a global conflict rule.

One settings *field* needs a wider rule than a direct reader, and
:func:`lookup_field_value` states it. A settings field is matched against a
variable name, not read as a whole setting, so the two behaviours a field must
keep are the case-insensitive name matching and the fail-closed coercion that
``pydantic-settings`` performed before this resolver existed. See that function
for the three layers and their order.

The fail-closed path raises :class:`IdentityEnvironmentConflictError`, which
carries the setting-name suffix only. It never carries, derives, or logs a
value, a length, a hash, or a ``repr`` of either value, and it never returns a
partially resolved pair.
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


def identity_environment_names(suffix: str) -> tuple[str, str]:
    """Return both accepted variable names for one setting-name suffix."""
    return (
        f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{suffix}",
    )


def canonical_identity_environment(values: Mapping[str, str]) -> dict[str, str]:
    """Present a mapping under canonical variable names.

    ``pydantic-settings`` lowercases the keys it reads from an environment file,
    so a file mapping cannot be handed to :func:`lookup_env` directly. This
    helper keeps only the entries that carry an accepted identity prefix, under
    the canonical upper-case spelling, and drops everything else. Values that
    carry no accepted prefix are outside the identity surface.
    """
    canonical: dict[str, str] = {}
    prefixes = (PRIMARY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX)
    for key, value in values.items():
        upper = key.upper()
        for prefix in prefixes:
            if upper.startswith(prefix):
                canonical[f"{prefix}{upper[len(prefix):]}"] = value
                break
    return canonical


def lookup_env(
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Return the resolved value of one prefixed setting name.

    ``suffix`` is the part of the variable name after the prefix, for example
    ``DATABASE_PATH``. ``environ`` exists for the readers that already accept an
    explicit environment mapping so they can resolve without mutating the
    process environment; it defaults to :data:`os.environ`.

    Returns ``None`` when neither spelling carries a non-empty value, which is
    the caller's own default. Raises
    :class:`IdentityEnvironmentConflictError` when both spellings are set to
    different values, before either value is returned.

    The check covers exactly the mapping passed in, so it is per channel. A
    caller that reads two channels calls this once per channel, and two
    spellings of one suffix split across the two channels never meet in one
    call. The module docstring states that rule and why it exists.
    """
    env = os.environ if environ is None else environ
    primary = env.get(f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}")
    compatible = env.get(f"{COMPATIBLE_ENVIRONMENT_PREFIX}{suffix}")
    if primary == "":
        primary = None
    if compatible == "":
        compatible = None
    if primary is None:
        return compatible
    if compatible is None or primary == compatible:
        return primary
    raise IdentityEnvironmentConflictError(suffix)


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
    """Return the value one settings field takes from an environment mapping.

    :func:`lookup_env` resolves a whole setting name and is right for the
    ``ENV_FILE`` selector and for the direct reader call sites. A settings field
    additionally has to match a variable *name*, so this function resolves in
    three layers, in this order:

    1. the case-exact identity spelling, when it carries a non-empty value. The
       case-exact spelling wins outright because it is the spelling the identity
       authority names.
    2. the compatible spelling from the case-folded view, raw, including an
       explicitly empty value. This is what ``pydantic-settings`` produced for
       the field before this resolver existed, so an old-spelling name
       configures the field whatever its case and an unparseable value still
       fails closed instead of silently becoming the default.
    3. the identity spelling from the case-folded view, with an empty value
       counting as unset, because that is the identity prefix's documented rule.

    ``case_folded`` is accepted so a caller that resolves many fields folds the
    mapping once. :func:`lookup_env` runs for every field, so the cross-prefix
    conflict check stays exactly as strict as before within that one mapping, and
    raises before any layer returns.

    What the layer order means, measured on one mapping:

    - A case-exact ``KRONIKA_<SUFFIX>`` carrying a non-empty value is answered
      by layer 1 and short-circuits, so it is shadowed neither by a case variant
      of the compatible spelling nor by a case variant of itself.
    - When both spellings are present only as case variants, layer 2 answers
      first, so the compatible spelling wins and a case variant of the identity
      spelling **can** be shadowed by a case variant of the compatible one:
      ``kronika_port=9998`` beside ``framenest_port=9999`` resolves to ``9999``
      and raises nothing.
    - An empty identity spelling counts as unset in layer 1 and in layer 3
      alike, so ``KRONIKA_PORT=`` beside ``framenest_port=9999`` resolves to
      ``9999``.

    The compatible layer sits ahead of the identity layer deliberately. Before
    this resolver existed the compatible prefix was the only prefix the library
    read, so a case variant of the compatible spelling is what configured a
    field then; the differential parity matrix in
    ``tests/contract/test_kronika_settings_parity.py`` measures that reading
    case by case against the pre-cut source. Keeping layer 2 ahead therefore
    reproduces the pre-cut result for an input whose identity spelling is a
    case variant, and changes nothing for an input that carries no identity
    spelling at all. A case-exact ``KRONIKA_<SUFFIX>`` is the deliberate
    divergence: layer 1 answers it, where the pre-cut library read the
    compatible spelling instead.

    The cross-prefix conflict check is unaffected by any of this. It stays
    case-exact, because it compares the two canonical names only, and it runs
    unconditionally before any layer decides, because :func:`lookup_env` is
    called first. It is per mapping like everything else here.
    """
    values = os.environ if environ is None else environ
    folded = folded_identity_environment(values) if case_folded is None else case_folded
    resolved = lookup_env(suffix, environ=values)
    if values.get(f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}"):
        return resolved
    compatible = folded.get(f"{COMPATIBLE_ENVIRONMENT_PREFIX}{suffix}")
    if compatible is not None:
        return compatible
    primary = folded.get(f"{PRIMARY_ENVIRONMENT_PREFIX}{suffix}")
    if primary:
        return primary
    return resolved


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
