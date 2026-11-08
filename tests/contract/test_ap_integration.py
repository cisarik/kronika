"""Contract tests for FrameNest's pinned Analytic Programming integration."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

EXPECTED_AP_COMMIT = "d7023cf037f6d17885c3ede5c04812c61640da50"
EXPECTED_AP_URL = "https://github.com/cisarik/ap.git"
LEGACY_PROTOCOL_FILES = (
    "AP.md",
    "AP_ORCHESTRATOR.md",
    "AP_WORKER.md",
    "BOOT_ORCHESTRATOR.md",
    "BOOT_WORKER.md",
    "NEXT_ORCHESTRATOR.md",
    "NEXT_WORKER.md",
)


def _run_git(*args: str) -> str:
    result = subprocess.run(
        ("git", *args),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _require_initialized_ap_submodule() -> None:
    ap_tool = Path(".ap/ap")
    if not ap_tool.is_file():
        pytest.fail(
            "FrameNest AP submodule is not initialized; run "
            "`git submodule update --init --recursive` from the repository root."
        )


def test_ap_submodule_gitlink_and_configuration_are_pinned() -> None:
    _require_initialized_ap_submodule()

    assert _run_git("config", "--file", ".gitmodules", "submodule..ap.path") == ".ap"
    assert (
        _run_git("config", "--file", ".gitmodules", "submodule..ap.url")
        == EXPECTED_AP_URL
    )
    assert _run_git("-C", ".ap", "rev-parse", "HEAD") == EXPECTED_AP_COMMIT
    assert (
        _run_git("ls-files", "-s", ".ap")
        == f"160000 {EXPECTED_AP_COMMIT} 0\t.ap"
    )


def test_ap_integration_is_healthy_and_not_copied_to_root() -> None:
    _require_initialized_ap_submodule()

    doctor = subprocess.run(
        ("./.ap/ap", "doctor"),
        check=False,
        capture_output=True,
        text=True,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert "ap doctor: PASS" in doctor.stdout

    agents = Path("AGENTS.md").read_text(encoding="utf-8")
    assert "<!-- BEGIN MANAGED AP INTEGRATION -->" in agents
    assert "<!-- END MANAGED AP INTEGRATION -->" in agents

    for legacy_file in LEGACY_PROTOCOL_FILES:
        assert not Path(legacy_file).exists(), legacy_file
