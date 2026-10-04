"""Tests for explicit-only environment-file configuration authority."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from pydantic import ValidationError

from kronika.configuration import (
    ENV_FILE_ENVIRONMENT_VARIABLE,
    EXPLICIT_ENV_FILE_MESSAGE,
    FrameNestConfigurationError,
    load_settings,
)

FRAMENEST_ENV_VARS = (
    "FRAMENEST_HOST",
    "FRAMENEST_PORT",
    "FRAMENEST_DATABASE_PATH",
    ENV_FILE_ENVIRONMENT_VARIABLE,
)


@pytest.fixture(autouse=True)
def isolate_framenest_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove FrameNest configuration variables before and after each test."""
    for variable in FRAMENEST_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)


def _write_env_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def test_omitted_env_file_ignores_random_caller_cwd_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_env_file(tmp_path / ".env", "FRAMENEST_PORT=9999\n")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.port == 8000


def test_omitted_env_file_ignores_garbage_caller_cwd_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Positive control: this file would fail validation if it were consumed.
    _write_env_file(tmp_path / ".env", "FRAMENEST_PORT=not-a-number\n")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.port == 8000


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_omitted_env_file_ignores_unreadable_caller_cwd_dotenv(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(tmp_path / ".env", "FRAMENEST_PORT=9999\n")
    env_file.chmod(0o000)
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.port == 8000


def test_omitted_env_file_without_variable_uses_safe_defaults(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.host == "127.0.0.1"
    assert settings.port == 8000


def test_explicit_variable_env_file_is_loaded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(tmp_path / "operator.env", "FRAMENEST_PORT=8123\n")
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))

    settings = load_settings()

    assert settings.port == 8123


def test_explicit_variable_missing_file_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing = tmp_path / "missing.env"
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(missing))

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings()

    assert str(excinfo.value) == EXPLICIT_ENV_FILE_MESSAGE
    assert str(missing) not in str(excinfo.value)


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_explicit_variable_unreadable_file_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(tmp_path / "operator.env", "FRAMENEST_PORT=8123\n")
    env_file.chmod(0o000)
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings()

    assert str(excinfo.value) == EXPLICIT_ENV_FILE_MESSAGE
    assert str(env_file) not in str(excinfo.value)
    assert "8123" not in str(excinfo.value)


def test_explicit_variable_directory_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(tmp_path))

    with pytest.raises(FrameNestConfigurationError):
        load_settings()


def test_explicit_variable_empty_value_is_ignored(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, "  ")
    monkeypatch.chdir(tmp_path)

    settings = load_settings()

    assert settings.port == 8000


def test_explicit_parameter_missing_file_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.env"

    with pytest.raises(FrameNestConfigurationError) as excinfo:
        load_settings(env_file=missing)

    assert str(excinfo.value) == EXPLICIT_ENV_FILE_MESSAGE
    assert str(missing) not in str(excinfo.value)


def test_explicit_none_disables_even_configured_variable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(tmp_path / "operator.env", "FRAMENEST_PORT=8123\n")
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))

    settings = load_settings(env_file=None)

    assert settings.port == 8000


def test_process_environment_overrides_explicit_env_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(tmp_path / "operator.env", "FRAMENEST_PORT=8123\n")
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))
    monkeypatch.setenv("FRAMENEST_PORT", "8200")

    settings = load_settings()

    assert settings.port == 8200


def test_malformed_explicit_env_file_fails_deterministically(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env_file(
        tmp_path / "operator.env",
        "FRAMENEST_PORT=not-a-number\n",
    )
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))

    with pytest.raises(ValidationError):
        load_settings()
    with pytest.raises(ValidationError):
        load_settings()


def test_explicit_relative_env_file_is_operator_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_env_file(tmp_path / ".env", "FRAMENEST_PORT=8123\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, ".env")

    settings = load_settings()

    assert settings.port == 8123


def test_load_settings_does_not_depend_on_resolvable_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    _write_env_file(inner / ".env", "FRAMENEST_PORT=not-a-number\n")
    env_file = _write_env_file(tmp_path / "operator.env", "FRAMENEST_PORT=8123\n")
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))
    monkeypatch.chdir(inner)
    outer.chmod(0o000)
    try:
        settings = load_settings()
    finally:
        outer.chmod(0o700)

    assert settings.port == 8123
