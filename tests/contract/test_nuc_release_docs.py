"""Documentation-parity contract tests for the NUC release-update contract."""

from __future__ import annotations

import stat
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPOSITORY_ROOT / "AGENTS.md"
README_PATH = REPOSITORY_ROOT / "README.md"
SERVER_PATH = REPOSITORY_ROOT / "SERVER.md"
RUNBOOK_PATH = REPOSITORY_ROOT / "docs" / "UBUNTU_NUC_DEPLOYMENT.md"
INFOSEC_PATH = REPOSITORY_ROOT / "docs" / "INFOSEC.md"
ACCEPTANCE_PATH = REPOSITORY_ROOT / "docs" / "ACCEPTANCE_DUAL_AUDIENCE.md"
DEPLOY_README_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "README.md"
ADR_INDEX_PATH = REPOSITORY_ROOT / "docs" / "adr" / "README.md"
ADR_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "0060-repeatable-immutable-nuc-release-update-contract.md"
)
NUC_BASELINE_PATH = REPOSITORY_ROOT / "docs" / "NUC_HOST_BASELINE.md"
FISH_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest-release"
CANONICAL_FISH_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika-release"
ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest_release.py"
CANONICAL_ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"

POETRY_PATH = "/opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry"
CPYTHON_PATH = (
    "/opt/framenest/tooling/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13"
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_agents_documents_canonical_entry_point() -> None:
    text = _text(AGENTS_PATH)
    assert "deploy/ubuntu/kronika-release" in text
    assert "deploy/ubuntu/kronika_release.py" in text
    # The retained spellings are named as still working until the
    # compatibility-removal cut.
    assert "deploy/ubuntu/framenest-release" in text
    assert "deploy/ubuntu/framenest_release.py" in text
    assert POETRY_PATH in text
    assert CPYTHON_PATH in text
    assert "uv" in text
    assert "status" in text
    assert ".framenest-release-manifest.json" in text


def test_agents_requires_check_before_deployment() -> None:
    text = _text(AGENTS_PATH)
    assert "kronika-release status" in text
    assert "kronika-release check" in text
    assert "Deployment never follows automatically" in text


def test_adr_0060_indexed_and_accepted() -> None:
    index = _text(ADR_INDEX_PATH)
    assert "0060 | Repeatable Immutable NUC Release-Update Contract" in index
    assert "0060-repeatable-immutable-nuc-release-update-contract.md" in index

    adr = _text(ADR_PATH)
    assert "## Status\n\n`Accepted`" in adr
    assert "deploy/ubuntu/framenest-release" in adr
    assert "migration-required" in adr


def test_runbook_separates_bootstrap_from_routine_update() -> None:
    text = _text(RUNBOOK_PATH)
    assert "Routine Immutable Release Update" in text
    assert "Initial bootstrap versus routine update" in text
    assert "never invokes `uv`" in text
    assert POETRY_PATH in text
    assert CPYTHON_PATH in text
    assert "migration-required" in text


def test_runbook_reconciles_production_claim_as_dated_history() -> None:
    text = _text(RUNBOOK_PATH)
    assert "framenest-release status" in text
    assert "dated history" in text


def test_deploy_readme_no_longer_claims_untested_automation() -> None:
    text = _text(DEPLOY_README_PATH)
    assert "without adding\nuntested host-mutating automation" not in text
    assert "untested host-mutating automation" not in text
    assert "framenest-release" in text
    assert "0075-nuc-development-test-target-and-routine-release-refresh.md" in text
    assert "migration-required" in text
    assert "section 5 annex" in text


def test_nuc_host_baseline_keeps_history_and_adds_cross_ref() -> None:
    text = _text(NUC_BASELINE_PATH)
    assert "0060-repeatable-immutable-nuc-release-update-contract.md" in text
    # Historical observations are preserved, not rewritten.
    assert "Secure Boot disabled" in text


def test_readme_reconciles_production_claim() -> None:
    text = _text(README_PATH)
    assert "framenest-release status" in text
    assert "dated history" in text
    assert "aec2f0091c10aed2fc2033dac154a0d9651b2b6d" in text
    assert "exact public `main` SHA" in text
    assert "Companion review Save does not publish" in text
    assert "Apply writes metadata\nonly" in text
    assert "sole publication path" in text
    assert "`public_published_uds` composition and workspace rollout successors are\nimplemented at this baseline" in text
    assert "may publish after review Save" not in text
    assert "None of those successors is shipped" not in text


def test_server_records_dev_test_authoritative_serving() -> None:
    text = _text(SERVER_PATH)
    assert "disposable\ndevelopment-and-testing instance" in text
    assert "exact public `main` SHA" in text
    assert "framenest-release status" in text
    assert "owner-authoritative production release" not in text
    assert "aec2f0091c10aed2fc2033dac154a0d9651b2b6d" in text


def test_infosec_present_tense_is_development_test_workspace() -> None:
    text = _text(INFOSEC_PATH)
    assert "development-test workspace access over Tailscale Serve" in text
    assert "serves production over Tailscale" not in text
    assert "3a21405e08ff30a840afe655e702d931e833acf2" in text


def test_acceptance_part_b_gates_on_tested_sha() -> None:
    text = _text(ACCEPTANCE_PATH)
    assert "BLOCKED: NUC not at tested SHA" in text
    assert "framenest-release status" in text
    assert "No rendered Apply entry exists for analyzed rows" in text
    assert "Apply acceptance is deterministic by owner decision\n  (2026-08-26)" in text


def test_runbook_documents_exit_13_schema_jump_continuation() -> None:
    text = _text(RUNBOOK_PATH)
    assert "exits exactly 13" in text
    assert "`migration-required`" in text
    assert "/opt/framenest/releases/<T>" in text
    assert ".framenest-release-sha" in text
    assert "current_revision=0032" in text
    assert "head_revision=0033" in text
    assert "current_revision=head_revision=0033" in text
    assert "/run/framenest-release-deploy/ap.tar" in text
    assert "/run/framenest-release-deploy/framenest_release.py" in text
    assert "/run/framenest-release-deploy/superproject.tar" in text
    assert "rollback --release <T> --yes" in text
    assert "sudo -K" in text
    assert "/opt/framenest/current/.venv/bin/framenest-db migrate" not in text
    assert "/opt/framenest/releases/<T>/.venv/bin/framenest-db migrate" in text


def test_engine_and_entry_point_are_committed_together() -> None:
    assert FISH_PATH.exists()
    assert ENGINE_PATH.exists()
    assert CANONICAL_FISH_PATH.exists()
    assert CANONICAL_ENGINE_PATH.exists()


def test_the_retained_entry_points_forward_to_the_canonical_ones() -> None:
    entry = _text(FISH_PATH)
    assert "kronika-release" in entry
    assert "framenest_release.py" not in entry
    module = _text(ENGINE_PATH)
    assert "kronika_release.py" in module
    assert "Kronika release engine is unavailable." in module


def test_engine_documents_never_uv_or_migrate() -> None:
    for path in (ENGINE_PATH, CANONICAL_ENGINE_PATH):
        text = _text(path)
        # The engine must not shell out to uv or run migrations anywhere.
        assert "framenest-db migrate" not in text
        assert "uv " not in text


def test_poetry_toml_virtualenv_in_project_declared() -> None:
    text = _text(CANONICAL_ENGINE_PATH)
    assert 'in-project = true' in text
    assert 'POETRY_TOML = "[virtualenvs]\\nin-project = true\\n"' in text


def test_exit_codes_are_distinct_and_documented() -> None:
    text = _text(CANONICAL_ENGINE_PATH)
    for code in (
        "EXIT_SOURCE_GATE",
        "EXIT_PUBLIC_MISMATCH",
        "EXIT_AP_MISMATCH",
        "EXIT_TOOLING",
        "EXIT_ARCHIVE_HASH",
        "EXIT_UNSAFE_ARCHIVE",
        "EXIT_EXISTS",
        "EXIT_CAPACITY",
        "EXIT_BACKUP_NOT_READY",
        "EXIT_CHECKPOINT",
        "EXIT_MIGRATION_REQUIRED",
        "EXIT_POETRY",
        "EXIT_READINESS",
        "EXIT_SERVICE_TERMINAL",
        "EXIT_READINESS_TIMEOUT",
        "EXIT_ROLLBACK",
        "EXIT_CLEANUP",
        "EXIT_TRANSPORT",
        "EXIT_PRIVILEGE",
        "EXIT_MARKER_CONFLICT",
        "EXIT_UNIT_EXEC_GUARD",
    ):
        assert code in text


def test_adr_documents_environmentfile_production_cli_and_bounded_readiness() -> None:
    adr = _text(ADR_PATH)
    assert "EnvironmentFile" in adr
    assert "FRAMENEST_ENV_FILE" in adr
    assert "30 seconds" in adr
    assert "EXIT_READINESS_TIMEOUT" in adr
    assert "one-second polling" in adr


def test_runbook_documents_environmentfile_production_cli_and_bounded_readiness() -> None:
    text = _text(RUNBOOK_PATH)
    assert "EnvironmentFile" in text
    assert "EXIT_READINESS_TIMEOUT" in text
    assert "30 seconds" in text


def test_runbook_and_deploy_readme_document_capture_activation() -> None:
    runbook = _text(RUNBOOK_PATH)
    deploy = _text(DEPLOY_README_PATH)
    assert "Capture Runtime Sources (Not Deployed)" in runbook
    assert "activate-capture --release <40-hex-SHA> --yes" in runbook
    assert "rollback-capture --release <40-hex-SHA> --yes" in runbook
    assert "do not record a completed capture deployment" in runbook
    assert "activate-capture --release <40-hex-SHA> --yes" in deploy
    assert "rollback-capture --release <40-hex-SHA> --yes" in deploy
    assert "evidence that capture is already deployed" in deploy


def test_canonical_and_retained_entry_points_are_executable() -> None:
    for path in (CANONICAL_FISH_PATH, FISH_PATH, REPOSITORY_ROOT / "kronika"):
        assert path.is_file(), path
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, path
    for path in (
        REPOSITORY_ROOT / "scripts" / "operator" / "network" / "kronika_nuc_worker_gate.fish",
        REPOSITORY_ROOT / "scripts" / "operator" / "network" / "framenest_nuc_worker_gate.fish",
    ):
        assert path.is_file(), path
        assert path.stat().st_mode & stat.S_IXUSR, path


def test_the_retained_gate_wrapper_forwards_to_the_canonical_gate() -> None:
    wrapper = _text(
        REPOSITORY_ROOT / "scripts" / "operator" / "network" / "framenest_nuc_worker_gate.fish"
    )
    canonical = _text(
        REPOSITORY_ROOT / "scripts" / "operator" / "network" / "kronika_nuc_worker_gate.fish"
    )

    assert "kronika_nuc_worker_gate.fish" in wrapper
    assert "exec" in wrapper
    # The retained wrapper carries no gate logic of its own.
    assert "_resolve_env" not in wrapper
    assert "_resolve_env" in canonical
    assert "KRONIKA_" in canonical
    assert "FRAMENEST_" in canonical


def test_the_canonical_entry_point_reports_the_canonical_program_name() -> None:
    engine_text = _text(CANONICAL_ENGINE_PATH)

    assert 'PROGRAM = "kronika-release"' in engine_text
    assert 'print("kronika-release status")' in engine_text
    assert 'print("kronika-release check")' in engine_text
    assert "framenest-release status" not in engine_text
    assert "framenest-release deploy complete" not in engine_text


def test_the_installed_unit_guard_and_the_lock_are_engine_owned() -> None:
    engine_text = _text(CANONICAL_ENGINE_PATH)

    assert "cmd_remote_unit_execution_properties" in engine_text
    assert "verify_unit_executables" in engine_text
    assert "cmd_remote_test_regular_executable" in engine_text
    assert "remote_deploy_lock" in engine_text
    assert "classify_deploy_lock_owner" in engine_text
    assert "read_release_markers" in engine_text
