"""Documentation-parity contract tests for the NUC release-update contract."""

from __future__ import annotations

import inspect
import os
import re
import shlex
import socket
import stat
from pathlib import Path

import pytest

from kronika.infrastructure.persistence import catalog_backup_ops as backup_ops
from kronika.infrastructure.persistence.catalog_backup_offdevice import DEFAULT_OFFDEVICE_ROOT
from kronika.infrastructure.persistence.catalog_backup_transfer import PROTOCOL_MAGIC
from kronika_capture.config import STATE_DIR_NAME
from tests.contract import test_nuc_release_remote_contract as _remote_contract

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
AGENTS_PATH = REPOSITORY_ROOT / "AGENTS.md"
README_PATH = REPOSITORY_ROOT / "README.md"
SERVER_PATH = REPOSITORY_ROOT / "SERVER.md"
RUNBOOK_PATH = REPOSITORY_ROOT / "docs" / "UBUNTU_NUC_DEPLOYMENT.md"
CLEAN_INSTALL_PATH = REPOSITORY_ROOT / "docs" / "UBUNTU_NUC_CLEAN_INSTALL.md"
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

#: The documentation pins are cross-checked against the real engine, so a
#: renamed lock path, artifact or reclaim reason fails here instead of going
#: stale in the runbook. The engine instance is the one the remote-contract
#: suite already loaded from the canonical path.
_ENGINE = _remote_contract.engine


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _a_dead_pid() -> int:
    """Return a pid that is certainly not running on this workstation."""

    for candidate in range(4_000_000, 4_000_050):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:  # pragma: no cover - not reachable in practice
            continue
    raise AssertionError("no certainly-unused pid found")  # pragma: no cover


def test_agents_documents_canonical_entry_point() -> None:
    text = _text(AGENTS_PATH)
    assert "deploy/ubuntu/kronika-release" in text
    assert "deploy/ubuntu/kronika_release.py" in text
    assert "wrappers are removed" in text
    assert "deploy/ubuntu/framenest-release" in text
    assert "deploy/ubuntu/framenest_release.py" in text
    assert "C7-B window" not in text
    assert "still runs the same" not in text
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
    assert "kronika-release status" in text
    assert "dated history" in text


def test_deploy_readme_no_longer_claims_untested_automation() -> None:
    text = _text(DEPLOY_README_PATH)
    assert "without adding\nuntested host-mutating automation" not in text
    assert "untested host-mutating automation" not in text
    assert "kronika-release" in text
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
    assert "kronika-release status" in text
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
    assert "kronika-release status" in text
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
    assert "kronika-release status" in text
    assert "No rendered Apply entry exists for analyzed rows" in text
    assert "Apply acceptance is deterministic by owner decision\n  (2026-08-26)" in text


def test_runbook_documents_exit_13_schema_jump_continuation() -> None:
    text = _text(RUNBOOK_PATH)
    assert "exits exactly 13" in text
    assert "`migration-required`" in text
    assert "/opt/kronika/releases/<T>" in text
    assert ".kronika-release-sha" in text
    # The schema pair is derived from the observed status, never a permanent
    # example: the stale 0032 -> 0033 literals must not survive in the annex.
    assert "current_revision=<C>" in text
    assert "head_revision=<H>" in text
    assert "current_revision=head_revision=<H>" in text
    assert "current_revision=0032" not in text
    assert "head_revision=0033" not in text
    assert "current_revision=head_revision=0033" not in text
    assert "`0033`" not in text
    assert "rollback --release <T> --yes" in text
    assert "sudo -K" in text
    assert "/opt/kronika/current/.venv/bin/kronika-db migrate" not in text
    assert "/opt/kronika/releases/<T>/.venv/bin/kronika-db migrate" in text


def test_runbook_lock_lifecycle_matches_the_engine() -> None:
    """Every documented lock object, reason and path is engine-derived."""
    text = _text(RUNBOOK_PATH)
    engine = _ENGINE

    assert engine.REMOTE_DEPLOY_DIR in text
    assert engine.REMOTE_DEPLOY_LOCK_OWNER_PATH in text

    # The lock creation is deliberately non-recursive: no `-p` flag.
    mkdir_command = engine.cmd_remote_mkdir_deploy_dir()
    assert " -p " not in mkdir_command
    assert f"mkdir -m 0700 {engine.REMOTE_DEPLOY_DIR}" in mkdir_command
    assert mkdir_command in text

    # The deploy-phase artifact inventory is parsed from the production
    # method, so a renamed or added artifact cannot hide behind stale prose.
    deploy_artifacts = set(
        re.findall(
            r"\{REMOTE_DEPLOY_DIR\}/([A-Za-z0-9_.-]+)",
            inspect.getsource(engine._cmd_deploy),
        )
    )
    assert deploy_artifacts == {
        "superproject.tar",
        "ap.tar",
        "kronika_release.py",
        "previous-release",
    }
    rollback_artifacts = set(
        re.findall(
            r"\{REMOTE_DEPLOY_DIR\}/([A-Za-z0-9_.-]+)",
            inspect.getsource(engine._cmd_rollback),
        )
    )
    assert rollback_artifacts == {"rollback-previous-release"}
    for name in sorted(deploy_artifacts | rollback_artifacts):
        assert f"{engine.REMOTE_DEPLOY_DIR}/{name}" in text

    # Reclaim reasons and quarantine naming are derived by exercising the
    # production classifier rather than hardcoded in this test.
    own = engine.deploy_lock_owner_record()
    assert engine.classify_deploy_lock_owner(own, own) == "own-identity"
    abandoned = f"{'a' * 32} {_a_dead_pid()} 1 {socket.gethostname()}"
    assert engine.classify_deploy_lock_owner(abandoned, own) == "abandoned-owner"
    for reason in ("own-identity", "abandoned-owner"):
        assert f"{engine.REMOTE_DEPLOY_DIR}.reclaimed-{reason}" in text
    assert f"{engine.REMOTE_DEPLOY_LOCK_RECLAIM_STALE_SECONDS} seconds" in text

    # The deleted identity-migration command is not an operator invocation.
    assert (
        "The one-time host identity migration has already run and "
        "`migrate-identity` is gone."
    ) in text
    assert "Identity Migration (Non-Routine; Not This Continuation)" not in text
    for entry in ("framenest-release", "kronika-release"):
        assert f"{entry} migrate-identity" not in text


def test_runbook_recovery_is_exact_object_not_recursive() -> None:
    text = _text(RUNBOOK_PATH)
    flattened = " ".join(text.split())
    assert "rm -rf" not in text
    assert "recursive" in text
    assert "wildcard" in text
    assert (
        "Unexpected names, extra files, or a missing expected file stop the "
        "run."
    ) in flattened
    assert "`rmdir` must succeed on an empty directory. If it fails, stop" in text


def test_engine_exit_13_residue_is_the_documented_three_artifacts() -> None:
    """The engine, on a host with a non-empty lock directory, leaves exactly
    the pre-schema-gate residue and its owner record in place.

    The real release path cannot remove a populated lock directory: its
    ``rmdir`` fails, the cleanup error is suppressed while the primary
    ``migration-required`` failure propagates, and the owner-record removal is
    never reached. The runbook recovery names exactly this residue.
    """
    engine = _ENGINE
    remote = _remote_contract

    class _NonEmptyRmdir(remote.FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if f"rmdir {engine.REMOTE_DEPLOY_DIR}" in combined:
                raise engine.ReleaseError("directory not empty", engine.EXIT_TRANSPORT)
            if "kronika-db status" in combined:
                return (
                    '{"operation":"status","state":"behind",'
                    '"current_revision":"0026","head_revision":"0031"}'
                )
            return super()._ssh_respond(combined, input_bytes)

    runner = _NonEmptyRmdir()
    result = engine.main(remote._args("deploy"), runner=runner)

    assert result == engine.EXIT_MIGRATION_REQUIRED
    commands = remote._ssh_combined(runner)
    for name in ("ap.tar", "kronika_release.py", "superproject.tar"):
        assert f"cat > {engine.REMOTE_DEPLOY_DIR}/{name}" in commands
    assert "previous-release" not in commands
    assert runner.locked is True
    assert f"rm -f {engine.REMOTE_DEPLOY_LOCK_OWNER_PATH}" not in commands


def test_engine_post_checkpoint_residue_adds_the_documented_previous_release() -> None:
    """A failure after the checkpoint leaves the fourth documented artifact."""
    engine = _ENGINE
    remote = _remote_contract

    class _PostSwitchFailure(remote.FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self._journal_failures = 0

        def _ssh_respond(self, combined, input_bytes):
            if f"rmdir {engine.REMOTE_DEPLOY_DIR}" in combined:
                raise engine.ReleaseError("directory not empty", engine.EXIT_TRANSPORT)
            if "journalctl -u kronika.service" in combined:
                self._journal_failures += 1
                if self._journal_failures == 1:
                    raise engine.ReleaseError("terminal", engine.EXIT_SERVICE_TERMINAL)
            return super()._ssh_respond(combined, input_bytes)

    runner = _PostSwitchFailure()
    result = engine.main(remote._args("deploy"), runner=runner)

    assert result == engine.EXIT_SERVICE_TERMINAL
    commands = remote._ssh_combined(runner)
    assert f"{engine.REMOTE_DEPLOY_DIR}/previous-release" in commands
    assert runner.locked is True
    # The interrupted cleanup never removes any transferred artifact and never
    # reaches the owner-record removal.
    for name in ("ap.tar", "framenest_release.py", "superproject.tar", "previous-release"):
        assert f"rm -f {engine.REMOTE_DEPLOY_DIR}/{name}" not in commands
    assert f"rm -f {engine.REMOTE_DEPLOY_LOCK_OWNER_PATH}" not in commands


def test_engine_and_entry_point_are_committed_together() -> None:
    assert not FISH_PATH.exists()
    assert not ENGINE_PATH.exists()
    assert CANONICAL_FISH_PATH.exists()
    assert CANONICAL_ENGINE_PATH.exists()


def test_retired_release_entry_points_are_absent() -> None:
    assert not FISH_PATH.exists()
    assert not ENGINE_PATH.exists()
    entry = _text(CANONICAL_FISH_PATH)
    assert "kronika_release.py" in entry
    assert "framenest" not in entry



def test_engine_documents_never_uv_or_migrate() -> None:
    for path in (CANONICAL_ENGINE_PATH,):
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


def test_runbook_documents_the_bounded_identity_label_maintenance_window() -> None:
    text = _text(RUNBOOK_PATH)
    assert "Catalog Identity-Label Maintenance (Non-Routine; Stopped Writers)" in text
    assert "kronika-catalog identity-labels check" in text
    assert "kronika-catalog identity-labels apply --yes" in text
    assert "kronika-catalog identity-labels rollback --yes" in text
    assert "--include-libraries" in text
    assert "catalog.sqlite3.identity-labels-receipt.json" in text
    assert "mode `0600`" in text
    flattened = " ".join(text.split())
    assert "Run every operation as the catalog owner" in flattened
    assert "device list` is not sufficient evidence" in flattened
    assert "Never restore an old whole-catalog backup" in flattened
    assert "no schema revision is applied" in flattened


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
    flattened = " ".join(deploy.split())
    assert "They do not switch the capture pointer and they do not restart the capture bridge or runner." in flattened
    assert "The capture pointer is `/opt/kronika/capture-current`." in flattened
    assert (
        "`activate-capture` switches it to a reviewed capture release and "
        "restarts the bridge, then the runner, once"
    ) in flattened
    assert (
        "`rollback-capture` switches it back to an existing capture release "
        "under those same checks and the same single bridge-then-runner restart."
    ) in flattened
    assert (
        "The one-time capture-path migration has already run and "
        "`migrate-capture-paths` is gone. Routine `status`, `check`, `deploy`, "
        "and `rollback` address only the canonical layout and do not migrate."
    ) in flattened
    assert (
        "The one-time capture-path migration has already run and "
        "`migrate-capture-paths` is gone."
    ) in runbook
    assert "Capture Path Migration (Explicit; Not A Routine Release)" not in runbook


def test_canonical_and_retained_entry_points_are_executable() -> None:
    for path in (CANONICAL_FISH_PATH, REPOSITORY_ROOT / "kronika"):
        assert path.is_file(), path
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR, path
    for path in (
        REPOSITORY_ROOT / "scripts" / "operator" / "network" / "kronika_nuc_worker_gate.fish",
    ):
        assert path.is_file(), path
        assert path.stat().st_mode & stat.S_IXUSR, path


def test_retired_gate_is_absent_and_canonical_gate_has_no_fallback() -> None:
    network = REPOSITORY_ROOT / "scripts" / "operator" / "network"
    assert not (network / "framenest_nuc_worker_gate.fish").exists()
    canonical = _text(network / "kronika_nuc_worker_gate.fish")
    assert "_resolve_env" in canonical
    assert "KRONIKA_" in canonical
    assert "FRAMENEST_" not in canonical



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


def _repository_links(document: Path, text: str) -> set[Path]:
    targets = re.findall(r"\[[^\]\n]+\]\(([^)\s]+)\)", text)
    assert targets, "expected repository links"
    resolved: set[Path] = set()
    for target in targets:
        assert "://" not in target, target
        path = (document.parent / target).resolve()
        assert path.is_relative_to(REPOSITORY_ROOT), target
        assert path.exists(), target
        resolved.add(path)
    return resolved


def test_clean_install_is_discoverable_and_every_new_link_resolves() -> None:
    assert CLEAN_INSTALL_PATH.is_file()
    readme_map = _text(README_PATH).split("## Documentation Map\n", 1)[1].split(
        "\n## ", 1
    )[0]
    for document, text, target in (
        (README_PATH, readme_map, "docs/UBUNTU_NUC_CLEAN_INSTALL.md"),
        (RUNBOOK_PATH, _text(RUNBOOK_PATH), "UBUNTU_NUC_CLEAN_INSTALL.md"),
    ):
        assert len(re.findall(rf"\]\({re.escape(target)}\)", text)) == 1
        assert (document.parent / target).resolve() == CLEAN_INSTALL_PATH
    links = _repository_links(CLEAN_INSTALL_PATH, _text(CLEAN_INSTALL_PATH))
    assert {RUNBOOK_PATH, CANONICAL_ENGINE_PATH, CANONICAL_FISH_PATH} <= links
    # An impossible target must fail through the same resolver, not disappear
    # into an empty successful scan.
    with pytest.raises(AssertionError):
        _repository_links(CLEAN_INSTALL_PATH, "[control](missing-clean-install-sentinel.md)")


def test_clean_install_pins_the_ten_required_areas_and_operator_decisions() -> None:
    text = _text(CLEAN_INSTALL_PATH)
    sections = re.split(r"^## \d+\. ", text, flags=re.MULTILINE)[1:]
    required = (
        ("Purpose and installation boundary", "future installation", "not a receipt",
         "live host retains", "no live reinstall"),
        ("Canonical naming and frozen exceptions", "kronika-nuc", "Kronika NUC"),
        ("Prepare Ubuntu, access, and bootstrap tooling", "Ubuntu Server 24.04 LTS",
         "Wi-Fi", "wired-only", "never invokes `uv`", "Poetry 2.4.1"),
        ("Accounts, directories, and original media", "read-only to the service",
         "quarantine", "publication", "staging", "disjoint"),
        ("Install configuration and units in dependency order", "tailscale_uds",
         "KRONIKA_IDENTITY_MAP", "0600", "public_published_uds` off",
         "Research stays disabled by default"),
        ("First release, routine updates, and schema recovery", "not an empty-host installer",
         "migration-required", "current_revision=head_revision=<H>",
         "never from `current`", "sudo -K"),
        ("Schedule backups and prove restore readiness", "scheduled success is the requirement",
         "timer-triggered", "manual checkpoint", "48 hours", "new absent path"),
        ("Optional parked capture provisioning", "Capture stays parked",
         "separate host task", "no credential provisioning", "Cooperator performs real login"),
        ("Private operator setup notes", "existing SSH identity filename",
         "retired spelling", "canonical SSH identity filename", "exported user-dotfile",
         "single existing source", "do not claim Fish universal-variable storage"),
        ("Recovery cautions and final verification", "Historical operator finding",
         "policy-table default route", "cleared separately", "not an instruction to delete routes",
         "no completed installation"),
    )
    assert len(sections) == len(required) == 10
    for section, markers in zip(sections, required, strict=True):
        flattened = " ".join(section.split())
        assert section.startswith(markers[0])
        for marker in markers[1:]:
            assert marker in flattened, marker
    assert text.count("**Checkpoint:**") == 9


def test_clean_install_retains_only_the_named_frozen_spellings() -> None:
    text = _text(CLEAN_INSTALL_PATH)
    exceptions = (POETRY_PATH, CPYTHON_PATH, str(DEFAULT_OFFDEVICE_ROOT), STATE_DIR_NAME)
    assert PROTOCOL_MAGIC.rstrip(b"\0").decode("ascii") in text
    for exception in exceptions:
        assert text.count(exception) == 1, exception
        text = text.replace(exception, "")
    retired_spelling = Path(POETRY_PATH).parts[2]
    assert retired_spelling not in text.lower()
    assert "no executable alias of that name" in text
    assert "explicit service state directory" in text


def test_clean_install_unit_inventory_matches_every_source_unit() -> None:
    text = _text(CLEAN_INSTALL_PATH)
    links = _repository_links(CLEAN_INSTALL_PATH, text)
    source_directory = REPOSITORY_ROOT / "deploy" / "systemd"
    units = {path for path in source_directory.iterdir()
             if path.suffix in {".service", ".timer"}}
    assert units
    assert {path for path in links if path.suffix in {".service", ".timer"}} == units
    dropins = set(source_directory.glob("*.conf"))
    assert {path for path in links if path.suffix == ".conf"} == dropins
    for unit in units:
        assert unit.name in text
        if unit.suffix == ".timer":
            source = _text(unit)
            schedule = re.search(r"^OnCalendar=\*-\*-\* (.+)$", source, re.MULTILINE)
            assert schedule is not None
            assert f"`{schedule[1]}`" in text
            for setting in ("Persistent=yes", "RandomizedDelaySec=900"):
                assert setting in source and setting in text
    capture = text.split("## 8. Optional parked capture provisioning\n", 1)[1].split(
        "\n## 9.", 1
    )[0]
    ordered = ("xvfb", "bridge", "runner", "vnc", "view")
    positions = [capture.index(f"[kronika-capture-{part}.service]") for part in ordered]
    assert positions == sorted(positions)


def test_clean_install_release_commands_parse_without_running_the_helper() -> None:
    text = _text(CLEAN_INSTALL_PATH)
    commands = re.findall(r"`deploy/ubuntu/kronika-release ([^`\n]+)`", text)
    assert len(commands) == 5
    parsed = [_ENGINE._build_parser().parse_args(shlex.split(
        command.replace("<40-hex-SHA>", "a" * 40)
    )) for command in commands]
    assert {args.command for args in parsed} == {
        "status", "check", "deploy", "rollback", "activate-capture",
    }
    for args in parsed:
        if args.command != "status":
            _ENGINE.validate_release_sha(args.release)
        if args.command in {"deploy", "rollback", "activate-capture"}:
            assert args.yes is True
    with pytest.raises(SystemExit):
        _ENGINE._build_parser().parse_args(["impossible-clean-install-command"])
    assert _ENGINE.RELEASE_SHA_MARKER in text
    assert _ENGINE.RELEASE_MANIFEST_MARKER in text
    assert _ENGINE.CANONICAL_WEB_LAYOUT.env_file in text
    assert _ENGINE.CANONICAL_CAPTURE_CURRENT in text


def test_clean_install_backup_roots_and_console_environment_match_sources() -> None:
    text = _text(CLEAN_INSTALL_PATH)
    for root in (backup_ops.DEFAULT_BACKUP_ROOT, backup_ops.DEFAULT_RESTORE_VERIFY_ROOT,
                 backup_ops.DEFAULT_OPS_ROOT):
        assert f"`{root}`" in text
    assert f"Default `{backup_ops.DEFAULT_KEEP_AUTO}`, minimum `{backup_ops.MIN_KEEP_AUTO}`" in text
    blocks = re.findall(r"^```text\n(.*?)^```", text, re.MULTILINE | re.DOTALL)
    assert len(blocks) == 1
    lines = blocks[0].splitlines()
    assert lines[0] == "# [NUC / bash]"
    assert lines[-1] == "#------------------------------------------------------"
    assert lines[1:-1] == [_ENGINE.cmd_remote_backup_status(_ENGINE.CANONICAL_WEB_LAYOUT.current)]
