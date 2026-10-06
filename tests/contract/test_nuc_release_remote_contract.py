"""Remote contract tests for the NUC release-update engine.

These tests exercise command building and the deploy/rollback orchestration flow
using a fake command runner and synthetic archives. They never contact a real
NUC, never use real sudo/systemd, and never inspect credentials or media.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import sys
import tarfile

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"
LEGACY_ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest_release.py"
CANONICAL_ENTRY_POINT = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika-release"
LEGACY_ENTRY_POINT = REPOSITORY_ROOT / "deploy" / "ubuntu" / "framenest-release"

_SPEC = importlib.util.spec_from_file_location("kronika_release", ENGINE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
engine = importlib.util.module_from_spec(_SPEC)
sys.modules["kronika_release"] = engine
_SPEC.loader.exec_module(engine)

RELEASE = "a" * 40
AP_PIN = "b" * 40
PREV = "c" * 40
TARGET = f"/opt/framenest/releases/{RELEASE}"
STAGING = f"/opt/framenest/releases/{RELEASE}.staging"
PREV_PATH = f"/opt/framenest/releases/{PREV}"

#: Every marker spelling this fake release tree carries. ``None`` means all four,
#: which is the both-present-and-equal case of the marker matrix.
ALL_MARKER_SPELLINGS = (
    *engine.ACCEPTED_RELEASE_MANIFEST_MARKERS,
    *engine.ACCEPTED_RELEASE_SHA_MARKERS,
)
CANONICAL_MANIFEST_MARKER = engine.ACCEPTED_RELEASE_MANIFEST_MARKERS[1]
CANONICAL_SHA_MARKER = engine.ACCEPTED_RELEASE_SHA_MARKERS[1]

#: A realistic ``systemctl show`` answer for the installed unit, drop-ins
#: included, as systemd reports the effective configuration.
DEFAULT_UNIT_SHOW = (
    "ExecStart={ path=/opt/framenest/current/.venv/bin/framenest-production ; "
    "argv[]=/opt/framenest/current/.venv/bin/framenest-production serve ; "
    "ignore_errors=no ; start_time=[not set] ; stop_time=[not set] ; pid=0 ; "
    "code=(null) ; status=0/0 }\n"
    "ExecStartPre={ path=/opt/framenest/current/.venv/bin/framenest-production ; "
    "argv[]=/opt/framenest/current/.venv/bin/framenest-production "
    "check-database-ready ; ignore_errors=no ; start_time=[not set] ; "
    "stop_time=[not set] ; pid=0 ; code=(null) ; status=0/0 }"
)

#: Effective layout probes for the two candidate web units. The default fake
#: host is in the former layout; the canonical unit does not exist yet.
DEFAULT_LAYOUT_SHOWS = {
    "framenest.service": (
        "LoadState=loaded\n"
        "ActiveState=active\n"
        "UnitFileState=enabled\n"
        "User=framenest\n"
        "Group=framenest\n"
        "WorkingDirectory=/opt/framenest/current\n"
        "ExecStart={ path=/opt/framenest/current/.venv/bin/framenest-production ; "
        "argv[]=/opt/framenest/current/.venv/bin/framenest-production serve ; "
        "ignore_errors=no ; start_time=[not set] ; stop_time=[not set] ; pid=0 ; "
        "code=(null) ; status=0/0 }"
    ),
    "kronika.service": (
        "LoadState=not-found\n"
        "ActiveState=inactive\n"
        "UnitFileState=not-found\n"
        "User=\n"
        "Group=\n"
        "WorkingDirectory=\n"
        "ExecStart="
    ),
}

#: Marker-presence, SHA-marker and manifest reads carry the marker name last, so
#: one pattern resolves every spelling rather than one literal per spelling.
SHA_MARKER_READ = re.compile(r"cat (/[^ ]+)/(\.\S+-release-sha)$")
MANIFEST_MARKER_READ = re.compile(r"cat (/[^ ]+)/(\.\S+-release-manifest\.json)$")
PRESENCE_LINE = re.compile(r"test -e (/[^ ]+)/(\.\S+); then echo (manifest|sha) ")
REGULAR_EXECUTABLE = re.compile(r"sh -c 'test -f (\S+) -a ! -L \S+ -a -x ")

SSH = ["ssh", *engine.SSH_OPTIONS, "-i", "identity", "op@nuc"]


def _write_tar(path: str) -> None:
    with tarfile.open(path, "w") as archive:
        for member, content in (("pyproject.toml", b"[tool.poetry]\n"), ("poetry.lock", b"lock")):
            info = tarfile.TarInfo(name=member)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))


class FakeRunner:
    def __init__(self, *, fail: str | None = None, fail_occurrence: int = 1,
                 fail_message: str = "simulated failure", exit_code: int | None = None) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.fail = fail
        self.fail_occurrence = fail_occurrence
        self.fail_message = fail_message
        self.exit_code = exit_code
        self._matches = 0
        self.current = PREV_PATH
        #: Which marker spellings the fake trees carry. ``None`` means all four.
        self.markers: tuple[str, ...] | None = None
        #: Which manifest key the fake manifests carry.
        self.manifest_key = engine.RELEASE_SHA_MANIFEST_KEY
        #: What every SHA marker read returns, or ``None`` to derive it from the
        #: release directory the marker belongs to.
        self.sha_value: str | None = None
        #: What the manifest key declares, or ``None`` to follow the SHA markers.
        self.manifest_release_value: str | None = None
        #: Per-marker overrides, so one spelling can disagree with another.
        self.sha_by_marker: dict[str, str] = {}
        self.manifest_by_marker: dict[str, str] = {}
        #: What ``systemctl show`` returns for the effective unit properties.
        self.unit_show: str | None = DEFAULT_UNIT_SHOW
        #: Effective layout probe answers per candidate service.
        self.layout_shows: dict[str, str] = dict(DEFAULT_LAYOUT_SHOWS)
        #: What the canonical capture pointer reports, if probed.
        self.new_capture_link = "absent"
        #: Console scripts the target release is missing.
        self.missing_executables: frozenset[str] = frozenset()
        #: Symlinks standing where a console script must be.
        self.symlinked_executables: frozenset[str] = frozenset()
        #: Ownership record the existing remote lock carries, or ``None`` for a
        #: lock with no readable ownership.
        self.lock_owner: str | None = None
        #: Whether the remote deploy directory already exists.
        self.locked = False
        #: Whether reclaiming an existing lock succeeds.
        self.reclaim_fails = False
        self.switched: list[str] = []

    def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
        self.calls.append((list(argv), input_bytes))
        combined = " ".join(argv)
        if self.fail is not None and self.fail in combined:
            self._matches += 1
            if self._matches == self.fail_occurrence:
                raise engine.ReleaseError(self.fail_message, self.exit_code or engine.EXIT_TRANSPORT)
        return self._respond(combined, input_bytes)

    def _respond(self, combined: str, input_bytes: bytes | None) -> str:
        if combined.startswith("git "):
            return self._git_respond(combined)
        if combined.startswith("ssh "):
            return self._ssh_respond(combined, input_bytes)
        raise AssertionError(f"unexpected command: {combined}")

    def _git_respond(self, combined: str) -> str:
        if "rev-parse --show-toplevel" in combined:
            return "/repo"
        if "-C .ap rev-parse HEAD" in combined:
            return AP_PIN
        if "rev-parse HEAD" in combined:
            return RELEASE
        if "status --porcelain" in combined:
            return ""
        if "ls-remote origin refs/heads/main" in combined:
            return f"{RELEASE}\trefs/heads/main"
        if "ls-tree" in combined and ".ap" in combined:
            return f"160000 commit {AP_PIN}\t.ap"
        if "src/kronika_capture" in combined and "rev-parse" in combined:
            return "d" * 40
        if " show " in combined and "kronika-capture" in combined:
            return "capture-contract\n"
        if "archive --format=tar" in combined:
            idx = combined.split().index("--output") + 1
            _write_tar(combined.split()[idx])
            return ""
        raise AssertionError(f"unexpected git command: {combined}")

    def _marker_presence(self, combined: str) -> str:
        present = ALL_MARKER_SPELLINGS if self.markers is None else self.markers
        lines = [
            f"{kind} {marker}"
            for _path, marker, kind in PRESENCE_LINE.findall(combined)
            if marker in present
        ]
        return "\n".join(lines)

    def _unit_executables(self, combined: str) -> str:
        if self.unit_show is None:
            raise engine.ReleaseError("no such unit", engine.EXIT_TRANSPORT)
        for path in REGULAR_EXECUTABLE.findall(combined):
            relative = path.split("/releases/", 1)[-1].split("/", 1)[-1]
            name = relative.rsplit("/", 1)[-1]
            if relative in self.symlinked_executables or name in self.missing_executables:
                raise engine.ReleaseError("no such file", engine.EXIT_TRANSPORT)
        return ""

    def _sha_for(self, path: str) -> str:
        """Return the identity the fake release directory named by ``path`` has."""
        if self.sha_value is not None:
            return self.sha_value
        return PREV if f"/releases/{PREV}" in path else RELEASE

    def _manifest_payload(self, path: str, marker: str | None = None) -> str:
        release = self._sha_for(path)
        if marker is not None and marker in self.manifest_by_marker:
            release = self.manifest_by_marker[marker]
        elif self.manifest_release_value is not None:
            release = self.manifest_release_value
        payload = engine.make_manifest(
            release_sha=release, ap_pin=AP_PIN,
            superproject_sha256="e" * 64, ap_archive_sha256="f" * 64,
            capture_code_tree="d" * 40,
            capture_runtime_contract_sha256="1" * 64,
            capture_unit_contract_sha256="2" * 64,
            capture_bridge_protocol="1",
        )
        if self.manifest_key != engine.RELEASE_SHA_MANIFEST_KEY:
            payload[self.manifest_key] = payload.pop(engine.RELEASE_SHA_MANIFEST_KEY)
        return json.dumps(payload)

    def _ssh_respond(self, combined: str, input_bytes: bytes | None) -> str:
        if "mkdir -m 0700 /run/framenest-release-deploy" in combined:
            if self.locked:
                raise engine.ReleaseError("exists", engine.EXIT_EXISTS)
            self.locked = True
            return ""
        if "/run/framenest-release-deploy.owner" in combined and "cat >" in combined:
            return ""
        if "/run/framenest-release-deploy.owner" in combined and "cat " in combined:
            if self.lock_owner is None:
                raise engine.ReleaseError("no such file", engine.EXIT_TRANSPORT)
            return self.lock_owner
        if "/run/framenest-release-deploy.owner" in combined and "rm -f" in combined:
            return ""
        if "mv -T /run/framenest-release-deploy" in combined:
            if self.reclaim_fails:
                raise engine.ReleaseError("busy", engine.EXIT_EXISTS)
            self.locked = False
            return ""
        if "/run/framenest-release-deploy.reclaimed-" in combined and "rm -rf" in combined:
            return ""
        if "systemctl show --property=LoadState" in combined:
            for service, payload in self.layout_shows.items():
                if service in combined:
                    return payload
            raise AssertionError(f"unexpected layout probe: {combined}")
        if "systemctl show --property=ExecStart" in combined:
            if self.unit_show is None:
                raise engine.ReleaseError("no such unit", engine.EXIT_TRANSPORT)
            return self.unit_show
        if "then echo manifest" in combined or "then echo sha" in combined:
            return self._marker_presence(combined)
        if REGULAR_EXECUTABLE.search(combined):
            return self._unit_executables(combined)
        if "test -e " in combined:
            return ""
        sha_match = SHA_MARKER_READ.search(combined)
        if sha_match is not None:
            marker = sha_match.group(2)
            if marker in self.sha_by_marker:
                return self.sha_by_marker[marker]
            return self._sha_for(sha_match.group(1))
        manifest_match = MANIFEST_MARKER_READ.search(combined)
        if manifest_match is not None:
            return self._manifest_payload(manifest_match.group(1), manifest_match.group(2))
        if "test ! -e" in combined:
            return ""
        if "test -x /opt/framenest/tooling" in combined:
            return ""
        if "poetry --version" in combined:
            return "Poetry (version 2.4.1)"
        if "python3.13 --version" in combined:
            return "Python 3.13.14"
        if "df -Pk /opt/framenest" in combined:
            return "/dev/sda1 100000000 1000 99999000 1% /opt/framenest"
        if "cat > /run/framenest-release-deploy/framenest_release.py" in combined:
            return ""
        if "cat > /run/framenest-release-deploy/superproject.tar" in combined:
            return ""
        if "cat > /run/framenest-release-deploy/ap.tar" in combined:
            return ""
        if "install -d -o root -g root -m 0755" in combined:
            return ""
        if "_remote-extract" in combined:
            return ""
        if "_remote-relocate-venv-shebangs" in combined:
            return ""
        if "cat > /opt/framenest/releases/" in combined and "poetry.toml" in combined:
            return ""
        if (
            "cat > /opt/framenest/releases/" in combined
            and f"/{engine.RELEASE_MANIFEST_MARKER}" in combined
        ):
            return ""
        if (
            "cat > /opt/framenest/releases/" in combined
            and f"/{engine.RELEASE_SHA_MARKER}" in combined
        ):
            return ""
        if "sha256sum /opt/framenest/releases/" in combined and "poetry.lock" in combined:
            return "deadbeef  /opt/framenest/releases/placeholder/poetry.lock"
        if "check --lock" in combined:
            return ""
        if "env use" in combined:
            return ""
        if "install --only main" in combined:
            return ""
        if "chown -R root:root" in combined:
            return ""
        if "chmod -R a-w" in combined:
            return ""
        if "mv /opt/framenest/releases/" in combined and ".staging" in combined:
            return ""
        if "framenest-db status" in combined:
            return '{"operation":"status","state":"at_head","current_revision":"0028","head_revision":"0028"}'
        if "framenest-backup status" in combined:
            return '{"operation":"status","restore_readiness":"ready"}'
        if "framenest-backup run-scheduled" in combined:
            return '{"operation":"run-scheduled","state":"succeeded","bundle_id":"b1"}'
        if "test -L /opt/framenest/capture-current" in combined:
            return "absent"
        if "test -L /opt/kronika/capture-current" in combined:
            return self.new_capture_link
        if "readlink -n /opt/framenest/current" in combined:
            return self.current
        if "previous-release" in combined and "printf" in combined:
            return ""
        if "check-database-ready" in combined:
            return '{"operation":"check-database-ready","state":"ready"}'
        if "ln -s" in combined and "current.next" in combined:
            parts = combined.split()
            idx = next(i for i, part in enumerate(parts) if part.endswith("current.next"))
            self.switched.append(parts[idx - 1])
            self.current = parts[idx - 1]
            return ""
        if "mv -T /opt/framenest/current.next" in combined:
            return ""
        if "restart framenest.service" in combined:
            return ""
        if "ActiveState" in combined:
            return "active"
        if "-p Result --value" in combined:
            return "success"
        if "systemctl is-active" in combined:
            return "active"
        if "WorkingDirectory" in combined:
            return "/opt/framenest/current"
        if "check-health" in combined:
            return '{"operation":"check-health","state":"ready"}'
        if "journalctl -u framenest.service" in combined:
            return ""
        if "rm -f /run/framenest-release-deploy" in combined:
            self.locked = False
            return ""
        if "rmdir /run/framenest-release-deploy" in combined:
            self.locked = False
            return ""
        if "cat /run/framenest-release-deploy/previous-release" in combined:
            return PREV_PATH
        if "cat /run/framenest-release-deploy/rollback-previous-release" in combined:
            return PREV_PATH
        raise AssertionError(f"unexpected ssh command: {combined}")


def _dead_pid() -> int:
    """Return a pid that is certainly not running on this workstation."""

    for candidate in range(4_000_000, 4_000_050):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except PermissionError:  # pragma: no cover - not reachable in practice
            continue
    raise AssertionError("no certainly-unused pid found")  # pragma: no cover


def _args(command: str, extra: list[str] | None = None) -> list[str]:
    argv = [command]
    if command in ("check", "deploy", "rollback"):
        argv += ["--release", RELEASE]
    if command in ("deploy", "rollback"):
        argv += ["--yes"]
    argv += ["--target", "nuc", "--user", "op", "--identity", "identity"]
    if extra:
        argv += extra
    return argv


def _combined(runner: FakeRunner) -> str:
    return "\n".join(" ".join(argv) for argv, _ in runner.calls)


def _index(runner: FakeRunner, needle: str) -> int:
    for i, (argv, _) in enumerate(runner.calls):
        if argv and argv[0] == "ssh" and needle in " ".join(argv):
            return i
    raise AssertionError(f"missing ssh command containing {needle!r}")


def _ssh_combined(runner: FakeRunner) -> str:
    return "\n".join(" ".join(argv) for argv, _ in runner.calls if argv and argv[0] == "ssh")


# --- Command builder contracts ---

def test_remote_commands_never_invoke_uv_or_migrate() -> None:
    builders = [
        engine.cmd_remote_poetry_check_lock(TARGET),
        engine.cmd_remote_poetry_env_use(TARGET),
        engine.cmd_remote_poetry_install(TARGET),
        engine.cmd_remote_atomic_switch(TARGET),
        engine.cmd_remote_restart_service(),
        engine.cmd_remote_check_database_ready(TARGET),
        engine.cmd_remote_run_scheduled_backup(TARGET),
    ]
    for command in builders:
        assert "uv " not in command
        assert "migrate" not in command
        assert "uv:" not in command


def test_poetry_commands_use_exact_tooling_paths() -> None:
    assert engine.POETRY_BIN in engine.cmd_remote_poetry_check_lock(TARGET)
    assert engine.POETRY_BIN in engine.cmd_remote_poetry_env_use(TARGET)
    assert engine.CPYTHON_BIN in engine.cmd_remote_poetry_env_use(TARGET)
    assert engine.POETRY_BIN in engine.cmd_remote_poetry_install(TARGET)
    assert "--only main" in engine.cmd_remote_poetry_install(TARGET)
    assert "--no-interaction" in engine.cmd_remote_poetry_install(TARGET)
    assert "--no-ansi" in engine.cmd_remote_poetry_install(TARGET)


def test_atomic_switch_creates_new_symlink_then_renames() -> None:
    command = engine.cmd_remote_atomic_switch(TARGET)
    assert "ln -s" in command
    assert "/opt/framenest/current.next" in command
    assert "mv -T /opt/framenest/current.next" in command
    assert command.index("ln -s") < command.index("mv -T")


def test_service_account_commands_establish_release_cwd_and_env() -> None:
    command = engine.cmd_remote_db_status(TARGET)
    assert f"--chdir={TARGET}" in command
    assert f"env FRAMENEST_ENV_FILE={engine.ENV_FILE}" in command
    assert "-u framenest" in command


def test_production_cli_uses_systemd_environment_file() -> None:
    for command in (
        engine.cmd_remote_check_database_ready(TARGET),
        engine.cmd_remote_check_health(TARGET),
    ):
        assert "systemd-run" in command
        assert f"--working-directory={TARGET}" in command
        assert f"EnvironmentFile={engine.ENV_FILE}" in command
        assert "--uid=framenest" in command
        assert "--gid=framenest" in command
        assert "FRAMENEST_ENV_FILE" not in command
        assert "uv " not in command
        assert "migrate" not in command


def test_cmd_remote_extract_emits_nested_private_argv_and_extracts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transferred-engine extract must be nested ``_remote _remote-extract``.

    Top-level ``_remote-extract`` is invalid parser input and must stay so.
    """
    monkeypatch.setattr(engine, "REMOTE_DEPLOY_DIR", str(tmp_path))
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    archive = tmp_path / "safe.tar"
    destination = tmp_path / "out"
    destination.mkdir()
    _write_tar(str(archive))
    remote_engine = str(tmp_path / "framenest_release.py")

    command = engine.cmd_remote_extract(str(archive), str(destination), remote_engine)
    argv = shlex.split(command)
    assert argv[:3] == ["sudo", "-n", "python3"]
    remaining = argv[4:]

    parsed = engine._build_parser().parse_args(remaining)
    assert parsed.command == "_remote"
    assert parsed.remote_command == "_remote-extract"
    assert parsed.archive == str(archive)
    assert parsed.destination == str(destination)

    result = engine.main(remaining)
    assert result == engine.EXIT_OK
    assert (destination / "pyproject.toml").read_bytes() == b"[tool.poetry]\n"
    assert (destination / "poetry.lock").read_bytes() == b"lock"

    with pytest.raises(SystemExit) as exc:
        engine._build_parser().parse_args(
            ["_remote-extract", "--archive", str(archive), "--destination", str(destination)]
        )
    assert exc.value.code == 2


def test_cmd_remote_relocate_venv_shebangs_emits_nested_private_argv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Transferred-engine shebang relocation must be nested ``_remote _remote-relocate-venv-shebangs``.

    Top-level ``_remote-relocate-venv-shebangs`` is invalid parser input and must stay so.
    """
    monkeypatch.setattr(engine, "RELEASE_ROOT", str(tmp_path))
    sha = RELEASE
    staging = tmp_path / f"{sha}.staging"
    final = tmp_path / sha
    bindir = staging / ".venv" / "bin"
    bindir.mkdir(parents=True)
    (bindir / "framenest-db").write_text(
        f"#!{staging}/.venv/bin/python\nprint('db')\n", encoding="utf-8"
    )
    (bindir / "framenest-backup").write_text(
        f"#!{staging}/.venv/bin/python\nprint('backup')\n", encoding="utf-8"
    )
    remote_engine = str(tmp_path / "framenest_release.py")

    command = engine.cmd_remote_relocate_venv_shebangs(
        str(staging), str(final), remote_engine
    )
    argv = shlex.split(command)
    assert argv[:3] == ["sudo", "-n", "python3"]
    remaining = argv[4:]

    parsed = engine._build_parser().parse_args(remaining)
    assert parsed.command == "_remote"
    assert parsed.remote_command == "_remote-relocate-venv-shebangs"
    assert parsed.staging == str(staging)
    assert parsed.final == str(final)

    result = engine.main(remaining)
    assert result == engine.EXIT_OK
    assert (bindir / "framenest-db").read_text(encoding="utf-8").startswith(
        f"#!{final}/.venv/bin/python"
    )

    with pytest.raises(SystemExit) as exc:
        engine._build_parser().parse_args(
            [
                "_remote-relocate-venv-shebangs",
                "--staging",
                str(staging),
                "--final",
                str(final),
            ]
        )
    assert exc.value.code == 2


def _sudo_sh_c_script(command: str) -> str:
    parts = shlex.split(command)
    assert parts[:4] == ["sudo", "-n", "sh", "-c"], command
    assert len(parts) == 5, command
    return parts[4]


def test_cmd_remote_write_poetry_toml_uses_stdin_not_nested_quotes(
    tmp_path: Path,
) -> None:
    """Payload must travel as stdin; nested shlex quotes made in-project a command."""
    command = engine.cmd_remote_write_poetry_toml(str(tmp_path))
    assert "in-project" not in command
    assert engine.POETRY_TOML not in command
    script = _sudo_sh_c_script(command)
    dest = tmp_path / "poetry.toml"
    assert script == f"umask 077; cat > {shlex.quote(str(dest))}"
    subprocess.run(["sh", "-c", script], input=engine.POETRY_TOML.encode("utf-8"), check=True)
    assert dest.read_bytes() == engine.POETRY_TOML.encode("utf-8")


def test_cmd_remote_write_markers_uses_stdin_not_nested_quotes(tmp_path: Path) -> None:
    """Manifest JSON and SHA must not be nested inside single-quoted sh -c strings."""
    manifest_json = json.dumps(
        engine.make_manifest(
            release_sha=RELEASE,
            ap_pin=AP_PIN,
            superproject_sha256="e" * 64,
            ap_archive_sha256="f" * 64,
            capture_code_tree="d" * 40,
            capture_runtime_contract_sha256="1" * 64,
            capture_unit_contract_sha256="2" * 64,
            capture_bridge_protocol="1",
        ),
        sort_keys=True,
        separators=(",", ":"),
    )
    sha_payload = RELEASE + "\n"
    manifest_cmd, sha_cmd = engine.cmd_remote_write_markers(str(tmp_path))
    for command in (manifest_cmd, sha_cmd):
        assert manifest_json not in command
        assert RELEASE not in command
        assert "printf %s" not in command
    manifest_script = _sudo_sh_c_script(manifest_cmd)
    sha_script = _sudo_sh_c_script(sha_cmd)
    manifest_dest = tmp_path / ".framenest-release-manifest.json"
    sha_dest = tmp_path / ".framenest-release-sha"
    assert manifest_script == f"umask 077; cat > {shlex.quote(str(manifest_dest))}"
    assert sha_script == f"umask 077; cat > {shlex.quote(str(sha_dest))}"
    subprocess.run(
        ["sh", "-c", manifest_script], input=manifest_json.encode("utf-8"), check=True
    )
    subprocess.run(["sh", "-c", sha_script], input=sha_payload.encode("utf-8"), check=True)
    assert manifest_dest.read_bytes() == manifest_json.encode("utf-8")
    assert sha_dest.read_bytes() == sha_payload.encode("utf-8")


# --- status flow ---

def test_status_positive_path(capsys: pytest.CaptureFixture) -> None:
    runner = FakeRunner()
    result = engine.main(_args("status"), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_OK
    assert "service_active: active" in captured.out
    assert "database_revision: 0028" in captured.out
    assert "backup_restore_readiness: ready" in captured.out
    # Status never transfers a helper or mutates.
    assert not any("cat > /run/framenest-release-deploy" in " ".join(a) for a, _ in runner.calls)


class _PreManifest(FakeRunner):
    """Live pre-ADR-0060 tree: SHA marker present, manifest absent."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.markers = (engine.RELEASE_SHA_MARKER,)
        self.sha_value = PREV


def test_status_pre_manifest_sha_only(capsys: pytest.CaptureFixture) -> None:
    runner = _PreManifest()
    result = engine.main(_args("status"), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_OK
    assert f"active_release: {PREV}" in captured.out
    assert "release_manifest: absent" in captured.out
    assert AP_PIN not in captured.out
    assert "e" * 64 not in captured.out
    assert "f" * 64 not in captured.out
    assert "superproject_archive_sha256" not in captured.out
    assert "ap_archive_sha256" not in captured.out
    assert "ap_gitlink" not in captured.out


def test_check_pre_manifest_uses_current_path_for_backup(
    capsys: pytest.CaptureFixture,
) -> None:
    runner = _PreManifest()
    result = engine.main(_args("check"), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_OK
    assert "backup_restore_readiness: ready" in captured.out
    assert f"current_release: {PREV_PATH}" in captured.out
    assert any(
        "framenest-backup status" in " ".join(argv) for argv, _ in runner.calls
    )


def test_status_missing_both_markers_fails_closed(capsys: pytest.CaptureFixture) -> None:
    class _NoMarkers(FakeRunner):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.markers = ()

    result = engine.main(_args("status"), runner=_NoMarkers())
    captured = capsys.readouterr()
    assert result != engine.EXIT_OK
    assert "command failed" not in captured.err
    assert "absent" in captured.err
    assert "manifest" in captured.err
    assert "SHA" in captured.err or "sha" in captured.err


def test_status_invalid_sha_marker_fails_closed(capsys: pytest.CaptureFixture) -> None:
    class _InvalidSha(FakeRunner):
        def __init__(self, **kwargs) -> None:
            super().__init__(**kwargs)
            self.markers = (engine.RELEASE_SHA_MARKER,)
            self.sha_value = "not-a-valid-release-sha"

    result = engine.main(_args("status"), runner=_InvalidSha())
    captured = capsys.readouterr()
    assert result != engine.EXIT_OK
    assert "command failed" not in captured.err
    assert "invalid" in captured.err


# --- check flow ---

def test_check_positive_path_passes_all_gates(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    runner = FakeRunner()
    result = engine.main(_args("check"), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_OK
    assert RELEASE in captured.out
    assert AP_PIN in captured.out


def test_check_requires_backup_ready(capsys: pytest.CaptureFixture) -> None:
    class _BackupNotReady(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "framenest-backup status" in combined:
                return '{"operation":"status","restore_readiness":"stale"}'
            return super()._ssh_respond(combined, input_bytes)

    result = engine.main(_args("check"), runner=_BackupNotReady())
    assert result == engine.EXIT_BACKUP_NOT_READY


def test_check_requires_matching_tooling() -> None:
    class _BadTooling(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "poetry --version" in combined:
                return "Poetry (version 1.8.0)"
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("check"), runner=_BadTooling()) == engine.EXIT_TOOLING


# --- deploy flow ---

def test_deploy_happy_path_sequence(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    runner = FakeRunner()
    result = engine.main(_args("deploy"), runner=runner)
    combined = _combined(runner)
    assert result == engine.EXIT_OK
    assert "deploy complete" in capsys.readouterr().out

    # Lock and pre-existence gates precede any transfer.
    assert _index(runner, "mkdir -m 0700 /run/framenest-release-deploy") < _index(runner, "cat > /run/framenest-release-deploy/framenest_release.py")
    # Engine transfer precedes archive transfers.
    assert _index(runner, "framenest_release.py") < _index(runner, "superproject.tar")
    assert _index(runner, "superproject.tar") < _index(runner, "ap.tar")
    # Poetry install, then shebang rewrite, then chown/chmod, then rename.
    assert _index(runner, "install --only main") < _index(
        runner, "_remote-relocate-venv-shebangs"
    )
    assert _index(runner, "_remote-relocate-venv-shebangs") < _index(
        runner, "chmod -R a-w"
    )
    assert _index(runner, "_remote-relocate-venv-shebangs") < _index(
        runner, "chown -R root:root"
    )
    assert _index(runner, "chown -R root:root") < _index(runner, "mv /opt/framenest/releases/")
    assert _index(runner, "chmod -R a-w") < _index(runner, "mv /opt/framenest/releases/")
    assert _index(runner, "mv /opt/framenest/releases/") < _index(
        runner, "framenest-db status"
    )
    # Schema and checkpoint before atomic switch.
    assert _index(runner, "framenest-db status") < _index(runner, "ln -s")
    assert _index(runner, "framenest-backup run-scheduled") < _index(runner, "ln -s")
    # Single restart.
    assert _ssh_combined(runner).count("restart framenest.service") == 1
    assert "capture-current.next" not in _ssh_combined(runner)
    assert "restart kronika-capture-runner.service" not in _ssh_combined(runner)
    # Cleanup present.
    assert _index(runner, "rmdir /run/framenest-release-deploy") > _index(runner, "restart framenest.service")


def test_deploy_transfers_engine_and_both_archives_as_stdin() -> None:
    runner = FakeRunner()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_OK
    payloads = [(argv, data) for argv, data in runner.calls if data is not None]
    transferred = [" ".join(argv) for argv, _ in payloads]
    assert any("framenest_release.py" in c for c in transferred)
    assert any("superproject.tar" in c for c in transferred)
    assert any("ap.tar" in c for c in transferred)
    poetry = next(data for argv, data in payloads if any("poetry.toml" in part for part in argv))
    manifest = next(
        data
        for argv, data in payloads
        if any(f"/{engine.RELEASE_MANIFEST_MARKER}" in part for part in argv)
    )
    sha = next(
        data
        for argv, data in payloads
        if any(f"/{engine.RELEASE_SHA_MARKER}" in part for part in argv)
    )
    assert poetry == engine.POETRY_TOML.encode("utf-8")
    assert json.loads(manifest.decode("utf-8"))[engine.RELEASE_SHA_MANIFEST_KEY] == RELEASE
    assert sha == (RELEASE + "\n").encode("utf-8")
    # Engine, two archives, poetry.toml, manifest JSON, SHA marker, lock owner.
    assert len(payloads) == 7


def test_deploy_verifies_archive_hashes_remotely() -> None:
    runner = FakeRunner()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_OK
    combined = _ssh_combined(runner)
    assert "sha256sum /run/framenest-release-deploy/framenest_release.py" in combined
    assert "sha256sum /run/framenest-release-deploy/superproject.tar" in combined
    assert "sha256sum /run/framenest-release-deploy/ap.tar" in combined


def test_deploy_verifies_committed_lock_unchanged() -> None:
    runner = FakeRunner()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_OK
    hashes = [c for c in _ssh_combined(runner).splitlines() if "poetry.lock" in c and "sha256sum" in c]
    assert len(hashes) == 2


def test_deploy_materializes_ap_under_release() -> None:
    runner = FakeRunner()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_OK
    combined = _ssh_combined(runner)
    assert ".staging/.ap" in combined or ".ap" in combined


def test_deploy_rejects_existing_target() -> None:
    class _Existing(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "test ! -e /opt/framenest/releases/" in combined and ".staging" not in combined:
                raise engine.ReleaseError("exists", engine.EXIT_EXISTS)
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("deploy"), runner=_Existing()) == engine.EXIT_EXISTS


def test_deploy_refuses_an_ownerless_existing_remote_lock(
    capsys: pytest.CaptureFixture,
) -> None:
    """An interrupted pre-guard deploy left no ownership record, so it refuses."""

    runner = FakeRunner()
    runner.locked = True
    runner.lock_owner = None

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_EXISTS
    captured = capsys.readouterr()
    assert "existing remote lock without readable ownership" in captured.err
    assert "ln -s" not in _ssh_combined(runner)
    assert "restart framenest.service" not in _ssh_combined(runner)


def test_deploy_refuses_a_lock_owned_by_another_live_run(
    capsys: pytest.CaptureFixture,
) -> None:
    """A record naming a live process on this workstation is never reclaimed."""

    runner = FakeRunner()
    runner.locked = True
    runner.lock_owner = (
        f"{'a' * 32} {os.getpid()} 1 {socket.gethostname()}"
    )

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_EXISTS
    assert "existing remote lock owned by another run" in capsys.readouterr().err
    assert "mv -T /run/framenest-release-deploy" not in _ssh_combined(runner)


def test_deploy_reclaims_a_lock_whose_recorded_process_is_gone(
    capsys: pytest.CaptureFixture,
) -> None:
    runner = FakeRunner()
    runner.locked = True
    runner.lock_owner = (
        f"{'a' * 32} {_dead_pid()} 1 {socket.gethostname()}"
    )

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_OK
    output = capsys.readouterr().out
    assert "remote_lock: reclaimed (abandoned-owner)" in output
    assert "mv -T /run/framenest-release-deploy" in _ssh_combined(runner)
    assert "rm -rf /run/framenest-release-deploy.reclaimed-abandoned-owner" in _ssh_combined(runner)


def test_deploy_reclaims_only_its_own_recorded_identity(
    capsys: pytest.CaptureFixture,
) -> None:
    """This run's own prior lock is reclaimable without a liveness argument."""

    owner = engine.deploy_lock_owner_record()
    runner = FakeRunner()
    runner.locked = True
    runner.lock_owner = owner

    assert engine.classify_deploy_lock_owner(owner, owner) == "own-identity"
    assert engine.classify_deploy_lock_owner(owner, engine.deploy_lock_owner_record()) is None


def test_deploy_refuses_when_the_reclaim_move_fails(
    capsys: pytest.CaptureFixture,
) -> None:
    runner = FakeRunner()
    runner.locked = True
    runner.reclaim_fails = True
    runner.lock_owner = f"{'a' * 32} {_dead_pid()} 1 {socket.gethostname()}"

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_EXISTS
    assert "existing remote lock could not be reclaimed" in capsys.readouterr().err


def test_deploy_releases_the_lock_on_a_failure_inside_the_operation() -> None:
    """The lock is now released on every exit path, not only on success."""

    class _SchemaDiff(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "framenest-db status" in combined:
                return '{"operation":"status","state":"behind","current_revision":"0027","head_revision":"0028"}'
            return super()._ssh_respond(combined, input_bytes)

    runner = _SchemaDiff()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_MIGRATION_REQUIRED
    assert runner.locked is False
    assert "rm -f /run/framenest-release-deploy.owner" in _ssh_combined(runner)


def test_deploy_rejects_insufficient_capacity() -> None:
    class _NoSpace(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "df -Pk /opt/framenest" in combined:
                return "Filesystem 1K-blocks Used Available Use% Mounted\n/dev/sda1 100 50 10 50% /opt/framenest"
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("deploy"), runner=_NoSpace()) == engine.EXIT_CAPACITY


def test_deploy_schema_mismatch_stops_before_cutover() -> None:
    class _SchemaDiff(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "framenest-db status" in combined:
                return '{"operation":"status","state":"behind","current_revision":"0027","head_revision":"0028"}'
            return super()._ssh_respond(combined, input_bytes)

    runner = _SchemaDiff()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_MIGRATION_REQUIRED
    assert "ln -s" not in _ssh_combined(runner)
    assert "restart framenest.service" not in _ssh_combined(runner)


def test_deploy_checkpoint_failure_stops_before_cutover() -> None:
    class _BadCheckpoint(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "framenest-backup run-scheduled" in combined:
                return '{"operation":"run-scheduled","state":"failed"}'
            return super()._ssh_respond(combined, input_bytes)

    runner = _BadCheckpoint()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_CHECKPOINT
    assert "ln -s" not in _ssh_combined(runner)


def test_deploy_lock_changed_is_poetry_failure() -> None:
    class _LockChanged(FakeRunner):
        def __init__(self):
            super().__init__()
            self._count = 0

        def _ssh_respond(self, combined, input_bytes):
            if "sha256sum /opt/framenest/releases/" in combined and "poetry.lock" in combined:
                self._count += 1
                return f"{'deadbeef' if self._count == 1 else 'cafebabe'}  /x/poetry.lock"
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("deploy"), runner=_LockChanged()) == engine.EXIT_POETRY


def test_deploy_post_switch_failure_rolls_back(capsys: pytest.CaptureFixture) -> None:
    class _PostSwitchFail(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self._journal_failures = 0

        def _ssh_respond(self, combined, input_bytes):
            if "journalctl -u framenest.service" in combined:
                self._journal_failures += 1
                if self._journal_failures == 1:
                    raise engine.ReleaseError("terminal", engine.EXIT_SERVICE_TERMINAL)
            return super()._ssh_respond(combined, input_bytes)

    runner = _PostSwitchFail()
    result = engine.main(_args("deploy"), runner=runner)
    assert result == engine.EXIT_SERVICE_TERMINAL
    # Rollback restores the previous release symlink and restarts once more.
    assert _ssh_combined(runner).count("restart framenest.service") == 2
    assert _index(runner, "cat /run/framenest-release-deploy/previous-release") > 0


def test_deploy_rollback_failure_is_distinct(capsys: pytest.CaptureFixture) -> None:
    class _RollbackFail(FakeRunner):
        def __init__(self):
            super().__init__()
            self._restarts = 0

        def _ssh_respond(self, combined, input_bytes):
            if "journalctl -u framenest.service" in combined:
                raise engine.ReleaseError("terminal", engine.EXIT_SERVICE_TERMINAL)
            if "restart framenest.service" in combined:
                self._restarts += 1
                if self._restarts == 2:
                    raise engine.ReleaseError("rollback restart failed", engine.EXIT_ROLLBACK)
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("deploy"), runner=_RollbackFail()) == engine.EXIT_ROLLBACK


def test_deploy_cleanup_failure_is_distinct(capsys: pytest.CaptureFixture) -> None:
    class _CleanupFail(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "rmdir /run/framenest-release-deploy" in combined:
                raise engine.ReleaseError("cleanup failed", engine.EXIT_CLEANUP)
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("deploy"), runner=_CleanupFail()) == engine.EXIT_CLEANUP


# --- rollback flow ---

def test_rollback_happy_path(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    runner = FakeRunner()
    result = engine.main(_args("rollback"), runner=runner)
    assert result == engine.EXIT_OK
    captured = capsys.readouterr().out
    assert "rollback complete" in captured
    assert "web_release: " in captured
    assert "capture_release: absent" in captured
    assert "remote_lock: acquired (none)" in captured
    assert "unit_executables: .venv/bin/framenest-production" in captured
    combined = _ssh_combined(runner)
    assert combined.count("restart framenest.service") == 1
    assert "capture-current.next" not in combined
    assert "kronika-capture-runner" not in combined
    assert "/opt/framenest/releases/" not in "\n".join(
        line for line in combined.splitlines() if "rm " in line or "rmdir" in line
    )


def test_rollback_requires_yes() -> None:
    runner = FakeRunner()
    argv = ["rollback", "--release", RELEASE, "--target", "nuc", "--user", "op", "--identity", "identity"]
    assert engine.main(argv, runner=runner) == engine.EXIT_USAGE


def test_rollback_missing_target_release_fails() -> None:
    class _Missing(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "test -e /opt/framenest/releases/" in combined:
                raise engine.ReleaseError("missing", engine.EXIT_EXISTS)
            return super()._ssh_respond(combined, input_bytes)

    assert engine.main(_args("rollback"), runner=_Missing()) == engine.EXIT_EXISTS


class _FakeClock:
    def __init__(self, *, jump: float) -> None:
        self.t = 0.0
        self.jump = jump

    def monotonic(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        self.t += self.jump if self.jump else seconds


def test_wait_ready_retries_transient_health_then_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    clock = _FakeClock(jump=1)
    monkeypatch.setattr(engine.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(engine.time, "sleep", clock.sleep)
    runner = FakeRunner(fail="check-health", fail_occurrence=1)
    result = engine.main(_args("deploy"), runner=runner)
    assert result == engine.EXIT_OK
    assert "command failed" not in capsys.readouterr().err
    assert _ssh_combined(runner).count("check-health") >= 2


def test_wait_ready_deadline_expiry_is_distinct_when_rollback_succeeds(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    clock = _FakeClock(jump=engine.READINESS_DEADLINE_SECONDS + 1)
    monkeypatch.setattr(engine.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(engine.time, "sleep", clock.sleep)
    runner = FakeRunner(fail="check-health", fail_occurrence=1)
    result = engine.main(_args("deploy"), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_READINESS_TIMEOUT
    assert "service readiness deadline exceeded" in captured.err
    assert "command failed" not in captured.err


def test_rollback_uses_the_same_bounded_readiness(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    clock = _FakeClock(jump=1)
    monkeypatch.setattr(engine.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(engine.time, "sleep", clock.sleep)

    runner = FakeRunner(fail="check-health", fail_occurrence=1)
    result = engine.main(_args("rollback"), runner=runner)
    assert result == engine.EXIT_OK
    assert "command failed" not in capsys.readouterr().err
    assert _ssh_combined(runner).count("check-health") >= 2


def test_pre_cutover_readiness_failure_is_classified(
    capsys: pytest.CaptureFixture,
) -> None:
    class _PreCutoverFail(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self._production_db_ready = 0

        def _ssh_respond(self, combined, input_bytes):
            if "systemd-run" in combined and "check-database-ready" in combined:
                self._production_db_ready += 1
                if self._production_db_ready == 1:
                    raise engine.ReleaseError(
                        "command failed",
                        engine.EXIT_TRANSPORT,
                        remote_exit=4,
                    )
            return super()._ssh_respond(combined, input_bytes)

    result = engine.main(_args("deploy"), runner=_PreCutoverFail())
    captured = capsys.readouterr()
    assert result == engine.EXIT_READINESS
    assert "pre-cutover target readiness failed" in captured.err
    assert "command failed" not in captured.err


# --- sanitized output ---

def test_output_never_contains_secrets_or_identity() -> None:
    runner = FakeRunner()
    engine.main(_args("deploy"), runner=runner)
    # The engine prints no transport identity, no secret-bearing patterns.
    for argv, _ in runner.calls:
        command = " ".join(argv)
        assert "password" not in command.lower()
        assert "BEGIN " not in command
        assert "NOPASSWD" not in command
        assert "sudo -S" not in command


def test_first_causal_error_is_preserved(capsys: pytest.CaptureFixture) -> None:
    class _SchemaDiff(FakeRunner):
        def _ssh_respond(self, combined, input_bytes):
            if "framenest-db status" in combined:
                return '{"operation":"status","state":"behind","current_revision":"0027","head_revision":"0028"}'
            return super()._ssh_respond(combined, input_bytes)

    result = engine.main(_args("deploy"), runner=_SchemaDiff())
    assert result == engine.EXIT_MIGRATION_REQUIRED
    assert "migration-required" in capsys.readouterr().err


# --- the marker matrix, proven across every reader ---


def _release_runner(**kwargs) -> FakeRunner:
    runner = FakeRunner(**kwargs)
    runner.markers = ALL_MARKER_SPELLINGS
    return runner


def _disagreeing(**overrides: object) -> FakeRunner:
    """A runner whose present markers disagree by the requested mechanism."""
    runner = _release_runner()
    for name, value in overrides.items():
        setattr(runner, name, value)
    return runner


@pytest.mark.parametrize(
    "markers",
    [
        pytest.param((engine.RELEASE_MANIFEST_MARKER,), id="old-manifest-only"),
        pytest.param((CANONICAL_MANIFEST_MARKER,), id="new-manifest-only"),
        pytest.param(
            (engine.RELEASE_SHA_MARKER,), id="old-sha-only"
        ),
        pytest.param((CANONICAL_SHA_MARKER,), id="new-sha-only"),
        pytest.param(ALL_MARKER_SPELLINGS, id="both-equal"),
    ],
)
def test_every_reader_resolves_under_each_marker_matrix_row(
    markers: tuple[str, ...], capsys: pytest.CaptureFixture
) -> None:
    expected = {
        "status": f"active_release: {PREV}",
        "check": f"current_release: {PREV_PATH}",
        "rollback": f"web_release: {RELEASE}",
    }
    for command, marker in expected.items():
        runner = _release_runner()
        runner.markers = markers
        result = engine.main(_args(command), runner=runner)
        captured = capsys.readouterr()
        assert result == engine.EXIT_OK, (command, markers, result, captured.err)
        assert marker in captured.out, (command, markers, captured.out)


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param(
            {
                "markers": (engine.RELEASE_SHA_MARKER, CANONICAL_SHA_MARKER),
                "sha_by_marker": {CANONICAL_SHA_MARKER: "d" * 40},
            },
            id="two-sha-markers-disagree",
        ),
        pytest.param(
            {
                "markers": (
                    engine.RELEASE_MANIFEST_MARKER,
                    CANONICAL_MANIFEST_MARKER,
                ),
                "manifest_by_marker": {CANONICAL_MANIFEST_MARKER: "d" * 40},
            },
            id="two-manifests-disagree",
        ),
        pytest.param(
            {
                "markers": (engine.RELEASE_SHA_MARKER, engine.RELEASE_MANIFEST_MARKER),
                "sha_by_marker": {engine.RELEASE_SHA_MARKER: "d" * 40},
            },
            id="sha-marker-disagrees-with-manifest",
        ),
        pytest.param(
            {
                "markers": (CANONICAL_SHA_MARKER, CANONICAL_MANIFEST_MARKER),
                "sha_by_marker": {CANONICAL_SHA_MARKER: "d" * 40},
            },
            id="canonical-sha-marker-disagrees-with-manifest",
        ),
    ],
)
def test_every_reader_fails_closed_on_disagreeing_markers(
    overrides: dict[str, object], capsys: pytest.CaptureFixture
) -> None:
    for command in ("status", "check", "rollback"):
        runner = _disagreeing(**overrides)
        result = engine.main(_args(command), runner=runner)
        captured = capsys.readouterr()
        assert result == engine.EXIT_MARKER_CONFLICT, (command, overrides, result)
        assert "release identity markers disagree" in captured.err
        assert "ln -s" not in _ssh_combined(runner)


def test_capture_activation_resolves_under_both_marker_spellings(
    capsys: pytest.CaptureFixture,
) -> None:
    from tests.contract import test_kronika_capture_services as capture

    for markers in (
(
            engine.RELEASE_MANIFEST_MARKER,
            engine.RELEASE_SHA_MARKER,
        ),
        (CANONICAL_MANIFEST_MARKER, CANONICAL_SHA_MARKER),
        ALL_MARKER_SPELLINGS,
    ):
        runner = capture.CaptureRunner()
        runner.markers = markers
        assert engine.main(capture._activate(), runner=runner) == engine.EXIT_OK


def test_marker_readers_never_open_a_literal_marker_name() -> None:
    """Every reader resolves through the accepted tables, never through a literal.

    Parsed, not searched: the only marker literals the engine may contain are the
    four table entries themselves, each declared once. A reader that reintroduced
    a literal would add a fifth literal and fail here.
    """
    import ast

    tree = ast.parse(ENGINE_PATH.read_text(encoding="utf-8"))
    literals = sorted(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and re.fullmatch(
            r"\.(?:framenest|kronika)-release-(?:sha|manifest\.json)", node.value
        )
    )

    # Exactly the two writer constants and the four accepted-table entries. A
    # reader that reintroduced a literal would add a seventh.
    assert literals == sorted(
        [
            engine.RELEASE_SHA_MARKER,
            engine.RELEASE_MANIFEST_MARKER,
            engine.RELEASE_SHA_MARKER,
            engine.RELEASE_MANIFEST_MARKER,
            CANONICAL_SHA_MARKER,
            CANONICAL_MANIFEST_MARKER,
        ]
    )


# --- the installed-unit executable guard ---


def test_unit_executable_extraction_reads_only_the_path_field() -> None:
    resolved = engine.unit_executables_from_show(DEFAULT_UNIT_SHOW)

    assert resolved["ExecStart"] == (
        "/opt/framenest/current/.venv/bin/framenest-production",
    )
    assert resolved["ExecStartPre"] == (
        "/opt/framenest/current/.venv/bin/framenest-production",
    )


def test_unit_executable_extraction_sees_effective_drop_ins() -> None:
    """A drop-in command is part of the effective property systemd reports."""
    drop_in_show = (
        "ExecStart={ path=/opt/framenest/current/.venv/bin/framenest-production ; "
        "argv[]=/opt/framenest/current/.venv/bin/framenest-production serve }\n"
        "ExecStartPre={ path=/opt/framenest/current/.venv/bin/framenest-production ; "
        "argv[]=/opt/framenest/current/.venv/bin/framenest-production "
        "check-database-ready } { path=/bin/sh ; argv[]=/bin/sh -c 'exec "
        "/opt/framenest/current/.venv/bin/kronika-production serve' }"
    )

    resolved = engine.unit_executables_from_show(drop_in_show)

    assert resolved["ExecStartPre"] == (
        "/opt/framenest/current/.venv/bin/framenest-production",
        "/bin/sh",
    )
    assert engine.release_scoped_console_script("/bin/sh") is None


@pytest.mark.parametrize(
    "executable",
    [
        "/bin/sh",
        "/usr/bin/python3",
        "/opt/framenest/current/pyproject.toml",
        "/opt/framenest/current/.venv/lib/python3.13/site-packages/x.py",
        "/opt/framenest/releases/not-a-sha/.venv/bin/framenest-production",
        "",
    ],
)
def test_unknown_effective_execution_forms_are_not_release_console_scripts(
    executable: str,
) -> None:
    assert engine.release_scoped_console_script(executable) is None


def test_regular_executable_check_rejects_a_symlink() -> None:
    command = engine.cmd_remote_test_regular_executable("/opt/x/.venv/bin/a")

    assert "test -f" in command
    assert "! -L" in command
    assert "-a -x" in command


def test_deploy_refuses_a_target_lacking_the_installed_unit_executable() -> None:
    runner = FakeRunner()
    runner.missing_executables = frozenset({"framenest-production"})

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_UNIT_EXEC_GUARD
    transcript = _ssh_combined(runner)
    assert "ln -s" not in transcript
    assert "restart framenest.service" not in transcript
    assert runner.switched == []


def test_rollback_refuses_a_target_lacking_the_installed_unit_executable() -> None:
    runner = FakeRunner()
    runner.missing_executables = frozenset({"framenest-production"})

    assert engine.main(_args("rollback"), runner=runner) == engine.EXIT_UNIT_EXEC_GUARD
    transcript = _ssh_combined(runner)
    assert "ln -s" not in transcript
    assert "restart framenest.service" not in transcript
    assert runner.switched == []


def test_automatic_rollback_is_guarded_before_it_switches() -> None:
    """The guard runs inside the automatic rollback, before its own switch."""

    class _PostSwitchFail(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self._journal_failures = 0

        def _ssh_respond(self, combined, input_bytes):
            if "journalctl -u framenest.service" in combined:
                self._journal_failures += 1
                if self._journal_failures == 1:
                    raise engine.ReleaseError("terminal", engine.EXIT_SERVICE_TERMINAL)
            return super()._ssh_respond(combined, input_bytes)

    runner = _PostSwitchFail()
    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_SERVICE_TERMINAL
    transcript = _ssh_combined(runner)
    rollback_switch = transcript.rindex("ln -s")
    rollback_guard = transcript.rindex(
        f"test -f {PREV_PATH}/.venv/bin/framenest-production"
    )
    assert rollback_guard < rollback_switch
    assert "restart framenest.service" in transcript[rollback_switch:]


def test_automatic_rollback_refuses_a_previous_release_it_cannot_start() -> None:
    """A previous release lacking the executable is refused, not switched to."""

    class _PostSwitchFail(FakeRunner):
        def __init__(self) -> None:
            super().__init__()
            self._journal_failures = 0

        def _ssh_respond(self, combined, input_bytes):
            if "journalctl -u framenest.service" in combined:
                self._journal_failures += 1
                if self._journal_failures == 1:
                    raise engine.ReleaseError("terminal", engine.EXIT_SERVICE_TERMINAL)
            if "test -f " in combined and PREV_PATH in combined:
                raise engine.ReleaseError("no such file", engine.EXIT_TRANSPORT)
            return super()._ssh_respond(combined, input_bytes)

    runner = _PostSwitchFail()
    result = engine.main(_args("deploy"), runner=runner)

    assert result == engine.EXIT_ROLLBACK
    # The deploy switch happened; the automatic rollback switched nothing.
    assert runner.switched == [TARGET]


def test_deploy_refuses_when_the_unit_declares_no_executable() -> None:
    runner = FakeRunner()
    runner.unit_show = "ExecStartPre={ path=/opt/framenest/current/.venv/bin/framenest-production ; argv[]=x }"

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_UNIT_EXEC_GUARD
    assert "ln -s" not in _ssh_combined(runner)


def test_deploy_refuses_an_unknown_execution_form_before_switching() -> None:
    runner = FakeRunner()
    runner.unit_show = (
        "ExecStart={ path=/usr/bin/python3 ; argv[]=/usr/bin/python3 -m kronika.server }"
    )

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_UNIT_EXEC_GUARD
    assert "ln -s" not in _ssh_combined(runner)
    assert "restart framenest.service" not in _ssh_combined(runner)


def test_deploy_refuses_a_symlink_standing_where_a_console_script_must_be() -> None:
    runner = FakeRunner()
    runner.symlinked_executables = frozenset({".venv/bin/framenest-production"})

    assert engine.main(_args("deploy"), runner=runner) == engine.EXIT_UNIT_EXEC_GUARD
    assert "ln -s" not in _ssh_combined(runner)


def test_routine_paths_never_migrate_install_or_restart_capture() -> None:
    for command in ("deploy", "rollback"):
        runner = FakeRunner()
        assert engine.main(_args(command), runner=runner) == engine.EXIT_OK
        transcript = _ssh_combined(runner)
        assert " migrate" not in transcript
        assert "systemctl daemon-reload" not in transcript
        assert "systemctl enable" not in transcript
        assert "systemctl link" not in transcript
        assert "capture-current.next" not in transcript
        assert "restart kronika-capture-runner.service" not in transcript
