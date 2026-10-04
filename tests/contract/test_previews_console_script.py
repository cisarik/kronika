"""Contract tests for the FrameNest gallery preview operator console entry point."""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PREVIEWS_CONSOLE_SCRIPT = REPOSITORY_ROOT / ".venv" / "bin" / "framenest-previews"
PYTHON_EXECUTABLE = REPOSITORY_ROOT / ".venv" / "bin" / "python"


def _require_previews_console_script() -> Path:
    if not PREVIEWS_CONSOLE_SCRIPT.is_file():
        raise AssertionError(f"Expected installed console script at {PREVIEWS_CONSOLE_SCRIPT}")
    return PREVIEWS_CONSOLE_SCRIPT


def _run_previews_command(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("FRAMENEST_API_KEY", None)
    return subprocess.run(
        [str(_require_previews_console_script()), *args],
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )


def test_previews_console_script_is_declared() -> None:
    metadata = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    assert (
        metadata["project"]["scripts"]["framenest-previews"]
        == "kronika.adapters.cli.previews:main"
    )


def test_previews_console_script_is_installed() -> None:
    assert PREVIEWS_CONSOLE_SCRIPT.is_file()


def test_importing_previews_cli_has_no_execution_side_effects(tmp_path: Path) -> None:
    result = subprocess.run(
        [str(PYTHON_EXECUTABLE), "-c", "import kronika.adapters.cli.previews"],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
        timeout=8.0,
    )

    assert result.returncode == 0
    assert result.stdout == ""
    assert result.stderr == ""


def test_previews_help_succeeds(tmp_path: Path) -> None:
    result = _run_previews_command("--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "framenest-previews" in result.stdout
    assert "status" in result.stdout
    assert "generate" in result.stdout


def test_previews_status_help_succeeds(tmp_path: Path) -> None:
    result = _run_previews_command("status", "--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "--library-id" in result.stdout


def test_previews_generate_help_succeeds(tmp_path: Path) -> None:
    result = _run_previews_command("generate", "--help", cwd=tmp_path)

    assert result.returncode == 0
    assert "--all" in result.stdout
    assert "--yes" in result.stdout
    assert "--max-items" in result.stdout
