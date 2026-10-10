"""Behavioral contract tests for operator Mullvad egress scripts.

Synthetic fake tools only. These tests must not contact a real network or host.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_DIR = REPOSITORY_ROOT / "scripts" / "operator" / "network"
BASH_SCRIPT = SCRIPT_DIR / "framenest_mullvad_egress.sh"
FISH_SCRIPT = SCRIPT_DIR / "framenest_mullvad_egress.fish"
CANONICAL_BASH_SCRIPT = SCRIPT_DIR / "kronika_mullvad_egress.sh"
CANONICAL_FISH_SCRIPT = SCRIPT_DIR / "kronika_mullvad_egress.fish"
GATE_SCRIPT = SCRIPT_DIR / "framenest_nuc_worker_gate.fish"
CANONICAL_GATE_SCRIPT = SCRIPT_DIR / "kronika_nuc_worker_gate.fish"
SCRIPT_README = SCRIPT_DIR / "README.md"
ADR_PATH = (
    REPOSITORY_ROOT
    / "docs"
    / "adr"
    / "0058-independent-mullvad-egress-and-operator-network-recovery.md"
)
ADR_INDEX_PATH = REPOSITORY_ROOT / "docs" / "adr" / "README.md"
OPERATOR_DOC = REPOSITORY_ROOT / "docs" / "OPERATOR_NETWORK.md"
OPERATOR_DOC_PATHS = (
    OPERATOR_DOC,
    ADR_PATH,
    SCRIPT_README,
)

MULLVAD_NODE = "se-got-wg-001.mullvad.ts.net"
FIXTURE_IP = "203.0.113.10"
OTHER_FIXTURE_IP = "198.51.100.20"
PUBLIC_KEY_LEAK = "leak-status-json-now"

FORBIDDEN_SCRIPT_TOKENS = (
    "xdg-open",
    "firefox",
    "google-chrome",
    "tailscale up",
    "tailscale down",
    "tailscale login",
    "tailscale logout",
    "accept-new",
    "ForwardAgent=yes",
    "systemd-run",
    "systemctl",
    "nmcli",
    "iptables",
    "nft ",
    "sysctl",
    "tailscale funnel",
    "--advertise-exit-node",
    "eval ",
    "sudo",
    "--operator",
)


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _healthy_status_json(
    *,
    backend: str = "Running",
    advertises: bool = False,
    mullvad_available: bool = True,
    selected_mullvad: bool = False,
    selected_other: bool = False,
) -> str:
    peers: dict[str, object] = {}
    if mullvad_available:
        peers["nodekey:mullvad"] = {
            "DNSName": f"{MULLVAD_NODE}.",
            "ExitNodeOption": True,
            "ExitNode": selected_mullvad,
            "Online": True,
        }
    if selected_other:
        peers["nodekey:other"] = {
            "DNSName": "relay.example.ts.net.",
            "ExitNodeOption": True,
            "ExitNode": True,
            "Online": True,
        }
    payload = {
        "BackendState": backend,
        "TailscaleIPs": [FIXTURE_IP],
        "PublicKey": PUBLIC_KEY_LEAK,
        "Self": {
            "ExitNodeOption": advertises,
            "DNSName": "workstation.example.ts.net.",
            "TailscaleIPs": [OTHER_FIXTURE_IP],
        },
        "Peer": peers,
        "CurrentTailnet": {"Name": "must-not-print.example"},
    }
    return json.dumps(payload)


def _install_fakes(
    tmp_path: Path,
    *,
    get_supported: bool = True,
    get_pref_readable: bool = True,
    get_exit_node: str = "",
    get_lan: str = "false",
    status_json: str | None = None,
    set_fail: bool = False,
    status_fail_after: int | None = None,
    mullvad_mode: str = "disconnected",
    curl_mode: str = "mullvad",
    ssh_exit: int = 0,
    gpgconf_socket: Path | None = None,
) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    argv_log = log_dir / "tailscale.argv"
    env_log = log_dir / "child.env"
    set_log = log_dir / "tailscale.set"
    curl_log = log_dir / "curl.argv"
    ssh_log = log_dir / "ssh.argv"
    status_file = log_dir / "status.json"
    status_file.write_text(status_json or _healthy_status_json(), encoding="utf-8")
    cwd_trap_log = log_dir / "cwd-trap.argv"
    state_dir = log_dir / "state"
    state_dir.mkdir(parents=True, exist_ok=True)

    tailscale = bin_dir / "tailscale"
    _write_executable(
        tailscale,
        f"""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> {argv_log}
{{
  printf 'APPIMAGE=%s\\n' "${{APPIMAGE-}}"
  printf 'APPDIR=%s\\n' "${{APPDIR-}}"
  printf 'ARGV0=%s\\n' "${{ARGV0-}}"
  printf 'LD_LIBRARY_PATH=%s\\n' "${{LD_LIBRARY_PATH-}}"
  printf 'LD_PRELOAD=%s\\n' "${{LD_PRELOAD-}}"
}} >> {env_log}
if [[ "${{1:-}}" == up || "${{1:-}}" == down || "${{1:-}}" == login || "${{1:-}}" == logout ]]; then
  echo "forbidden subcommand" >&2
  exit 99
fi
if [[ "${{1:-}}" == get ]]; then
  if [[ {int(not get_supported)} -eq 1 ]]; then
    echo "unknown command: get" >&2
    exit 1
  fi
  if [[ {int(not get_pref_readable)} -eq 1 ]]; then
    echo "fn-unreadable-pref-token" >&2
    exit 1
  fi
  if [[ "${{2:-}}" == exit-node ]]; then
    printf '%s\\n' {get_exit_node!r}
    exit 0
  fi
  if [[ "${{2:-}}" == exit-node-allow-lan-access ]]; then
    printf '%s\\n' {get_lan!r}
    exit 0
  fi
  echo "unsupported get pref" >&2
  exit 1
fi
if [[ "${{1:-}}" == status && "${{2:-}}" == --json ]]; then
  count_file={state_dir / "status.count"}
  count=0
  if [[ -f "$count_file" ]]; then
    count=$(cat "$count_file")
  fi
  count=$((count + 1))
  printf '%s\\n' "$count" > "$count_file"
  fail_after={status_fail_after if status_fail_after is not None else 0}
  if [[ "$fail_after" -gt 0 && "$count" -ge "$fail_after" ]]; then
    echo "status failed" >&2
    exit 1
  fi
  cat {status_file}
  exit 0
fi
if [[ "${{1:-}}" == set ]]; then
  printf '%s\\n' "$*" >> {set_log}
  if [[ {int(set_fail)} -eq 1 ]]; then
    echo "set failed" >&2
    exit 1
  fi
  exit 0
fi
echo "unexpected tailscale invocation" >&2
exit 98
""",
    )

    mullvad = bin_dir / "mullvad"
    if mullvad_mode == "absent":
        mullvad = bin_dir / "mullvad-absent"
    else:
        output_lines = {
            "disconnected": "Disconnected",
            "connected": "Connected",
            "daemon-only": "Mullvad daemon is running",
        }
        first_line = output_lines[mullvad_mode]
        extra = ""
        if mullvad_mode == "connected":
            extra = "echo '    Relay: synthetic'\n"
        _write_executable(
            mullvad,
            f"""#!/bin/bash
printf '%s\\n' "$*" >> {argv_log}
{{
  printf 'APPIMAGE=%s\\n' "${{APPIMAGE-}}"
}} >> {env_log}
if [[ "${{1:-}}" != status ]]; then
  echo "unexpected mullvad invocation" >&2
  exit 98
fi
echo {first_line!r}
{extra}
""",
        )

    curl = bin_dir / "curl"
    bodies = {
        "mullvad": f'{{"ip":"{FIXTURE_IP}","mullvad_exit_ip":true}}\n',
        "other": f'{{"ip":"{OTHER_FIXTURE_IP}","mullvad_exit_ip":false}}\n',
        "invalid": f'{{"ip":"{FIXTURE_IP}"}}\n',
    }
    body_file = log_dir / "curl-body.json"
    if curl_mode in bodies:
        body_file.write_text(bodies[curl_mode], encoding="utf-8")
    _write_executable(
        curl,
        f"""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> {curl_log}
{{
  printf 'APPIMAGE=%s\\n' "${{APPIMAGE-}}"
  printf 'APPDIR=%s\\n' "${{APPDIR-}}"
  printf 'ARGV0=%s\\n' "${{ARGV0-}}"
  printf 'LD_LIBRARY_PATH=%s\\n' "${{LD_LIBRARY_PATH-}}"
  printf 'LD_PRELOAD=%s\\n' "${{LD_PRELOAD-}}"
}} >> {env_log}
output=""
http_writer=""
i=1
args=("$@")
while [[ $i -le $# ]]; do
  arg="${{args[$((i-1))]}}"
  if [[ "$arg" == --output ]]; then
    output="${{args[$i]}}"
    i=$((i+2))
    continue
  fi
  if [[ "$arg" == --write-out ]]; then
    http_writer="${{args[$i]}}"
    i=$((i+2))
    continue
  fi
  i=$((i+1))
done
if [[ {curl_mode!r} == transport ]]; then
  echo "transport failed" >&2
  exit 7
fi
if [[ {curl_mode!r} == http-error ]]; then
  if [[ -n "$output" ]]; then
    : > "$output"
  fi
  if [[ "$http_writer" == '%{{http_code}}' ]]; then
    printf '500'
  fi
  exit 0
fi
if [[ -n "$output" ]]; then
  cat {body_file} > "$output"
fi
if [[ "$http_writer" == '%{{http_code}}' ]]; then
  printf '200'
fi
""",
    )

    ssh = bin_dir / "ssh"
    _write_executable(
        ssh,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {ssh_log}
{{
  printf 'APPIMAGE=%s\\n' "${{APPIMAGE-}}"
  if [[ -n "${{SSH_AUTH_SOCK-}}" ]]; then
    printf 'SSH_AUTH_SOCK_SET=1\\n'
  else
    printf 'SSH_AUTH_SOCK_SET=0\\n'
  fi
}} >> {env_log}
exit {ssh_exit}
""",
    )

    gpgconf = bin_dir / "gpgconf"
    if gpgconf_socket is None:
        _write_executable(
            gpgconf,
            """#!/bin/bash
exit 1
""",
        )
    else:
        _write_executable(
            gpgconf,
            f"""#!/bin/bash
if [[ "$1" == --list-dirs && "$2" == agent-ssh-socket ]]; then
  printf '%s\\n' {str(gpgconf_socket)!r}
  exit 0
fi
exit 1
""",
        )

    cwd_trap = tmp_path / "tailscale"
    _write_executable(
        cwd_trap,
        f"""#!/bin/bash
printf '%s\\n' "$*" >> {cwd_trap_log}
exit 97
""",
    )

    identity = tmp_path / "id_ed25519"
    identity.write_text("synthetic-identity\n", encoding="utf-8")

    argv_log.write_text("", encoding="utf-8")
    env_log.write_text("", encoding="utf-8")
    set_log.write_text("", encoding="utf-8")
    curl_log.write_text("", encoding="utf-8")
    ssh_log.write_text("", encoding="utf-8")
    cwd_trap_log.write_text("", encoding="utf-8")

    return {
        "tailscale": tailscale,
        "mullvad": mullvad,
        "curl": curl,
        "ssh": ssh,
        "gpgconf": gpgconf,
        "identity": identity,
        "argv_log": argv_log,
        "env_log": env_log,
        "set_log": set_log,
        "curl_log": curl_log,
        "ssh_log": ssh_log,
        "cwd_trap_log": cwd_trap_log,
        "cwd": tmp_path,
    }


def _hook_env(
    paths: dict[str, Path],
    *,
    include_mullvad: bool = True,
    prefix: str = "FRAMENEST_",
) -> dict[str, str]:
    """Build the synthetic hook environment under one identity prefix.

    The canonical names take the ``KRONIKA_`` prefix and the retained names the
    ``FRAMENEST_`` prefix. Both spellings are cleared from the inherited
    environment first, so a test always drives exactly one chosen spelling
    unless it adds the other one explicitly through ``extra_env``.
    """
    env = os.environ.copy()
    for polluted in ("APPIMAGE", "APPDIR", "ARGV0", "LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(polluted, None)
    hook_names = (
        "NETWORK_TEST_HOOKS",
        "NETWORK_TEST_TAILSCALE",
        "NETWORK_TEST_CURL",
        "NETWORK_TEST_SSH",
        "NETWORK_TEST_GPGCONF",
        "NETWORK_TEST_MULLVAD",
    )
    for name in hook_names:
        env.pop(f"KRONIKA_{name}", None)
        env.pop(f"FRAMENEST_{name}", None)
    env[f"{prefix}NETWORK_TEST_HOOKS"] = "1"
    env[f"{prefix}NETWORK_TEST_TAILSCALE"] = str(paths["tailscale"])
    env[f"{prefix}NETWORK_TEST_CURL"] = str(paths["curl"])
    env[f"{prefix}NETWORK_TEST_SSH"] = str(paths["ssh"])
    env[f"{prefix}NETWORK_TEST_GPGCONF"] = str(paths["gpgconf"])
    if include_mullvad and paths["mullvad"].name == "mullvad":
        env[f"{prefix}NETWORK_TEST_MULLVAD"] = str(paths["mullvad"])
    return env


def _run_bash_script(
    script: Path,
    paths: dict[str, Path],
    args: list[str],
    *,
    prefix: str = "FRAMENEST_",
    extra_env: dict[str, str] | None = None,
    include_mullvad: bool = True,
) -> subprocess.CompletedProcess[str]:
    env = _hook_env(paths, include_mullvad=include_mullvad, prefix=prefix)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(script), *args],
        cwd=paths["cwd"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _run_bash(
    paths: dict[str, Path],
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    include_mullvad: bool = True,
) -> subprocess.CompletedProcess[str]:
    return _run_bash_script(
        BASH_SCRIPT,
        paths,
        args,
        extra_env=extra_env,
        include_mullvad=include_mullvad,
    )


def _fish_executable() -> str:
    found = shutil.which("fish")
    if found:
        return found
    # ap exec keeps PATH at /usr/bin:/bin. Homebrew fish stays outside that
    # path on Darwin, so the gate tests resolve a known absolute executable.
    for candidate in (
        "/usr/bin/fish",
        "/bin/fish",
        "/usr/local/bin/fish",
        "/opt/homebrew/bin/fish",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    pytest.fail("fish is not installed; Fish wrapper and gate tests cannot run")


def _hermetic_env(paths: dict[str, Path]) -> dict[str, str]:
    """Return the synthetic Fish configuration environment every run receives.

    ``fish --no-config`` suppresses configuration files for the process it
    starts, but a wrapper that re-execs its target through the target's own
    shebang starts a new process that reads the configuration root of the
    environment it is given. Pointing every Fish configuration and data root at
    a synthetic directory keeps both entry points from ever reading the
    operator's personal configuration, without changing what either entry point
    does in production.
    """
    env = _hook_env(paths)
    hermetic_home = paths["cwd"] / "hermetic-home"
    hermetic_config = hermetic_home / "config"
    hermetic_data = hermetic_home / "data"
    hermetic_config.mkdir(parents=True, exist_ok=True)
    hermetic_data.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(hermetic_home)
    env["XDG_CONFIG_HOME"] = str(hermetic_config)
    env["XDG_DATA_HOME"] = str(hermetic_data)
    return env


def _run_fish(
    script: Path,
    paths: dict[str, Path],
    args: list[str],
    *,
    extra_env: dict[str, str] | None = None,
    clear_env: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    fish = _fish_executable()
    env = _hermetic_env(paths)
    for name in clear_env:
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        [fish, "--no-config", str(script), *args],
        cwd=paths["cwd"],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def _assert_no_secrets(text: str) -> None:
    assert FIXTURE_IP not in text
    assert OTHER_FIXTURE_IP not in text
    assert PUBLIC_KEY_LEAK not in text
    assert "must-not-print.example" not in text
    assert '"TailscaleIPs"' not in text
    assert '"Peer"' not in text


def test_expected_files_exist_and_are_executable() -> None:
    for path in (
        BASH_SCRIPT,
        FISH_SCRIPT,
        CANONICAL_BASH_SCRIPT,
        CANONICAL_FISH_SCRIPT,
        GATE_SCRIPT,
        CANONICAL_GATE_SCRIPT,
    ):
        assert path.is_file(), path
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH
    assert SCRIPT_README.is_file()
    assert OPERATOR_DOC.is_file()
    assert ADR_PATH.is_file()


def test_bash_and_fish_surfaces_expose_only_intended_interface() -> None:
    bash_text = BASH_SCRIPT.read_text(encoding="utf-8")
    fish_text = FISH_SCRIPT.read_text(encoding="utf-8")
    for token in ("status", "enable", "disable", "verify", "recover"):
        assert token in bash_text
    assert "enable --node" in bash_text
    assert "framenest_mullvad_egress.sh" in fish_text
    assert "$argv" in fish_text
    assert "tailscale set" not in fish_text
    assert "tailscale up" not in bash_text
    assert "--accept-routes" not in bash_text
    assert "--advertise-exit-node" not in bash_text


def test_canonical_bash_and_fish_surfaces_expose_only_intended_interface() -> None:
    bash_text = CANONICAL_BASH_SCRIPT.read_text(encoding="utf-8")
    fish_text = CANONICAL_FISH_SCRIPT.read_text(encoding="utf-8")
    for token in ("status", "enable", "disable", "verify", "recover"):
        assert token in bash_text
    assert "enable --node" in bash_text
    assert "kronika_mullvad_egress.sh" in fish_text
    assert "framenest_mullvad_egress.sh" not in fish_text
    assert "$argv" in fish_text
    assert "tailscale set" not in fish_text
    assert "tailscale up" not in bash_text
    assert "--accept-routes" not in bash_text
    assert "--advertise-exit-node" not in bash_text


def test_unknown_subcommand_fails_without_invoking_tailscale(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["definitely-not-a-command"])
    assert result.returncode != 0
    assert "Unknown subcommand" in result.stderr
    assert paths["argv_log"].read_text(encoding="utf-8") == ""
    assert paths["cwd_trap_log"].read_text(encoding="utf-8") == ""


def test_enable_without_node_fails(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["enable"])
    assert result.returncode != 0
    assert "--node" in result.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""
    assert paths["argv_log"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    "node",
    [
        "--evil.mullvad.ts.net",
        "se got.mullvad.ts.net",
        "not a dns",
        "example.com",
        "auto:any",
        "foo.mullvad.ts.net.evil.example",
        ".mullvad.ts.net",
        "mullvad.ts.net",
    ],
)
def test_invalid_node_names_fail(tmp_path: Path, node: str) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["enable", "--node", node])
    assert result.returncode != 0
    assert paths["set_log"].read_text(encoding="utf-8") == ""
    assert "auto:any" not in paths["argv_log"].read_text(encoding="utf-8")


def test_valid_mullvad_hostname_sets_expected_arguments(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode == 0, result.stderr
    set_line = paths["set_log"].read_text(encoding="utf-8").strip()
    assert set_line == (
        f"set --exit-node={MULLVAD_NODE} --exit-node-allow-lan-access=false"
    )
    assert "auto:any" not in paths["argv_log"].read_text(encoding="utf-8")


def test_enable_includes_lan_access_false(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode == 0, result.stderr
    assert "--exit-node-allow-lan-access=false" in paths["set_log"].read_text(
        encoding="utf-8"
    )


def test_no_command_uses_auto_any(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    _run_bash(paths, ["status"])
    _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    _run_bash(paths, ["disable"])
    _run_bash(paths, ["recover"])
    joined = (
        paths["argv_log"].read_text(encoding="utf-8")
        + paths["set_log"].read_text(encoding="utf-8")
    )
    assert "auto:any" not in joined


def test_disable_clears_only_selected_exit_node(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["disable"])
    assert result.returncode == 0, result.stderr
    set_line = paths["set_log"].read_text(encoding="utf-8").strip()
    assert set_line == "set --exit-node="
    assert "allow-lan-access" not in set_line
    assert "--accept-routes" not in set_line


def test_recover_clears_exit_node_and_preserves_first_failure(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path, set_fail=True, status_fail_after=2)
    result = _run_bash(paths, ["recover"])
    assert result.returncode != 0
    combined = _combined(result)
    assert "Failed to clear the selected exit node." in combined
    assert combined.index("Failed to clear the selected exit node.") < combined.find(
        "Failed to read Tailscale status after recover."
    ) or "Failed to read Tailscale status after recover." not in combined
    set_line = paths["set_log"].read_text(encoding="utf-8").strip()
    assert set_line == "set --exit-node="


def test_needs_login_blocks_mutation(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        status_json=_healthy_status_json(backend="NeedsLogin"),
    )
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode != 0
    assert "NeedsLogin" in result.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""


def test_missing_mullvad_availability_blocks_mutation(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        status_json=_healthy_status_json(mullvad_available=False),
    )
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode != 0
    assert "No Mullvad exit nodes are available" in result.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""


def test_self_advertised_exit_node_blocks_mutation(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        status_json=_healthy_status_json(advertises=True),
    )
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode != 0
    assert "advertises itself as an exit node" in result.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""


def test_competing_standalone_mullvad_tunnel_blocks_mutation(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path, mullvad_mode="connected")
    result = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert result.returncode != 0
    assert "competing standalone Mullvad tunnel" in result.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""


def test_active_daemon_without_tunnel_is_not_called_connected(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path, mullvad_mode="disconnected")
    result = _run_bash(paths, ["status"])
    assert result.returncode == 0, result.stderr
    assert "standalone-mullvad-tunnel: disconnected" in result.stdout
    assert "standalone-mullvad-tunnel: connected" not in result.stdout
    enable = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert enable.returncode == 0, enable.stderr


def test_absence_of_tailscale_get_uses_readonly_fallback(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        get_supported=False,
        status_json=_healthy_status_json(selected_mullvad=True),
    )
    result = _run_bash(paths, ["status"])
    assert result.returncode == 0, result.stderr
    assert "client-get: unsupported" in result.stdout
    assert f"exit-node: mullvad:{MULLVAD_NODE}" in result.stdout
    assert "lan-access: unavailable-without-tailscale-get" in result.stdout
    enable = _run_bash(paths, ["enable", "--node", MULLVAD_NODE])
    assert enable.returncode == 0, enable.stderr
    assert "--exit-node=" in paths["set_log"].read_text(encoding="utf-8")


def test_unreadable_tailscale_get_prefs_fall_back_to_status_json(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        get_pref_readable=False,
        status_json=_healthy_status_json(),
        mullvad_mode="disconnected",
    )
    result = _run_bash(paths, ["status"])
    combined = _combined(result)
    assert result.returncode == 0, result.stderr
    assert "backend: Running" in result.stdout
    assert "client-get: unsupported" in result.stdout
    assert "exit-node: none" in result.stdout
    assert "lan-access: unavailable-without-tailscale-get" in result.stdout
    assert "mullvad-nodes: available" in result.stdout
    assert "self-advertises-exit-node: no" in result.stdout
    assert "standalone-mullvad-tunnel: disconnected" in result.stdout
    argv_text = paths["argv_log"].read_text(encoding="utf-8")
    for line in argv_text.splitlines():
        first = line.split()[0] if line.split() else ""
        assert first not in {"set", "up", "down", "login", "logout"}
    assert paths["set_log"].read_text(encoding="utf-8") == ""
    assert "fn-unreadable-pref-token" not in combined
    assert "tailscale get is present but could not read" not in combined
    _assert_no_secrets(combined)
    assert "{" not in result.stdout


def test_readable_opaque_get_reconciles_selected_mullvad_from_status_json(
    tmp_path: Path,
) -> None:
    opaque_pref = "fnOpaqueSelectedPref"
    paths = _install_fakes(
        tmp_path,
        get_exit_node=opaque_pref,
        get_lan="false",
        status_json=_healthy_status_json(selected_mullvad=True),
        mullvad_mode="disconnected",
    )
    result = _run_bash(paths, ["status"])
    combined = _combined(result)
    assert result.returncode == 0, result.stderr
    assert "backend: Running" in result.stdout
    assert "client-get: supported" in result.stdout
    assert f"exit-node: mullvad:{MULLVAD_NODE}" in result.stdout
    assert "exit-node: non-mullvad" not in result.stdout
    assert "lan-access: false" in result.stdout
    assert "mullvad-nodes: available" in result.stdout
    assert "self-advertises-exit-node: no" in result.stdout
    assert "standalone-mullvad-tunnel: disconnected" in result.stdout
    assert opaque_pref not in combined
    argv_text = paths["argv_log"].read_text(encoding="utf-8")
    for line in argv_text.splitlines():
        first = line.split()[0] if line.split() else ""
        assert first not in {"set", "up", "down", "login", "logout"}
    assert paths["set_log"].read_text(encoding="utf-8") == ""
    _assert_no_secrets(combined)
    assert "{" not in result.stdout


def test_readable_opaque_get_keeps_selected_non_mullvad_from_status_json(
    tmp_path: Path,
) -> None:
    opaque_pref = "fnOpaqueSelectedPref"
    paths = _install_fakes(
        tmp_path,
        get_exit_node=opaque_pref,
        get_lan="false",
        status_json=_healthy_status_json(selected_other=True),
        mullvad_mode="disconnected",
    )
    result = _run_bash(paths, ["status"])
    combined = _combined(result)
    assert result.returncode == 0, result.stderr
    assert "client-get: supported" in result.stdout
    assert "exit-node: non-mullvad" in result.stdout
    assert "exit-node: mullvad:" not in result.stdout
    assert opaque_pref not in combined
    argv_text = paths["argv_log"].read_text(encoding="utf-8")
    for line in argv_text.splitlines():
        first = line.split()[0] if line.split() else ""
        assert first not in {"set", "up", "down", "login", "logout"}
    assert paths["set_log"].read_text(encoding="utf-8") == ""
    _assert_no_secrets(combined)
    assert "{" not in result.stdout


def test_diagnostic_transport_failure_is_unknown_not_non_mullvad(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path, curl_mode="transport")
    result = _run_bash(paths, ["verify"])
    assert result.returncode != 0
    combined = _combined(result)
    assert "unknown" in combined
    assert "non-Mullvad egress" not in combined
    _assert_no_secrets(combined)


def test_diagnostic_fixtures_classified_without_printing_ips(tmp_path: Path) -> None:
    mullvad_paths = _install_fakes(tmp_path / "mullvad", curl_mode="mullvad")
    mullvad_result = _run_bash(mullvad_paths, ["verify"])
    assert mullvad_result.returncode == 0, mullvad_result.stderr
    assert mullvad_result.stdout.strip() == "Mullvad egress"
    _assert_no_secrets(_combined(mullvad_result))

    other_paths = _install_fakes(tmp_path / "other", curl_mode="other")
    other_result = _run_bash(other_paths, ["verify"])
    assert other_result.returncode != 0
    assert "non-Mullvad egress" in other_result.stdout
    _assert_no_secrets(_combined(other_result))


def test_raw_status_json_is_not_emitted(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash(paths, ["status"])
    assert result.returncode == 0, result.stderr
    _assert_no_secrets(_combined(result))
    assert "{" not in result.stdout


def test_appimage_variables_do_not_reach_child_tools(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    polluted = {
        "APPIMAGE": "/tmp/Cursor.AppImage",
        "APPDIR": "/tmp/appdir",
        "ARGV0": "cursor",
        "LD_LIBRARY_PATH": "/tmp/bad-libs",
        "LD_PRELOAD": "/tmp/bad.so",
    }
    result = _run_bash(paths, ["status"], extra_env=polluted)
    assert result.returncode == 0, result.stderr
    env_text = paths["env_log"].read_text(encoding="utf-8")
    assert "APPIMAGE=/tmp/Cursor.AppImage" not in env_text
    assert "APPDIR=/tmp/appdir" not in env_text
    assert "ARGV0=cursor" not in env_text
    assert "LD_LIBRARY_PATH=/tmp/bad-libs" not in env_text
    assert "LD_PRELOAD=/tmp/bad.so" not in env_text
    assert "APPIMAGE=\n" in env_text


def test_fish_wrapper_preserves_arguments_and_exit_status(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        FISH_SCRIPT,
        paths,
        ["enable", "--node", MULLVAD_NODE],
    )
    assert result.returncode == 0, result.stderr
    assert paths["set_log"].read_text(encoding="utf-8").strip() == (
        f"set --exit-node={MULLVAD_NODE} --exit-node-allow-lan-access=false"
    )
    failing = _install_fakes(tmp_path / "fail", set_fail=True)
    failed = _run_fish(FISH_SCRIPT, failing, ["enable", "--node", MULLVAD_NODE])
    bash_failed = _run_bash(failing, ["enable", "--node", MULLVAD_NODE])
    assert failed.returncode == bash_failed.returncode != 0


def test_ssh_gate_includes_required_options(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        [
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
            "--command",
            "framenest_mullvad_egress.sh status",
        ],
    )
    assert result.returncode == 0, result.stderr
    logged = paths["ssh_log"].read_text(encoding="utf-8")
    for option in (
        "BatchMode=yes",
        "RequestTTY=no",
        "StrictHostKeyChecking=yes",
        "IdentitiesOnly=yes",
        "ForwardAgent=no",
        "ClearAllForwardings=yes",
        "ConnectTimeout=10",
        "ServerAliveInterval=15",
        "ServerAliveCountMax=2",
    ):
        assert f"-o {option}" in logged
    assert "framenest_mullvad_egress.sh status" in logged
    assert "nuc-magicdns-name" in logged
    assert "operator-user@" in logged


_AMBIENT_NUC_SSH_NAMES = (
    "FRAMENEST_NUC_SSH_TARGET",
    "FRAMENEST_NUC_SSH_USER",
    "FRAMENEST_NUC_SSH_IDENTITY",
    "FRAMENEST_NUC_SSH_COMMAND",
)


@pytest.mark.parametrize("missing", ["target", "user", "identity", "command"])
def test_ssh_gate_rejects_missing_required_values(tmp_path: Path, missing: str) -> None:
    paths = _install_fakes(tmp_path)
    args = {
        "target": ["--target", "nuc-magicdns-name"],
        "user": ["--user", "operator-user"],
        "identity": ["--identity", str(paths["identity"])],
        "command": ["--command", "framenest_mullvad_egress.sh status"],
    }
    messages = {
        "target": "Missing remote target.",
        "user": "Missing remote user.",
        "identity": "Missing identity file.",
        "command": "Missing bounded remote command.",
    }
    del args[missing]
    flat = [item for group in args.values() for item in group]
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        flat,
        clear_env=_AMBIENT_NUC_SSH_NAMES,
    )
    assert result.returncode == 2
    assert messages[missing] in result.stderr
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_run_fish_ignores_injected_startup_configuration(tmp_path: Path) -> None:
    config_home = tmp_path / "xdg-config"
    fish_config = config_home / "fish"
    fish_config.mkdir(parents=True)
    (fish_config / "config.fish").write_text(
        "echo HERMETIC_FISH_STARTUP_MARKER\n",
        encoding="utf-8",
    )
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        CANONICAL_GATE_SCRIPT,
        paths,
        ["--help"],
        extra_env={"XDG_CONFIG_HOME": str(config_home)},
    )
    combined = _combined(result)
    assert "HERMETIC_FISH_STARTUP_MARKER" not in combined
    assert result.returncode == 0, combined
    assert "Usage: kronika_nuc_worker_gate.fish --probe" in result.stderr


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
def test_either_gate_path_never_sees_the_operator_configuration_root(
    tmp_path: Path, script: Path
) -> None:
    """The retained wrapper re-execs through its target's own shebang.

    That new process reads the Fish configuration root of the environment it is
    given, so the harness must give it a synthetic one. Asserting on the exact
    environment the harness builds keeps the claim precise: the operator's real
    home and configuration roots are never in scope for either entry point.
    """
    paths = _install_fakes(tmp_path)
    env = _hermetic_env(paths)
    hermetic = paths["cwd"] / "hermetic-home"

    for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME"):
        assert not env[name].startswith(str(Path.home())), name
        assert env[name].startswith(str(hermetic)), name
    assert _run_fish(script, paths, ["--help"]).returncode == 0


# --- canonical gate name and the two accepted identity prefixes ---


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
def test_both_gate_paths_reach_the_same_gate(
    tmp_path: Path, script: Path
) -> None:
    """The retained wrapper and the canonical gate behave identically."""
    paths = _install_fakes(tmp_path)
    canonical = _run_fish(
        CANONICAL_GATE_SCRIPT, paths, ["--probe"], clear_env=_AMBIENT_NUC_SSH_NAMES
    )
    retained = _run_fish(
        GATE_SCRIPT, paths, ["--probe"], clear_env=_AMBIENT_NUC_SSH_NAMES
    )

    assert retained.returncode == canonical.returncode
    assert retained.stdout == canonical.stdout
    assert retained.stderr == canonical.stderr
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_gate_reads_either_identity_prefix_for_its_operator_variables(
    tmp_path: Path, script: Path, prefix: str
) -> None:
    """Only the new names, only the old names, and no names at all."""
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        script,
        paths,
        [
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
            "--command",
            "true",
        ],
        extra_env={
            f"{prefix}NUC_SSH_TARGET": "nuc-magicdns-name",
            f"{prefix}NUC_SSH_USER": "operator-user",
            f"{prefix}NUC_SSH_IDENTITY": str(paths["identity"]),
            f"{prefix}NUC_SSH_COMMAND": "true",
        },
        clear_env=_AMBIENT_NUC_SSH_NAMES,
    )

    assert result.returncode == 0, _combined(result)
    assert "operator-user@nuc-magicdns-name" in paths["ssh_log"].read_text(
        encoding="utf-8"
    )


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_gate_reads_either_identity_prefix_for_its_test_hooks(
    tmp_path: Path, script: Path, prefix: str
) -> None:
    """Each accepted prefix alone is honoured for a hook value as well."""
    other = "FRAMENEST_" if prefix == "KRONIKA_" else "KRONIKA_"
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        script,
        paths,
        [
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
            "--command",
            "true",
        ],
        extra_env={f"{prefix}NETWORK_TEST_SSH": "/nonexistent/ssh"},
        clear_env=(*_AMBIENT_NUC_SSH_NAMES, f"{other}NETWORK_TEST_SSH"),
    )

    assert result.returncode == 1, _combined(result)
    assert "Test hook tool path is not a trusted absolute executable." in result.stderr
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
def test_gate_exits_two_on_a_conflicting_pair_naming_only_the_variables(
    tmp_path: Path, script: Path
) -> None:
    """Both set to different values exits 2 and discloses no value."""
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        script,
        paths,
        ["--probe"],
        extra_env={
            "KRONIKA_NUC_SSH_TARGET": "alpha-host",
            "FRAMENEST_NUC_SSH_TARGET": "beta-host",
        },
        clear_env=_AMBIENT_NUC_SSH_NAMES,
    )
    combined = _combined(result)

    assert result.returncode == 2, combined
    assert "KRONIKA_NUC_SSH_TARGET" in combined
    assert "FRAMENEST_NUC_SSH_TARGET" in combined
    assert "alpha-host" not in combined
    assert "beta-host" not in combined
    assert "Conflicting environment variables " in combined
    assert "are set to different values" in combined
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize("script", [CANONICAL_GATE_SCRIPT, GATE_SCRIPT])
def test_gate_accepts_identical_values_in_both_prefixes(
    tmp_path: Path, script: Path
) -> None:
    paths = _install_fakes(tmp_path)
    plain = _run_fish(
        script, paths, ["--probe"], clear_env=_AMBIENT_NUC_SSH_NAMES
    )
    both = _run_fish(
        script,
        paths,
        ["--probe"],
        extra_env={
            "KRONIKA_NETWORK_TEST_SSH": paths["ssh"],
            "FRAMENEST_NETWORK_TEST_SSH": paths["ssh"],
        },
        clear_env=_AMBIENT_NUC_SSH_NAMES,
    )

    assert both.returncode == plain.returncode
    assert "Conflicting environment variables " not in _combined(both)


def test_gate_treats_an_explicitly_empty_value_as_unset(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        CANONICAL_GATE_SCRIPT,
        paths,
        [
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
        ],
        extra_env={"KRONIKA_NUC_SSH_COMMAND": ""},
        clear_env=_AMBIENT_NUC_SSH_NAMES,
    )

    assert result.returncode == 2
    assert "Missing bounded remote command." in result.stderr
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_contains_no_private_values() -> None:
    text = CANONICAL_GATE_SCRIPT.read_text(encoding="utf-8")
    for token in (
        "michal",
        "agile",
        "/home/agile",
        "SHA256:",
        "@gmail.com",
        "tailscale.com",
    ):
        assert token not in text
    assert "id_ed25519" not in text
    assert "100." not in text


def _bind_unix_socket(path: Path) -> socket.socket:
    if path.exists():
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    sock.listen(1)
    return sock


def test_ssh_gate_probe_reports_ready_without_printing_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "agent.sock"
    agent = _bind_unix_socket(socket_path)
    try:
        paths = _install_fakes(tmp_path, gpgconf_socket=socket_path)
        first = _run_fish(GATE_SCRIPT, paths, ["--probe"])
        second = _run_fish(GATE_SCRIPT, paths, ["--probe"])
    finally:
        agent.close()
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "ssh-agent: ready"
    assert str(socket_path) not in _combined(first)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "ssh-agent: ready"
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_reports_absent_when_gpgconf_fails(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(GATE_SCRIPT, paths, ["--probe"])
    assert result.returncode == 1
    assert result.stdout.strip() == "ssh-agent: absent"
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_ignores_env_ssh_defaults(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env={
            "FRAMENEST_NUC_SSH_TARGET": "nuc-magicdns-name",
            "FRAMENEST_NUC_SSH_USER": "operator-user",
            "FRAMENEST_NUC_SSH_IDENTITY": str(paths["identity"]),
            "FRAMENEST_NUC_SSH_COMMAND": "true",
        },
    )
    assert result.returncode == 1
    assert result.stdout.strip() == "ssh-agent: absent"
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_rejects_ssh_cli_parameters(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        [
            "--probe",
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
            "--command",
            "true",
        ],
    )
    assert result.returncode == 2
    assert "Probe mode does not accept SSH target parameters." in result.stderr
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_attaches_agent_without_printing_socket(tmp_path: Path) -> None:
    socket_path = tmp_path / "agent.sock"
    agent = _bind_unix_socket(socket_path)
    try:
        paths = _install_fakes(tmp_path, gpgconf_socket=socket_path)
        result = _run_fish(
            GATE_SCRIPT,
            paths,
            [
                "--target",
                "nuc-magicdns-name",
                "--user",
                "operator-user",
                "--identity",
                str(paths["identity"]),
                "--command",
                "true",
            ],
        )
    finally:
        agent.close()
    assert result.returncode == 0, result.stderr
    combined = _combined(result)
    assert str(socket_path) not in combined
    env_text = paths["env_log"].read_text(encoding="utf-8")
    assert "SSH_AUTH_SOCK_SET=1" in env_text
    assert str(socket_path) not in env_text
    assert "true" in paths["ssh_log"].read_text(encoding="utf-8")


_DARWIN_AGENT_SOCKET = "/var/run/com.apple.launchd.fixture/Listeners"
_DARWIN_AGENT_RESOLVED = "/private/var/run/com.apple.launchd.fixture/Listeners"


def _darwin_agent_env(**overrides: str) -> dict[str, str]:
    env = {
        "FRAMENEST_NETWORK_TEST_GPGCONF": "",
        "FRAMENEST_NETWORK_TEST_UNAME": "Darwin",
        "FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK": _DARWIN_AGENT_SOCKET,
        "FRAMENEST_NETWORK_TEST_AGENT_IS_SOCKET": "1",
        "FRAMENEST_NETWORK_TEST_AGENT_OWNER_MATCH": "1",
        "FRAMENEST_NETWORK_TEST_AGENT_RESOLVED": _DARWIN_AGENT_RESOLVED,
        "FRAMENEST_NETWORK_TEST_SSH_ADD_STATUS": "0",
    }
    env.update(overrides)
    return env


def _assert_probe_line(result: subprocess.CompletedProcess[str], expected: str) -> None:
    stdout = result.stdout.strip()
    if stdout not in {"ssh-agent: ready", "ssh-agent: absent"}:
        raise AssertionError("probe output was not a sanitized agent status")
    assert result.returncode == (0 if expected == "ssh-agent: ready" else 1)
    assert stdout == expected
    assert _DARWIN_AGENT_SOCKET not in _combined(result)
    assert _DARWIN_AGENT_RESOLVED not in _combined(result)


def test_ssh_gate_probe_accepts_validated_darwin_launchd_agent(tmp_path: Path) -> None:
    """Causal regression: Darwin with no gpgconf accepts a validated launchd socket."""
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env=_darwin_agent_env(),
        clear_env=("SSH_AUTH_SOCK",),
    )
    _assert_probe_line(result, "ssh-agent: ready")
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_accepts_darwin_agent_with_no_identities(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env=_darwin_agent_env(FRAMENEST_NETWORK_TEST_SSH_ADD_STATUS="1"),
        clear_env=("SSH_AUTH_SOCK",),
    )
    _assert_probe_line(result, "ssh-agent: ready")
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


@pytest.mark.parametrize(
    ("case", "overrides"),
    [
        ("relative-path", {"FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK": "relative.sock"}),
        ("dotdot-segment", {"FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK": "/tmp/../agent.sock"}),
        ("non-socket", {"FRAMENEST_NETWORK_TEST_AGENT_IS_SOCKET": "0"}),
        ("foreign-owner", {"FRAMENEST_NETWORK_TEST_AGENT_OWNER_MATCH": "0"}),
        (
            "outside-launchd",
            {"FRAMENEST_NETWORK_TEST_AGENT_RESOLVED": "/tmp/not-launchd/Listeners"},
        ),
        ("dead-agent", {"FRAMENEST_NETWORK_TEST_SSH_ADD_STATUS": "2"}),
        ("empty-sock", {"FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK": ""}),
    ],
)
def test_ssh_gate_probe_rejects_invalid_darwin_agent(
    tmp_path: Path,
    case: str,
    overrides: dict[str, str],
) -> None:
    del case
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env=_darwin_agent_env(**overrides),
        clear_env=("SSH_AUTH_SOCK",),
    )
    _assert_probe_line(result, "ssh-agent: absent")
    leaked = overrides.get("FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK", "")
    if leaked:
        assert leaked not in _combined(result)
    resolved = overrides.get("FRAMENEST_NETWORK_TEST_AGENT_RESOLVED", "")
    if resolved:
        assert resolved not in _combined(result)
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_ignores_darwin_fallback_on_linux(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env=_darwin_agent_env(FRAMENEST_NETWORK_TEST_UNAME="Linux"),
        clear_env=("SSH_AUTH_SOCK",),
    )
    _assert_probe_line(result, "ssh-agent: absent")
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_does_not_fall_back_when_gpgconf_fails(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    env = _darwin_agent_env()
    del env["FRAMENEST_NETWORK_TEST_GPGCONF"]
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env=env,
        clear_env=("SSH_AUTH_SOCK",),
    )
    _assert_probe_line(result, "ssh-agent: absent")
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_prefers_present_gpgconf_over_darwin_fallback(
    tmp_path: Path,
) -> None:
    socket_path = tmp_path / "agent.sock"
    agent = _bind_unix_socket(socket_path)
    try:
        paths = _install_fakes(tmp_path, gpgconf_socket=socket_path)
        env = _darwin_agent_env(FRAMENEST_NETWORK_TEST_SSH_AUTH_SOCK="relative.sock")
        del env["FRAMENEST_NETWORK_TEST_GPGCONF"]
        result = _run_fish(
            GATE_SCRIPT,
            paths,
            ["--probe"],
            extra_env=env,
            clear_env=("SSH_AUTH_SOCK",),
        )
    finally:
        agent.close()
    _assert_probe_line(result, "ssh-agent: ready")
    assert str(socket_path) not in _combined(result)
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_probe_darwin_fallback_inert_without_hook_values(
    tmp_path: Path,
) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        ["--probe"],
        extra_env={"FRAMENEST_NETWORK_TEST_GPGCONF": ""},
    )
    _assert_probe_line(result, "ssh-agent: absent")
    assert paths["ssh_log"].read_text(encoding="utf-8") == ""


def test_ssh_gate_darwin_fallback_sets_socket_for_ssh_child(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        GATE_SCRIPT,
        paths,
        [
            "--target",
            "nuc-magicdns-name",
            "--user",
            "operator-user",
            "--identity",
            str(paths["identity"]),
            "--command",
            "true",
        ],
        extra_env=_darwin_agent_env(),
        clear_env=("SSH_AUTH_SOCK", *_AMBIENT_NUC_SSH_NAMES),
    )
    assert result.returncode == 0
    assert _DARWIN_AGENT_SOCKET not in _combined(result)
    assert _DARWIN_AGENT_RESOLVED not in _combined(result)
    env_text = paths["env_log"].read_text(encoding="utf-8")
    assert "SSH_AUTH_SOCK_SET=1" in env_text
    assert _DARWIN_AGENT_SOCKET not in env_text
    assert "true" in paths["ssh_log"].read_text(encoding="utf-8")


def test_ssh_gate_darwin_ownership_follows_final_symlink(tmp_path: Path) -> None:
    """Ownership stat follows a presented symlink onto the socket inode.

    The link and the socket share this user's uid, so a live same-user probe
    cannot tell link ownership from target ownership. The production command
    shape is the check that fails while the gate still calls stat without -L.
    """
    socket_path = tmp_path / "Listeners"
    presented = tmp_path / "agent-link"
    agent = _bind_unix_socket(socket_path)
    try:
        presented.symlink_to(socket_path)
        link_info = presented.lstat()
        target_info = presented.stat()
        assert stat.S_ISLNK(link_info.st_mode)
        assert not stat.S_ISSOCK(link_info.st_mode)
        assert stat.S_ISSOCK(target_info.st_mode)
        assert link_info.st_ino != target_info.st_ino
        assert link_info.st_uid == target_info.st_uid == os.getuid()
    finally:
        agent.close()

    text = CANONICAL_GATE_SCRIPT.read_text(encoding="utf-8")
    start = text.index("function _attach_darwin_ambient_from_system")
    end = text.index("\nfunction ", start + 1)
    ownership = [
        line.strip()
        for line in text[start:end].splitlines()
        if "$stat_bin" in line and "%u" in line
    ]
    assert ownership == ['$stat_bin -L -f %u "$sock" 2>/dev/null']


def test_scripts_contain_no_forbidden_commands() -> None:
    for path in (
        BASH_SCRIPT,
        FISH_SCRIPT,
        CANONICAL_BASH_SCRIPT,
        CANONICAL_FISH_SCRIPT,
        GATE_SCRIPT,
        CANONICAL_GATE_SCRIPT,
    ):
        text = path.read_text(encoding="utf-8")
        for token in FORBIDDEN_SCRIPT_TOKENS:
            assert token not in text, f"{path.name} contains {token}"
        assert "auto:any" not in text or "explicit" in text


def test_scripts_do_not_configure_operator_or_invoke_sudo() -> None:
    for path in (
        BASH_SCRIPT,
        FISH_SCRIPT,
        CANONICAL_BASH_SCRIPT,
        CANONICAL_FISH_SCRIPT,
        GATE_SCRIPT,
        CANONICAL_GATE_SCRIPT,
    ):
        text = path.read_text(encoding="utf-8")
        assert "--operator" not in text
        assert "sudo" not in text


def test_documentation_contains_no_private_values() -> None:
    ipv4 = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b")
    email = re.compile(r"\b\S+@\S+\.\S+\b")
    fingerprint = re.compile(r"SHA256:")
    tailnet = re.compile(r"tail[0-9a-z]+\.ts\.net")
    for path in OPERATOR_DOC_PATHS:
        text = path.read_text(encoding="utf-8")
        assert "/home/agile" not in text
        assert ipv4.search(text) is None, path
        assert email.search(text) is None, path
        assert fingerprint.search(text) is None, path
        assert tailnet.search(text) is None, path
    index = ADR_INDEX_PATH.read_text(encoding="utf-8")
    assert (
        "0058 | Independent Mullvad Egress and Operator Network Recovery"
        in index
    )
    adr = ADR_PATH.read_text(encoding="utf-8")
    assert "## Status\n\n`Accepted`" in adr
    assert "2026-08-13" in adr
    assert "auto:any" in adr
    assert "ahw" in adr
    assert "framenest-nuc" in adr


def test_operator_network_doc_requires_ten_minute_nuc_rollback() -> None:
    text = OPERATOR_DOC.read_text(encoding="utf-8")
    rollback = text.split("## Transient NUC rollback design", 1)[1]
    assert "10 minutes" in rollback


# --- canonical Mullvad counterparts and the dual-prefix contract ---


@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_canonical_mullvad_bash_reads_either_identity_prefix(
    tmp_path: Path, prefix: str
) -> None:
    """Canonical-only and retained-only hook spellings both drive the script."""
    paths = _install_fakes(tmp_path)
    result = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["enable", "--node", MULLVAD_NODE],
        prefix=prefix,
    )
    assert result.returncode == 0, result.stderr
    assert paths["set_log"].read_text(encoding="utf-8").strip() == (
        f"set --exit-node={MULLVAD_NODE} --exit-node-allow-lan-access=false"
    )


def test_canonical_mullvad_accepts_identical_values_in_both_prefixes(
    tmp_path: Path,
) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["status"],
        prefix="KRONIKA_",
        extra_env={
            "FRAMENEST_NETWORK_TEST_TAILSCALE": str(paths["tailscale"]),
            "FRAMENEST_NETWORK_TEST_CURL": str(paths["curl"]),
            "FRAMENEST_NETWORK_TEST_MULLVAD": str(paths["mullvad"]),
        },
    )
    assert result.returncode == 0, result.stderr
    assert "Conflicting environment variables " not in _combined(result)


def test_canonical_mullvad_exits_two_on_a_conflicting_pair_naming_only_names(
    tmp_path: Path,
) -> None:
    """A conflicting hook pair exits 2 and discloses no value."""
    paths = _install_fakes(tmp_path)
    result = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["status"],
        prefix="KRONIKA_",
        extra_env={"FRAMENEST_NETWORK_TEST_TAILSCALE": "/nonexistent/retained-value"},
    )
    combined = _combined(result)
    assert result.returncode == 2, combined
    assert "KRONIKA_NETWORK_TEST_TAILSCALE" in combined
    assert "FRAMENEST_NETWORK_TEST_TAILSCALE" in combined
    assert "retained-value" not in combined
    assert str(paths["tailscale"]) not in combined
    assert "Conflicting environment variables " in combined
    assert "are set to different values" in combined
    assert paths["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_mullvad_treats_an_empty_value_as_unset(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    fallback = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["status"],
        prefix="KRONIKA_",
        extra_env={
            "KRONIKA_NETWORK_TEST_TAILSCALE": "",
            "FRAMENEST_NETWORK_TEST_TAILSCALE": str(paths["tailscale"]),
        },
    )
    assert fallback.returncode == 0, fallback.stderr
    assert "Conflicting environment variables " not in _combined(fallback)

    before_both_empty = paths["argv_log"].read_text(encoding="utf-8")
    both_empty = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["status"],
        prefix="KRONIKA_",
        extra_env={
            "KRONIKA_NETWORK_TEST_TAILSCALE": "",
            "FRAMENEST_NETWORK_TEST_TAILSCALE": "",
        },
    )
    assert both_empty.returncode == 1
    assert (
        "Required tool 'tailscale' is not provided through the test hook."
        in both_empty.stderr
    )
    assert paths["argv_log"].read_text(encoding="utf-8") == before_both_empty


def test_canonical_mullvad_missing_hook_tool_refuses(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    result = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["status"],
        prefix="KRONIKA_",
        extra_env={"KRONIKA_NETWORK_TEST_TAILSCALE": "/nonexistent/tailscale"},
    )
    assert result.returncode == 1
    assert "Test hook tool path is not a trusted absolute executable." in result.stderr
    assert paths["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_mullvad_preserves_refusal_exit_codes(tmp_path: Path) -> None:
    paths = _install_fakes(
        tmp_path,
        status_json=_healthy_status_json(backend="NeedsLogin"),
    )
    needs_login = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["enable", "--node", MULLVAD_NODE],
        prefix="KRONIKA_",
    )
    assert needs_login.returncode == 3
    assert "NeedsLogin" in needs_login.stderr
    assert paths["set_log"].read_text(encoding="utf-8") == ""

    invalid = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["enable", "--node", "example.com"],
        prefix="KRONIKA_",
    )
    assert invalid.returncode == 2
    assert paths["set_log"].read_text(encoding="utf-8") == ""

    missing_node = _run_bash_script(
        CANONICAL_BASH_SCRIPT, paths, ["enable"], prefix="KRONIKA_"
    )
    assert missing_node.returncode == 2
    assert "--node" in missing_node.stderr

    unknown = _run_bash_script(
        CANONICAL_BASH_SCRIPT,
        paths,
        ["definitely-not-a-command"],
        prefix="KRONIKA_",
    )
    assert unknown.returncode == 2
    assert "Unknown subcommand" in unknown.stderr


def test_canonical_mullvad_preserves_success_and_verify_exit_codes(
    tmp_path: Path,
) -> None:
    paths = _install_fakes(tmp_path)
    status = _run_bash_script(
        CANONICAL_BASH_SCRIPT, paths, ["status"], prefix="KRONIKA_"
    )
    assert status.returncode == 0, status.stderr

    other = _install_fakes(tmp_path / "other", curl_mode="other")
    non_mullvad = _run_bash_script(
        CANONICAL_BASH_SCRIPT, other, ["verify"], prefix="KRONIKA_"
    )
    assert non_mullvad.returncode == 4
    assert "non-Mullvad egress" in non_mullvad.stdout

    transport = _install_fakes(tmp_path / "transport", curl_mode="transport")
    failed = _run_bash_script(
        CANONICAL_BASH_SCRIPT, transport, ["verify"], prefix="KRONIKA_"
    )
    assert failed.returncode == 1
    assert "unknown" in _combined(failed)


def test_canonical_fish_wrapper_reaches_the_canonical_bash_implementation(
    tmp_path: Path,
) -> None:
    """Only the canonical Bash implementation owns the dual-prefix contract.

    The wrapper is driven with a conflicting hook pair while every fake tool is
    present through the retained prefix. The retained Bash implementation would
    ignore the canonical spelling and succeed; the observed exit 2 can only
    come from the canonical implementation.
    """
    paths = _install_fakes(tmp_path)
    result = _run_fish(
        CANONICAL_FISH_SCRIPT,
        paths,
        ["status"],
        extra_env={"KRONIKA_NETWORK_TEST_TAILSCALE": "/nonexistent/canonical-only"},
    )
    combined = _combined(result)
    assert result.returncode == 2, combined
    assert "KRONIKA_NETWORK_TEST_TAILSCALE" in combined
    assert "FRAMENEST_NETWORK_TEST_TAILSCALE" in combined
    assert "Conflicting environment variables " in combined
    assert paths["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_fish_wrapper_sanitizes_loader_environment(tmp_path: Path) -> None:
    paths = _install_fakes(tmp_path)
    polluted = {
        "APPIMAGE": "/tmp/Cursor.AppImage",
        "APPDIR": "/tmp/appdir",
        "ARGV0": "cursor",
        "LD_LIBRARY_PATH": "/tmp/bad-libs",
        "LD_PRELOAD": "/tmp/bad.so",
    }
    result = _run_fish(CANONICAL_FISH_SCRIPT, paths, ["status"], extra_env=polluted)
    assert result.returncode == 0, result.stderr
    env_text = paths["env_log"].read_text(encoding="utf-8")
    assert "APPIMAGE=/tmp/Cursor.AppImage" not in env_text
    assert "APPDIR=/tmp/appdir" not in env_text
    assert "ARGV0=cursor" not in env_text
    assert "LD_LIBRARY_PATH=/tmp/bad-libs" not in env_text
    assert "LD_PRELOAD=/tmp/bad.so" not in env_text
    assert "APPIMAGE=\n" in env_text


def test_canonical_fish_wrapper_requires_the_bash_implementation(
    tmp_path: Path,
) -> None:
    wrapper_dir = tmp_path / "wrapper-only"
    wrapper_dir.mkdir()
    wrapper = wrapper_dir / "kronika_mullvad_egress.fish"
    shutil.copy2(CANONICAL_FISH_SCRIPT, wrapper)
    wrapper.chmod(0o755)
    paths = _install_fakes(tmp_path / "fakes")
    result = _run_fish(wrapper, paths, ["status"])
    assert result.returncode == 127
    assert "Shared Bash implementation is missing." in result.stderr
    assert paths["argv_log"].read_text(encoding="utf-8") == ""
