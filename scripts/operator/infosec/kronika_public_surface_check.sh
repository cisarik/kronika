#!/usr/bin/env bash
# kronika_public_surface_check.sh — read-only public posture verification.
#
# Verifies that a deployed Kronika public origin answers every sensitive or
# malformed probe with the identical uniform sanitized 404 envelope and keeps
# no-store/nosniff headers. Probes cover interactive docs, OpenAPI, an
# administrator path, a POST to a denied route (the public composition refuses
# all unsafe methods before routing, so this cannot mutate anything), and a
# malformed UUID path parameter compared byte-for-byte against an unlisted-path
# reference.
#
# This tool performs strictly unauthenticated read-only probes. It is source
# material and grants no operational authority by itself.
#
# Environment variables (dual-prefix contract):
#   KRONIKA_PUBLIC_BASE_URL        Required. Origin base URL (http/https).
#   KRONIKA_CURL_BIN               Optional. Default: curl.
#   KRONIKA_SURFACE_CHECK_TIMEOUT  Optional. Per-request timeout seconds.
#                                  Default: 10.
#
# Every variable also accepts the retained FRAMENEST_ spelling until the
# compatibility window closes. An empty value counts as unset. When both
# spellings are set to different non-empty values, the script prints only the
# two variable names and exits 2 without using either value.
#
# Usage:
#   kronika_public_surface_check.sh [-h]
#
# Exit status: 0 all checks passed; 1 any check failed or input invalid.

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

BASE_URL="$(resolve_identity_env PUBLIC_BASE_URL)" || exit $?
CURL_BIN="$(resolve_identity_env CURL_BIN)" || exit $?
CURL_BIN="${CURL_BIN:-curl}"
TIMEOUT="$(resolve_identity_env SURFACE_CHECK_TIMEOUT)" || exit $?
TIMEOUT="${TIMEOUT:-10}"

usage() {
    cat <<'USAGE'
Usage: kronika_public_surface_check.sh [-h]

Verifies the Kronika public published surface answers sensitive, admin,
unsafe-method, and malformed-UUID probes with one byte-identical sanitized
404 envelope carrying Cache-Control: no-store and X-Content-Type-Options:
nosniff.

Required environment (canonical spelling; the retained FRAMENEST_ spelling is
also accepted until the compatibility window closes):
  KRONIKA_PUBLIC_BASE_URL=<origin base url>

Optional environment:
  KRONIKA_CURL_BIN=<curl-compatible binary>      (default: curl)
  KRONIKA_SURFACE_CHECK_TIMEOUT=<seconds>        (default: 10)

Exit status: 0 pass, 1 fail.
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

if [[ -z "$BASE_URL" ]]; then
    echo "FAIL: KRONIKA_PUBLIC_BASE_URL is required." >&2
    usage >&2
    exit 64
fi

BASE_URL="${BASE_URL%/}"
case "$BASE_URL" in
    http://*|https://*) ;;
    *)
        echo "FAIL: KRONIKA_PUBLIC_BASE_URL must start with http:// or https://." >&2
        exit 64
        ;;
esac

command -v "$CURL_BIN" >/dev/null 2>&1 || {
    echo "FAIL: curl binary not found: $CURL_BIN" >&2
    exit 64
}

TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

REFERENCE_PATH="/infosec-surface-probe-unlisted"

fetch() {
    # fetch <method> <path> <body-file-out> <headers-file-out>
    local method="$1" path="$2" body_out="$3" headers_out="$4"
    local args=(--max-time "$TIMEOUT" -sS -o "$body_out" -D "$headers_out"
                -X "$method" -w '%{http_code}')
    if [[ "$method" == "POST" ]]; then
        args+=(-H 'Content-Type: application/json' --data '{}')
    fi
    "$CURL_BIN" "${args[@]}" "${BASE_URL}${path}" 2>"$TMP_DIR/curl.err"
}

header_has() {
    # header_has <headers-file> <lowercase-substring>
    grep -qi -- "$2" "$1"
}

print_row() {
    printf '%-46s %-6s %-9s %-9s %s\n' "$1" "$2" "$3" "$4" "$5"
}

echo "Kronika public surface check: $BASE_URL"
printf '%-46s %-6s %-9s %-9s %s\n' "PROBE" "HTTP" "HEADERS" "BODY" "RESULT"

REFERENCE_BODY="$TMP_DIR/reference.body"
REFERENCE_HEADERS="$TMP_DIR/reference.headers"
REF_CODE="$(fetch GET "$REFERENCE_PATH" "$REFERENCE_BODY" "$REFERENCE_HEADERS")" ||
    REF_CODE="ERR"

OVERALL="PASS"
check_probe() {
    # check_probe <label> <method> <path>
    local label="$1" method="$2" path="$3"
    local body="$TMP_DIR/probe.body" headers="$TMP_DIR/probe.headers"
    local code result headers_ok body_ok
    if ! code="$(fetch "$method" "$path" "$body" "$headers")"; then
        code="ERR"
    fi
    headers_ok="ok"
    body_ok="ok"
    result="PASS"
    if [[ "$code" != "404" ]]; then
        result="FAIL"
    fi
    if ! header_has "$headers" 'cache-control: *no-store'; then
        headers_ok="MISSING"
        result="FAIL"
    fi
    if ! header_has "$headers" 'x-content-type-options: *nosniff'; then
        headers_ok="MISSING"
        result="FAIL"
    fi
    if [[ -s "$REFERENCE_BODY" ]] && ! cmp -s "$body" "$REFERENCE_BODY"; then
        body_ok="DIFFERS"
        result="FAIL"
    fi
    [[ "$result" == "PASS" ]] || OVERALL="FAIL"
    print_row "$label" "$code" "$headers_ok" "$body_ok" "$result"
}

if [[ "$REF_CODE" == "200" || "$REF_CODE" == "404" ]] && [[ -s "$REFERENCE_BODY" ]]; then
    :
else
    echo "FAIL: reference probe unreachable or empty (code=$REF_CODE)." >&2
    exit 1
fi

check_probe "GET $REFERENCE_PATH (reference)" GET "$REFERENCE_PATH"
check_probe "GET /docs" GET "/docs"
check_probe "GET /redoc" GET "/redoc"
check_probe "GET /openapi.json" GET "/openapi.json"
check_probe "GET /api/admin/analysis-proposals" GET "/api/admin/analysis-proposals"
check_probe "POST /api/media (denied route)" POST "/api/media"
check_probe "GET /api/media/not-a-uuid" GET "/api/media/not-a-uuid"

if [[ "$OVERALL" == "PASS" ]]; then
    echo "RESULT: PASS — uniform sanitized 404 posture verified."
    exit 0
fi
echo "RESULT: FAIL — public surface deviates from the uniform 404 contract."
exit 1
