"""Unit tests for the ADR-0085 dual-prefix identity environment resolver."""

from __future__ import annotations

import pytest

from kronika.identity_env import (
    COMPATIBLE_ENVIRONMENT_PREFIX,
    EXIT_IDENTITY_ENVIRONMENT_CONFLICT,
    PRIMARY_ENVIRONMENT_PREFIX,
    IdentityEnvironmentConflictError,
    lookup_env,
    lookup_field_value,
)

SUFFIX = "DATABASE_PATH"


def test_primary_prefix_only_wins() -> None:
    resolved = lookup_env(SUFFIX, environ={f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/a"})

    assert resolved == "/a"


def test_compatible_prefix_only_still_resolves() -> None:
    resolved = lookup_env(SUFFIX, environ={f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/b"})

    assert resolved == "/b"


def test_both_unset_returns_the_caller_default() -> None:
    assert lookup_env(SUFFIX, environ={}) is None


def test_identical_values_in_both_prefixes_are_accepted() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/same",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/same",
    }

    assert lookup_env(SUFFIX, environ=environ) == "/same"


def test_empty_value_counts_as_unset_for_the_primary_prefix() -> None:
    environ = {f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": ""}

    assert lookup_env(SUFFIX, environ=environ) is None


def test_empty_value_counts_as_unset_for_the_compatible_prefix() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "",
    }

    assert lookup_env(SUFFIX, environ=environ) == "/a"


def test_both_empty_counts_as_unset() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "",
    }

    assert lookup_env(SUFFIX, environ=environ) is None


def test_conflicting_values_fail_closed() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/b",
    }

    with pytest.raises(IdentityEnvironmentConflictError):
        lookup_env(SUFFIX, environ=environ)


def test_conflict_carries_the_suffix_only() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-b",
    }

    with pytest.raises(IdentityEnvironmentConflictError) as excinfo:
        lookup_env(SUFFIX, environ=environ)

    error = excinfo.value
    assert error.suffix == SUFFIX
    assert SUFFIX in str(error)
    for forbidden in ("/secret-a", "/secret-b", "secret", "8", "9"):
        assert forbidden not in str(error)


def test_conflict_repr_holds_no_value() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-b",
    }

    with pytest.raises(IdentityEnvironmentConflictError) as excinfo:
        lookup_env(SUFFIX, environ=environ)

    rendered = f"{excinfo.value!r}"
    assert "/secret-a" not in rendered
    assert "/secret-b" not in rendered


def test_conflict_is_raised_before_any_value_is_returned() -> None:
    """The fail-closed path is total: no field is resolved partially."""
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}PORT": "9000",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT": "9001",
    }

    with pytest.raises(IdentityEnvironmentConflictError):
        lookup_env("PORT", environ=environ)


def test_process_environment_is_the_default_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}", "/process")

    assert lookup_env(SUFFIX) == "/process"


def test_cli_conflict_exit_status_is_two() -> None:
    assert EXIT_IDENTITY_ENVIRONMENT_CONFLICT == 2


def test_accepted_prefixes_are_exactly_the_recorded_identity_pair() -> None:
    assert (PRIMARY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX) == (
        "KRONIKA_",
        "FRAMENEST_",
    )


# ---------------------------------------------------------------------------
# The field layer order, in both halves
# ---------------------------------------------------------------------------


def test_a_case_exact_identity_spelling_short_circuits_every_other_layer() -> None:
    """Layer 1 answers, so no case variant displaces the canonical name."""
    assert (
        lookup_field_value("PORT", environ={"KRONIKA_PORT": "9998", "framenest_port": "9999"})
        == "9998"
    )
    assert (
        lookup_field_value("PORT", environ={"KRONIKA_PORT": "9998", "kronika_port": "9999"})
        == "9998"
    )


def test_a_case_variant_of_the_identity_spelling_loses_to_the_compatible_layer() -> None:
    """Layer 2 precedes layer 3, so the case variant of the identity name loses."""
    assert (
        lookup_field_value("PORT", environ={"kronika_port": "9998", "framenest_port": "9999"})
        == "9999"
    )
    assert (
        lookup_field_value(
            "PORT",
            environ={"kronika_port": "9998", "Framenest_Port": "9999"},
        )
        == "9999"
    )


def test_an_empty_identity_spelling_is_unset_in_both_identity_layers() -> None:
    """Layers 1 and 3 agree that an empty identity value carries no value."""
    assert (
        lookup_field_value("PORT", environ={"KRONIKA_PORT": "", "framenest_port": "9999"})
        == "9999"
    )
    assert (
        lookup_field_value("PORT", environ={"kronika_port": "", "framenest_port": "9999"})
        == "9999"
    )
    assert lookup_field_value("PORT", environ={"KRONIKA_PORT": ""}) is None
    assert lookup_field_value("PORT", environ={"kronika_port": ""}) is None


def test_the_cross_prefix_conflict_check_precedes_every_field_layer() -> None:
    """The check runs first, so it also outranks the case-exact short-circuit."""
    with pytest.raises(IdentityEnvironmentConflictError):
        lookup_field_value(
            "PORT", environ={"KRONIKA_PORT": "9998", "FRAMENEST_PORT": "9999"}
        )
