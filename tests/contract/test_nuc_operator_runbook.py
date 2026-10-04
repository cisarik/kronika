"""Contract tests for NUC operator documentation and execution hygiene."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from tests.support.tooling import resolve_tool, resolve_tool_optional

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
UBUNTU_DOC_PATH = REPOSITORY_ROOT / "docs" / "UBUNTU_NUC_DEPLOYMENT.md"
README_PATH = REPOSITORY_ROOT / "README.md"
SPEC_PATH = REPOSITORY_ROOT / "SPEC.md"
DEVELOPMENT_DOC_PATH = REPOSITORY_ROOT / "DEVELOPMENT.md"
LAUNCHER_PATH = REPOSITORY_ROOT / "kronika"
DEPLOY_HELPER_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "production_ai_deploy.py"
SERVICE_PATH = REPOSITORY_ROOT / "deploy" / "systemd" / "framenest.service"
ADR_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "0047-operator-cli-configuration-and-working-directory-hygiene.md"
)
ADR_INDEX_PATH = REPOSITORY_ROOT / "docs" / "adr" / "README.md"

ACTIVE_TREE_ROOT = "/opt/framenest/current"
TARGET_RELEASE_ROOT = "/opt/framenest/releases/<T>"
ACTIVE_CHDIR_FLAG = f"--chdir={ACTIVE_TREE_ROOT}"
TARGET_CHDIR_FLAG = f"--chdir={TARGET_RELEASE_ROOT}"
ENV_FILE_FLAG = "FRAMENEST_ENV_FILE=/etc/framenest/framenest.env"


def _ubuntu_text() -> str:
    return UBUNTU_DOC_PATH.read_text(encoding="utf-8")


def _service_account_commands() -> list[str]:
    logical: list[str] = []
    pending = ""
    for raw_line in _ubuntu_text().splitlines():
        line = raw_line.strip()
        if pending:
            pending += " " + line.rstrip("\\").strip()
        elif "sudo -u framenest" in line:
            pending = line.rstrip("\\").strip()
        if pending and not line.endswith("\\"):
            logical.append(pending)
            pending = ""
    return logical


def _classified_service_account_commands() -> tuple[list[str], list[str]]:
    active: list[str] = []
    target: list[str] = []
    for command in _service_account_commands():
        if TARGET_CHDIR_FLAG in command:
            target.append(command)
        else:
            active.append(command)
    return active, target


def test_runbook_service_account_commands_establish_explicit_safe_cwd() -> None:
    active, target = _classified_service_account_commands()

    assert active, "runbook must document active-tree service-account commands"
    assert target, "runbook must document schema-jump target-tree commands"
    for command in active:
        assert ACTIVE_CHDIR_FLAG in command
        assert TARGET_CHDIR_FLAG not in command
    for command in target:
        assert TARGET_CHDIR_FLAG in command
        assert ACTIVE_CHDIR_FLAG not in command


def test_runbook_service_account_commands_use_release_local_entry_points() -> None:
    active, target = _classified_service_account_commands()

    for command in active:
        assert f"{ACTIVE_TREE_ROOT}/.venv/bin/" in command
        assert f"{TARGET_RELEASE_ROOT}/.venv/bin/" not in command
    for command in target:
        assert f"{TARGET_RELEASE_ROOT}/.venv/bin/" in command
        assert f"{ACTIVE_TREE_ROOT}/.venv/bin/" not in command


def test_runbook_service_account_commands_supply_explicit_environment() -> None:
    for command in _service_account_commands():
        assert ENV_FILE_FLAG in command


def test_runbook_contains_no_home_path_or_permission_broadening() -> None:
    text = _ubuntu_text()

    assert "/home/" not in text
    for token in ("chmod", "chown", "usermod", "setfacl", "adduser", "addgroup"):
        assert token not in text
    assert "Capture Runtime Sources (Not Deployed)" in text
    assert "activate-capture" in text
    assert "Agents do not open the view and do not enter credentials." in text


def test_runbook_does_not_present_fish_launcher_as_nuc_interface() -> None:
    text = _ubuntu_text()

    for line in text.splitlines():
        stripped = line.strip()
        assert not stripped.startswith(("./framenest ", "./kronika ")), stripped
        assert stripped not in ("./framenest", "./kronika"), stripped
    flattened = " ".join(text.split())
    assert "CachyOS Fish development tooling" in flattened
    assert "Fish is not a production prerequisite" in flattened


def test_runbook_documents_youtube_release_local_entry_point() -> None:
    text = _ubuntu_text()

    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-youtube ingest URL --yes" in text
    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-youtube status CLAIM_ID" in text
    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-youtube retry CLAIM_ID --yes" in text


def test_runbook_documents_previews_release_local_entry_point() -> None:
    text = _ubuntu_text()

    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-previews" in text
    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-previews status" in text
    assert (
        f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-previews generate --all --yes"
        in text
    )


def test_runbook_documents_migration_command_under_operator_contract() -> None:
    text = _ubuntu_text()

    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-db migrate" not in text
    assert f"{TARGET_RELEASE_ROOT}/.venv/bin/framenest-db migrate" in text
    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-db status" in text
    assert f"{TARGET_RELEASE_ROOT}/.venv/bin/framenest-db status" in text
    assert f"{ACTIVE_TREE_ROOT}/.venv/bin/framenest-ai" in text
    assert "--config-path /var/lib/framenest/ai/config.json status --no-write" in text


def test_runbook_schema_jump_continuation_uses_target_release_tree() -> None:
    text = _ubuntu_text()
    active, target = _classified_service_account_commands()

    assert "exits exactly 13" in text
    assert "migration-required" in text
    assert "current_revision=0032" in text
    assert "head_revision=0033" in text
    assert "current_revision=head_revision=0033" in text
    assert "/run/framenest-release-deploy/ap.tar" in text
    assert "/run/framenest-release-deploy/framenest_release.py" in text
    assert "/run/framenest-release-deploy/superproject.tar" in text
    assert "rollback --release <T> --yes" in text
    assert "sudo -K" in text
    flattened = " ".join(text.split())
    assert "Never improvise a downgrade or catalog restore" in flattened
    assert any("framenest-db migrate" in command for command in target)
    assert any("framenest-db status" in command for command in target)
    assert not any("framenest-db migrate" in command for command in active)


def test_readme_youtube_section_points_nuc_to_release_entry_point() -> None:
    text = README_PATH.read_text(encoding="utf-8")
    youtube_section = text.split("## YouTube Operator Ingestion", 1)[1]

    assert "framenest-youtube" in youtube_section
    assert "CachyOS development-launcher forms" in youtube_section
    assert "does not require Fish" in youtube_section
    assert "docs/UBUNTU_NUC_DEPLOYMENT.md" in youtube_section


def test_readme_and_spec_record_explicit_env_file_authority() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    spec = SPEC_PATH.read_text(encoding="utf-8")
    development = DEVELOPMENT_DOC_PATH.read_text(encoding="utf-8")

    assert "FRAMENEST_ENV_FILE" in readme
    assert "never read a `.env` file from the\ncaller's working directory" in readme
    assert "MUST NOT implicitly probe the caller's working directory" in spec
    assert "FRAMENEST_ENV_FILE" in spec
    assert "FRAMENEST_ENV_FILE" in development


def test_systemd_unit_keeps_working_directory_and_hardening_contract() -> None:
    text = SERVICE_PATH.read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/framenest/current" in text
    assert "EnvironmentFile=/etc/framenest/framenest.env" in text
    assert "ProtectHome=read-only" in text
    assert "ProtectSystem=strict" in text
    assert "NoNewPrivileges=true" in text


def test_deploy_helper_privilege_switch_establishes_explicit_cwd() -> None:
    text = DEPLOY_HELPER_PATH.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sudo -n -u framenest" in line]

    assert lines, "deploy helper service-account command must remain tracked"
    for line in lines:
        assert "--chdir=" in line


def test_deploy_helper_privilege_switch_uses_no_unquoted_interpolation() -> None:
    text = DEPLOY_HELPER_PATH.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if "sudo -n -u framenest" in line]

    for line in lines:
        assert "{quoted_provider}" in line
        assert "{quoted_model}" in line
        assert "{provider_id}" not in line
        assert "{model_id}" not in line


def test_adr_records_operator_hygiene_decision() -> None:
    text = ADR_PATH.read_text(encoding="utf-8")
    index = ADR_INDEX_PATH.read_text(encoding="utf-8")

    assert "## Status\n\n`Accepted`" in text
    assert "FRAMENEST_ENV_FILE" in text
    assert "--chdir=/opt/framenest/current" in text
    assert "broadening access to a\nuser home directory" in text
    assert (
        "0047 | Operator CLI Configuration and Working-Directory Hygiene"
        in index
    )


def test_fish_launcher_remains_development_only_and_repo_rooted() -> None:
    text = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert text.startswith("#!/usr/bin/env fish")
    assert "_kronika_use_repo_env_file" in text
    assert 'set -g local_dotenv "$repo_root/.env"' in text
    # The launcher never overrides an operator-supplied explicit env file.
    assert "if set -q FRAMENEST_ENV_FILE" in text
    assert 'set -gx FRAMENEST_ENV_FILE "$local_dotenv"' in text
    # The repository-root .env must be a regular non-symlink file.
    assert 'test -L "$local_dotenv"' in text
    assert "./kronika youtube ingest URL" in text


@pytest.mark.skipif(resolve_tool_optional("fish") is None, reason="fish is not installed")
def test_fish_launcher_parses_on_its_development_host() -> None:
    result = subprocess.run(
        [resolve_tool("fish"), "--no-execute", str(LAUNCHER_PATH)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
