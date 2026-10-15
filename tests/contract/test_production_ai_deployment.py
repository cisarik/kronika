"""Contract tests for production AI credential deployment support."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import stat
import subprocess
import sys
from typing import Any

import pytest
from tests.support.tooling import resolve_tool

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
FISH_ENTRYPOINT = REPOSITORY_ROOT / "deploy" / "ubuntu" / "fn-production-env-deploy"
HELPER_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "production_ai_deploy.py"
SYNTHETIC_SECRET = "synthetic-provider-credential"

_SPEC = importlib.util.spec_from_file_location("production_ai_deploy", HELPER_PATH)
assert _SPEC is not None
assert _SPEC.loader is not None
production_ai_deploy = importlib.util.module_from_spec(_SPEC)
sys.modules["production_ai_deploy"] = production_ai_deploy
_SPEC.loader.exec_module(production_ai_deploy)


class _Runner:
    def __init__(
        self,
        *,
        fail_on: str | None = None,
        fail_on_occurrence: int = 1,
        fail_message: str = "simulated failure",
        fail_plan: list[str] | None = None,
        readiness_plan: list[str] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.fail_on = fail_on
        self.fail_on_occurrence = fail_on_occurrence
        self.fail_message = fail_message
        self.fail_plan = [] if fail_plan is None else list(fail_plan)
        self.readiness_plan = [] if readiness_plan is None else list(readiness_plan)
        self._matches = 0

    def __call__(self, argv: list[str], input_bytes: bytes | None = None) -> str:
        self.calls.append((argv, input_bytes))
        combined = " ".join(argv)
        if "Kronika readiness probe" in combined and self.readiness_plan:
            outcome = self.readiness_plan.pop(0)
            if outcome == "terminal":
                raise production_ai_deploy.DeploymentCommandError(
                    "service entered terminal failed state"
                )
            if outcome == "timeout":
                raise production_ai_deploy.DeploymentCommandError(
                    "service readiness deadline exceeded"
                )
            if outcome == "transient":
                raise AssertionError("transient readiness must be handled inside the remote loop")
            return ""
        if self.fail_plan and self.fail_plan[0] in combined:
            self.fail_plan.pop(0)
            raise production_ai_deploy.DeploymentCommandError("simulated failure")
        if self.fail_on is not None and self.fail_on in combined:
            self._matches += 1
            if self._matches == self.fail_on_occurrence:
                if self.fail_on == "mkdir -m 0700 /run/kronika-ai-credential-deploy":
                    raise production_ai_deploy.DeploymentCommandError(
                        "retained recovery material exists; independent recovery is required"
                    )
                raise production_ai_deploy.DeploymentCommandError(self.fail_message)
        return ""


def _credential_file(tmp_path: Path) -> Path:
    path = tmp_path / "credential.txt"
    path.write_text(SYNTHETIC_SECRET + "\n", encoding="utf-8")
    path.chmod(0o600)
    return path


def _base_args(tmp_path: Path) -> list[str]:
    return [
        "--target",
        "kronika-nuc",
        "--expected-hostname",
        "kronika-nuc",
        "--provider",
        "vercel-ai-gateway",
        "--model",
        "google/gemini-3.1-flash-lite",
        "--credential-file",
        str(_credential_file(tmp_path)),
    ]


def _combined(runner: _Runner) -> str:
    return "\n".join(" ".join(argv) for argv, _input in runner.calls)


def _call_index(runner: _Runner, needle: str) -> int:
    for index, (argv, _input) in enumerate(runner.calls):
        if needle in " ".join(argv):
            return index
    raise AssertionError(f"missing command containing {needle!r}")


def _template_bytes(provider_id: str) -> bytes:
    return (
        REPOSITORY_ROOT
        / "deploy"
        / "systemd"
        / f"kronika-ai-credential-{provider_id}.conf"
    ).read_bytes()


def _stdin_payloads(runner: _Runner) -> list[bytes]:
    return [input_bytes for _argv, input_bytes in runner.calls if input_bytes is not None]


def test_fish_entrypoint_has_valid_syntax() -> None:
    first_line = FISH_ENTRYPOINT.read_text(encoding="utf-8").splitlines()[0]

    assert first_line == "#!/usr/bin/env fish"
    subprocess.run([resolve_tool("fish"), "--no-execute", str(FISH_ENTRYPOINT)], check=True)


def test_fish_entrypoint_avoids_shell_tracing_and_bash_secret_patterns() -> None:
    text = FISH_ENTRYPOINT.read_text(encoding="utf-8")

    assert "set -x" not in text
    assert "sudo -S" not in text
    assert "NOPASSWD" not in text
    assert "eval" not in text
    assert "[[" not in text


def test_check_mode_validates_inputs_and_does_not_mutate(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner()

    result = production_ai_deploy.main([*_base_args(tmp_path), "--check"], runner=runner)

    output = capsys.readouterr().out
    assert result == 0
    assert runner.calls == []
    assert "Provider: vercel-ai-gateway" in output
    assert "Model: google/gemini-3.1-flash-lite" in output
    assert "Credential identity: AI_GATEWAY_API_KEY" in output
    assert SYNTHETIC_SECRET not in output


def test_check_mode_validates_selected_template_before_remote_activity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    missing_template = tmp_path / "missing.conf"
    monkeypatch.setitem(
        production_ai_deploy.PROVIDER_DROPIN_TEMPLATES,
        "vercel-ai-gateway",
        missing_template,
    )
    runner = _Runner()

    result = production_ai_deploy.main([*_base_args(tmp_path), "--check"], runner=runner)

    assert result == 1
    assert runner.calls == []


def test_exact_provider_to_credential_mapping(tmp_path: Path, capsys: Any) -> None:
    credential = _credential_file(tmp_path)

    assert (
        production_ai_deploy.main(
            [
                "--target",
                "kronika-nuc",
                "--expected-hostname",
                "kronika-nuc",
                "--provider",
                "nvidia-nim",
                "--model",
                "nvidia/default",
                "--credential-file",
                str(credential),
                "--check",
            ],
            runner=_Runner(),
        )
        == 0
    )

    assert "Credential identity: NVIDIA_API_KEY" in capsys.readouterr().out


def test_supported_provider_credential_and_template_maps_match_exactly() -> None:
    assert production_ai_deploy.PROVIDER_CREDENTIALS == {
        "nvidia-nim": "NVIDIA_API_KEY",
        "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
        "opencode-go": "OPENCODE_API_KEY",
    }
    assert set(production_ai_deploy.PROVIDER_DROPIN_TEMPLATES) == set(
        production_ai_deploy.PROVIDER_CREDENTIALS
    )


def test_opencode_go_credential_template_is_exact_two_line_contract() -> None:
    template = production_ai_deploy._load_provider_dropin_template("opencode-go")

    assert template.identity == "OPENCODE_API_KEY"
    assert template.payload == (
        b"[Service]\n"
        b"LoadCredential=OPENCODE_API_KEY:/etc/kronika/credentials/OPENCODE_API_KEY\n"
    )


def test_check_mode_reports_opencode_go_credential_identity(tmp_path: Path, capsys: Any) -> None:
    result = production_ai_deploy.main(
        [
            "--target",
            "kronika-nuc",
            "--expected-hostname",
            "kronika-nuc",
            "--provider",
            "opencode-go",
            "--model",
            "deepseek-v4-flash-vision-exp",
            "--credential-file",
            str(_credential_file(tmp_path)),
            "--check",
        ],
        runner=_Runner(),
    )

    output = capsys.readouterr().out
    assert result == 0
    assert "Provider: opencode-go" in output
    assert "Credential identity: OPENCODE_API_KEY" in output
    assert SYNTHETIC_SECRET not in output


@pytest.mark.parametrize(
    ("provider_id", "identity"),
    [
        ("nvidia-nim", "NVIDIA_API_KEY"),
        ("vercel-ai-gateway", "AI_GATEWAY_API_KEY"),
        ("opencode-go", "OPENCODE_API_KEY"),
    ],
)
def test_provider_template_mapping_loads_exact_tracked_bytes(
    provider_id: str,
    identity: str,
) -> None:
    template = production_ai_deploy._load_provider_dropin_template(provider_id)

    assert template.identity == identity
    assert template.payload == _template_bytes(provider_id)
    assert template.payload.endswith(b"\n")
    assert b"\\n" not in template.payload


def test_template_resolution_is_independent_of_current_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    template = production_ai_deploy._load_provider_dropin_template("nvidia-nim")

    assert template.payload == _template_bytes("nvidia-nim")


def test_unsupported_provider_cannot_select_a_template() -> None:
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._load_provider_dropin_template("untrusted-provider")


@pytest.mark.parametrize(
    ("payload", "reason"),
    [
        (b"[Service]\\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY\\n", "literal"),
        (b"[Service]\r\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY\r\n", "crlf"),
        (b"[Service]\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY", "final"),
        (b"[Service]\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY\n\n", "blank"),
        (b"[Service]\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY\n[Install]\n", "section"),
        (b"[Service]\nEnvironment=AI_GATEWAY_API_KEY=secret\n", "secret"),
        (b"[Service]\nLoadCredential=UNKNOWN:/etc/kronika/credentials/UNKNOWN\n", "unknown"),
        (b"[Service]\nLoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY\x00\n", "nul"),
    ],
)
def test_template_validation_rejects_malformed_or_secret_bearing_payloads(
    payload: bytes,
    reason: str,
) -> None:
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._validate_dropin_template_payload(
            payload=payload,
            identity="AI_GATEWAY_API_KEY",
        )


def test_template_validation_rejects_invalid_utf8() -> None:
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._validate_dropin_template_payload(
            payload=b"\xff\n",
            identity="AI_GATEWAY_API_KEY",
        )


def test_template_validation_rejects_non_regular_symlink_and_outside_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = tmp_path / "directory.conf"
    directory.mkdir()
    monkeypatch.setitem(
        production_ai_deploy.PROVIDER_DROPIN_TEMPLATES,
        "vercel-ai-gateway",
        directory,
    )
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._load_provider_dropin_template("vercel-ai-gateway")

    outside = tmp_path / "outside.conf"
    outside.write_bytes(_template_bytes("vercel-ai-gateway"))
    monkeypatch.setitem(
        production_ai_deploy.PROVIDER_DROPIN_TEMPLATES,
        "vercel-ai-gateway",
        outside,
    )
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._load_provider_dropin_template("vercel-ai-gateway")

    target = tmp_path / "target.conf"
    target.write_bytes(_template_bytes("vercel-ai-gateway"))
    symlink = tmp_path / "linked.conf"
    symlink.symlink_to(target)
    monkeypatch.setitem(
        production_ai_deploy.PROVIDER_DROPIN_TEMPLATES,
        "vercel-ai-gateway",
        symlink,
    )
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy._load_provider_dropin_template("vercel-ai-gateway")


def test_model_punctuation_is_quoted_and_shell_metacharacters_are_rejected(
    tmp_path: Path,
) -> None:
    runner = _Runner()

    assert (
        production_ai_deploy.main(
            [
                "--target",
                "kronika-nuc",
                "--expected-hostname",
                "kronika-nuc",
                "--provider",
                "vercel-ai-gateway",
                "--model",
                "vendor/model.name:1",
                "--credential-file",
                str(_credential_file(tmp_path)),
            ],
            runner=runner,
        )
        == 0
    )
    combined = "\n".join(" ".join(argv) for argv, _input in runner.calls)
    assert "--model-id vendor/model.name:1" in combined

    blocked = production_ai_deploy.main(
        [
            "--target",
            "kronika-nuc",
            "--expected-hostname",
            "kronika-nuc",
            "--provider",
            "vercel-ai-gateway",
            "--model",
            "vendor/model;touch",
            "--credential-file",
            str(_credential_file(tmp_path)),
        ],
        runner=_Runner(),
    )
    assert blocked == 1


def test_deploy_never_places_secret_in_ssh_argv_or_output(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner()

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    captured = capsys.readouterr()
    assert result == 0
    assert runner.calls
    for argv, _input in runner.calls:
        command_text = " ".join(argv)
        assert argv[:4] == ["ssh", "-o", "BatchMode=yes", "--"]
        assert SYNTHETIC_SECRET not in command_text
    assert SYNTHETIC_SECRET.encode("utf-8") in _stdin_payloads(runner)
    assert SYNTHETIC_SECRET not in captured.out + captured.err


def test_secret_and_template_use_separate_exact_stdin_payloads(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    payloads = _stdin_payloads(runner)
    template = _template_bytes("vercel-ai-gateway")
    secret = SYNTHETIC_SECRET.encode("utf-8")
    assert payloads.count(secret) == 1
    assert payloads.count(template) == 1
    assert secret not in template
    for argv, input_bytes in runner.calls:
        command_text = " ".join(argv)
        if input_bytes == secret:
            assert template.decode("utf-8") not in command_text
        if input_bytes == template:
            assert SYNTHETIC_SECRET not in command_text
            assert b"\\n" not in input_bytes


def test_remote_command_text_contains_neither_secret_nor_reconstructed_template(
    tmp_path: Path,
) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    combined = _combined(runner)
    template_text = _template_bytes("vercel-ai-gateway").decode("utf-8")
    assert SYNTHETIC_SECRET not in combined
    assert template_text not in combined
    assert "LoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY" not in combined
    assert "printf '%s' '[Service]\\\\n" not in combined
    assert "printf %b" not in combined
    assert "echo -e" not in combined


def test_deploy_uses_sudo_n_only_and_no_remote_access_mutation_terms(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    combined = "\n".join(" ".join(argv) for argv, _input in runner.calls)
    assert "sudo -n" in combined
    assert "sudo -S" not in combined
    assert "NOPASSWD" not in combined
    for forbidden in ["ufw", "firewall", "tailscale", "0.0.0.0", "proxy_pass"]:
        assert forbidden not in combined.lower()


def test_deploy_sequences_atomic_installation_and_cleanup(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    combined = _combined(runner)
    assert "install -d -o root -g root -m 0700 /etc/kronika/credentials" in combined
    assert "mv /etc/kronika/credentials/AI_GATEWAY_API_KEY.next" in combined
    assert "cat > /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next" in combined
    assert (
        combined.index("cat > /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next")
        < combined.index("mv /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next")
    )
    assert "mv /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next" in combined
    assert "sha256sum /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next" in combined
    assert "sha256sum /etc/systemd/system/kronika.service.d/20-ai-credential.conf" in combined
    assert "rm -f /etc/kronika/credentials/AI_GATEWAY_API_KEY.next" in combined
    assert "rm -f /etc/kronika/credentials/AI_GATEWAY_API_KEY.next /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next" in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


def test_systemd_acceptance_occurs_before_single_restart(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    verify_index = _call_index(runner, "systemd-analyze verify")
    daemon_index = _call_index(runner, "systemctl daemon-reload")
    enabled_index = _call_index(runner, "systemctl is-enabled kronika.service")
    dropin_index = _call_index(runner, "DropInPaths")
    credential_index = _call_index(runner, "Verify LoadCredential mapping without redacted systemctl show")
    restart_index = _call_index(runner, "systemctl restart kronika.service")
    assert verify_index < daemon_index < enabled_index < dropin_index < credential_index < restart_index
    assert _combined(runner).count("systemctl restart kronika.service") == 1


def test_successful_deployment_validates_running_capability_before_cleanup(
    tmp_path: Path,
) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    readiness_index = _call_index(runner, "Kronika readiness probe")
    capability_index = _call_index(runner, "/api/ai/media-suggestion-capability")
    cleanup_index = _call_index(runner, "rm -rf /run/kronika-ai-credential-deploy")
    capability_command = " ".join(runner.calls[capability_index][0])
    assert readiness_index < capability_index < cleanup_index
    assert "vercel-ai-gateway" in capability_command
    assert "google/gemini-3.1-flash-lite" in capability_command
    assert "credential_available" in capability_command
    assert "configured_unverified" not in capability_command
    assert "last_connection_test') is not None" not in capability_command
    assert "kronika-ai test" not in capability_command
    assert "integrate.api.nvidia.com" not in capability_command
    assert "ai-gateway.vercel.sh" not in capability_command


def test_identity_failure_performs_no_rollback_command(tmp_path: Path) -> None:
    runner = _Runner(fail_on='test "$(hostname)"')

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    assert result == 1
    assert "restore deployment-controlled files" not in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_backup_failure_performs_no_production_state_rollback(tmp_path: Path) -> None:
    runner = _Runner(fail_on="transaction.complete")

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    assert result == 1
    assert "restore deployment-controlled files" not in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_backup_records_credential_dropin_config_and_complete_marker() -> None:
    command = production_ai_deploy._remote_backup("AI_GATEWAY_API_KEY")

    assert "rm -rf /run/kronika-ai-credential-deploy" not in command
    assert "mkdir -m 0700 /run/kronika-ai-credential-deploy" in command
    assert command.index("/run/kronika-ai-credential-deploy/config.json.absent") < command.index(
        "/run/kronika-ai-credential-deploy/transaction.complete"
    )
    assert "/run/kronika-ai-credential-deploy/AI_GATEWAY_API_KEY.present" in command
    assert "/run/kronika-ai-credential-deploy/AI_GATEWAY_API_KEY.absent" in command
    assert "/run/kronika-ai-credential-deploy/20-ai-credential.conf.present" in command
    assert "/run/kronika-ai-credential-deploy/20-ai-credential.conf.absent" in command
    assert "/run/kronika-ai-credential-deploy/config.json.present" in command
    assert "/run/kronika-ai-credential-deploy/config.json.absent" in command
    assert "/run/kronika-ai-credential-deploy/transaction.complete" in command
    assert "/var/lib/kronika/ai/config.json" in command


def test_service_account_configure_establishes_explicit_release_cwd() -> None:
    command = production_ai_deploy._remote_configure_ai(
        provider_id="vercel-ai-gateway",
        model_id="google/gemini-3.1-flash-lite",
    )

    assert "sudo -n -u kronika --chdir=/opt/kronika/current" in command
    assert "/opt/kronika/current/.venv/bin/kronika-ai" in command
    assert "--config-path /var/lib/kronika/ai/config.json" in command


def test_service_account_configure_quotes_operator_values() -> None:
    command = production_ai_deploy._remote_configure_ai(
        provider_id="vercel-ai-gateway",
        model_id="model with spaces",
    )

    assert "'model with spaces'" in command
    assert "--chdir=/opt/kronika/current" in command


def test_pre_existing_recovery_fails_closed_before_mutation(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runner = _Runner(fail_on="mkdir -m 0700 /run/kronika-ai-credential-deploy")

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "retained recovery material exists; independent recovery is required" in captured.err
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined
    assert "cat > /etc/kronika/credentials/AI_GATEWAY_API_KEY.next" not in combined
    assert "kronika-ai --config-path" not in combined
    assert "systemctl restart kronika.service" not in combined
    assert "Kronika readiness probe" not in combined
    assert "restore deployment-controlled files" not in combined
    assert all(input_bytes is None for _argv, input_bytes in runner.calls)


def test_atomic_transaction_acquisition_allows_only_one_winner() -> None:
    command = production_ai_deploy._remote_backup("AI_GATEWAY_API_KEY")

    assert "test -e /run/kronika-ai-credential-deploy" not in command
    assert command.count("mkdir -m 0700 /run/kronika-ai-credential-deploy") == 1
    assert "|| exit" in command
    assert "rm -rf /run/kronika-ai-credential-deploy" not in command


def test_credential_is_transmitted_only_after_complete_backup(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    backup_index = _call_index(runner, "transaction.complete")
    credential_index = next(
        index
        for index, (_argv, input_bytes) in enumerate(runner.calls)
        if input_bytes == SYNTHETIC_SECRET.encode("utf-8")
    )
    assert backup_index < credential_index


@pytest.mark.parametrize("failure", ["systemctl restart", "service entered terminal failed state"])
def test_deploy_rolls_back_on_restart_or_health_failure(
    tmp_path: Path,
    failure: str,
) -> None:
    runner = (
        _Runner(readiness_plan=["terminal"])
        if failure == "service entered terminal failed state"
        else _Runner(fail_on=failure)
    )

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    assert result == 1
    assert "restore deployment-controlled files" in combined
    assert "/run/kronika-ai-credential-deploy/AI_GATEWAY_API_KEY.present" in combined
    assert "/run/kronika-ai-credential-deploy/AI_GATEWAY_API_KEY.absent" in combined
    assert "rm -f /etc/kronika/credentials/AI_GATEWAY_API_KEY" in combined
    assert "/run/kronika-ai-credential-deploy/20-ai-credential.conf.present" in combined
    assert "/run/kronika-ai-credential-deploy/20-ai-credential.conf.absent" in combined
    assert "rm -f /etc/systemd/system/kronika.service.d/20-ai-credential.conf" in combined
    assert "/run/kronika-ai-credential-deploy/config.json.present" in combined
    assert "/run/kronika-ai-credential-deploy/config.json.absent" in combined
    assert "rm -f /var/lib/kronika/ai/config.json" in combined
    assert "systemctl daemon-reload" in combined
    assert "systemctl restart kronika.service" in combined
    assert "Kronika readiness probe" in combined
    assert "rm -f /etc/kronika/credentials/AI_GATEWAY_API_KEY.next /etc/systemd/system/kronika.service.d/20-ai-credential.conf.next" in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("drop-in byte equivalence failed", "drop-in byte equivalence failed"),
        ("systemd-analyze verify", "systemd drop-in verification failed"),
        ("systemctl daemon-reload", "daemon reload failed"),
        ("systemctl is-enabled kronika.service", "service enabled-state validation failed"),
        ("DropInPaths", "loaded drop-in mismatch"),
        ("Verify LoadCredential mapping without redacted systemctl show", "loaded credential identity mismatch"),
        ("/api/ai/media-suggestion-capability", "running capability validation failed"),
    ],
)
def test_deploy_rolls_back_when_acceptance_or_capability_gates_fail(
    tmp_path: Path,
    failure: str,
    message: str,
    capsys: Any,
) -> None:
    runner = _Runner(fail_on=failure, fail_message=message)

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert message in captured.err
    assert "restore deployment-controlled files" in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


@pytest.mark.parametrize(
    "message",
    [
        "running capability validation failed",
        "running capability unavailable",
        "running capability malformed",
    ],
)
def test_capability_failure_prevents_cleanup_until_rollback_succeeds(
    tmp_path: Path,
    message: str,
) -> None:
    runner = _Runner(fail_on="/api/ai/media-suggestion-capability", fail_message=message)

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 1

    capability_index = _call_index(runner, "/api/ai/media-suggestion-capability")
    rollback_index = _call_index(runner, "restore deployment-controlled files")
    cleanup_index = _call_index(runner, "rm -rf /run/kronika-ai-credential-deploy")
    assert capability_index < rollback_index < cleanup_index


def test_deploy_readiness_timeout_is_distinct_and_rolls_back(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runner = _Runner(readiness_plan=["timeout"])

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "service readiness deadline exceeded" in captured.err
    assert "restore deployment-controlled files" in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


def test_readiness_command_has_bounded_polling_and_strict_health_validation() -> None:
    command = production_ai_deploy._remote_wait_ready("http://127.0.0.1:8000/health")

    assert "Kronika readiness probe" in command
    assert "READINESS_DEADLINE_SECONDS=30" in command
    assert "READINESS_POLL_INTERVAL_SECONDS=1" in command
    assert "curl --silent --max-time" in command
    assert "sleep \"$sleep_for\"" in command
    assert '\'{"status":"ok"}\'' in command
    assert "curl --fail --silent --show-error --max-time 5" not in command


def test_rollback_restore_failure_preserves_recovery_directory(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner(
        fail_plan=[
            "systemctl restart kronika.service",
            "restore deployment-controlled files",
        ]
    )

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "rollback failed" in captured.err
    assert "restore" in captured.err
    assert "/run/kronika-ai-credential-deploy" in captured.err
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_rollback_restart_failure_preserves_recovery_directory(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner(
        fail_plan=[
            "systemctl restart kronika.service",
            "systemctl restart kronika.service",
        ]
    )

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "rollback failed" in captured.err
    assert "restart" in captured.err
    assert "/run/kronika-ai-credential-deploy" in captured.err
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_rollback_health_failure_preserves_recovery_directory(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner(readiness_plan=["terminal", "terminal"])

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "rollback failed during terminal service state" in captured.err
    assert "/run/kronika-ai-credential-deploy" in captured.err
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_rollback_readiness_timeout_preserves_recovery_directory(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runner = _Runner(readiness_plan=["terminal", "timeout"])

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "rollback readiness deadline exceeded" in captured.err
    assert "/run/kronika-ai-credential-deploy" in captured.err
    assert "rm -rf /run/kronika-ai-credential-deploy" not in combined


def test_rollback_transient_readiness_success_cleans_recovery_directory(tmp_path: Path) -> None:
    runner = _Runner(readiness_plan=["terminal", "ready"])

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    assert result == 1
    assert combined.count("Kronika readiness probe") == 2
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


def test_successful_deployment_cleanup_failure_reports_sanitized_failure(
    tmp_path: Path,
    capsys: Any,
) -> None:
    runner = _Runner(fail_on="rm -rf /run/kronika-ai-credential-deploy")

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    captured = capsys.readouterr()
    assert result == 1
    assert "cleanup failed" in captured.err
    assert "/run/kronika-ai-credential-deploy" in captured.err


def test_successful_rollback_cleans_recovery_directory(tmp_path: Path) -> None:
    runner = _Runner(readiness_plan=["terminal", "ready"])

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    assert result == 1
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined


def test_readiness_polling_does_not_restart_service(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    combined = _combined(runner)
    assert combined.count("systemctl restart kronika.service") == 1
    assert "systemctl restart kronika.service\nsleep" not in combined


def test_runner_errors_expose_only_sanitized_categories(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner(fail_on="kronika-ai --config-path")

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    captured = capsys.readouterr()
    assert result == 1
    assert "simulated failure" not in captured.err
    assert "remote command failed" in captured.err
    assert SYNTHETIC_SECRET not in captured.err


def test_local_ai_env_source_validation_and_extraction(tmp_path: Path) -> None:
    env_path = tmp_path / "ai.env.fish"
    env_path.write_text(
        "\n".join(
            [
                "set -gx NVIDIA_API_KEY unrelated-sentinel",
                f"set -gx AI_GATEWAY_API_KEY {SYNTHETIC_SECRET}",
                f"set -gx OPENCODE_API_KEY {SYNTHETIC_SECRET}",
                "",
            ]
        ),
        encoding="utf-8",
    )
    env_path.chmod(0o600)

    secret = production_ai_deploy.load_local_secret(
        provider_id="vercel-ai-gateway",
        credential_file=None,
        local_ai_env=env_path,
    )

    assert secret.value == SYNTHETIC_SECRET
    assert secret.identity == "AI_GATEWAY_API_KEY"

    opencode_secret = production_ai_deploy.load_local_secret(
        provider_id="opencode-go",
        credential_file=None,
        local_ai_env=env_path,
    )

    assert opencode_secret.value == SYNTHETIC_SECRET
    assert opencode_secret.identity == "OPENCODE_API_KEY"


def test_local_ai_env_rejects_symlink_and_insecure_permissions(tmp_path: Path) -> None:
    target = tmp_path / "target.env.fish"
    target.write_text(f"set -gx AI_GATEWAY_API_KEY {SYNTHETIC_SECRET}\n", encoding="utf-8")
    target.chmod(0o600)
    link = tmp_path / "ai.env.fish"
    link.symlink_to(target)

    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy.load_local_secret(
            provider_id="vercel-ai-gateway",
            credential_file=None,
            local_ai_env=link,
        )

    insecure = tmp_path / "insecure.env.fish"
    insecure.write_text(f"set -gx AI_GATEWAY_API_KEY {SYNTHETIC_SECRET}\n", encoding="utf-8")
    insecure.chmod(0o644)
    if stat.S_IMODE(insecure.stat().st_mode) != 0o644:
        pytest.skip("platform did not preserve test file permissions")
    with pytest.raises(production_ai_deploy.DeploymentInputError):
        production_ai_deploy.load_local_secret(
            provider_id="vercel-ai-gateway",
            credential_file=None,
            local_ai_env=insecure,
        )


def test_no_provider_or_nuc_network_call_occurs_in_contract_tests(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    assert shutil.which("ssh") is not None or runner.calls
    combined = "\n".join(" ".join(argv) for argv, _input in runner.calls)
    assert "api.nvidia.com" not in combined
    assert "api.vercel.com" not in combined
    assert "integrate.api.nvidia.com" not in combined
    assert "ai-gateway.vercel.sh" not in combined
    assert "kronika-ai test" not in combined


def test_loaded_credential_verification_tolerates_redacted_systemctl_show() -> None:
    dropin = production_ai_deploy._load_provider_dropin_template("vercel-ai-gateway")
    commands = production_ai_deploy._remote_systemd_acceptance_commands(dropin)
    combined = "\n".join(commands)

    assert "systemctl show --property=LoadCredential" not in combined
    assert "Verify LoadCredential mapping without redacted systemctl show" in combined
    assert "systemctl cat" in combined
    assert "cmp -s" in combined
    assert "DropInPaths" in combined
    assert 'expected_line="LoadCredential=${identity}:${cred_dir}/${identity}"' in combined
    assert "LoadCredential=AI_GATEWAY_API_KEY:/etc/kronika/credentials/AI_GATEWAY_API_KEY" not in combined
    assert "match_count=" in combined
    assert "other_count=" in combined


def test_loaded_credential_verification_requires_exact_identity_and_path() -> None:
    dropin = production_ai_deploy._load_provider_dropin_template("nvidia-nim")
    commands = production_ai_deploy._remote_systemd_acceptance_commands(dropin)
    combined = "\n".join(commands)

    assert 'identity="NVIDIA_API_KEY"' in combined or "identity=NVIDIA_API_KEY" in combined
    assert 'cred_dir="/etc/kronika/credentials"' in combined or "cred_dir=/etc/kronika/credentials" in combined
    assert f'expected_sha="{dropin.sha256}"' in combined or f"expected_sha={dropin.sha256}" in combined
    assert "test \"$match_count\" = 1" in combined
    assert "test \"$other_count\" = 0" in combined


def test_loaded_credential_verification_fails_closed_on_missing_or_extra_mapping() -> None:
    dropin = production_ai_deploy._load_provider_dropin_template("vercel-ai-gateway")
    commands = production_ai_deploy._remote_systemd_acceptance_commands(dropin)
    combined = "\n".join(commands)

    assert f"|| exit {production_ai_deploy.LOADED_CREDENTIAL_EXIT}" in combined
    assert f"|| exit {production_ai_deploy.LOADED_DROPIN_EXIT}" in combined
    assert 'grep -Fq -- "$dropin_path"' in combined
    assert "grep -Fxc -- \"$expected_line\"" in combined
    assert "grep -E '^[[:space:]]*LoadCredential='" in combined


def test_capability_gate_ignores_historical_connection_test_and_requires_credential_available() -> None:
    command = production_ai_deploy._remote_validate_capability(
        provider_id="nvidia-nim",
        model_id="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning",
    )

    assert "credential_available" in command
    assert "Historical connection-test state is ignored" in command
    assert "configured_unverified" not in command
    assert "last_connection_test') is not None" not in command
    assert "payload.get('last_connection_test') is not None" not in command
    assert "kronika-ai test" not in command
    assert "integrate.api.nvidia.com" not in command


def test_helper_does_not_treat_historical_test_as_new_credential_proof(tmp_path: Path) -> None:
    runner = _Runner()

    assert production_ai_deploy.main(_base_args(tmp_path), runner=runner) == 0

    capability_command = " ".join(
        runner.calls[_call_index(runner, "/api/ai/media-suggestion-capability")][0]
    )
    assert "Historical connection-test state is ignored" in capability_command
    assert "last_connection_test') is not None" not in capability_command
    assert "kronika-ai test" not in _combined(runner)


def test_loaded_credential_gate_failure_still_rolls_back(tmp_path: Path, capsys: Any) -> None:
    runner = _Runner(
        fail_on="Verify LoadCredential mapping without redacted systemctl show",
        fail_message="loaded credential identity mismatch",
    )

    result = production_ai_deploy.main(_base_args(tmp_path), runner=runner)

    combined = _combined(runner)
    captured = capsys.readouterr()
    assert result == 1
    assert "loaded credential identity mismatch" in captured.err
    assert "restore deployment-controlled files" in combined
    assert "rm -rf /run/kronika-ai-credential-deploy" in combined
    assert "kronika-ai test" not in combined
    assert "integrate.api.nvidia.com" not in combined
