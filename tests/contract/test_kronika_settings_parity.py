"""Differential parity between the current settings source and the pre-cut source.

A settings field must behave, for old-spelling-only input, exactly as
``pydantic-settings`` behaved before the dual-prefix resolver existed. This
module proves that differentially instead of by reading, for every field, over
case variants, explicitly empty values, whitespace-only values and invalid
values, through both the process environment and an environment file.

The reference side is built from the library's own
``BaseSettings.settings_customise_sources``, which is the hook the library
defines and the hook the code at the restoration reference commit used. Patching
that hook makes ``_settings_init_sources`` build the stock ``EnvSettingsSource``
and ``DotEnvSettingsSource`` and apply the library's default source order, so no
project dual-prefix class takes part in the reference.

Why today's library is a valid reference for that older commit: ``poetry.lock``
is byte-identical at ``18c357cf6f8c5ff9cc3b2c28e638510fc73a3672`` and at this
commit, the installed ``pydantic-settings`` is the locked version, and the
``KronikaSettings`` field set, validators and ``model_config`` are
byte-identical at both commits. Settings-source assembly is therefore the only
difference, and this module replaces it with the library's own.
``test_the_settings_library_is_unchanged_since_the_restoration_reference`` keeps
that claim honest.
"""

from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Iterator
from unittest import mock

import pytest
from pydantic import SecretStr, ValidationError
from pydantic_settings import BaseSettings

from kronika.configuration import (
    FrameNestConfigurationError,
    KronikaSettings,
    load_settings,
)
from kronika.identity_env import (
    COMPATIBLE_ENVIRONMENT_PREFIX,
    PRIMARY_ENVIRONMENT_PREFIX,
    lookup_env,
    lookup_field_value,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

#: Published ``main`` at the moment the restoration was scoped; the behaviour the
#: dual-prefix cut must not change for old-spelling-only input.
RESTORATION_REFERENCE = "18c357cf6f8c5ff9cc3b2c28e638510fc73a3672"

#: The library's own hook, taken from the installed library class.
STOCK_SOURCE_HOOK = BaseSettings.__dict__["settings_customise_sources"]

#: One value per shape a field can be handed, so every field is exercised with a
#: value it can accept and with values it must reject.
MATRIX_VALUES = (
    "",
    " ",
    " 8123 ",
    "9999",
    "127.0.0.1",
    "/srv/framenest/parity-value",
    "true",
    '{"parity": "value"}',
    '["parity"]',
    "not-a-valid-value",
)

#: Canonical spelling plus the two case shapes a variable name can differ by.
MATRIX_SPELLINGS = ("upper", "lower", "mixed")

PROCESS_CHANNEL = "process-environment"
FILE_CHANNEL = "environment-file"


def _spelled(field_name: str, spelling: str) -> str:
    canonical = f"{COMPATIBLE_ENVIRONMENT_PREFIX}{field_name.upper()}"
    if spelling == "upper":
        return canonical
    if spelling == "lower":
        return canonical.lower()
    return "".join(
        character.upper() if index % 2 else character.lower()
        for index, character in enumerate(canonical)
    )


def _comparable(settings: KronikaSettings) -> dict[str, Any]:
    dumped: dict[str, Any] = dict(settings.model_dump())
    api_key = dumped.get("api_key")
    if isinstance(api_key, SecretStr):
        # The dump masks a secret, so an unmasked synthetic value is compared.
        dumped["api_key"] = api_key.get_secret_value()
    return dumped


def _error_locations(error: Exception) -> tuple[str, ...]:
    if not isinstance(error, ValidationError):
        return ()
    return tuple(sorted(str(part) for entry in error.errors() for part in entry["loc"]))


def _observed(process_env: dict[str, str], env_file: Path | None, *, stock: bool) -> Any:
    """Return the observable outcome of one settings build.

    An outcome is the built settings, or the failure type together with the
    field locations the failure names. Failure messages are not compared: the
    library embeds the source class name in ``SettingsError`` text, and that name
    is exactly what this correction is allowed to change.
    """
    with ExitStack() as stack:
        if stock:
            stack.enter_context(
                mock.patch.object(
                    KronikaSettings,
                    "settings_customise_sources",
                    STOCK_SOURCE_HOOK,
                )
            )
        stack.enter_context(mock.patch.dict(os.environ, process_env, clear=True))
        try:
            return ("ok", _comparable(KronikaSettings(_env_file=env_file)))
        except Exception as error:  # noqa: BLE001 - a parity probe reports every outcome
            return ("error", type(error).__name__, _error_locations(error))


def _matrix() -> Iterator[tuple[str, str, str]]:
    for field_name in KronikaSettings.model_fields:
        for spelling in MATRIX_SPELLINGS:
            for value in MATRIX_VALUES:
                yield field_name, spelling, value


def _parity_mismatches(tmp_path: Path, channel: str) -> list[str]:
    mismatches: list[str] = []
    for field_name, spelling, value in _matrix():
        variable = _spelled(field_name, spelling)
        label = f"{channel} {variable}={value!r}"
        if channel == PROCESS_CHANNEL:
            stock = _observed({variable: value}, None, stock=True)
            current = _observed({variable: value}, None, stock=False)
        else:
            env_file = tmp_path / "parity.env"
            env_file.write_text(f"{variable}={value}\n", encoding="utf-8")
            stock = _observed({}, env_file, stock=True)
            current = _observed({}, env_file, stock=False)
        if stock != current:
            mismatches.append(f"{label}: stock={stock!r} current={current!r}")
    return mismatches


def _git_blob(revision: str, path: str) -> bytes:
    completed = subprocess.run(
        ("git", "show", f"{revision}:{path}"),
        check=True,
        capture_output=True,
        cwd=REPOSITORY_ROOT,
    )
    return completed.stdout


@pytest.fixture(autouse=True)
def _isolate_accepted_identity_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for field_name in KronikaSettings.model_fields:
        for prefix in (PRIMARY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX):
            monkeypatch.delenv(f"{prefix}{field_name.upper()}", raising=False)


# ---------------------------------------------------------------------------
# The differential parity claim
# ---------------------------------------------------------------------------


def test_process_environment_matches_the_stock_source_for_every_field(
    tmp_path: Path,
) -> None:
    mismatches = _parity_mismatches(tmp_path, PROCESS_CHANNEL)

    assert not mismatches, "process-environment parity broken:\n" + "\n".join(mismatches)


def test_environment_file_matches_the_stock_source_for_every_field(tmp_path: Path) -> None:
    mismatches = _parity_mismatches(tmp_path, FILE_CHANNEL)

    assert not mismatches, "environment-file parity broken:\n" + "\n".join(mismatches)


def test_the_matrix_subject_is_pinned_to_the_compatible_prefix_value() -> None:
    """The matrix builds its variable names from the constant, so pin its value.

    Without this guard a later cut that changed the constant's *value* would
    change the subject of every comparison above, and the parity claim would
    become vacuous with nothing failing.
    """
    assert COMPATIBLE_ENVIRONMENT_PREFIX == "FRAMENEST_"
    assert KronikaSettings.model_config["env_prefix"] == "FRAMENEST_"


def test_the_matrix_reaches_every_field_and_every_value() -> None:
    cases = list(_matrix())

    assert len(cases) == (
        len(KronikaSettings.model_fields) * len(MATRIX_SPELLINGS) * len(MATRIX_VALUES)
    )
    assert {field for field, _, _ in cases} == set(KronikaSettings.model_fields)
    assert {spelling for _, spelling, _ in cases} == set(MATRIX_SPELLINGS)
    assert {value for _, _, value in cases} == set(MATRIX_VALUES)


def test_the_stock_reference_runs_only_library_sources() -> None:
    """The reference must not reach a project dual-prefix class."""
    from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource

    seen: list[type] = []

    def _record(settings_cls: type, *args: Any, **kwargs: Any) -> Any:
        seen.extend(type(source) for source in kwargs.values())
        return STOCK_SOURCE_HOOK.__func__(settings_cls, *args, **kwargs)

    with mock.patch.object(
        KronikaSettings,
        "settings_customise_sources",
        classmethod(_record),
    ):
        with mock.patch.dict(os.environ, {}, clear=True):
            KronikaSettings(_env_file=None)

    assert EnvSettingsSource in seen
    assert DotEnvSettingsSource in seen
    assert not any(source.__name__.startswith("_DualPrefix") for source in seen)


def test_the_settings_library_is_unchanged_since_the_restoration_reference() -> None:
    """The oracle is licensed only while the library matches the older commit."""
    import pydantic_settings

    lock = (REPOSITORY_ROOT / "poetry.lock").read_text(encoding="utf-8")
    locked = re.search(
        r'name = "pydantic-settings"\nversion = "([^"]+)"',
        lock,
    )
    assert locked is not None

    assert pydantic_settings.VERSION == locked.group(1)
    assert _git_blob(RESTORATION_REFERENCE, "poetry.lock") == _git_blob("HEAD", "poetry.lock")
    assert _git_blob(RESTORATION_REFERENCE, "pyproject.toml") == _git_blob(
        "HEAD", "pyproject.toml"
    )


# ---------------------------------------------------------------------------
# Case-insensitive name matching
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("variable", ["framenest_port", "Framenest_Port", "fRaMeNeSt_pOrT"])
def test_an_old_spelling_name_in_any_case_configures_the_field(
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(variable, "9999")

    settings = load_settings(env_file=None)

    assert settings.port == 9999


@pytest.mark.parametrize("variable", ["kronika_port", "Kronika_Port"])
def test_an_identity_spelling_name_in_any_case_configures_the_field(
    variable: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(variable, "9999")

    settings = load_settings(env_file=None)

    assert settings.port == 9999


def test_a_case_variant_of_a_name_outside_both_prefixes_is_still_ignored(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Folding must not widen the set of ambient names a field accepts."""
    monkeypatch.setenv("port", "9999")
    monkeypatch.setenv("host", "203.0.113.7")

    settings = load_settings(env_file=None)

    assert settings.port == 8000
    assert settings.host == "127.0.0.1"


def test_both_cases_of_one_old_name_resolve_to_the_later_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-cut collision rule, kept exactly: the later entry wins."""
    monkeypatch.setenv("FRAMENEST_PORT", "9998")
    monkeypatch.setenv("framenest_port", "9999")

    settings = load_settings(env_file=None)

    assert settings.port == 9999
    assert lookup_field_value("PORT", environ={"FRAMENEST_PORT": "1", "framenest_port": "2"}) == "2"


def test_the_case_exact_identity_spelling_outranks_a_case_variant_of_the_old_spelling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deliberate divergence, stated rather than hidden.

    The conflict rule stays case-exact, so this pair is not a conflict, and the
    identity spelling the authority names wins instead of being shadowed by a
    case variant of the compatible spelling.
    """
    monkeypatch.setenv(f"{PRIMARY_ENVIRONMENT_PREFIX}PORT", "9998")
    monkeypatch.setenv("framenest_port", "9999")

    settings = load_settings(env_file=None)

    assert settings.port == 9998


def test_the_two_prefixes_in_different_cases_do_not_raise_a_conflict() -> None:
    """The conflict rule stays case-exact on the two canonical names."""
    environ = {
        f"{PRIMARY_ENVIRONMENT_PREFIX}PORT": "9998",
        "framenest_port": "9999",
    }

    assert lookup_env("PORT", environ=environ) == "9998"


# ---------------------------------------------------------------------------
# An explicitly empty value
# ---------------------------------------------------------------------------


def test_an_explicitly_empty_old_spelling_value_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pre-cut outcome: a validation failure, not the field default."""
    monkeypatch.setenv(f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT", "")

    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_an_explicitly_empty_old_spelling_path_value_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{COMPATIBLE_ENVIRONMENT_PREFIX}DATABASE_PATH", "")

    with pytest.raises(ValidationError):
        load_settings(env_file=None)


def test_an_explicitly_empty_environment_file_value_fails_closed(tmp_path: Path) -> None:
    """The environment-file path never lost this; it must keep it."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT=\n", encoding="utf-8")

    with pytest.raises(ValidationError):
        load_settings(env_file=env_file)


@pytest.mark.parametrize("prefix", [PRIMARY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX])
def test_the_empty_environment_file_selector_still_behaves_as_unset(
    prefix: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "framenest.env").write_text(
        f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT=7106\n",
        encoding="utf-8",
    )
    monkeypatch.setenv(f"{prefix}ENV_FILE", "")

    settings = load_settings()

    assert settings.port == 8000


def test_an_explicitly_empty_identity_spelling_value_still_counts_as_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The identity prefix keeps the resolver's own empty-means-unset rule."""
    monkeypatch.setenv(f"{PRIMARY_ENVIRONMENT_PREFIX}PORT", "")

    settings = load_settings(env_file=None)

    assert settings.port == 8000


def test_an_empty_old_spelling_beside_an_identity_value_keeps_the_identity_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The empty compatible spelling counts as unset, so no conflict is raised."""
    monkeypatch.setenv(f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT", "")
    monkeypatch.setenv(f"{PRIMARY_ENVIRONMENT_PREFIX}PORT", "9999")

    settings = load_settings(env_file=None)

    assert settings.port == 9999


def test_the_conflicting_pair_still_fails_closed_with_a_suffix_only_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{PRIMARY_ENVIRONMENT_PREFIX}PORT", "9998")
    monkeypatch.setenv(f"{COMPATIBLE_ENVIRONMENT_PREFIX}PORT", "9999")

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings(env_file=None)

    rendered = str(excinfo.value)
    assert "PORT" in rendered
    assert "9998" not in rendered
    assert "9999" not in rendered
    assert excinfo.value.exit_status == 2