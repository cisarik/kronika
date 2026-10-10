# FrameNest INFOSEC Hardening Manual

Status: repository-local security engineering guidance.

Audience: the Cooperator and the future, separately authorized TLS/reverse-
proxy preflight whole.

## 1. Scope and honesty banner

This document describes **repository truth only**. Every code claim below is
anchored to a file and line in this checkout
(`3a21405e08ff30a840afe655e702d931e833acf2`). Nothing here claims a deployed
VPS, NUC state, DNS name, TLS certificate, firewall rule, or completed
preflight. FrameNest today serves development-test workspace access over Tailscale Serve
only; the public published composition exists in source and contract tests and
is not exposed externally ([SECURITY.md](../SECURITY.md), "Dual-audience public trust
boundary").

The final public-net go/no-go decision belongs to:

1. the separately authorized TLS/reverse-proxy deployment preflight whole
   ([ADR-0074](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md),
   "Phased rollout" step 3), then
2. explicit Cooperator acceptance.

Treat this manual as input to that whole, never as its replacement or as
deployment authority.

## 2. Audit record — independent security audit, 2026-08-25

An independent adversarial audit of the dual-audience boundary (Meta report
`04/00…/10_report_00.md`, baseline `f59f4018eb86dfb40d339458d1d50dc208edcdd3`)
returned **yes-with-conditions**: no Critical or High findings; two conditions
were fix-before-public-bind, plus ride-along hardenings. Remediation landed on
`feat/x-meme-browser-companion` as commits `bcf5ec1` (F-2), `d3b203f`
(F-1, F-4, F-5), `4b7b87e` (F-6), and `3a21405` (F-3 disposition B).

| Finding | Severity | One-line description | Closure status |
|---|---|---|---|
| F-1 | Low | Public composition emitted stock FastAPI 422 bodies reflecting attacker input, breaking the uniform sanitized-404 contract | **Closed** (`d3b203f`) — `RequestValidationError` handler returns the uniform envelope (`src/framenest/adapters/api/public_published_application.py:210`) |
| F-2 | Medium | tcp ingress mode accepted binding the full unauthenticated workspace application to any IP address | **Closed** (`bcf5ec1`) — fail-closed loopback guard (`src/framenest/configuration.py:449`) |
| F-3 | Low | Analysis proposals grew unboundedly by design (no dedupe, cap, or resolution route) | **Closed** via Cooperator disposition B — per-user hourly submit rate limit (`src/framenest/application/analysis_proposal.py:30`,`:77`) |
| F-4 | Info | Companion-marker removal in served HTML was fragile silent string surgery | **Closed** (`d3b203f`) — startup-time verification plus serve-time loud failure (`public_published_application.py:94-105`, `public_published_api.py:181-192`) |
| F-5 | Low | Public error paths discarded exceptions with zero server-side signal | **Closed** (`d3b203f`) — sanitized structured-log emits before every uniform failure response (`public_published_application.py:215,230,247`; `public_published_api.py:529-537`) |
| F-6 | Info | Read-only SQLite URI composed without percent-encoding reserved path characters | **Closed** (`4b7b87e`) — `quote(..., safe="/")` before `file:…?mode=ro` (`src/framenest/infrastructure/persistence/engine.py:65-66`) |
| F-7 | Info | Reader startup pins exact schema revision `0033`; writer migrations strand older readers | **Recorded rule** (no code change intended) — reader/writer releases must ship atomically; see §4.10 |
| F-8 | Low | No transport-level abuse resistance inside the ASGI layer (connection economics, slow clients) | **Deferred by design** — owned by the reverse proxy; see §4.4 |
| F-9 | — | The audit's condition C3 cites "Finding F-9" for proxy-owned transport limits while the findings table numbers that content **F-8** | **Numbering note:** F-8 and the C3 citation denote the same item; no separate F-9 exists |

Verified-claims inventory (audit could not break): allowlist-not-hide public
composition; uniform sanitized 404 across unlisted routes/methods; Tailscale
headers powerless on the public composition; publication-gate sole-writer
integrity; readiness cannot bypass publication; capability model without
escalation; alias caller-privacy; read-only engine genuinely read-only;
fail-closed startup; frontend fail-closed bootstrap; CSRF/mutation-proof
workspace posture; audit-before-execute; additive migration `0033`. The full
attempt-by-attempt list lives in the audit report; this manual's checklist
re-verifies the load-bearing subset below.

## 3. Threat model digest

Trust boundaries, from most to least trusted:

| Boundary | Composition | Identity source | Write authority |
|---|---|---|---|
| Trusted loopback | full app, plain `FastAPI()` when ingress disabled (`src/framenest/adapters/api/application.py:1204-1205`) | physical host access | full |
| Tailscale workspace | full app behind `TailscaleIngressMiddleware` (`application.py:1286-1295`), mounted only for `tailscale_uds` | identity-mapped headers + mutation proof | catalog/media via policy gates |
| Public origin | separate `public_published_uds` app (`application.py:375-380`), GET-only allowlist | none — identity-absent | none (read-only engine) |

Principal assets: the private media library and its filesystem layout, the
catalog database (including unpublished rows and per-user aliases), provider
credentials, and administrator identity mapping.

Top abuse vectors for the public surface and their current structural answers:

- **Unauthenticated reads / unpublished leakage.** Every read rechecks durable
  publication truth; unknown, unpublished, malformed, and unlisted requests all
  receive one byte-identical sanitized 404
  (`public_published_application.py:189-192`, `public_published_api.py:159-166`;
  re-checks at `public_published_api.py:474-482`).
- **Range/stream economics.** The range parser accepts a single bounded range,
  clamps ends, and rejects unsatisfiable requests
  (`src/framenest/adapters/api/media_content_api.py:297-341`); connection
  duration and bandwidth themselves remain proxy-owned (§4.4).
- **Credential-free fingerprinting.** Interactive docs are disabled and the
  route inventory is exact (`public_published_application.py:173-179`,
  `:187-192`); validation failures no longer fingerprint the framework (F-1).
- **Header spoofing.** `proxy_headers=False` and an empty
  `forwarded_allow_ips` mean Uvicorn ignores forwarded headers
  (`src/framenest/server.py:38-39`); `Tailscale-*` headers are consulted only
  inside the workspace middleware, which the public module graph never imports.
- **Alias/attribution leakage.** Alias routes do not exist on the public
  composition; projections exclude internal fields (contract-enforced redaction
  in `tests/contract/test_public_published_uds.py`).

## 4. Public-bind readiness checklist

Work top to bottom. Do not skip items; do not reorder application conditions
behind proxy work. Items marked **[app]** are already satisfied in this
checkout and listed for verification; everything else is **[preflight]** work
for the authorized deployment whole.

### 4.1 [app] Fix-before-public-bind audit conditions closed

- [x] C1/F-1: no reachable stock 422 on the public composition
      (`public_published_application.py:210-220`).
- [x] C2/F-2: settings refuse non-loopback tcp hosts fail-closed, with no
      override hatch (`configuration.py:449-451`, message constant at `:75`).
- [x] Ride-alongs F-4/F-5/F-6 closed (§2 table).
- Verification tool: `scripts/operator/infosec/kronika_public_surface_check.sh`
  against the staged origin.

### 4.2 [app] Public posture facts to re-verify at deploy time

- [ ] Exactly eleven GET/HEAD routes answer; everything else is the uniform 404
      (`public_published_application.py:187-192`; inventory:
      `public_published_api.py`, eleven `@router.get` registrations).
- [ ] Unsafe methods refused before routing
      (`public_published_application.py:60`, `:194-197`).
- [ ] Docs/OpenAPI disabled, redirects off
      (`public_published_application.py:173-179`).
- [ ] `Cache-Control: no-store` and `nosniff` present on failure responses
      (`public_published_api.py:55-58`).
- [ ] Read-only engine: missing file refuses (`engine.py:59-63`), percent-
      encoded URI (`:65-66`), per-connection `PRAGMA query_only=ON`
      (`:83`), startup INSERT probe with rollback (`:101-122`).
- [ ] Startup pins schema head `0034`
      (`public_published_application.py:55`, enforced at `:263-276`).

### 4.3 [preflight] TLS termination baseline (reverse proxy)

- Modern TLS only (TLS 1.2 minimum, TLS 1.3 preferred); disable SSLv3/TLS1.0/
  TLS1.1 and weak cipher suites.
- HSTS enabled once the origin is stable (`Strict-Transport-Security` with a
  deliberate max-age; start small, escalate deliberately).
- ACME discipline: automated issuance/renewal with monitoring on renewal
  failure; private keys never copied into the repository or env templates.
- No TLS-level session resumption sharing across unrelated origins; dedicated
  certificate for the public hostname.
- Proxy must not forward client-supplied `Tailscale-*` headers unchanged, and
  must strip hop-by-hop junk; the application additionally ignores forwarded
  headers (`server.py:38-39`).

### 4.4 [preflight] Transport limits the ASGI layer intentionally lacks (F-8/C3)

The application owns none of these; the proxy MUST:

- per-source-IP connection caps and idle-connection reaping;
- request body size limits (public routes accept no bodies anyway — enforce a
  tiny cap such as 16 KiB and reject the rest);
- connect/read/send timeouts tuned against slow-loris style consumption;
- worker/concurrency caps so streaming clients cannot exhaust the pool;
- rate limiting for content-heavy routes (`/api/media/*/locations/*/content`,
  gallery previews, cover thumbnails) keyed by IP;
- request-rate limits on `/api/media` search with sane burst allowance.

### 4.5 [preflight] OS/host hardening

- Firewall default-deny inbound; allow only proxy listener ports and the
  administrative access channel. Router port-forwarding remains forbidden
  ([ADR-0074](adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
  decision 4; [AGENTS.md](../AGENTS.md) security boundaries).
- SSH discipline: key-only auth, no root login, optional non-standard port or
  better yet no direct exposure at all.
- Unattended security upgrades enabled for the OS package base.
- Time sync (systemd-timesyncd or chrony) verified — audit events and logs are
  millisecond-stamped (`structured_logging.py:339-342`).
- Disk-full monitoring: uploads, staging roots, cover caches, and the catalog
  partition need free-space alerting (application-side reserve checks exist for
  acquisition staging; e.g. `x_acquisition.py:505-510` — the host still needs
  its own watchdog).

### 4.6 [preflight] Service hardening

- Dedicated non-login system user/group for both server processes; no shared
  shell accounts.
- UDS directory permissions: the socket directory should be owned by the
  service identity with no world access; verify with
  `scripts/operator/infosec/kronika_socket_permissions_check.sh`.
- systemd suggestions for the preflight whole to evaluate (suggestions, not
  applied unit files): `DynamicUser=` or fixed `User=framenest`,
  `NoNewPrivileges=yes`, `ProtectSystem=strict` with explicit `ReadWritePaths=`
  for media/library roots only, `ProtectHome=yes`, `PrivateTmp=yes`,
  `PrivateDevices=yes`, `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6`,
  `RestrictSUIDSGID=yes`, `MemoryDenyWriteExecute=yes` (verify compatibility),
  `SystemCallFilter=@system-service`.
- The public reader needs read access to the catalog file, media library,
  cover storage, and preview cache — nothing else; it generates no derivatives
  (`public_published_application.py:64-73` fail-closed stand-ins).

### 4.7 [preflight] Secrets and configuration hygiene

- No secrets in the repository or committed env templates
  ([SECURITY.md](../SECURITY.md), "Secret Handling"). The public process needs
  no credentials at all.
- `/etc/framenest/framenest.env` stays non-secret; anything sensitive belongs
  in systemd credentials, not environment files.
- Provider keys (`NVIDIA_API_KEY`, `AI_GATEWAY_API_KEY`) must exist only on
  workspace-side infrastructure, never on the public reader host path.
- Settings loading fails closed on unreadable explicit env files
  (`src/framenest/configuration.py:476-523`); keep it that way.

### 4.8 [preflight] Log hygiene

- Structured JSON logs redact bearer tokens, URLs, paths, media filenames, and
  sensitive keys before serialization
  (`structured_logging.py:50-75`, `:247-255`, filter `:183`, formatter `:204`);
  exception payloads carry type names only (`:258-259`).
- Uvicorn HTTP access logging is disabled (`server.py:40`,
  `structured_logging.py:133-137`); if the proxy adds access logs, treat them
  as sensitive (they contain client IPs and requested paths) and bound their
  retention.
- Never paste raw journal output into tickets or chats; use
  `scripts/operator/infosec/kronika_log_triage.sh`, which prints counts and
  event keys only.

### 4.9 [preflight] Backup and restore cadence

- Follow [docs/BACKUP_AND_RECOVERY.md](BACKUP_AND_RECOVERY.md): scheduled
  catalog backup timer with restore drills; a backup you have never restored is
  a hypothesis, not a backup.
- Verify the public reader fails closed (refuses to start) rather than serving
  a stale or half-restored catalog (`public_published_application.py:107-117`,
  `engine.py:59-63`).

### 4.10 [rule] F-7 atomic reader/writer release ordering

The public reader pins schema revision `0034`
(`public_published_application.py:55`) and refuses to start otherwise. Any
workspace-side migration therefore strands running readers until they are
updated in the same release. Rule: **reader and writer always ship as one
atomic release step**; deploy order is stop-reader → release → migrate-writer →
release → start-reader, or a single immutable-release switch that moves both.
Never leave a reader running behind a migrated catalog.

### 4.11 [app→preflight] Workspace admission bounds in force

- Per-user proposal submits bounded at 6/hour by default
  (`analysis_proposal.py:30`, enforcement `:77-88`), mirroring the YouTube/X
  requester limiters (`x_acquisition.py:216-230`, `:474-511`);
  duplicates inside the window get an honest sanitized 429.
- Audit-before-execute remains: allowed attempts are recorded before dispatch
  and status-stamped afterwards (`tailscale_ingress.py:882-927`); unmatched
  routes hit the fail-closed fallback policy (`:615-630`).

## 5. Incident response first steps

Ordered, minimal, read-mostly. These are preparation notes for the preflight
whole — nothing here authorizes live mutation today.

1. **Freeze evidence windows.** Pull bounded journal ranges for the structured
   event keys (`public_unexpected_failure`,
   `public_request_validation_rejected`, `public_http_exception_rejected`)
   plus proxy error logs for the same window. Prefer counts and keys
   (`kronika_log_triage.sh`) over raw dumps.
2. **Stop the bleeding at the proxy.** Remove the public vhost/upstream or
   return 503 at the proxy layer. This drops the public listener without
   touching the workspace socket — the two compositions share a database, not
   a process (`application.py:375-380`).
3. **Unpublish instantly when content is the incident.** Administrator
   `PUT /api/admin/media/{media_id}/content-publication` toggles durable
   publication truth; unpublish stops future public requests immediately
   (`content_publication_repository.py:282-306`; ADR-0074 publication gate).
   Accepted limit: bytes already downloaded cannot be revoked.
4. **Check workspace blast radius.** The public composition cannot mutate, but
   confirm no new policies/routes appeared: route inventory test
   (`test_route_inventory_is_exact_get_allowlist`) and the fallback-policy grep
   (`tailscale_ingress.py:615-630`).
5. **Preserve, then rotate.** Snapshot journals and audit-event tables for the
   window before cleanup; rotate any credential that could have been exposed
   ([SECURITY.md](../SECURITY.md), accidental-exposure procedure).
6. **Post-incident:** rerun
   `kronika_public_surface_check.sh` and the contract suite through the
   canonical AP route before re-enabling the origin.

## 6. Explicit non-goals

These stay out of scope regardless of hardening effort; reopening any of them
is a product/architecture decision, not an ops task:

- No user registration, billing, payments, or multi-tenant SaaS.
- No router port-forwarding of any FrameNest port — ever
  ([AGENTS.md](../AGENTS.md); ADR-0074 rejected alternatives).
- No Tailscale Funnel to the workspace socket
  (`/run/framenest/framenest.sock`); Funnel to the public socket is
  contingency-only through a later operational ADR (ADR-0074 ingress ranking).
- No public mutations, CORS, or public upload/acquisition surfaces.
- No automatic analysis enablement by default
  ([ADR-0066](adr/0066-administrator-owned-x-automatic-generic-analysis.md)).
- No second catalog or ownership columns (ADR-0074 rejected alternatives).

## 7. Operator diagnostics

Read-only, unauthenticated, bash-only tools under
[`scripts/operator/infosec/`](../scripts/operator/infosec/). All inputs come
from environment variables with safe placeholder defaults; none embed real
hostnames, tokens, or host-specific identifiers. They are source material and
grant no operational authority by themselves
([AGENTS.md](../AGENTS.md)).

| Tool | Purpose |
|---|---|
| `kronika_public_surface_check.sh` | Verifies the deployed public posture: docs/OpenAPI/admin paths and POST probes match the uniform sanitized 404 byte-for-byte, with `no-store`/`nosniff` present |
| `kronika_log_triage.sh` | Counts security-relevant structured-log event keys and audit markers from `journalctl`, flags spikes above a threshold, prints counts only |
| `kronika_socket_permissions_check.sh` | Stats configured UDS paths; fails on missing sockets, world access bits, or unexpected owner |

Each script prints usage with `-h` and documents its environment variables in
its header comment. The canonical `kronika_*` names are authoritative; the
retained `framenest_*` entry points keep working unchanged until the
compatibility-removal window.
