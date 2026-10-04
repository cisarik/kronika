"""Contract tests for the ADR-0085 dual-prefix settings read boundary.

Every test here proves one clause of the C1 organizing rule: readers learn the
``KRONIKA_`` spelling, writers keep emitting the ``FRAMENEST_`` one. No test in
this module asserts that a writer changed.
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
from kronika.identity_env import IdentityEnvironmentConflictError, lookup_env

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INSTALLED_ENV_FILE_EXAMPLE = REPOSITORY_ROOT / "deploy" / "systemd" / "framenest.env.example"

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


def test_framenest_prefix_alone_still_configures_the_same_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9124")

    settings = load_settings(env_file=None)

    assert settings.port == 9124


def test_identical_values_in_both_prefixes_are_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "9125")
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9125")

    settings = load_settings(env_file=None)

    assert settings.port == 9125


def test_conflicting_field_values_fail_closed_without_revealing_a_value(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}PORT", "9126")
    monkeypatch.setenv(f"{COMPATIBLE}PORT", "9127")

    with caplog.at_level(logging.DEBUG), pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings(env_file=None)

    rendered = str(excinfo.value)
    assert "9126" not in rendered
    assert "9127" not in rendered
    assert "PORT" in rendered
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
            7008,
            id="compatible-spelling-in-the-process-environment",
        ),
    ],
)
def test_the_conflict_rule_is_per_channel_and_the_process_environment_wins(
    process_name: str,
    process_value: str,
    file_line: str,
    expected: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cross-channel pair is not a conflict, in either arrangement.

    The conflict check runs once per source over that source's mapping only, so
    one spelling in the process environment and the other spelling in the
    environment file never meet in one call. Reaching this assertion is the
    evidence that no conflict was raised.
    """
    env_file = tmp_path / "framenest.env"
    env_file.write_text(file_line, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(process_name, process_value)

    settings = load_settings(env_file=env_file)

    assert settings.port == expected


def test_the_two_spellings_inside_one_channel_still_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The silent cross-channel resolution must not weaken the same-channel rule."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{PRIMARY}PORT=7009\n{COMPATIBLE}PORT=7010\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings(env_file=env_file)

    assert "PORT" in str(excinfo.value)
    assert "7009" not in str(excinfo.value)
    assert "7010" not in str(excinfo.value)
    assert excinfo.value.exit_status == 2


def test_extra_ignore_is_preserved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    env_file = tmp_path / "framenest.env"
    env_file.write_text(
        f"{COMPATIBLE}PORT=7004\n{COMPATIBLE}NOT_A_SETTING=x\n",
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

    with pytest.raises(ValidationError) as excinfo:
        _SecretLengthSettings(_env_file=None)

    rendered = str(excinfo.value)
    assert "k3y" not in rendered
    assert "api key is too short" in rendered


@pytest.mark.parametrize("prefix", [PRIMARY, COMPATIBLE])
def test_secret_field_resolves_under_both_prefixes(
    prefix: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{prefix}API_KEY", "sufficiently-long-secret")

    settings = load_settings(env_file=None)

    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "sufficiently-long-secret"
    assert "sufficiently-long-secret" not in repr(settings)
    assert "sufficiently-long-secret" not in str(settings)


def test_secret_field_conflict_never_appears_in_a_validation_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}API_KEY", "primary-secret-value")
    monkeypatch.setenv(f"{COMPATIBLE}API_KEY", "compatible-secret-value")

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings(env_file=None)

    rendered = str(excinfo.value)
    assert "primary-secret-value" not in rendered
    assert "compatible-secret-value" not in rendered


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


def test_framenest_env_file_still_selects_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{COMPATIBLE}PORT=7102\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.port == 7102


def test_identical_env_file_selection_in_both_prefixes_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "framenest.env"
    env_file.write_text(f"{COMPATIBLE}PORT=7103\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}ENV_FILE", str(env_file))
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(env_file))

    settings = load_settings()

    assert settings.port == 7103


def test_conflicting_env_file_selection_fails_closed_before_the_file_opens(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = tmp_path / "opened.env"
    opened.write_text(f"{COMPATIBLE}PORT=7104\n", encoding="utf-8")
    other = tmp_path / "other.env"
    other.write_text(f"{COMPATIBLE}PORT=7105\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(f"{PRIMARY}ENV_FILE", str(opened))
    monkeypatch.setenv(f"{COMPATIBLE}ENV_FILE", str(other))

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings()

    rendered = str(excinfo.value)
    assert rendered != EXPLICIT_ENV_FILE_MESSAGE
    assert str(opened) not in rendered
    assert str(other) not in rendered


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


def test_installed_environment_file_shape_keeps_working_unchanged(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The installed ``/etc/framenest/framenest.env`` shape is still readable."""
    env_file = tmp_path / "framenest.env"
    env_file.write_text(
        INSTALLED_ENV_FILE_EXAMPLE.read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    settings = load_settings(env_file=env_file)

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000
    assert settings.database_path == Path("/var/lib/framenest/catalog.sqlite3")
    assert settings.gallery_preview_cache_path == Path("/var/cache/framenest/gallery-previews")
    assert settings.cover_storage_root == Path("/var/lib/framenest/covers")


def test_every_uncommented_key_in_the_installed_shape_is_still_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No installed key becomes unreadable, so nothing silently reverts."""
    text = INSTALLED_ENV_FILE_EXAMPLE.read_text(encoding="utf-8")
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
    assert settings.host == "127.0.0.1"


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

    assert settings.database_path == Path("/var/lib/framenest/catalog.sqlite3")


# ---------------------------------------------------------------------------
# The resolver is the only reader
# ---------------------------------------------------------------------------


def test_settings_source_resolves_each_field_through_the_resolver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from kronika.configuration import _DualPrefixEnvSettingsSource

    monkeypatch.setenv(f"{PRIMARY}PORT", "7201")
    monkeypatch.setenv(f"{COMPATIBLE}DATABASE_PATH", "/srv/catalog.sqlite3")

    loaded = _DualPrefixEnvSettingsSource(KronikaSettings)._load_env_vars()

    assert loaded == {
        "framenest_port": "7201",
        "framenest_database_path": "/srv/catalog.sqlite3",
    }


def test_resolver_conflict_type_is_the_one_the_settings_boundary_translates() -> None:
    with pytest.raises(IdentityEnvironmentConflictError) as excinfo:
        lookup_env("API_KEY", environ={f"{PRIMARY}API_KEY": "a", f"{COMPATIBLE}API_KEY": "b"})

    assert excinfo.value.suffix == "API_KEY"


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
        _DualPrefixDotEnvSettingsSource,
        _DualPrefixEnvSettingsSource,
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
        _DualPrefixEnvSettingsSource,
        _DualPrefixDotEnvSettingsSource,
        object,
    ]
    assert sources[2].env_file is None
    assert issubclass(_DualPrefixEnvSettingsSource, EnvSettingsSource)
    assert issubclass(_DualPrefixDotEnvSettingsSource, DotEnvSettingsSource)
