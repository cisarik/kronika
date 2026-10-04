"""Contract tests for FrameNest's baseline-bound AP execution envelope.

Static contract tests parse the root ``ap.project.conf`` through Git's own
config parser and run in any environment, including direct pre-commit Poetry
execution.  The live-environment sentinel tests run only inside an ``ap exec``
child process (detected through ``AP_OPERATION``) and prove that the approved
execution values are present while inherited contamination classes are absent.
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = PROJECT_ROOT / "ap.project.conf"

EXPECTED_PROJECT_ID = "cisarik/kronika"
EXPECTED_RUNTIME_INFO_CODE = (
    "import sys, kronika; "
    "print(sys.executable); "
    "print(sys.version); "
    "print(kronika.__file__)"
)
EXPECTED_OPERATIONS = ("runtime-info", "test", "test-focus")
EXPECTED_TRAILING_ARGV = {
    "runtime-info": "false",
    "test": "false",
    "test-focus": "true",
}

AP_ENVELOPE_KEYS = {
    "AP_BASELINE",
    "AP_OPERATION",
    "AP_PROJECT_ROOT",
    "LANG",
    "LC_ALL",
    "PATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONNOUSERSITE",
    "PYTHONPATH",
}


def _git_config(*args: str) -> list[str]:
    """Read the worktree contract through Git's own config parser."""
    result = subprocess.run(
        (
            "git",
            "config",
            "--no-includes",
            "--file",
            str(CONTRACT_PATH),
            *args,
        ),
        check=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    return result.stdout.splitlines()


def _git_config_value(key: str) -> str:
    values = _git_config("--get", key)
    assert len(values) == 1, f"{key} must occur exactly once"
    return values[0]


def _origin_derived_project_id() -> str:
    """Derive owner/project from remote.origin.url like the AP tool does."""
    result = subprocess.run(
        ("git", "config", "--get", "remote.origin.url"),
        check=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    )
    origin = result.stdout.strip().removesuffix("/").removesuffix(".git")
    if "://" in origin:
        path = origin.split("://", 1)[1].split("/", 1)[1]
    elif "@" in origin and ":" in origin.split("@", 1)[1]:
        path = origin.split(":", 1)[1]
    else:
        pytest.fail(f"remote.origin.url cannot be mapped to owner/project: {origin!r}")
    return f"{path.split('/')[-2]}/{path.split('/')[-1]}"


def test_contract_is_valid_git_config_syntax() -> None:
    keys = _git_config("--list")
    assert keys, "ap.project.conf contains no configuration"


def test_schema_v1_fields_match_the_approved_contract() -> None:
    allowed_keys = {
        "ap.schemaversion",
        "ap.projectid",
        "ap.environmentpolicy",
        "runtime.cpython.kind",
        "runtime.cpython.executable",
        "runtime.cpython.requiredversion",
        "runtime.cpython.sourceroot",
        "runtime.cpython.provenancemodule",
    } | {
        f"operation.{operation}.{field}"
        for operation in EXPECTED_OPERATIONS
        for field in ("workingdirectory", "argv", "allowtrailingargv")
    }
    listed = {entry.split("=", 1)[0] for entry in _git_config("--list")}
    assert listed <= allowed_keys, f"unknown schema-v1 keys: {sorted(listed - allowed_keys)}"

    assert _git_config_value("ap.schemaversion") == "1"
    assert _git_config_value("ap.projectid") == EXPECTED_PROJECT_ID
    assert _git_config_value("ap.environmentpolicy") == "sanitized-v1"
    for operation in EXPECTED_OPERATIONS:
        assert _git_config_value(f"operation.{operation}.workingdirectory") == "."


def test_repeated_argv_values_retain_exact_order() -> None:
    assert _git_config("--get-all", "operation.runtime-info.argv") == [
        "-c",
        EXPECTED_RUNTIME_INFO_CODE,
    ]
    assert _git_config("--get-all", "operation.test.argv") == ["-m", "pytest"]
    assert _git_config("--get-all", "operation.test-focus.argv") == ["-m", "pytest"]


def test_operation_ids_are_exactly_the_three_approved_operations() -> None:
    keys = {entry.split("=", 1)[0] for entry in _git_config("--list")}
    operations = sorted(
        {key.split(".")[1] for key in keys if key.startswith("operation.")}
    )
    assert operations == sorted(EXPECTED_OPERATIONS)


def test_trailing_argv_is_allowed_only_for_test_focus() -> None:
    for operation, expected in EXPECTED_TRAILING_ARGV.items():
        assert _git_config_value(f"operation.{operation}.allowtrailingargv") == expected


def test_runtime_contract_is_exact() -> None:
    assert _git_config_value("runtime.cpython.kind") == "cpython"
    assert _git_config_value("runtime.cpython.executable") == ".venv/bin/python"
    assert _git_config_value("runtime.cpython.requiredversion") == "3.13"
    assert _git_config_value("runtime.cpython.sourceroot") == "src"
    assert _git_config_value("runtime.cpython.provenancemodule") == "kronika"


def test_project_id_agrees_with_origin_derived_identity() -> None:
    assert _git_config_value("ap.projectid") == _origin_derived_project_id()


def _require_ap_operation() -> str:
    operation = os.environ.get("AP_OPERATION")
    if operation is None:
        pytest.skip("AP_OPERATION is absent; not running inside an AP operation")
    return operation


def test_ap_exec_child_contains_approved_execution_values() -> None:
    operation = _require_ap_operation()

    assert operation in EXPECTED_OPERATIONS
    assert os.environ["PATH"] == "/usr/bin:/bin"
    assert os.environ["PYTHONPATH"] == str(PROJECT_ROOT / "src")
    assert os.environ["AP_PROJECT_ROOT"] == str(PROJECT_ROOT)

    baseline = os.environ["AP_BASELINE"]
    assert re.fullmatch(r"[0-9a-f]{40}", baseline), baseline
    resolved = subprocess.run(
        ("git", "rev-parse", "--verify", f"{baseline}^{{commit}}"),
        check=True,
        capture_output=True,
        text=True,
        cwd=PROJECT_ROOT,
    ).stdout.strip()
    assert resolved == baseline

    # macOS Core Foundation injects __CF_USER_TEXT_ENCODING at exec.
    assert set(os.environ) <= AP_ENVELOPE_KEYS | {"__CF_USER_TEXT_ENCODING"} | {
        key for key in os.environ if key.startswith("PYTEST_")
    }


def test_ap_exec_child_has_no_inherited_contamination() -> None:
    _require_ap_operation()

    forbidden_exact = (
        "APPIMAGE",
        "APPDIR",
        "GIT_ASKPASS",
        "GIT_TERMINAL_PROMPT",
        "PYTHONHOME",
        "SSH_AUTH_SOCK",
        "VIRTUAL_ENV",
        "VIRTUAL_ENV_DISABLE_PROMPT",
    )
    for name in forbidden_exact:
        assert name not in os.environ, f"{name} leaked into the AP child process"

    forbidden_prefixes = ("GIT_CONFIG_", "LD_", "VSCODE_")
    leaked = [
        name
        for name in os.environ
        if name.startswith(forbidden_prefixes)
    ]
    assert leaked == [], f"contaminated variables reached the AP child: {leaked}"
