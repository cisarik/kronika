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

RELEASE_SHA_MARKER = ".kronika-release-sha"
RELEASE_MANIFEST_MARKER = ".kronika-release-manifest.json"
# The spellings a release artefact has ever been written under. These two tables
# are frozen data rather than a derivation from the writer constants above: a
# release written before the durable-writer cut must still resolve after that
# cut, so every reader resolves through these tables and never through a literal
# or through the current writer name. The writers now emit the canonical
# spellings; the former spellings stay in the tables permanently, because
# historical release trees carry them and are read during rollback.
ACCEPTED_RELEASE_SHA_MARKERS = (
    ".framenest-release-sha",
    ".kronika-release-sha",
)
ACCEPTED_RELEASE_MANIFEST_MARKERS = (
    ".framenest-release-manifest.json",
    ".kronika-release-manifest.json",
)
RELEASE_SHA_MANIFEST_KEY = "kronika_release_sha"
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

# Migration-owned remote scratch area. The identity migration prepares this
# directory itself and writes its private remote engine helper there, so the
# routine deployment scratch directory stays exactly the artefact set the
# runbook publishes and the two operations cannot overwrite each other's
# helpers. The shared routine exclusion still serializes both operations.
MIGRATION_SCRATCH_DIRECTORY = "/run/kronika-identity-migration"
MIGRATION_REMOTE_ENGINE = f"{MIGRATION_SCRATCH_DIRECTORY}/kronika_release.py"

# Effective unit properties the installed-unit executable guard reads. Drop-ins
# are part of the effective configuration systemd reports for these properties.
UNIT_EXEC_PROPERTIES = ("ExecStart", "ExecStartPre")

# Suffixes systemd loads as units. A tracked artifact with another suffix is a
# configuration file, not a unit: it is observed by file existence, copied to
# its canonical name, and never stopped, started or scheduled.
SYSTEMD_UNIT_SUFFIXES = (".service", ".timer", ".socket", ".target")

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

# Every exit status this engine can return, so the layout and migration
# refusals are distinct from transport and gate failures.
EXIT_LAYOUT = 26
EXIT_MIGRATION = 27


# ---------------------------------------------------------------------------
# Accepted web host layouts
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WebLayout:
    """One accepted effective web-service layout on the host.

    ``key`` names the transition-state axis this layout describes, never a
    preference. Routine operations select a layout from the host's validated
    effective service configuration; they never assume one.
    """

    key: str
    service: str
    user: str
    group: str
    release_root: str
    current: str
    env_file: str
    state_root: str
    cache_root: str
    runtime_root: str
    socket_path: str

    def release_dir(self, sha: str) -> str:
        return f"{self.release_root}/{sha}"

    def staging_dir(self, sha: str) -> str:
        return f"{self.release_root}/{sha}.staging"


# The accepted former layout. Its values are the routine host constants, and
# this cut does not change any of them; cut C6 owns that move. Keeping the data
# in one object beside the canonical target lets routine operations select a
# layout instead of assuming this one.
OLD_WEB_LAYOUT = WebLayout(
    key="old",
    service=SERVICE,
    user=SERVICE_USER,
    group=SERVICE_GROUP,
    release_root=RELEASE_ROOT,
    current=CURRENT,
    env_file=ENV_FILE,
    state_root="/var/lib/framenest",
    cache_root="/var/cache/framenest",
    runtime_root="/run/framenest",
    socket_path="/run/framenest/framenest.sock",
)

# The canonical target layout ADR-0085 names. Cut C4-B prepares it; C6 installs
# it. Nothing here is active until C6.
NEW_WEB_LAYOUT = WebLayout(
    key="new",
    service="kronika.service",
    user="kronika",
    group="kronika",
    release_root="/opt/kronika/releases",
    current="/opt/kronika/current",
    env_file="/etc/kronika/kronika.env",
    state_root="/var/lib/kronika",
    cache_root="/var/cache/kronika",
    runtime_root="/run/kronika",
    socket_path="/run/kronika/kronika.sock",
)

WEB_LAYOUTS = (OLD_WEB_LAYOUT, NEW_WEB_LAYOUT)

# The two accepted capture pointers. The capture account, units, state, token,
# profile, protocol and environment paths are already canonical and
# deliberately asymmetric with the web identity; only this pointer's location
# moves, and cut C8-A owns that move.
OLD_CAPTURE_CURRENT = CAPTURE_CURRENT
NEW_CAPTURE_CURRENT = "/opt/kronika/capture-current"
CAPTURE_POINTERS = (
    ("old", OLD_CAPTURE_CURRENT, OLD_WEB_LAYOUT.release_root),
    ("new", NEW_CAPTURE_CURRENT, NEW_WEB_LAYOUT.release_root),
)


# ---------------------------------------------------------------------------
# Identity migration constants (C4-B repository machinery; C6 executes it)
# ---------------------------------------------------------------------------

# Migration control state is root-only and deliberately outside every state
# root the plan copies. A path below `/var/lib/kronika` made the state-copy
# destination exist before `copy_state` required it absent, so the journal is a
# sibling of the canonical state root rather than a child of it.
MIGRATION_DIRECTORY = "/var/lib/kronika-identity-migration"
MIGRATION_JOURNAL_PATH = f"{MIGRATION_DIRECTORY}/journal.json"
MIGRATION_RECOVERY_MANIFEST_PATH = f"{MIGRATION_DIRECTORY}/recovery-manifest.json"
MIGRATION_JOURNAL_VERSION = 2
MIGRATION_PLAN_VERSION = "kronika-identity-migration-plan-v1"
MIGRATION_RECEIPT_PHASE_KEY = "phase"

# The ordered apply sequence. A failure after every one of these phases must
# select a recovery branch; the phase after which the new application can
# admit writes is the cutover boundary.
MIGRATION_PHASE_QUIESCE = "quiesce"
MIGRATION_PHASE_VERIFY_CAPTURE = "verify-capture"
MIGRATION_PHASE_CHECKPOINT = "checkpoint"
MIGRATION_PHASE_COPY_STATE = "copy-state"
MIGRATION_PHASE_TRANSFORM_ENVIRONMENT = "transform-environment"
MIGRATION_PHASE_PRESERVE_ANCILLARY = "preserve-ancillary"
MIGRATION_PHASE_PREPARE_RELEASE = "prepare-release-environment"
MIGRATION_PHASE_RENAME_ACCOUNT = "rename-account"
MIGRATION_PHASE_INSTALL_UNITS = "install-units"
MIGRATION_PHASE_VERIFY_UNITS = "verify-effective-units"
MIGRATION_PHASE_SWITCH_CURRENT = "switch-current"
MIGRATION_PHASE_START_SERVICE = "start-service"
MIGRATION_PHASE_REPLACE_INGRESS = "replace-tailscale-handler"
MIGRATION_PHASE_VERIFY_INGRESS = "verify-ingress"
MIGRATION_PHASE_RESUME_WRITERS = "resume-writers"
MIGRATION_PHASES = (
    MIGRATION_PHASE_QUIESCE,
    MIGRATION_PHASE_VERIFY_CAPTURE,
    MIGRATION_PHASE_CHECKPOINT,
    MIGRATION_PHASE_COPY_STATE,
    MIGRATION_PHASE_TRANSFORM_ENVIRONMENT,
    MIGRATION_PHASE_PRESERVE_ANCILLARY,
    MIGRATION_PHASE_PREPARE_RELEASE,
    MIGRATION_PHASE_RENAME_ACCOUNT,
    MIGRATION_PHASE_INSTALL_UNITS,
    MIGRATION_PHASE_VERIFY_UNITS,
    MIGRATION_PHASE_SWITCH_CURRENT,
    MIGRATION_PHASE_START_SERVICE,
    MIGRATION_PHASE_REPLACE_INGRESS,
    MIGRATION_PHASE_VERIFY_INGRESS,
    MIGRATION_PHASE_RESUME_WRITERS,
)
# A completed ``start-service`` means the new application is running and may
# already have admitted writes. Every phase at or after it is post-write on the
# recovery side, even though the migration itself is not finished.
MIGRATION_CUTOVER_PHASE = MIGRATION_PHASE_START_SERVICE

# Repository source artifacts the migration installs for the canonical target
# layout. The mapping is derived from the retention ledger's retired-basename
# set; entries whose canonical counterpart already exists are listed with
# ``None`` and are not copied.
MIGRATION_UNIT_ARTIFACTS = (
    ("framenest.service", "kronika.service", True),
    ("framenest-catalog-backup.service", "kronika-catalog-backup.service", False),
    ("framenest-catalog-backup.timer", "kronika-catalog-backup.timer", False),
    ("framenest-catalog-offdevice.service", "kronika-catalog-offdevice.service", False),
    ("framenest-catalog-offdevice.timer", "kronika-catalog-offdevice.timer", False),
    ("framenest-ai-credential-nvidia-nim.conf", "kronika-ai-credential-nvidia-nim.conf", False),
    ("framenest-ai-credential-opencode-go.conf", "kronika-ai-credential-opencode-go.conf", False),
    ("framenest-ai-credential-vercel-ai-gateway.conf", "kronika-ai-credential-vercel-ai-gateway.conf", False),
    ("framenest-research-credential.conf", "kronika-research-credential.conf", False),
)
MIGRATION_ENVIRONMENT_ARTIFACT = ("framenest.env.example", "kronika.env.example")
MIGRATION_EXPORT_ARTIFACT = (
    "deploy/ubuntu/framenest-catalog-export-v1",
    "deploy/ubuntu/kronika-catalog-export-v1",
)
# Installed host paths of the export facility, existence-only preflight facts.
MIGRATION_EXPORT_INSTALLED_CANDIDATES = (
    "/usr/local/libexec/framenest-catalog-export-v1",
    "/usr/local/libexec/kronika-catalog-export-v1",
)
MIGRATION_SUDO_RULE_CANDIDATES = (
    "/etc/sudoers.d/framenest-catalog-export-v1",
    "/etc/sudoers.d/kronika-catalog-export-v1",
)

# Product-owned host path roots that this migration moves. A value outside
# these roots is a custom or foreign path and is preserved, not rewritten.
MIGRATION_OLD_TO_NEW_ROOTS = (
    ("/var/lib/framenest", "/var/lib/kronika"),
    ("/var/cache/framenest", "/var/cache/kronika"),
    ("/run/framenest", "/run/kronika"),
    ("/etc/framenest", "/etc/kronika"),
    ("/opt/framenest", "/opt/kronika"),
)
# Named frozen residues that must never be renamed, checked before any root
# mapping. ``/opt/framenest/tooling`` keeps the former spelling by ADR-0085.
MIGRATION_FROZEN_PATH_PREFIXES = (
    "/mnt/framenest-catalog-offdevice",
    "/opt/framenest/tooling",
)
# Exact path segments and filenames whose canonical spelling replaces the
# former one inside a moved root. A value that merely contains the token
# elsewhere is preserved, not rewritten.
MIGRATION_SEGMENT_RENAMES = {
    "framenest": "kronika",
    "framenest.sock": "kronika.sock",
    "framenest.env": "kronika.env",
}

# Environment keys whose value is a filesystem path. Derived by parsing the
# settings model (``kronika.configuration.KronikaSettings`` path fields), the
# environment template, and the code that consumes each key outside the model
# (the catalog backup roots and the AI configuration path).
IDENTITY_ENVIRONMENT_PATH_SUFFIXES = frozenset(
    {
        "DATABASE_PATH",
        "GALLERY_PREVIEW_CACHE_PATH",
        "COVER_STORAGE_ROOT",
        "COVER_THUMBNAIL_CACHE_PATH",
        "AI_CONFIG_PATH",
        "CATALOG_BACKUP_ROOT",
        "CATALOG_RESTORE_VERIFY_ROOT",
        "CATALOG_BACKUP_OPS_ROOT",
        "YOUTUBE_ACQUISITION_ROOT",
        "X_ACQUISITION_ROOT",
        "UPLOAD_QUARANTINE_ROOT",
        "RUNTIME_SETTINGS_PATH",
        "UDS_PATH",
        "ENV_FILE",
    }
)

# Environment keys whose value is opaque: never path-transformed and never
# inspected beyond presence. Derived from the same three sources.
IDENTITY_ENVIRONMENT_OPAQUE_SUFFIXES = frozenset(
    {
        "HOST",
        "PORT",
        "API_KEY",
        "UPLOAD_PUBLICATION_LIBRARY_ID",
        "UPLOAD_MAX_TOTAL_BYTES",
        "UPLOAD_MAX_PATCH_BYTES",
        "UPLOAD_SESSION_TTL_SECONDS",
        "UPLOAD_MIN_FREE_SPACE_RESERVE_BYTES",
        "YOUTUBE_ACQUISITION_MAX_STAGING_BYTES",
        "YOUTUBE_REQUEST_MAX_ACTIVE_PER_USER",
        "YOUTUBE_REQUEST_MAX_GLOBAL_ACTIVE",
        "YOUTUBE_REQUEST_MAX_SUBMITS_PER_HOUR",
        "YOUTUBE_REQUEST_MAX_FAILED_PER_24H",
        "YOUTUBE_REQUEST_MAX_PRIVATE_ITEMS",
        "YOUTUBE_REQUEST_MAX_PRIVATE_BYTES",
        "X_ACQUISITION_MAX_STAGING_BYTES",
        "X_REQUEST_MAX_ACTIVE_PER_USER",
        "X_REQUEST_MAX_GLOBAL_ACTIVE",
        "X_REQUEST_MAX_SUBMITS_PER_HOUR",
        "X_REQUEST_MAX_FAILED_PER_24H",
        "AI_PROVIDER_ID",
        "AI_MODEL_ID",
        "INGRESS_MODE",
        "EXTERNAL_ORIGIN",
        "COMPANION_EXTENSION_ORIGINS",
        "IDENTITY_MAP",
        "LOCAL_OWNER_LOGIN",
        "AUTOMATIC_MEDIA_ANALYSIS_ENABLED",
        "AUTOMATIC_MEDIA_ANALYSIS_MAX_ATTEMPTS",
        "CATALOG_BACKUP_KEEP_AUTO",
        "CATALOG_OFFDEVICE_DESTINATION_ID",
    }
)


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


def release_dir(sha: str, layout: WebLayout = OLD_WEB_LAYOUT) -> str:
    return layout.release_dir(sha)


def staging_dir(sha: str, layout: WebLayout = OLD_WEB_LAYOUT) -> str:
    return layout.staging_dir(sha)


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


def cmd_remote_write_file_atomic(path: str, sha256: str) -> str:
    """Write beside the destination, verify it, then rename it into place.

    An interruption leaves the previous file intact instead of a partial one.
    """
    quoted = shlex.quote(path)
    pending = shlex.quote(f"{path}.next")
    return (
        "set -e\n"
        f"sudo -n sh -c 'umask 077; cat > {pending}'\n"
        f"test \"$(sudo -n sha256sum {pending} | cut -d' ' -f1)\" = "
        f"{shlex.quote(sha256)}\n"
        f"sudo -n mv -T {pending} {quoted}\n"
    )


def cmd_remote_readlink_current(layout: WebLayout = OLD_WEB_LAYOUT) -> str:
    return f"sudo -n readlink -n {layout.current}"


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


def cmd_remote_unit_execution_properties(service: str = SERVICE) -> str:
    """Effective ``ExecStart`` and ``ExecStartPre``, drop-ins included."""
    properties = " ".join(
        f"--property={name}" for name in UNIT_EXEC_PROPERTIES
    )
    return f"sudo -n systemctl show {properties} {service}"


def cmd_remote_service_is_active(service: str = SERVICE) -> str:
    return f"sudo -n systemctl is-active {service}"


def cmd_remote_service_active_state(service: str = SERVICE) -> str:
    return f"sudo -n systemctl show -p ActiveState --value {service}"


def cmd_remote_service_result(service: str = SERVICE) -> str:
    return f"sudo -n systemctl show -p Result --value {service}"


def cmd_remote_systemd_working_directory(service: str = SERVICE) -> str:
    return f"sudo -n systemctl show -p WorkingDirectory --value {service}"


def cmd_remote_service_fragment_path(service: str = SERVICE) -> str:
    return f"sudo -n systemctl show -p FragmentPath --value {service}"


def cmd_remote_restart_service(service: str = SERVICE) -> str:
    return f"sudo -n systemctl restart {service}"


def cmd_remote_journal(service: str = SERVICE) -> str:
    return (
        f"sudo -n journalctl -u {service} -n 40 --no-pager "
        "--output=cat | grep -Ev '^$' || true"
    )


def cmd_remote_df_capacity(layout: WebLayout = OLD_WEB_LAYOUT) -> str:
    return f"sudo -n df -Pk {layout.release_root.rsplit('/', 1)[0]} | tail -n 1"


def cmd_remote_capture_current(
    target_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    quoted = shlex.quote(target_path)
    return (
        "set -e\n"
        f"prev=$(sudo -n readlink -n {layout.current})\n"
        + 'test -n "$prev"\n'
        + "sudo -n sh -c 'printf %s \"$1\" > " + quoted + "' sh \"$prev\"\n"
    )


def service_account_prefix(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return (
        f"sudo -n -u {layout.user} --chdir={shlex.quote(release_path)} "
        f"env FRAMENEST_ENV_FILE={layout.env_file} "
        f"{shlex.quote(release_path)}/.venv/bin"
    )


def cmd_remote_db_status(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return f"{service_account_prefix(release_path, layout)}/framenest-db status"


def cmd_remote_backup_status(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return f"{service_account_prefix(release_path, layout)}/framenest-backup status"


def cmd_remote_production_cli(
    release_path: str, operation: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
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
        f"--uid={layout.user} --gid={layout.group} "
        f"--working-directory={shlex.quote(release_path)} "
        f"--property=EnvironmentFile={shlex.quote(layout.env_file)} "
        f"{shlex.quote(f'{release_path}/.venv/bin/framenest-production')} "
        f"{operation}"
    )


def cmd_remote_check_database_ready(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return cmd_remote_production_cli(release_path, "check-database-ready", layout)


def cmd_remote_check_health(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return cmd_remote_production_cli(release_path, "check-health", layout)


def cmd_remote_run_scheduled_backup(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return f"{service_account_prefix(release_path, layout)}/framenest-backup run-scheduled"


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


def cmd_remote_atomic_switch(
    release_path: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str:
    return (
        "set -e\n"
        f"sudo -n ln -s {shlex.quote(release_path)} {layout.current}.next\n"
        f"sudo -n mv -T {layout.current}.next {layout.current}"
    )


def cmd_remote_atomic_switch_capture(
    release_path: str, pointer: str = CAPTURE_CURRENT
) -> str:
    return (
        "set -e\n"
        f"sudo -n ln -s {shlex.quote(release_path)} {pointer}.next\n"
        f"sudo -n mv -T {pointer}.next {pointer}"
    )


def cmd_remote_restart_capture_runner() -> str:
    return f"sudo -n systemctl restart {CAPTURE_RUNNER_SERVICE}"


def cmd_remote_read_optional_link(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"elif sudo -n test -L {quoted}; then sudo -n readlink -n {quoted}; "
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

def accepted_release_roots() -> tuple[str, ...]:
    """Every accepted release root, so a remote helper can serve both layouts.

    The transferred ``_remote`` helpers run on the host with no transport to
    select a layout from. Accepting both accepted roots keeps the former layout
    working and lets the canonical layout work after C6 without teaching the
    helper a preference. The active constant is read at call time, so a layout
    constant change in C6 takes effect here without a second edit.
    """
    roots: list[str] = []
    for root in (RELEASE_ROOT, NEW_WEB_LAYOUT.release_root):
        if root not in roots:
            roots.append(root)
    return tuple(roots)


def remote_extract(runner: Runner, archive: str, destination: str) -> None:
    validate_remote_path(archive, REMOTE_DEPLOY_DIR, *accepted_release_roots())
    validate_remote_path(destination, *accepted_release_roots())
    try:
        extract_validated_archive(Path(archive), destination)
    except tarfile.TarError as exc:
        raise ReleaseError("archive extraction failed", EXIT_UNSAFE_ARCHIVE) from exc


def relocate_venv_shebangs(staging_path: str, final_path: str) -> None:
    """Rewrite staging-prefix paths under ``.venv`` to the final release path.

    Covers console-script shebangs and editable-install metadata such as
    ``.pth`` and ``direct_url.json``.
    """
    validate_remote_path(staging_path, *accepted_release_roots())
    validate_remote_path(final_path, *accepted_release_roots())
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
    runner: Runner, transport: dict[str, str], layout: WebLayout = OLD_WEB_LAYOUT
) -> ResolvedReleaseMarkers:
    raw = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_readlink_current(layout),
    )
    current_path = raw.strip()
    if not current_path.startswith(layout.release_root):
        raise ReleaseError("current release path is unexpected", EXIT_TRANSPORT)
    return read_release_markers(runner, transport, current_path)


def read_optional_release_sha(
    runner: Runner,
    transport: dict[str, str],
    pointer: str,
    release_root: str = RELEASE_ROOT,
) -> str:
    raw = ssh(
        runner, **transport, remote_command=cmd_remote_read_optional_link(pointer)
    ).strip()
    if raw == "query-failed":
        raise ReleaseError("release pointer state is unverifiable", EXIT_LAYOUT)
    if raw == "absent":
        return "absent"
    validate_remote_path(raw, release_root)
    return read_release_markers(runner, transport, raw).sha


def read_backup_readiness(
    runner: Runner,
    transport: dict[str, str],
    release_path: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> str:
    raw = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_backup_status(release_path, layout),
    )
    payload = parse_json_status(raw)
    readiness = payload.get("restore_readiness")
    if not isinstance(readiness, str):
        raise ReleaseError("backup readiness unavailable", EXIT_BACKUP_NOT_READY)
    return readiness


def read_db_current_revision(
    runner: Runner,
    transport: dict[str, str],
    release_path: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> str:
    raw = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_db_status(release_path, layout),
    )
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
    runner: Runner,
    transport: dict[str, str],
    required_bytes: int,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> None:
    raw = ssh(
        runner, **transport, remote_command=cmd_remote_df_capacity(layout)
    ).strip()
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


def release_scoped_console_script(
    executable: str, layout: WebLayout = OLD_WEB_LAYOUT
) -> str | None:
    """Return the release-relative console script a unit executable names.

    Only a release console script under a ``.venv/bin`` directory of the current
    pointer or of a concrete release directory maps onto the target release.
    Anything else - a system interpreter, an absolute path outside the release
    root, or a release file that is not a console script - is an unknown
    effective execution form and is refused rather than skipped.
    """
    for prefix, expects_release_directory in (
        (layout.current + "/", False),
        (layout.release_root + "/", True),
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
    runner: Runner,
    transport: dict[str, str],
    release_path: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> tuple[str, ...]:
    """Require every effective unit executable to exist in ``release_path``.

    This runs before any pointer switch and before any restart, so a release
    whose console scripts the installed unit cannot execute is refused while the
    running release still serves. Drop-ins contribute, because systemd reports
    the effective configuration for the property.
    """
    output = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_unit_execution_properties(layout.service),
    )
    resolved = unit_executables_from_show(output)
    if not resolved.get("ExecStart"):
        raise ReleaseError(
            "installed unit declares no executable command", EXIT_UNIT_EXEC_GUARD
        )
    required: set[str] = set()
    for name in UNIT_EXEC_PROPERTIES:
        for executable in resolved.get(name, ()):
            scoped = release_scoped_console_script(executable, layout)
            if scoped is None:
                raise ReleaseError(
                    "installed unit execution form is not a release console script",
                    EXIT_UNIT_EXEC_GUARD,
                )
            required.add(scoped)
    for relative in sorted(required):
        candidate = f"{release_path}/{relative}"
        validate_remote_path(candidate, layout.release_root)
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
# Effective host layout selection (fail-closed)
# ---------------------------------------------------------------------------

#: One ``Key=Value`` line of an effective unit property from ``systemctl show``.
UNIT_PROPERTY_LINE = re.compile(
    r"^(LoadState|ActiveState|UnitFileState|User|Group|WorkingDirectory|ExecStart)=(.*)$"
)

#: The systemd active states that mean a candidate may be serving traffic.
WEB_ACTIVE_STATES = ("active", "activating", "reloading")


@dataclass(frozen=True)
class WebLayoutReading:
    """One candidate web unit's validated effective configuration."""

    layout: WebLayout
    load_state: str
    active_state: str
    unit_file_state: str
    user: str
    group: str
    working_directory: str
    executables: tuple[str, ...]
    unrecognised: tuple[str, ...]

    @property
    def installed(self) -> bool:
        return self.load_state not in ("", "not-found")

    @property
    def active(self) -> bool:
        return self.active_state in WEB_ACTIVE_STATES

    @property
    def enabled(self) -> bool:
        return self.unit_file_state == "enabled"

    @property
    def recognises_layout(self) -> bool:
        return not self.unrecognised


@dataclass(frozen=True)
class HostLayoutState:
    """The selected effective web layout and the capture-pointer axis.

    ``web`` is chosen by :func:`select_web_layout` from validated effective
    service configuration. ``capture_pointer`` is ``old``, ``new`` or ``none``;
    a wrong or ambiguous signal fails rather than selecting a wrong tree.
    """

    web: WebLayout
    readings: tuple[WebLayoutReading, ...]
    capture_pointer: str
    capture_current: str
    capture_release_root: str

    def signal(self) -> str:
        for reading in self.readings:
            if reading.layout.key == self.web.key:
                return f"web:{reading.layout.service}:{reading.active_state}"
        return f"web:{self.web.service}:unknown"


def cmd_remote_layout_probe(service: str) -> str:
    """Effective configuration of one candidate web unit, drop-ins included."""
    properties = (
        "LoadState",
        "ActiveState",
        "UnitFileState",
        "User",
        "Group",
        "WorkingDirectory",
        "ExecStart",
    )
    rendered = " ".join(f"--property={name}" for name in properties)
    return f"sudo -n systemctl show {rendered} {service}"


def _single_property(fields: dict[str, list[str]], name: str) -> str:
    values = fields.get(name, [])
    return values[0] if values else ""


def parse_web_layout_probe(output: str, layout: WebLayout) -> WebLayoutReading:
    """Parse and validate one effective unit configuration against ``layout``.

    A candidate loaded with a configuration that does not name this layout's
    user, group, working directory and ``.venv/bin`` console script is
    ``unrecognised`` and can never be selected.
    """
    fields: dict[str, list[str]] = {
        name: []
        for name in (
            "LoadState",
            "ActiveState",
            "UnitFileState",
            "User",
            "Group",
            "WorkingDirectory",
            "ExecStart",
        )
    }
    for raw in output.splitlines():
        match = UNIT_PROPERTY_LINE.match(raw.strip())
        if match is None:
            continue
        fields[match.group(1)].append(match.group(2))

    load_state = _single_property(fields, "LoadState")
    active_state = _single_property(fields, "ActiveState")
    unit_file_state = _single_property(fields, "UnitFileState")
    user = _single_property(fields, "User")
    group = _single_property(fields, "Group")
    working_directory = _single_property(fields, "WorkingDirectory")
    executables: list[str] = []
    for value in fields.get("ExecStart", []):
        for path in UNIT_EXEC_PATH.findall(value):
            executables.append(_unescape_unit_text(path))

    reasons: list[str] = []
    if load_state not in ("", "not-found", "loaded"):
        reasons.append("effective load state is not a service")
    if load_state == "loaded":
        if user != layout.user:
            reasons.append("effective service user")
        if group not in ("", layout.group):
            reasons.append("effective service group")
        if working_directory != layout.current:
            reasons.append("effective working directory")
        if not executables:
            reasons.append("effective service has no executable command")
        for executable in executables:
            if release_scoped_console_script(executable, layout) is None:
                reasons.append("effective execution form")
                break

    return WebLayoutReading(
        layout=layout,
        load_state=load_state,
        active_state=active_state,
        unit_file_state=unit_file_state,
        user=user,
        group=group,
        working_directory=working_directory,
        executables=tuple(executables),
        unrecognised=tuple(dict.fromkeys(reasons)),
    )


def select_web_layout(readings: Sequence[WebLayoutReading]) -> WebLayout:
    """Select the one installed web layout, or fail closed.

    Selection rules, in order:

    - no installed candidate at all fails;
    - any installed candidate whose effective configuration does not match its
      own layout identity fails, whatever the other candidate says;
    - more than one active candidate fails;
    - exactly one active candidate is selected even when the other candidate is
      installed and enabled but stopped, because the active service is the tree
      that is actually serving;
    - with no active candidate, exactly one installed and enabled candidate is
      selected, because a failed or stopped service keeps its enablement;
    - zero or two enabled candidates fail.

    The selector never falls back to a service name, never prefers the old
    layout, and never guesses from a partial signal.
    """
    installed = [reading for reading in readings if reading.installed]
    if not installed:
        raise ReleaseError(
            "no installed web service layout is recognised", EXIT_LAYOUT
        )
    unrecognised = [reading for reading in installed if not reading.recognises_layout]
    if unrecognised:
        names = ",".join(sorted(reading.layout.service for reading in unrecognised))
        raise ReleaseError(
            f"installed web service layout is unrecognised: {names}", EXIT_LAYOUT
        )
    active = [reading for reading in installed if reading.active]
    if len(active) > 1:
        raise ReleaseError("ambiguous active web service layout", EXIT_LAYOUT)
    if active:
        return active[0].layout
    enabled = [reading for reading in installed if reading.enabled]
    if len(enabled) > 1:
        raise ReleaseError("ambiguous installed web service layout", EXIT_LAYOUT)
    if len(enabled) == 1:
        return enabled[0].layout
    raise ReleaseError("no active or enabled web service layout", EXIT_LAYOUT)


def resolve_capture_pointer(
    runner: Runner, transport: dict[str, str]
) -> tuple[str, str, str]:
    """Return the capture pointer axis, its path and its release root.

    Both candidate pointers are read. Two live pointers are ambiguous and fail;
    no pointer is ``none`` and keeps the former pointer path for a future
    activation.
    """
    present: list[tuple[str, str, str, str]] = []
    for key, pointer, release_root in CAPTURE_POINTERS:
        target = ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_optional_link(pointer),
        ).strip()
        if target == "query-failed":
            raise ReleaseError(
                "capture release pointer state is unverifiable", EXIT_LAYOUT
            )
        if target != "absent":
            present.append((key, pointer, release_root, target))
    if not present:
        return ("none", OLD_CAPTURE_CURRENT, OLD_WEB_LAYOUT.release_root)
    if len(present) > 1:
        raise ReleaseError("ambiguous capture release pointer", EXIT_LAYOUT)
    key, pointer, release_root, target = present[0]
    validate_remote_path(target, release_root)
    return (key, pointer, release_root)


def resolve_host_layout(
    runner: Runner, transport: dict[str, str]
) -> HostLayoutState:
    """Resolve the installed web layout and capture-pointer axis.

    Every routine operation begins here, so no operation can silently assume
    the former service or a moved capture pointer.
    """
    readings = tuple(
        parse_web_layout_probe(
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_layout_probe(layout.service),
            ),
            layout,
        )
        for layout in WEB_LAYOUTS
    )
    web = select_web_layout(readings)
    capture_pointer, capture_current, capture_release_root = resolve_capture_pointer(
        runner, transport
    )
    return HostLayoutState(
        web=web,
        readings=readings,
        capture_pointer=capture_pointer,
        capture_current=capture_current,
        capture_release_root=capture_release_root,
    )


# ---------------------------------------------------------------------------
# Typed environment and path transformation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EnvironmentAssignment:
    """One parsed environment assignment and its typed transformation."""

    key: str
    suffix: str | None
    classification: str
    transformed_key: str
    value: str
    transformed_value: str

    @property
    def changed(self) -> bool:
        return (
            self.transformed_key != self.key
            or self.transformed_value != self.value
        )


@dataclass(frozen=True)
class EnvironmentTransformation:
    """A typed, idempotent transformation of one environment file."""

    assignments: tuple[EnvironmentAssignment, ...]
    text: str

    @property
    def moved_path_keys(self) -> tuple[str, ...]:
        return tuple(
            item.key
            for item in self.assignments
            if item.classification == "path" and item.transformed_value != item.value
        )

    @property
    def preserved_path_keys(self) -> tuple[str, ...]:
        return tuple(
            item.key
            for item in self.assignments
            if item.classification == "path" and item.transformed_value == item.value
        )

    @property
    def opaque_keys(self) -> tuple[str, ...]:
        return tuple(
            item.key for item in self.assignments if item.classification == "opaque"
        )

    @property
    def foreign_keys(self) -> tuple[str, ...]:
        return tuple(
            item.key for item in self.assignments if item.classification == "foreign"
        )

    @property
    def distinct_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.key for item in self.assignments))


def environment_key_suffix(name: str) -> str | None:
    for prefix in (IDENTITY_ENVIRONMENT_PREFIX, COMPATIBLE_ENVIRONMENT_PREFIX):
        if name.startswith(prefix):
            return name[len(prefix):].upper()
    return None


def classify_environment_key(name: str) -> tuple[str | None, str]:
    """Classify one environment key as ``path``, ``opaque`` or ``foreign``.

    A key under neither accepted identity prefix is foreign and is preserved
    verbatim. A key under an accepted prefix with a suffix this engine cannot
    classify is refused by :func:`transform_environment_text` rather than
    renamed or silently passed through.
    """
    suffix = environment_key_suffix(name)
    if suffix is None:
        return None, "foreign"
    if suffix in IDENTITY_ENVIRONMENT_PATH_SUFFIXES:
        return suffix, "path"
    if suffix in IDENTITY_ENVIRONMENT_OPAQUE_SUFFIXES:
        return suffix, "opaque"
    return suffix, "unknown"


def _split_quotes(value: str) -> tuple[str, str, str]:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[0], value[1:-1], value[-1]
    return "", value, ""


def transform_path_value(value: str) -> str:
    """Transform one path value under the migrated roots, or preserve it.

    Frozen residues are always preserved. A value outside every migrated root is
    a custom or foreign path and is preserved. A value already canonical is
    unchanged, which makes the transformation idempotent. Inside a moved root,
    only exact path segments named by ``MIGRATION_SEGMENT_RENAMES`` are
    rewritten; a value that still carries the retired spelling afterwards is
    refused rather than left on an unmigrated path.
    """
    path = value
    if path == "":
        return value
    for frozen in MIGRATION_FROZEN_PATH_PREFIXES:
        if path == frozen or path.startswith(frozen + "/"):
            return value
    moved = False
    for old_root, new_root in MIGRATION_OLD_TO_NEW_ROOTS:
        if path == old_root or path.startswith(old_root + "/"):
            path = new_root + path[len(old_root):]
            moved = True
            break
    if not moved:
        return value
    segments = path.split("/")
    candidate = "/".join(
        MIGRATION_SEGMENT_RENAMES.get(segment, segment) for segment in segments
    )
    if "framenest" in candidate.lower():
        raise ReleaseError(
            "path value under a moved root is not classifiable", EXIT_MIGRATION
        )
    return candidate


def transformed_environment_value(classification: str, value: str) -> str:
    if classification != "path":
        return value
    prefix, inner, suffix = _split_quotes(value)
    if prefix:
        return f"{prefix}{transform_path_value(inner)}{suffix}"
    return transform_path_value(value)


def parse_environment_text(text: str) -> tuple[EnvironmentAssignment, ...]:
    """Parse assignments, failing closed on an unclassified identity key."""
    assignments: list[EnvironmentAssignment] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            continue
        if "=" not in line:
            raise ReleaseError("environment assignment is malformed", EXIT_MIGRATION)
        key, _, value = line.partition("=")
        key = key.strip()
        if not key or any(character.isspace() for character in key):
            raise ReleaseError("environment assignment is malformed", EXIT_MIGRATION)
        suffix, classification = classify_environment_key(key)
        if classification == "unknown":
            raise ReleaseError(
                f"environment key suffix {suffix} is not classified", EXIT_MIGRATION
            )
        if key in seen:
            raise ReleaseError("environment assignment is duplicated", EXIT_MIGRATION)
        seen.add(key)
        if suffix is not None:
            transformed_key = f"{IDENTITY_ENVIRONMENT_PREFIX}{suffix}"
        else:
            transformed_key = key
        assignments.append(
            EnvironmentAssignment(
                key=key,
                suffix=suffix,
                classification=classification,
                transformed_key=transformed_key,
                value=value,
                transformed_value=transformed_environment_value(
                    classification, value
                ),
            )
        )
    return tuple(assignments)


def transform_environment_text(text: str) -> EnvironmentTransformation:
    """Rewrite one environment file with typed keys and typed path values.

    Comments and blank lines are preserved byte-for-byte. Re-running the
    transformation over its own output changes nothing.
    """
    assignments = parse_environment_text(text)
    by_key = {assignment.key: assignment for assignment in assignments}
    transformed_lines: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith(";"):
            transformed_lines.append(raw)
            continue
        key = line.partition("=")[0].strip()
        parsed = by_key[key]
        transformed_lines.append(
            f"{parsed.transformed_key}={parsed.transformed_value}"
        )
    rendered = "\n".join(transformed_lines)
    if text.endswith("\n"):
        rendered += "\n"
    return EnvironmentTransformation(assignments=assignments, text=rendered)


def environment_transformation_is_idempotent(
    transformation: EnvironmentTransformation,
) -> bool:
    return transform_environment_text(transformation.text).text == transformation.text


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
# Identity migration: fixed remote commands
# ---------------------------------------------------------------------------

def cmd_remote_existence(path: str) -> str:
    """``present``, ``absent`` or ``query-failed`` for one path.

    ``sudo -n true`` separates "the privileged query itself failed" from "the
    object does not exist", so a failed query is never read as absence.
    """
    quoted = shlex.quote(path)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"elif sudo -n test -e {quoted}; then printf %s present; "
        "else printf %s absent; fi"
    )


def cmd_remote_read_optional_file(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"elif sudo -n test -e {quoted}; then sudo -n cat {quoted}; "
        "else printf %s absent; fi"
    )


def cmd_remote_make_directory(path: str, mode: str = "0700") -> str:
    return (
        f"sudo -n install -d -o root -g root -m {mode} {shlex.quote(path)}"
    )


def cmd_remote_test_absent_or_fail(path: str) -> str:
    return f"sudo -n test ! -e {shlex.quote(path)}"


def cmd_remote_directory_state(path: str) -> str:
    """Classify a copy destination without touching it.

    Returns ``absent``, ``empty``, ``populated``, ``not-a-directory`` or
    ``query-failed``. A populated unexpected destination is refused by the
    caller rather than overwritten.
    """
    quoted = shlex.quote(path)
    script = (
        f"if [ ! -e {quoted} ]; then printf %s absent; "
        f"elif [ ! -d {quoted} ]; then printf %s not-a-directory; "
        f"elif [ -z \"$(ls -A {quoted} 2>/dev/null)\" ]; then printf %s empty; "
        "else printf %s populated; fi"
    )
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"else sudo -n sh -c {shlex.quote(script)}; fi"
    )


def cmd_remote_copy_tree(source: str, destination: str) -> str:
    """Copy one tree under a moved root; the destination must already exist."""
    return (
        "set -e\n"
        f"sudo -n cp -a {shlex.quote(source)}/. {shlex.quote(destination)}/\n"
    )


def cmd_remote_tree_manifest(path: str) -> str:
    """Stable per-entry type, target, mode, ownership and content manifest.

    Every entry reports its relative path, file type, permission bits and
    owner/group; regular files add their content hash and symlinks add their
    target. Two trees with equal manifests are equal in all five dimensions.
    """
    script = (
        f"cd {shlex.quote(path)}\n"
        "find . -mindepth 1 -print0 | LC_ALL=C sort -z | "
        "while IFS= read -r -d '' entry; do "
        "meta=$(stat -c '%U:%G:%a' \"$entry\"); "
        "if [ -L \"$entry\" ]; then "
        "printf '%s|symlink|%s|%s\\n' \"$entry\" \"$(readlink \"$entry\")\" \"$meta\"; "
        "elif [ -d \"$entry\" ]; then "
        "printf '%s|dir|-|%s\\n' \"$entry\" \"$meta\"; "
        "elif [ -f \"$entry\" ]; then "
        "printf '%s|file|%s|%s\\n' \"$entry\" \"$(sha256sum \"$entry\" | cut -d' ' -f1)\" \"$meta\"; "
        "else "
        "printf '%s|other|-|%s\\n' \"$entry\" \"$meta\"; "
        "fi; done"
    )
    return f"sudo -n sh -c {shlex.quote(script)}"


def cmd_remote_assert_no_retired_assignments(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        f"if sudo -n grep -Eq '^{COMPATIBLE_ENVIRONMENT_PREFIX}' {quoted}; "
        "then printf %s retired-assignment-present; "
        "else printf %s canonical-only; fi"
    )


def cmd_remote_stop_unit(unit: str) -> str:
    return f"sudo -n systemctl stop {unit}"


def cmd_remote_start_unit(unit: str) -> str:
    return f"sudo -n systemctl start {unit}"


def cmd_remote_enable_unit(unit: str) -> str:
    return f"sudo -n systemctl enable {unit}"


def cmd_remote_disable_unit(unit: str) -> str:
    return f"sudo -n systemctl disable {unit}"


def cmd_remote_daemon_reload() -> str:
    return "sudo -n systemctl daemon-reload"


def cmd_remote_unit_state(unit: str) -> str:
    """Observed load, enablement and activity of one installed unit.

    ``query-failed`` means the privileged query itself failed; an absent unit
    reports an empty or ``not-found`` load state instead.
    """
    quoted = shlex.quote(unit)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; exit 0; fi\n"
        f"load=$(sudo -n systemctl show -p LoadState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"enabled=$(sudo -n systemctl show -p UnitFileState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"active=$(sudo -n systemctl show -p ActiveState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        "printf 'load=%s enabled=%s active=%s' \"$load\" \"$enabled\" \"$active\""
    )


def cmd_remote_list_directory(path: str) -> str:
    quoted = shlex.quote(path)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"elif sudo -n test -d {quoted}; then sudo -n ls -1 {quoted}; "
        "else printf %s absent; fi"
    )


def cmd_remote_account_record() -> str:
    """UID, GID and home of the former web account, or ``absent``."""
    return (
        "record=$(getent passwd framenest || true); "
        'if [ -z "$record" ]; then printf %s absent; else '
        "printf '%s %s %s' \"$(id -u framenest)\" \"$(id -g framenest)\" "
        "\"$(getent passwd framenest | cut -d: -f6)\"; fi"
    )


def cmd_remote_new_account_absent() -> str:
    return (
        "if getent passwd kronika >/dev/null || getent group kronika >/dev/null; "
        "then printf %s present; else printf %s absent; fi"
    )


def cmd_remote_rename_group() -> str:
    return "sudo -n groupmod -n kronika framenest"


def cmd_remote_rename_user(home: str | None) -> str:
    if home:
        return (
            f"sudo -n usermod -l kronika -d {shlex.quote(home)} framenest"
        )
    return "sudo -n usermod -l kronika framenest"


def cmd_remote_assert_account_renamed(uid: int, gid: int) -> str:
    return (
        "set -e\n"
        "sudo -n getent passwd kronika >/dev/null\n"
        "sudo -n getent group kronika >/dev/null\n"
        f"test \"$(id -u kronika)\" = {uid}\n"
        f"test \"$(id -g kronika)\" = {gid}\n"
        "! getent passwd framenest >/dev/null\n"
        "! getent group framenest >/dev/null\n"
    )


def cmd_remote_account_processes_absent(user: str) -> str:
    """Process count for one account, or ``query-failed``.

    A failing ``ps`` (unknown account, broken query) must not be read as the
    successful absence of processes.
    """
    quoted = shlex.quote(user)
    return (
        f"if ! ps -u {quoted} -o pid= >/dev/null 2>&1; then "
        "printf %s query-failed; "
        f"else count=$(ps -u {quoted} -o pid= 2>/dev/null | wc -l); "
        'printf %s "$count"; fi'
    )


def cmd_remote_tailscale_serve_status() -> str:
    return "sudo -n tailscale serve status --json"


def cmd_remote_tailscale_replace(mount: str, target: str) -> str:
    if not mount.endswith(":443/"):
        raise ReleaseError(
            "ingress handler mount is not the documented default", EXIT_MIGRATION
        )
    return f"sudo -n tailscale serve --bg {shlex.quote(target)}"


def cmd_remote_validate_sudoers(path: str) -> str:
    return f"sudo -n visudo -cf {shlex.quote(path)}"


def cmd_remote_rmdir(path: str) -> str:
    """Remove one empty directory only; never a tree."""
    return f"sudo -n rmdir {shlex.quote(path)}"


def cmd_remote_unit_effective_state(unit: str) -> str:
    """Observed effective fragment, drop-ins, enablement and activity.

    ``DropInPaths`` is reported as one ``dropin=<path>`` line per effective
    drop-in, so an installation outside the guessed ``/etc`` filename is still
    observed exactly as systemd loaded it. ``query-failed`` means the
    privileged query itself failed; an absent unit reports an empty or
    ``not-found`` load state instead.
    """
    quoted = shlex.quote(unit)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; exit 0; fi\n"
        f"load=$(sudo -n systemctl show -p LoadState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"fragment=$(sudo -n systemctl show -p FragmentPath --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"dropins=$(sudo -n systemctl show -p DropInPaths --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"enabled=$(sudo -n systemctl show -p UnitFileState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        f"active=$(sudo -n systemctl show -p ActiveState --value {quoted} "
        "2>/dev/null) || { printf %s query-failed; exit 0; }\n"
        "printf 'load=%s\\nfragment=%s\\nenabled=%s\\nactive=%s\\n' "
        "\"$load\" \"$fragment\" \"$enabled\" \"$active\"\n"
        "for dropin in $dropins; do printf 'dropin=%s\\n' \"$dropin\"; done"
    )


def cmd_remote_root_file_evidence(path: str) -> str:
    """Owner, group, mode and content hash of one installed root file.

    The classification distinguishes an absent object, an unsafe object (a
    symlink or non-regular file) and a failed privileged query, so none of them
    is ever read as a mismatch of a file that is actually installed.
    """
    quoted = shlex.quote(path)
    return (
        "if ! sudo -n true 2>/dev/null; then printf %s query-failed; "
        f"elif [ ! -e {quoted} ]; then printf %s absent; "
        f"elif [ -L {quoted} ] || [ ! -f {quoted} ]; then printf %s unsafe; "
        f"else printf '%s %s' \"$(sudo -n stat -c '%U:%G:%a' {quoted})\" "
        f"\"$(sudo -n sha256sum {quoted} | cut -d' ' -f1)\"; fi"
    )


def cmd_remote_prepare_migration_release(old_release: str, staging: str) -> str:
    """Copy the retained release into the new staging tree, then drop its venv."""
    return (
        "set -e\n"
        f"sudo -n cp -a {shlex.quote(old_release)}/. {shlex.quote(staging)}/\n"
        f"sudo -n rm -rf {shlex.quote(staging)}/.venv\n"
    )


def cmd_remote_verify_migration_release(
    staging: str, final_path: str
) -> str:
    """The final release must carry no staging prefix in its venv metadata."""
    return (
        "set -e\n"
        f"test -d {shlex.quote(final_path)}/.venv\n"
        f"! sudo -n grep -RFq {shlex.quote(staging)} {shlex.quote(final_path)}/.venv\n"
        f"sudo -n test -x {shlex.quote(final_path)}/.venv/bin/framenest-production\n"
    )


# ---------------------------------------------------------------------------
# Identity migration: plan, journal, host and orchestration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ObservedUnit:
    """One effective unit installation, wherever its fragment is installed."""

    name: str
    installed: bool
    load: str
    fragment_path: str
    dropin_paths: tuple[str, ...]
    enabled: str
    active: str

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "installed": self.installed,
            "load": self.load,
            "fragment_path": self.fragment_path,
            "dropin_paths": list(self.dropin_paths),
            "enabled": self.enabled,
            "active": self.active,
        }


@dataclass(frozen=True)
class DropinInstallation:
    """One observed drop-in and the transformed bytes the plan is bound to."""

    unit: str
    source: str
    text: str


@dataclass(frozen=True)
class MigrationObservation:
    """Sanitized preflight facts about one host."""

    release_sha: str
    old_layout: WebLayout
    new_layout: WebLayout
    environment_text: str
    copy_sources: tuple[tuple[str, str], ...]
    installed_units: tuple[str, ...]
    unit_observations: tuple[ObservedUnit, ...]
    dropin_installations: tuple[DropinInstallation, ...]
    credential_sources: tuple[str, ...]
    export_installed: str | None
    sudo_rule_installed: str | None
    sudo_rule_text: str | None
    account_uid: int
    account_gid: int
    account_home: str
    current_release_sha: str
    capture_pointer: str
    capture_release_sha: str | None
    tailscale_handler_count: int
    tailscale_old_handlers: int
    artifact_identity: tuple[tuple[str, str], ...]
    required_scripts: tuple[str, ...]


@dataclass(frozen=True)
class MigrationPlan:
    """The exact, validated plan one apply is bound to by its digest."""

    release_sha: str
    old_layout: WebLayout
    new_layout: WebLayout
    environment: EnvironmentTransformation
    copy_moves: tuple[tuple[str, str], ...]
    installed_units: tuple[str, ...]
    unit_observations: tuple[ObservedUnit, ...]
    dropin_installations: tuple[DropinInstallation, ...]
    credential_sources: tuple[str, ...]
    export_installed: str | None
    sudo_rule_installed: str | None
    sudo_rule_text: str | None
    account_uid: int
    account_gid: int
    account_home: str
    current_release_sha: str
    capture_pointer: str
    capture_release_sha: str | None
    tailscale_handler_count: int
    tailscale_old_handlers: int
    artifact_identity: tuple[tuple[str, str], ...]
    required_scripts: tuple[str, ...]
    plan_digest: str

    def payload(self) -> dict[str, object]:
        return {
            "plan_version": MIGRATION_PLAN_VERSION,
            "release_sha": self.release_sha,
            "old_layout": self.old_layout.key,
            "new_layout": self.new_layout.key,
            "moved_path_keys": list(self.environment.moved_path_keys),
            "preserved_path_keys": list(self.environment.preserved_path_keys),
            "opaque_keys": list(self.environment.opaque_keys),
            "foreign_keys": list(self.environment.foreign_keys),
            "environment_sha256": hashlib.sha256(
                self.environment.text.encode("utf-8")
            ).hexdigest(),
            "copy_moves": [list(move) for move in self.copy_moves],
            "installed_units": list(self.installed_units),
            "unit_observations": [
                observation.payload() for observation in self.unit_observations
            ],
            "dropin_installations": [
                {
                    "unit": installation.unit,
                    "source": installation.source,
                    "sha256": hashlib.sha256(
                        installation.text.encode("utf-8")
                    ).hexdigest(),
                }
                for installation in self.dropin_installations
            ],
            "credential_sources": list(self.credential_sources),
            "export_installed": self.export_installed,
            "sudo_rule_installed": self.sudo_rule_installed,
            "sudo_rule_sha256": (
                hashlib.sha256(self.sudo_rule_text.encode("utf-8")).hexdigest()
                if self.sudo_rule_text is not None
                else None
            ),
            "account_uid": self.account_uid,
            "account_gid": self.account_gid,
            "account_home": self.account_home,
            "current_release_sha": self.current_release_sha,
            "capture_pointer": self.capture_pointer,
            "capture_release_sha": self.capture_release_sha,
            "tailscale_handler_count": self.tailscale_handler_count,
            "tailscale_old_handlers": self.tailscale_old_handlers,
            "artifact_identity": [list(pair) for pair in self.artifact_identity],
            "required_scripts": list(self.required_scripts),
        }

    def recovery_manifest(self) -> dict[str, object]:
        """The exact recovery objects one apply may need to restore."""
        return {
            "plan_version": MIGRATION_PLAN_VERSION,
            "plan_digest": self.plan_digest,
            "release_sha": self.release_sha,
            "old_layout": self.old_layout.key,
            "new_layout": self.new_layout.key,
            "old_user": self.old_layout.user,
            "new_user": self.new_layout.user,
            "account_uid": self.account_uid,
            "account_gid": self.account_gid,
            "account_home": self.account_home,
            "current_release_sha": self.current_release_sha,
            "copy_moves": [list(move) for move in self.copy_moves],
            "installed_units": list(self.installed_units),
            "credential_sources": list(self.credential_sources),
            "export_installed": self.export_installed,
            "sudo_rule_installed": self.sudo_rule_installed,
            "capture_pointer": self.capture_pointer,
            "capture_release_sha": self.capture_release_sha,
        }


def migration_plan_digest(payload: Mapping[str, object]) -> str:
    rendered = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(rendered.encode("utf-8")).hexdigest()


def build_migration_plan(observation: MigrationObservation) -> MigrationPlan:
    """Build one validated plan and its digest from preflight observations."""
    environment = transform_environment_text(observation.environment_text)
    partial = {
        "plan_version": MIGRATION_PLAN_VERSION,
        "release_sha": observation.release_sha,
        "old_layout": observation.old_layout.key,
        "new_layout": observation.new_layout.key,
        "moved_path_keys": list(environment.moved_path_keys),
        "preserved_path_keys": list(environment.preserved_path_keys),
        "opaque_keys": list(environment.opaque_keys),
        "foreign_keys": list(environment.foreign_keys),
        "environment_sha256": hashlib.sha256(
            environment.text.encode("utf-8")
        ).hexdigest(),
        "copy_moves": [list(move) for move in observation.copy_sources],
        "installed_units": list(observation.installed_units),
        "unit_observations": [
            observation_unit.payload()
            for observation_unit in observation.unit_observations
        ],
        "dropin_installations": [
            {
                "unit": installation.unit,
                "source": installation.source,
                "sha256": hashlib.sha256(
                    installation.text.encode("utf-8")
                ).hexdigest(),
            }
            for installation in observation.dropin_installations
        ],
        "credential_sources": list(observation.credential_sources),
        "export_installed": observation.export_installed,
        "sudo_rule_installed": observation.sudo_rule_installed,
        "sudo_rule_sha256": (
            hashlib.sha256(observation.sudo_rule_text.encode("utf-8")).hexdigest()
            if observation.sudo_rule_text is not None
            else None
        ),
        "account_uid": observation.account_uid,
        "account_gid": observation.account_gid,
        "account_home": observation.account_home,
        "current_release_sha": observation.current_release_sha,
        "capture_pointer": observation.capture_pointer,
        "capture_release_sha": observation.capture_release_sha,
        "tailscale_handler_count": observation.tailscale_handler_count,
        "tailscale_old_handlers": observation.tailscale_old_handlers,
        "artifact_identity": [
            list(pair) for pair in observation.artifact_identity
        ],
        "required_scripts": list(observation.required_scripts),
    }
    return MigrationPlan(
        release_sha=observation.release_sha,
        old_layout=observation.old_layout,
        new_layout=observation.new_layout,
        environment=environment,
        copy_moves=observation.copy_sources,
        installed_units=observation.installed_units,
        unit_observations=observation.unit_observations,
        dropin_installations=observation.dropin_installations,
        credential_sources=observation.credential_sources,
        export_installed=observation.export_installed,
        sudo_rule_installed=observation.sudo_rule_installed,
        sudo_rule_text=observation.sudo_rule_text,
        account_uid=observation.account_uid,
        account_gid=observation.account_gid,
        account_home=observation.account_home,
        current_release_sha=observation.current_release_sha,
        capture_pointer=observation.capture_pointer,
        capture_release_sha=observation.capture_release_sha,
        tailscale_handler_count=observation.tailscale_handler_count,
        tailscale_old_handlers=observation.tailscale_old_handlers,
        artifact_identity=observation.artifact_identity,
        required_scripts=observation.required_scripts,
        plan_digest=migration_plan_digest(partial),
    )


def migration_journal_payload(plan: MigrationPlan) -> dict[str, object]:
    return {
        "plan_version": MIGRATION_PLAN_VERSION,
        "journal_version": MIGRATION_JOURNAL_VERSION,
        "plan_digest": plan.plan_digest,
        "release_sha": plan.release_sha,
        "old_layout": plan.old_layout.key,
        "new_layout": plan.new_layout.key,
        "completed_phases": [],
        "current_phase": None,
        "substeps": {},
        "writes_possible": False,
        "writes_admitted": False,
        "outcome": "running",
    }


def migration_is_post_write(completed_phases: Sequence[str]) -> bool:
    return MIGRATION_CUTOVER_PHASE in completed_phases


def migration_writes_possible(journal: Mapping[str, object]) -> bool:
    """Whether the new application may already have admitted writes.

    Recovery selection reads the durable ``writes_possible`` boundary recorded
    before the new service was started. The completed-phase list is a fallback
    for older journals, never the primary signal.
    """
    if journal.get("writes_possible") or journal.get("writes_admitted"):
        return True
    return migration_is_post_write(list(journal.get("completed_phases", [])))


def migration_control_conflicts(
    control_directory: str, copy_moves: Sequence[tuple[str, str]]
) -> tuple[str, ...]:
    """Copy roots that overlap the control directory, derived from the plan.

    The control directory must be outside every copy source and destination, in
    both containment directions: inside a destination would make the
    destination exist before the copy, and containing one would create it while
    writing control state.
    """
    control = control_directory.rstrip("/")
    conflicts: list[str] = []
    for source, destination in copy_moves:
        for root in (source, destination):
            normalized = root.rstrip("/")
            if not normalized:
                continue
            if (
                control == normalized
                or control.startswith(normalized + "/")
                or normalized.startswith(control + "/")
            ):
                conflicts.append(normalized)
    return tuple(dict.fromkeys(conflicts))


def parse_remote_unit_state(raw: str) -> dict[str, object]:
    """Classify one unit-state answer without reading a failed query as absent."""
    text = raw.strip()
    if text == "query-failed":
        raise ReleaseError("unit state query failed", EXIT_MIGRATION)
    fields: dict[str, str] = {}
    for item in text.split():
        key, separator, value = item.partition("=")
        if separator:
            fields[key] = value
    load_state = fields.get("load", "")
    if load_state in ("", "not-found"):
        return {"installed": False, "load": load_state, "enabled": "", "active": ""}
    return {
        "installed": True,
        "load": load_state,
        "enabled": fields.get("enabled", ""),
        "active": fields.get("active", ""),
    }


def parse_effective_unit_state(raw: str, name: str) -> ObservedUnit:
    """Parse one effective fragment/drop-in/enablement/activity answer.

    A failed privileged query is refused instead of being read as an absent
    unit, and every reported drop-in path is preserved exactly as systemd
    loaded it, including installations outside ``/etc/systemd/system``.
    """
    text = raw.strip()
    if text == "query-failed":
        raise ReleaseError("unit state query failed", EXIT_MIGRATION)
    fields: dict[str, str] = {}
    dropins: list[str] = []
    for line in text.splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            continue
        if key == "dropin":
            dropins.append(value)
        else:
            fields[key] = value
    load_state = fields.get("load", "")
    installed = load_state not in ("", "not-found")
    return ObservedUnit(
        name=name,
        installed=installed,
        load=load_state,
        fragment_path=fields.get("fragment", "") if installed else "",
        dropin_paths=tuple(dropins) if installed else (),
        enabled=fields.get("enabled", "") if installed else "",
        active=fields.get("active", "") if installed else "",
    )


# ---------------------------------------------------------------------------
# Identity migration: production host
# ---------------------------------------------------------------------------

def migration_systemd_dir(engine_path: Path) -> Path:
    return engine_path.resolve().parent.parent / "systemd"


def canonical_unit_name(old_name: str) -> str:
    for old, new, _required in MIGRATION_UNIT_ARTIFACTS:
        if old == old_name:
            return new
    raise ReleaseError("unknown migration unit", EXIT_MIGRATION)


class RemoteMigrationHost:
    """Production host adapter for one identity migration apply.

    Every method is a bounded remote command or a local verification of remote
    output. Nothing in this class runs until a separately authorized apply
    grant is issued; this repository cut only builds and simulates it.
    """

    def __init__(
        self,
        runner: Runner,
        transport: dict[str, str],
        plan: MigrationPlan,
        *,
        engine_path: Path,
        repository_root: Path,
    ) -> None:
        self.runner = runner
        self.transport = transport
        self.plan = plan
        self.engine_path = engine_path
        self.repository_root = repository_root
        self._active_journal: dict[str, object] | None = None
        self._lock_decision: DeployLockDecision | None = None

    # -- journal, manifest, lock ------------------------------------------

    def bind_journal(self, journal: dict[str, object]) -> None:
        """Attach the live journal so substep outcomes persist durably."""
        self._active_journal = journal

    def _persist_substep(self, name: str, value: object) -> None:
        if self._active_journal is None:
            return
        substeps = self._active_journal.setdefault("substeps", {})
        if isinstance(substeps, dict):
            substeps[name] = value
        self.write_journal(self._active_journal)

    def _assert_control_location(self) -> None:
        conflicts = migration_control_conflicts(
            MIGRATION_DIRECTORY, self.plan.copy_moves
        )
        if conflicts:
            raise ReleaseError(
                "identity migration control state overlaps copied state roots",
                EXIT_MIGRATION,
            )

    def read_journal(self) -> dict[str, object] | None:
        self._assert_control_location()
        raw = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_read_optional_file(MIGRATION_JOURNAL_PATH),
        ).strip()
        if raw == "query-failed":
            raise ReleaseError("migration journal state is unverifiable", EXIT_MIGRATION)
        if raw == "absent":
            return None
        return parse_json_status(raw)

    def write_journal(self, payload: dict[str, object]) -> None:
        self._assert_control_location()
        self._write_root_file(
            MIGRATION_JOURNAL_PATH,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def write_recovery_manifest(self, payload: dict[str, object]) -> None:
        self._assert_control_location()
        self._write_root_file(
            MIGRATION_RECOVERY_MANIFEST_PATH,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )

    def _write_root_file(self, path: str, payload: bytes) -> None:
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_make_directory(
                str(Path(path).parent), "0700"
            ),
        )
        digest = hashlib.sha256(payload).hexdigest()
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_write_file_atomic(path, digest),
            input_bytes=payload,
        )

    def acquire_lock(self) -> str:
        """Take the one routine deployment exclusion, shared with deploy/rollback."""
        decision = acquire_deploy_lock(
            self.runner, self.transport, deploy_lock_owner_record()
        )
        self._lock_decision = decision
        return decision.outcome

    def release_lock(self) -> None:
        decision = self._lock_decision
        if decision is None:
            return
        self._lock_decision = None
        # The remote helper this apply wrote into its own prepared scratch area
        # is this run's exact owned file; removing it and the now-empty scratch
        # directory keeps the host clean. The shared exclusion guarantees no
        # concurrent run owns that path, so the cleanup is still exact.
        with contextlib.suppress(ReleaseError):
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_remove_file(MIGRATION_REMOTE_ENGINE),
            )
        with contextlib.suppress(ReleaseError):
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_rmdir(MIGRATION_SCRATCH_DIRECTORY),
            )
        release_deploy_lock(self.runner, self.transport, decision)

    # -- phases ------------------------------------------------------------

    def observe_writer_state(self) -> dict[str, dict[str, object]]:
        """Record observed writer unit installation, enablement and activity."""
        units: list[str] = []
        for unit in (self.plan.old_layout.service, *self.plan.installed_units):
            if not unit.endswith(SYSTEMD_UNIT_SUFFIXES):
                continue
            if unit not in units:
                units.append(unit)
        observed: dict[str, dict[str, object]] = {}
        for unit in units:
            state = parse_remote_unit_state(
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_unit_state(unit),
                )
            )
            if not state["installed"]:
                raise ReleaseError(
                    "an observed migration writer unit is absent", EXIT_MIGRATION
                )
            observed[unit] = state
        self._persist_substep("observed_units", observed)
        return observed

    def quiesce(self) -> list[str]:
        self.observe_writer_state()
        stopped = self.stop_writers()
        self.assert_no_legacy_writers()
        self._capture_before = self.capture_snapshot()
        return stopped

    def stop_writers(self) -> list[str]:
        """Stop admission, then timers, then their jobs.

        The web service stops first so no new work is admitted. Timers stop
        before the services they trigger, so no new job is scheduled while the
        running job is being drained.
        """
        stopped: list[str] = []
        timers: list[str] = []
        services: list[str] = []
        for old_name in self.plan.installed_units:
            if old_name == self.plan.old_layout.service:
                continue
            if not old_name.endswith(SYSTEMD_UNIT_SUFFIXES):
                continue
            if old_name.endswith(".timer"):
                timers.append(old_name)
            else:
                services.append(old_name)
        units = [self.plan.old_layout.service, *timers, *services]
        for unit in units:
            try:
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_stop_unit(unit),
                )
            except ReleaseError as exc:
                raise ReleaseError(
                    "could not stop migration writers", EXIT_MIGRATION
                ) from exc
            stopped.append(unit)
            self._persist_substep("writers_stopped", list(stopped))
        return stopped

    def assert_no_legacy_writers(self) -> None:
        count = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_account_processes_absent(
                self.plan.old_layout.user
            ),
        ).strip()
        if count == "query-failed":
            raise ReleaseError(
                "former web account process state is unverifiable", EXIT_MIGRATION
            )
        if count != "0":
            raise ReleaseError(
                "a process still runs as the former web account", EXIT_MIGRATION
            )

    def capture_snapshot(self) -> dict[str, object]:
        snapshot: dict[str, object] = {
            "pointer": self.plan.capture_pointer,
            "release_sha": self.plan.capture_release_sha,
        }
        active = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_service_is_active(CAPTURE_RUNNER_SERVICE),
        ).strip()
        snapshot["runner_active"] = active == "active"
        try:
            snapshot["identity"] = _snapshot_capture_identity(
                self.runner, self.transport
            )
        except ReleaseError:
            snapshot["identity"] = None
        return snapshot

    def verify_capture_untouched(self, _before: object = None) -> None:
        """Prove capture still runs under its own account, untouched.

        No migration phase ever stops or restarts a capture unit. The runner
        must keep its active state and a fresh private runtime identity snapshot
        taken here must equal the snapshot taken before the writers were
        quiesced; a changed identity means the migration is not capture-neutral.
        """
        before = getattr(self, "_capture_before", None)
        if not isinstance(before, dict):
            before = self.capture_snapshot()
        after = self.capture_snapshot()
        if before.get("runner_active") and not after.get("runner_active"):
            raise ReleaseError("capture runner left the active state", EXIT_MIGRATION)
        if before.get("identity") != after.get("identity"):
            raise ReleaseError(
                "capture runtime identity changed during migration", EXIT_MIGRATION
            )

    def create_checkpoint(self) -> str:
        release_path = self.plan.old_layout.release_dir(self.plan.release_sha)
        raw = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_run_scheduled_backup(
                release_path, self.plan.old_layout
            ),
        )
        payload = parse_json_status(raw)
        if payload.get("state") != "succeeded":
            raise ReleaseError("quiescent checkpoint failed", EXIT_MIGRATION)
        return str(payload.get("bundle_id", "present"))

    def copy_state(self) -> list[str]:
        verified: list[str] = []
        for source, destination in self.plan.copy_moves:
            state = ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_directory_state(destination),
            ).strip()
            if state == "query-failed":
                raise ReleaseError(
                    "copy destination state is unverifiable", EXIT_MIGRATION
                )
            if state != "absent":
                raise ReleaseError(
                    "copy destination is not absent; refusing to overwrite",
                    EXIT_MIGRATION,
                )
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_make_directory(
                    str(Path(destination).parent), "0755"
                ),
            )
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_make_directory(destination, "0755"),
            )
            before = ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_tree_manifest(source),
            )
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_copy_tree(source, destination),
            )
            after = ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_tree_manifest(destination),
            )
            if before != after:
                raise ReleaseError(
                    "copied state did not verify against its source", EXIT_MIGRATION
                )
            verified.append(destination)
        return verified

    def transform_environment(self) -> dict[str, object]:
        destination = self.plan.new_layout.env_file
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_make_directory(
                str(Path(destination).parent), "0755"
            ),
        )
        payload = self.plan.environment.text.encode("utf-8")
        digest = hashlib.sha256(payload).hexdigest()
        # Write beside the destination, verify the canonical-only assertion on
        # the staged bytes, then move the file into place atomically.
        pending = f"{destination}.next"
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_write_file(pending, digest),
            input_bytes=payload,
        )
        assertion = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_assert_no_retired_assignments(pending),
        ).strip()
        if assertion != "canonical-only":
            raise ReleaseError(
                "installed environment still carries retired assignments",
                EXIT_MIGRATION,
            )
        ssh(
            self.runner,
            **self.transport,
            remote_command=(
                f"sudo -n mv -T {shlex.quote(pending)} {shlex.quote(destination)}"
            ),
        )
        return {
            "moved_path_keys": list(self.plan.environment.moved_path_keys),
            "preserved_path_keys": list(self.plan.environment.preserved_path_keys),
        }

    def preserve_ancillary(self) -> dict[str, object]:
        """Migrate only the ancillary integrations the host actually has.

        The export launcher and the narrow rule that runs it are copied to their
        canonical installed names from the installed bytes; a facility that is
        absent stays absent. Secret values are never read: credential sources
        are copied as opaque files by the state copy, and this phase only
        verifies the installed ancillary objects still exist.
        """
        for value in (self.plan.export_installed, self.plan.sudo_rule_installed):
            if value is None:
                continue
            existence = _require_query_result(
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_existence(value),
                ),
                f"ancillary integration {value}",
            )
            if existence != "present":
                raise ReleaseError(
                    "an existing ancillary integration disappeared", EXIT_MIGRATION
                )
        if self.plan.export_installed is not None:
            canonical = canonical_installed_export_path(
                self.plan.export_installed
            )
            if canonical != self.plan.export_installed:
                payload = (
                    self.repository_root / MIGRATION_EXPORT_ARTIFACT[1]
                ).read_bytes()
                self._install_root_file(canonical, payload, "0755")
            else:
                self._validate_root_file(canonical, "0755")
        if self.plan.sudo_rule_installed is not None:
            if self.plan.sudo_rule_text is None:
                raise ReleaseError(
                    "installed sudo rule was not transformed at preflight",
                    EXIT_MIGRATION,
                )
            canonical = canonical_installed_sudo_rule_path(
                self.plan.sudo_rule_installed
            )
            if canonical != self.plan.sudo_rule_installed:
                self._install_root_file(
                    canonical, self.plan.sudo_rule_text.encode("utf-8"), "0440"
                )
            else:
                self._validate_root_file(canonical, "0440")
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_validate_sudoers(canonical),
            )
        return {
            "export_installed": self.plan.export_installed,
            "sudo_rule_installed": self.plan.sudo_rule_installed,
            "credential_sources": list(self.plan.credential_sources),
        }

    @staticmethod
    def _root_file_mode_octal(mode: str) -> str:
        return format(int(mode, 8), "o")

    def _root_file_evidence(
        self, path: str, context: str
    ) -> tuple[str, str]:
        evidence = _require_query_result(
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_root_file_evidence(path),
            ),
            context,
        )
        fields = evidence.split(" ")
        if len(fields) != 2 or ":" not in fields[0]:
            raise ReleaseError(f"{context} evidence is malformed", EXIT_MIGRATION)
        return fields[0], fields[1]

    def _validate_root_file(self, path: str, mode: str) -> None:
        owner, _digest = self._root_file_evidence(path, f"root file {path}")
        expected_owner = f"root:root:{self._root_file_mode_octal(mode)}"
        if owner != expected_owner:
            raise ReleaseError(
                "installed ancillary file ownership or mode is unexpected",
                EXIT_MIGRATION,
            )

    def _install_root_file(self, path: str, payload: bytes, mode: str) -> None:
        digest = hashlib.sha256(payload).hexdigest()
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_write_file_atomic(path, digest),
            input_bytes=payload,
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=f"sudo -n chown root:root {shlex.quote(path)}",
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=f"sudo -n chmod {mode} {shlex.quote(path)}",
        )
        owner, installed_digest = self._root_file_evidence(
            path, f"installed root file {path}"
        )
        expected_owner = f"root:root:{self._root_file_mode_octal(mode)}"
        if owner != expected_owner or installed_digest != digest:
            raise ReleaseError(
                "installed ancillary file did not verify", EXIT_MIGRATION
            )

    def prepare_release_environment(self) -> None:
        target = self.plan.new_layout.release_dir(self.plan.release_sha)
        staging = self.plan.new_layout.staging_dir(self.plan.release_sha)
        old_release = self.plan.old_layout.release_dir(self.plan.release_sha)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_test_absent_or_fail(target),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_test_absent_or_fail(staging),
        )
        # The migration prepares its own root-only scratch area before it
        # uploads the remote engine helper there; it never writes into the
        # routine deployment scratch directory.
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_make_directory(MIGRATION_SCRATCH_DIRECTORY, "0700"),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_make_directory(staging, "0755"),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_prepare_migration_release(old_release, staging),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_write_poetry_toml(staging),
            input_bytes=POETRY_TOML.encode("utf-8"),
        )
        committed_lock = dict(self.plan.artifact_identity).get("poetry.lock", "")
        lock_before = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_lock_hash(staging),
        ).strip()
        if lock_before.split()[:1] != [committed_lock]:
            raise ReleaseError(
                "prepared release lock differs from the committed lock",
                EXIT_MIGRATION,
            )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_poetry_check_lock(staging),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_poetry_env_use(staging),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_poetry_install(staging),
        )
        lock_after = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_lock_hash(staging),
        ).strip()
        if lock_before != lock_after:
            raise ReleaseError("poetry.lock changed during migration", EXIT_MIGRATION)
        remote_engine = MIGRATION_REMOTE_ENGINE
        engine_bytes = self.engine_path.read_bytes()
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_write_file(
                remote_engine, hashlib.sha256(engine_bytes).hexdigest()
            ),
            input_bytes=engine_bytes,
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_relocate_venv_shebangs(
                staging, target, remote_engine
            ),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_verify_migration_release(staging, staging),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_chown_root(staging),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_remove_release_writable_bits(staging),
        )
        # The copied release keeps its own declared identity. The migrated tree
        # is the same release SHA, so its existing manifest bytes and SHA marker
        # are re-written unchanged; no new archive hashes are invented.
        old_markers = read_release_markers(self.runner, self.transport, old_release)
        manifest_cmd, sha_cmd = cmd_remote_write_markers(staging)
        if old_markers.manifest_raw:
            ssh(
                self.runner,
                **self.transport,
                remote_command=manifest_cmd,
                input_bytes=old_markers.manifest_raw.encode("utf-8"),
            )
        ssh(
            self.runner,
            **self.transport,
            remote_command=sha_cmd,
            input_bytes=(self.plan.release_sha + "\n").encode("utf-8"),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_rename_staging(staging, target),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_verify_migration_release(staging, target),
        )
        # Validate the final executable paths of the console scripts the
        # installed canonical units will execute, not only the retained
        # database tooling.
        for relative in self.plan.required_scripts:
            candidate = f"{target}/{relative}"
            validate_remote_path(candidate, self.plan.new_layout.release_root)
            try:
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_test_regular_executable(candidate),
                )
            except ReleaseError as exc:
                raise ReleaseError(
                    "a prepared release console script is not executable",
                    EXIT_MIGRATION,
                ) from exc
        # The retained old release is read-only source material: prove it is
        # byte-unchanged after preparation.
        retained = read_release_markers(self.runner, self.transport, old_release)
        if retained.sha != old_markers.sha or retained.manifest_raw != old_markers.manifest_raw:
            raise ReleaseError(
                "the retained old release changed during preparation",
                EXIT_MIGRATION,
            )

    def rename_account(self) -> dict[str, object]:
        home = (
            self.plan.new_layout.state_root
            if self.plan.account_home == self.plan.old_layout.state_root
            else None
        )
        # Intent is persisted before each rename command so a partially executed
        # rename is still reversible: the record means "may already have run".
        self._persist_substep("group_renamed", True)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_rename_group(),
        )
        self._persist_substep("user_renamed", True)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_rename_user(home),
        )
        self._persist_substep("account_home", home or self.plan.account_home)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_assert_account_renamed(
                self.plan.account_uid, self.plan.account_gid
            ),
        )
        return {
            "uid": self.plan.account_uid,
            "gid": self.plan.account_gid,
            "home": home or self.plan.account_home,
        }

    def install_units(self) -> list[str]:
        installed: list[str] = []
        systemd_source = migration_systemd_dir(self.engine_path)
        for old_name in self.plan.installed_units:
            new_name = canonical_unit_name(old_name)
            payload = (systemd_source / new_name).read_bytes()
            destination = f"/etc/systemd/system/{new_name}"
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_write_file(
                    destination, hashlib.sha256(payload).hexdigest()
                ),
                input_bytes=payload,
            )
            installed.append(new_name)
        created_directories: set[str] = set()
        for installation in self.plan.dropin_installations:
            new_unit = canonical_unit_name(installation.unit)
            directory = f"/etc/systemd/system/{new_unit}.d"
            if directory not in created_directories:
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_make_directory(directory, "0755"),
                )
                created_directories.add(directory)
            destination = f"{directory}/{Path(installation.source).name}"
            payload = installation.text.encode("utf-8")
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_write_file(
                    destination, hashlib.sha256(payload).hexdigest()
                ),
                input_bytes=payload,
            )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_daemon_reload(),
        )
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_enable_unit(self.plan.new_layout.service),
        )
        # Obsolete autostart links are disabled at cutover, before the
        # canonical pointer is switched and before the new service starts, so
        # the former units cannot come back on their own.
        disabled: list[str] = []
        for old_name in self.plan.installed_units:
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_disable_unit(old_name),
            )
            disabled.append(old_name)
        self._persist_substep("old_units_disabled", disabled)
        return installed

    def verify_effective_units(self) -> None:
        reading = parse_web_layout_probe(
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_layout_probe(
                    self.plan.new_layout.service
                ),
            ),
            self.plan.new_layout,
        )
        if not reading.installed or not reading.recognises_layout:
            raise ReleaseError(
                "canonical unit effective configuration is not recognised",
                EXIT_MIGRATION,
            )

    def switch_current(self) -> None:
        """Guard the installed executables, then switch the canonical pointer.

        The installed-executable guard runs first against the prepared release;
        only then is ``/opt/kronika/current`` atomically created and switched,
        and the link is read back before the service is allowed to start.
        """
        target = self.plan.new_layout.release_dir(self.plan.release_sha)
        required = verify_unit_executables(
            self.runner, self.transport, target, self.plan.new_layout
        )
        self._persist_substep("verified_unit_executables", list(required))
        self._persist_substep("current_pointer_switched", True)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_atomic_switch(target, self.plan.new_layout),
        )
        linked = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_readlink_current(self.plan.new_layout),
        ).strip()
        if linked != target:
            raise ReleaseError(
                "canonical current pointer did not switch", EXIT_MIGRATION
            )

    def start_service(self) -> None:
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_start_unit(self.plan.new_layout.service),
        )
        self.verify_local_readiness()

    def verify_local_readiness(self) -> None:
        deadline = time.monotonic() + READINESS_DEADLINE_SECONDS
        release_path = self.plan.new_layout.release_dir(self.plan.release_sha)
        while True:
            try:
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_check_database_ready(
                        release_path, self.plan.new_layout
                    ),
                )
                return
            except ReleaseError:
                if time.monotonic() >= deadline:
                    raise ReleaseError(
                        "canonical service did not become ready", EXIT_MIGRATION
                    ) from None
                time.sleep(READINESS_POLL_INTERVAL_SECONDS)

    def record_tailscale_state(self) -> dict[str, object]:
        raw = ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_tailscale_serve_status(),
        )
        status = parse_tailscale_serve_status(raw)
        return {
            "handler_count": status["handler_count"],
            "old_handlers": status["old_handlers"],
            "mounts": status["mounts"],
            "targets": status["targets"],
        }

    def replace_tailscale_handler(self) -> dict[str, object]:
        state = self.record_tailscale_state()
        self._tailscale_state = state
        handler_count = int(state["handler_count"])
        old_handlers = list(state["old_handlers"])
        mounts = list(state["mounts"])
        targets = list(state["targets"])
        if handler_count == 0 and not old_handlers:
            return {"replaced": 0, "handler_count": 0}
        if len(old_handlers) != 1:
            raise ReleaseError(
                "existing ingress handler is not uniquely identifiable",
                EXIT_MIGRATION,
            )
        index = old_handlers[0]
        if targets[index] != tailscale_unix_target(self.plan.old_layout.socket_path):
            raise ReleaseError(
                "existing ingress handler target is unexpected", EXIT_MIGRATION
            )
        new_target = tailscale_unix_target(self.plan.new_layout.socket_path)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_tailscale_replace(mounts[index], new_target),
        )
        return {"replaced": 1, "handler_count": handler_count}

    def verify_ingress(self) -> None:
        release_path = self.plan.new_layout.release_dir(self.plan.release_sha)
        ssh(
            self.runner,
            **self.transport,
            remote_command=cmd_remote_check_health(release_path, self.plan.new_layout),
        )
        if self.plan.tailscale_handler_count == 0:
            return
        status = parse_tailscale_serve_status(
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_tailscale_serve_status(),
            )
        )
        socket_path = self.plan.new_layout.socket_path
        new_forms = {
            f"unix+http://{socket_path}",
            f"unix:{socket_path}",
        }
        if status["old_handlers"] or not (set(status["targets"]) & new_forms):
            raise ReleaseError("ingress replacement did not verify", EXIT_MIGRATION)

    def _observed_writer_units(self) -> dict[str, dict[str, object]]:
        """Observed writer state from the durable journal, or the plan."""
        journal = self._active_journal
        if isinstance(journal, dict):
            substeps = journal.get("substeps")
            if isinstance(substeps, dict):
                observed = substeps.get("observed_units")
                if isinstance(observed, dict) and observed:
                    return observed
        return {
            observation.name: {
                "installed": observation.installed,
                "load": observation.load,
                "enabled": observation.enabled,
                "active": observation.active,
            }
            for observation in self.plan.unit_observations
        }

    def resume_writers(self) -> list[str]:
        """Restore only the previously intended canonical scheduling.

        A timer that was observed enabled is enabled, and a timer that was
        observed active is started; a timer that was observed disabled and
        inactive stays that way, and a unit that was absent stays absent.
        """
        observed = self._observed_writer_units()
        resumed: list[str] = []
        for old_name in self.plan.installed_units:
            if not old_name.endswith(".timer"):
                continue
            state = observed.get(old_name)
            if not isinstance(state, dict) or not state.get("installed"):
                continue
            canonical = canonical_unit_name(old_name)
            if state.get("enabled") in ("enabled", "enabled-runtime"):
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_enable_unit(canonical),
                )
                resumed.append(canonical)
            if state.get("active") in WEB_ACTIVE_STATES:
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_start_unit(canonical),
                )
                if canonical not in resumed:
                    resumed.append(canonical)
        return resumed

    def recover_pre_write(self, journal: dict[str, object]) -> None:
        """Restore the observed former layout; no new writes exist to keep.

        Every phase before the cutover runs with the former service stopped and
        the catalog quiescent, so the copied state is a verified copy of the
        same bytes and the former layout can be restored. The reverse rename is
        driven by the durable substep record, so a partially executed rename is
        reversed too, and the observed unit enablement and activity are restored
        rather than re-enabled unconditionally.
        """
        stop_layout = self.plan.new_layout
        for command in (
            cmd_remote_stop_unit(stop_layout.service),
            cmd_remote_disable_unit(stop_layout.service),
        ):
            with contextlib.suppress(ReleaseError):
                ssh(self.runner, **self.transport, remote_command=command)
        substeps = journal.get("substeps")
        if not isinstance(substeps, dict):
            substeps = {}
        completed = list(journal.get("completed_phases", []))
        legacy = not substeps
        # A switched canonical pointer is this run's own creation; removing it
        # (and any staging link left by an interrupted switch) restores the
        # observed "pointer absent" state. The removal is exact and uses
        # ``rm -f``, which cannot touch a directory.
        if substeps.get("current_pointer_switched"):
            for pointer in (
                stop_layout.current,
                stop_layout.current + ".next",
            ):
                with contextlib.suppress(ReleaseError):
                    ssh(
                        self.runner,
                        **self.transport,
                        remote_command=cmd_remote_remove_file(pointer),
                    )
        rename_phase_done = MIGRATION_PHASE_RENAME_ACCOUNT in completed
        group_renamed = bool(substeps.get("group_renamed")) or (
            legacy and rename_phase_done
        )
        user_renamed = bool(substeps.get("user_renamed")) or (
            legacy and rename_phase_done
        )
        if group_renamed:
            with contextlib.suppress(ReleaseError):
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command="sudo -n groupmod -n framenest kronika",
                )
        if user_renamed:
            if self.plan.account_home == self.plan.old_layout.state_root:
                reverse_user = (
                    "sudo -n usermod -l framenest -d "
                    f"{shlex.quote(self.plan.account_home)} kronika"
                )
            else:
                reverse_user = "sudo -n usermod -l framenest kronika"
            with contextlib.suppress(ReleaseError):
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=reverse_user,
                )
        observed = substeps.get("observed_units")
        if isinstance(observed, dict) and observed:
            self._restore_observed_units(observed)
            return
        # Legacy journals carry no observed unit state; restore the former
        # service and every installed timer as the previous contract did.
        with contextlib.suppress(ReleaseError):
            ssh(
                self.runner,
                **self.transport,
                remote_command=cmd_remote_start_unit(self.plan.old_layout.service),
            )
        for old_name in self.plan.installed_units:
            if not old_name.endswith(".timer"):
                continue
            with contextlib.suppress(ReleaseError):
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_enable_unit(old_name),
                )
            with contextlib.suppress(ReleaseError):
                ssh(
                    self.runner,
                    **self.transport,
                    remote_command=cmd_remote_start_unit(old_name),
                )

    def _restore_observed_units(
        self, observed: Mapping[str, object]
    ) -> None:
        for unit, raw_state in observed.items():
            if not isinstance(raw_state, dict) or not raw_state.get("installed"):
                continue
            enabled = raw_state.get("enabled")
            if enabled in ("enabled", "enabled-runtime"):
                with contextlib.suppress(ReleaseError):
                    ssh(
                        self.runner,
                        **self.transport,
                        remote_command=cmd_remote_enable_unit(unit),
                    )
            elif enabled in ("disabled", "masked", "masked-runtime"):
                with contextlib.suppress(ReleaseError):
                    ssh(
                        self.runner,
                        **self.transport,
                        remote_command=cmd_remote_disable_unit(unit),
                    )
            if raw_state.get("active") in WEB_ACTIVE_STATES:
                with contextlib.suppress(ReleaseError):
                    ssh(
                        self.runner,
                        **self.transport,
                        remote_command=cmd_remote_start_unit(unit),
                    )

    def recover_post_write(self, journal: dict[str, object]) -> None:
        """Never restore the stale copied state after the new application writes.

        Once the new application has started it may have admitted writes, so the
        copied catalog is no longer the current state. This branch keeps the new
        layout and the current state in place and leaves the forward recovery
        decision to an explicitly authorized grant; it does not copy state back,
        does not rename the account back, and does not restart capture. The
        durable ``writes_possible`` boundary is what selects this branch.
        """
        if not migration_writes_possible(journal):
            raise ReleaseError(
                "post-write recovery selected without a durable writes boundary",
                EXIT_MIGRATION,
            )


def parse_tailscale_serve_status(raw: str) -> dict[str, object]:
    """Classify the Serve handler set without exposing host identifiers."""
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ReleaseError("ingress status is unreadable", EXIT_MIGRATION) from exc
    if not isinstance(payload, dict):
        raise ReleaseError("ingress status is unreadable", EXIT_MIGRATION)
    mounts: list[str] = []
    targets: list[str] = []
    old_handlers: list[int] = []
    web = payload.get("Web")
    if isinstance(web, dict):
        for mount, block in web.items():
            handlers = block.get("Handlers") if isinstance(block, dict) else None
            if not isinstance(handlers, dict):
                continue
            for path, handler in handlers.items():
                proxy = handler.get("Proxy") if isinstance(handler, dict) else None
                if not isinstance(proxy, str):
                    continue
                mounts.append(f"{mount}{path}")
                targets.append(proxy)
                if proxy in (
                    tailscale_unix_target(OLD_WEB_LAYOUT.socket_path),
                    "unix:" + OLD_WEB_LAYOUT.socket_path,
                ):
                    old_handlers.append(len(targets) - 1)
    return {
        "handler_count": len(targets),
        "mounts": mounts,
        "targets": targets,
        "old_handlers": old_handlers,
    }


def tailscale_unix_target(socket_path: str) -> str:
    return f"unix+http://{socket_path}"


# ---------------------------------------------------------------------------
# Structural systemd drop-in and sudoers transformation (pure)
# ---------------------------------------------------------------------------

#: One ``Key=Value`` directive line of a unit or drop-in.
SYSTEMD_DIRECTIVE_LINE = re.compile(
    r"^(?P<indent>[ \t]*)(?P<key>[A-Za-z][A-Za-z0-9]*)=(?P<value>.*)$"
)
#: One section header line, for example ``[Service]``.
SYSTEMD_SECTION_LINE = re.compile(r"^[ \t]*\[[A-Za-z][A-Za-z0-9]*\][ \t]*$")
#: Directives whose value is one executable command.
SYSTEMD_EXEC_DIRECTIVES = frozenset(
    {
        "ExecStart",
        "ExecStartPre",
        "ExecStartPost",
        "ExecReload",
        "ExecStop",
        "ExecStopPost",
    }
)
#: Directives whose value is one or more filesystem paths.
SYSTEMD_PATH_DIRECTIVES = frozenset(
    {
        "WorkingDirectory",
        "EnvironmentFile",
        "ReadWritePaths",
        "ReadOnlyPaths",
        "InaccessiblePaths",
    }
)
SYSTEMD_IDENTITY_DIRECTIVES = frozenset({"User", "Group"})
#: systemd command-line prefix characters that may precede the executable.
SYSTEMD_EXEC_PREFIXES = "-+!@"
SYSTEMD_EXEC_TOKEN = re.compile(
    r"^(?P<prefix>[-+!@]*)(?P<path>(?:\\.|[^\s\\])+)", re.DOTALL
)

#: One sudoers rule line: principal, host list, then the command specification.
SUDOERS_RULE_LINE = re.compile(
    r"^(?P<principal>\S+)[ \t]+(?P<hosts>[^=\s]+)[ \t]*=[ \t]*(?P<spec>.+)$"
)
SUDOERS_TAG = re.compile(r"[A-Z_]+:")


def _systemd_transform_value(key: str, value: str) -> str:
    """Transform one supported directive value, or refuse the form."""
    if key in SYSTEMD_IDENTITY_DIRECTIVES:
        candidate = value.strip()
        if candidate.lower() == "framenest":
            return "kronika"
        if "framenest" in candidate.lower():
            raise ReleaseError(
                f"unit directive {key} identity is not classifiable",
                EXIT_MIGRATION,
            )
        return value
    if key == "LoadCredential":
        identifier, separator, path = value.partition(":")
        if not separator or not identifier or not path:
            raise ReleaseError(
                "LoadCredential directive is not identifier:path",
                EXIT_MIGRATION,
            )
        if any(character.isspace() for character in identifier):
            raise ReleaseError(
                "LoadCredential directive is not identifier:path",
                EXIT_MIGRATION,
            )
        transformed = transform_path_value(path)
        if transformed == path and "framenest" in path.lower():
            raise ReleaseError(
                "LoadCredential path is not classifiable", EXIT_MIGRATION
            )
        return f"{identifier}:{transformed}"
    if key in SYSTEMD_EXEC_DIRECTIVES:
        command = value.strip()
        if command.startswith("{"):
            match = UNIT_EXEC_PATH.search(command)
            if match is None:
                raise ReleaseError(
                    f"unit directive {key} revision is not classifiable",
                    EXIT_MIGRATION,
                )
            original = _unescape_unit_text(match.group(1))
            if not original.startswith("/"):
                raise ReleaseError(
                    f"unit directive {key} path is not absolute", EXIT_MIGRATION
                )
            transformed = transform_path_value(original)
            if transformed == original and "framenest" in original.lower():
                raise ReleaseError(
                    f"unit directive {key} path is not classifiable",
                    EXIT_MIGRATION,
                )
            return command.replace(match.group(1), transformed, 1)
        match = SYSTEMD_EXEC_TOKEN.match(command)
        if match is None:
            raise ReleaseError(
                f"unit directive {key} is not classifiable", EXIT_MIGRATION
            )
        if not match.group("path").startswith("/"):
            raise ReleaseError(
                f"unit directive {key} path is not absolute", EXIT_MIGRATION
            )
        transformed = transform_path_value(match.group("path"))
        if transformed == match.group("path") and "framenest" in command.lower():
            raise ReleaseError(
                f"unit directive {key} path is not classifiable", EXIT_MIGRATION
            )
        return match.group("prefix") + transformed + command[match.end():]
    if key in SYSTEMD_PATH_DIRECTIVES:
        tokens = value.split()
        if not tokens:
            raise ReleaseError(
                f"unit directive {key} has no path", EXIT_MIGRATION
            )
        transformed_tokens: list[str] = []
        for token in tokens:
            prefix = ""
            body = token
            if body.startswith("-"):
                prefix, body = "-", body[1:]
            if not body.startswith("/"):
                raise ReleaseError(
                    f"unit directive {key} path is not absolute", EXIT_MIGRATION
                )
            transformed = transform_path_value(body)
            if transformed == body and "framenest" in body.lower():
                raise ReleaseError(
                    f"unit directive {key} path is not classifiable", EXIT_MIGRATION
                )
            transformed_tokens.append(prefix + transformed)
        return " ".join(transformed_tokens)
    raise ReleaseError(
        f"unit directive {key} is not supported", EXIT_MIGRATION
    )


def transform_unit_dropin_text(text: str) -> str:
    """Rewrite one unit or drop-in using supported systemd directive forms.

    Section headers, comments and blank lines are preserved byte-for-byte. Each
    supported directive is parsed structurally: ``User``/``Group`` identities,
    ``LoadCredential`` identifier and path pairs, executable paths and path
    lists. An unknown or malformed directive form raises instead of passing the
    retired spelling through, so an unsupported installation stops preflight
    rather than being transformed silently.
    """
    transformed_lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            transformed_lines.append(raw)
            continue
        if SYSTEMD_SECTION_LINE.match(raw):
            transformed_lines.append(raw)
            continue
        match = SYSTEMD_DIRECTIVE_LINE.match(raw)
        if match is None:
            raise ReleaseError("unit directive is not supported", EXIT_MIGRATION)
        if match.group("value").rstrip().endswith("\\"):
            raise ReleaseError(
                "continued unit directive is not supported", EXIT_MIGRATION
            )
        transformed = _systemd_transform_value(
            match.group("key"), match.group("value")
        )
        transformed_lines.append(
            f"{match.group('indent')}{match.group('key')}={transformed}"
        )
    rendered = "\n".join(transformed_lines)
    if text.endswith("\n"):
        rendered += "\n"
    return rendered


def _transform_sudoers_identity(identity: str) -> str:
    parts = identity.split(":", 1)
    transformed: list[str] = []
    for part in parts:
        candidate = part.strip()
        if candidate.lower() == "framenest":
            transformed.append("kronika")
        elif "framenest" in candidate.lower():
            raise ReleaseError(
                "sudo rule run-as identity is not classifiable", EXIT_MIGRATION
            )
        else:
            transformed.append(part)
    return ":".join(transformed)


def _transform_sudoers_command(command: str, export_installed: str | None) -> str:
    if export_installed is not None and command == export_installed:
        return canonical_installed_export_path(export_installed)
    transformed = transform_path_value(command)
    if transformed == command and "framenest" in command.lower():
        raise ReleaseError(
            "sudo rule command path is not classifiable", EXIT_MIGRATION
        )
    return transformed


def transform_sudoers_text(
    text: str, *, export_installed: str | None = None
) -> str:
    """Rewrite the narrow installed export rule using structurally parsed fields.

    Comment lines are preserved. Each rule line is parsed into principal, host
    list, optional run-as list, optional tag list and command; the run-as
    identity is renamed, the command path is moved to its canonical installed
    name when it is the observed export facility, and every unknown form raises
    so preflight stops rather than leaving a retired path behind.
    """
    transformed_lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            transformed_lines.append(raw)
            continue
        match = SUDOERS_RULE_LINE.match(stripped)
        if match is None:
            raise ReleaseError("sudo rule line is not supported", EXIT_MIGRATION)
        rest = match.group("spec").lstrip()
        runas: str | None = None
        if rest.startswith("("):
            closing = rest.find(")")
            if closing < 0:
                raise ReleaseError(
                    "sudo rule run-as section is malformed", EXIT_MIGRATION
                )
            runas = rest[1:closing]
            rest = rest[closing + 1 :].lstrip()
        tags: list[str] = []
        while True:
            tag = SUDOERS_TAG.match(rest)
            if tag is None:
                break
            tags.append(tag.group(0))
            rest = rest[tag.end() :].lstrip()
        if not rest.startswith("/"):
            raise ReleaseError(
                "sudo rule command is not an absolute path", EXIT_MIGRATION
            )
        command, _separator, arguments = rest.partition(" ")
        transformed_runas = (
            f"({_transform_sudoers_identity(runas)}) " if runas is not None else ""
        )
        transformed_command = _transform_sudoers_command(command, export_installed)
        tag_text = (" ".join(tags) + " ") if tags else ""
        rebuilt = (
            f"{match.group('principal')} {match.group('hosts')}="
            f"{transformed_runas}{tag_text}{transformed_command}"
        )
        if arguments.strip():
            rebuilt += f" {arguments.strip()}"
        transformed_lines.append(rebuilt)
    rendered = "\n".join(transformed_lines)
    if text.endswith("\n"):
        rendered += "\n"
    return rendered


def migration_required_console_scripts(
    systemd_source: Path, layout: WebLayout = NEW_WEB_LAYOUT
) -> tuple[str, ...]:
    """Release-relative console scripts the canonical units will execute.

    Parsed from the canonical service artifacts this migration installs, so the
    prepared release is validated against the exact executables the installed
    units name rather than against a hard-coded script list.
    """
    required: set[str] = set()
    for _old_name, new_name, _required in MIGRATION_UNIT_ARTIFACTS:
        if not new_name.endswith(".service"):
            continue
        text = (systemd_source / new_name).read_text(encoding="utf-8")
        for raw in text.splitlines():
            match = SYSTEMD_DIRECTIVE_LINE.match(raw)
            if match is None or match.group("key") not in SYSTEMD_EXEC_DIRECTIVES:
                continue
            value = match.group("value").strip()
            executable_match = SYSTEMD_EXEC_TOKEN.match(value)
            if executable_match is None:
                raise ReleaseError(
                    "canonical unit execution form is not a release console script",
                    EXIT_MIGRATION,
                )
            scoped = release_scoped_console_script(
                transform_path_value(executable_match.group("path")), layout
            )
            if scoped is None:
                raise ReleaseError(
                    "canonical unit execution form is not a release console script",
                    EXIT_MIGRATION,
                )
            required.add(scoped)
    return tuple(sorted(required))


# ---------------------------------------------------------------------------
# Identity migration: orchestration and recovery
# ---------------------------------------------------------------------------

def run_migration_apply(
    host: RemoteMigrationHost, plan: MigrationPlan
) -> dict[str, object]:
    """Execute the ordered apply sequence against one host adapter.

    The orchestration itself is host-agnostic: tests drive the same sequence
    with a simulated host and failure injection. The routine deployment
    exclusion is taken before any control state is written; an existing
    incomplete journal is preserved and refuses a fresh apply. Each phase
    records its intent before it runs and its completion only after it
    succeeded, and the conservative ``writes_possible`` boundary is persisted
    before the new service is started, so recovery never infers the write
    boundary from a completed-phase list alone.
    """
    journal = migration_journal_payload(plan)
    host.acquire_lock()
    try:
        existing = host.read_journal()
        if existing is not None:
            raise ReleaseError(
                "an existing identity migration journal is preserved; "
                "explicit recovery is required first",
                EXIT_MIGRATION,
            )
        host.write_recovery_manifest(plan.recovery_manifest())
        host.write_journal(journal)
    except ReleaseError:
        with contextlib.suppress(ReleaseError):
            host.release_lock()
        raise
    bind = getattr(host, "bind_journal", None)
    if callable(bind):
        bind(journal)
    try:
        for phase in MIGRATION_PHASES:
            if phase == MIGRATION_CUTOVER_PHASE:
                journal["writes_possible"] = True
            journal["current_phase"] = phase
            host.write_journal(journal)
            getattr(host, _MIGRATION_PHASE_METHODS[phase])()
            journal["completed_phases"].append(phase)
            journal["current_phase"] = None
            if phase == MIGRATION_CUTOVER_PHASE:
                journal["writes_admitted"] = True
            host.write_journal(journal)
        journal["outcome"] = "completed"
        host.write_journal(journal)
        return journal
    except ReleaseError:
        journal["outcome"] = "failed"
        with contextlib.suppress(ReleaseError):
            host.write_journal(journal)
        if migration_writes_possible(journal):
            host.recover_post_write(journal)
        else:
            host.recover_pre_write(journal)
        raise
    finally:
        try:
            host.release_lock()
        except ReleaseError:
            if sys.exc_info()[0] is None:
                raise


_MIGRATION_PHASE_METHODS = {
    MIGRATION_PHASE_QUIESCE: "quiesce",
    MIGRATION_PHASE_VERIFY_CAPTURE: "verify_capture_untouched",
    MIGRATION_PHASE_CHECKPOINT: "create_checkpoint",
    MIGRATION_PHASE_COPY_STATE: "copy_state",
    MIGRATION_PHASE_TRANSFORM_ENVIRONMENT: "transform_environment",
    MIGRATION_PHASE_PRESERVE_ANCILLARY: "preserve_ancillary",
    MIGRATION_PHASE_PREPARE_RELEASE: "prepare_release_environment",
    MIGRATION_PHASE_RENAME_ACCOUNT: "rename_account",
    MIGRATION_PHASE_INSTALL_UNITS: "install_units",
    MIGRATION_PHASE_VERIFY_UNITS: "verify_effective_units",
    MIGRATION_PHASE_SWITCH_CURRENT: "switch_current",
    MIGRATION_PHASE_START_SERVICE: "start_service",
    MIGRATION_PHASE_REPLACE_INGRESS: "replace_tailscale_handler",
    MIGRATION_PHASE_VERIFY_INGRESS: "verify_ingress",
    MIGRATION_PHASE_RESUME_WRITERS: "resume_writers",
}


def canonical_installed_export_path(current: str) -> str:
    for candidate in MIGRATION_EXPORT_INSTALLED_CANDIDATES:
        if candidate == current:
            return MIGRATION_EXPORT_INSTALLED_CANDIDATES[-1]
    return current


def canonical_installed_sudo_rule_path(current: str) -> str:
    for candidate in MIGRATION_SUDO_RULE_CANDIDATES:
        if candidate == current:
            return MIGRATION_SUDO_RULE_CANDIDATES[-1]
    return current


def _require_query_result(raw: str, context: str) -> str:
    """Refuse a privileged query failure instead of reading it as absence."""
    text = raw.strip()
    if text == "query-failed":
        raise ReleaseError(f"{context} could not be queried", EXIT_MIGRATION)
    return text


def required_current_release_sha(
    runner: Runner, transport: dict[str, str], layout: WebLayout
) -> str:
    """Resolve the served release through the current pointer, or refuse.

    A missing pointer is a migration refusal, not a transport error, and a
    failed privileged query is never read as absence.
    """
    raw = _require_query_result(
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_optional_link(layout.current),
        ),
        "current release pointer",
    )
    if raw == "absent":
        raise ReleaseError("current release pointer is absent", EXIT_MIGRATION)
    validate_remote_path(raw, layout.release_root)
    return read_release_markers(runner, transport, raw).sha


def read_migration_observation(
    runner: Runner,
    transport: dict[str, str],
    *,
    release_sha: str,
    repository_root: Path | None = None,
    engine_path: Path | None = None,
) -> MigrationObservation:
    """Read one host's migration facts, read-only, reporting filenames only.

    The observation binds the exact served release, the effective unit
    fragments, drop-ins, enablement and activity, each observed drop-in's
    transformed bytes, the installed sudo rule's transformed bytes and the
    source artifact identity of the engine and every artifact the migration
    installs. An unsupported directive or sudo form stops the preflight here
    rather than being transformed silently during apply.
    """
    repository = (
        Path(repository_root)
        if repository_root is not None
        else Path(__file__).resolve().parents[2]
    )
    engine = Path(engine_path) if engine_path is not None else Path(__file__)
    state = resolve_host_layout(runner, transport)
    if state.web.key != "old":
        raise ReleaseError("identity migration requires the former web layout", EXIT_LAYOUT)
    old_layout = state.web
    new_layout = NEW_WEB_LAYOUT

    old_release = old_layout.release_dir(release_sha)
    existence = _require_query_result(
        ssh(runner, **transport, remote_command=cmd_remote_existence(old_release)),
        "former release tree",
    )
    if existence != "present":
        raise ReleaseError("former release tree is absent", EXIT_MIGRATION)

    current_release_sha = required_current_release_sha(
        runner, transport, old_layout
    )
    if current_release_sha != release_sha:
        raise ReleaseError(
            "current release does not equal the migration release", EXIT_MIGRATION
        )
    new_current = _require_query_result(
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_optional_link(new_layout.current),
        ),
        "canonical current pointer",
    )
    if new_current != "absent":
        raise ReleaseError(
            "canonical current pointer already exists", EXIT_MIGRATION
        )

    environment_raw = _require_query_result(
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_read_optional_file(old_layout.env_file),
        ),
        "former environment file",
    )
    if environment_raw == "absent":
        raise ReleaseError("former environment file is absent", EXIT_MIGRATION)

    copy_sources: list[tuple[str, str]] = []
    for old_root, new_root in (
        (old_layout.state_root, new_layout.state_root),
        (old_layout.cache_root, new_layout.cache_root),
        ("/etc/framenest/credentials", "/etc/kronika/credentials"),
    ):
        existence = _require_query_result(
            ssh(runner, **transport, remote_command=cmd_remote_existence(old_root)),
            f"state root {old_root}",
        )
        if existence == "present":
            copy_sources.append((old_root, new_root))

    unit_observations: list[ObservedUnit] = []
    installed_units: list[str] = []
    for old_name, _new_name, required in MIGRATION_UNIT_ARTIFACTS:
        if old_name.endswith(SYSTEMD_UNIT_SUFFIXES):
            observed = parse_effective_unit_state(
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_unit_effective_state(old_name),
                ),
                old_name,
            )
        else:
            existence = _require_query_result(
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_existence(
                        f"/etc/systemd/system/{old_name}"
                    ),
                ),
                f"installed artifact {old_name}",
            )
            installed = existence == "present"
            observed = ObservedUnit(
                name=old_name,
                installed=installed,
                load="loaded" if installed else "",
                fragment_path=(
                    f"/etc/systemd/system/{old_name}" if installed else ""
                ),
                dropin_paths=(),
                enabled="",
                active="",
            )
        unit_observations.append(observed)
        if observed.installed:
            installed_units.append(old_name)
        elif required:
            raise ReleaseError(
                "the installed web unit is absent", EXIT_MIGRATION
            )
    dropin_installations: list[DropinInstallation] = []
    for observed in unit_observations:
        if not observed.installed:
            continue
        for source in observed.dropin_paths:
            raw = _require_query_result(
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_read_optional_file(source),
                ),
                f"installed drop-in {source}",
            )
            if raw == "absent":
                raise ReleaseError(
                    "an observed drop-in is absent", EXIT_MIGRATION
                )
            dropin_installations.append(
                DropinInstallation(
                    unit=observed.name,
                    source=source,
                    text=transform_unit_dropin_text(raw),
                )
            )

    credential_sources: list[str] = []
    credentials_root = "/etc/framenest/credentials"
    credentials_listing = _require_query_result(
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_list_directory(credentials_root),
        ),
        "credential source directory",
    )
    if credentials_listing != "absent":
        credential_sources = [
            f"{credentials_root}/{line}"
            for line in credentials_listing.splitlines()
            if line and "/" not in line
        ]

    export_installed = None
    for candidate in MIGRATION_EXPORT_INSTALLED_CANDIDATES:
        existence = _require_query_result(
            ssh(runner, **transport, remote_command=cmd_remote_existence(candidate)),
            f"export facility {candidate}",
        )
        if existence == "present":
            export_installed = candidate
            break
    sudo_rule_installed = None
    for candidate in MIGRATION_SUDO_RULE_CANDIDATES:
        existence = _require_query_result(
            ssh(runner, **transport, remote_command=cmd_remote_existence(candidate)),
            f"sudo rule {candidate}",
        )
        if existence == "present":
            sudo_rule_installed = candidate
            break
    sudo_rule_text: str | None = None
    if sudo_rule_installed is not None:
        raw_sudo_rule = _require_query_result(
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_read_optional_file(sudo_rule_installed),
            ),
            "installed sudo rule",
        )
        if raw_sudo_rule == "absent":
            raise ReleaseError("installed sudo rule is absent", EXIT_MIGRATION)
        sudo_rule_text = transform_sudoers_text(
            raw_sudo_rule, export_installed=export_installed
        )

    account_record = ssh(
        runner, **transport, remote_command=cmd_remote_account_record()
    ).strip()
    if account_record == "absent":
        raise ReleaseError("former web account is absent", EXIT_MIGRATION)
    fields = account_record.split(" ")
    if len(fields) != 3 or not fields[0].isdigit() or not fields[1].isdigit():
        raise ReleaseError("former web account record is unreadable", EXIT_MIGRATION)
    new_account = ssh(
        runner, **transport, remote_command=cmd_remote_new_account_absent()
    ).strip()
    if new_account != "absent":
        raise ReleaseError(
            "canonical web account already exists", EXIT_MIGRATION
        )

    capture_release_sha: str | None = None
    if state.capture_current:
        capture_sha = read_optional_release_sha(
            runner, transport, state.capture_current, state.capture_release_root
        )
        capture_release_sha = None if capture_sha == "absent" else capture_sha

    raw_status = ssh(
        runner, **transport, remote_command=cmd_remote_tailscale_serve_status()
    )
    tailscale = parse_tailscale_serve_status(raw_status)

    artifact_identity: list[tuple[str, str]] = []
    for _old_name, new_name, _required in MIGRATION_UNIT_ARTIFACTS:
        relative = f"deploy/systemd/{new_name}"
        artifact_identity.append((relative, sha256_of_file(repository / relative)))
    export_relative = MIGRATION_EXPORT_ARTIFACT[1]
    artifact_identity.append(
        (export_relative, sha256_of_file(repository / export_relative))
    )
    lock_relative = "poetry.lock"
    artifact_identity.append(
        (lock_relative, sha256_of_file(repository / lock_relative))
    )
    engine_relative = "deploy/ubuntu/kronika_release.py"
    artifact_identity.append((engine_relative, sha256_of_file(engine)))
    required_scripts = migration_required_console_scripts(
        migration_systemd_dir(engine), new_layout
    )

    return MigrationObservation(
        release_sha=release_sha,
        old_layout=old_layout,
        new_layout=new_layout,
        environment_text=environment_raw,
        copy_sources=tuple(copy_sources),
        installed_units=tuple(installed_units),
        unit_observations=tuple(unit_observations),
        dropin_installations=tuple(dropin_installations),
        credential_sources=tuple(credential_sources),
        export_installed=export_installed,
        sudo_rule_installed=sudo_rule_installed,
        sudo_rule_text=sudo_rule_text,
        account_uid=int(fields[0]),
        account_gid=int(fields[1]),
        account_home=fields[2],
        current_release_sha=current_release_sha,
        capture_pointer=state.capture_pointer,
        capture_release_sha=capture_release_sha,
        tailscale_handler_count=int(tailscale["handler_count"]),
        tailscale_old_handlers=len(tailscale["old_handlers"]),
        artifact_identity=tuple(sorted(artifact_identity)),
        required_scripts=required_scripts,
    )


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

    migrate = subcommands.add_parser(
        "migrate-identity",
        help="Prepare the canonical host identity migration machinery.",
    )
    migrate_sub = migrate.add_subparsers(dest="migration_command", required=True)
    migrate_preflight = migrate_sub.add_parser(
        "preflight",
        help="Read-only plan of the identity migration for one release.",
    )
    migrate_preflight.add_argument("--release", required=True)
    _add_transport_args(migrate_preflight)
    migrate_apply = migrate_sub.add_parser(
        "apply",
        help=(
            "Apply the migration. Requires --yes and the digest of a validated "
            "preflight."
        ),
    )
    migrate_apply.add_argument("--release", required=True)
    migrate_apply.add_argument("--yes", action="store_true")
    migrate_apply.add_argument("--preflight-digest", required=True)
    _add_transport_args(migrate_apply)

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
        if args.command == "migrate-identity":
            return _cmd_migrate_identity(args, command_runner)
        if args.command == "_remote":
            return _cmd_remote(args, command_runner)
        raise ReleaseError("invalid command", EXIT_USAGE)
    except ReleaseError as exc:
        print(f"{PROGRAM}: {exc}", file=sys.stderr)
        return exc.exit_code


def _cmd_migrate_identity(args: argparse.Namespace, runner: Runner) -> int:
    if args.migration_command == "preflight":
        return _cmd_migrate_preflight(args, runner)
    if args.migration_command == "apply":
        return _cmd_migrate_apply(args, runner)
    raise ReleaseError("invalid command", EXIT_USAGE)


def _cmd_migrate_preflight(args: argparse.Namespace, runner: Runner) -> int:
    """Read-only migration plan. Prints no value of any environment assignment."""
    release_sha = args.release
    validate_release_sha(release_sha)
    transport = _resolve_transport(args)
    observation = read_migration_observation(
        runner, transport, release_sha=release_sha
    )
    plan = build_migration_plan(observation)
    print("kronika-release migrate-identity preflight")
    print(f"release: {plan.release_sha}")
    print(f"current_release: {plan.current_release_sha}")
    print(f"old_layout: {plan.old_layout.key}")
    print(f"new_layout: {plan.new_layout.key}")
    print(f"plan_digest: {plan.plan_digest}")
    print(f"copy_moves: {len(plan.copy_moves)}")
    print(f"installed_units: {','.join(plan.installed_units)}")
    print(f"observed_units: {len(plan.unit_observations)}")
    print(f"installed_dropins: {len(plan.dropin_installations)}")
    print(f"credential_sources: {len(plan.credential_sources)}")
    print(f"export_installed: {plan.export_installed or 'absent'}")
    print(f"sudo_rule_installed: {plan.sudo_rule_installed or 'absent'}")
    print(f"capture_pointer: {plan.capture_pointer}")
    print(f"tailscale_handlers: {plan.tailscale_handler_count}")
    print(f"tailscale_old_handlers: {plan.tailscale_old_handlers}")
    print(f"required_scripts: {','.join(plan.required_scripts)}")
    print(f"moved_path_keys: {','.join(plan.environment.moved_path_keys)}")
    print(f"preserved_path_keys: {','.join(plan.environment.preserved_path_keys)}")
    return EXIT_OK


def _cmd_migrate_apply(args: argparse.Namespace, runner: Runner) -> int:
    """Mutating migration. This repository cut never executes this path."""
    release_sha = args.release
    validate_release_sha(release_sha)
    if not args.yes:
        raise ReleaseError(
            "migrate-identity apply requires --yes to confirm", EXIT_USAGE
        )
    if not args.preflight_digest:
        raise ReleaseError(
            "migrate-identity apply requires a validated preflight digest",
            EXIT_USAGE,
        )
    transport = _resolve_transport(args)
    observation = read_migration_observation(
        runner, transport, release_sha=release_sha
    )
    plan = build_migration_plan(observation)
    if args.preflight_digest != plan.plan_digest:
        raise ReleaseError(
            "preflight digest does not match the current migration plan",
            EXIT_MIGRATION,
        )
    host = RemoteMigrationHost(
        runner,
        transport,
        plan,
        engine_path=Path(__file__),
        repository_root=Path(__file__).resolve().parents[2],
    )
    existing = host.read_journal()
    if existing is not None:
        if existing.get("outcome") == "completed":
            raise ReleaseError(
                "identity migration is already recorded complete", EXIT_MIGRATION
            )
        raise ReleaseError(
            "an incomplete migration journal exists; recovery is required first",
            EXIT_MIGRATION,
        )
    journal = run_migration_apply(host, plan)
    print(f"kronika-release migrate-identity apply complete: {release_sha}")
    print(f"completed_phases: {','.join(journal['completed_phases'])}")
    return EXIT_OK


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
    state = resolve_host_layout(runner, transport)
    layout = state.web
    current = read_current_release(runner, transport, layout)
    current_path = current.path
    active = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_service_is_active(layout.service),
    ).strip()
    db_revision = read_db_current_revision(runner, transport, current_path, layout)
    backup = read_backup_readiness(runner, transport, current_path, layout)
    web_sha = current.sha
    capture_sha = read_optional_release_sha(
        runner, transport, state.capture_current, state.capture_release_root
    )
    print("kronika-release status")
    print(f"layout: {layout.key}")
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
    state = resolve_host_layout(runner, transport)
    layout = state.web
    current = read_current_release(runner, transport, layout)
    current_path = current.path
    readiness = read_backup_readiness(runner, transport, current_path, layout)
    if readiness != "ready":
        raise ReleaseError("catalog backup is not restore-ready", EXIT_BACKUP_NOT_READY)

    print("kronika-release check")
    print(f"release: {release_sha}")
    print(f"layout: {layout.key}")
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

    state = resolve_host_layout(runner, transport)
    layout = state.web

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

        target = release_dir(release_sha, layout)
        staging = staging_dir(release_sha, layout)
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
        verify_capacity(runner, transport, super_size + ap_size, layout)

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
            runner, **transport, remote_command=cmd_remote_db_status(target, layout)
        )
        db_payload = parse_json_status(status_raw)
        current_revision = db_payload.get("current_revision")
        head_revision = db_payload.get("head_revision")
        if current_revision != head_revision:
            raise ReleaseError("migration-required", EXIT_MIGRATION_REQUIRED)

        # Fresh verified checkpoint before cutover.
        checkpoint = ssh(
            runner,
            **transport,
            remote_command=cmd_remote_run_scheduled_backup(target, layout),
        )
        checkpoint_payload = parse_json_status(checkpoint)
        if checkpoint_payload.get("state") != "succeeded":
            raise ReleaseError("checkpoint failed", EXIT_CHECKPOINT)

        # Capture the previous release for rollback.
        ssh(
            runner,
            **transport,
            remote_command=cmd_remote_capture_current(remote_prev, layout),
        )

        # The installed-unit executable guard runs before the pointer switch and
        # before any restart, and outside the rollback-wrapped block, so a
        # release the installed unit cannot start is refused while the running
        # release still serves.
        unit_executables = verify_unit_executables(runner, transport, target, layout)

        try:
            # Pre-cutover readiness under the target release.
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_database_ready(target, layout),
                )
            except ReleaseError as exc:
                raise ReleaseError(
                    "pre-cutover target readiness failed", EXIT_READINESS
                ) from exc
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_atomic_switch(target, layout),
                )
            except ReleaseError as exc:
                raise ReleaseError("atomic switch failed", EXIT_READINESS) from exc
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_restart_service(layout.service),
                )
            except ReleaseError as exc:
                raise ReleaseError("restart failed", EXIT_READINESS) from exc
            _verify_cutover(runner, transport, target, layout)
        except ReleaseError as exc:
            _rollback(runner, transport, remote_prev, layout)
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

    capture_sha = read_optional_release_sha(
        runner, transport, state.capture_current, state.capture_release_root
    )
    print(f"kronika-release deploy complete: {release_sha}")
    print(format_release_pointers(release_sha, capture_sha))
    print(lock_report(lock_decision))
    print(f"unit_executables: {','.join(unit_executables)}")
    return EXIT_OK


def _verify_cutover(
    runner: Runner,
    transport: dict[str, str],
    target: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> None:
    current_path = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_readlink_current(layout),
    ).strip()
    if current_path != target:
        raise ReleaseError("cutover failed", EXIT_READINESS)
    working_dir = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_systemd_working_directory(layout.service),
    ).strip()
    if working_dir != layout.current:
        raise ReleaseError("service working directory is unexpected", EXIT_READINESS)
    _wait_ready(runner, transport, target, layout)
    logs = ssh(
        runner, **transport, remote_command=cmd_remote_journal(layout.service)
    )
    _assert_logs_sanitized(logs)


def _wait_ready(
    runner: Runner,
    transport: dict[str, str],
    target: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> None:
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
            runner,
            **transport,
            remote_command=cmd_remote_service_active_state(layout.service),
        ).strip()
        result_state = ssh(
            runner,
            **transport,
            remote_command=cmd_remote_service_result(layout.service),
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
                    remote_command=cmd_remote_check_health(target, layout),
                )
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_database_ready(target, layout),
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
    for token in (
        "/etc/framenest/credentials",
        "/etc/kronika/credentials",
        "Authorization:",
        "Bearer ",
        "BEGIN ",
    ):
        if token in logs:
            raise ReleaseError("unsanitized log content", EXIT_SERVICE_TERMINAL)


def _rollback(
    runner: Runner,
    transport: dict[str, str],
    previous_path: str,
    layout: WebLayout = OLD_WEB_LAYOUT,
) -> None:
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
        validate_remote_path(prev, layout.release_root)
        verify_unit_executables(runner, transport, prev, layout)
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_check_database_ready(prev, layout),
            )
        except ReleaseError as exc:
            raise ReleaseError(
                "rollback pre-restart readiness failed", EXIT_ROLLBACK
            ) from exc
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_atomic_switch(prev, layout),
            )
        except ReleaseError as exc:
            raise ReleaseError("rollback switch failed", EXIT_ROLLBACK) from exc
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_restart_service(layout.service),
            )
        except ReleaseError as exc:
            raise ReleaseError("rollback restart failed", EXIT_ROLLBACK) from exc
        _verify_cutover(runner, transport, prev, layout)
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
    state = resolve_host_layout(runner, transport)
    layout = state.web

    target = release_dir(release_sha, layout)
    ssh(runner, **transport, remote_command=cmd_remote_test_exists(target))
    # The installed release must resolve its identity under every accepted
    # marker spelling, and agree with each other when several are present.
    installed = read_release_markers(runner, transport, target)
    if installed.sha != release_sha:
        raise ReleaseError("installed release SHA does not match", EXIT_SOURCE_GATE)
    # The installed-unit executable guard runs before the pointer switch and
    # before any restart.
    unit_executables = verify_unit_executables(runner, transport, target, layout)

    remote_prev = f"{REMOTE_DEPLOY_DIR}/rollback-previous-release"
    with contextlib.ExitStack() as stack:
        lock_decision = stack.enter_context(remote_deploy_lock(runner, transport))
        try:
            ssh(
                runner,
                **transport,
                remote_command=cmd_remote_capture_current(remote_prev, layout),
            )
            try:
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_check_database_ready(target, layout),
                )
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_atomic_switch(target, layout),
                )
                ssh(
                    runner,
                    **transport,
                    remote_command=cmd_remote_restart_service(layout.service),
                )
                _verify_cutover(runner, transport, target, layout)
            except ReleaseError as exc:
                _rollback(runner, transport, remote_prev, layout)
                raise ReleaseError(
                    f"rollback target failed; previous release restored ({exc})",
                    exc.exit_code,
                ) from exc
        finally:
            ssh(runner, **transport, remote_command=cmd_remote_remove_file(remote_prev))

    capture_sha = read_optional_release_sha(
        runner, transport, state.capture_current, state.capture_release_root
    )
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
    state = resolve_host_layout(runner, transport)
    layout = state.web
    identity = capture_runtime_identity(runner, release_sha)
    target = release_dir(release_sha, layout)

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
        runner,
        **transport,
        remote_command=cmd_remote_readlink_current(layout),
    ).strip()
    validate_remote_path(web_path, layout.release_root)
    web_sha = read_release_markers(runner, transport, web_path).sha

    capture_link = ssh(
        runner,
        **transport,
        remote_command=cmd_remote_read_optional_link(state.capture_current),
    ).strip()
    if capture_link == "query-failed":
        raise ReleaseError("capture release pointer state is unverifiable", EXIT_LAYOUT)
    if capture_link != "absent":
        validate_remote_path(capture_link, state.capture_release_root)
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
    ssh(
        runner,
        **transport,
        remote_command=cmd_remote_atomic_switch_capture(
            target, state.capture_current
        ),
    )
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
