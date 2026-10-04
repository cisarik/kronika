"""External ffprobe and ffmpeg executable discovery."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from kronika.application.media_analysis import (
    FFPROBE_TIMEOUT_SECONDS,
    MAX_TOOL_VERSION_LENGTH,
    SUBPROCESS_STDERR_MAX_BYTES,
)
from kronika.infrastructure.media_analysis.process import (
    PROCESS_FAILED_MESSAGE,
    ProcessExecutionError,
    ProcessRunner,
)

TOOL_NOT_AVAILABLE_MESSAGE = "Required external media tool is not available."
TOOL_IDENTITY_FAILED_MESSAGE = "External media tool identity check failed."


_FALLBACK_TOOL_DIRECTORIES = ("/opt/homebrew/bin", "/usr/local/bin")


def resolve_executable(name: str) -> str:
    """Resolve one external tool to an absolute executable path.

    ``PATH`` comes first. When it does not contain the tool, standard Homebrew
    locations cover macOS development hosts whose execution envelope fixes
    ``PATH`` to ``/usr/bin:/bin``.
    """
    resolved = shutil.which(name)
    if resolved is None:
        for directory in _FALLBACK_TOOL_DIRECTORIES:
            candidate = Path(directory) / name
            if candidate.is_file() and os.access(candidate, os.X_OK):
                resolved = str(candidate)
                break
    if resolved is None:
        raise ProcessExecutionError(TOOL_NOT_AVAILABLE_MESSAGE)
    return resolved


def _first_version_line(stdout: bytes, stderr: bytes) -> str:
    for chunk in (stdout, stderr):
        text = chunk.decode("utf-8", errors="replace")
        for line in text.splitlines():
            stripped = line.strip()
            if stripped:
                return stripped[:MAX_TOOL_VERSION_LENGTH]
    return "unknown"


def validate_tool_identity(
    runner: ProcessRunner,
    *,
    executable: str,
    expected_name: str,
) -> str:
    """Verify executable identity and return a sanitized first version line."""
    try:
        result = runner.run(
            executable=executable,
            argv=["-version"],
            timeout_seconds=FFPROBE_TIMEOUT_SECONDS,
            stdout_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
            stderr_max_bytes=SUBPROCESS_STDERR_MAX_BYTES,
        )
    except ProcessExecutionError:
        raise ProcessExecutionError(TOOL_NOT_AVAILABLE_MESSAGE) from None
    if result.returncode != 0:
        raise ProcessExecutionError(TOOL_IDENTITY_FAILED_MESSAGE)
    version_line = _first_version_line(result.stdout, result.stderr)
    if expected_name not in version_line.lower():
        raise ProcessExecutionError(TOOL_IDENTITY_FAILED_MESSAGE)
    return version_line


def resolve_ffprobe(runner: ProcessRunner) -> tuple[str, str]:
    """Resolve ffprobe and return executable path plus sanitized version line."""
    executable = resolve_executable("ffprobe")
    version = validate_tool_identity(runner, executable=executable, expected_name="ffprobe")
    return executable, version


def resolve_ffmpeg(runner: ProcessRunner) -> tuple[str, str]:
    """Resolve ffmpeg and return executable path plus sanitized version line."""
    executable = resolve_executable("ffmpeg")
    version = validate_tool_identity(runner, executable=executable, expected_name="ffmpeg")
    return executable, version


def sanitize_retained_stderr(stderr: bytes) -> str:
    """Return bounded sanitized stderr text for internal diagnostics only."""
    text = stderr.decode("utf-8", errors="replace").strip()
    if not text:
        return ""
    first_line = text.splitlines()[0].strip()
    return first_line[:MAX_TOOL_VERSION_LENGTH]
