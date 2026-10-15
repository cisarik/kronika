"""Production AI credential deployment helper support."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import shlex
import stat
import shutil
import subprocess
import sys
from typing import Callable, Mapping, Sequence

# Standard-library mirror of kronika.identity_env.lookup_env.
# Canonical variables only; empty values count as unset.
IDENTITY_ENVIRONMENT_PREFIX = "KRONIKA_"
SSH_TARGET_ENVIRONMENT_SUFFIX = "PRODUCTION_SSH_TARGET"

AI_CREDENTIAL_MAX_BYTES = 4096
AI_DROPIN_TEMPLATE_MAX_BYTES = 1024
READINESS_DEADLINE_SECONDS = 30
READINESS_POLL_INTERVAL_SECONDS = 1
READINESS_TERMINAL_EXIT = 75
READINESS_TIMEOUT_EXIT = 76
RETAINED_RECOVERY_EXIT = 77
DROPIN_BYTE_MISMATCH_EXIT = 78
SYSTEMD_VERIFY_EXIT = 79
DAEMON_RELOAD_EXIT = 80
SERVICE_ENABLED_EXIT = 81
LOADED_DROPIN_EXIT = 82
LOADED_CREDENTIAL_EXIT = 83
CAPABILITY_UNAVAILABLE_EXIT = 84
CAPABILITY_MALFORMED_EXIT = 85
CAPABILITY_VALIDATION_EXIT = 86
SERVICE_NAME = "kronika.service"
REMOTE_CREDENTIAL_DIR = "/etc/kronika/credentials"
REMOTE_DROPIN_PATH = "/etc/systemd/system/kronika.service.d/20-ai-credential.conf"
REMOTE_AI_CONFIG_PATH = "/var/lib/kronika/ai/config.json"
REMOTE_RELEASE_ROOT = "/opt/kronika/current"
REMOTE_AI_BIN = "/opt/kronika/current/.venv/bin/kronika-ai"
REMOTE_ROLLBACK_DIR = "/run/kronika-ai-credential-deploy"
HELPER_DIR = Path(__file__).resolve().parent
DEPLOY_SYSTEMD_DIR = HELPER_DIR.parent / "systemd"
REMOTE_SERVICE_UNIT_PATH = f"/etc/systemd/system/{SERVICE_NAME}"
AI_CAPABILITY_URL = "http://127.0.0.1:8000/api/ai/media-suggestion-capability"

PROVIDER_CREDENTIALS = {
    "nvidia-nim": "NVIDIA_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "opencode-go": "OPENCODE_API_KEY",
}

PROVIDER_DROPIN_TEMPLATES = {
    "nvidia-nim": DEPLOY_SYSTEMD_DIR / "kronika-ai-credential-nvidia-nim.conf",
    "vercel-ai-gateway": DEPLOY_SYSTEMD_DIR
    / "kronika-ai-credential-vercel-ai-gateway.conf",
    "opencode-go": DEPLOY_SYSTEMD_DIR / "kronika-ai-credential-opencode-go.conf",
}


class DeploymentInputError(Exception):
    """Sanitized operator input failure."""


class DeploymentCommandError(Exception):
    """Sanitized deployment command failure."""


class DeploymentRollbackError(DeploymentCommandError):
    """Sanitized rollback failure with recovery material retained."""


def lookup_env(
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve a canonical operator variable; an empty value is unset."""
    env = os.environ if environ is None else environ
    return env.get(f"{IDENTITY_ENVIRONMENT_PREFIX}{suffix}") or None


SANITIZED_COMMAND_ERRORS = {
    RETAINED_RECOVERY_EXIT: "retained recovery material exists; independent recovery is required",
    READINESS_TERMINAL_EXIT: "service entered terminal failed state",
    READINESS_TIMEOUT_EXIT: "service readiness deadline exceeded",
    DROPIN_BYTE_MISMATCH_EXIT: "drop-in byte equivalence failed",
    SYSTEMD_VERIFY_EXIT: "systemd drop-in verification failed",
    DAEMON_RELOAD_EXIT: "daemon reload failed",
    SERVICE_ENABLED_EXIT: "service enabled-state validation failed",
    LOADED_DROPIN_EXIT: "loaded drop-in mismatch",
    LOADED_CREDENTIAL_EXIT: "loaded credential identity mismatch",
    CAPABILITY_UNAVAILABLE_EXIT: "running capability unavailable",
    CAPABILITY_MALFORMED_EXIT: "running capability malformed",
    CAPABILITY_VALIDATION_EXIT: "running capability validation failed",
}


@dataclass(frozen=True, slots=True)
class LocalSecret:
    identity: str
    value: str


@dataclass(frozen=True, slots=True)
class DropinTemplate:
    provider_id: str
    identity: str
    path: Path
    payload: bytes
    sha256: str


Runner = Callable[[list[str], bytes | None], str]


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Callable[[list[str], bytes | None], None] | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_runner = _subprocess_runner if runner is None else runner
    try:
        target = args.target or (lookup_env(SSH_TARGET_ENVIRONMENT_SUFFIX) or "")
        if not target:
            raise DeploymentInputError("SSH target is required.")
        secret = load_local_secret(
            provider_id=args.provider,
            credential_file=args.credential_file,
            local_ai_env=args.local_ai_env,
        )
        _validate_model(args.model)
        dropin = _load_provider_dropin_template(args.provider)
        _print_plan(args=args, target=target, secret=secret)
        if args.check:
            return 0
        deploy(
            target=target,
            expected_hostname=args.expected_hostname,
            provider_id=args.provider,
            model_id=args.model,
            secret=secret,
            dropin=dropin,
            health_url=args.health_url,
            runner=command_runner,
        )
    except (DeploymentInputError, DeploymentCommandError) as exc:
        print(f"Kronika production AI deployment failed: {exc}", file=sys.stderr)
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fn-production-env-deploy")
    parser.add_argument("--target", default=None, help="SSH target alias.")
    parser.add_argument("--expected-hostname", required=True, help="Expected remote hostname.")
    parser.add_argument("--provider", choices=tuple(PROVIDER_CREDENTIALS), required=True)
    parser.add_argument("--model", required=True)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--credential-file", type=Path, default=None)
    source.add_argument("--local-ai-env", type=Path, default=None)
    parser.add_argument("--health-url", default="http://127.0.0.1:8000/health")
    parser.add_argument("--check", action="store_true", help="Validate and print a sanitized plan.")
    return parser


def load_local_secret(
    *,
    provider_id: str,
    credential_file: Path | None,
    local_ai_env: Path | None,
) -> LocalSecret:
    identity = _credential_identity(provider_id)
    if credential_file is not None:
        payload = _read_private_file(credential_file)
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise DeploymentInputError("Credential source is invalid.") from exc
        return LocalSecret(identity=identity, value=_sanitize_secret(text))
    if local_ai_env is None:
        raise DeploymentInputError("Credential source is required.")
    _validate_private_file(local_ai_env)
    _run_fish_no_execute(local_ai_env)
    value = _extract_fish_secret(local_ai_env, identity)
    return LocalSecret(identity=identity, value=_sanitize_secret(value))


def deploy(
    *,
    target: str,
    expected_hostname: str,
    provider_id: str,
    model_id: str,
    secret: LocalSecret,
    dropin: DropinTemplate,
    health_url: str,
    runner: Callable[[list[str], bytes | None], None],
) -> None:
    transaction_active = False
    try:
        _ssh(
            target,
            _remote_identity_check(expected_hostname),
            runner=runner,
            input_bytes=None,
        )
        try:
            _ssh(target, _remote_backup(secret.identity), runner=runner, input_bytes=None)
        except DeploymentCommandError as exc:
            if str(exc) == SANITIZED_COMMAND_ERRORS[RETAINED_RECOVERY_EXIT]:
                raise
            raise DeploymentCommandError(
                f"incomplete backup; recovery material retained at {REMOTE_ROLLBACK_DIR}"
            ) from exc
        transaction_active = True
        _ssh(
            target,
            _remote_install_credential(secret.identity),
            runner=runner,
            input_bytes=secret.value.encode("utf-8"),
        )
        _ssh(
            target,
            _remote_install_dropin(dropin),
            runner=runner,
            input_bytes=dropin.payload,
        )
        _ssh(
            target,
            _remote_configure_ai(provider_id=provider_id, model_id=model_id),
            runner=runner,
            input_bytes=None,
        )
        for command in _remote_systemd_acceptance_commands(dropin):
            _ssh(target, command, runner=runner, input_bytes=None)
        _ssh(target, _remote_restart_service(), runner=runner, input_bytes=None)
        _ssh(target, _remote_wait_ready(health_url), runner=runner, input_bytes=None)
        _ssh(
            target,
            _remote_validate_capability(provider_id=provider_id, model_id=model_id),
            runner=runner,
            input_bytes=None,
        )
    except DeploymentCommandError:
        if transaction_active:
            _rollback(
                target=target,
                identity=secret.identity,
                health_url=health_url,
                runner=runner,
            )
        raise
    try:
        _ssh(target, _remote_cleanup(secret.identity), runner=runner, input_bytes=None)
    except DeploymentCommandError as exc:
        raise DeploymentCommandError(
            f"cleanup failed; recovery material retained at {REMOTE_ROLLBACK_DIR}"
        ) from exc


def _rollback(
    *,
    target: str,
    identity: str,
    health_url: str,
    runner: Callable[[list[str], bytes | None], None],
) -> None:
    phases = [
        ("restore", _remote_restore(identity)),
        ("daemon-reload", _remote_daemon_reload()),
        ("restart", _remote_restart_service()),
        ("readiness", _remote_wait_ready(health_url)),
        ("cleanup", _remote_cleanup(identity)),
    ]
    for phase, command in phases:
        try:
            _ssh(target, command, runner=runner, input_bytes=None)
        except DeploymentCommandError as exc:
            if phase == "readiness" and str(exc) == SANITIZED_COMMAND_ERRORS[READINESS_TERMINAL_EXIT]:
                raise DeploymentRollbackError(
                    f"rollback failed during terminal service state; recovery material retained at {REMOTE_ROLLBACK_DIR}"
                ) from exc
            if phase == "readiness" and str(exc) == SANITIZED_COMMAND_ERRORS[READINESS_TIMEOUT_EXIT]:
                raise DeploymentRollbackError(
                    f"rollback readiness deadline exceeded; recovery material retained at {REMOTE_ROLLBACK_DIR}"
                ) from exc
            raise DeploymentRollbackError(
                f"rollback failed during {phase}; recovery material retained at {REMOTE_ROLLBACK_DIR}"
            ) from exc


def _subprocess_runner(argv: list[str], input_bytes: bytes | None) -> str:
    result = subprocess.run(argv, input=input_bytes, check=False, capture_output=True, text=False)
    if result.returncode != 0:
        raise DeploymentCommandError(
            SANITIZED_COMMAND_ERRORS.get(result.returncode, "remote command failed")
        )
    return ""


def _ssh(
    target: str,
    remote_command: str,
    *,
    runner: Callable[[list[str], bytes | None], None],
    input_bytes: bytes | None,
) -> str:
    try:
        return runner(["ssh", "-o", "BatchMode=yes", "--", target, remote_command], input_bytes)
    except DeploymentCommandError as exc:
        message = str(exc)
        if message in SANITIZED_COMMAND_ERRORS.values():
            raise DeploymentCommandError(message) from exc
        raise DeploymentCommandError("remote command failed") from exc


def _credential_identity(provider_id: str) -> str:
    try:
        return PROVIDER_CREDENTIALS[provider_id]
    except KeyError as exc:
        raise DeploymentInputError("Unsupported provider.") from exc


def _load_provider_dropin_template(provider_id: str) -> DropinTemplate:
    identity = _credential_identity(provider_id)
    try:
        template_path = PROVIDER_DROPIN_TEMPLATES[provider_id]
    except KeyError as exc:
        raise DeploymentInputError("Unsupported provider.") from exc
    path = Path(template_path)
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentInputError("Template validation failed.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeploymentInputError("Template validation failed.")
    try:
        resolved = path.resolve(strict=True)
        boundary = DEPLOY_SYSTEMD_DIR.resolve(strict=True)
    except OSError as exc:
        raise DeploymentInputError("Template validation failed.") from exc
    if resolved.parent != boundary:
        raise DeploymentInputError("Template validation failed.")
    if metadata.st_size < 1 or metadata.st_size > AI_DROPIN_TEMPLATE_MAX_BYTES:
        raise DeploymentInputError("Template validation failed.")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise DeploymentInputError("Template validation failed.") from exc
    _validate_dropin_template_payload(payload=payload, identity=identity)
    return DropinTemplate(
        provider_id=provider_id,
        identity=identity,
        path=resolved,
        payload=payload,
        sha256=hashlib.sha256(payload).hexdigest(),
    )


def _validate_dropin_template_payload(*, payload: bytes, identity: str) -> None:
    if len(payload) < 1 or len(payload) > AI_DROPIN_TEMPLATE_MAX_BYTES:
        raise DeploymentInputError("Template validation failed.")
    if b"\x00" in payload or b"\r" in payload or b"\\n" in payload:
        raise DeploymentInputError("Template validation failed.")
    if not payload.endswith(b"\n") or payload.endswith(b"\n\n"):
        raise DeploymentInputError("Template validation failed.")
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise DeploymentInputError("Template validation failed.") from exc
    if text.count("\n") != 2:
        raise DeploymentInputError("Template validation failed.")
    lines = text.splitlines()
    expected = [
        "[Service]",
        f"LoadCredential={identity}:{REMOTE_CREDENTIAL_DIR}/{identity}",
    ]
    if lines != expected:
        raise DeploymentInputError("Template validation failed.")


def _validate_model(model_id: str) -> None:
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._/-:")
    if not model_id or any(ch not in allowed for ch in model_id):
        raise DeploymentInputError("Model ID is invalid.")


def _validate_private_file(path: Path) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise DeploymentInputError("Credential source is unavailable.") from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise DeploymentInputError("Credential source is unsafe.")
    if metadata.st_uid != os.getuid():
        raise DeploymentInputError("Credential source is unsafe.")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise DeploymentInputError("Credential source is unsafe.")
    if metadata.st_size < 1 or metadata.st_size > AI_CREDENTIAL_MAX_BYTES:
        raise DeploymentInputError("Credential source is invalid.")


def _read_private_file(path: Path) -> bytes:
    _validate_private_file(path)
    payload = path.read_bytes()
    if len(payload) > AI_CREDENTIAL_MAX_BYTES:
        raise DeploymentInputError("Credential source is invalid.")
    return payload


def _sanitize_secret(value: str) -> str:
    if "\x00" in value:
        raise DeploymentInputError("Credential source is invalid.")
    if value.endswith("\r\n"):
        value = value[:-2]
    elif value.endswith("\n"):
        value = value[:-1]
    if "\n" in value or "\r" in value:
        raise DeploymentInputError("Credential source is invalid.")
    if not value.strip():
        raise DeploymentInputError("Credential source is invalid.")
    if len(value.encode("utf-8")) > AI_CREDENTIAL_MAX_BYTES:
        raise DeploymentInputError("Credential source is invalid.")
    return value.strip()


def _resolve_fish_executable() -> str:
    """Resolve fish from PATH, then standard Homebrew locations on macOS."""
    found = shutil.which("fish")
    if found:
        return found
    for directory in ("/opt/homebrew/bin", "/usr/local/bin"):
        candidate = Path(directory) / "fish"
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return "fish"


def _run_fish_no_execute(path: Path) -> None:
    result = subprocess.run(
        [_resolve_fish_executable(), "--no-execute", str(path)],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        raise DeploymentInputError("Credential source is invalid.")


def _extract_fish_secret(path: Path, identity: str) -> str:
    script = (
        "set -e NVIDIA_API_KEY; "
        "set -e AI_GATEWAY_API_KEY; "
        "set -e OPENCODE_API_KEY; "
        "source $argv[1] >/dev/null 2>/dev/null; "
        "switch $argv[2]; "
        "case NVIDIA_API_KEY; printf '%s' $NVIDIA_API_KEY; "
        "case AI_GATEWAY_API_KEY; printf '%s' $AI_GATEWAY_API_KEY; "
        "case OPENCODE_API_KEY; printf '%s' $OPENCODE_API_KEY; "
        "case '*'; exit 2; "
        "end"
    )
    result = subprocess.run(
        [_resolve_fish_executable(), "-c", script, str(path), identity],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise DeploymentInputError("Credential source is invalid.")
    return result.stdout


def _print_plan(*, args: argparse.Namespace, target: str, secret: LocalSecret) -> None:
    mode = "check" if args.check else "deploy"
    print(f"Kronika production AI credential {mode}")
    print(f"Target: {target}")
    print(f"Expected hostname: {args.expected_hostname}")
    print(f"Provider: {args.provider}")
    print(f"Model: {args.model}")
    print(f"Credential identity: {secret.identity}")
    print(f"Credential destination: {REMOTE_CREDENTIAL_DIR}/{secret.identity}")
    print(f"Drop-in: {REMOTE_DROPIN_PATH}")
    print(f"Service: {SERVICE_NAME}")


def _remote_identity_check(expected_hostname: str) -> str:
    quoted_hostname = shlex.quote(expected_hostname)
    return "\n".join(
        [
            "set -e",
            f'test "$(hostname)" = {quoted_hostname}',
            "sudo -n test -n \"$(id -u kronika 2>/dev/null)\"",
            f"sudo -n systemctl show --property=FragmentPath --value {SERVICE_NAME} >/dev/null",
        ]
    )


def _remote_backup(identity: str) -> str:
    credential_path = f"{REMOTE_CREDENTIAL_DIR}/{identity}"
    return "\n".join(
        [
            "set -e",
            f"sudo -n mkdir -m 0700 {REMOTE_ROLLBACK_DIR} || exit {RETAINED_RECOVERY_EXIT}",
            f"sudo -n chown root:root {REMOTE_ROLLBACK_DIR}",
            f"sudo -n chmod 0700 {REMOTE_ROLLBACK_DIR}",
            f"if sudo -n test -e {credential_path}; then sudo -n cp -a {credential_path} {REMOTE_ROLLBACK_DIR}/{identity}; sudo -n touch {REMOTE_ROLLBACK_DIR}/{identity}.present; else sudo -n touch {REMOTE_ROLLBACK_DIR}/{identity}.absent; fi",
            f"if sudo -n test -e {REMOTE_DROPIN_PATH}; then sudo -n cp -a {REMOTE_DROPIN_PATH} {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf; sudo -n touch {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf.present; else sudo -n touch {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf.absent; fi",
            f"if sudo -n test -e {REMOTE_AI_CONFIG_PATH}; then sudo -n cp -a {REMOTE_AI_CONFIG_PATH} {REMOTE_ROLLBACK_DIR}/config.json; sudo -n touch {REMOTE_ROLLBACK_DIR}/config.json.present; else sudo -n touch {REMOTE_ROLLBACK_DIR}/config.json.absent; fi",
            f"sudo -n touch {REMOTE_ROLLBACK_DIR}/transaction.complete",
        ]
    )


def _remote_install_credential(identity: str) -> str:
    credential_path = f"{REMOTE_CREDENTIAL_DIR}/{identity}"
    next_path = f"{credential_path}.next"
    return "\n".join(
        [
            "set -e",
            f"sudo -n install -d -o root -g root -m 0700 {REMOTE_CREDENTIAL_DIR}",
            f"sudo -n sh -c 'umask 077; cat > {next_path}; chown root:root {next_path}; chmod 0600 {next_path}; mv {next_path} {credential_path}'",
        ]
    )


def _remote_install_dropin(dropin: DropinTemplate) -> str:
    next_path = f"{REMOTE_DROPIN_PATH}.next"
    quoted_sha = shlex.quote(dropin.sha256)
    quoted_size = shlex.quote(str(len(dropin.payload)))
    return "\n".join(
        [
            "set -e",
            "sudo -n install -d -o root -g root -m 0755 /etc/systemd/system/kronika.service.d",
            f"sudo -n sh -c 'umask 022; cat > {next_path}; chown root:root {next_path}; chmod 0644 {next_path}'",
            f"sudo -n test -f {next_path}",
            f"sudo -n test ! -L {next_path}",
            f"test \"$(sudo -n stat -c '%U:%G:%a' {next_path})\" = root:root:644",
            f"test \"$(sudo -n wc -c < {next_path})\" = {quoted_size} || exit {DROPIN_BYTE_MISMATCH_EXIT}",
            "# drop-in byte equivalence failed",
            f"test \"$(sudo -n sha256sum {next_path} | awk '{{print $1}}')\" = {quoted_sha} || exit {DROPIN_BYTE_MISMATCH_EXIT}",
            f"sudo -n mv {next_path} {REMOTE_DROPIN_PATH}",
            f"sudo -n test -f {REMOTE_DROPIN_PATH}",
            f"sudo -n test ! -L {REMOTE_DROPIN_PATH}",
            f"test \"$(sudo -n stat -c '%U:%G:%a' {REMOTE_DROPIN_PATH})\" = root:root:644",
            f"test \"$(sudo -n wc -c < {REMOTE_DROPIN_PATH})\" = {quoted_size} || exit {DROPIN_BYTE_MISMATCH_EXIT}",
            f"test \"$(sudo -n sha256sum {REMOTE_DROPIN_PATH} | awk '{{print $1}}')\" = {quoted_sha} || exit {DROPIN_BYTE_MISMATCH_EXIT}",
        ]
    )


def _remote_configure_ai(*, provider_id: str, model_id: str) -> str:
    quoted_provider = shlex.quote(provider_id)
    quoted_model = shlex.quote(model_id)
    return "\n".join(
        [
            "set -e",
            f"sudo -n -u kronika --chdir={REMOTE_RELEASE_ROOT} {REMOTE_AI_BIN} --config-path {REMOTE_AI_CONFIG_PATH} configure --provider-id {quoted_provider} --model-id {quoted_model} --yes",
        ]
    )


def _remote_systemd_acceptance_commands(dropin: DropinTemplate) -> list[str]:
    quoted_dropin_path = shlex.quote(REMOTE_DROPIN_PATH)
    quoted_service = shlex.quote(SERVICE_NAME)
    quoted_identity = shlex.quote(dropin.identity)
    quoted_cred_dir = shlex.quote(REMOTE_CREDENTIAL_DIR)
    quoted_sha = shlex.quote(dropin.sha256)
    return [
        "\n".join(
            [
                "set -e",
                f"sudo -n systemd-analyze verify {REMOTE_SERVICE_UNIT_PATH} >/dev/null 2>/dev/null || exit {SYSTEMD_VERIFY_EXIT}",
            ]
        ),
        "\n".join(
            [
                "set -e",
                f"sudo -n systemctl daemon-reload >/dev/null 2>/dev/null || exit {DAEMON_RELOAD_EXIT}",
            ]
        ),
        "\n".join(
            [
                "set -e",
                f"test \"$(sudo -n systemctl is-enabled {SERVICE_NAME} 2>/dev/null || true)\" = enabled || exit {SERVICE_ENABLED_EXIT}",
            ]
        ),
        "\n".join(
            [
                "set -e",
                f"test \"$(sudo -n systemctl show --property=FragmentPath --value {SERVICE_NAME} 2>/dev/null || true)\" = {shlex.quote(REMOTE_SERVICE_UNIT_PATH)} || exit {LOADED_DROPIN_EXIT}",
                f"sudo -n systemctl show --property=DropInPaths --value {SERVICE_NAME} 2>/dev/null | tr ' ' '\\n' | grep -Fx -- {quoted_dropin_path} >/dev/null || exit {LOADED_DROPIN_EXIT}",
            ]
        ),
        "\n".join(
            [
                "set -e",
                "# Verify LoadCredential mapping without redacted systemctl show",
                f"dropin_path={quoted_dropin_path}",
                f"service_name={quoted_service}",
                f"identity={quoted_identity}",
                f"cred_dir={quoted_cred_dir}",
                f"expected_sha={quoted_sha}",
                'expected_line="LoadCredential=${identity}:${cred_dir}/${identity}"',
                'sudo -n test -f "$dropin_path" || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'sudo -n test ! -L "$dropin_path" || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'test "$(sudo -n stat -c \'%U:%G:%a\' "$dropin_path")" = root:root:644 || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'test "$(sudo -n sha256sum "$dropin_path" | awk \'{print $1}\')" = "$expected_sha" || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'printf \'%s\\n\' \'[Service]\' "$expected_line" | sudo -n cmp -s - "$dropin_path" || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'unit_text=$(sudo -n systemctl cat "$service_name" 2>/dev/null || true)',
                'printf \'%s\\n\' "$unit_text" | grep -Fq -- "$dropin_path" || exit '
                f"{LOADED_DROPIN_EXIT}",
                'match_count=$(printf \'%s\\n\' "$unit_text" | grep -Fxc -- "$expected_line" || true)',
                'test "$match_count" = 1 || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
                'other_count=$(printf \'%s\\n\' "$unit_text" | grep -E \'^[[:space:]]*LoadCredential=\' | grep -Fvc -- "$expected_line" || true)',
                'test "$other_count" = 0 || exit '
                f"{LOADED_CREDENTIAL_EXIT}",
            ]
        ),
    ]


def _remote_validate_capability(*, provider_id: str, model_id: str) -> str:
    return "\n".join(
        [
            "set -e",
            "python3 - <<'PY'",
            "import json",
            "import urllib.error",
            "import urllib.request",
            f"url = {AI_CAPABILITY_URL!r}",
            f"expected_provider = {provider_id!r}",
            f"expected_model = {model_id!r}",
            "try:",
            "    with urllib.request.urlopen(url, timeout=5) as response:",
            "        if response.status < 200 or response.status >= 300:",
            f"            raise SystemExit({CAPABILITY_UNAVAILABLE_EXIT})",
            "        raw = response.read(16384)",
            "except (OSError, urllib.error.URLError):",
            f"    raise SystemExit({CAPABILITY_UNAVAILABLE_EXIT})",
            "try:",
            "    payload = json.loads(raw.decode('utf-8'))",
            "except (UnicodeDecodeError, json.JSONDecodeError):",
            f"    raise SystemExit({CAPABILITY_MALFORMED_EXIT})",
            "if not isinstance(payload, dict):",
            f"    raise SystemExit({CAPABILITY_MALFORMED_EXIT})",
            "raw_text = raw.decode('utf-8', errors='ignore')",
            "for forbidden in ('/etc/kronika/credentials', 'Authorization', 'Bearer '):",
            "    if forbidden in raw_text:",
            f"        raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "if payload.get('provider_id') != expected_provider:",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "if payload.get('model_id') != expected_model:",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "if payload.get('configured') is not True:",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "if payload.get('available') is not True:",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "if payload.get('credential_available') is not True:",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "# Historical connection-test state is ignored: deployment does not prove liveness.",
            "last_status = payload.get('last_status_check')",
            "if isinstance(last_status, dict) and last_status.get('provider_error'):",
            f"    raise SystemExit({CAPABILITY_VALIDATION_EXIT})",
            "PY",
        ]
    )


def _remote_wait_ready(health_url: str) -> str:
    quoted_url = shlex.quote(health_url)
    return "\n".join(
        [
            "set -e",
            "# Kronika readiness probe",
            f"READINESS_DEADLINE_SECONDS={READINESS_DEADLINE_SECONDS}",
            f"READINESS_POLL_INTERVAL_SECONDS={READINESS_POLL_INTERVAL_SECONDS}",
            "deadline_epoch=$(($(date +%s) + READINESS_DEADLINE_SECONDS))",
            "while :; do",
            "  now_epoch=$(date +%s)",
            "  remaining=$((deadline_epoch - now_epoch))",
            f"  if [ \"$remaining\" -le 0 ]; then exit {READINESS_TIMEOUT_EXIT}; fi",
            f"  active_state=$(sudo -n systemctl show --property=ActiveState --value {SERVICE_NAME} 2>/dev/null || true)",
            f"  result_state=$(sudo -n systemctl show --property=Result --value {SERVICE_NAME} 2>/dev/null || true)",
            '  if [ "$active_state" = "active" ]; then',
            "    http_timeout=$remaining",
            '    if [ "$http_timeout" -gt 1 ]; then http_timeout=1; fi',
            f"    health_body=$(curl --silent --max-time \"$http_timeout\" {quoted_url} 2>/dev/null || true)",
            "    if [ \"$health_body\" = '{\"status\":\"ok\"}' ]; then exit 0; fi",
            "  fi",
            '  if [ "$active_state" = "failed" ]; then',
            f"    exit {READINESS_TERMINAL_EXIT}",
            "  fi",
            '  if [ "$active_state" != "active" ] && [ "$active_state" != "activating" ] && [ -n "$result_state" ] && [ "$result_state" != "success" ]; then',
            f"    exit {READINESS_TERMINAL_EXIT}",
            "  fi",
            "  now_epoch=$(date +%s)",
            "  remaining=$((deadline_epoch - now_epoch))",
            f"  if [ \"$remaining\" -le 0 ]; then exit {READINESS_TIMEOUT_EXIT}; fi",
            "  sleep_for=$READINESS_POLL_INTERVAL_SECONDS",
            '  if [ "$remaining" -lt "$sleep_for" ]; then sleep_for=$remaining; fi',
            '  sleep "$sleep_for"',
            "done",
        ]
    )


def _remote_restore(identity: str) -> str:
    credential_path = f"{REMOTE_CREDENTIAL_DIR}/{identity}"
    return "\n".join(
        [
            "set -e",
            "# restore deployment-controlled files",
            f"sudo -n test -e {REMOTE_ROLLBACK_DIR}/transaction.complete",
            f"if sudo -n test -e {REMOTE_ROLLBACK_DIR}/{identity}.present; then sudo -n install -d -o root -g root -m 0700 {REMOTE_CREDENTIAL_DIR}; sudo -n cp -a {REMOTE_ROLLBACK_DIR}/{identity} {credential_path}; elif sudo -n test -e {REMOTE_ROLLBACK_DIR}/{identity}.absent; then sudo -n rm -f {credential_path}; else exit 42; fi",
            f"if sudo -n test -e {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf.present; then sudo -n install -d -o root -g root -m 0755 /etc/systemd/system/kronika.service.d; sudo -n cp -a {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf {REMOTE_DROPIN_PATH}; elif sudo -n test -e {REMOTE_ROLLBACK_DIR}/20-ai-credential.conf.absent; then sudo -n rm -f {REMOTE_DROPIN_PATH}; else exit 42; fi",
            f"if sudo -n test -e {REMOTE_ROLLBACK_DIR}/config.json.present; then sudo -n cp -a {REMOTE_ROLLBACK_DIR}/config.json {REMOTE_AI_CONFIG_PATH}; elif sudo -n test -e {REMOTE_ROLLBACK_DIR}/config.json.absent; then sudo -n rm -f {REMOTE_AI_CONFIG_PATH}; else exit 42; fi",
            f"sudo -n rm -f {REMOTE_CREDENTIAL_DIR}/{identity}.next {REMOTE_DROPIN_PATH}.next {REMOTE_AI_CONFIG_PATH}.next",
        ]
    )


def _remote_daemon_reload() -> str:
    return "\n".join(["set -e", "sudo -n systemctl daemon-reload"])


def _remote_restart_service() -> str:
    return "\n".join(["set -e", f"sudo -n systemctl restart {SERVICE_NAME}"])


def _remote_cleanup(identity: str) -> str:
    return "\n".join(
        [
            "set -e",
            f"sudo -n rm -f {REMOTE_CREDENTIAL_DIR}/{identity}.next {REMOTE_DROPIN_PATH}.next {REMOTE_AI_CONFIG_PATH}.next",
            f"sudo -n rm -rf {REMOTE_ROLLBACK_DIR}",
        ]
    )


if __name__ == "__main__":
    raise SystemExit(main())
