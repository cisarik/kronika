"""Canonical settings boundary after retirement of FRAMENEST_ inputs.

Old inputs are ignored in both channels, including file selection; mixed inputs
use the canonical value. Source order, coercion and secret containment persist.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
import sys
from typing import Any

import pytest
from pydantic import Field, SecretStr, ValidationError, field_validator

from kronika.configuration import (
    EXPLICIT_ENV_FILE_MESSAGE,
    FrameNestConfigurationError,
    KronikaSettings,
    load_settings,
)
from kronika.identity_env import lookup_env

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLED_ENV_FILE_EXAMPLE = REPOSITORY_ROOT / "deploy" / "systemd" / "kronika.env.example"

PRIMARY = "KRONIKA_"
COMPATIBLE = "FRAMENEST_"

READ_ENVIRONMENT_VARIABLES = (
    f"{PRIMARY}HOST",
    f"{PRIMARY}PORT",
    f"{PRIMARY}API_KEY",
    f"{PRIMARY}DATABASE_PATH",
    f"{PRIMARY}ENV_FILE",
    f"{PRIMARY}INGRESS_MODE",
    f"{PRIMARY}AI_CONFIG_PATH",
    f"{PRIMARY}CATALOG_BACKUP_ROOT",
    f"{PRIMARY}DEVELOPMENT_RUNTIME_DIR",
    f"{PRIMARY}DEVELOPMENT_LOG_DIR",
    f"{COMPATIBLE}HOST",
    f"{COMPATIBLE}PORT",
    f"{COMPATIBLE}API_KEY",
    f"{COMPATIBLE}DATABASE_PATH",
    f"{COMPATIBLE}ENV_FILE",
    f"{COMPATIBLE}INGRESS_MODE",
    f"{COMPATIBLE}AI_CONFIG_PATH",
    f"{COMPATIBLE}CATALOG_BACKUP_ROOT",
    f"{COMPATIBLE}DEVELOPMENT_RUNTIME_DIR",
    f"{COMPATIBLE}DEVELOPMENT_LOG_DIR",
)


@pytest.fixture(autouse=True)
def isolate_identity_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for variable in READ_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


def _load_deploy_helper(name: str) -> Any:
    path = REPOSITORY_ROOT / "deploy" / "ubuntu" / name
    spec = importlib.util.spec_from_file_location(f"_dual_read_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Settings fields under each accepted prefix
# ---------------------------------------------------------------------------


def test_kronika_prefix_alone_configures_a_settings_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "9123")

    settings = load_settings(env_file=None)

    assert settings.port == 9123


def test_framenest_prefix_alone_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9124")

    settings = load_settings(env_file=None)

    assert settings.port == 8000


def test_identical_values_in_both_prefixes_are_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "9125")
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9125")

    settings = load_settings(env_file=None)

    assert settings.port == 9125


def test_conflicting_field_values_use_canonical_value_without_logging(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "9126")
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9127")

    with caplog.at_level(logging.DEBUG):
        assert load_settings(env_file=None).port == 9126
    assert caplog.records == []


def test_empty_value_behaves_as_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "")

    settings = load_settings(env_file=None)

    assert settings.port == 8000


def test_unknown_extra_primary_prefix_variable_does_not_raise(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}TOTALLY_UNKNOWN_SETTING_NAME", "1")

    settings = load_settings(env_file=None)

    assert settings.port == 8000
    assert not hasattr(settings, "totally_unknown_setting_name")


def test_process_environment_still_overrides_environment_file_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{COMPATIBLE}PORT=7001\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "7002")

    settings = load_settings(env_file=env_file)

    assert settings.port == 7002


def test_environment_file_still_applies_without_a_process_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{PRIMARY}PORT=7003\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env_file=env_file)

    assert settings.port == 7003


@pytest.mark.parametrize(
    ("process_name", "process_value", "file_line", "expected"),
    [
        pytest.param(
            f"{PRIMARY}PORT",
            "7006",
            f"{COMPATIBLE}PORT=7005\n",
            7006,
            id="identity-spelling-in-the-process-environment",
        ),
        pytest.param(
            f"{COMPATIBLE}PORT",
            "7008",
            f"{PRIMARY}PORT=7007\n",
            7007,
            id="compatible-spelling-in-the-process-environment",
        ),
    ],
)
def test_canonical_input_wins_across_channels(
    process_name: str,
    process_value: str,
    file_line: str,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A retired input in either channel cannot displace a canonical value."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(file_line, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(process_name, process_value)

    settings = load_settings(env_file=env_file)

    assert settings.port == expected


def test_the_two_spellings_inside_one_channel_use_canonical_input(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A file containing both names uses only the canonical name."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{PRIMARY}PORT=7009\n{COMPATIBLE}PORT=7010\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert load_settings(env_file=env_file).port == 7009


def test_extra_ignore_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "framenest.env"
    env_file.write_text(
        f"{PRIMARY}PORT=7004\n{PRIMARY}NOT_A_SETTING=x\n",
        encoding="utf-8",
    )

    settings = load_settings(env_file=env_file)

    assert settings.port == 7004
    assert KronikaSettings.model_config["extra"] == "ignore"


def test_env_file_encoding_is_preserved() -> None:
    assert KronikaSettings.model_config["env_file_encoding"] == "utf-8"


def test_hide_input_in_errors_is_preserved() -> None:
    assert KronikaSettings.model_config["hide_input_in_errors"] is True


def test_bare_unprefixed_process_variables_are_not_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A `KRONIKA_` prefix must not widen the set of ambient names accepted."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PORT", "5999")
    monkeypatch.setenv("HOST", "203.0.113.7")

    settings = load_settings(env_file=None)

    assert settings.port == 8000
    assert settings.host == "127.0.0.1"


# ---------------------------------------------------------------------------
# Secret containment
# ---------------------------------------------------------------------------


class _SecretLengthSettings(KronikaSettings):
    """Production settings plus a constraint the ``SecretStr`` field can fail.

    ``api_key`` itself cannot fail validation, so the containment control needs
    a SecretStr field that can. Subclassing inherits the production
    ``model_config``, including ``hide_input_in_errors``.
    """

    @field_validator("api_key")
    @classmethod
    def _reject_short_api_key(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and len(value.get_secret_value()) < 12:
            raise ValueError("api key is too short")
        return value


@pytest.mark.parametrize("prefix", [PRIMARY, COMPATIBLE])
def test_secret_field_value_never_appears_in_a_validation_error(
    prefix: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(f"{prefix}API_KEY", "k3y")

    if prefix == COMPATIBLE:
        assert _SecretLengthSettings(_env_file=None).api_key is None
        return
    with pytest.raises(ValidationError) as excinfo:
        _SecretLengthSettings(_env_file=None)

    rendered = str(excinfo.value)
    assert "k3y" not in rendered
    assert "api key is too short" in rendered


@pytest.mark.parametrize("prefix", [PRIMARY, COMPATIBLE])
def test_secret_field_resolves_only_under_the_canonical_prefix(
    prefix: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{prefix}API_KEY", "sufficiently-long-secret")

    settings = load_settings(env_file=None)

    if prefix == COMPATIBLE:
        assert settings.api_key is None
        return
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "sufficiently-long-secret"
    assert "sufficiently-long-secret" not in repr(settings)
    assert "sufficiently-long-secret" not in str(settings)


def test_secret_field_mixed_input_is_canonical_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}API_KEY", "primary-secret-value")
    monkeypatch.setenv(f"{COMPATIBLE}API_KEY", "compatible-secret-value")

    settings = load_settings(env_file=None)
    assert settings.api_key.get_secret_value() == "primary-secret-value"
    assert "primary-secret-value" not in repr(settings)
    assert "compatible-secret-value" not in repr(settings)

# ---------------------------------------------------------------------------
# Environment-file selection
# ---------------------------------------------------------------------------


def test_kronika_env_file_selects_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "kronika.env"
    env_file.write_text(f"{PRIMARY}PORT=7101\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.port == 7101


def test_framenest_env_file_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{PRIMARY}PORT=7102\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.port == 8000


def test_identical_env_file_selection_in_both_prefixes_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{PRIMARY}PORT=7103\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}ENV_FILE", str(env_file))
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.port == 7103


def test_conflicting_env_file_selection_uses_only_the_canonical_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = tmp_path / "opened.env"
    opened.write_text(f"{PRIMARY}PORT=7104\n", encoding="utf-8")
    other = tmp_path / "other.env"
    other.write_text(f"{PRIMARY}PORT=7105\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}ENV_FILE", str(opened))
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(other))

    assert load_settings().port == 7104


def test_empty_env_file_selection_behaves_as_unset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "framenest.env").write_text(
        f"{COMPATIBLE}PORT=7106\n", encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", "")

    settings = load_settings()

    assert settings.port == 8000


# ---------------------------------------------------------------------------
# The installed environment-file shape
# ---------------------------------------------------------------------------


def test_retired_environment_file_shape_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The retired environment file does not configure any field."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(
        INSTALLED_ENV_FILE_EXAMPLE.read_text(encoding="utf-8").replace(PRIMARY, COMPATIBLE),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env_file=env_file)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.model_dump() == KronikaSettings(_env_file=None).model_dump()


def test_every_uncommented_key_in_the_installed_shape_is_retired(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every uncommented legacy key is explicitly outside the canonical prefix."""
    text = INSTALLED_ENV_FILE_EXAMPLE.read_text(encoding="utf-8").replace(PRIMARY, COMPATIBLE)
    keys = [
        line.split("=", 1)[0]
        for line in text.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    env_file = tmp_path / "framenest.env"
    env_file.write_text(text, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env_file=env_file)

    assert keys
    assert all(key.startswith(COMPATIBLE) for key in keys)
    assert settings.model_dump() == KronikaSettings(_env_file=None).model_dump()


def test_the_same_shape_with_the_new_prefix_also_works(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    text = INSTALLED_ENV_FILE_EXAMPLE.read_text(encoding="utf-8")
    renamed = "\n".join(line.replace(COMPATIBLE, PRIMARY) for line in text.splitlines())
    env_file = tmp_path / "kronika.env"
    env_file.write_text(f"{renamed}\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env_file=env_file)

    assert settings.database_path == Path("/var/lib/kronika/catalog.sqlite3")


# ---------------------------------------------------------------------------
# The resolver is the only reader
# ---------------------------------------------------------------------------


def test_settings_source_resolves_each_field_through_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.configuration import _CanonicalEnvSettingsSource

    monkeypatch.setenv(f"{PRIMARY}PORT", "7201")
    monkeypatch.setenv(f"{COMPATIBLE}DATABASE_PATH", "/srv/catalog.sqlite3")

    loaded = _CanonicalEnvSettingsSource(KronikaSettings)._load_env_vars()

    assert loaded == {
        "kronika_port": "7201",
    }


def test_resolver_ignores_the_retired_half_of_a_mixed_pair() -> None:
    assert lookup_env("API_KEY", environ={f"{PRIMARY}API_KEY": "a", f"{COMPATIBLE}API_KEY": "b"}) == "a"

# ---------------------------------------------------------------------------
# The pinned settings library API this cut is written against
# ---------------------------------------------------------------------------


def test_dual_prefix_sources_hook_the_installed_pydantic_settings_api() -> None:
    """The override seams this cut relies on exist in the installed version."""
    import pydantic_settings
    from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource

    assert pydantic_settings.VERSION.startswith("2.14.")
    assert EnvSettingsSource._load_env_vars is not DotEnvSettingsSource._load_env_vars
    assert hasattr(DotEnvSettingsSource, "_read_env_files")
    assert hasattr(EnvSettingsSource, "_extract_field_info")
    assert hasattr(EnvSettingsSource, "_apply_case_sensitive")


def test_configured_source_order_keeps_process_environment_over_env_file() -> None:
    from pydantic_settings.sources import DotEnvSettingsSource, EnvSettingsSource

    from kronika.configuration import (
        _CanonicalDotEnvSettingsSource,
        _CanonicalEnvSettingsSource,
    )

    placeholder = object()
    dotenv_placeholder = DotEnvSettingsSource(KronikaSettings, env_file=None)
    sources = KronikaSettings.settings_customise_sources(
        KronikaSettings,
        placeholder,
        placeholder,
        dotenv_placeholder,
        placeholder,
    )

    assert [type(source) for source in sources] == [
        object,
        _CanonicalEnvSettingsSource,
        _CanonicalDotEnvSettingsSource,
        object,
    ]
    assert sources[2].env_file is None
    assert issubclass(_CanonicalEnvSettingsSource, EnvSettingsSource)
    assert issubclass(_CanonicalDotEnvSettingsSource, DotEnvSettingsSource)
