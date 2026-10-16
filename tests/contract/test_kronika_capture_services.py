"""Repository contracts for capture units and capture activation.

These tests read unit sources and drive the release helper with a fake command
runner. They do not contact a host, create a real token, or start a service.
"""

from __future__ import annotations

import difflib
import importlib.util
import inspect
from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import re
import shlex
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest

from kronika_capture.config import CAPTURE_RESTART_BRAKE_MS, PROTO_VERSION

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
ENGINE_PATH = REPOSITORY_ROOT / "deploy" / "ubuntu" / "kronika_release.py"
SYSTEMD = REPOSITORY_ROOT / "deploy" / "systemd"
ENV_EXAMPLE = SYSTEMD / "kronika-capture.env.example"
PROTOCOL_JS = (
    REPOSITORY_ROOT
    / "src"
    / "kronika_capture"
    / "_assets"
    / "extension"
    / "src"
    / "protocol.js"
)
DRIVER_JS = (
    REPOSITORY_ROOT
    / "src"
    / "kronika_capture"
    / "_assets"
    / "extension"
    / "src"
    / "headless"
    / "driver.mjs"
)

_SPEC = importlib.util.spec_from_file_location("framenest_release_capture", ENGINE_PATH)
assert _SPEC is not None and _SPEC.loader is not None
engine = importlib.util.module_from_spec(_SPEC)
sys.modules["framenest_release_capture"] = engine
_SPEC.loader.exec_module(engine)

RELEASE = "a" * 40
PREV = "c" * 40
PREV_PATH = f"/opt/framenest/releases/{PREV}"
TARGET = f"/opt/framenest/releases/{RELEASE}"
OLD_IDENTITY = ("11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222")
NEW_IDENTITY = ("33333333-3333-4333-8333-333333333333", "44444444-4444-4444-8444-444444444444")

UNITS = {
    "xvfb": SYSTEMD / "kronika-capture-xvfb.service",
    "bridge": SYSTEMD / "kronika-capture-bridge.service",
    "runner": SYSTEMD / "kronika-capture-runner.service",
    "vnc": SYSTEMD / "kronika-capture-vnc.service",
    "view": SYSTEMD / "kronika-capture-view.service",
}


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _git_response(combined: str) -> str:
    if "src/kronika_capture" in combined and "rev-parse" in combined:
        return "d" * 40
    if " show " in combined and "kronika-capture" in combined:
        return "capture-contract\n"
    raise AssertionError(f"unexpected git command: {combined}")


class _Git:
    def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
        return _git_response(" ".join(argv))


def _identity() -> dict[str, str]:
    return engine.capture_runtime_identity(_Git(), RELEASE)


def _target_manifest(**overrides: str) -> str:
    payload = engine.make_manifest(
        release_sha=RELEASE,
        ap_pin="b" * 40,
        superproject_sha256="a" * 64,
        ap_archive_sha256="b" * 64,
        **_identity(),
    )
    payload.update(overrides)
    return json.dumps(payload)


OLD_LAYOUT_SHOW = (
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
)
NEW_LAYOUT_SHOW = (
    "LoadState=not-found\n"
    "ActiveState=inactive\n"
    "UnitFileState=not-found\n"
    "User=\nGroup=\nWorkingDirectory=\nExecStart="
)


class CaptureRunner:
    def __init__(
        self,
        *,
        blocked: list[str] | None = None,
        brake: str = "brake=ok",
        readiness: str = "readiness=ready",
        capture_link: str = "absent",
        capture_protocol: str = "1",
        manifest_overrides: dict[str, str] | None = None,
    ) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.blocked = list(blocked or ["blocked=none"])
        self.brake = brake
        self.readiness = readiness
        self.capture_link = capture_link
        self.capture_protocol = capture_protocol
        self.manifest_overrides = manifest_overrides or {}

    def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
        self.calls.append((list(argv), input_bytes))
        combined = " ".join(argv)
        if combined.startswith("git "):
            return _git_response(combined)
        if combined.startswith("ssh "):
            return self._ssh(combined)
        raise AssertionError(combined)

    def _ssh(self, combined: str) -> str:
        if "systemctl show --property=LoadState" in combined:
            if "kronika.service" in combined:
                return NEW_LAYOUT_SHOW
            return OLD_LAYOUT_SHOW
        if "kronika-capture-work-gate" in combined:
            if not self.blocked:
                return "blocked=none"
            return self.blocked.pop(0)
        if "kronika-capture-brake-gate" in combined:
            return self.brake
        if "kronika-capture-identity-snapshot" in combined:
            return json.dumps(OLD_IDENTITY)
        if "kronika-capture-readiness-gate" in combined:
            return self.readiness
        if "test -L /opt/framenest/capture-current" in combined:
            return self.capture_link
        if "test -L /opt/kronika/capture-current" in combined:
            return "absent"
        if "readlink -n /opt/framenest/current" in combined:
            return PREV_PATH
        if "then echo manifest" in combined or "then echo sha" in combined:
            return "\n".join(
                f"{kind} {marker}"
                for _path, marker, kind in re.findall(
                    r"test -e (/[^ ]+)/(\.\S+); then echo (manifest|sha) ", combined
                )
            )
        sha_read = re.search(r"cat (/[^ ]+)/(\.\S+-release-sha)$", combined)
        if sha_read is not None:
            return (PREV if PREV in sha_read.group(1) else RELEASE) + "\n"
        manifest_read = re.search(
            r"cat (/[^ ]+)/(\.\S+-release-manifest\.json)$", combined
        )
        if manifest_read is not None:
            if PREV in manifest_read.group(1):
                return json.dumps(
                    {
                        "framenest_release_sha": PREV,
                        "capture_bridge_protocol": self.capture_protocol,
                    }
                )
            return _target_manifest(**self.manifest_overrides)
        if "test -e " in combined or "test -x " in combined:
            return ""
        if "capture-current.next" in combined:
            return ""
        if "restart kronika-capture-runner.service" in combined:
            return ""
        raise AssertionError(f"unexpected ssh command: {combined}")


def _ssh(runner: CaptureRunner) -> str:
    return "\n".join(" ".join(argv) for argv, _ in runner.calls if argv and argv[0] == "ssh")


def _index(runner: CaptureRunner, needle: str) -> int:
    for index, (argv, _) in enumerate(runner.calls):
        if argv and argv[0] == "ssh" and needle in " ".join(argv):
            return index
    raise AssertionError(needle)


def _activate(extra: list[str] | None = None, command: str = "activate-capture") -> list[str]:
    argv = [command, "--release", RELEASE, "--yes", "--target", "nuc", "--user", "op", "--identity", "identity"]
    if extra:
        argv.extend(extra)
    return argv


def test_unit_sources_parse_and_keep_the_capture_boundary() -> None:
    for name, path in UNITS.items():
        text = _text(path)
        assert "[Unit]" in text
        assert "[Service]" in text
        assert "PartOf=framenest.service" not in text
        assert "Group=framenest" not in text
        assert "SupplementaryGroups=" not in text
        assert "User=kronika-capture" in text
        assert name in path.name

    xvfb = _text(UNITS["xvfb"])
    runner = _text(UNITS["runner"])
    bridge = _text(UNITS["bridge"])
    vnc = _text(UNITS["vnc"])
    view = _text(UNITS["view"])

    assert "Restart=no" in xvfb
    assert "Restart=on-failure" not in xvfb
    assert "Restart=no" in runner
    assert "Restart=on-failure" not in runner
    assert "Restart=on-failure" in bridge
    assert "PrivateTmp=true" not in xvfb
    assert "PrivateTmp=true" not in runner
    assert "/tmp/.X11-unix" in xvfb
    assert "/tmp/.X11-unix" in runner
    assert "/run/kronika-capture/Xauthority" in xvfb
    assert "/run/kronika-capture/Xauthority" in runner
    assert "-auth /run/kronika-capture/Xauthority" in xvfb
    assert "-nolisten tcp" in xvfb
    assert " -ac" not in xvfb
    assert "-ac\n" not in xvfb
    assert "LoadCredential=token:/etc/kronika-capture/credentials/kronika-bridge-token" in bridge
    assert "LoadCredential=token:/etc/kronika-capture/credentials/kronika-bridge-token" in runner
    for unit in (bridge, runner, vnc, view, xvfb):
        assert "0.0.0.0" not in unit
    assert "--profile /var/lib/kronika-capture/profile" in runner
    assert "WorkingDirectory=/opt/kronika/capture-current" in bridge
    assert "WorkingDirectory=/opt/kronika/capture-current" in runner
    assert "[Install]" in xvfb and "[Install]" in bridge and "[Install]" in runner
    assert "[Install]" not in vnc
    assert "[Install]" not in view
    assert "RuntimeMaxSec=1800" in vnc
    assert "RuntimeMaxSec=1800" in view
    assert "-localhost" in vnc
    assert "-rfbport 5900" in vnc
    assert "127.0.0.1:6080" in view
    assert "127.0.0.1:5900" in view
    assert "Restart=no" in vnc
    assert "Restart=no" in view
    assert "StateDirectoryMode=0700" in bridge
    assert "StateDirectoryMode=0700" in runner


def _exec_start_args(unit_text: str) -> list[str]:
    for line in unit_text.splitlines():
        if line.startswith("ExecStart="):
            argv = shlex.split(line.split("=", 1)[1])
            assert len(argv) >= 2
            return argv[1:]
    raise AssertionError("ExecStart missing")


def test_cli_execstart_parses_and_rejects_the_old_option_order() -> None:
    from kronika_capture.cli import build_parser

    parser = build_parser()
    expected = {
        "bridge": [
            "--state-dir",
            "/var/lib/kronika-capture",
            "bridge",
            "run",
            "--port",
            "8765",
        ],
        "runner": [
            "--state-dir",
            "/var/lib/kronika-capture",
            "runner",
            "run",
            "--profile",
            "/var/lib/kronika-capture/profile",
            "--port",
            "8765",
            "--headed",
        ],
    }
    for name, args in expected.items():
        assert _exec_start_args(_text(UNITS[name])) == args
        parser.parse_args(args)

    rejected = [
        ["bridge", "run", "--state-dir", "/var/lib/kronika-capture", "--port", "8765"],
        [
            "runner",
            "run",
            "--state-dir",
            "/var/lib/kronika-capture",
            "--profile",
            "/var/lib/kronika-capture/profile",
            "--port",
            "8765",
            "--headed",
        ],
    ]
    for old in rejected:
        with pytest.raises(SystemExit) as caught:
            parser.parse_args(old)
        assert caught.value.code == 2


def test_xvfb_lock_strategy_keeps_tmp_writable() -> None:
    xvfb = _text(UNITS["xvfb"])
    runner = _text(UNITS["runner"])
    exec_start = next(line for line in xvfb.splitlines() if line.startswith("ExecStart="))
    assert exec_start == (
        "ExecStart=/usr/bin/Xvfb :99 -screen 0 1280x800x24 "
        "-nolisten tcp -auth /run/kronika-capture/Xauthority"
    )
    assert "-nolisten tcp" in exec_start
    assert "-auth /run/kronika-capture/Xauthority" in exec_start
    assert "-nolock" not in exec_start
    assert "-ac" not in exec_start
    assert "ReadWritePaths=/tmp /tmp/.X11-unix /run/kronika-capture" in xvfb
    assert "PrivateTmp=true" not in xvfb
    assert "PrivateTmp=true" not in runner


def test_runner_temporary_directory_stays_inside_the_runtime_boundary() -> None:
    runner = _text(UNITS["runner"])
    xvfb = _text(UNITS["xvfb"])
    env = _text(ENV_EXAMPLE)
    runner_lines = runner.splitlines()
    assert "Environment=TMPDIR=/run/kronika-capture/tmp" in runner_lines
    assert (
        "ExecStartPre=/usr/bin/install -d -m 0700 "
        "/var/lib/kronika-capture/profile "
        "/var/lib/kronika-capture/staging "
        "/run/kronika-capture/tmp"
    ) in runner_lines
    read_write = next(line for line in runner_lines if line.startswith("ReadWritePaths="))
    assert read_write == (
        "ReadWritePaths=/var/lib/kronika-capture /run/kronika-capture /tmp/.X11-unix"
    )
    assert "/tmp" not in read_write.split("=", 1)[1].split()
    assert "PrivateTmp=true" not in runner
    assert "PrivateTmp=true" not in xvfb
    assert "ProtectSystem=strict" in runner
    assert "NoNewPrivileges=true" in runner
    assignments = [
        line.split("=", 1)[0].strip()
        for line in env.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    ]
    assert "TMPDIR" not in assignments


def test_env_template_has_no_secret_and_matches_documented_paths() -> None:
    text = _text(ENV_EXAMPLE)
    assert "KRONIKA_CHROMIUM_PATH=/usr/bin/chromium" in text
    for path in (
        "/var/lib/kronika-capture",
        "/var/lib/kronika-capture/profile",
        "/var/lib/kronika-capture/capture-journal.sqlite3",
        "/var/lib/kronika-capture/staging",
        "/run/kronika-capture",
        "/opt/kronika/capture-current",
        "/etc/kronika-capture/capture.env",
    ):
        assert path in text
    lowered = text.lower()
    for forbidden in ("password=", "api_key=", "begin private", "bearer ", "cookie="):
        assert forbidden not in lowered
    assert "do not put" in lowered


def test_capture_gate_scripts_compile() -> None:
    for builder in (
        engine.cmd_remote_capture_work_gate,
        engine.cmd_remote_capture_brake_gate,
        engine.cmd_remote_capture_identity,
        lambda: engine.cmd_remote_capture_readiness_gate(OLD_IDENTITY),
    ):
        parts = shlex.split(builder())
        assert parts[:4] == ["sudo", "-n", "python3", "-c"]
        compile(parts[4], "<capture-gate>", "exec")
        assert "LoadCredential" not in parts[4]


def test_brake_and_protocol_constants_match_the_capture_sources() -> None:
    assert engine.CAPTURE_BRAKE_MS == CAPTURE_RESTART_BRAKE_MS == 300_000
    assert engine.CAPTURE_BRIDGE_PROTOCOL == str(PROTO_VERSION) == "1"
    assert "export const PROTO_VERSION = 1;" in _text(PROTOCOL_JS)
    assert "300000" in _text(DRIVER_JS)
    assert engine.CAPTURE_BRAKE_DIRECTORY == (
        "/var/lib/kronika-capture/profile.capture-launch"
    )


def test_capture_activation_switches_once_and_reports_both_shas(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = CaptureRunner()
    result = engine.main(_activate(), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_OK
    assert f"web_release: {PREV}" in captured.out
    assert f"capture_release: {RELEASE}" in captured.out
    transcript = _ssh(runner)
    assert transcript.count("restart kronika-capture-runner.service") == 1
    assert "restart framenest.service" not in transcript
    assert "kronika-db" not in transcript
    assert "migrate" not in transcript
    assert _index(runner, "kronika-capture-work-gate") < _index(runner, "kronika-capture-brake-gate")
    assert _index(runner, "kronika-capture-brake-gate") < _index(runner, "kronika-capture-identity-snapshot")
    assert _index(runner, "kronika-capture-identity-snapshot") < _index(runner, "capture-current.next")
    assert _index(runner, "capture-current.next") < _index(
        runner, "restart kronika-capture-runner.service"
    )
    assert _index(runner, "restart kronika-capture-runner.service") < _index(
        runner, "kronika-capture-readiness-gate"
    )


def test_capture_activation_refuses_live_or_paused_work() -> None:
    for blocked in ("blocked=live", "blocked=paused"):
        runner = CaptureRunner(blocked=[blocked])
        result = engine.main(_activate(), runner=runner)
        assert result == engine.EXIT_CAPTURE_BUSY
        transcript = _ssh(runner)
        assert "capture-current.next" not in transcript
        assert "restart kronika-capture-runner.service" not in transcript


def test_capture_activation_drains_queued_work_then_restarts_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(engine.time, "sleep", lambda _seconds: None)
    runner = CaptureRunner(blocked=["blocked=queued", "blocked=none"])
    assert engine.main(_activate(), runner=runner) == engine.EXIT_OK
    assert _ssh(runner).count("kronika-capture-work-gate") == 2
    assert _ssh(runner).count("restart kronika-capture-runner.service") == 1


def test_capture_activation_stops_when_queued_work_does_not_drain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Clock:
        def __init__(self) -> None:
            self.now = 50.0

        def monotonic(self) -> float:
            return self.now

        def sleep(self, seconds: float) -> None:
            self.now += seconds

    clock = _Clock()
    monkeypatch.setattr(engine.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(engine.time, "sleep", clock.sleep)
    monkeypatch.setattr(engine, "CAPTURE_DRAIN_DEADLINE_SECONDS", 0)
    runner = CaptureRunner(blocked=["blocked=queued"])
    assert engine.main(_activate(), runner=runner) == engine.EXIT_CAPTURE_BUSY
    assert "restart kronika-capture-runner.service" not in _ssh(runner)


def test_capture_activation_enforces_the_brake_before_switch() -> None:
    runner = CaptureRunner(brake="brake=refuse")
    assert engine.main(_activate(), runner=runner) == engine.EXIT_CAPTURE_BRAKE
    transcript = _ssh(runner)
    assert "kronika-capture-brake-gate" in transcript
    assert "capture-current.next" not in transcript
    assert "restart kronika-capture-runner.service" not in transcript


def test_failed_browser_readiness_does_not_restart_again(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = CaptureRunner(readiness="readiness=browser_unavailable")
    result = engine.main(_activate(), runner=runner)
    captured = capsys.readouterr()
    assert result == engine.EXIT_SERVICE_TERMINAL
    assert _ssh(runner).count("restart kronika-capture-runner.service") == 1
    assert _ssh(runner).count("kronika-capture-readiness-gate") == 1
    assert f"web_release: {PREV}" in captured.out
    assert f"capture_release: {RELEASE}" in captured.out
    assert "restart framenest.service" not in _ssh(runner)


def test_incompatible_capture_protocol_refuses_before_restart() -> None:
    runner = CaptureRunner(capture_link=PREV_PATH, capture_protocol="9")
    assert engine.main(_activate(), runner=runner) == engine.EXIT_SOURCE_GATE
    assert "restart kronika-capture-runner.service" not in _ssh(runner)


def test_capture_rollback_uses_the_same_single_runner_restart() -> None:
    runner = CaptureRunner()
    assert engine.main(_activate(command="rollback-capture"), runner=runner) == engine.EXIT_OK
    transcript = _ssh(runner)
    assert transcript.count("restart kronika-capture-runner.service") == 1
    assert "restart framenest.service" not in transcript
    assert "/opt/framenest/current.next" not in transcript


def _service_record(state="ready", identity=OLD_IDENTITY):
    return {"state": state, "runner_id": identity[0], "browser_session": identity[1]}


def _write_service(journal, payload):
    with sqlite3.connect(journal) as con:
        con.execute("CREATE TABLE IF NOT EXISTS service (singleton INTEGER PRIMARY KEY, record TEXT)")
        con.execute("INSERT OR REPLACE INTO service VALUES (1, ?)", (json.dumps(payload),))


def _run_metadata_script(command, monkeypatch, *, active="active", returncode=0):
    """Execute the real generated gate against a synthetic journal; no host call."""
    parts = shlex.split(command)
    assert parts[:4] == ["sudo", "-n", "python3", "-c"]

    def show(argv, **kwargs):
        assert argv == ["systemctl", "show", "-p", "ActiveState", "--value", engine.CAPTURE_RUNNER_SERVICE]
        return SimpleNamespace(returncode=returncode, stdout=active + "\n")

    output = io.StringIO()
    with monkeypatch.context() as patch, redirect_stdout(output):
        patch.setattr(subprocess, "run", show)
        try:
            exec(compile(parts[4], "<synthetic-capture-gate>", "exec"), {})
        except SystemExit as exc:
            assert exc.code is None
    return output.getvalue().strip()


class JournalRunner(CaptureRunner):
    def __init__(self, journal, monkeypatch, readings, *, active="active"):
        super().__init__()
        self.journal, self.monkeypatch = journal, monkeypatch
        self.readings, self.active = list(readings), active
        self.observed = []

    def _ssh(self, combined):
        if "kronika-capture-identity-snapshot" in combined:
            command = engine.cmd_remote_capture_identity()
        elif "kronika-capture-readiness-gate" in combined:
            if len(self.readings) > 1:
                payload = self.readings.pop(0)
            else:
                payload = self.readings[0]
            _write_service(self.journal, payload)
            # Use the command emitted by the transition, including its snapshot.
            command = self.calls[-1][0][-1]
        else:
            return super()._ssh(combined)
        value = _run_metadata_script(command, self.monkeypatch, active=self.active)
        self.observed.append(value)
        return value


class CaptureClock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _journal_transition(tmp_path, monkeypatch, readings, *, active="active"):
    journal = tmp_path / "synthetic-capture.sqlite3"
    _write_service(journal, _service_record())
    monkeypatch.setattr(engine, "CAPTURE_JOURNAL", str(journal))
    clock = CaptureClock()
    monkeypatch.setattr(engine.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(engine.time, "sleep", clock.sleep)
    return JournalRunner(journal, monkeypatch, readings, active=active), clock


@pytest.mark.parametrize("stale_state", ["ready", "browser_unavailable", "needs_admin"])
def test_activation_waits_for_both_new_identities_in_the_real_journal_gate(tmp_path, monkeypatch, stale_state):
    runner, clock = _journal_transition(tmp_path, monkeypatch, [
        _service_record(stale_state), _service_record(identity=NEW_IDENTITY),
    ])
    assert engine.main(_activate(), runner=runner) == engine.EXIT_OK
    assert runner.observed == [json.dumps(OLD_IDENTITY), "readiness=starting", "readiness=ready"]
    assert clock.now == 1
    assert _ssh(runner).count("restart kronika-capture-runner.service") == 1


@pytest.mark.parametrize("state", ["needs_admin", "browser_unavailable"])
def test_fresh_blocked_identity_is_terminal_without_a_second_restart(tmp_path, monkeypatch, capsys, state):
    runner, clock = _journal_transition(tmp_path, monkeypatch, [_service_record(state, NEW_IDENTITY)])
    assert engine.main(_activate(), runner=runner) == engine.EXIT_SERVICE_TERMINAL
    assert runner.observed[-1] == f"readiness={state}"
    assert clock.now == 0
    transcript = _ssh(runner)
    assert transcript.count("restart kronika-capture-runner.service") == 1
    assert transcript.count("mv -T /opt/framenest/capture-current.next") == 1
    assert f"capture_release: {RELEASE}" in capsys.readouterr().out


def test_unchanged_ready_identity_times_out_after_180_seconds_without_retry(tmp_path, monkeypatch, capsys):
    runner, clock = _journal_transition(tmp_path, monkeypatch, [_service_record()])
    assert engine.CAPTURE_READINESS_DEADLINE_SECONDS == 180
    assert engine.main(_activate(), runner=runner) == engine.EXIT_READINESS_TIMEOUT
    assert clock.now == 180
    assert set(runner.observed[1:]) == {"readiness=starting"}
    assert _ssh(runner).count("restart kronika-capture-runner.service") == 1
    assert _ssh(runner).count("mv -T /opt/framenest/capture-current.next") == 1
    assert f"capture_release: {RELEASE}" in capsys.readouterr().out


def test_failed_unit_is_terminal_even_with_the_old_identity(tmp_path, monkeypatch):
    runner, clock = _journal_transition(tmp_path, monkeypatch, [_service_record()], active="failed")
    assert engine.main(_activate(), runner=runner) == engine.EXIT_SERVICE_TERMINAL
    assert runner.observed[-1] == "readiness=failed"
    assert clock.now == 0
    assert _ssh(runner).count("restart kronika-capture-runner.service") == 1


@pytest.mark.parametrize("identity,active,expected", [
    ((NEW_IDENTITY[0], OLD_IDENTITY[1]), "active", "starting"),
    ((OLD_IDENTITY[0], NEW_IDENTITY[1]), "active", "starting"),
    ((None, None), "active", "starting"),
    (("invalid", NEW_IDENTITY[1]), "active", "unverifiable"),
    (NEW_IDENTITY, "inactive", "starting"),
    (NEW_IDENTITY, "active", "ready"),
])
def test_real_readiness_gate_requires_two_valid_changed_ids_and_active_unit(tmp_path, monkeypatch, identity, active, expected):
    journal = tmp_path / "synthetic.sqlite3"
    _write_service(journal, _service_record(identity=identity))
    monkeypatch.setattr(engine, "CAPTURE_JOURNAL", str(journal))
    assert _run_metadata_script(engine.cmd_remote_capture_readiness_gate(OLD_IDENTITY), monkeypatch, active=active) == f"readiness={expected}"


def test_identity_snapshot_is_metadata_only_and_absence_allows_first_launch(tmp_path, monkeypatch):
    journal = tmp_path / "synthetic.sqlite3"
    monkeypatch.setattr(engine, "CAPTURE_JOURNAL", str(journal))
    assert _run_metadata_script(engine.cmd_remote_capture_identity(), monkeypatch) == "[null, null]"
    assert not journal.exists()
    _write_service(journal, {**_service_record(identity=NEW_IDENTITY), "private": "SECRET-MARKER"})
    snapshot = _run_metadata_script(engine.cmd_remote_capture_identity(), monkeypatch)
    assert snapshot == json.dumps(NEW_IDENTITY)
    assert "SECRET" not in snapshot
    assert _run_metadata_script(engine.cmd_remote_capture_readiness_gate((None, None)), monkeypatch) == "readiness=ready"


@pytest.mark.parametrize("raw", ["identity=unverifiable", "{}", '["invalid", null]', "x" * 257])
def test_unverifiable_snapshot_refuses_before_pointer_switch(tmp_path, monkeypatch, raw):
    class InvalidSnapshot(CaptureRunner):
        def _ssh(self, combined):
            return raw if "kronika-capture-identity-snapshot" in combined else super()._ssh(combined)
    runner = InvalidSnapshot()
    assert engine.main(_activate(), runner=runner) == engine.EXIT_READINESS
    assert "capture-current.next" not in _ssh(runner)
    assert "restart kronika-capture-runner.service" not in _ssh(runner)


def test_web_rollback_leaves_capture_untouched(capsys: pytest.CaptureFixture[str]) -> None:
    from tests.contract import test_nuc_release_remote_contract as remote

    runner = remote.FakeRunner()
    result = remote.engine.main(remote._args("rollback"), runner=runner)
    output = capsys.readouterr().out
    assert result == remote.engine.EXIT_OK
    transcript = remote._ssh_combined(runner)
    assert "capture-current.next" not in transcript
    assert "kronika-capture-runner" not in transcript
    assert transcript.count("restart framenest.service") == 1
    assert "capture_release: absent" in output
    assert f"web_release: {RELEASE}" in output


def test_referenced_release_paths_keep_both_pointers() -> None:
    assert engine.retained_release_paths(TARGET, PREV_PATH) == (TARGET, PREV_PATH)
    assert engine.retained_release_paths(TARGET, TARGET) == (TARGET,)
    assert engine.retained_release_paths(TARGET, "absent") == (TARGET,)


# ---------------------------------------------------------------------------
# C8-A/P capture path migration
# ---------------------------------------------------------------------------

CODE_TREE = "e" * 40
HISTORICAL_UNIT_HASH = "c" * 64
SOURCE_TREE = (
    "./.framenest-release-sha|file|abc|root:root:444\n"
    "./.venv/bin/kronika-capture|file|def|root:root:755\n"
)
TRANSPORT = ["--target", "nuc", "--user", "op", "--identity", "id"]


def _path_source() -> str:
    return f"{engine.OLD_WEB_LAYOUT.release_root}/{RELEASE}"


def _path_target() -> str:
    return engine.NEW_WEB_LAYOUT.release_dir(RELEASE)


def _historical_manifest(protocol: str = "1") -> str:
    return json.dumps(
        {
            "framenest_release_sha": RELEASE,
            "capture_code_tree": CODE_TREE,
            "capture_bridge_protocol": protocol,
            "capture_unit_contract_sha256": HISTORICAL_UNIT_HASH,
            "capture_runtime_contract_sha256": "d" * 64,
            "ap_gitlink": "b" * 40,
        }
    )


def _installed_unit_text(name: str) -> str:
    text = (SYSTEMD / name).read_text(encoding="utf-8")
    return text.replace(engine.NEW_CAPTURE_CURRENT, engine.OLD_CAPTURE_CURRENT)


class PathMigrationRunner:
    """Fake host for migrate-capture-paths. Unknown commands fail closed."""

    def __init__(
        self,
        *,
        pointers: str = "old",
        blocked: str = "blocked=none",
        brake: str = "brake=ok",
        readiness: str = "readiness=ready",
        protocol: str = "1",
        tree_mismatch: bool = False,
        lock_changes: bool = False,
        existing_target: bool = False,
    ) -> None:
        self.calls: list[tuple[list[str], bytes | None]] = []
        self.pointers = pointers
        self.blocked = blocked
        self.brake = brake
        self.readiness = readiness
        self.protocol = protocol
        self.tree_mismatch = tree_mismatch
        self.lock_changes = lock_changes
        self.existing_target = existing_target
        self.files: dict[str, str] = {}
        self.rewritten: set[str] = set()
        self.lock_reads = 0

    def __call__(self, argv: list[str], input_bytes: bytes | None) -> str:
        self.calls.append((list(argv), input_bytes))
        if not argv or argv[0] != "ssh":
            raise AssertionError(argv)
        return self._remote(argv[-1], input_bytes)

    def _link(self, key: str) -> str:
        if self.pointers == "unverifiable":
            return "query-failed"
        if key == "old" and self.pointers in ("old", "both"):
            return _path_source()
        if key == "new" and self.pointers in ("new", "both"):
            return _path_target()
        return "absent"

    def _remote(self, command: str, payload: bytes | None) -> str:
        if "kronika-capture-work-gate" in command:
            return self.blocked
        if "kronika-capture-brake-gate" in command:
            return self.brake
        if "kronika-capture-identity-snapshot" in command:
            return json.dumps(list(OLD_IDENTITY))
        if "kronika-capture-readiness-gate" in command:
            return self.readiness
        if "systemctl show -p FragmentPath --value " in command:
            return f"/etc/systemd/system/{command.rsplit(' ', 1)[-1]}\n"
        if "systemctl show -p WorkingDirectory --value " in command:
            name = command.rsplit(" ", 1)[-1]
            pointer = (
                engine.NEW_CAPTURE_CURRENT
                if name in self.rewritten
                else engine.OLD_CAPTURE_CURRENT
            )
            return pointer + "\n"
        if "test -L " in command and engine.OLD_CAPTURE_CURRENT in command:
            return self._link("old")
        if "test -L " in command and engine.NEW_CAPTURE_CURRENT in command:
            return self._link("new")
        if "then echo manifest" in command or "then echo sha" in command:
            return (
                "manifest .framenest-release-manifest.json\n"
                "sha .framenest-release-sha\n"
            )
        if (
            "release-sha" in command
            and "cat " in command
            and "cat >" not in command
        ):
            return RELEASE + "\n"
        if (
            "release-manifest.json" in command
            and "cat " in command
            and "cat >" not in command
        ):
            return self._historical_manifest()
        if "find . -mindepth 1" in command:
            if self.tree_mismatch and ".staging" in command:
                return SOURCE_TREE + "mismatch\n"
            return SOURCE_TREE
        if "/etc/systemd/system/" in command and "cat >" in command:
            match = re.search(
                r"cat > (/etc/systemd/system/[^'\s]+)\.next", command
            )
            assert match is not None and payload is not None
            path = match.group(1)
            self.files[path] = payload.decode("utf-8")
            self.rewritten.add(Path(path).name)
            return ""
        if "/etc/systemd/system/" in command and "sudo -n cat " in command:
            match = re.search(
                r"sudo -n cat (/etc/systemd/system/[^;\s]+)", command
            )
            assert match is not None
            path = match.group(1)
            if path in self.files:
                return self.files[path]
            return self.unit_text(Path(path).name)
        if "poetry --version" in command:
            return "Poetry (version 2.4.1)\n"
        if "python3.13 --version" in command:
            return "Python 3.13.14\n"
        if "test ! -e " in command:
            if (
                self.existing_target
                and ".staging" not in command
                and "/opt/kronika/releases/" in command
            ):
                raise engine.ReleaseError("command failed", engine.EXIT_TRANSPORT)
            return ""
        if "sha256sum" in command and "poetry.lock" in command:
            self.lock_reads += 1
            digest = "ff" * 32 if self.lock_changes and self.lock_reads > 1 else "ab" * 32
            return f"{digest}  poetry.lock\n"
        if (
            "test -e " in command
            or "test -x " in command
            or "test -f " in command
        ):
            return ""
        if "cp -a " in command:
            return ""
        if (
            "poetry check --lock" in command
            or "poetry env use" in command
            or "poetry install " in command
        ):
            return ""
        if (
            "install -d " in command
            or "chown -R " in command
            or "chmod -R " in command
        ):
            return ""
        if "_remote-relocate-venv-shebangs" in command:
            return ""
        if (
            "systemctl stop " in command
            or "systemctl start " in command
            or "systemctl daemon-reload" in command
        ):
            return ""
        if "ln -s " in command or "mv " in command:
            return ""
        if "rm -rf " in command or "rm -f " in command:
            return ""
        if "cat >" in command:
            return ""
        raise AssertionError(command)

    def unit_text(self, name: str) -> str:
        return _installed_unit_text(name)

    def _historical_manifest(self) -> str:
        return _historical_manifest(self.protocol)


def _ssh_commands(runner: PathMigrationRunner) -> list[str]:
    return [argv[-1] for argv, _ in runner.calls if argv and argv[0] == "ssh"]


def _transcript(runner: PathMigrationRunner) -> str:
    return "\n".join(_ssh_commands(runner))


def _indexes(runner: PathMigrationRunner, needle: str) -> list[int]:
    found = [
        index
        for index, command in enumerate(_ssh_commands(runner))
        if needle in command
    ]
    assert found, needle
    return found


def _preflight_argv() -> list[str]:
    return ["migrate-capture-paths", "preflight", *TRANSPORT]


def _apply_argv(digest: str, *, yes: bool = True) -> list[str]:
    argv = ["migrate-capture-paths", "apply", "--preflight-digest", digest]
    if yes:
        argv.append("--yes")
    argv.extend(TRANSPORT)
    return argv


def _run_preflight(
    runner: PathMigrationRunner, capsys: pytest.CaptureFixture[str]
) -> tuple[int, str]:
    result = engine.main(_preflight_argv(), runner=runner)
    return result, capsys.readouterr().out


def _digest(output: str) -> str:
    for line in output.splitlines():
        if line.startswith("plan_digest: "):
            return line.split(": ", 1)[1]
    raise AssertionError(output)


def test_capture_artifact_diff_is_only_the_pointer_path_lines() -> None:
    former = engine.OLD_CAPTURE_CURRENT
    canonical = engine.NEW_CAPTURE_CURRENT
    changed = 0
    for relative in engine.CAPTURE_CONTRACT_PATHS:
        current = (REPOSITORY_ROOT / relative).read_text(encoding="utf-8")
        assert former not in current
        previous = current.replace(canonical, former)
        assert engine.canonicalize_capture_path_text(previous) == current
        diff = list(
            difflib.unified_diff(
                previous.splitlines(),
                current.splitlines(),
                lineterm="",
            )
        )
        for line in diff:
            if line.startswith(("+++", "---", "@@")) or not line.startswith(("+", "-")):
                continue
            body = line[1:]
            assert body.startswith(engine.CAPTURE_PATH_LINE_PREFIXES), body
            assert former in line or canonical in line
            changed += 1
    assert changed == 10


def test_routine_paths_cannot_reach_capture_path_migration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for name in ("_cmd_status", "_cmd_check", "_cmd_deploy", "_cmd_rollback"):
        source = inspect.getsource(getattr(engine, name))
        assert "migrate-capture-paths" not in source
        assert "observe_capture_path_plan" not in source
    assert "migrate-capture-paths" in inspect.getsource(engine.main)
    assert "migrate-capture-paths" in inspect.getsource(engine._build_parser)

    from tests.contract import test_nuc_release_remote_contract as remote

    def forbidden(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("routine path reached capture path migration")

    monkeypatch.setattr(remote.engine, "_cmd_migrate_capture_paths", forbidden)
    monkeypatch.setattr(remote.engine, "observe_capture_path_plan", forbidden)
    for command in ("status", "check", "deploy", "rollback"):
        runner = remote.FakeRunner()
        assert remote.engine.main(remote._args(command), runner=runner) == remote.engine.EXIT_OK
        transcript = remote._ssh_combined(runner)
        assert "migrate-capture-paths" not in transcript
        assert "kronika-capture-path-migration" not in transcript
        assert "systemctl stop kronika-capture-bridge" not in transcript
        assert "systemctl start kronika-capture-bridge" not in transcript
        assert "systemctl stop kronika-capture-runner" not in transcript
        assert "systemctl start kronika-capture-runner" not in transcript


def test_preflight_is_read_only_and_preserves_source_identity(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = PathMigrationRunner()
    result, output = _run_preflight(runner, capsys)
    transcript = _transcript(runner)
    assert result == engine.EXIT_OK
    assert output.startswith("kronika-release migrate-capture-paths preflight\n")
    assert f"source_path: {_path_source()}" in output
    assert f"release_sha: {RELEASE}" in output
    assert ".framenest-release-sha" in output
    assert ".framenest-release-manifest.json" in output
    assert f"capture_code_tree: {CODE_TREE}" in output
    assert "capture_bridge_protocol: 1" in output
    assert f"target_pointer: {engine.NEW_CAPTURE_CURRENT}" in output
    assert f"executable: {_path_target()}/{engine.CAPTURE_RELEASE_EXECUTABLE}" in output
    assert engine.HEX64.fullmatch(_digest(output))
    assert all(argv[0] != "git" for argv, _ in runner.calls)
    for needle in (
        "cp -a ",
        "poetry",
        "systemctl stop",
        "systemctl start",
        "systemctl restart",
        "ln -s ",
        "mv -T ",
        " uv ",
        "/uv",
    ):
        assert needle not in transcript


@pytest.mark.parametrize(
    ("pointers", "message"),
    [
        ("new", "canonical capture pointer is already present"),
        ("both", "both capture release pointers are present"),
        ("none", "no capture release pointer is present"),
        ("unverifiable", "capture release pointer state is unverifiable"),
    ],
)
def test_conflicting_capture_pointers_are_refused(
    capsys: pytest.CaptureFixture[str], pointers: str, message: str
) -> None:
    runner = PathMigrationRunner(pointers=pointers)
    result = engine.main(_preflight_argv(), runner=runner)
    assert result == engine.EXIT_LAYOUT
    assert message in capsys.readouterr().err
    assert "cp -a " not in _transcript(runner)


def test_wrong_unit_contract_is_refused(capsys: pytest.CaptureFixture[str]) -> None:
    class WrongUnit(PathMigrationRunner):
        def unit_text(self, name: str) -> str:
            text = super().unit_text(name)
            if name == engine.CAPTURE_BRIDGE_UNIT:
                return text.replace("User=kronika-capture", "User=not-capture", 1)
            return text

    runner = WrongUnit()
    result = engine.main(_preflight_argv(), runner=runner)
    assert result == engine.EXIT_SOURCE_GATE
    assert "differs beyond the pointer path lines" in capsys.readouterr().err
    assert "cp -a " not in _transcript(runner)


def test_incompatible_bridge_protocol_is_refused(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = PathMigrationRunner(protocol="9")
    result = engine.main(_preflight_argv(), runner=runner)
    assert result == engine.EXIT_SOURCE_GATE
    assert "capture bridge protocol is not compatible" in capsys.readouterr().err


def test_apply_refuses_without_confirmation_or_digest(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = PathMigrationRunner()
    result = engine.main(_apply_argv("a" * 64, yes=False), runner=runner)
    assert result == engine.EXIT_USAGE
    assert runner.calls == []
    assert "requires --yes" in capsys.readouterr().err

    with pytest.raises(SystemExit) as caught:
        engine.main(
            ["migrate-capture-paths", "apply", "--yes", *TRANSPORT],
            runner=runner,
        )
    assert caught.value.code == 2
    assert runner.calls == []


def test_apply_refuses_a_digest_that_does_not_match(
    capsys: pytest.CaptureFixture[str],
) -> None:
    runner = PathMigrationRunner()
    result, output = _run_preflight(runner, capsys)
    assert result == engine.EXIT_OK
    real = _digest(output)
    runner.calls.clear()
    refused = engine.main(_apply_argv("0" * 64), runner=runner)
    assert refused == engine.EXIT_MIGRATION
    assert "does not match" in capsys.readouterr().err
    transcript = _transcript(runner)
    assert "cp -a " not in transcript
    assert "systemctl stop" not in transcript
    assert "systemctl start" not in transcript
    assert real != "0" * 64


def test_busy_capture_is_refused_before_mutation(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(blocked="blocked=live")
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    assert refused == engine.EXIT_CAPTURE_BUSY
    transcript = _transcript(runner)
    assert "kronika-capture-work-gate" in transcript
    assert "cp -a " not in transcript
    assert "systemctl stop" not in transcript
    assert "systemctl start" not in transcript


def test_brake_refuses_before_mutation(capsys: pytest.CaptureFixture[str]) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(brake="brake=refuse")
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    assert refused == engine.EXIT_CAPTURE_BRAKE
    transcript = _transcript(runner)
    assert "kronika-capture-brake-gate" in transcript
    assert "systemctl stop" not in transcript
    assert "systemctl start" not in transcript


def test_copied_tree_mismatch_refuses_before_the_service_cycle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(tree_mismatch=True)
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    transcript = _transcript(runner)
    assert refused == engine.EXIT_SOURCE_GATE
    assert "does not match the source" in capsys.readouterr().err
    assert "cp -a " in transcript
    assert "systemctl stop" not in transcript
    assert f"sudo -n rm -rf {_path_source()}" not in set(transcript.splitlines())


def test_changed_lock_refuses_before_the_service_cycle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(lock_changes=True)
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    assert refused == engine.EXIT_POETRY
    assert "poetry.lock changed" in capsys.readouterr().err
    assert "systemctl stop" not in _transcript(runner)


def test_existing_target_is_not_deleted(capsys: pytest.CaptureFixture[str]) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(existing_target=True)
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    assert refused == engine.EXIT_TRANSPORT
    transcript = _transcript(runner)
    assert f"rm -rf {_path_target()}" not in transcript
    assert f"rm -rf {_path_source()}" not in transcript


def test_apply_preserves_the_release_and_bounds_the_service_cycle(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    digest = _digest(output)
    runner = PathMigrationRunner()
    applied = engine.main(_apply_argv(digest), runner=runner)
    applied_output = capsys.readouterr().out
    transcript = _transcript(runner)
    commands = _ssh_commands(runner)
    target = _path_target()
    source = _path_source()
    staging = f"{target}.staging"
    assert applied == engine.EXIT_OK
    assert f"migrate-capture-paths apply complete: {RELEASE}" in applied_output
    assert f"capture_code_tree: {CODE_TREE}" in applied_output
    assert f"executable: {target}/{engine.CAPTURE_RELEASE_EXECUTABLE}" in applied_output
    assert all(argv[0] != "git" for argv, _ in runner.calls)
    assert not any("cat >" in command and "/opt/" in command for command in commands)
    assert transcript.count("cp -a ") == 1
    assert f"cp -a {source}/. {staging}/" in transcript
    assert engine.POETRY_BIN in transcript
    assert engine.CPYTHON_BIN in transcript
    assert transcript.count("poetry install ") == 1
    assert " uv " not in transcript and "/uv" not in transcript
    assert transcript.count(f"ln -s {target} {engine.NEW_CAPTURE_CURRENT}.next") == 1
    assert transcript.count(
        f"mv -T {engine.NEW_CAPTURE_CURRENT}.next {engine.NEW_CAPTURE_CURRENT}"
    ) == 1
    assert transcript.count(f"rm -f {engine.OLD_CAPTURE_CURRENT}") == 1
    lines = set(transcript.splitlines())
    assert f"sudo -n rm -rf {source}" not in lines
    assert f"sudo -n rm -f {source}" not in lines
    assert f"sudo -n rm -rf {target}" not in lines
    assert f"sudo -n rm -rf {staging}" not in lines
    assert transcript.count("systemctl stop kronika-capture-runner.service") == 1
    assert transcript.count("systemctl stop kronika-capture-bridge.service") == 1
    assert transcript.count("systemctl start kronika-capture-bridge.service") == 1
    assert transcript.count("systemctl start kronika-capture-runner.service") == 1
    assert "systemctl restart" not in transcript
    assert transcript.count("systemctl daemon-reload") == 1
    for unit in (
        "kronika-capture-xvfb.service",
        "kronika-capture-vnc.service",
        "kronika-capture-view.service",
        "kronika.service",
        "framenest.service",
    ):
        for verb in ("stop", "start", "restart"):
            assert f"systemctl {verb} {unit}" not in transcript
    work = _indexes(runner, "kronika-capture-work-gate")
    brake = _indexes(runner, "kronika-capture-brake-gate")
    assert len(work) == 2 and len(brake) == 2
    copy_at = _indexes(runner, "cp -a ")[0]
    stop_at = _indexes(runner, "systemctl stop kronika-capture-runner.service")[0]
    switch_at = _indexes(runner, f"mv -T {engine.NEW_CAPTURE_CURRENT}.next")[0]
    start_bridge = _indexes(runner, "systemctl start kronika-capture-bridge.service")[0]
    start_runner = _indexes(runner, "systemctl start kronika-capture-runner.service")[0]
    ready_at = _indexes(runner, "kronika-capture-readiness-gate")[0]
    assert work[0] < copy_at < work[1] < stop_at < switch_at < start_bridge < start_runner < ready_at
    assert brake[0] < copy_at < brake[1] < stop_at
    payloads: dict[str, str] = {}
    for argv, data in runner.calls:
        command = argv[-1]
        if data and "/etc/systemd/system/" in command:
            match = re.search(
                r"/etc/systemd/system/(kronika-capture-[a-z]+\.service)", command
            )
            assert match is not None
            payloads[match.group(1)] = data.decode("utf-8")
    assert set(payloads) == set(engine.capture_unit_names())
    for name, text in payloads.items():
        assert text == (SYSTEMD / name).read_text(encoding="utf-8")
        assert engine.OLD_CAPTURE_CURRENT not in text
    bridge = payloads[engine.CAPTURE_BRIDGE_UNIT]
    runner_unit = payloads[engine.CAPTURE_RUNNER_SERVICE]
    executable = f"{engine.NEW_CAPTURE_CURRENT}/{engine.CAPTURE_RELEASE_EXECUTABLE}"
    assert f"WorkingDirectory={engine.NEW_CAPTURE_CURRENT}" in bridge
    assert f"ExecStart={executable} " in bridge
    assert f"WorkingDirectory={engine.NEW_CAPTURE_CURRENT}" in runner_unit
    assert f"ExecStart={executable} " in runner_unit
    assert "User=kronika-capture" in bridge
    assert "/var/lib/kronika-capture" in runner_unit
    assert "8765" in bridge


def test_readiness_failure_does_not_start_the_browser_again(
    capsys: pytest.CaptureFixture[str],
) -> None:
    prepared = PathMigrationRunner()
    result, output = _run_preflight(prepared, capsys)
    assert result == engine.EXIT_OK
    runner = PathMigrationRunner(readiness="readiness=failed")
    refused = engine.main(_apply_argv(_digest(output)), runner=runner)
    transcript = _transcript(runner)
    assert refused == engine.EXIT_SERVICE_TERMINAL
    assert transcript.count("systemctl start kronika-capture-bridge.service") == 1
    assert transcript.count("systemctl start kronika-capture-runner.service") == 1
    assert transcript.count("kronika-capture-readiness-gate") == 1
    lines = set(transcript.splitlines())
    assert f"sudo -n rm -rf {_path_target()}" not in lines
    assert f"sudo -n rm -rf {_path_source()}" not in lines
    assert "capture-path restoration requires the prior pointer" in capsys.readouterr().out
