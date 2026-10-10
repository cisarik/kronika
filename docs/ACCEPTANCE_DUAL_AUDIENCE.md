# Dual-Audience Boundary — Cooperator Acceptance Guide

Status: repository-local acceptance-test instruction guide for logical whole
`framenest-public-published-surface-and-tailscale-workspace`.

Baseline tested: `be35922d223c49f3b140453e69b313c9086c3831`
(schema head `0033`).

Consumers: the Cooperator (Michal) and, read-only, the Orchestrator.

Retention: remains while the dual-audience boundary exists; supersede through
an explicitly authorized documentation task when a future era reopens
deployment planning (see the Deployment-Freeze Annex).

Inbound links: [README.md](../README.md),
[docs/INFOSEC.md](INFOSEC.md),
[docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task.

This guide describes repository truth only. It claims no deployed state, no
public bind, and no completed preflight. The final public-net go/no-go stays
with the separately authorized TLS/reverse-proxy preflight plus Cooperator
acceptance ([docs/INFOSEC.md](INFOSEC.md), §1).

**What changed in this whole** (commits `37da5f2..be35922`, one line per
rollout):

| Commit | Rollout |
|---|---|
| `0008ca5`+`6aac705` | ADR-0074 proposed and accepted: dual-audience public/Tailscale boundary |
| `ffef457`+`dd26782` | administrator publication became the sole publication write; unpublish added on that route |
| `95f514b` | local-only `public_published_uds` reader: separate app, read-only engine, GET-only allowlist |
| `5b99575` | contributor-scoped workspace media list + administrator contribution filter |
| `da06109` | durable ordinary-user analysis proposals (no provider execution) |
| `f59f401` | audited administrator team-alias reads |
| `bcf5ec1`→`3a21405` | independent-audit remediation: tcp loopback guard (F-2), uniform sanitized 404 + loud marker/logging failures (F-1/F-4/F-5), percent-encoded read-only URI (F-6), per-user hourly proposal rate limit (F-3) |
| `be35922` | `docs/INFOSEC.md` hardening manual + three read-only operator diagnostics |
| *(this range)* | infosec operator script polish: executable bits set; explicit `-h/--help` prints usage and exits 0 before any other logic |

The operator diagnostics below name the canonical `kronika_*` scripts. The
retained `framenest_*` entry points accept the same arguments and keep working
unchanged until the compatibility-removal window.

---

## Part A — MacBook, local only (no NUC, no Tailscale)

All commands are Fish on your MacBook, run from the repository root. You need
the prepared environment once (`./framenest setup`) if you have not run this
repo lately.

### A0 — deterministic acceptance directory

Purpose: keep every artifact of this test in one place so the two processes
share one known database and you delete one directory at the end.

```fish
# [MacBook / fish]
set -x ACC $HOME/.cache/framenest-acceptance
mkdir -p $ACC/sockets
#------------------------------------------------------
```

Expected: directory created silently. Failure: a `mkdir:` error means a stale
file occupies the path — remove `$ACC` and retry. Report: cite **A0**.

### A1 — start the workspace (trusted-loopback) server

Purpose: run the workspace composition locally on loopback with the catalog
migrated to schema head `0033`, using your acceptance database. The launcher
enforces host `127.0.0.1`, migrates to the packaged Alembic head, and waits
for `GET /health` (`DEVELOPMENT.md`, "Start And Migration").

```fish
# [MacBook / fish]
cd ~/Projects/framenest   # adjust to your checkout path
set -x FRAMENEST_DATABASE_PATH $ACC/catalog.sqlite3
./framenest start --no-open
#------------------------------------------------------
```

Expected: launcher output ends with a successful health wait; the server runs
detached (`./framenest stop` ends it later). Failure: any red traceback or
non-zero exit — paste the launcher's final lines. Report: cite **A1**.

### A2 — confirm the audience bootstrap grants the admin-grade local audience

Purpose: prove the local composition resolves to `trusted_loopback` with the
full administrator capability set, identity-absent
(`src/framenest/adapters/api/application.py:1357-1362`).

```fish
# [MacBook / fish]
curl -s http://127.0.0.1:8000/api/audience/me
#------------------------------------------------------
```

Expected JSON contains `"audience":"trusted_loopback"`, `"identity":null`,
and capabilities including `"metadata.canonical.write"`,
`"media.content.publish"`, `"analysis.propose"`.
Failure: anything else (404, empty body, different audience) — include the raw
response. Report: cite **A2**.

### A3 — workspace admin UX walk-through in the browser

Purpose: exercise, by hand, the four admin surfaces this whole shipped.

```fish
# [MacBook / fish]
open http://127.0.0.1:8000/
#------------------------------------------------------
```

Check, in order (UI anchors verified in source at the tested baseline):

1. **Publish / Unpublish**: select one catalog item in Manage media and use
   the **Publish** button (label rendered at
   `src/framenest/adapters/api/web/app.js:8349`; batch confirm
   "Publish selected" at `app.js:9235-9238`). Then **Unpublish** it again
   (per-item and batch `Unpublish` controls exist,
   `app.js:1244`,`:8369-8425`). Leave exactly one or two items **published**
   for the public-reader steps.
2. **Contributor filter**: in the administrator media list, set the
   contributor filter control (strings present in `web/index.html`; API
   support landed in `5b99575`) and observe the list narrowing.
3. **Team aliases panel**: open the **Team aliases** panel
   (`web/index.html:872`) on an item with a user alias present; expect
   proposer/owner login identifiers only behind this audited administrator
   view (`f59f401`). If you have no alias yet, skip and note "no data".
4. **Analysis proposals browser**: open the **Analysis proposals** browser
   (`web/index.html:61`,`:967`). It should show an empty or existing
   open-proposal list without errors.

Expected: every action returns visible success; no console errors.
Failure: screenshot + which numbered sub-step failed. Report: cite
**A3.1**…**A3.4**.

### A4 — create analysis proposals repeatedly (rate-limit evidence)

Purpose: see disposition B work end-to-end: duplicates are accepted up to the
per-user window limit, then honestly rejected with a sanitized 429
(`src/framenest/application/analysis_proposal.py:30`,`:77-88`; default six per
hour, mirroring the YouTube/X requester limiters).

```fish
# [MacBook / fish]
set MEDIA_ID <paste-the-published-media-id-from-A3>
for i in 1 2 3 4 5 6 7
    curl -s -o /dev/null -w "%{http_code}\n" \
        -X POST -H 'Content-Type: application/json' -H 'X-FrameNest-Request: 1' \
        -H "Origin: http://127.0.0.1:8000" \
        -d '{}' "http://127.0.0.1:8000/api/workspace/media/$MEDIA_ID/analysis-proposals"
end
curl -s -X POST -H 'Content-Type: application/json' -H 'X-FrameNest-Request: 1' \
    -H "Origin: http://127.0.0.1:8000" -d '{}' \
    "http://127.0.0.1:8000/api/workspace/media/$MEDIA_ID/analysis-proposals"
#------------------------------------------------------
```

Expected: six lines `201`, then `429` whose body is exactly
`{"error":{"code":"ANALYSIS_PROPOSAL_RATE_LIMIT","message":"Too many analysis proposals this hour."}}`
(the final command shows the body).
Failure: seven 201s would mean the limiter is not wired — include the codes.
Report: cite **A4**.

### A5 — start the public reader on its own Unix socket

Purpose: run the second, separate composition exactly as designed —
`public_published_uds`, distinct socket, same database opened read-only
(dispatch at `src/framenest/adapters/api/application.py:375-380`; UDS binding
at `src/framenest/server.py:29-30`, so no TCP port is opened).

New terminal (keep A1 running):

```fish
# [MacBook / fish]
cd ~/Projects/framenest
set -x FRAMENEST_INGRESS_MODE public_published_uds
set -x FRAMENEST_UDS_PATH $ACC/sockets/public.sock
set -x FRAMENEST_DATABASE_PATH $ACC/catalog.sqlite3
poetry run framenest-server
#------------------------------------------------------
```

Expected: server starts and binds the socket. Failure: startup refusal naming
the schema head or missing catalog means A1 did not migrate
`$ACC/catalog.sqlite3` — recheck A0/A1 ordering. Report: cite **A5**.

### A6 — public projection shows published items only

Purpose: confirm the free public origin answers with the redacted published
projection.

```fish
# [MacBook / fish]
curl --unix-socket $ACC/sockets/public.sock http://localhost/api/audience/me
curl --unix-socket $ACC/sockets/public.sock http://localhost/api/media | head -c 400
# optional page view through socat, if installed:
# socat TCP-LISTEN:8443,range=127.0.0.1/32,fork UNIX-CONNECT:$ACC/sockets/public.sock
# then open http://127.0.0.1:8443/
#------------------------------------------------------
```

Note: a plain browser cannot reach a UDS directly; the curl evidence is
sufficient, and the socat line above is an optional convenience bound to
loopback only.

Expected: `/api/audience/me` reports `"audience":"public_published"` with
capabilities exactly `["gallery.read","media.original.read"]`
(`docs/adr/0074-…boundary.md`, "Public audience"); `/api/media` lists only
what you published in A3, with no internal fields.
Failure: unpublished item visible, or extra fields — capture the JSON.
Report: cite **A6**.

### A7 — scripted surface check against the local reader

Purpose: run the repository diagnostic end-to-end. The script speaks HTTP base
URLs; against a UDS you lend it `--unix-socket` through its documented
`KRONIKA_CURL_BIN` hook (the retained `FRAMENEST_CURL_BIN` spelling is also
accepted; no repository change needed). Since the polish
commit the scripts also carry the executable bit, but the explicit `bash`
form below works everywhere regardless of mode.

```fish
# [MacBook / fish]
printf '%s\n' '#!/usr/bin/env bash' \
  'exec curl --unix-socket "$FRAMENEST_UDS_PATH" "$@"' > $ACC/curl-uds.sh
chmod +x $ACC/curl-uds.sh
env KRONIKA_PUBLIC_BASE_URL=http://localhost \
    KRONIKA_CURL_BIN=$ACC/curl-uds.sh \
    bash scripts/operator/infosec/kronika_public_surface_check.sh
#------------------------------------------------------
```

Expected output class (paste the whole table back to the Orchestrator):

```text
PROBE                                          HTTP   HEADERS   BODY      RESULT
GET /infosec-surface-probe-unlisted (reference) 404    ok        ok        PASS
GET /docs                                      404    ok        ok        PASS
GET /redoc                                     404    ok        ok        PASS
GET /openapi.json                              404    ok        ok        PASS
GET /api/admin/analysis-proposals              404    ok        ok        PASS
POST /api/media (denied route)                 404    ok        ok        PASS
GET /api/media/not-a-uuid                      404    ok        ok        PASS
RESULT: PASS — uniform sanitized 404 posture verified.
```

Failure: any row FAIL — include the table verbatim; the failing dimension
(HTTP / HEADERS MISSING / BODY DIFFERS) names the broken contract.
Report: cite **A7**.

### A8 — manual uniform-404 spot checks

Purpose: eyeball the byte-level contract yourself, including a malformed UUID
and out-of-range pagination (`d3b203f`, audit F-1; handler at
`src/framenest/adapters/api/public_published_application.py:210`).

```fish
# [MacBook / fish]
for p in "/docs" "/api/media/not-a-uuid" "/api/media?limit=9999" "/api/admin/media"
    curl -s --unix-socket $ACC/sockets/public.sock "http://localhost$p"; echo
end
#------------------------------------------------------
```

Expected: four identical bodies,
`{"error":{"code":"NOT_FOUND","message":"Not found."}}`, all status 404
(verify one with `-o /dev/null -w "%{http_code}\n"`).
Failure: any divergent body/status. Report: cite **A8**.

### A9 — socket permission gate (positive and negative control)

Purpose: see the third diagnostic pass on sockets you just created, then fail
on a deliberately bad fixture.

```fish
# [MacBook / fish]
touch $ACC/sockets/bad.sock; chmod 604 $ACC/sockets/bad.sock
env KRONIKA_SOCKET_PATHS="$ACC/sockets/public.sock:$ACC/sockets/bad.sock" \
    ./scripts/operator/infosec/kronika_socket_permissions_check.sh
#------------------------------------------------------
```

Expected: `public.sock` row PASS (type `socket`, no world bits); `bad.sock`
row FAIL (`not-a-socket`/world-access); overall exit status 1.
Failure: unexpected column values — paste the table. Report: cite **A9**.

### A10 — clean shutdown

```fish
# [MacBook / fish]
./framenest stop
rm -rf $ACC
#------------------------------------------------------
```

Expected: launcher stops its server; acceptance directory removed. (Stop the
public-reader terminal first.) Report: cite **A10** only if something fails.

---

## Part B — NUC / Tailscale verification (CONDITIONAL — read the banner)

> **Honesty banner:** Part B is valid only when the tested public `main` SHA
> equals the live `framenest-release status` active release. If they differ,
> do not run Part B; report `BLOCKED: NUC not at tested SHA`.

If, and only if, the tested public `main` SHA equals the live
`framenest-release status` active release:

- **B1 — ordinary mapped user flows over Tailscale Serve** (requires live
  identity mapping and real data — privacy reminder: use your own test items,
  never private media): sign in through your tailnet origin; verify the
  workspace list shows only your contributions (`5b99575` semantics); submit
  analysis proposals (expect 201, then 429 once the hourly window limit is
  reached); manage your own alias; confirm you cannot see another user's
  proposals or aliases.
- **B2 — administrator review inbox**: confirm companion review inbox and
  Apply behave exactly as before (this whole touched none of it); publish one
  reviewed item via the administrator route only.
- **B3 — companion extension regression**: Save from the side panel on an X
  page; expect unchanged extension-origin rules (`SECURITY.md`, companion
  paragraphs). Apply acceptance is deterministic by owner decision
  (2026-08-26). No rendered Apply entry exists for analyzed rows; an analyzed
  history click opens hosted Details rather than an Apply surface. Do not
  treat the absence of a rendered Apply control as a Part B failure.
- **B4 — log triage on the NUC** (read-only; counts only):

```text
# [NUC / bash]
env KRONIKA_LOG_UNIT=framenest.service KRONIKA_LOG_SINCE="-24h" \
    ./scripts/operator/infosec/kronika_log_triage.sh
#------------------------------------------------------
```

  Expected: counts print; `RESULT: CLEAN` unless something abnormal happened.
  Privacy: never paste raw journal lines into chat — the tool prints counts
  precisely so you do not have to. The unit filter follows the live NUC unit
  name until the C6-H host cut switches it.

Every B step: purpose = regression of shipped behavior under real identity;
failure reporting identical (step id + captured output). Marked clearly:
B1–B3 require live identity mapping and production data; B4 requires journal
read membership.

---

## Part C — explicitly NOT testable yet

These remain impossible to verify anywhere until their own wholes exist —
do not attempt them, and treat any apparent success as misconfiguration:

- Public TLS bind of the reader, HSTS/ACME baseline — preflight whole;
  checklist: [docs/INFOSEC.md](INFOSEC.md) §4.3.
- Proxy-owned rate limits, connection caps, timeouts (audit F-8/C3) —
  [docs/INFOSEC.md](INFOSEC.md) §4.4.
- VPS hosting of the public origin — ranked option 4 in ADR-0074, needs its
  own operational decision; [docs/INFOSEC.md](INFOSEC.md) §1.
- Reader/writer atomic release ordering (F-7 rule) — exercised only by the
  first post-migration release; [docs/INFOSEC.md](INFOSEC.md) §4.10.

---

## Deployment-Freeze Annex

The Cooperator has decided: **all public-net / VPS / TLS deployment planning
for this logical whole is intentionally frozen.** This annex records intent,
not capability — nothing here claims a listener, a certificate, or a host.

When a future era resumes deployment work, it MUST reconnect with the
then-current deployment truth rather than acting on this document:

1. [docs/UBUNTU_NUC_DEPLOYMENT.md](UBUNTU_NUC_DEPLOYMENT.md) — the runbook as
   it stands in that future checkout.
2. the `deploy/ubuntu/framenest-release` immutable-update contract
   ([ADR-0060](adr/0060-repeatable-immutable-nuc-release-update-contract.md)) —
   status/check/deploy discipline, pinned tooling, provenance files.
3. [docs/INFOSEC.md](INFOSEC.md) §4 — the public-bind readiness checklist,
   revalidated against the code of that era (line citations drift; claims must
   be re-derived).

The planned preflight shape, to be re-authored under its own bounded authority
when the freeze lifts:

- independent posture re-verification — the surface-check diagnostic against
  the real origin, not only fixtures;
- proxy-owned transport limits (rate limits, body caps, timeouts, concurrency
  caps — audit F-8/condition C3);
- TLS/HSTS/ACME baseline for the chosen product;
- systemd hardening suggestions review against the actual unit files;
- F-7 reader/writer atomic-release rule exercised end-to-end;
- Cooperator step-by-step operational authorization for every host mutation;
- Cooperator acceptance sign-off before DNS or any exposure.

Also part of this freeze range: infosec operator script polish — executable
bits set on all three `scripts/operator/infosec/kronika_*.sh` tools (the
retained `framenest_*.sh` spellings remain working entry points until the
compatibility-removal window), and an explicit `-h/--help` now prints usage
and exits 0 before any other logic.

---

## Report-back template

Copy, fill, and send back to the Orchestrator (screenshots optional; raw
journals/secrets never):

```text
Acceptance run — framenest-public-published-surface-and-tailscale-workspace
Tested at HEAD: <full sha from git rev-parse HEAD>
Part A: A0 [PASS/FAIL] … A10 [PASS/FAIL]   (A7 table pasted above/below)
Part B: BLOCKED: NUC not at tested SHA | B1..B4 results
Part C: acknowledged as deferred
Deviations / notes: <free text>
Failed step ids + evidence: <e.g. A7 — BODY DIFFERS on /openapi.json, table attached>
```

A single-line reply "Part A all PASS, Part B BLOCKED: NUC not at tested SHA"
is a complete blocked-NUC result; Part A plus B1..B4 results is the complete
positive result when the NUC is at the tested SHA.
