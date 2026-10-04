"""Contract tests for operator CLI configuration and working-directory hygiene."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from framenest.configuration import ENV_FILE_ENVIRONMENT_VARIABLE
from framenest.infrastructure.persistence import cli as db_cli

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
DB_MODULE = "framenest.infrastructure.persistence.cli"
YOUTUBE_MODULE = "framenest.adapters.cli.youtube"

CLI_MODULES = (
    "framenest.server",
    "framenest.infrastructure.persistence.cli",
    "framenest.infrastructure.runtime.production",
    "framenest.adapters.cli.ai",
    "framenest.adapters.cli.backup",
    "framenest.adapters.cli.catalog",
    "framenest.adapters.cli.library",
    "framenest.adapters.cli.previews",
    "framenest.adapters.cli.recovery",
    "framenest.adapters.cli.youtube",
)


def _operator_env(tmp_path: Path, env_file: Path | None) -> dict[str, str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "PYTHONNOUSERSITE": "1",
    }
    if env_file is not None:
        env[ENV_FILE_ENVIRONMENT_VARIABLE] = str(env_file)
    return env


def _write_env_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


def _run_module(
    module: str,
    args: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", module, *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_db_status_succeeds_from_unrelated_cwd_with_explicit_env(
    tmp_path: Path,
) -> None:
    caller_cwd = tmp_path / "unrelated"
    caller_cwd.mkdir()
    env_file = _write_env_file(
        tmp_path / "operator.env",
        f"FRAMENEST_DATABASE_PATH={tmp_path / 'state' / 'catalog.sqlite3'}\n",
    )

    result = _run_module(
        DB_MODULE,
        ["status"],
        cwd=caller_cwd,
        env=_operator_env(tmp_path, env_file),
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["operation"] == "status"
    assert payload["state"] == "uninitialized"
    # Read-only status must not create the database (no implicit migration).
    assert not (tmp_path / "state" / "catalog.sqlite3").exists()


def test_db_status_ignores_random_caller_cwd_dotenv(tmp_path: Path) -> None:
    caller_cwd = tmp_path / "unrelated"
    caller_cwd.mkdir()
    # Positive control: this file would fail validation if it were consumed.
    _write_env_file(
        caller_cwd / ".env",
        "FRAMENEST_PORT=not-a-number\n"
        "FRAMENEST_DATABASE_PATH=/nonexistent/arbitrary.sqlite3\n",
    )
    env_file = _write_env_file(
        tmp_path / "operator.env",
        f"FRAMENEST_DATABASE_PATH={tmp_path / 'state' / 'catalog.sqlite3'}\n",
    )

    result = _run_module(
        DB_MODULE,
        ["status"],
        cwd=caller_cwd,
        env=_operator_env(tmp_path, env_file),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "uninitialized"


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file permission bits")
def test_db_status_ignores_unreadable_caller_cwd_dotenv(tmp_path: Path) -> None:
    caller_cwd = tmp_path / "unrelated"
    caller_cwd.mkdir()
    unreadable = _write_env_file(caller_cwd / ".env", "FRAMENEST_PORT=9999\n")
    unreadable.chmod(0o000)
    env_file = _write_env_file(
        tmp_path / "operator.env",
        f"FRAMENEST_DATABASE_PATH={tmp_path / 'state' / 'catalog.sqlite3'}\n",
    )

    result = _run_module(
        DB_MODULE,
        ["status"],
        cwd=caller_cwd,
        env=_operator_env(tmp_path, env_file),
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["state"] == "uninitialized"


def test_db_status_explicit_missing_env_file_fails_closed_sanitized(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.env"
    result = _run_module(
        DB_MODULE,
        ["status"],
        cwd=tmp_path,
        env=_operator_env(tmp_path, missing),
    )

    assert result.returncode == 1
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error_code"] == "FRAMENEST_DB_CONFIGURATION_FAILED"
    assert payload["message"] == "Kronika configuration could not be loaded."
    assert str(missing) not in result.stderr
    assert "Traceback" not in result.stderr
    assert "Permission denied" not in result.stderr


def test_youtube_explicit_missing_env_file_fails_closed_sanitized(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "missing.env"
    result = _run_module(
        YOUTUBE_MODULE,
        ["status", "6b3945b8-4c91-41b5-82c2-142645377fc4"],
        cwd=tmp_path,
        env=_operator_env(tmp_path, missing),
    )

    assert result.returncode == 6
    assert result.stdout == ""
    payload = json.loads(result.stderr)
    assert payload["error_code"] == "YOUTUBE_CONFIGURATION_FAILED"
    assert payload["message"] == "Kronika configuration could not be loaded."
    assert str(missing) not in result.stderr
    assert "Traceback" not in result.stderr


def test_db_help_succeeds_from_unrelated_cwd(tmp_path: Path) -> None:
    result = _run_module(
        DB_MODULE,
        ["--help"],
        cwd=tmp_path,
        env=_operator_env(tmp_path, None),
    )

    assert result.returncode == 0
    assert "migrate" in result.stdout
    assert "status" in result.stdout


def test_youtube_help_succeeds_from_unrelated_cwd(tmp_path: Path) -> None:
    result = _run_module(
        YOUTUBE_MODULE,
        ["--help"],
        cwd=tmp_path,
        env=_operator_env(tmp_path, None),
    )

    assert result.returncode == 0
    assert "ingest" in result.stdout
    assert "status" in result.stdout
    assert "retry" in result.stdout


def test_cli_module_import_has_no_configuration_side_effects(tmp_path: Path) -> None:
    # Imports must not load settings, probe the caller CWD, or touch the
    # explicit environment file; a missing explicit file must be survivable
    # at import time.
    unreadable = _write_env_file(tmp_path / ".env", "FRAMENEST_PORT=9999\n")
    if os.geteuid() != 0:
        unreadable.chmod(0o000)
    imports = "; ".join(f"import {module}" for module in CLI_MODULES)
    result = subprocess.run(
        [sys.executable, "-c", imports],
        cwd=tmp_path,
        env=_operator_env(tmp_path, tmp_path / "missing.env"),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""


def test_db_command_succeeds_when_cwd_becomes_unresolvable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Reproduces the inherited-inaccessible-CWD class with disposable
    # fixtures only: the process working directory cannot be traversed or
    # restored, exactly as when an identity switch inherits an unrelated
    # home directory. The CLI must not depend on that directory at all.
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    _write_env_file(inner / ".env", "FRAMENEST_PORT=not-a-number\n")
    env_file = _write_env_file(
        tmp_path / "operator.env",
        f"FRAMENEST_DATABASE_PATH={tmp_path / 'state' / 'catalog.sqlite3'}\n",
    )
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(env_file))
    monkeypatch.chdir(inner)
    outer.chmod(0o000)
    try:
        result = db_cli.main(["status"])
    finally:
        outer.chmod(0o700)

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["state"] == "uninitialized"
    assert not (tmp_path / "state" / "catalog.sqlite3").exists()


def test_db_command_explicit_env_file_unreadable_fails_closed_in_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "missing.env"
    monkeypatch.setenv(ENV_FILE_ENVIRONMENT_VARIABLE, str(missing))
    monkeypatch.chdir(tmp_path)

    result = db_cli.main(["status"])

    assert result == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    payload = json.loads(captured.err)
    assert payload["error_code"] == "FRAMENEST_DB_CONFIGURATION_FAILED"
    assert str(missing) not in captured.err
