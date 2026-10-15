# Kronika Server and Client Architecture

## Status

This is a living permanent product architecture reference. It records accepted
server/client direction and current shipped foundations. It does not claim that
streaming, multi-device synchronization, complete Cover Studio, or full NUC
security hardening are finished.

Classification: living permanent product architecture/UX reference.

Consumers: Orchestrator, Worker, designers, implementers, maintainers, and
security reviewers.

Retention: remains while the server product subsystem exists.

Inbound links: [README.md](README.md), [PRODUCT.md](PRODUCT.md),
[SPEC.md](SPEC.md), [ROADMAP.md](ROADMAP.md),
[ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md),
[ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md),
[ADR-0021](docs/adr/0021-tauri-desktop-shell.md),
and [ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task. Git history remains the archive.

## Accepted Kronika Application and Research Boundary

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) and
[ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
record the next architecture in this same repository. This section documents
that target. Existing Kronika implementation summaries below remain the
pre-transition baseline. They do not prove that research, common-record
privacy or capture deployment exists.

The application owns one authoritative catalog, media, complete Search and
Research documents, identity, administrator approval and the Timeline. There
is no second server or deployment system. The existing `kronika` package,
headers, migrations and service identities remain. The workspace shell calls
the existing record and research routes and does not add an HTTP route.
Research stays disabled by default. The S8 shell is a local candidate, not a
deployed or accepted release. The public composition stays off.

### Local supervision and the hosted research loop

Kronika runs the supervisory runtime: authorize a submission, reserve
budget, submit, poll, cancel, validate, save and reconcile cleanup. The model
and web-search loop runs at the selected provider. The first provider is the
OpenAI Responses API, id `openai-responses`, with native provider-managed
research and no automatic fallback. Research configuration is disabled by
default. An absent research section means disabled. A disabled provider must
not prevent ordinary application startup.

The persistent coordinator is built whenever the catalog engine exists,
including a disabled start. Construction performs no credential provisioning
and no provider contact. Admission and capabilities requests read a fresh
validated configuration; saving settings never replaces the coordinator, and a
process started disabled can be enabled without a restart. Disabling prevents
new admissions and new submission claims while preserving history, polling,
cancellation, cleanup and reservations. An atomic `ADMITTED` to `SUBMITTING`
claim guarantees one provider creation per request.

Verified administrators select one of the catalog models through
`GET`/`PUT /api/admin/ai/research-settings`. Admission persists an immutable
provider, model and profile identity that determines later pricing; a restart
resolves pricing from that persisted identity, never from the current
selection. Configuration writes share one concurrency contract: a per-path
process lock plus a sibling OS advisory lock, a SHA-256 revision snapshot and
strong `If-Match` compare-and-set. A research-only save preserves the media
configuration subtree and a media-only save preserves the research subtree;
JSON formatting, key order and `updated_at_ms` are not preserved
byte-for-byte.

Non-secret configuration stores the credential identifier
`KRONIKA_RESEARCH_OPENAI_API_KEY` only. Production reads that one credential
through the existing systemd credential boundary. Development may use the
named environment variable only when an operator explicitly configures it.
The server does not accept a client-supplied endpoint, model, tool or key.
Ordinary clients never receive provider secrets. Media-analysis provider
selection stays separate and is not replaced by the research provider.

Only the submitted question text and fixed non-secret instructions may leave
the host. Research accepts no attachment. The application does not fetch
arbitrary result URLs. Generated output is untrusted. Complete question and
answer text, including Research reports, is stored locally. The shared
Timeline does not receive a card until an administrator approves the completed
record.

### Parked capture module

The chatgpt.com capture module, package `kronika_capture` and command
`kronika-capture`, remains parked. It is not the current Search or Research
provider. It owns ChatGPT page interaction and its own bounded job state, not
family records or access decisions. It runs in a separate process behind a
`127.0.0.1` bridge. The frontend receives no bridge token. Frame preparation,
deterministic ZIP and budget calculations stay in the application and are not
research-upload support.

While parked, its constraints remain: one active job includes a paused one;
identical retries return the same job; conflicting content is refused;
terminal state is bounded; one persistent headed Chromium on permanent Xvfb
uses one dedicated profile; bridge outages reconnect without closing it;
normal web releases do not restart it; `needs_admin` blocks new work until
explicit continuation; a possibly sent prompt is never automatically resent;
and the module does not fall back from the page to an external API. Host
setup and real capture calls stay unauthorized.

### Records and ingress

Existing Tailscale Serve, restricted UDS provenance, explicit identity mapping
and mutation protection remain the ingress. New records start private to the
owner derived from verified identity or an explicitly configured local owner.
An authenticated application administrator can read all product records,
including private and unfinished work. That access is application content
only. Household members see a record on the shared Timeline only after
administrator approval. Owners do not publish directly to that page. Approval
does not use the internet-publication APIs. The public composition stays off
and excludes new records. Every content route and query must enforce these
rules.

Archived generated content preserves complete text and Markdown and uses
isolated sanitized HTML without scripts or external resources. The main
application must not trust generated HTML. The exact stopped-writer database
reset remains a later authorized step in [ROADMAP.md](ROADMAP.md).

## Server Authority

ADR-0035 records the current server/client authority model. A Kronika server
process is authoritative for:

- catalog records;
- server media originals;
- canonical title, description, and tags;
- future category and language metadata;
- per-user visibility state;
- upload and ingest state;
- server preview cache;
- durable accepted covers and cover artifacts;
- authentication and capability decisions.

Browser, desktop, local NUC browser, and future remote interfaces are clients
of that server API. The server process may run locally on the same device as a
desktop client or later on the Ubuntu NUC. Local-first operation means Kronika
can be owned and run locally without public cloud dependence; it does not mean
each client interface owns an independent authoritative catalog.

Ordinary clients may request catalog state and may explicitly stream, open, or
download authorized media. They must not mutate arbitrary server files and must
not infer administrator authority from loopback, source IP, hostname, Tailscale
membership, cookies, or same-machine execution.

## NUC Role

The Intel NUC currently serves as the Kronika development-and-testing
machine
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)):
its accepted role is the development/test home of this product, routinely
refreshed toward public `main` through the
immutable release-update contract. It was previously framed as the personal
production server role ([ADR-0032](docs/adr/0032-ubuntu-nuc-deployment-foundation.md));
dated production facts remain history. The NUC is not required for local
ownership and must not make Kronika a public-cloud or SaaS dependency.

ADR-0082 keeps capture as part of the same product, now parked, not another
project. ADR-0083 adds the research supervisor to this same server. The
accepted reset concerns the unwanted test databases only; it is not blanket
permission to delete media or state directories. Installed browser tooling,
active services, profile readiness and research-credential readiness require
a later read-only preflight.

The NUC currently provides authoritative catalog serving for the disposable
development-and-testing instance. Later or incomplete NUC capabilities
include:

- archive or preferred storage for selected media bytes beyond current roots;
- remote streaming and download beyond current Tailscale Serve access;
- transfer coordination;
- explicit archive/copy/move destinations;
- centralized provider access for AI workflows beyond current credential
  foundations;
- media second-copy backup participation.

Ubuntu Server 24.04 on the Intel NUC6i5SYH supersedes Fedora as the active
deployment target. A repository-native systemd service foundation and Ubuntu
NUC deployment runbook exist, and a routine immutable release-update contract
(`deploy/ubuntu/kronika-release`) is accepted through
[ADR-0060](docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md).
The NUC is routinely refreshed to the exact public `main` SHA; the
authoritative runtime readback is `kronika-release status`. A production release was
previously accepted at commit `aec2f0091c10aed2fc2033dac154a0d9651b2b6d` from
`/opt/framenest/releases/aec2f0091c10aed2fc2033dac154a0d9651b2b6d` with schema
`0028`, healthy service, and Tailscale Serve only (Funnel not publicly
exposed); that fact is dated history. Further NUC security hardening,
AppArmor/UFW completion, production database replacement automation, and media
second-copy backup remain open. A
Tailscale remote-access and identity foundation (root-owned HTTPS Serve to a
permission-restricted Unix socket, verified-identity mapping, capability
authorization, and privileged-action audit) is recorded in
[ADR-0048](docs/adr/0048-tailscale-remote-access-and-identity-foundation.md)
and the current runbook. An unpacked Manifest V3 X companion may submit five
`companion_mutation` routes from an exact allowlisted `chrome-extension://`
origin: X submit, X retry, review opened, review apply, and
`PUT /api/admin/settings/automatic-analysis`
([ADR-0079](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md)). It may also list
requester-visible memes through `GET /api/x/companion/media` and read the
administrator review inbox through `GET /api/companion/review-inbox` and
`GET /api/companion/review-inbox/{media_id}`. GET inbox routes work with an
empty `companion_extension_origins` allowlist; mutations that carry the
extension Origin fail closed when that allowlist is empty. Hosted
`external_origin` mutations stay on the Tailscale web origin. The companion
is mutation-inert until the allowlist is set
([ADR-0061](docs/adr/0061-x-meme-browser-companion.md),
[ADR-0064](docs/adr/0064-x-save-category-and-public-photo-acquisition.md),
[ADR-0067](docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md),
[docs/X_COMPANION.md](docs/X_COMPANION.md)). Catalog backup create/verify/restore, automated
retention/restore-verification, mounted-filesystem off-device
copy/restore-verification, and operator-workstation pull foundations are
documented in
[docs/BACKUP_AND_RECOVERY.md](docs/BACKUP_AND_RECOVERY.md),
[ADR-0052](docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md),
[ADR-0056](docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md),
and
[ADR-0057](docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md).
Repository off-device copy and workstation pull do not by themselves prove
physical host-loss survival until later authorized host acceptance.
Sanitized command-observed NUC hardening and media-storage baseline facts are
preserved in [docs/NUC_HOST_BASELINE.md](docs/NUC_HOST_BASELINE.md); that
baseline is historical host evidence, not a substitute for current acceptance.

## Same Core, Different Deployment Capabilities

Desktop and server roles should reuse the same Kronika domain and application
core. They are not separate products. Deployment role decides which adapters,
native capabilities, supervision, storage, and networking are available.

The desktop owns native interactive capabilities such as system WebView
presentation, file pickers, notifications, file-manager reveal, clipboard
integration, and future playback handoff. The server owns catalog and
server-state authority.

## Local Operation And Server State

Local desktop operation should remain useful when the required local server
process, local catalog/cache records, and local media are available. The Intel
NUC is optional for local ownership, because a desktop installation may run its
own local Kronika server process.

Offline client caching and synchronization rules remain unresolved. A client
must distinguish stale cached records, unavailable server state, and unavailable
media bytes rather than pretending it still has authoritative live state.

## Metadata And Cover Aggregation

Remote-only media cards should be visible without full media-byte replication.
The server may provide:

- logical media metadata;
- titles and descriptions;
- canonical tags;
- collection labels;
- availability and location summaries;
- cover identity and provenance;
- derived JPEG thumbnails sized for gallery use.

Original covers and reproducible derived thumbnails remain separate concepts.
The full video must not be downloaded merely to render a gallery card.

The first durable manual cover foundation is implemented ([ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md),
migration `0022`). Durable accepted-cover facts live in the authoritative SQLite
catalog; immutable cover artifacts live under `KRONIKA_COVER_STORAGE_ROOT`
(production-oriented `/var/lib/kronika/covers`) and regenerable cover
thumbnails under `KRONIKA_COVER_THUMBNAIL_CACHE_PATH` (production-oriented
`/var/cache/kronika/cover-thumbnails`). A Gallery cover thumbnail is served
through an identity-only endpoint gated by `gallery.read` and the shared
content-publication audience policy, so unpublished media never leaks cover
bytes to ordinary users. Complete Cover Studio, imported/AI/series covers, and
candidate management remain future work.

## Location And Availability Tracking

Kronika models one logical media item with zero or more physical locations.
Locations may be local, remote, archived, offline, missing, or unverified.

Users must be able to inspect where a media item is known to exist. Remote-only
cards should clearly show that playback or copy requires a remote operation.

## Selective Media Placement

Kronika must not automatically mirror all media bytes to every device.
Metadata, tags, covers, availability state, and lightweight thumbnails may
synchronize independently of full media bytes. Full media transfer must be
explicit or governed by a later accepted automation rule.

Future actions may include:

- `Archive on server`;
- `Copy to server`;
- `Move to server`;
- `Download to this device`;
- `Stream from server`;
- `Show locations`.

## Remote Streaming Direction

Remote streaming may later allow a desktop to play server-hosted media without
first storing a permanent local copy. Streaming must use authorized local or
remote URLs through the playback abstraction. External VLC remains the first
intended full-playback backend.

The exact streaming transport, authorization, buffering, range behavior, and
failure states remain deferred.

## Transfer Safety

Transfers must be explicit and safe. Move operations must not delete the source
until the destination copy is verified, readable, and registered. Transfer UI
should eventually show real determinate progress where available: bytes,
percentage, speed, ETA, and verification/finalization state.

Partial failures must be recoverable and must not silently remove the last known
valid copy.

## Service Unavailability Behavior

When a remote server is unavailable, remote-only items may become unavailable
for stream/download, but their known metadata and cover summaries should remain
visible if previously synchronized or cached.

The UI must distinguish offline server state from stale cached records, missing
local catalog/cache data, and unavailable media bytes.

## Upload, Synchronization, Cache, And Trash Direction

Future work must keep these operations separate:

- catalog synchronization;
- authenticated server-managed media ingest or upload;
- explicit client cache or download;
- per-user visibility state such as Hide or Trash;
- global retirement or physical purge of originals.

Future upload must require quarantine, content validation, limits, safe
filenames, duplicate detection, atomic publication, cleanup after failure, and
server-selected placement. Clients must not choose arbitrary server filesystem
paths.

Per-user Trash is server-persisted visibility state and must not delete the
server original. Separate future operations may include `Remove from this
managed client`, `Hide or Trash for this user`, `Request server deletion`,
`Retire globally`, and `Purge physical originals`.

## Category, Language, And Playback Direction

Future first-class categories include `memes`, `youtube`, and `movies`.
Categories are a dedicated catalog facet, not merely canonical tags or
operational directory names.

Movies may carry explicit language metadata such as English, Slovak, or Czech.
Prefer container or audio metadata and user editing before expensive AI
analysis. Do not automatically upload audio to a cloud provider.

Future playback should support fullscreen and truthful audio-track selection
where technically supported. Use capability detection and fallback instead of
fake controls. Subtitle support is not currently required. Do not silently
transcode originals.

## Network And Deployment Direction

Workspace remote access remains Tailscale-only: authenticated Tailscale Serve
to `/run/kronika/kronika.sock` and `tailscale_uds`.
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
ships a local-only `public_published_uds` published-reader composition.
That composition is not exposed externally, has no TLS listener, Funnel, or
NUC bind.
This is retained implementation history. ADR-0082 keeps the public composition
off and forbids exposing new Kronika records through it. ADR-0083 keeps
internet publication disabled; household Timeline entry is administrator
approval on this server, not this public composition.
Funnel to the workspace socket stays forbidden. Kronika must not require
router port forwarding. Tailscale networking is not sufficient authorization by
itself; application-level authorization remains required. The accepted
implementation of the workspace remote path is the Tailscale remote-access and
identity foundation in
[ADR-0048](docs/adr/0048-tailscale-remote-access-and-identity-foundation.md):
root-owned Tailscale HTTPS Serve proxies to a permission-restricted Unix
socket, and the application maps the exact verified Serve login to explicit
roles and capabilities with durable privileged-action audit.

Public-internet egress is a separate operator concern from Serve ingress.
Independent Mullvad exit-node selection and recovery are recorded in
[ADR-0058](docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md)
and [docs/OPERATOR_NETWORK.md](docs/OPERATOR_NETWORK.md). Those controls do
not add a public listener and do not change Tailscale Serve.

The historical Fedora service foundation is recorded in
[ADR-0031](docs/adr/0031-fedora-systemd-service-foundation.md). It is
superseded for the active deployment target by
[ADR-0032](docs/adr/0032-ubuntu-nuc-deployment-foundation.md) and the current
[Ubuntu NUC deployment runbook](docs/UBUNTU_NUC_DEPLOYMENT.md). Do not describe
NUC security hardening or VPS migration as completed until a later bounded task
verifies them.

## Server-Side AI Provider Boundary

Kronika has a server-operated AI provider boundary for the local development
server. Ordinary browser and desktop clients never configure providers, select
models, enter API keys, receive provider credentials, or call NVIDIA, Vercel,
OpenCode Go, Google, or another provider directly. Browser clients may view
sanitized read-only server AI status and may explicitly request AI analysis
through the Kronika server when a provider is configured. Authenticated
administrators additionally get a separate website surface under
`provider.operate`: they may manage non-secret declarative provider records,
activate one provider/model, and run explicit ping/pong diagnostics through
the server. That surface exposes only credential environment-variable names
and availability booleans, never credential values.

Server operators use the root CLI:

```text
./kronika ai status
./kronika ai configure
./kronika ai test
```

`status` is network-free and writes only a safe status snapshot beside the
non-secret AI configuration state. `configure` writes only schema-versioned
non-secret provider/model selection outside the repository, using the platform
application configuration location or an explicit configuration-path override.
`test` is an explicit minimal text-only provider request and persists only safe
historical test category/timestamp state. NVIDIA NIM remains supported. Vercel
AI Gateway is supported with preferred model `google/gemini-3.1-flash-lite`.

The browser Status modal is read-only. Its AI tab shows the configured provider
and model plus safe historical status rows when such state exists. Its Cloud tab
uses the sanitized server status contract and reports the local development
server as connected over loopback; when the Tailscale ingress mode is active it
reports the exact external tailnet origin instead. Provider administration
lives in the separate administrator AI providers surface, not in this
read-only modal.

In development, provider credentials remain in the server process environment:
`NVIDIA_API_KEY` for NVIDIA NIM and `AI_GATEWAY_API_KEY` for Vercel AI Gateway.
Production AI credential support via systemd credential files is repository
source material and may be deployed under explicit operator authority per
[ADR-0036](docs/adr/0036-production-ai-credentials-via-systemd.md). OS keychain
integration, browser provider Settings, and broader multi-user authorization
remain future bounded work.

## Security And Authorization Deferred Decisions

The initial server authentication and authorization slice is implemented by
the Tailscale remote-access and identity foundation: exact-login identity
mapping, role capabilities, sanitized 401/403 responses, and durable
privileged-action audit logging. Deferred security decisions now include:

- device trust and enrollment;
- provider-secret storage;
- transfer authorization;
- stream URL lifetime;
- backup access;
- administrator operations beyond the catalog-removal and publication
  capability set already accepted;
- schema-backed identity migration if the configuration map outgrows its
  audit provenance or operational needs;
- multi-user behavior, if any;
- physical original-byte purge, batch catalog removal, Trash/Hide, and
  soft-delete tombstone filtering.

## Current MacBook MVP Non-Goals

The current MacBook MVP does not include:

- complete NUC security hardening;
- remote streaming beyond Tailscale Serve access to the packaged web shell;
- transfer protocol;
- automatic synchronization;
- per-user visibility state such as Trash;
- media second-copy backup orchestration;
- a desktop Settings app or generalized browser settings beyond the
  authenticated administrator AI provider surface (the companion
  automatic-analysis checkbox is unchanged and desktop Settings remains
  unshipped);
- multi-device conflict resolution;
- server-side media import beyond the shipped upload and acquisition paths.

The local Gallery and Details phase is frozen for MVP absent a concrete defect.
Further NUC work should stay bounded to security hardening, acceptance, or
server-authority tasks rather than reopening Gallery or Details UX.
