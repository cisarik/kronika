"""Behavioral contract tests for operator infosec diagnostic scripts.

Synthetic fake tools only. These tests must not read a real journal, contact a
real origin, inspect real service sockets, or invoke a real journalctl, curl or
stat. They drive the canonical `kronika_*` counterparts and the retained
`framenest_*` entry points through fixtures so the dual-prefix contract and
every refusal path are exercised without host side effects.
"""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INFOSEC_DIR = REPOSITORY_ROOT / "scripts" / "operator" / "infosec"
CANONICAL_LOG_TRIAGE = INFOSEC_DIR / "kronika_log_triage.sh"
CANONICAL_PUBLIC_SURFACE = INFOSEC_DIR / "kronika_public_surface_check.sh"
CANONICAL_SOCKET_CHECK = INFOSEC_DIR / "kronika_socket_permissions_check.sh"
RETAINED_LOG_TRIAGE = INFOSEC_DIR / "framenest_log_triage.sh"
RETAINED_PUBLIC_SURFACE = INFOSEC_DIR / "framenest_public_surface_check.sh"
RETAINED_SOCKET_CHECK = INFOSEC_DIR / "framenest_socket_permissions_check.sh"

FAKE_SECRET_A = "ksi-fixture-alpha-value"
FAKE_SECRET_B = "ksi-fixture-beta-value"
JOURNAL_LINE_MARKER = "ksi-fixture-journal-line"

COUNTED_PATTERNS = (
    "public_unexpected_failure",
    "public_request_validation_rejected",
    "public_http_exception_rejected",
    "ANALYSIS_PROPOSAL_RATE_LIMIT",
    "audit_write_failure",
)

_PUBLIC_404_BODY = '{"error":{"code":"NOT_FOUND","message":"Not found."}}'

_UNSET = object()

_DUAL_PREFIX_VARIABLES = (
    "LOG_UNIT",
    "LOG_SINCE",
    "SPIKE_THRESHOLD",
    "JOURNALCTL_BIN",
    "PUBLIC_BASE_URL",
    "CURL_BIN",
    "SURFACE_CHECK_TIMEOUT",
    "SOCKET_PATHS",
    "EXPECTED_OWNER_PATTERN",
    "STAT_BIN",
)


def _write_executable(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    for polluted in ("APPIMAGE", "APPDIR", "ARGV0", "LD_LIBRARY_PATH", "LD_PRELOAD"):
        env.pop(polluted, None)
    for name in _DUAL_PREFIX_VARIABLES:
        env.pop(f"KRONIKA_{name}", None)
        env.pop(f"FRAMENEST_{name}", None)
    return env


def _run(
    script: Path,
    env: dict[str, str],
    args: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script), *args],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def _combined(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


# --- journalctl fixture and log-triage runner ---


def _install_journalctl_fake(
    tmp_path: Path,
    *,
    lines: str = "",
    exit_code: int = 0,
    stderr: str = "",
) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    argv_log = log_dir / "journalctl.argv"
    lines_file = log_dir / "journal.lines"
    lines_file.write_text(lines, encoding="utf-8")
    fake = bin_dir / "journalctl"
    _write_executable(
        fake,
        f"""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> {argv_log}
if [[ {exit_code} -ne 0 ]]; then
  printf '%s\\n' {stderr!r} >&2
  exit {exit_code}
fi
cat {lines_file}
""",
    )
    argv_log.write_text("", encoding="utf-8")
    return {"bin": fake, "argv_log": argv_log}


def _run_log_triage(
    script: Path,
    tmp_path: Path,
    *,
    prefix: str = "KRONIKA_",
    journal_lines: str = "",
    exit_code: int = 0,
    stderr: str = "",
    extra_env: dict[str, str] | None = None,
    args: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    fake = _install_journalctl_fake(
        tmp_path, lines=journal_lines, exit_code=exit_code, stderr=stderr
    )
    env = _clean_env()
    env[f"{prefix}JOURNALCTL_BIN"] = str(fake["bin"])
    if extra_env:
        env.update(extra_env)
    return _run(script, env, args), fake


# --- curl fixture and public-surface runner ---


def _install_curl_fake(tmp_path: Path, *, mode: str = "pass") -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    argv_log = log_dir / "curl.argv"
    fake = bin_dir / "curl"
    _write_executable(
        fake,
        f"""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> {argv_log}
mode={mode!r}
if [[ "$mode" == "transport" ]]; then
  echo "synthetic transport failure" >&2
  exit 7
fi
out=""
hdr=""
url=""
code="404"
args=("$@")
i=0
while [[ $i -lt $# ]]; do
  a="${{args[$i]}}"
  case "$a" in
    -o) out="${{args[$((i+1))]}}"; i=$((i+2));;
    -D) hdr="${{args[$((i+1))]}}"; i=$((i+2));;
    -X|--max-time|-w|-H|--data) i=$((i+2));;
    -sS) i=$((i+1));;
    *) url="$a"; i=$((i+1));;
  esac
done
body={_PUBLIC_404_BODY!r}
include_no_store=1
case "$mode:$url" in
  mixed:*openapi.json) body='{{"openapi":"3.0.0"}}';;
  bad-code:*/docs) code="200";;
  missing-header:*) include_no_store=0;;
  ref-empty:*infosec-surface-probe-unlisted) body="";;
esac
if [[ -n "$out" ]]; then printf '%s' "$body" > "$out"; fi
if [[ -n "$hdr" ]]; then
  {{
    printf 'HTTP/1.1 404 Not Found\\n'
    if [[ "$include_no_store" == "1" ]]; then
      printf 'Cache-Control: no-store\\n'
    fi
    printf 'X-Content-Type-Options: nosniff\\n'
  }} > "$hdr"
fi
printf '%s' "$code"
""",
    )
    argv_log.write_text("", encoding="utf-8")
    return {"bin": fake, "argv_log": argv_log}


def _run_public_surface(
    script: Path,
    tmp_path: Path,
    *,
    prefix: str = "KRONIKA_",
    mode: str = "pass",
    extra_env: dict[str, str] | None = None,
    with_url: bool = True,
    args: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    fake = _install_curl_fake(tmp_path, mode=mode)
    env = _clean_env()
    env[f"{prefix}CURL_BIN"] = str(fake["bin"])
    if with_url:
        env[f"{prefix}PUBLIC_BASE_URL"] = "http://origin.invalid"
    if extra_env:
        env.update(extra_env)
    return _run(script, env, args), fake


# --- stat fixture and socket-check runner ---


def _install_stat_fake(tmp_path: Path, mapping: dict[str, str]) -> dict[str, Path]:
    bin_dir = tmp_path / "bin"
    log_dir = tmp_path / "logs"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)
    argv_log = log_dir / "stat.argv"
    map_file = log_dir / "stat.map"
    map_file.write_text(
        "".join(f"{path}|{info}\n" for path, info in mapping.items()),
        encoding="utf-8",
    )
    fake = bin_dir / "stat"
    _write_executable(
        fake,
        f"""#!/bin/bash
set -euo pipefail
printf '%s\\n' "$*" >> {argv_log}
target="${{@: -1}}"
line="$(grep -F -- "$target|" {map_file} | head -n 1 || true)"
if [[ -z "$line" ]]; then
  exit 1
fi
printf '%s\\n' "${{line#*|}}"
""",
    )
    argv_log.write_text("", encoding="utf-8")
    return {"bin": fake, "argv_log": argv_log}


_DEFAULT_SOCKET_MAPPING = {
    "/run/kronika/kronika.sock": "socket|660|kronika",
    "/run/kronika/kronika-public.sock": "socket|600|root",
}


def _run_socket_check(
    script: Path,
    tmp_path: Path,
    *,
    prefix: str = "KRONIKA_",
    mapping: dict[str, str] | None = None,
    socket_paths: str | None = _UNSET,  # type: ignore[assignment]
    extra_env: dict[str, str] | None = None,
    args: tuple[str, ...] = (),
) -> tuple[subprocess.CompletedProcess[str], dict[str, Path]]:
    if mapping is None:
        mapping = dict(_DEFAULT_SOCKET_MAPPING)
    fake = _install_stat_fake(tmp_path, mapping)
    env = _clean_env()
    env[f"{prefix}STAT_BIN"] = str(fake["bin"])
    if socket_paths is _UNSET:
        env[f"{prefix}SOCKET_PATHS"] = ":".join(mapping)
    elif socket_paths is not None:
        env[f"{prefix}SOCKET_PATHS"] = socket_paths
    if extra_env:
        env.update(extra_env)
    return _run(script, env, args), fake


# --- structural checks ---


def test_expected_infosec_files_exist_and_are_executable() -> None:
    for path in (
        CANONICAL_LOG_TRIAGE,
        CANONICAL_PUBLIC_SURFACE,
        CANONICAL_SOCKET_CHECK,
        RETAINED_LOG_TRIAGE,
        RETAINED_PUBLIC_SURFACE,
        RETAINED_SOCKET_CHECK,
    ):
        assert path.is_file(), path
        mode = path.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH


def test_infosec_scripts_do_not_invoke_sudo() -> None:
    for path in (
        CANONICAL_LOG_TRIAGE,
        CANONICAL_PUBLIC_SURFACE,
        CANONICAL_SOCKET_CHECK,
        RETAINED_LOG_TRIAGE,
        RETAINED_PUBLIC_SURFACE,
        RETAINED_SOCKET_CHECK,
    ):
        text = path.read_text(encoding="utf-8")
        assert re.search(r"^\s*sudo\b", text, re.MULTILINE) is None, path
        assert re.search(r"[;&|]\s*sudo\b", text) is None, path


# --- log triage ---


@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_canonical_log_triage_reads_either_identity_prefix(
    tmp_path: Path, prefix: str
) -> None:
    lines = "\n".join((*COUNTED_PATTERNS, JOURNAL_LINE_MARKER)) + "\n"
    result, fake = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        prefix=prefix,
        journal_lines=lines,
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT: CLEAN" in result.stdout
    for pattern in COUNTED_PATTERNS:
        assert pattern in result.stdout
    assert JOURNAL_LINE_MARKER not in _combined(result)
    argv = fake["argv_log"].read_text(encoding="utf-8")
    assert "-u kronika.service" in argv
    assert "--no-pager -o cat" in argv
    assert "--since" not in argv


def test_canonical_log_triage_spike_flags_exit_one(tmp_path: Path) -> None:
    lines = ("public_unexpected_failure\n" * 60)
    result, _ = _run_log_triage(
        CANONICAL_LOG_TRIAGE, tmp_path, journal_lines=lines
    )
    assert result.returncode == 1
    assert "SPIKE(>50)" in result.stdout
    assert "RESULT: FLAGGED" in result.stdout


def test_canonical_log_triage_mixes_prefixes_per_variable(tmp_path: Path) -> None:
    lines = "public_unexpected_failure\n" * 3
    result, fake = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        journal_lines=lines,
        extra_env={
            "KRONIKA_SPIKE_THRESHOLD": "2",
            "FRAMENEST_LOG_SINCE": "24h ago",
        },
    )
    assert result.returncode == 1
    assert "SPIKE(>2)" in result.stdout
    argv = fake["argv_log"].read_text(encoding="utf-8")
    assert "--since 24h ago" in argv


def test_canonical_log_triage_empty_value_is_unset(tmp_path: Path) -> None:
    result, fake = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        journal_lines="x\n",
        extra_env={"KRONIKA_LOG_UNIT": "", "FRAMENEST_LOG_UNIT": "kronika-alt.service"},
    )
    assert result.returncode == 0, result.stderr
    assert "-u kronika-alt.service" in fake["argv_log"].read_text(encoding="utf-8")


def test_canonical_log_triage_conflicting_pair_exits_two_naming_only_names(
    tmp_path: Path,
) -> None:
    result, fake = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        journal_lines="x\n",
        extra_env={
            "KRONIKA_LOG_UNIT": FAKE_SECRET_A,
            "FRAMENEST_LOG_UNIT": FAKE_SECRET_B,
        },
    )
    combined = _combined(result)
    assert result.returncode == 2, combined
    assert "KRONIKA_LOG_UNIT" in combined
    assert "FRAMENEST_LOG_UNIT" in combined
    assert FAKE_SECRET_A not in combined
    assert FAKE_SECRET_B not in combined
    assert "Conflicting environment variables " in combined
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_log_triage_refuses_invalid_threshold(tmp_path: Path) -> None:
    result, fake = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        journal_lines="x\n",
        extra_env={"KRONIKA_SPIKE_THRESHOLD": "not-a-number"},
    )
    assert result.returncode == 64
    assert "KRONIKA_SPIKE_THRESHOLD must be a non-negative integer." in result.stderr
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_log_triage_missing_journalctl_refuses(tmp_path: Path) -> None:
    env = _clean_env()
    env["KRONIKA_JOURNALCTL_BIN"] = str(tmp_path / "missing-journalctl")
    result = _run(CANONICAL_LOG_TRIAGE, env)
    assert result.returncode == 64
    assert "FAIL: journalctl binary not found" in result.stderr


def test_canonical_log_triage_tool_error_preserves_exit_one(tmp_path: Path) -> None:
    result, _ = _run_log_triage(
        CANONICAL_LOG_TRIAGE,
        tmp_path,
        exit_code=3,
        stderr="synthetic journal failure",
    )
    assert result.returncode == 1
    assert "FAIL: journalctl could not read the requested window." in result.stderr
    assert "synthetic journal failure" in result.stderr


def test_canonical_log_triage_usage_and_unknown_argument(tmp_path: Path) -> None:
    env = _clean_env()
    help_result = _run(CANONICAL_LOG_TRIAGE, env, ("--help",))
    assert help_result.returncode == 0
    assert "Usage: kronika_log_triage.sh [-h]" in help_result.stdout
    bad = _run(CANONICAL_LOG_TRIAGE, env, ("unexpected-operand",))
    assert bad.returncode == 64
    assert "Usage: kronika_log_triage.sh [-h]" in bad.stderr


def test_retained_log_triage_defaults_to_its_own_unit(tmp_path: Path) -> None:
    result, fake = _run_log_triage(
        RETAINED_LOG_TRIAGE,
        tmp_path,
        prefix="FRAMENEST_",
        journal_lines="x\n",
    )
    assert result.returncode == 0, result.stderr
    assert "-u framenest.service" in fake["argv_log"].read_text(encoding="utf-8")


# --- public surface ---


@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_canonical_public_surface_reads_either_identity_prefix(
    tmp_path: Path, prefix: str
) -> None:
    result, fake = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE, tmp_path, prefix=prefix
    )
    assert result.returncode == 0, result.stderr
    assert "Kronika public surface check: http://origin.invalid" in result.stdout
    assert "RESULT: PASS" in result.stdout
    for label in (
        "GET /docs",
        "GET /redoc",
        "GET /openapi.json",
        "GET /api/admin/analysis-proposals",
        "POST /api/media (denied route)",
        "GET /api/media/not-a-uuid",
    ):
        assert label in result.stdout
    argv = fake["argv_log"].read_text(encoding="utf-8")
    assert argv.count("origin.invalid") == 8
    assert "origin.invalid/openapi.json" in argv
    assert "origin.invalid/api/media" in argv


def test_canonical_public_surface_body_mismatch_fails(tmp_path: Path) -> None:
    result, _ = _run_public_surface(CANONICAL_PUBLIC_SURFACE, tmp_path, mode="mixed")
    assert result.returncode == 1
    assert "DIFFERS" in result.stdout
    assert "RESULT: FAIL" in result.stdout


def test_canonical_public_surface_missing_no_store_header_fails(tmp_path: Path) -> None:
    result, _ = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE, tmp_path, mode="missing-header"
    )
    assert result.returncode == 1
    assert "MISSING" in result.stdout
    assert "RESULT: FAIL" in result.stdout


def test_canonical_public_surface_http_code_mismatch_fails(tmp_path: Path) -> None:
    result, _ = _run_public_surface(CANONICAL_PUBLIC_SURFACE, tmp_path, mode="bad-code")
    assert result.returncode == 1
    docs_row = next(
        line for line in result.stdout.splitlines() if "GET /docs" in line
    )
    assert "FAIL" in docs_row
    assert "RESULT: FAIL" in result.stdout


@pytest.mark.parametrize("mode", ["ref-empty", "transport"])
def test_canonical_public_surface_reference_probe_must_be_reachable(
    tmp_path: Path, mode: str
) -> None:
    result, _ = _run_public_surface(CANONICAL_PUBLIC_SURFACE, tmp_path, mode=mode)
    assert result.returncode == 1
    assert "reference probe unreachable or empty" in result.stderr


def test_canonical_public_surface_missing_required_url_refuses(tmp_path: Path) -> None:
    result, fake = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE, tmp_path, with_url=False
    )
    assert result.returncode == 64
    assert "FAIL: KRONIKA_PUBLIC_BASE_URL is required." in result.stderr
    assert "Usage: kronika_public_surface_check.sh [-h]" in result.stderr
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_public_surface_invalid_scheme_refuses(tmp_path: Path) -> None:
    result, fake = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE,
        tmp_path,
        extra_env={"KRONIKA_PUBLIC_BASE_URL": "ftp://origin.invalid"},
    )
    assert result.returncode == 64
    assert (
        "FAIL: KRONIKA_PUBLIC_BASE_URL must start with http:// or https://."
        in result.stderr
    )
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_public_surface_missing_curl_refuses(tmp_path: Path) -> None:
    env = _clean_env()
    env["KRONIKA_PUBLIC_BASE_URL"] = "http://origin.invalid"
    env["KRONIKA_CURL_BIN"] = str(tmp_path / "missing-curl")
    result = _run(CANONICAL_PUBLIC_SURFACE, env)
    assert result.returncode == 64
    assert "FAIL: curl binary not found" in result.stderr


def test_canonical_public_surface_empty_value_is_unset(tmp_path: Path) -> None:
    result, _ = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE,
        tmp_path,
        extra_env={
            "KRONIKA_PUBLIC_BASE_URL": "",
            "FRAMENEST_PUBLIC_BASE_URL": "http://origin.invalid",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT: PASS" in result.stdout


def test_canonical_public_surface_conflicting_pair_exits_two_naming_only_names(
    tmp_path: Path,
) -> None:
    result, fake = _run_public_surface(
        CANONICAL_PUBLIC_SURFACE,
        tmp_path,
        extra_env={
            "KRONIKA_PUBLIC_BASE_URL": FAKE_SECRET_A,
            "FRAMENEST_PUBLIC_BASE_URL": FAKE_SECRET_B,
        },
    )
    combined = _combined(result)
    assert result.returncode == 2, combined
    assert "KRONIKA_PUBLIC_BASE_URL" in combined
    assert "FRAMENEST_PUBLIC_BASE_URL" in combined
    assert FAKE_SECRET_A not in combined
    assert FAKE_SECRET_B not in combined
    assert "Conflicting environment variables " in combined
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_public_surface_help_prints_canonical_usage(tmp_path: Path) -> None:
    env = _clean_env()
    result = _run(CANONICAL_PUBLIC_SURFACE, env, ("--help",))
    assert result.returncode == 0
    assert "Usage: kronika_public_surface_check.sh [-h]" in result.stdout


def test_retained_public_surface_reads_its_own_variables(tmp_path: Path) -> None:
    result, fake = _run_public_surface(
        RETAINED_PUBLIC_SURFACE, tmp_path, prefix="FRAMENEST_"
    )
    assert result.returncode == 0, result.stderr
    assert "FrameNest public surface check: http://origin.invalid" in result.stdout
    assert fake["argv_log"].read_text(encoding="utf-8") != ""


# --- socket permissions ---


@pytest.mark.parametrize("prefix", ["KRONIKA_", "FRAMENEST_"])
def test_canonical_socket_check_reads_either_identity_prefix(
    tmp_path: Path, prefix: str
) -> None:
    result, _ = _run_socket_check(CANONICAL_SOCKET_CHECK, tmp_path, prefix=prefix)
    assert result.returncode == 0, result.stderr
    assert "RESULT: PASS" in result.stdout
    assert "socket" in result.stdout
    assert "kronika" in result.stdout


def test_canonical_socket_check_uses_canonical_default_paths(tmp_path: Path) -> None:
    result, fake = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        socket_paths=None,
    )
    assert result.returncode == 0, result.stderr
    argv = fake["argv_log"].read_text(encoding="utf-8")
    assert "/run/kronika/kronika.sock" in argv
    assert "/run/kronika/kronika-public.sock" in argv


def test_canonical_socket_check_fails_on_world_access(tmp_path: Path) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "socket|666|kronika"},
    )
    assert result.returncode == 1
    assert "FAIL(world-access)" in result.stdout


def test_canonical_socket_check_fails_on_non_socket(tmp_path: Path) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "regular file|600|kronika"},
    )
    assert result.returncode == 1
    assert "FAIL(not-a-socket)" in result.stdout


def test_canonical_socket_check_fails_on_owner_mismatch(tmp_path: Path) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "socket|660|other-user"},
    )
    assert result.returncode == 1
    assert "FAIL(owner=other-user)" in result.stdout


def test_canonical_socket_check_default_owner_pattern_rejects_retired_account(
    tmp_path: Path,
) -> None:
    """The canonical default accepts root and kronika only."""
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "socket|660|framenest"},
    )
    assert result.returncode == 1
    assert "FAIL(owner=framenest)" in result.stdout


def test_canonical_socket_check_fails_on_missing_path(tmp_path: Path) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika-public.sock": "socket|600|root"},
        socket_paths="/run/kronika/kronika.sock:/run/kronika/kronika-public.sock",
    )
    assert result.returncode == 1
    assert "FAIL(missing)" in result.stdout


def test_canonical_socket_check_accepts_custom_owner_pattern(tmp_path: Path) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "socket|660|operator-user"},
        extra_env={"KRONIKA_EXPECTED_OWNER_PATTERN": "^operator-user$"},
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT: PASS" in result.stdout


def test_canonical_socket_check_invalid_owner_pattern_fails_closed(
    tmp_path: Path,
) -> None:
    result, _ = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        mapping={"/run/kronika/kronika.sock": "socket|660|kronika"},
        extra_env={"KRONIKA_EXPECTED_OWNER_PATTERN": "("},
    )
    assert result.returncode == 1
    assert "RESULT: FAIL" in result.stdout


def test_canonical_socket_check_empty_value_is_unset(tmp_path: Path) -> None:
    result, fake = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        socket_paths=None,
        extra_env={
            "KRONIKA_SOCKET_PATHS": "",
            "FRAMENEST_SOCKET_PATHS": "/run/kronika/kronika.sock",
        },
    )
    assert result.returncode == 0, result.stderr
    argv = fake["argv_log"].read_text(encoding="utf-8")
    assert "/run/kronika/kronika.sock" in argv
    assert "kronika-public.sock" not in argv


def test_canonical_socket_check_conflicting_pair_exits_two_naming_only_names(
    tmp_path: Path,
) -> None:
    result, fake = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        extra_env={
            "KRONIKA_SOCKET_PATHS": FAKE_SECRET_A,
            "FRAMENEST_SOCKET_PATHS": FAKE_SECRET_B,
        },
    )
    combined = _combined(result)
    assert result.returncode == 2, combined
    assert "KRONIKA_SOCKET_PATHS" in combined
    assert "FRAMENEST_SOCKET_PATHS" in combined
    assert FAKE_SECRET_A not in combined
    assert FAKE_SECRET_B not in combined
    assert "Conflicting environment variables " in combined
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_socket_check_refuses_empty_path_list(tmp_path: Path) -> None:
    result, fake = _run_socket_check(
        CANONICAL_SOCKET_CHECK,
        tmp_path,
        socket_paths="::",
    )
    assert result.returncode == 64
    assert "FAIL: KRONIKA_SOCKET_PATHS resolved to no paths." in result.stderr
    assert fake["argv_log"].read_text(encoding="utf-8") == ""


def test_canonical_socket_check_missing_stat_refuses(tmp_path: Path) -> None:
    env = _clean_env()
    env["KRONIKA_STAT_BIN"] = str(tmp_path / "missing-stat")
    result = _run(CANONICAL_SOCKET_CHECK, env)
    assert result.returncode == 64
    assert "FAIL: stat binary not found" in result.stderr


def test_canonical_socket_check_help_prints_canonical_usage(tmp_path: Path) -> None:
    env = _clean_env()
    result = _run(CANONICAL_SOCKET_CHECK, env, ("--help",))
    assert result.returncode == 0
    assert "Usage: kronika_socket_permissions_check.sh [-h]" in result.stdout
    assert "^(root|kronika)$" in result.stdout


def test_retained_socket_check_reads_its_own_variables(tmp_path: Path) -> None:
    result, fake = _run_socket_check(
        RETAINED_SOCKET_CHECK,
        tmp_path,
        prefix="FRAMENEST_",
        mapping={"/run/framenest/framenest.sock": "socket|660|framenest"},
    )
    assert result.returncode == 0, result.stderr
    assert "RESULT: PASS" in result.stdout
    assert "/run/framenest/framenest.sock" in fake["argv_log"].read_text(
        encoding="utf-8"
    )
