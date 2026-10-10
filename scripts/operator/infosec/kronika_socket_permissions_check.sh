#!/usr/bin/env bash
# kronika_socket_permissions_check.sh — read-only UDS permission gate.
#
# Stats the configured Kronika Unix domain sockets and fails when any socket
# is missing, is not a socket file, is world-readable or world-writable, or has
# an owner outside the expected user pattern. Prints one table row per socket.
#
# Read-only: stat only. No chmod, no chown, no service interaction, no writes
# outside stdout.
#
# Environment variables (dual-prefix contract):
#   KRONIKA_SOCKET_PATHS              Optional. Colon-separated socket paths.
#                                     Default placeholders:
#                                     /run/kronika/kronika.sock:/run/kronika/kronika-public.sock
#   KRONIKA_EXPECTED_OWNER_PATTERN    Optional. Extended regex the owner
#                                     user must match. Default: ^(root|kronika)$
#   KRONIKA_STAT_BIN                  Optional. GNU stat binary. Default: stat.
#
# Every variable also accepts the retained FRAMENEST_ spelling until the
# compatibility window closes. An empty value counts as unset. When both
# spellings are set to different non-empty values, the script prints only the
# two variable names and exits 2 without using either value.
#
# Usage:
#   kronika_socket_permissions_check.sh [-h]
#
# Exit status: 0 all sockets conform; 1 any check failed; 64 invalid input.

set -euo pipefail
export LC_ALL=C

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

DEFAULT_OWNER_PATTERN='^(root|kronika)$'
SOCKET_PATHS="$(resolve_identity_env SOCKET_PATHS)" || exit $?
SOCKET_PATHS="${SOCKET_PATHS:-/run/kronika/kronika.sock:/run/kronika/kronika-public.sock}"
OWNER_PATTERN="$(resolve_identity_env EXPECTED_OWNER_PATTERN)" || exit $?
OWNER_PATTERN="${OWNER_PATTERN:-$DEFAULT_OWNER_PATTERN}"
STAT_BIN="$(resolve_identity_env STAT_BIN)" || exit $?
STAT_BIN="${STAT_BIN:-stat}"

usage() {
    cat <<'USAGE'
Usage: kronika_socket_permissions_check.sh [-h]

Verifies Kronika UDS paths exist, are sockets, carry no world-read/world-
write bits, and have an owner matching the expected pattern.

Optional environment (canonical spelling; the retained FRAMENEST_ spelling is
also accepted until the compatibility window closes):
  KRONIKA_SOCKET_PATHS=<colon-separated paths>
  KRONIKA_EXPECTED_OWNER_PATTERN=<extended regex>   (default: ^(root|kronika)$)
  KRONIKA_STAT_BIN=<stat binary>                    (default: stat)

Exit status: 0 conform, 1 fail, 64 invalid input.
USAGE
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "") ;;
    *) usage >&2; exit 64 ;;
esac

command -v "$STAT_BIN" >/dev/null 2>&1 || {
    echo "FAIL: stat binary not found: $STAT_BIN" >&2
    exit 64
}

if [[ -z "$SOCKET_PATHS" ]]; then
    echo "FAIL: KRONIKA_SOCKET_PATHS is empty." >&2
    exit 64
fi

IFS=':' read -r -a PATHS <<<"$SOCKET_PATHS"

printf '%-45s %-10s %-6s %-14s %s\n' "SOCKET" "TYPE" "MODE" "OWNER" "RESULT"

OVERALL="PASS"
CHECKED=0
for path in "${PATHS[@]}"; do
    if [[ -z "$path" ]]; then
        continue
    fi
    CHECKED=$((CHECKED + 1))
    if ! info="$("$STAT_BIN" -c '%F|%a|%U' -- "$path" 2>/dev/null)"; then
        printf '%-45s %-10s %-6s %-14s %s\n' "$path" "-" "-" "-" "FAIL(missing)"
        OVERALL="FAIL"
        continue
    fi
    type="${info%%|*}"
    rest="${info#*|}"
    mode="${rest%%|*}"
    owner="${rest#*|}"
    result="PASS"

    if [[ "$type" != "socket" ]]; then
        case "$result" in
            PASS) result="FAIL(not-a-socket)" ;;
            *) result="FAIL(multiple)" ;;
        esac
    fi
    world_bits=$(( (8#$mode & 8#004) | (8#$mode & 8#002) ))
    if (( world_bits != 0 )); then
        case "$result" in
            PASS) result="FAIL(world-access)" ;;
            *) result="FAIL(multiple)" ;;
        esac
    fi
    # An invalid ERE fails the match and therefore fails closed below.
    if ! [[ "$owner" =~ $OWNER_PATTERN ]]; then
        case "$result" in
            PASS) result="FAIL(owner=$owner)" ;;
            *) result="FAIL(multiple)" ;;
        esac
    fi
    if [[ "$result" != "PASS" ]]; then
        OVERALL="FAIL"
    fi
    printf '%-45s %-10s %-6s %-14s %s\n' "$path" "$type" "$mode" "$owner" "$result"
done

if (( CHECKED == 0 )); then
    echo "FAIL: KRONIKA_SOCKET_PATHS resolved to no paths." >&2
    exit 64
fi

if [[ "$OVERALL" == "PASS" ]]; then
    echo "RESULT: PASS — all configured sockets conform."
    exit 0
fi
echo "RESULT: FAIL — at least one socket violates expectations."
exit 1
