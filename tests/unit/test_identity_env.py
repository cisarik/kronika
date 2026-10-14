"""Unit tests for the ADR-0085 canonical identity environment resolver."""

from __future__ import annotations

import pytest

from kronika.identity_env import (
    COMPATIBLE_ENVIRONMENT_PREFIX,
    EXIT_IDENTITY_ENVIRONMENT_CONFLICT,
    PRIMARY_ENVIRONMENT_PREFIX,
    lookup_env,
    lookup_field_value,
)

SUFFIX = "DATABASE_PATH"


def test_primary_prefix_only_wins() -> None:
    resolved = lookup_env(SUFFIX, environ={f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/a"})

    assert resolved == "/a"


def test_compatible_prefix_only_is_ignored() -> None:
    resolved = lookup_env(SUFFIX, environ={f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/b"})

    assert resolved is None


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


def test_conflicting_values_use_the_canonical_value() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/b",
    }

    assert lookup_env(SUFFIX, environ=environ) == "/a"


def test_mixed_input_carries_the_suffix_only() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-b",
    }

    assert lookup_env(SUFFIX, environ=environ) == "/secret-a"


def test_mixed_input_repr_holds_no_value() -> None:
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-a",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}{SUFFIX}": "/secret-b",
    }

    assert lookup_env(SUFFIX, environ=environ) == "/secret-a"


def test_canonical_value_is_returned_for_a_mixed_pair() -> None:
    """A retired name cannot prevent the canonical value from being used."""
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}PORT": "9000",
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT": "9001",
    }

    assert lookup_env("PORT", environ=environ) == "9000"


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


def test_a_case_variant_of_the_identity_spelling_ignores_the_retired_layer() -> None:
    """Only canonical case variants take part in field resolution."""
    assert (
        lookup_field_value("PORT", environ={"kronika_port": "9998", "framenest_port": "9999"})
        == "9998"
    )
    assert (
        lookup_field_value(
            "PORT",
            environ={"kronika_port": "9998", "Framenest_Port": "9999"},
        )
        == "9998"
    )


def test_an_empty_identity_spelling_is_unset_in_both_identity_layers() -> None:
    """Layers 1 and 3 agree that an empty identity value carries no value."""
    assert (
        lookup_field_value("PORT", environ={"KRONIKA_PORT": "", "framenest_port": "9999"})
        is None
    )
    assert (
        lookup_field_value("PORT", environ={"kronika_port": "", "framenest_port": "9999"})
        is None
    )
    assert lookup_field_value("PORT", environ={"KRONIKA_PORT": ""}) is None
    assert lookup_field_value("PORT", environ={"kronika_port": ""}) is None


def test_a_mixed_pair_keeps_the_canonical_field_value() -> None:
    assert lookup_field_value(
        "PORT", environ={"KRONIKA_PORT": "9998", "FRAMENEST_PORT": "9999"}
    ) == "9998"
