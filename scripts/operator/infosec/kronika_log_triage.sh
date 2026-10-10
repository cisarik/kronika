#!/usr/bin/env bash
# kronika_log_triage.sh — read-only security log triage summary.
#
# Counts security-relevant structured-log event keys and audit markers in a
# bounded journalctl window and flags spikes above a configurable threshold.
# Prints counts and pattern keys only — never matched log lines, paths,
# identities, or any captured payload text.
#
# Counted patterns:
#   public_unexpected_failure          application structured-log events (F-5)
#   public_request_validation_rejected application structured-log events (F-1)
#   public_http_exception_rejected     application structured-log events (F-5)
#   ANALYSIS_PROPOSAL_RATE_LIMIT       429 code occurrences where downstream
#                                      logging includes response bodies (the
#                                      Kronika journal itself does not; proxy
#                                      logs piped to journald may)
#   audit_write_failure                audit-recorder write failures
#
# Read-only: runs journalctl without elevation; the invoking user needs the
# usual journal read membership. No sudo, no writes outside its own temp dir.
#
# Environment variables (dual-prefix contract):
#   KRONIKA_LOG_UNIT            Optional. systemd unit name filter.
#                               Default: kronika.service.
#   KRONIKA_LOG_SINCE           Optional. Passed to journalctl --since.
#   KRONIKA_SPIKE_THRESHOLD     Optional. Flag count above this. Default: 50.
#   KRONIKA_JOURNALCTL_BIN      Optional. Default: journalctl.
#
# Every variable also accepts the retained FRAMENEST_ spelling until the
# compatibility window closes. An empty value counts as unset. When both
# spellings are set to different non-empty values, the script prints only the
# two variable names and exits 2 without using either value.
#
# Usage:
#   kronika_log_triage.sh [-h]
#
# Exit status: 0 triage ran with no spike flag; 1 tool error or any spike
# flagged.

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

UNIT="$(resolve_identity_env LOG_UNIT)" || exit $?
UNIT="${UNIT:-kronika.service}"
SINCE="$(resolve_identity_env LOG_SINCE)" || exit $?
THRESHOLD="$(resolve_identity_env SPIKE_THRESHOLD)" || exit $?
THRESHOLD="${THRESHOLD:-50}"
JOURNALCTL_BIN="$(resolve_identity_env JOURNALCTL_BIN)" || exit $?
JOURNALCTL_BIN="${JOURNALCTL_BIN:-journalctl}"

usage() {
    cat <<'USAGE'
Usage: kronika_log_triage.sh [-h]

Summarizes security-relevant Kronika log-event and audit-marker counts from
journalctl for one unit and optional time window. Prints counts only.

Optional environment (canonical spelling; the retained FRAMENEST_ spelling is
also accepted until the compatibility window closes):
  KRONIKA_LOG_UNIT=<unit>              (default: kronika.service)
  KRONIKA_LOG_SINCE=<journalctl --since value>
  KRONIKA_SPIKE_THRESHOLD=<count>      (default: 50)
  KRONIKA_JOURNALCTL_BIN=<binary>      (default: journalctl)

Exit status: 0 clean window, 1 tool error or spike flagged.
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

command -v "$JOURNALCTL_BIN" >/dev/null 2>&1 || {
    echo "FAIL: journalctl binary not found: $JOURNALCTL_BIN" >&2
    exit 64
}

case "$THRESHOLD" in
    ''|*[!0-9]*)
        echo "FAIL: KRONIKA_SPIKE_THRESHOLD must be a non-negative integer." >&2
        exit 64
        ;;
esac

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

ARGS=(-u "$UNIT" --no-pager -o cat)
if [[ -n "$SINCE" ]]; then
    ARGS+=(--since "$SINCE")
fi

if ! "$JOURNALCTL_BIN" "${ARGS[@]}" >"$TMP_DIR/journal.txt" 2>"$TMP_DIR/err.txt"; then
    echo "FAIL: journalctl could not read the requested window." >&2
    cat "$TMP_DIR/err.txt" >&2
    exit 1
fi

PATTERNS=(
    public_unexpected_failure
    public_request_validation_rejected
    public_http_exception_rejected
    ANALYSIS_PROPOSAL_RATE_LIMIT
    audit_write_failure
)

SPIKES=0
printf '%-40s %10s %s\n' "PATTERN" "COUNT" "FLAG"
for pattern in "${PATTERNS[@]}"; do
    count="$(grep -cF -- "$pattern" "$TMP_DIR/journal.txt" || true)"
    flag=""
    if (( count > THRESHOLD )); then
        flag="SPIKE(>${THRESHOLD})"
        SPIKES=$((SPIKES + 1))
    fi
    printf '%-40s %10s %s\n' "$pattern" "$count" "$flag"
done

if (( SPIKES > 0 )); then
    echo "RESULT: FLAGGED — ${SPIKES} pattern(s) exceeded the threshold."
    exit 1
fi
echo "RESULT: CLEAN — all counted patterns within threshold."
exit 0
