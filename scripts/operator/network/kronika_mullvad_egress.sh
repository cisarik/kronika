#!/bin/bash
# Shared operator control for independent Mullvad egress.
# Host mutation remains separately authorized.
#
# Canonical implementation. The retained framenest_mullvad_egress.sh keeps
# working unchanged until the compatibility window closes.
#
# Test-hook variables use the dual-prefix contract: every hook below accepts
# the canonical KRONIKA_ prefix and the retained FRAMENEST_ prefix. An empty
# value counts as unset; when both spellings are set to different non-empty
# values the script prints only the two variable names and exits 2 without
# using either value. Hooks: NETWORK_TEST_HOOKS, NETWORK_TEST_PYTHON,
# NETWORK_TEST_TAILSCALE, NETWORK_TEST_CURL, NETWORK_TEST_MULLVAD.

set -euo pipefail

unset APPIMAGE APPDIR ARGV0 LD_LIBRARY_PATH LD_PRELOAD || true

readonly TRUSTED_PATH="/usr/sbin:/usr/bin:/sbin:/bin"
readonly MULLVAD_SUFFIX=".mullvad.ts.net"
readonly DIAGNOSTIC_URL="https://am.i.mullvad.net/json"
readonly SCRIPT_NAME="${0##*/}"

FIRST_ERROR_MSG=""
FIRST_ERROR_CODE=0

TAILSCALE_BIN=""
CURL_BIN=""
MULLVAD_BIN=""
PYTHON_BIN=""
HAVE_TAILSCALE_GET=""

resolve_identity_env() {
  local suffix="$1"
  local primary_name="KRONIKA_${suffix}"
  local compatible_name="FRAMENEST_${suffix}"
  local primary="${!primary_name-}"
  local compatible="${!compatible_name-}"
  if [[ -z "${primary}" ]]; then
    printf '%s' "${compatible}"
    return 0
  fi
  if [[ -z "${compatible}" || "${primary}" == "${compatible}" ]]; then
    printf '%s' "${primary}"
    return 0
  fi
  printf '%s\n' \
    "Conflicting environment variables ${primary_name} and ${compatible_name} are set to different values" >&2
  return 2
}

TEST_HOOKS="$(resolve_identity_env NETWORK_TEST_HOOKS)" || exit $?
TEST_PYTHON="$(resolve_identity_env NETWORK_TEST_PYTHON)" || exit $?
TEST_TAILSCALE="$(resolve_identity_env NETWORK_TEST_TAILSCALE)" || exit $?
TEST_CURL="$(resolve_identity_env NETWORK_TEST_CURL)" || exit $?
TEST_MULLVAD="$(resolve_identity_env NETWORK_TEST_MULLVAD)" || exit $?

usage() {
  printf '%s\n' \
    "Usage:" \
    "  ${SCRIPT_NAME} status" \
    "  ${SCRIPT_NAME} enable --node <verified-mullvad-dns-name>" \
    "  ${SCRIPT_NAME} disable" \
    "  ${SCRIPT_NAME} verify" \
    "  ${SCRIPT_NAME} recover" >&2
}

err() {
  printf '%s\n' "$*" >&2
}

note_error() {
  local code="$1"
  shift
  if [[ -z "${FIRST_ERROR_MSG}" ]]; then
    FIRST_ERROR_MSG="$*"
    FIRST_ERROR_CODE="${code}"
  fi
}

emit_first_error() {
  if [[ -n "${FIRST_ERROR_MSG}" ]]; then
    err "${FIRST_ERROR_MSG}"
    exit "${FIRST_ERROR_CODE}"
  fi
}

permission_denied_hint() {
  err "Permission denied while changing Tailscale preferences. A separately authorized host grant for this login is required; this script never escalates privileges."
}

is_absolute_executable() {
  local candidate="$1"
  [[ "${candidate}" == /* ]] || return 1
  [[ "${candidate}" != *..* ]] || return 1
  [[ -f "${candidate}" && -x "${candidate}" ]] || return 1
  return 0
}

resolve_override() {
  local value="${1:-}"
  if [[ -z "${value}" ]]; then
    return 1
  fi
  if ! is_absolute_executable "${value}"; then
    err "Test hook tool path is not a trusted absolute executable."
    exit 1
  fi
  printf '%s\n' "${value}"
}

resolve_from_trusted_path() {
  local name="$1"
  local found
  found="$(PATH="${TRUSTED_PATH}" command -v "${name}" || true)"
  if [[ -z "${found}" ]]; then
    return 1
  fi
  if [[ "${found}" != /* || "${found}" == *..* ]]; then
    return 1
  fi
  if [[ "${found}" == ./* || "${found}" == */./ || "${found}" == ./ ]]; then
    return 1
  fi
  printf '%s\n' "${found}"
}

resolve_required_tool() {
  local name="$1"
  local override_value="${2:-}"
  local resolved=""

  if [[ "${TEST_HOOKS}" == "1" ]]; then
    if resolved="$(resolve_override "${override_value}")"; then
      printf '%s\n' "${resolved}"
      return 0
    fi
    err "Required tool '${name}' is not provided through the test hook."
    exit 1
  fi

  if resolved="$(resolve_from_trusted_path "${name}")"; then
    printf '%s\n' "${resolved}"
    return 0
  fi
  err "Required tool '${name}' was not found in the trusted executable search path."
  exit 1
}

resolve_optional_tool() {
  local name="$1"
  local override_value="${2:-}"
  local resolved=""

  if [[ "${TEST_HOOKS}" == "1" ]]; then
    if resolved="$(resolve_override "${override_value}")"; then
      printf '%s\n' "${resolved}"
    fi
    return 0
  fi

  if resolved="$(resolve_from_trusted_path "${name}")"; then
    printf '%s\n' "${resolved}"
  fi
}

run_child() {
  local bin="$1"
  shift
  env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
    PATH="${TRUSTED_PATH}" \
    "${bin}" "$@"
}

scrub_temp_dir() {
  local dir="${1:-}"
  if [[ -n "${dir}" && -d "${dir}" ]]; then
    rm -rf "${dir}"
  fi
}

normalize_node() {
  local raw="$1"
  printf '%s' "${raw}" | PATH="${TRUSTED_PATH}" command tr '[:upper:]' '[:lower:]'
}

validate_mullvad_node() {
  local node="$1"

  if [[ -z "${node}" ]]; then
    err "enable requires --node <verified-mullvad-dns-name>."
    exit 2
  fi
  if [[ "${node}" =~ [[:space:]] ]]; then
    err "Exit-node name must not contain whitespace."
    exit 2
  fi
  if [[ "${node}" == -* ]]; then
    err "Exit-node name must not be option-like."
    exit 2
  fi
  if [[ "${node}" == *:* ]]; then
    err "Exit-node name must be an explicit Mullvad DNS hostname."
    exit 2
  fi
  if [[ "${#node}" -gt 253 ]]; then
    err "Exit-node name is not a valid DNS hostname."
    exit 2
  fi
  if [[ ! "${node}" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)*\.mullvad\.ts\.net$ ]]; then
    err "Exit-node name must be a normalized hostname ending exactly in ${MULLVAD_SUFFIX}."
    exit 2
  fi
}

ensure_python() {
  local resolved=""
  if [[ -n "${PYTHON_BIN}" ]]; then
    return 0
  fi
  if [[ "${TEST_HOOKS}" == "1" && -n "${TEST_PYTHON}" ]]; then
    PYTHON_BIN="$(resolve_override "${TEST_PYTHON}")"
    return 0
  fi
  if resolved="$(resolve_from_trusted_path python3)"; then
    PYTHON_BIN="${resolved}"
    return 0
  fi
  err "Required tool 'python3' was not found in the trusted executable search path."
  exit 1
}

ensure_tailscale() {
  if [[ -n "${TAILSCALE_BIN}" ]]; then
    return 0
  fi
  TAILSCALE_BIN="$(resolve_required_tool tailscale "${TEST_TAILSCALE}")"
}

ensure_curl() {
  if [[ -n "${CURL_BIN}" ]]; then
    return 0
  fi
  CURL_BIN="$(resolve_required_tool curl "${TEST_CURL}")"
}

detect_tailscale_get() {
  local tmpdir out err rc
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-get.XXXXXX")"
  out="${tmpdir}/out"
  err="${tmpdir}/err"
  set +e
  run_child "${TAILSCALE_BIN}" get exit-node >"${out}" 2>"${err}"
  rc=$?
  set -e
  if [[ "${rc}" -eq 0 ]]; then
    HAVE_TAILSCALE_GET="yes"
  else
    HAVE_TAILSCALE_GET="no"
  fi
  scrub_temp_dir "${tmpdir}"
  return 0
}

extract_status_fields() {
  local json_file="$1"
  local out_file="$2"
  ensure_python
  env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
    PATH="${TRUSTED_PATH}" PYTHONNOUSERSITE=1 LC_ALL=C \
    "${PYTHON_BIN}" - "${json_file}" "${out_file}" <<'PY'
import json
import sys

src, dest = sys.argv[1], sys.argv[2]
try:
    with open(src, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.stderr.write("Tailscale status JSON could not be parsed.\n")
    sys.exit(1)
if not isinstance(data, dict):
    sys.stderr.write("Tailscale status JSON is not an object.\n")
    sys.exit(1)

backend = str(data.get("BackendState") or "")
self_obj = data.get("Self") if isinstance(data.get("Self"), dict) else {}
advertises = "yes" if self_obj.get("ExitNodeOption") is True else "no"
peers = data.get("Peer") if isinstance(data.get("Peer"), dict) else {}
mullvad_available = "no"
selected_kind = "none"
selected_dns = ""

def peer_dns(peer):
    raw = str(peer.get("DNSName") or "").rstrip(".").lower()
    return raw

for peer in peers.values():
    if not isinstance(peer, dict):
        continue
    dns = peer_dns(peer)
    if dns.endswith(".mullvad.ts.net") and peer.get("ExitNodeOption") is True:
        mullvad_available = "yes"
    if peer.get("ExitNode") is True:
        if dns.endswith(".mullvad.ts.net"):
            selected_kind = "mullvad"
            selected_dns = dns
        else:
            selected_kind = "other"
            selected_dns = ""

lines = [
    "BACKEND_STATE=" + backend.replace("\n", ""),
    "SELF_ADVERTISES_EXIT=" + advertises,
    "MULLVAD_AVAILABLE=" + mullvad_available,
    "SELECTED_KIND=" + selected_kind,
    "SELECTED_MULLVAD_DNS=" + selected_dns,
]
with open(dest, "w", encoding="utf-8") as handle:
    handle.write("\n".join(lines) + "\n")
PY
}

read_status_fields() {
  local tmpdir json_file fields_file rc
  ensure_tailscale
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-status.XXXXXX")"
  json_file="${tmpdir}/status.json"
  fields_file="${tmpdir}/fields.env"
  set +e
  run_child "${TAILSCALE_BIN}" status --json >"${json_file}" 2>"${tmpdir}/err"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    if PATH="${TRUSTED_PATH}" command grep -qiE 'permission denied|access denied' "${tmpdir}/err"; then
      permission_denied_hint
    else
      err "Failed to read Tailscale status."
    fi
    scrub_temp_dir "${tmpdir}"
    return 1
  fi
  if ! extract_status_fields "${json_file}" "${fields_file}"; then
    scrub_temp_dir "${tmpdir}"
    return 1
  fi
  BACKEND_STATE=""
  SELF_ADVERTISES_EXIT=""
  MULLVAD_AVAILABLE=""
  SELECTED_KIND=""
  SELECTED_MULLVAD_DNS=""
  while IFS= read -r line || [[ -n "${line}" ]]; do
    case "${line}" in
      BACKEND_STATE=*) BACKEND_STATE="${line#BACKEND_STATE=}" ;;
      SELF_ADVERTISES_EXIT=*) SELF_ADVERTISES_EXIT="${line#SELF_ADVERTISES_EXIT=}" ;;
      MULLVAD_AVAILABLE=*) MULLVAD_AVAILABLE="${line#MULLVAD_AVAILABLE=}" ;;
      SELECTED_KIND=*) SELECTED_KIND="${line#SELECTED_KIND=}" ;;
      SELECTED_MULLVAD_DNS=*) SELECTED_MULLVAD_DNS="${line#SELECTED_MULLVAD_DNS=}" ;;
    esac
  done <"${fields_file}"
  scrub_temp_dir "${tmpdir}"
  return 0
}

classify_mullvad_cli() {
  local tmpdir out err rc
  if [[ -z "${MULLVAD_BIN}" ]]; then
    printf '%s\n' "absent"
    return 0
  fi
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-mullvad.XXXXXX")"
  out="${tmpdir}/out"
  err="${tmpdir}/err"
  set +e
  run_child "${MULLVAD_BIN}" status >"${out}" 2>"${err}"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    scrub_temp_dir "${tmpdir}"
    printf '%s\n' "ambiguous"
    return 0
  fi
  if PATH="${TRUSTED_PATH}" command grep -Eq '^[[:space:]]*Connected([[:space:]]|$)' "${out}"; then
    scrub_temp_dir "${tmpdir}"
    printf '%s\n' "connected"
    return 0
  fi
  if PATH="${TRUSTED_PATH}" command grep -Eq '^[[:space:]]*Disconnected([[:space:]]|$)' "${out}"; then
    scrub_temp_dir "${tmpdir}"
    printf '%s\n' "disconnected"
    return 0
  fi
  scrub_temp_dir "${tmpdir}"
  printf '%s\n' "ambiguous"
}

read_lan_access() {
  local tmpdir out err rc
  if [[ "${HAVE_TAILSCALE_GET}" != "yes" ]]; then
    printf '%s\n' "unavailable-without-tailscale-get"
    return 0
  fi
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-lan.XXXXXX")"
  out="${tmpdir}/out"
  err="${tmpdir}/err"
  set +e
  run_child "${TAILSCALE_BIN}" get exit-node-allow-lan-access >"${out}" 2>"${err}"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    scrub_temp_dir "${tmpdir}"
    printf '%s\n' "unavailable-without-tailscale-get"
    return 0
  fi
  local value
  value="$(PATH="${TRUSTED_PATH}" command tr -d '[:space:]' <"${out}")"
  scrub_temp_dir "${tmpdir}"
  case "${value}" in
    true|false)
      printf '%s\n' "${value}"
      ;;
    *)
      printf '%s\n' "unavailable-without-tailscale-get"
      ;;
  esac
}

read_exit_node_from_get() {
  local tmpdir out err rc
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-node.XXXXXX")"
  out="${tmpdir}/out"
  err="${tmpdir}/err"
  set +e
  run_child "${TAILSCALE_BIN}" get exit-node >"${out}" 2>"${err}"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    scrub_temp_dir "${tmpdir}"
    return 1
  fi
  PATH="${TRUSTED_PATH}" command tr -d '[:space:]' <"${out}"
  scrub_temp_dir "${tmpdir}"
}

print_status() {
  local backend selected lan mullvad_tunnel client_get
  ensure_tailscale
  MULLVAD_BIN="$(resolve_optional_tool mullvad "${TEST_MULLVAD}")"
  detect_tailscale_get
  if ! read_status_fields; then
    return 1
  fi
  backend="${BACKEND_STATE:-unknown}"
  client_get="unsupported"
  if [[ "${HAVE_TAILSCALE_GET}" == "yes" ]]; then
    client_get="supported"
  fi
  selected="none"
  if [[ "${HAVE_TAILSCALE_GET}" == "yes" ]]; then
    local from_get=""
    if from_get="$(read_exit_node_from_get)"; then
      if [[ -z "${from_get}" ]]; then
        selected="none"
      elif [[ "${from_get}" == *:* ]]; then
        selected="unsafe-non-explicit"
      elif [[ "${from_get}" == *"${MULLVAD_SUFFIX}" ]]; then
        selected="mullvad:${from_get}"
      elif [[ "${SELECTED_KIND:-none}" == "mullvad" && -n "${SELECTED_MULLVAD_DNS}" ]]; then
        selected="mullvad:${SELECTED_MULLVAD_DNS}"
      else
        selected="non-mullvad"
      fi
    else
      err "tailscale get is present but could not read the selected exit node."
      return 1
    fi
  else
    case "${SELECTED_KIND:-none}" in
      mullvad)
        selected="mullvad:${SELECTED_MULLVAD_DNS}"
        ;;
      other)
        selected="non-mullvad"
        ;;
      *)
        selected="none"
        ;;
    esac
  fi
  lan="$(read_lan_access)"
  mullvad_tunnel="$(classify_mullvad_cli)"
  printf '%s\n' \
    "backend: ${backend}" \
    "client-get: ${client_get}" \
    "exit-node: ${selected}" \
    "lan-access: ${lan}" \
    "mullvad-nodes: $([[ "${MULLVAD_AVAILABLE:-no}" == yes ]] && printf available || printf unavailable)" \
    "self-advertises-exit-node: ${SELF_ADVERTISES_EXIT:-no}" \
    "standalone-mullvad-tunnel: ${mullvad_tunnel}"
}

preflight_mutation() {
  local require_mullvad="${1:-yes}"
  ensure_tailscale
  MULLVAD_BIN="$(resolve_optional_tool mullvad "${TEST_MULLVAD}")"
  detect_tailscale_get
  if ! read_status_fields; then
    return 1
  fi
  if [[ "${BACKEND_STATE:-}" == "NeedsLogin" ]]; then
    err "Tailscale reports NeedsLogin. This script does not perform login."
    return 3
  fi
  if [[ "${BACKEND_STATE:-}" != "Running" ]]; then
    err "Tailscale backend state is not Running; refusing mutation."
    return 3
  fi
  if [[ "${SELF_ADVERTISES_EXIT:-no}" == "yes" ]]; then
    err "This device advertises itself as an exit node; refusing mutation."
    return 3
  fi
  local tunnel
  tunnel="$(classify_mullvad_cli)"
  if [[ "${tunnel}" == "connected" ]]; then
    err "A competing standalone Mullvad tunnel is active; refusing mutation."
    return 3
  fi
  if [[ "${tunnel}" == "ambiguous" ]]; then
    err "Standalone Mullvad state is ambiguous; refusing mutation."
    return 3
  fi
  if [[ "${require_mullvad}" == "yes" && "${MULLVAD_AVAILABLE:-no}" != "yes" ]]; then
    err "No Mullvad exit nodes are available; refusing mutation."
    return 3
  fi
  return 0
}

set_exit_node() {
  local node="$1"
  local tmpdir err rc
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-set.XXXXXX")"
  err="${tmpdir}/err"
  set +e
  if [[ -z "${node}" ]]; then
    run_child "${TAILSCALE_BIN}" set --exit-node= >"${tmpdir}/out" 2>"${err}"
    rc=$?
  else
    run_child "${TAILSCALE_BIN}" set --exit-node="${node}" --exit-node-allow-lan-access=false \
      >"${tmpdir}/out" 2>"${err}"
    rc=$?
  fi
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    if PATH="${TRUSTED_PATH}" command grep -qiE 'permission denied|access denied' "${err}"; then
      permission_denied_hint
    else
      err "Failed to change the selected exit node."
    fi
    scrub_temp_dir "${tmpdir}"
    return 1
  fi
  scrub_temp_dir "${tmpdir}"
  return 0
}

cmd_status() {
  print_status
}

cmd_enable() {
  local node=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --node)
        if [[ $# -lt 2 ]]; then
          err "enable requires --node <verified-mullvad-dns-name>."
          exit 2
        fi
        node="$2"
        shift 2
        ;;
      --node=*)
        node="${1#--node=}"
        shift
        ;;
      -*)
        err "Unknown flag: $1"
        usage
        exit 2
        ;;
      *)
        err "Unexpected operand: $1"
        usage
        exit 2
        ;;
    esac
  done
  if [[ -z "${node}" ]]; then
    err "enable requires --node <verified-mullvad-dns-name>."
    exit 2
  fi
  node="$(normalize_node "${node}")"
  validate_mullvad_node "${node}"
  preflight_mutation yes
  set_exit_node "${node}"
}

cmd_disable() {
  if [[ $# -gt 0 ]]; then
    err "disable accepts no operands."
    usage
    exit 2
  fi
  preflight_mutation no
  set_exit_node ""
}

cmd_recover() {
  if [[ $# -gt 0 ]]; then
    err "recover accepts no operands."
    usage
    exit 2
  fi
  ensure_tailscale
  detect_tailscale_get
  if ! read_status_fields; then
    note_error 1 "Failed to read Tailscale status."
    emit_first_error
  fi
  if [[ "${BACKEND_STATE:-}" == "NeedsLogin" ]]; then
    note_error 3 "Tailscale reports NeedsLogin. This script does not perform login."
    emit_first_error
  fi
  if ! set_exit_node ""; then
    note_error 1 "Failed to clear the selected exit node."
  fi
  if ! read_status_fields; then
    note_error 1 "Failed to read Tailscale status after recover."
  fi
  emit_first_error
}

classify_diagnostic_body() {
  local body_file="$1"
  ensure_python
  env -u APPIMAGE -u APPDIR -u ARGV0 -u LD_LIBRARY_PATH -u LD_PRELOAD \
    PATH="${TRUSTED_PATH}" PYTHONNOUSERSITE=1 LC_ALL=C \
    "${PYTHON_BIN}" - "${body_file}" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    sys.exit(2)
if not isinstance(data, dict):
    sys.exit(2)
value = data.get("mullvad_exit_ip")
if value is True:
    sys.stdout.write("mullvad\n")
    sys.exit(0)
if value is False:
    sys.stdout.write("other\n")
    sys.exit(0)
sys.exit(2)
PY
}

cmd_verify() {
  local tmpdir body code rc class
  if [[ $# -gt 0 ]]; then
    err "verify accepts no operands."
    usage
    exit 2
  fi
  ensure_curl
  tmpdir="$(PATH="${TRUSTED_PATH}" command mktemp -d "${TMPDIR:-/tmp}/fn-net-verify.XXXXXX")"
  body="${tmpdir}/body"
  set +e
  code="$(
    run_child "${CURL_BIN}" \
      --silent --show-error --max-time 10 --max-redirs 0 \
      --proto '=https' --tlsv1.2 \
      --output "${body}" --write-out '%{http_code}' \
      "${DIAGNOSTIC_URL}" 2>"${tmpdir}/err"
  )"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 || -z "${code}" ]]; then
    err "unknown"
    err "Diagnostic transport failed; egress is unknown."
    scrub_temp_dir "${tmpdir}"
    exit 1
  fi
  if [[ "${code}" != "200" ]]; then
    err "unknown"
    err "Diagnostic HTTP status could not establish egress."
    scrub_temp_dir "${tmpdir}"
    exit 1
  fi
  set +e
  class="$(classify_diagnostic_body "${body}")"
  rc=$?
  set -e
  scrub_temp_dir "${tmpdir}"
  if [[ "${rc}" -ne 0 ]]; then
    err "unknown"
    err "Diagnostic response could not be parsed."
    exit 1
  fi
  case "${class}" in
    mullvad)
      printf '%s\n' "Mullvad egress"
      ;;
    other)
      printf '%s\n' "non-Mullvad egress"
      exit 4
      ;;
    *)
      err "unknown"
      err "Diagnostic response could not be classified."
      exit 1
      ;;
  esac
}

main() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 2
  fi
  local command="$1"
  shift
  case "${command}" in
    -h|--help)
      usage
      exit 0
      ;;
    status)
      if [[ $# -gt 0 ]]; then
        err "status accepts no operands."
        usage
        exit 2
      fi
      cmd_status
      ;;
    enable)
      cmd_enable "$@"
      ;;
    disable)
      cmd_disable "$@"
      ;;
    verify)
      cmd_verify "$@"
      ;;
    recover)
      cmd_recover "$@"
      ;;
    *)
      err "Unknown subcommand: ${command}"
      usage
      exit 2
      ;;
  esac
}

main "$@"
