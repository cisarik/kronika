"""Repeatable immutable Ubuntu NUC release-update engine.

This module is the repository-owned routine NUC release-update contract. It is
executed locally by the operator through the thin Fish entry point
``deploy/ubuntu/kronika-release``, and, for the mutating deploy/rollback
paths, the same file is transferred to the NUC and run in a private remote mode
under Ubuntu system Python 3.12. It uses only the Python standard library.

``deploy/ubuntu/framenest-release`` and
``deploy/ubuntu/framenest_release.py`` are retained compatibility entry points
for the same engine and forward identical arguments until the compatibility
removal cut. They are not a second deployment system.

It never stores, prints, or transmits secrets; never invokes ``uv``; never
runs migrations; and never accepts user-supplied remote shell commands.
Web deploy and rollback move only ``/opt/framenest/current`` and
``framenest.service``. Capture activation is a separate operation.
"""

from __future__ import annotations

import argparse
import contextlib
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
from typing import Callable, Iterator, Mapping, Sequence
import uuid

PROGRAM = "kronika-release"

# Accepted identity environment prefixes for the operator variables this engine
# reads. This is a local standard-library mirror of
# ``framenest.identity_env.lookup_env``: this file runs from the Git checkout
# under Ubuntu system Python without the installed package on ``sys.path``, and
# it must not import the application package. The rule is identical: one spelling
# wins, both unset means unset, an empty value means unset, and both set to
# different values fails closed with status 2 and the two variable names only.
IDENTITY_ENVIRONMENT_PREFIX = "KRONIKA_"
COMPATIBLE_ENVIRONMENT_PREFIX = "FRAMENEST_"

RELEASE_SHA_MARKER = ".framenest-release-sha"
RELEASE_MANIFEST_MARKER = ".framenest-release-manifest.json"
# The spellings a release artefact has ever been written under. These two tables
# are frozen data rather than a derivation from the writer constants above: a
# release written before the writer cut must still resolve after that cut removes
# the former constants, so every reader resolves through these tables and never
# through a literal or through the current writer name. The writer cut owns
# changing ``RELEASE_SHA_MARKER`` and ``RELEASE_MANIFEST_MARKER``; this cut
# changes no writer spelling and adds the canonical spellings as readers only.
ACCEPTED_RELEASE_SHA_MARKERS = (
    ".framenest-release-sha",
    ".kronika-release-sha",
)
ACCEPTED_RELEASE_MANIFEST_MARKERS = (
    ".framenest-release-manifest.json",
    ".kronika-release-manifest.json",
)
RELEASE_SHA_MANIFEST_KEY = "framenest_release_sha"
ACCEPTED_RELEASE_SHA_MANIFEST_KEYS = (
    "framenest_release_sha",
    "kronika_release_sha",
)

# Accepted exact NUC tooling.
SERVICE = "framenest.service"
SERVICE_USER = "framenest"
SERVICE_GROUP = "framenest"
RELEASE_ROOT = "/opt/framenest/releases"
CURRENT = "/opt/framenest/current"
CAPTURE_CURRENT = "/opt/framenest/capture-current"
CAPTURE_RUNNER_SERVICE = "kronika-capture-runner.service"
CAPTURE_JOURNAL = "/var/lib/kronika-capture/capture-journal.sqlite3"
CAPTURE_BRAKE_DIRECTORY = "/var/lib/kronika-capture/profile.capture-launch"
CAPTURE_BRAKE_MS = 300_000
CAPTURE_BRIDGE_PROTOCOL = "1"
CAPTURE_DRAIN_DEADLINE_SECONDS = 30
CAPTURE_READINESS_DEADLINE_SECONDS = 180
CAPTURE_POLL_INTERVAL_SECONDS = 1
ENV_FILE = "/etc/framenest/framenest.env"
POETRY_BIN = "/opt/framenest/tooling/poetry/2.4.1/.venv/bin/poetry"
CPYTHON_BIN = (
    "/opt/framenest/tooling/python/cpython-3.13.14-linux-x86_64-gnu/bin/python3.13"
)
EXPECTED_POETRY_VERSION = "2.4.1"
EXPECTED_CPYTHON_VERSION = "3.13.14"
REMOTE_DEPLOY_DIR = "/run/framenest-release-deploy"
# The lock owner record is a sibling of the deploy directory rather than a file
# inside it, so the documented artefact set inside the deploy directory stays
# exactly the one the runbook publishes.
REMOTE_DEPLOY_LOCK_OWNER_PATH = f"{REMOTE_DEPLOY_DIR}.owner"
REMOTE_DEPLOY_LOCK_RECLAIM_STALE_SECONDS = 60
READINESS_DEADLINE_SECONDS = 30
READINESS_POLL_INTERVAL_SECONDS = 1

# Effective unit properties the installed-unit executable guard reads. Drop-ins
# are part of the effective configuration systemd reports for these properties.
UNIT_EXEC_PROPERTIES = ("ExecStart", "ExecStartPre")

POETRY_TOML = "[virtualenvs]\nin-project = true\n"

# Minimum free capacity required under /opt/framenest beyond transferred bytes.
MIN_FREE_CAPACITY_BYTES = 1 << 30  # 1 GiB safety margin for the release-local .venv.

SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")

# Exit codes are part of the sanitized evidence model. Each outcome is distinct.
EXIT_OK = 0
EXIT_USAGE = 2
EXIT_SOURCE_GATE = 3
EXIT_PUBLIC_MISMATCH = 4
EXIT_AP_MISMATCH = 5
EXIT_TOOLING = 6
EXIT_ARCHIVE_HASH = 7
EXIT_UNSAFE_ARCHIVE = 8
EXIT_EXISTS = 9
EXIT_CAPACITY = 10
EXIT_BACKUP_NOT_READY = 11
EXIT_CHECKPOINT = 12
EXIT_MIGRATION_REQUIRED = 13
EXIT_POETRY = 14
EXIT_READINESS = 15
EXIT_SERVICE_TERMINAL = 16
EXIT_READINESS_TIMEOUT = 17
EXIT_ROLLBACK = 18
EXIT_CLEANUP = 19
EXIT_TRANSPORT = 20
EXIT_PRIVILEGE = 21
EXIT_CAPTURE_BUSY = 22
EXIT_CAPTURE_BRAKE = 23
EXIT_MARKER_CONFLICT = 24
EXIT_UNIT_EXEC_GUARD = 25

CAPTURE_CONTRACT_PATHS = (
    "deploy/systemd/kronika-capture.env.example",
    "deploy/systemd/kronika-capture-xvfb.service",
    "deploy/systemd/kronika-capture-bridge.service",
    "deploy/systemd/kronika-capture-runner.service",
    "deploy/systemd/kronika-capture-vnc.service",
    "deploy/systemd/kronika-capture-view.service",
)
CAPTURE_RUNTIME_CONTRACT = (
    "runtime: node-stdlib-only\n"
    "packages: none\n"
    "chromium: explicit-preflight-verified-executable\n"
    "sandbox: unchanged\n"
    "stealth: unsupported\n"
)
HEX64 = re.compile(r"^[0-9a-f]{64}$")


class ReleaseError(Exception):
    """Sanitized failure with a stable exit code."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_TRANSPORT,
        *,
        remote_exit: int | None = None,
    ) -> None:
        super().__init__(message)
        self.exit_code = exit_code
        self.remote_exit = remote_exit


def lookup_env(
    suffix: str,
    *,
    environ: Mapping[str, str] | None = None,
) -> str | None:
    """Resolve one operator variable under both accepted identity prefixes.

    ``suffix`` is the part of the variable name after the prefix. An empty value
    counts as unset. A pair of different values fails closed with
    ``EXIT_USAGE`` and a message naming the two variable names only; no value,
    length, hash or repr of either value appears in the message or the exit.
    """
    env = os.environ if environ is None else environ
    primary = env.get(f"{IDENTITY_ENVIRONMENT_PREFIX}{suffix}")
    compatible = env.get(f"{COMPATIBLE_ENVIRONMENT_PREFIX}{suffix}")
    if primary == "":
        primary = None
    if compatible == "":
        compatible = None
    if primary is None:
        return compatible
    if compatible is None or primary == compatible:
        return primary
    raise ReleaseError(
        f"conflicting environment variables {IDENTITY_ENVIRONMENT_PREFIX}{suffix} "
        f"and {COMPATIBLE_ENVIRONMENT_PREFIX}{suffix} are set to different values",
        EXIT_USAGE,
    )


# A command runner executes ``argv`` with optional stdin bytes and returns the
# combined decoded output. It raises ReleaseError on non-zero exit. Tests inject
# a fake runner; production uses subprocess.
Runner = Callable[[Sequence[str], bytes | None], str]


def subprocess_runner(argv: Sequence[str], input_bytes: bytes | None) -> str:
    result = subprocess.run(
        list(argv), input=input_bytes, capture_output=True, text=False
    )
    if result.returncode != 0:
        # Keep stderr and argv out of the operator message. Callers map
        # remote_exit to a sanitized phase; they must not print it raw.
        raise ReleaseError(
            "command failed",
            EXIT_TRANSPORT,
            remote_exit=result.returncode,
        )
    return result.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# Input validation (pure)
# ---------------------------------------------------------------------------

def validate_release_sha(sha: str) -> None:
    if not SHA_PATTERN.match(sha):
        raise ReleaseError(
            "release must be a full lowercase 40-hex commit SHA", EXIT_USAGE
        )


def validate_remote_path(value: str, *prefixes: str) -> None:
    if not value.startswith("/"):
        raise ReleaseError("unsafe remote path", EXIT_TRANSPORT)
    parts = [part for part in value.split("/") if part]
    if any(part in (".", "..") for part in parts):
        raise ReleaseError("unsafe remote path", EXIT_TRANSPORT)
    if not any(value == prefix or value.startswith(prefix + "/") for prefix in prefixes):
        raise ReleaseError("unsafe remote path", EXIT_TRANSPORT)


def release_dir(sha: str) -> str:
    return f"{RELEASE_ROOT}/{sha}"


def staging_dir(sha: str) -> str:
    return f"{RELEASE_ROOT}/{sha}.staging"


# ---------------------------------------------------------------------------
# Manifest and marker construction (pure)
# ---------------------------------------------------------------------------

def make_manifest(
    *,
    release_sha: str,
    ap_pin: str,
    superproject_sha256: str,
    ap_archive_sha256: str,
    capture_code_tree: str,
    capture_runtime_contract_sha256: str,
    capture_unit_contract_sha256: str,
    capture_bridge_protocol: str,
) -> dict[str, str]:
    validate_release_sha(capture_code_tree)
    if capture_bridge_protocol != CAPTURE_BRIDGE_PROTOCOL:
        raise ReleaseError("capture bridge protocol is not compatible", EXIT_SOURCE_GATE)
    for digest in (capture_runtime_contract_sha256, capture_unit_contract_sha256):
        if not HEX64.match(digest):
            raise ReleaseError("capture runtime identity is invalid", EXIT_SOURCE_GATE)
    return {
        RELEASE_SHA_MANIFEST_KEY: release_sha,
        "ap_gitlink": ap_pin,
        "superproject_archive_sha256": superproject_sha256,
        "ap_archive_sha256": ap_archive_sha256,
        "capture_code_tree": capture_code_tree,
        "capture_runtime_contract_sha256": capture_runtime_contract_sha256,
        "capture_unit_contract_sha256": capture_unit_contract_sha256,
        "capture_bridge_protocol": capture_bridge_protocol,
    }


def sha256_of_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Archive member validation (pure; safe on Python 3.12)
# ---------------------------------------------------------------------------

def _parts_unsafe(rel: str) -> bool:
    return any(part == ".." for part in Path(rel).parts)


def validate_archive_member(name: str, *, linkname: str | None, isdev: bool) -> None:
    if not name or name.startswith("/") or "\x00" in name or "\\" in name:
        raise ReleaseError("unsafe archive member", EXIT_UNSAFE_ARCHIVE)
    if _parts_unsafe(name):
        raise ReleaseError("unsafe archive member", EXIT_UNSAFE_ARCHIVE)
    if isdev:
        raise ReleaseError("unsafe archive member", EXIT_UNSAFE_ARCHIVE)
    if linkname is not None:
        if (
            linkname.startswith("/")
            or "\x00" in linkname
            or "\\" in linkname
            or _parts_unsafe(linkname)
        ):
            raise ReleaseError("unsafe archive member", EXIT_UNSAFE_ARCHIVE)


def extract_validated_archive(archive_path: Path, destination: str) -> None:
    with tarfile.open(archive_path, "r:*") as archive:
        members = archive.getmembers()
        for member in members:
            validate_archive_member(
                member.name,
                linkname=member.linkname if (member.issym() or member.islnk()) else None,
                isdev=member.isdev(),
            )
        for member in members:
            archive.extract(member, path=destination, filter="data")  # validated above


# ---------------------------------------------------------------------------
# JSON parsing (sanitized)
# ---------------------------------------------------------------------------

def parse_json_status(output: str) -> dict[str, object]:
    try:
        payload = json.loads(output.strip())
    except json.JSONDecodeError as exc:
        raise ReleaseError("unexpected remote output", EXIT_TRANSPORT) from exc
    if not isinstance(payload, dict):
        raise ReleaseError("unexpected remote output", EXIT_TRANSPORT)
    return payload


# ---------------------------------------------------------------------------
# SSH transport
# ---------------------------------------------------------------------------

SSH_OPTIONS = [
    "-o", "BatchMode=yes",
    "-o", "RequestTTY=no",
    "-o", "StrictHostKeyChecking=yes",
    "-o", "IdentitiesOnly=yes",
    "-o", "ForwardAgent=no",
    "-o", "ClearAllForwardings=yes",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "ServerAliveCountMax=2",
]


def ssh(
    runner: Runner,
    *,
    target: str,
    user: str,
    identity: str,
    remote_command: str,
    input_bytes: bytes | None = None,
) -> str:
    argv = ["ssh", *SSH_OPTIONS, "-i", identity, f"{user}@{target}", remote_command]
    return runner(argv, input_bytes)


# ---------------------------------------------------------------------------
# Fixed remote command builders (single source of truth for remote work)
# ---------------------------------------------------------------------------

def cmd_remote_mkdir_deploy_dir() -> str:
    return f"sudo -n mkdir -m 0700 {REMOTE_DEPLOY_DIR}"


def cmd_remote_rm_deploy_dir() -> str:
    return f"sudo -n rmdir {REMOTE_DEPLOY_DIR}"


def cmd_remote_read_deploy_lock_owner() -> str:
    return f"sudo -n cat {shlex.quote(REMOTE_DEPLOY_LOCK_OWNER_PATH)}"


def cmd_remote_write_deploy_lock_owner() -> str:
    return (
        "sudo -n sh -c 'umask 077; cat > "
        f"{shlex.quote(REMOTE_DEPLOY_LOCK_OWNER_PATH)}'"
    )


def cmd_remote_remove_deploy_lock_owner() -> str:
    return f"sudo -n rm -f {shlex.quote(REMOTE_DEPLOY_LOCK_OWNER_PATH)}"


def cmd_remote_quarantine_deploy_dir(destination: str) -> str:
    """Move an abandoned deploy directory aside with a single atomic rename."""
    return (
        f"sudo -n mv -T {shlex.quote(REMOTE_DEPLOY_DIR)} {shlex.quote(destination)}"
    )


def cmd_remote_rm_deploy_quarantine(path: str) -> str:
    return f"sudo -n rm -rf {shlex.quote(path)}"


def cmd_remote_write_file(path: str, sha256: str) -> str:
    return (
        "set -e\n"
        f"sudo -n sh -c 'umask 077; cat > {shlex.quote(path)}'\n"
        f"test \"$(sudo -n sha256sum {shlex.quote(path)} | cut -d' ' -f1)\" = "
        f"{shlex.quote(sha256)}"
    )


def cmd_remote_readlink_current() -> str:
    return f"sudo -n readlink -n {CURRENT}"


def cmd_remote_read_release_sha(path: str, marker: str = RELEASE_SHA_MARKER) -> str:
    return f"sudo -n cat {shlex.quote(f'{path}/{marker}')}"


def cmd_remote_read_manifest(
    path: str, marker: str = RELEASE_MANIFEST_MARKER
) -> str:
    return f"sudo -n cat {shlex.quote(f'{path}/{marker}')}"


def cmd_remote_release_marker_presence(path: str) -> str:
    """Report which accepted release-marker spellings a tree actually carries.

    Returns a remote command whose stdout is one ``<kind> <marker>`` line per
    marker that exists, manifest lines before SHA lines and each group in the
    accepted order. A tree carrying no marker produces no line. Absence is not a
    ``test -e`` failure, so an empty result is a classification and not a
    transport error. Only presence is reported here; the caller reads and
    validates every present spelling through the central resolver.
    """
    lines: list[str] = []
    for kind, markers in (
        ("manifest", ACCEPTED_RELEASE_MANIFEST_MARKERS),
        ("sha", ACCEPTED_RELEASE_SHA_MARKERS),
    ):
        for marker in markers:
            candidate = shlex.quote(f"{path}/{marker}")
            lines.append(
                f"if test -e {candidate}; then echo {kind} {marker}; fi"
            )
    return f"sudo -n sh -c {shlex.quote(chr(10).join(lines))}"


def cmd_remote_test_not_exists(path: str) -> str:
    return f"sudo -n test ! -e {shlex.quote(path)}"


def cmd_remote_test_exists(path: str) -> str:
    return f"sudo -n test -e {shlex.quote(path)}"


def cmd_remote_test_executable(path: str) -> str:
    return f"sudo -n test -x {shlex.quote(path)}"


def cmd_remote_test_regular_executable(path: str) -> str:
    """Require a regular, executable, non-symlink file.

    ``test -f`` follows symlinks, so the ``! -L`` term is what rejects a
    symlink standing where a console script must be.
    """
    quoted = shlex.quote(path)
    script = f"test -f {quoted} -a ! -L {quoted} -a -x {quoted}"
    return f"sudo -n sh -c {shlex.quote(script)}"


def cmd_remote_unit_execution_properties() -> str:
    """Effective ``ExecStart`` and ``ExecStartPre``, drop-ins included."""
    properties = " ".join(
        f"--property={name}" for name in UNIT_EXEC_PROPERTIES
    )
    return f"sudo -n systemctl show {properties} {SERVICE}"


def cmd_remote_service_is_active() -> str:
    return f"sudo -n systemctl is-active {SERVICE}"


def cmd_remote_service_active_state() -> str:
    return f"sudo -n systemctl show -p ActiveState --value {SERVICE}"


def cmd_remote_service_result() -> str:
    return f"sudo -n systemctl show -p Result --value {SERVICE}"


def cmd_remote_systemd_working_directory() -> str:
    return f"sudo -n systemctl show -p WorkingDirectory --value {SERVICE}"


def cmd_remote_service_fragment_path() -> str:
    return f"sudo -n systemctl show -p FragmentPath --value {SERVICE}"


def cmd_remote_restart_service() -> str:
    return f"sudo -n systemctl restart {SERVICE}"


def cmd_remote_journal() -> str:
    return (
        f"sudo -n journalctl -u {SERVICE} -n 40 --no-pager "
        "--output=cat | grep -Ev '^$' || true"
    )


def cmd_remote_df_capacity() -> str:
    return "sudo -n df -Pk /opt/framenest | tail -n 1"


def cmd_remote_capture_current(target_path: str) -> str:
    quoted = shlex.quote(target_path)
    return (
        "set -e\n"
        f"prev=$(sudo -n readlink -n {CURRENT})\n"
        + 'test -n "$prev"\n'
        + "sudo -n sh -c 'printf %s \"$1\" > " + quoted + "' sh \"$prev\"\n"
    )


def service_account_prefix(release_path: str) -> str:
    return (
        f"sudo -n -u {SERVICE_USER} --chdir={shlex.quote(release_path)} "
        f"env FRAMENEST_ENV_FILE={ENV_FILE} "
        f"{shlex.quote(release_path)}/.venv/bin"
    )


def cmd_remote_db_status(release_path: str) -> str:
    return f"{service_account_prefix(release_path)}/framenest-db status"


def cmd_remote_backup_status(release_path: str) -> str:
    return f"{service_account_prefix(release_path)}/framenest-backup status"


def cmd_remote_production_cli(release_path: str, operation: str) -> str:
    """Run ``framenest-production`` with the unit EnvironmentFile.

    ``framenest-production`` calls ``load_settings(env_file=None)`` and therefore
    ignores ``FRAMENEST_ENV_FILE``. The systemd unit supplies process
    environment via ``EnvironmentFile=``; this helper uses the same contract
    through a oneshot ``systemd-run`` so secrets never enter argv.
    """
    if operation not in ("check-database-ready", "check-health"):
        raise ReleaseError("invalid command", EXIT_USAGE)
    return (
        "sudo -n systemd-run --quiet --pipe --wait --collect "
        f"--uid={SERVICE_USER} --gid={SERVICE_GROUP} "
        f"--working-directory={shlex.quote(release_path)} "
        f"--property=EnvironmentFile={shlex.quote(ENV_FILE)} "
        f"{shlex.quote(f'{release_path}/.venv/bin/framenest-production')} "
        f"{operation}"
    )


def cmd_remote_check_database_ready(release_path: str) -> str:
    return cmd_remote_production_cli(release_path, "check-database-ready")


def cmd_remote_check_health(release_path: str) -> str:
    return cmd_remote_production_cli(release_path, "check-health")


def cmd_remote_run_scheduled_backup(release_path: str) -> str:
    return f"{service_account_prefix(release_path)}/framenest-backup run-scheduled"


def cmd_remote_prepare_dir(release_path: str) -> str:
    return f"sudo -n install -d -o root -g root -m 0755 {shlex.quote(release_path)}"


def cmd_remote_extract(archive_path: str, destination: str, engine_path: str) -> str:
    return (
        f"sudo -n python3 {shlex.quote(engine_path)} _remote _remote-extract "
        f"--archive {shlex.quote(archive_path)} "
        f"--destination {shlex.quote(destination)}"
    )


def cmd_remote_relocate_venv_shebangs(
    staging_path: str, final_path: str, engine_path: str
) -> str:
    return (
        f"sudo -n python3 {shlex.quote(engine_path)} _remote "
        f"_remote-relocate-venv-shebangs "
        f"--staging {shlex.quote(staging_path)} "
        f"--final {shlex.quote(final_path)}"
    )


def cmd_remote_cat_stdin(path: str) -> str:
    """Write runner stdin to ``path``; payload bytes must not enter the command string."""
    return f"sudo -n sh -c 'umask 077; cat > {shlex.quote(path)}'"


def cmd_remote_write_markers(release_path: str) -> tuple[str, str]:
    """Write the marker pair the routine writers still emit.

    The spellings come from the writer constants, never from a literal, so the
    writer cut changes exactly these two constants and nothing else.
    """
    return (
        cmd_remote_cat_stdin(f"{release_path}/{RELEASE_MANIFEST_MARKER}"),
        cmd_remote_cat_stdin(f"{release_path}/{RELEASE_SHA_MARKER}"),
    )


def cmd_remote_write_poetry_toml(release_path: str) -> str:
    return cmd_remote_cat_stdin(f"{release_path}/poetry.toml")


def cmd_remote_poetry_check_lock(release_path: str) -> str:
    return (
        f"sudo -n {shlex.quote(POETRY_BIN)} check --lock "
        f"--directory {shlex.quote(release_path)}"
    )


def cmd_remote_poetry_env_use(release_path: str) -> str:
    return (
        f"sudo -n {shlex.quote(POETRY_BIN)} env use {shlex.quote(CPYTHON_BIN)} "
        f"--directory {shlex.quote(release_path)}"
    )


def cmd_remote_poetry_install(release_path: str) -> str:
    return (
        f"sudo -n {shlex.quote(POETRY_BIN)} install --only main "
        f"--no-interaction --no-ansi --directory {shlex.quote(release_path)}"
    )


def cmd_remote_poetry_version() -> str:
    return f"sudo -n {shlex.quote(POETRY_BIN)} --version"


def cmd_remote_cpython_version() -> str:
    return f"sudo -n {shlex.quote(CPYTHON_BIN)} --version"


def cmd_remote_sha256(path: str) -> str:
    return f"sudo -n sha256sum {shlex.quote(path)}"


def cmd_remote_chown_root(release_path: str) -> str:
    return (
        f"sudo -n chown -R root:root {shlex.quote(release_path)}"
    )


def cmd_remote_remove_release_writable_bits(release_path: str) -> str:
    return (
        f"sudo -n chmod -R a-w {shlex.quote(release_path)}"
    )


def cmd_remote_remove_file(path: str) -> str:
    return f"sudo -n rm -f {shlex.quote(path)}"


def cmd_remote_rename_staging(staging_path: str, release_path: str) -> str:
    return (
        f"sudo -n mv {shlex.quote(staging_path)} {shlex.quote(release_path)}"
    )


def cmd_remote_atomic_switch(release_path: str) -> str:
    return (
        "set -e\n"
        f"sudo -n ln -s {shlex.quote(release_path)} /opt/framenest/current.next\n"
        f"sudo -n mv -T /opt/framenest/current.next {CURRENT}"
    )


def cmd_remote_atomic_switch_capture(release_path: str) -> str:
    return (
        "set -e\n"
        f"sudo -n ln -s {shlex.quote(release_path)} /opt/framenest/capture-current.next\n"
        f"sudo -n mv -T /opt/framenest/capture-current.next {CAPTURE_CURRENT}"
    )


def cmd_remote_restart_capture_runner() -> str:
    return f"sudo -n systemctl restart {CAPTURE_RUNNER_SERVICE}"


def cmd_remote_read_optional_link(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        f"if sudo -n test -L {quoted}; then sudo -n readlink -n {quoted}; "
        "else printf %s absent; fi"
    )


def _remote_python(script: str) -> str:
    return "sudo -n python3 -c " + shlex.quote(script)


def cmd_remote_capture_work_gate() -> str:
    script = (
        "# kronika-capture-work-gate\n"
        "import json, sqlite3, stat\n"
        "from pathlib import Path\n"
        f"journal = Path({CAPTURE_JOURNAL!r})\n"
        "live = {'offered', 'running'}\n"
        "paused = {'needs_admin'}\n"
        "def finish(value):\n"
        "    print('blocked=' + value)\n"
        "    raise SystemExit\n"
        "try:\n"
        "    if not journal.exists():\n"
        "        finish('none')\n"
        "    elif journal.is_symlink() or not stat.S_ISREG(journal.lstat().st_mode):\n"
        "        finish('unverifiable')\n"
        "    else:\n"
        "        con = sqlite3.connect('file:' + journal.as_posix() + '?mode=ro', uri=True)\n"
        "        statuses = []\n"
        "        for (record,) in con.execute('SELECT record FROM jobs'):\n"
        "            payload = json.loads(record)\n"
        "            status = payload.get('status') if isinstance(payload, dict) else None\n"
        "            if isinstance(status, str):\n"
        "                statuses.append(status)\n"
        "        service = ''\n"
        "        row = con.execute('SELECT record FROM service WHERE singleton=1').fetchone()\n"
        "        if row:\n"
        "            service_payload = json.loads(row[0])\n"
        "            if isinstance(service_payload, dict) and isinstance(service_payload.get('state'), str):\n"
        "                service = service_payload['state']\n"
        "        if any(status in live for status in statuses):\n"
        "            finish('live')\n"
        "        elif service == 'needs_admin' or any(status in paused for status in statuses):\n"
        "            finish('paused')\n"
        "        elif any(status == 'queued' for status in statuses):\n"
        "            finish('queued')\n"
        "        else:\n"
        "            finish('none')\n"
        "except Exception:\n"
        "    finish('unverifiable')\n"
    )
    return _remote_python(script)


def cmd_remote_capture_brake_gate() -> str:
    script = (
        "# kronika-capture-brake-gate\n"
        "import json, stat, time\n"
        "from pathlib import Path\n"
        f"directory = Path({CAPTURE_BRAKE_DIRECTORY!r})\n"
        f"limit = {CAPTURE_BRAKE_MS}\n"
        "metadata = directory / 'last-start.json'\n"
        "def finish(value):\n"
        "    print('brake=' + value)\n"
        "    raise SystemExit\n"
        "try:\n"
        "    if not directory.exists():\n"
        "        finish('ok')\n"
        "    elif directory.is_symlink() or not directory.is_dir():\n"
        "        finish('refuse')\n"
        "    elif (not metadata.exists()) or metadata.is_symlink() or not stat.S_ISREG(metadata.lstat().st_mode):\n"
        "        finish('refuse')\n"
        "    elif metadata.stat().st_size > 256:\n"
        "        finish('refuse')\n"
        "    else:\n"
        "        payload = json.loads(metadata.read_text(encoding='utf-8'))\n"
        "        started = payload.get('started_ms') if isinstance(payload, dict) else None\n"
        "        now = time.time() * 1000\n"
        "        if isinstance(started, bool) or not isinstance(started, (int, float)):\n"
        "            finish('refuse')\n"
        "        elif started < 0 or now < started or (now - started) < limit:\n"
        "            finish('refuse')\n"
        "        else:\n"
        "            finish('ok')\n"
        "except Exception:\n"
        "    finish('refuse')\n"
    )
    return _remote_python(script)


def _capture_service_reader() -> str:
    """Read only coordination metadata; never query jobs, results or credentials."""
    return (
        "import json, sqlite3, stat, uuid\n"
        "from pathlib import Path\n"
        f"journal = Path({CAPTURE_JOURNAL!r})\n"
        "def valid_identity(value):\n"
        "    return isinstance(value, str) and str(uuid.UUID(value)) == value\n"
        "def read_service():\n"
        "    try:\n"
        "        info = journal.lstat()\n"
        "    except FileNotFoundError:\n"
        "        return {}\n"
        "    if not stat.S_ISREG(info.st_mode):\n"
        "        raise ValueError()\n"
        "    con = sqlite3.connect('file:' + journal.as_posix() + '?mode=ro', uri=True)\n"
        "    try:\n"
        "        row = con.execute('SELECT record FROM service WHERE singleton=1').fetchone()\n"
        "    finally:\n"
        "        con.close()\n"
        "    if row is None:\n"
        "        return {}\n"
        "    payload = json.loads(row[0])\n"
        "    if not isinstance(payload, dict):\n"
        "        raise ValueError()\n"
        "    for key in ('runner_id', 'browser_session'):\n"
        "        value = payload.get(key)\n"
        "        if value is not None and not valid_identity(value):\n"
        "            raise ValueError()\n"
        "    return payload\n"
    )


def cmd_remote_capture_identity() -> str:
    script = (
        "# kronika-capture-identity-snapshot\n"
        + _capture_service_reader()
        + "try:\n"
        "    payload = read_service()\n"
        "    print(json.dumps([payload.get('runner_id'), payload.get('browser_session')]))\n"
        "except Exception:\n"
        "    print('identity=unverifiable')\n"
    )
    return _remote_python(script)


def cmd_remote_capture_readiness_gate(previous_identity: tuple[str | None, str | None]) -> str:
    script = (
        "# kronika-capture-readiness-gate\n"
        + _capture_service_reader()
        + "import subprocess\n"
        f"previous_identity = {previous_identity!r}\n"
        f"service = {CAPTURE_RUNNER_SERVICE!r}\n"
        "def finish(value):\n"
        "    print('readiness=' + value)\n"
        "    raise SystemExit\n"
        "try:\n"
        "    shown = subprocess.run(\n"
        "        ['systemctl', 'show', '-p', 'ActiveState', '--value', service],\n"
        "        check=False, capture_output=True, text=True,\n"
        "    )\n"
        "    if shown.returncode != 0:\n"
        "        finish('unverifiable')\n"
        "    active = shown.stdout.strip()\n"
        "    if active == 'failed':\n"
        "        finish('failed')\n"
        "    else:\n"
        "        payload = read_service()\n"
        "        identity = (payload.get('runner_id'), payload.get('browser_session'))\n"
        "        if not all(valid_identity(value) and value != old for value, old in zip(identity, previous_identity)):\n"
        "            finish('starting')\n"
        "        service_state = payload.get('state', '')\n"
        "        if service_state == 'browser_unavailable':\n"
        "            finish('browser_unavailable')\n"
        "        elif service_state == 'needs_admin':\n"
        "            finish('needs_admin')\n"
        "        elif service_state == 'ready' and active == 'active':\n"
        "            finish('ready')\n"
        "        else:\n"
        "            finish('starting')\n"
        "except Exception:\n"
        "    finish('unverifiable')\n"
    )
    return _remote_python(script)


def cmd_remote_lock_hash(release_path: str) -> str:
    return f"sudo -n sha256sum {shlex.quote(release_path)}/poetry.lock"


# ---------------------------------------------------------------------------
# Local git source/public gates
# ---------------------------------------------------------------------------

def run_local_git(runner: Runner, argv: Sequence[str]) -> str:
    try:
        return runner(["git", *argv], None).strip()
    except ReleaseError as exc:
        raise ReleaseError("source gate failed", EXIT_SOURCE_GATE) from exc


def resolve_repo_root(runner: Runner) -> str:
    return run_local_git(runner, ["rev-parse", "--show-toplevel"])


def verify_local_head(runner: Runner, release_sha: str) -> None:
    head = run_local_git(runner, ["rev-parse", "HEAD"])
    if head != release_sha:
        raise ReleaseError("local HEAD does not match the requested release", EXIT_SOURCE_GATE)


def verify_clean_worktrees(runner: Runner) -> None:
    for argv in (
        ["status", "--porcelain", "--untracked-files=no"],
        ["-C", ".ap", "status", "--porcelain", "--untracked-files=no"],
    ):
        output = run_local_git(runner, argv)
        if output.strip():
            raise ReleaseError("worktree is not clean", EXIT_SOURCE_GATE)


def verify_public_main(runner: Runner, release_sha: str) -> None:
    output = run_local_git(runner, ["ls-remote", "origin", "refs/heads/main"])
    fields = output.split()
    if not fields or fields[0] != release_sha:
        raise ReleaseError("public main does not equal the requested release", EXIT_PUBLIC_MISMATCH)


def ap_gitlink_of(runner: Runner, release_sha: str) -> str:
    output = run_local_git(runner, ["ls-tree", release_sha, ".ap"])
    fields = output.split()
    if len(fields) < 3 or fields[1] != "commit":
        raise ReleaseError("unable to resolve the release .ap gitlink", EXIT_AP_MISMATCH)
    return fields[2]


def verify_ap_pin(runner: Runner, release_sha: str) -> str:
    gitlink = ap_gitlink_of(runner, release_sha)
    ap_head = run_local_git(runner, ["-C", ".ap", "rev-parse", "HEAD"])
    if ap_head != gitlink:
        raise ReleaseError("local .ap HEAD differs from the release gitlink", EXIT_AP_MISMATCH)
    return gitlink


def capture_runtime_identity(runner: Runner, release_sha: str) -> dict[str, str]:
    """Identity of packaged capture code, its runtime contract, and unit sources."""

    validate_release_sha(release_sha)
    try:
        code_tree = run_local_git(
            runner, ["rev-parse", f"{release_sha}:src/kronika_capture"]
        )
    except ReleaseError as exc:
        raise ReleaseError("capture code identity is unavailable", EXIT_SOURCE_GATE) from exc
    if not SHA_PATTERN.match(code_tree):
        raise ReleaseError("capture code identity is invalid", EXIT_SOURCE_GATE)
    runtime_hash = hashlib.sha256(CAPTURE_RUNTIME_CONTRACT.encode("utf-8")).hexdigest()
    chunks: list[str] = []
    for path in CAPTURE_CONTRACT_PATHS:
        try:
            text = run_local_git(runner, ["show", f"{release_sha}:{path}"])
        except ReleaseError as exc:
            raise ReleaseError(
                "capture unit contract is unavailable", EXIT_SOURCE_GATE
            ) from exc
        chunks.append(path + "\n" + text + "\n")
    unit_hash = hashlib.sha256("".join(chunks).encode("utf-8")).hexdigest()
    return {
        "capture_code_tree": code_tree,
        "capture_runtime_contract_sha256": runtime_hash,
        "capture_unit_contract_sha256": unit_hash,
        "capture_bridge_protocol": CAPTURE_BRIDGE_PROTOCOL,
    }


def retained_release_paths(*paths: str) -> tuple[str, ...]:
    retained: list[str] = []
    for path in paths:
        if path and path != "absent" and path not in retained:
            retained.append(path)
    return tuple(retained)


def format_release_pointers(web_sha: str, capture_sha: str) -> str:
    return f"web_release: {web_sha}\ncapture_release: {capture_sha}"


def build_archives(
    runner: Runner,
    *,
    repo_root: str,
    release_sha: str,
    ap_gitlink: str,
    destination: Path,
) -> tuple[Path, Path]:
    superproject = destination / "superproject.tar"
    ap_archive = destination / "ap.tar"
    run_local_git(
        runner,
        ["archive", "--format=tar", "--output", str(superproject), release_sha],
    )
    run_local_git(
        runner,
        ["-C", ".ap", "archive", "--format=tar", "--output", str(ap_archive), ap_gitlink],
    )
    return superproject, ap_archive


# ---------------------------------------------------------------------------
# Remote phase execution (deploy / rollback)
# ---------------------------------------------------------------------------

def remote_exec(runner: Runner, command: str) -> str:
    try:
        return runner(["/bin/sh", "-c", command], None)
    except ReleaseError as exc:
        raise ReleaseError("remote command failed", EXIT_TRANSPORT) from exc


# ---------------------------------------------------------------------------
# Remote engine private subcommands
# ---------------------------------------------------------------------------

def remote_extract(runner: Runner, archive: str, destination: str) -> None:
    validate_remote_path(archive, REMOTE_DEPLOY_DIR, RELEASE_ROOT)
    validate_remote_path(destination, RELEASE_ROOT)
    try:
        extract_validated_archive(Path(archive), destination)
    except tarfile.TarError as exc:
        raise ReleaseError("archive extraction failed", EXIT_UNSAFE_ARCHIVE) from exc


def relocate_venv_shebangs(staging_path: str, final_path: str) -> None:
    """Rewrite staging-prefix paths under ``.venv`` to the final release path.

    Covers console-script shebangs and editable-install metadata such as
    ``.pth`` and ``direct_url.json``.
    """
    validate_remote_path(staging_path, RELEASE_ROOT)
    validate_remote_path(final_path, RELEASE_ROOT)
    if staging_path != f"{final_path}.staging":
        raise ReleaseError("staging path does not match release", EXIT_TRANSPORT)

    venv_root = Path(staging_path) / ".venv"
    if not venv_root.is_dir():
        raise ReleaseError("release venv is missing", EXIT_POETRY)

    rewritten = 0
    for path in sorted(venv_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        if staging_path not in text:
            continue
        replacement = text.replace(staging_path, final_path)
        mode = path.stat().st_mode
        path.write_text(replacement, encoding="utf-8")
        path.chmod(mode)
        rewritten += 1

    if rewritten == 0:
        raise ReleaseError("venv staging paths were not relocated", EXIT_POETRY)

    venv_bin = venv_root / "bin"
    for name in ("framenest-db", "framenest-backup"):
        script = venv_bin / name
        if not script.is_file() or script.is_symlink():
            raise ReleaseError("required console script is missing", EXIT_POETRY)
        content = script.read_text(encoding="utf-8")
        if ".staging" in content:
            raise ReleaseError("console script still names staging path", EXIT_POETRY)
        first = content.splitlines()[0] if content else ""
        expected = f"#!{final_path}/.venv/bin/python"
        if not first.startswith(expected):
            raise ReleaseError("console script does not name release interpreter", EXIT_POETRY)

    for path in sorted(venv_root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        if path.suffix != ".pth" and path.name != "direct_url.json":
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            raise ReleaseError("editable install metadata is not text", EXIT_POETRY)
        if ".staging" in content:
            raise ReleaseError(
                "editable install metadata still names staging path", EXIT_POETRY
            )


def remote_relocate_venv_shebangs(
    runner: Runner, staging_path: str, final_path: str
) -> None:
    relocate_venv_shebangs(staging_path, final_path)


# ---------------------------------------------------------------------------
# Status and check read-only remote probes
# ---------------------------------------------------------------------------

def manifest_release_sha(manifest: object) -> object:
    """Return the release SHA under either accepted manifest key, or None."""
    if not isinstance(manifest, dict):
        return None
    for key in ACCEPTED_RELEASE_SHA_MANIFEST_KEYS:
        if key in manifest:
            return manifest[key]
    return None


#: One ``<kind> <marker>`` line of marker-presence output.
MARKER_PRESENCE_LINE = re.compile(r"^(manifest|sha) ([A-Za-z0-9._-]+)$")

#: One ``<property>=<value>`` line of effective unit execution output.
UNIT_EXEC_LINE = re.compile(r"^(ExecStart|ExecStartPre)=(.*)$")

#: The ``path=`` field of one effective command, which is the executable and not
#: an argument, a flag or an interpreter path named inside an argument.
UNIT_EXEC_PATH = re.compile(r"(?:^|[\s;{}])path=((?:\\.|[^\s\\};])*)")


@dataclass(frozen=True)
class ResolvedReleaseMarkers:
    """One release tree's identity, resolved under every accepted spelling."""

    path: str
    sha: str
    sha_markers: tuple[str, ...]
    manifest: dict[str, object] | None
    manifest_raw: str
    manifest_markers: tuple[str, ...]


def parse_release_marker_presence(output: str) -> tuple[list[str], list[str]]:
    """Return the present manifest and SHA marker spellings, in reported order."""
    manifests: list[str] = []
    shas: list[str] = []
    accepted = ACCEPTED_RELEASE_MANIFEST_MARKERS + ACCEPTED_RELEASE_SHA_MARKERS
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        match = MARKER_PRESENCE_LINE.match(line)
        if match is None or match.group(2) not in accepted:
            raise ReleaseError(
                "current release markers are unreadable", EXIT_TRANSPORT
            )
        if match.group(1) == "manifest":
            manifests.append(match.group(2))
        else:
            shas.append(match.group(2))
    return manifests, shas


def read_release_markers(
    runner: Runner, transport: dict[str, str], release_path: str
) -> ResolvedReleaseMarkers:
    """Resolve one release tree's identity through every accepted spelling.

    Every reader goes through this function, so no reader can prefer one
    spelling over another. When more than one spelling is present, all of them
    must agree: two SHA markers, two manifests, or a SHA marker and a manifest
    that disagree fail closed rather than silently preferring one of them.
    """
    presence = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_release_marker_presence(release_path),
    )
    manifests, shas = parse_release_marker_presence(presence)
    if not manifests and not shas:
        raise ReleaseError(
            "current release SHA marker and manifest are absent", EXIT_TRANSPORT
        )

    sha_by_marker: dict[str, str] = {}
    for marker in shas:
        value = ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_release_sha(release_path, marker),
        ).strip()
        if not SHA_PATTERN.match(value):
            raise ReleaseError(
                "current release SHA marker is invalid", EXIT_TRANSPORT
            )
        sha_by_marker[marker] = value

    manifest_by_marker: dict[str, dict[str, object]] = {}
    manifest_raw_by_marker: dict[str, str] = {}
    for marker in manifests:
        raw = ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_manifest(release_path, marker),
        )
        manifest = parse_json_status(raw)
        declared = manifest_release_sha(manifest)
        if not isinstance(declared, str) or not SHA_PATTERN.match(declared):
            raise ReleaseError(
                "current release SHA marker is invalid", EXIT_TRANSPORT
            )
        manifest_by_marker[marker] = manifest
        manifest_raw_by_marker[marker] = raw

    identities = sorted(set(sha_by_marker.values()))
    for manifest in manifest_by_marker.values():
        declared = manifest_release_sha(manifest)
        if isinstance(declared, str) and declared not in identities:
            identities.append(declared)
    if len(set(identities)) > 1:
        raise ReleaseError(
            "release identity markers disagree", EXIT_MARKER_CONFLICT
        )

    primary_manifest = manifest_by_marker[manifests[0]] if manifests else None
    return ResolvedReleaseMarkers(
        path=release_path,
        sha=identities[0],
        sha_markers=tuple(sha_by_marker),
        manifest=primary_manifest,
        manifest_raw=(
            manifest_raw_by_marker[manifests[0]] if manifests else ""
        ),
        manifest_markers=tuple(manifest_by_marker),
    )


def read_current_release(
    runner: Runner, transport: dict[str, str]
) -> ResolvedReleaseMarkers:
    raw = ssh(runner, **transport, remote_command=cmd_remote_readlink_current())
    current_path = raw.strip()
    if not current_path.startswith(RELEASE_ROOT):
        raise ReleaseError("current release path is unexpected", EXIT_TRANSPORT)
    return read_release_markers(runner, transport, current_path)


def read_optional_release_sha(
    runner: Runner, transport: dict[str, str], pointer: str
) -> str:
    raw = ssh(
        runner, **transport, remote_command=cmd_remote_read_optional_link(pointer)
    ).strip()
    if raw == "absent":
        return "absent"
    validate_remote_path(raw, RELEASE_ROOT)
    return read_release_markers(runner, transport, raw).sha


def read_backup_readiness(runner: Runner, transport: dict[str, str], release_path: str) -> str:
    raw = ssh(runner, **transport, remote_command=cmd_remote_backup_status(release_path))
    payload = parse_json_status(raw)
    readiness = payload.get("restore_readiness")
    if not isinstance(readiness, str):
        raise ReleaseError("backup readiness unavailable", EXIT_BACKUP_NOT_READY)
    return readiness


def read_db_current_revision(
    runner: Runner, transport: dict[str, str], release_path: str
) -> str:
    raw = ssh(runner, **transport, remote_command=cmd_remote_db_status(release_path))
    payload = parse_json_status(raw)
    current = payload.get("current_revision")
    if not isinstance(current, str):
        raise ReleaseError("database revision unavailable", EXIT_TRANSPORT)
    return current


def verify_tooling(runner: Runner, transport: dict[str, str]) -> None:
    for path in (POETRY_BIN, CPYTHON_BIN):
        ssh(runner, **transport, remote_command=cmd_remote_test_executable(path))
    poetry_version = ssh(runner, **transport, remote_command=cmd_remote_poetry_version()).strip()
    cpython_version = ssh(runner, **transport, remote_command=cmd_remote_cpython_version()).strip()
    if EXPECTED_POETRY_VERSION not in poetry_version:
        raise ReleaseError("Poetry tooling is mismatched", EXIT_TOOLING)
    if EXPECTED_CPYTHON_VERSION not in cpython_version:
        raise ReleaseError("CPython tooling is mismatched", EXIT_TOOLING)


def verify_capacity(
    runner: Runner, transport: dict[str, str], required_bytes: int
) -> None:
    raw = ssh(runner, **transport, remote_command=cmd_remote_df_capacity()).strip()
    fields = raw.split()
    if len(fields) < 4:
        raise ReleaseError("capacity check failed", EXIT_CAPACITY)
    try:
        available_kb = int(fields[3])
    except ValueError as exc:
        raise ReleaseError("capacity check failed", EXIT_CAPACITY) from exc
    if available_kb * 1024 < required_bytes + MIN_FREE_CAPACITY_BYTES:
        raise ReleaseError("insufficient capacity for the release", EXIT_CAPACITY)


# ---------------------------------------------------------------------------
# Installed-unit executable guard
# ---------------------------------------------------------------------------

def _unescape_unit_text(value: str) -> str:
    """Decode the C-style escapes systemd emits inside a property value."""
    if "\\" not in value:
        return value
    out: list[str] = []
    index = 0
    simple = {"n": "\n", "t": "\t", "r": "\r", "\\": "\\", '"': '"', "'": "'"}
    while index < len(value):
        char = value[index]
        if char != "\\":
            out.append(char)
            index += 1
            continue
        following = value[index + 1 : index + 2]
        if following == "x" and len(value) >= index + 4:
            out.append(chr(int(value[index + 2 : index + 4], 16)))
            index += 4
            continue
        if following in simple:
            out.append(simple[following])
            index += 2
            continue
        out.append(char)
        index += 1
    return "".join(out)


def unit_executables_from_show(output: str) -> dict[str, tuple[str, ...]]:
    """Extract the executable field of every effective command, per property.

    A property that systemd reports with no command contributes no executable,
    which is ordinary for ``ExecStartPre``. Only the ``path=`` field of a command
    is read; ``argv[]`` entries are arguments, so a path argument, a flag or an
    interpreter named inside an argument is never mistaken for the executable.
    """
    found: dict[str, list[str]] = {name: [] for name in UNIT_EXEC_PROPERTIES}
    for raw in output.splitlines():
        match = UNIT_EXEC_LINE.match(raw.strip())
        if match is None or match.group(1) not in found:
            continue
        for path in UNIT_EXEC_PATH.findall(match.group(2)):
            found[match.group(1)].append(_unescape_unit_text(path))
    return {name: tuple(values) for name, values in found.items()}


def release_scoped_console_script(executable: str) -> str | None:
    """Return the release-relative console script a unit executable names.

    Only a release console script under a ``.venv/bin`` directory of the current
    pointer or of a concrete release directory maps onto the target release.
    Anything else - a system interpreter, an absolute path outside the release
    root, or a release file that is not a console script - is an unknown
    effective execution form and is refused rather than skipped.
    """
    for prefix, expects_release_directory in (
        (CURRENT + "/", False),
        (RELEASE_ROOT + "/", True),
    ):
        if not executable.startswith(prefix):
            continue
        parts = executable[len(prefix) :].split("/")
        if expects_release_directory:
            if (
                len(parts) < 4
                or not SHA_PATTERN.match(parts[0])
                or parts[1] != ".venv"
                or parts[2] != "bin"
                or not parts[3]
            ):
                return None
            return "/".join(parts[1:])
        if len(parts) < 3 or parts[0] != ".venv" or parts[1] != "bin" or not parts[2]:
            return None
        return "/".join(parts)
    return None


def verify_unit_executables(
    runner: Runner, transport: dict[str, str], release_path: str
) -> tuple[str, ...]:
    """Require every effective unit executable to exist in ``release_path``.

    This runs before any pointer switch and before any restart, so a release
    whose console scripts the installed unit cannot execute is refused while the
    running release still serves. Drop-ins contribute, because systemd reports
    the effective configuration for the property.
    """
    output = ssh(
        runner, **transport, remote_command=cmd_remote_unit_execution_properties()
    )
    resolved = unit_executables_from_show(output)
    if not resolved.get("ExecStart"):
        raise ReleaseError(
            "installed unit declares no executable command", EXIT_UNIT_EXEC_GUARD
        )
    required: set[str] = set()
    for name in UNIT_EXEC_PROPERTIES:
        for executable in resolved.get(name, ()):
            scoped = release_scoped_console_script(executable)
            if scoped is None:
                raise ReleaseError(
                    "installed unit execution form is not a release console script",
                    EXIT_UNIT_EXEC_GUARD,
                )
            required.add(scoped)
    for relative in sorted(required):
        candidate = f"{release_path}/{relative}"
        validate_remote_path(candidate, RELEASE_ROOT)
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_test_regular_executable(candidate),
            )
        except ReleaseError as exc:
            raise ReleaseError(
                "installed unit executable is missing in the target release",
                EXIT_UNIT_EXEC_GUARD,
            ) from exc
    return tuple(sorted(required))


# ---------------------------------------------------------------------------
# Reclaimable remote deploy lock
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DeployLockDecision:
    outcome: str
    reason: str
    quarantine: str = ""


def deploy_lock_owner_record() -> str:
    """Return this run's lock identity: a nonce, its process and its start.

    The record is deliberately an identity, not a secret. It carries no path, no
    host-specific host value and no credential, and it is never printed.
    """
    return (
        f"{uuid.uuid4().hex} {os.getpid()} "
        f"{int(time.time())} {socket.gethostname()}"
    )


def _lock_owner_fields(record: str) -> tuple[str, int, int, str] | None:
    fields = record.strip().split(" ")
    if len(fields) != 4:
        return None
    token, pid_text, started_text, host = fields
    if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
        return None
    if not pid_text.isdigit() or not started_text.isdigit():
        return None
    pid = int(pid_text)
    started = int(started_text)
    if pid <= 0 or started <= 0 or not host:
        return None
    return token, pid, started, host


def _process_is_live(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


def classify_deploy_lock_owner(record: str, owner: str) -> str | None:
    """Return why an existing lock may be reclaimed, or ``None`` to refuse.

    Reclamation needs both ownership and liveness. A record naming this very run
    is this run's own prior lock. A record naming another run is reclaimable only
    when it was written by this workstation, it is older than the reclaim bound,
    and its recorded process is no longer alive. Anything else, including an
    unreadable or absent record, is refused.
    """
    parsed = _lock_owner_fields(record)
    if parsed is None:
        return None
    token, pid, started, host = parsed
    own = _lock_owner_fields(owner)
    if own is not None and token == own[0] and pid == own[1]:
        return "own-identity"
    if host != socket.gethostname():
        return None
    if time.time() - started < REMOTE_DEPLOY_LOCK_RECLAIM_STALE_SECONDS:
        return None
    if _process_is_live(pid):
        return None
    return "abandoned-owner"


def acquire_deploy_lock(
    runner: Runner, transport: dict[str, str], owner: str
) -> DeployLockDecision:
    try:
        ssh(runner, **transport, remote_command=cmd_remote_mkdir_deploy_dir())
    except ReleaseError as exc:
        try:
            existing = ssh(
                runner,
                **transport,
                remote_command=cmd_remote_read_deploy_lock_owner(),
            )
        except ReleaseError as read_exc:
            raise ReleaseError(
                "existing remote lock without readable ownership", EXIT_EXISTS
            ) from read_exc
        reason = classify_deploy_lock_owner(existing, owner)
        if reason is None:
            raise ReleaseError(
                "existing remote lock owned by another run", EXIT_EXISTS
            ) from exc
        quarantine = f"{REMOTE_DEPLOY_DIR}.reclaimed-{reason}"
        validate_remote_path(quarantine, "/run")
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_quarantine_deploy_dir(quarantine),
            )
            ssh(
                runner, **transport, remote_command=cmd_remote_mkdir_deploy_dir()
            )
        except ReleaseError as reclaim_exc:
            raise ReleaseError(
                "existing remote lock could not be reclaimed", EXIT_EXISTS
            ) from reclaim_exc
        _write_deploy_lock_owner(runner, transport, owner)
        return DeployLockDecision("reclaimed", reason, quarantine)
    _write_deploy_lock_owner(runner, transport, owner)
    return DeployLockDecision("acquired", "none")


def _write_deploy_lock_owner(
    runner: Runner, transport: dict[str, str], owner: str
) -> None:
    ssh(
        runner,
        **transport,
        remote_command=cmd_remote_write_deploy_lock_owner(),
        input_bytes=owner.encode("ascii"),
    )


def release_deploy_lock(
    runner: Runner, transport: dict[str, str], decision: DeployLockDecision
) -> None:
    if decision.quarantine:
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_rm_deploy_quarantine(decision.quarantine),
        )
    ssh(runner, **transport, remote_command=cmd_remote_rm_deploy_dir())
    ssh(runner, **transport, remote_command=cmd_remote_remove_deploy_lock_owner())


def lock_report(decision: DeployLockDecision) -> str:
    return f"remote_lock: {decision.outcome} ({decision.reason})"


@contextlib.contextmanager
def remote_deploy_lock(
    runner: Runner, transport: dict[str, str]
) -> Iterator[DeployLockDecision]:
    """Hold the remote deploy lock for one mutating operation.

    The lock is released on every exit path, including the ones that previously
    left it behind: before this, a failure between acquiring the lock and the
    cleanup block had no self-healing path at all.
    """
    decision = acquire_deploy_lock(
        runner, transport, deploy_lock_owner_record()
    )
    try:
        yield decision
    finally:
        failing = sys.exc_info()[0] is not None
        try:
            release_deploy_lock(runner, transport, decision)
        except ReleaseError as exc:
            if not failing:
                raise ReleaseError("cleanup failed", EXIT_CLEANUP) from exc


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=PROGRAM)
    subcommands = parser.add_subparsers(dest="command", required=True)

    status = subcommands.add_parser("status", help="Read-only current release status.")
    _add_transport_args(status)

    check = subcommands.add_parser("check", help="Read-only pre-deployment gate.")
    check.add_argument("--release", required=True)
    _add_transport_args(check)

    deploy = subcommands.add_parser("deploy", help="Prepare and switch a routine release.")
    deploy.add_argument("--release", required=True)
    deploy.add_argument("--yes", action="store_true")
    _add_transport_args(deploy)

    rollback = subcommands.add_parser("rollback", help="Switch to an existing complete release.")
    rollback.add_argument("--release", required=True)
    rollback.add_argument("--yes", action="store_true")
    _add_transport_args(rollback)

    activate_capture = subcommands.add_parser(
        "activate-capture",
        help="Switch capture-current and restart the capture runner once.",
    )
    activate_capture.add_argument("--release", required=True)
    activate_capture.add_argument("--yes", action="store_true")
    _add_transport_args(activate_capture)

    rollback_capture = subcommands.add_parser(
        "rollback-capture",
        help="Switch capture-current back under the same capture brake.",
    )
    rollback_capture.add_argument("--release", required=True)
    rollback_capture.add_argument("--yes", action="store_true")
    _add_transport_args(rollback_capture)

    remote = subcommands.add_parser("_remote", help=argparse.SUPPRESS)
    remote_sub = remote.add_subparsers(dest="remote_command", required=True)
    remote_extract_p = remote_sub.add_parser("_remote-extract", help=argparse.SUPPRESS)
    remote_extract_p.add_argument("--archive", required=True)
    remote_extract_p.add_argument("--destination", required=True)
    remote_relocate_p = remote_sub.add_parser(
        "_remote-relocate-venv-shebangs", help=argparse.SUPPRESS
    )
    remote_relocate_p.add_argument("--staging", required=True)
    remote_relocate_p.add_argument("--final", required=True)

    return parser


def _add_transport_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--target", default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--identity", default=None)


def _resolve_transport(args: argparse.Namespace) -> dict[str, str]:
    target = args.target or (lookup_env("NUC_SSH_TARGET") or "")
    user = args.user or (lookup_env("NUC_SSH_USER") or "")
    identity = args.identity or (lookup_env("NUC_SSH_IDENTITY") or "")
    if not target:
        raise ReleaseError("SSH target is required", EXIT_USAGE)
    if not user:
        raise ReleaseError("SSH user is required", EXIT_USAGE)
    if not identity:
        raise ReleaseError("SSH identity is required", EXIT_USAGE)
    return {"target": target, "user": user, "identity": identity}


def main(
    argv: Sequence[str] | None = None,
    *,
    runner: Runner | None = None,
) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    command_runner = runner if runner is not None else subprocess_runner
    try:
        if args.command == "status":
            return _cmd_status(args, command_runner)
        if args.command == "check":
            return _cmd_check(args, command_runner)
        if args.command == "deploy":
            return _cmd_deploy(args, command_runner)
        if args.command == "rollback":
            return _cmd_rollback(args, command_runner)
        if args.command in ("activate-capture", "rollback-capture"):
            return _cmd_capture_transition(args, command_runner)
        if args.command == "_remote":
            return _cmd_remote(args, command_runner)
        raise ReleaseError("invalid command", EXIT_USAGE)
    except ReleaseError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return exc.exit_code


def _cmd_remote(args: argparse.Namespace, runner: Runner) -> int:
    if args.remote_command == "_remote-extract":
        remote_extract(runner, args.archive, args.destination)
        return EXIT_OK
    if args.remote_command == "_remote-relocate-venv-shebangs":
        remote_relocate_venv_shebangs(runner, args.staging, args.final)
        return EXIT_OK
    raise ReleaseError("invalid remote command", EXIT_USAGE)


def _cmd_status(args: argparse.Namespace, runner: Runner) -> int:
    transport = _resolve_transport(args)
    current = read_current_release(runner, transport)
    current_path = current.path
    active = ssh(runner, **transport, remote_command=cmd_remote_service_is_active()).strip()
    db_revision = read_db_current_revision(runner, transport, current_path)
    backup = read_backup_readiness(runner, transport, current_path)
    web_sha = current.sha
    capture_sha = read_optional_release_sha(runner, transport, CAPTURE_CURRENT)
    print("kronika-release status")
    print(f"active_release: {web_sha}")
    print(format_release_pointers(web_sha, capture_sha))
    print(f"release_path: {current_path}")
    print(f"service_active: {active}")
    print(f"database_revision: {db_revision}")
    print(f"backup_restore_readiness: {backup}")
    if not current.manifest_raw:
        print("release_manifest: absent")
    return EXIT_OK


def _cmd_check(args: argparse.Namespace, runner: Runner) -> int:
    release_sha = args.release
    validate_release_sha(release_sha)
    transport = _resolve_transport(args)

    repo_root = resolve_repo_root(runner)
    verify_local_head(runner, release_sha)
    verify_clean_worktrees(runner)
    verify_public_main(runner, release_sha)
    ap_gitlink = verify_ap_pin(runner, release_sha)

    with tempfile.TemporaryDirectory(prefix="kronika-release-check-") as temp:
        temp_path = Path(temp)
        superproject, ap_archive = build_archives(
            runner,
            repo_root=repo_root,
            release_sha=release_sha,
            ap_gitlink=ap_gitlink,
            destination=temp_path,
        )
        super_hash = sha256_of_file(superproject)
        ap_hash = sha256_of_file(ap_archive)

    verify_tooling(runner, transport)
    current = read_current_release(runner, transport)
    current_path = current.path
    readiness = read_backup_readiness(runner, transport, current_path)
    if readiness != "ready":
        raise ReleaseError("catalog backup is not restore-ready", EXIT_BACKUP_NOT_READY)

    print("kronika-release check")
    print(f"release: {release_sha}")
    print(f"ap_gitlink: {ap_gitlink}")
    print(f"public_main: {release_sha}")
    print(f"superproject_sha256: {super_hash}")
    print(f"ap_archive_sha256: {ap_hash}")
    identity = capture_runtime_identity(runner, release_sha)
    print(f"current_release: {current_path}")
    print(f"backup_restore_readiness: {readiness}")
    print(f"capture_code_tree: {identity['capture_code_tree']}")
    print(f"capture_runtime_contract_sha256: {identity['capture_runtime_contract_sha256']}")
    print(f"capture_unit_contract_sha256: {identity['capture_unit_contract_sha256']}")
    print(f"capture_bridge_protocol: {identity['capture_bridge_protocol']}")
    return EXIT_OK


def _cmd_deploy(args: argparse.Namespace, runner: Runner) -> int:
    release_sha = args.release
    validate_release_sha(release_sha)
    if not args.yes:
        raise ReleaseError("deploy requires --yes to confirm", EXIT_USAGE)
    transport = _resolve_transport(args)

    # Re-run all check gates first.
    _cmd_check(args, runner)

    repo_root = resolve_repo_root(runner)
    ap_gitlink = verify_ap_pin(runner, release_sha)

    with contextlib.ExitStack() as stack:
        temp_path = Path(
            stack.enter_context(
                tempfile.TemporaryDirectory(prefix="kronika-release-deploy-")
            )
        )
        superproject, ap_archive = build_archives(
            runner,
            repo_root=repo_root,
            release_sha=release_sha,
            ap_gitlink=ap_gitlink,
            destination=temp_path,
        )
        super_hash = sha256_of_file(superproject)
        ap_hash = sha256_of_file(ap_archive)
        super_size = superproject.stat().st_size
        ap_size = ap_archive.stat().st_size

        engine_bytes = Path(__file__).read_bytes()
        engine_hash = hashlib.sha256(engine_bytes).hexdigest()

        target = release_dir(release_sha)
        staging = staging_dir(release_sha)
        remote_super = f"{REMOTE_DEPLOY_DIR}/superproject.tar"
        remote_ap = f"{REMOTE_DEPLOY_DIR}/ap.tar"
        remote_engine = f"{REMOTE_DEPLOY_DIR}/framenest_release.py"
        remote_prev = f"{REMOTE_DEPLOY_DIR}/previous-release"

        # Remote lock and pre-existence gates. The lock is held for the whole
        # mutating operation and is released on every exit path, so an
        # interrupted run cannot leave an unreclaimable lock behind.
        lock_decision = stack.enter_context(remote_deploy_lock(runner, transport))
        ssh(runner, **transport, remote_command=cmd_remote_test_not_exists(target))
        ssh(runner, **transport, remote_command=cmd_remote_test_not_exists(staging))

        verify_tooling(runner, transport)
        verify_capacity(runner, transport, super_size + ap_size)

        # Transfer the engine, then the two archives; verify each SHA-256.
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_write_file(remote_engine, engine_hash),
            input_bytes=engine_bytes,
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_write_file(remote_super, super_hash),
            input_bytes=superproject.read_bytes(),
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_write_file(remote_ap, ap_hash),
            input_bytes=ap_archive.read_bytes(),
        )

        # Prepare the staging tree: extract, materialize AP, write markers/poetry.
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_prepare_dir(staging),
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_extract(remote_super, staging, remote_engine),
        )
        ssh(
            runner,
            **transport,
            remote_command=(
                f"sudo -n install -d -o root -g root -m 0755 "
                f"{shlex.quote(staging)}/.ap"
            ),
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_extract(remote_ap, f"{staging}/.ap", remote_engine),
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_write_poetry_toml(staging),
            input_bytes=POETRY_TOML.encode("utf-8"),
        )

        # Poetry preparation against the committed lock; never update the lock.
        lock_before = ssh(
            runner, **transport, remote_command=cmd_remote_lock_hash(staging)
        ).strip()
        ssh(runner, **transport, remote_command=cmd_remote_poetry_check_lock(staging))
        ssh(runner, **transport, remote_command=cmd_remote_poetry_env_use(staging))
        ssh(runner, **transport, remote_command=cmd_remote_poetry_install(staging))
        lock_after = ssh(
            runner, **transport, remote_command=cmd_remote_lock_hash(staging)
        ).strip()
        if lock_before != lock_after:
            raise ReleaseError("poetry.lock changed during installation", EXIT_POETRY)

        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_relocate_venv_shebangs(
                staging, target, remote_engine
            ),
        )
        ssh(runner, **transport, remote_command=cmd_remote_chown_root(staging))
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_remove_release_writable_bits(staging),
        )

        # Write markers and publish atomically.
        identity = capture_runtime_identity(runner, release_sha)
        manifest_json = json.dumps(
            make_manifest(
                release_sha=release_sha,
                ap_pin=ap_gitlink,
                superproject_sha256=super_hash,
                ap_archive_sha256=ap_hash,
                **identity,
            ),
            sort_keys=True,
            separators=(",", ":"),
        )
        manifest_cmd, sha_cmd = cmd_remote_write_markers(staging)
        ssh(
            runner,
            **transport,
            remote_command=manifest_cmd,
            input_bytes=manifest_json.encode("utf-8"),
        )
        ssh(
            runner,
            **transport,
            remote_command=sha_cmd,
            input_bytes=(release_sha + "\n").encode("utf-8"),
        )
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_rename_staging(staging, target),
        )

        # Same-schema gate: packaged head must equal the current production revision.
        status_raw = ssh(
            runner, **transport, remote_command=cmd_remote_db_status(target)
        )
        db_payload = parse_json_status(status_raw)
        current_revision = db_payload.get("current_revision")
        head_revision = db_payload.get("head_revision")
        if current_revision != head_revision:
            raise ReleaseError("migration-required", EXIT_MIGRATION_REQUIRED)

        # Fresh verified checkpoint before cutover.
        checkpoint = ssh(
            runner, **transport, remote_command=cmd_remote_run_scheduled_backup(target)
        )
        checkpoint_payload = parse_json_status(checkpoint)
        if checkpoint_payload.get("state") != "succeeded":
            raise ReleaseError("checkpoint failed", EXIT_CHECKPOINT)

        # Capture the previous release for rollback.
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_capture_current(remote_prev),
        )

        # The installed-unit executable guard runs before the pointer switch and
        # before any restart, and outside the rollback-wrapped block, so a
        # release the installed unit cannot start is refused while the running
        # release still serves.
        unit_executables = verify_unit_executables(runner, transport, target)

        try:
            # Pre-cutover readiness under the target release.
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_database_ready(target),
                )
            except ReleaseError as exc:
                raise ReleaseError(
                    "pre-cutover target readiness failed", EXIT_READINESS
                ) from exc
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_atomic_switch(target),
                )
            except ReleaseError as exc:
                raise ReleaseError("atomic switch failed", EXIT_READINESS) from exc
            try:
                ssh(
                    runner, **transport, remote_command=cmd_remote_restart_service()
                )
            except ReleaseError as exc:
                raise ReleaseError("restart failed", EXIT_READINESS) from exc
            _verify_cutover(runner, transport, target)
        except ReleaseError as exc:
            _rollback(runner, transport, remote_prev)
            raise ReleaseError(
                f"deployment failed; rollback attempted ({exc})", exc.exit_code
            ) from exc

        # Cleanup exact owned temporary remote state. The deploy directory itself
        # and its ownership record are released by the lock context.
        try:
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_super))
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_ap))
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_engine))
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_prev))
        except ReleaseError as exc:
            raise ReleaseError("cleanup failed", EXIT_CLEANUP) from exc

    capture_sha = read_optional_release_sha(runner, transport, CAPTURE_CURRENT)
    print(f"kronika-release deploy complete: {release_sha}")
    print(format_release_pointers(release_sha, capture_sha))
    print(lock_report(lock_decision))
    print(f"unit_executables: {','.join(unit_executables)}")
    return EXIT_OK


def _verify_cutover(runner: Runner, transport: dict[str, str], target: str) -> None:
    current_path = ssh(
        runner, **transport, remote_command=cmd_remote_readlink_current()
    ).strip()
    if current_path != target:
        raise ReleaseError("cutover failed", EXIT_READINESS)
    working_dir = ssh(
        runner, **transport, remote_command=cmd_remote_systemd_working_directory()
    ).strip()
    if working_dir != CURRENT:
        raise ReleaseError("service working directory is unexpected", EXIT_READINESS)
    _wait_ready(runner, transport, target)
    logs = ssh(runner, **transport, remote_command=cmd_remote_journal())
    _assert_logs_sanitized(logs)


def _wait_ready(runner: Runner, transport: dict[str, str], target: str) -> None:
    """Poll service readiness for deploy and rollback.

    Retries transient ``activating``, socket-not-ready, and health-not-ready
    states for up to 30 seconds. Terminal systemd states fail immediately.
    Deadline expiry uses ``EXIT_READINESS_TIMEOUT``.
    """
    deadline = time.monotonic() + READINESS_DEADLINE_SECONDS
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReleaseError(
                "service readiness deadline exceeded", EXIT_READINESS_TIMEOUT
            )
        active = ssh(
            runner, **transport, remote_command=cmd_remote_service_active_state()
        ).strip()
        result_state = ssh(
            runner, **transport, remote_command=cmd_remote_service_result()
        ).strip()
        if active == "failed":
            raise ReleaseError(
                "service entered terminal failed state", EXIT_SERVICE_TERMINAL
            )
        if (
            active not in ("active", "activating")
            and result_state
            and result_state != "success"
        ):
            raise ReleaseError(
                "service entered terminal failed state", EXIT_SERVICE_TERMINAL
            )
        if active == "active":
            health_ok = True
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_health(target),
                )
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_database_ready(target),
                )
            except ReleaseError:
                health_ok = False
            if health_ok:
                return
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ReleaseError(
                "service readiness deadline exceeded", EXIT_READINESS_TIMEOUT
            )
        sleep_for = READINESS_POLL_INTERVAL_SECONDS
        if remaining < sleep_for:
            sleep_for = remaining
        time.sleep(sleep_for)


def _assert_logs_sanitized(logs: str) -> None:
    for token in ("/etc/framenest/credentials", "Authorization:", "Bearer ", "BEGIN "):
        if token in logs:
            raise ReleaseError("unsanitized log content", EXIT_SERVICE_TERMINAL)


def _rollback(runner: Runner, transport: dict[str, str], previous_path: str) -> None:
    """Restore the previous release, validating it before the pointer moves.

    Readiness and the installed-unit executable guard both run ahead of the
    symlink switch, so an automatic rollback can never move the pointer onto a
    release the installed unit cannot start.
    """
    try:
        prev = ssh(
            runner,
            **transport,
            remote_command=f"sudo -n cat {shlex.quote(previous_path)}",
        ).strip()
        validate_remote_path(prev, RELEASE_ROOT)
        verify_unit_executables(runner, transport, prev)
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_check_database_ready(prev),
            )
        except ReleaseError as exc:
            raise ReleaseError(
                "rollback pre-restart readiness failed", EXIT_ROLLBACK
            ) from exc
        try:
            ssh(runner, **transport, remote_command=cmd_remote_atomic_switch(prev))
        except ReleaseError as exc:
            raise ReleaseError("rollback switch failed", EXIT_ROLLBACK) from exc
        try:
            ssh(runner, **transport, remote_command=cmd_remote_restart_service())
        except ReleaseError as exc:
            raise ReleaseError("rollback restart failed", EXIT_ROLLBACK) from exc
        _verify_cutover(runner, transport, prev)
    except ReleaseError as exc:
        if exc.exit_code == EXIT_ROLLBACK:
            raise
        raise ReleaseError(f"rollback failed ({exc})", EXIT_ROLLBACK) from exc


def _cmd_rollback(args: argparse.Namespace, runner: Runner) -> int:
    release_sha = args.release
    validate_release_sha(release_sha)
    if not args.yes:
        raise ReleaseError("rollback requires --yes to confirm", EXIT_USAGE)
    transport = _resolve_transport(args)

    target = release_dir(release_sha)
    ssh(runner, **transport, remote_command=cmd_remote_test_exists(target))
    # The installed release must resolve its identity under every accepted
    # marker spelling, and agree with each other when several are present.
    installed = read_release_markers(runner, transport, target)
    if installed.sha != release_sha:
        raise ReleaseError("installed release SHA does not match", EXIT_SOURCE_GATE)
    # The installed-unit executable guard runs before the pointer switch and
    # before any restart.
    unit_executables = verify_unit_executables(runner, transport, target)

    remote_prev = f"{REMOTE_DEPLOY_DIR}/rollback-previous-release"
    with contextlib.ExitStack() as stack:
        lock_decision = stack.enter_context(remote_deploy_lock(runner, transport))
        try:
            ssh(runner, **transport, remote_command=cmd_remote_capture_current(remote_prev))
            try:
                ssh(runner, **transport, remote_command=cmd_remote_check_database_ready(target))
                ssh(runner, **transport, remote_command=cmd_remote_atomic_switch(target))
                ssh(runner, **transport, remote_command=cmd_remote_restart_service())
                _verify_cutover(runner, transport, target)
            except ReleaseError as exc:
                _rollback(runner, transport, remote_prev)
                raise ReleaseError(
                    f"rollback target failed; previous release restored ({exc})",
                    exc.exit_code,
                ) from exc
        finally:
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_prev))

    capture_sha = read_optional_release_sha(runner, transport, CAPTURE_CURRENT)
    print(f"kronika-release rollback complete: {release_sha}")
    print(format_release_pointers(release_sha, capture_sha))
    print(lock_report(lock_decision))
    print(f"unit_executables: {','.join(unit_executables)}")
    return EXIT_OK


def _poll_until(deadline: float) -> None:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return
    time.sleep(min(CAPTURE_POLL_INTERVAL_SECONDS, remaining))


def _drain_capture_work(runner: Runner, transport: dict[str, str]) -> None:
    deadline = time.monotonic() + CAPTURE_DRAIN_DEADLINE_SECONDS
    while True:
        blocked = ssh(
            runner, **transport, remote_command=cmd_remote_capture_work_gate()
        ).strip()
        if blocked == "blocked=none":
            return
        if blocked == "blocked=queued":
            if time.monotonic() >= deadline:
                raise ReleaseError("capture work did not drain", EXIT_CAPTURE_BUSY)
            _poll_until(deadline)
            continue
        if blocked in ("blocked=live", "blocked=paused"):
            raise ReleaseError("capture has live or paused work", EXIT_CAPTURE_BUSY)
        raise ReleaseError("capture work state is unverifiable", EXIT_CAPTURE_BUSY)


def _enforce_capture_brake(runner: Runner, transport: dict[str, str]) -> None:
    brake = ssh(
        runner, **transport, remote_command=cmd_remote_capture_brake_gate()
    ).strip()
    if brake != "brake=ok":
        raise ReleaseError("capture restart brake refuses launch", EXIT_CAPTURE_BRAKE)


def _snapshot_capture_identity(runner: Runner, transport: dict[str, str]) -> tuple[str | None, str | None]:
    raw = ssh(runner, **transport, remote_command=cmd_remote_capture_identity()).strip()
    try:
        if len(raw) > 256:
            raise ValueError()
        identity = json.loads(raw)
        if not isinstance(identity, list) or len(identity) != 2:
            raise ValueError()
        pattern = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
        if any(value is not None and (not isinstance(value, str) or re.fullmatch(pattern, value) is None)
               for value in identity):
            raise ValueError()
    except (ValueError, TypeError):
        raise ReleaseError("capture identity is unverifiable", EXIT_READINESS) from None
    return identity[0], identity[1]


def _verify_capture_readiness(
    runner: Runner, transport: dict[str, str], previous_identity: tuple[str | None, str | None],
) -> None:
    """Poll runner readiness. A failed browser launch is not started again."""

    deadline = time.monotonic() + CAPTURE_READINESS_DEADLINE_SECONDS
    while True:
        state = ssh(
            runner, **transport, remote_command=cmd_remote_capture_readiness_gate(previous_identity)
        ).strip()
        if state == "readiness=ready":
            return
        if state in (
            "readiness=failed",
            "readiness=browser_unavailable",
            "readiness=needs_admin",
        ):
            raise ReleaseError("capture readiness failed", EXIT_SERVICE_TERMINAL)
        if state != "readiness=starting":
            raise ReleaseError("capture readiness is unverifiable", EXIT_READINESS)
        if time.monotonic() >= deadline:
            raise ReleaseError(
                "capture readiness deadline exceeded", EXIT_READINESS_TIMEOUT
            )
        _poll_until(deadline)


def _cmd_capture_transition(args: argparse.Namespace, runner: Runner) -> int:
    release_sha = args.release
    validate_release_sha(release_sha)
    if not args.yes:
        raise ReleaseError("capture transition requires --yes to confirm", EXIT_USAGE)
    transport = _resolve_transport(args)
    identity = capture_runtime_identity(runner, release_sha)
    target = release_dir(release_sha)

    ssh(runner, **transport, remote_command=cmd_remote_test_exists(target))
    installed = read_release_markers(runner, transport, target)
    if installed.sha != release_sha:
        raise ReleaseError("installed release SHA does not match", EXIT_SOURCE_GATE)
    manifest = installed.manifest
    if manifest is None:
        raise ReleaseError(
            "installed release manifest is absent", EXIT_SOURCE_GATE
        )
    if manifest_release_sha(manifest) != release_sha:
        raise ReleaseError("installed release manifest does not match", EXIT_SOURCE_GATE)
    for key, expected in identity.items():
        if manifest.get(key) != expected:
            raise ReleaseError("capture runtime identity does not match", EXIT_SOURCE_GATE)

    web_path = ssh(
        runner, **transport, remote_command=cmd_remote_readlink_current()
    ).strip()
    validate_remote_path(web_path, RELEASE_ROOT)
    web_sha = read_release_markers(runner, transport, web_path).sha

    capture_link = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_read_optional_link(CAPTURE_CURRENT),
    ).strip()
    if capture_link != "absent":
        validate_remote_path(capture_link, RELEASE_ROOT)
        current_capture = read_release_markers(
            runner, transport, capture_link
        ).manifest
        if current_capture is None:
            raise ReleaseError(
                "capture release manifest is absent", EXIT_SOURCE_GATE
            )
        if (
            current_capture.get("capture_bridge_protocol")
            != identity["capture_bridge_protocol"]
        ):
            raise ReleaseError(
                "capture bridge protocol is not compatible", EXIT_SOURCE_GATE
            )

    _drain_capture_work(runner, transport)
    _enforce_capture_brake(runner, transport)
    previous_identity = _snapshot_capture_identity(runner, transport)
    ssh(runner, **transport, remote_command=cmd_remote_atomic_switch_capture(target))
    try:
        ssh(runner, **transport, remote_command=cmd_remote_restart_capture_runner())
    except ReleaseError as exc:
        print(format_release_pointers(web_sha, release_sha))
        raise ReleaseError("capture runner restart failed", EXIT_READINESS) from exc
    try:
        _verify_capture_readiness(runner, transport, previous_identity)
    except ReleaseError as exc:
        print(format_release_pointers(web_sha, release_sha))
        raise ReleaseError(
            f"capture readiness failed without another browser launch ({exc})",
            exc.exit_code,
        ) from exc

    label = "activate-capture" if args.command == "activate-capture" else "rollback-capture"
    print(f"kronika-release {label} complete: {release_sha}")
    print(format_release_pointers(web_sha, release_sha))
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
