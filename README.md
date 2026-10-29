# Kronika

Kronika is a local-first, privacy-conscious, cross-platform library for video and animated media, designed around ownership, organization, and a premium visual gallery experience.

## Accepted Transition to Kronika

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) records
one Kronika built in this repository. No new repository or second product is
planned. [ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
is the current target for Search, Research and the shared Timeline. The
internal `kronika` package, migration history, HTTP headers, storage keys
and deployment identifiers remain. The shell now shows Kronika in the title,
header label and wordmark while keeping the `FN` mark. The public repository
is `cisarik/kronika`. The former capture repository is `cisarik/cli_chatgpt`
and stays active.

Accepted target:

- The Timeline is the main page and contains only administrator-approved
  records. Personal history is a separate view of questions and answers,
  including complete Research reports and the caller's unfinished work.
  Gallery remains a separate working view.
- Search and Research use a provider-neutral application boundary per
  [ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md),
  with the [ADR-0084](docs/adr/0084-administrator-managed-research-settings-and-versioned-pricing.md)
  correction. The first provider is the OpenAI Responses API with native
  provider-managed research. Kronika supervises that lifecycle. There is no
  automatic fallback. Verified administrators select one of four exact catalog
  models (`gpt-5.5-2026-04-23` remains the default) through server settings
  only, and admission persists immutable versioned pricing. Research stays
  disabled by default.
- An authenticated application administrator can read all product records,
  including private and unfinished work. That access is application content
  only. Owners do not publish directly to the shared page; administrators
  approve completed questions and answers and successfully analyzed media.
  The shared page is for verified household members only. Internet publication
  stays disabled.

Published on public `main`:

- MEME and Movie behavior already in the application remains.
- The chatgpt.com capture module is present at `src/kronika_capture` and is
  parked. It is not the current Search or Research provider. Bounded ZIP
  activation stays parked with it.
- S6 records and approval, S4-B research runtime, S7-P personal-history and
  rendering APIs, and S9-R administrator-managed research settings and
  versioned pricing are published. Research stays disabled by default.
- S8 is on public main: Timeline landing, separate personal history, Search
  and Research forms, completed-document viewing and administrator review.
  S9 integrated acceptance, the empty-catalog reset and live-provider
  acceptance are closed.
- The code and status descriptions below remain the pre-transition baseline
  where they describe publication-based Gallery and administrator workflows.
  They are not the target privacy policy for new records.

The old databases contain unwanted test data. There is no import project.
The S9 empty-catalog reset and live-provider acceptance are closed. See
[ROADMAP.md](ROADMAP.md) for the closed active order and the parked capture
rows.

## Status

Kronika is in an early foundation, pre-alpha stage with a substantial shipped
server, catalog, Gallery/Details, upload, acquisition, and operator foundation.

A Poetry package scaffold exists at the repository root with centralized
settings, a FastAPI application factory, a typed `GET /health` endpoint,
in-process contract tests, a loopback-first Uvicorn development server, a
packaged pre-alpha local web shell at `GET /`, pure-domain identity primitives,
and a repository-native systemd service foundation targeted at Ubuntu Server
24.04 on the Intel NUC6i5SYH development-and-testing machine. The NUC is
routinely refreshed to the exact public `main` SHA through the immutable
release-update contract; the authoritative runtime readback is
`kronika-release status` (see the Ubuntu NUC runbook), never a committed SHA
snapshot. A production release was previously accepted at public/canonical
commit `aec2f0091c10aed2fc2033dac154a0d9651b2b6d` (schema head `0028`), served
from `/opt/framenest/releases/aec2f0091c10aed2fc2033dac154a0d9651b2b6d` with
Tailscale Serve only (Funnel not publicly exposed); that fact is dated history.
The NUC serves as the Kronika development-and-testing machine
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md));
rendered UI/UX and Brave-companion acceptance always happen against this
freshly refreshed NUC.
There is still no completed desktop shell, installer, or generalized
multi-source downloader UI.

The repository also contains the first persistence, registry, media catalog,
local media-analysis, AI suggestion-review, and quarantine upload-transport
foundations: a centralized SQLite database path setting, synchronous SQLAlchemy
Core engine helpers, packaged Alembic resources through schema head `0035`,
including private Kronika records and administrator approval, which are
accepted and on public `main`,
explicit database commands, local device and library registry tables, durable
upload-session, canonical byte-identity, duplicate-disposition, and
publication-provenance and upload-to-catalog linkage tables, persistent
logical-media and physical-location tables, persistent display-title, plain-text
description, and canonical-tag tables, an automatic built-in `Processed`
workflow collection derived from durable tag saves, read-only library scan and
media-analysis preview commands, explicit idempotent scan-candidate import, an
imported-media catalog browser with display-title search, canonical-tag AND
filters, a virtual `All media` Catalog scope, and an optional `Processed`
Catalog scope, a manual browser `Edit media` dialog for persistent title,
optional plain-text description, ordered tag assignment, and explicit
server-provider AI assistance through NVIDIA NIM or Vercel AI Gateway. Ordinary
users may submit private uploads for administrator review; administrators have
bounded batch actions, safe catalog removal, and content publication.
Requester-private YouTube and X meme acquisition with explicit administrator
promotion are implemented (X native video, animated-GIF-like media delivered as
video, and public JPEG/PNG photos; WebP is rejected without transcoding). A
loadable unpacked Manifest V3 X companion uses a frozen ingest Save (Edit-media
subset, no category radios) and an administrator merged title-bar review history
in the side panel. Companion review Save does not publish. Apply writes metadata
only. The administrator publication PUT
(`PUT /api/admin/media/{media_id}/content-publication`), including unpublish on
the same PUT, is the sole publication path
([ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)).
Automatic analysis stays default-off; administrator-owned X may
enqueue when that flag is later enabled. The companion still attaches catalog
JPEG, PNG, GIF-style, or short-video memes to the X composer after an exact
extension-origin allowlist is configured
([ADR-0061](docs/adr/0061-x-meme-browser-companion.md),
[ADR-0064](docs/adr/0064-x-save-category-and-public-photo-acquisition.md),
[ADR-0066](docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md),
[ADR-0067](docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md),
[ADR-0068](docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md),
[ADR-0069](docs/adr/0069-five-tag-generic-media-suggestion-contract.md),
[ADR-0070](docs/adr/0070-companion-exclusion-of-movie-workflows.md),
[ADR-0071](docs/adr/0071-native-side-panel-review-inbox-chrome.md),
[ADR-0072](docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md),
[ADR-0073](docs/adr/0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md),
[ADR-0076](docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md),
[docs/X_COMPANION.md](docs/X_COMPANION.md)).
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
records the pre-transition dual-audience boundary. ADR-0082 kept internet
publication off for new Kronika records. ADR-0083 adds administrator-curated
household Timeline approval and does not enable that public reader: the Tailscale
workspace writer remains the current remote path. The local-only
`public_published_uds` composition and workspace rollout successors are
implemented at this baseline. Public bind, TLS, and Funnel remain unshipped.
This status does not claim a public bind, TLS, Funnel, or automatic-analysis
flag enablement.

Workspace identities with only `metadata.alias.write` can edit the
caller-private alias overlay through the existing Edit dialog, with per-field
AI suggestion strips revealed immediately from the newest stored run and a
dropdown that switches suggestions without a Load step, backed by
`GET /api/media/{media_id}/ai-suggestions`
([ADR-0077](docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md),
[ADR-0080](docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md)).
The Gallery card brain-symbol Analyze control opens that same Edit dialog
immediately after confirmation and resolves analysis in place instead of a
bulk last-write-wins save
([ADR-0078](docs/adr/0078-gallery-card-ai-per-field-review.md),
[ADR-0080](docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md)), and the
administrator companion Settings **Automatic media analysis** toggle is backed
by the fifth `companion_mutation` route,
`PUT /api/admin/settings/automatic-analysis`
([ADR-0079](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md)).

The upload path receives untrusted bytes only into
configured server quarantine, performs bounded server-side validation, and
derives canonical identity from validated byte size and SHA-256 digest. The
current foundation also includes a durable manual cover foundation: at most one
accepted manually selected cover per logical medium from an available GIF, MP4,
or still-image source, with durable server-owned artifacts and regenerable cover
thumbnails ([ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)). It
does not provide arbitrary user-created collections, a general collection
manager, persisted suggested filenames, complete Cover Studio,
imported/AI/series covers, cover candidates, or AI Draft persistence. Selected
catalog metadata can travel beside one chosen media copy through the portable
sidecar v1 projection and `kronika-sidecar` operator commands; the artifact is
deterministic and versioned, and it remains an explicit projection rather than
bidirectional synchronization.

Migration `0021` adds a separate durable content-publication relation. Existing
logical media is backfilled as published; newly cataloged media remains outside
the ordinary Gallery until persisted title, description, and canonical-tag
metadata is complete and an authorized administrator publishes that one item.
`GET /api/media` is published-only for every identity, while a separate
capability-gated `Manage media` surface supports responsive inspection,
bounded batch actions, and publication. Upload storage publication, cataloging,
`Processed`, AI analysis, and ordinary-audience content publication remain
distinct facts.

Migration `0022` adds the durable manual cover foundation: a sparse accepted
`media_covers` relation, admin-only source-frame authoring from an available
GIF or MP4 source, durable immutable cover artifacts, and regenerable
`cover-thumbnail-jpeg-v1` derivatives served through an identity-only,
publication-visibility-gated endpoint. Gallery cards prefer a validated cover
thumbnail and fall back to the existing `gallery-preview`/fallback path
([ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)). Later revisions
through `0028` add catalog-removal receipts, ordinary-user upload ownership,
requester-private YouTube and X acquisition, and YouTube/X creator taxonomy
contracts.

Supported runtime: CPython `>=3.13,<3.14`. Poetry is the dependency,
environment, and lockfile manager (`pyproject.toml` + committed `poetry.lock`).
Local development may use `uv` only to locate or install a CPython 3.13
interpreter for Poetry ([ADR-0006](docs/adr/0006-macos-python-interpreter-provider.md));
`uv` is not the project dependency manager and an untracked `uv.lock` is not
project authority. The committed `poetry.lock` was generated with Poetry 2.3.2;
NUC deployment tooling (`deploy/ubuntu/kronika_release.py`) pins Poetry 2.4.1.
The local virtual environment lives in `.venv/` and is not committed. Worker
runtime and exact-source rules:
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md).

## Development Setup

Preferred clone:

```text
git clone --recurse-submodules <repository>
```

If the repository was cloned without submodules:

```text
git submodule update --init --recursive
./.ap/ap doctor
```

From the repository root on Apple Silicon macOS:

```text
./kronika setup
poetry run pytest
```

`./kronika setup` idempotently uses `uv` to locate or install CPython
`3.13.14`, configures Poetry for the in-project `.venv/`, and installs the
committed dependencies. Re-run it after dependency or lockfile changes.
JavaScript `node:test` suites and opt-in browser evidence commands are
documented in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md) and
[DEVELOPMENT.md](DEVELOPMENT.md).

## Local Server

Start the browser-development application from the repository root:

```text
./kronika start
```

Default application URL: `http://127.0.0.1:8000`. Health path: `/health`.

Common launcher commands:

```text
./kronika start
./kronika start --no-open
./kronika status
./kronika open
./kronika logs
./kronika logs --follow
./kronika restart
./kronika stop
```

The launcher is browser-development tooling. It is not `Kronika.app`, not a
Tauri shell, and not a production service. It binds the managed server to
loopback, explicitly migrates the persistent development database as part of
user-invoked `start`, waits for the real `/health` response, opens the external
default browser by default, and controls only a process proven to have been
started by the launcher.

Lower-level developer/operator commands remain available:

```text
poetry run kronika-server
poetry run kronika-db status
poetry run kronika-db migrate
poetry run kronika-catalog --help
poetry run kronika-sidecar --help
poetry run kronika-backup --help
./kronika ai status
./kronika ai configure
./kronika ai test
./kronika ai provider add --help
./kronika ai provider list
./kronika ai provider remove --help
./kronika ai vision-probe --help
./kronika backup create --help
./kronika backup verify --help
./kronika backup restore --help
./kronika youtube --help
```

The raw `poetry run kronika-server` process keeps its existing behavior: it
does not migrate automatically and runs in the foreground until stopped.

The default URL serves the packaged pre-alpha Kronika web shell. It can confirm that the real local application server is running, load the same-origin health endpoint, list registered libraries from the local catalog, browse imported catalog media with persisted display-title search and repeated canonical-tag AND filters, open one imported medium in `Edit media`, edit or clear its persisted title, edit or clear its optional plain-text description, select up to 32 tags, explicitly create tag definitions through the hidden-key tag control, save or cancel title/description/tag changes, use compact Gallery overlay actions to edit metadata or open an available imported GIF or MP4 as the direct original media resource in a new browser context, and, when a server-side AI provider is configured, explicitly analyze the imported GIF or MP4 to produce editable current-form values. AI analysis requires user confirmation, sends up to three optimized JPEG preview frames plus bounded non-secret metadata through the Kronika server, never uploads the original file or local path, and never saves metadata or renames a file automatically. Same-origin APIs can create/list canonical tags, get/save media display-title/description/tag metadata, retrieve a deterministic read-only media catalog page, stream identity-only media content, initiate identity-only attachment downloads, and request identity-only imported-media AI suggestions. Library registration remains available through the catalog CLI in this slice. Gallery cards display only the media surface and title; they do not display canonical tag chips, hidden-tag counters, empty tag rows, or internal Processed state. The compact card Analyze control uses the brain symbol in the top-right corner when metadata is needed. Details prioritizes playback, clickable canonical tags that activate the existing Gallery filter, and the persisted description rather than generic media-kind text or a prominent Processed panel. The media content endpoint `GET /api/media/{media_id}/locations/{location_id}/content` is same-origin, read-only, identity-only (the URL contains catalog identities, never a filesystem path), and streams registered local GIF and MP4 content with single byte-range support. The download endpoint `GET /api/media/{media_id}/locations/{location_id}/download` uses the same identity-only catalog resolution and returns `Content-Disposition: attachment` with a sanitized deterministic filename; it initiates ordinary browser download behavior only, does not know the final destination, does not prove completion or retention, and does not register trusted client-local availability. Both endpoints verify the catalog relationship between the logical media, physical location, and registered library, require location availability `available`, allow only the exact supported kind/extension pairs (`video` + `.mp4` → `video/mp4`, `animated_image` + `.gif` → `image/gif`), enforce registered-root containment with symlink escape prevention, and return sanitized errors without path disclosure. Gallery Details renders the real local GIF or MP4 content from the identity-only endpoint when an `available` location exists. Premium gallery exposure with the complete Cover Studio, imported/AI/series covers, full native/VLC playback, arbitrary user-created collections, deletion/removal actions, physical rename, trusted local-copy removal, and AI Draft persistence remain future work. A durable manual cover foundation is implemented: an administrator opens `Choose cover` from Details, selects and previews a source frame, and explicitly `Set as cover`; Gallery cards then prefer the validated cover thumbnail.

After verified identity resolution explicitly grants `media.workflow.read`, the
header reveals `Manage media`. That sibling surface defaults to unpublished
items, filters by readiness and analysis state, reuses Details through a
path-redacted adapter, publishes only one ready item at a time, and offers a
single-item `Remove from catalog` action for administrators with
`media.catalog.remove`. Catalog removal is hard catalog retirement with retained
original bytes; it is not Trash, soft delete, batch removal, or physical purge.
The permissive loopback fallback never reveals this administrator navigation.

Browser API paths currently include:

```text
GET /api/libraries
POST /api/libraries/{library_id}/scan-preview
POST /api/libraries/{library_id}/media-imports
GET /api/media
POST /api/canonical-tags
GET /api/canonical-tags
GET /api/media/{media_id}/metadata
PUT /api/media/{media_id}/metadata
POST /api/libraries/{library_id}/media-analysis-preview
GET /api/ai/media-suggestion-capability
GET /api/status/cloud
GET /api/admin/ai/providers
PUT /api/admin/ai/providers/{provider_id}
DELETE /api/admin/ai/providers/{provider_id}
PUT /api/admin/ai/active-selection
POST /api/admin/ai/ping
POST /api/admin/ai/pong
POST /api/libraries/{library_id}/media-suggestion-preview
POST /api/media/{media_id}/locations/{location_id}/ai-suggestion-preview
GET /api/media/{media_id}/locations/{location_id}/content
POST /api/uploads
GET /api/uploads/{upload_id}
PATCH /api/uploads/{upload_id}
POST /api/uploads/{upload_id}/complete
POST /api/uploads/{upload_id}/duplicate-resolution
DELETE /api/uploads/{upload_id}
```

The non-browser operator API additionally exposes:

```text
POST /api/operator/youtube/claims
GET /api/operator/youtube/claims/{claim_id}
POST /api/operator/youtube/claims/{claim_id}/retry
```

These API paths do not run migrations automatically and do not expose library root paths. The scan-candidate import endpoint is explicit, same-origin, idempotent by exact `(library_id, relative_path)`, and creates only minimum logical-media and physical-location records. `GET /api/media` is read-only, returns one deterministic logical-media page with total count, searches only persisted display titles using escaped SQLite `LIKE` with `NOCASE`, treats repeated canonical tag filters as AND constraints, accepts an optional `collection=processed` query parameter that restricts the page to members of the built-in `Processed` workflow collection, and returns only catalog-safe library-relative location data. Canonical tag keys are stable English identifiers. Display titles are user-editable catalog metadata and remain separate from physical filenames and paths. Metadata saves derive collection membership automatically from the saved tag list, do not accept client-supplied collection state, and do not rename, move, delete, retag, or otherwise mutate media files. Gallery cards do not display the internal `Processed` workflow label or timestamp; the built-in Processed scope and persistence semantics remain internal catalog behavior. The media-analysis preview endpoint is explicit, same-origin, read-only, local-only, stateless, provider-free, and non-persistent. It returns inline base64 PNG frames only for the lifetime of the response/displayed browser preview and sets `Cache-Control: no-store`. Upload endpoints are disabled until `KRONIKA_UPLOAD_QUARANTINE_ROOT` names an existing absolute non-symlink quarantine directory outside registered media roots and the preview cache. They use a Kronika-owned offset protocol with `PATCH application/offset+octet-stream`, explicit completion, fixed session expiry, configured total/chunk/free-space limits, and same-origin browser mutation checks. Default upload limits are 1 GiB per session, 8 MiB per PATCH, 24 hours fixed session TTL, and 64 MiB minimum free-space reserve. Exact duplicates are compared only after authoritative validation, and the resolution endpoint accepts only explicit keep or discard decisions for the selected opaque session. Optional automatic publication additionally requires `KRONIKA_UPLOAD_PUBLICATION_LIBRARY_ID` to select an existing safe registered POSIX library. Without it, eligible uploads remain `publish_pending`; invalid explicit destination configuration fails closed. Publication uses opaque server-owned targets, verifies exact bytes, never overwrites an existing object, and creates no catalog or Gallery records. Upload responses remain sanitized and never expose storage keys, publication identities, destination paths, checksums, cleanup state, or server paths; duplicate-resolution responses additionally omit the matching session, byte identity, checksum, and filename.

The public catalog query is now published-only and cannot request unpublished
content. Capability-gated `GET /api/admin/media` provides the administrator
read model; audited same-origin
`PUT /api/admin/media/{media_id}/content-publication` performs idempotent
single-item publication. Audited same-origin catalog-removal preview and
mutation endpoints require `media.catalog.remove`, retain all original bytes,
and leave a durable receipt for exclusive derived-artifact cleanup retry.
Direct metadata, preview, content, range, download,
and analysis routes enforce the same audience policy and conceal unpublished
object existence from unauthorized callers.

The AI capability endpoint is same-origin, read-only, sanitized, and does not contact the provider. The browser Status modal exposes a read-only AI `Model` tab and a Cloud tab that reports the current development server as connected over local loopback. AI provider administration is an operator boundary: the CLI (`./kronika ai configure`, `./kronika ai status`, `./kronika ai status --no-write`, `./kronika ai test`, `./kronika ai provider add|list|remove`, and `./kronika ai vision-probe`) plus an authenticated administrator-only website surface over the trusted Tailscale workspace ingress. The website surface lists built-in and operator-declared providers, manages non-secret schema-v2 declarative provider records, activates one provider/model, and runs explicit ping/pong diagnostics under `provider.operate` with audit-before-mutation. Ping is an explicit text-only request; pong requires explicit cloud-upload confirmation, sends exactly one committed synthetic solid-red fixture image, and never uses catalog media. An added or activated provider resolves per operation and takes effect without a service restart. The CLI writes only non-secret provider/model selection outside the repository, `status` is network-free and records a safe local status snapshot, `status --no-write` performs the same sanitized resolution without writing a snapshot for deployment preflight, `test` is an explicit text-only provider connection test, `provider add|list|remove` manages declared records, and `vision-probe` runs one explicit synthetic color test. Configuration precedence is explicit `KRONIKA_AI_PROVIDER_ID`/`KRONIKA_AI_MODEL_ID` environment override, persisted non-secret server AI configuration, legacy NVIDIA compatibility when `NVIDIA_API_KEY` is present and no provider config exists, then unconfigured. An unconfigured server reports `not_configured` without fabricating a fallback provider/model. A selected provider/model with no matching credential reports `credential_unavailable` while preserving only safe provider/model diagnostics. Other sanitized states include `configured_unverified`, `available`, `authentication_failed`, `rate_limited_or_quota_exhausted`, `model_unavailable`, `provider_unreachable`, and `provider_error`. Credentials are resolved from the existing process environment first (`AI_GATEWAY_API_KEY` for Vercel AI Gateway, `NVIDIA_API_KEY` for NVIDIA NIM, and each declared record's credential environment-variable name, for example `OPENCODE_API_KEY`), then from systemd's `CREDENTIALS_DIRECTORY` using only the exact selected provider credential name. The preferred Vercel model is `google/gemini-3.1-flash-lite`; NVIDIA keeps the existing default model. The ordinary browser client never configures providers, selects models, enters API keys, or receives credential values, credential prefixes, Authorization headers, raw provider responses, prompt payloads, image payloads, absolute media paths, or database paths; the authenticated administrator provider surface manages only non-secret records and receives credential environment-variable names and availability booleans only. When configured, the imported-media AI suggestion preview endpoint requires `confirm_cloud_upload: true`, resolves only `media_id` and `location_id`, reuses local read-only media preparation, sends at most three derived JPEG frames plus bounded metadata to the active server provider, returns one validated editable suggestion with title, description, tags, and suggested filename, and performs no filesystem, catalog, or database mutation. Untagged supported Gallery cards may expose a direct `Analyze` shortcut that uses the same identity-only suggestion flow and opens the existing metadata editor with unsaved editable suggestions; tagged cards omit that shortcut. Metadata Save remains explicit, and the suggested filename is not persisted or applied. OS keychain support and explicit physical rename are separate work.

For local development, the root `./kronika` launcher optionally loads `.secrets/ai.env.fish` before `ai status`, `ai configure`, `ai test`, `start`, and `restart`. This ignored file is resolved only inside the repository root, must be a private regular file owned by the current user, and may export `NVIDIA_API_KEY`, `AI_GATEWAY_API_KEY`, and/or `OPENCODE_API_KEY`. Missing file means no local secret bootstrap, and existing exported environment values remain supported. Production AI credential support is repository source material only until a later authorized host task runs `deploy/ubuntu/fn-production-env-deploy`; the helper does not install itself into `~/.config/fish` and does not perform a provider test by default.

Kronika-owned runtime logs are compact JSON lines written to `stderr` by the direct application process. The installed console entrypoint `.venv/bin/kronika-server` is the strict application-process boundary used by machine-readable output contract tests. Ordinary interactive termination with Ctrl+C or SIGTERM through that direct entrypoint must not emit an unstructured traceback. The browser-development launcher redirects its managed child process output to the user development log shown by `./kronika logs`.

`poetry run kronika-server` remains the normal development command, but Poetry or other launchers may additionally emit their own diagnostics outside the Kronika logging graph. Those launcher-owned lines are not Kronika structured log records and are not covered by the application JSON contract.

Override bind address with `KRONIKA_HOST` and `KRONIKA_PORT` for the raw
server command. Default binding is loopback-only (`127.0.0.1`), and
loopback-only TCP binding is enforced in code: a non-loopback TCP host is
rejected at configuration time and no exposure override exists. UDS/Tailscale
ingress remains the remote path. The browser-development launcher enforces
loopback and accepts only `KRONIKA_PORT` for port selection.

Reload, AppArmor/UFW hardening completion, and broader multi-device
synchronization remain open work. A catalog backup create/verify/restore
foundation exists through `kronika-backup`, automated catalog
backup/retention/restore-verification is accepted through
[ADR-0052](docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md),
and mounted-filesystem off-device catalog copy/restore-verification is accepted
through
[ADR-0056](docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md),
with operator-workstation pull-based catalog snapshot recovery accepted through
[ADR-0057](docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md)
and documented in [docs/BACKUP_AND_RECOVERY.md](docs/BACKUP_AND_RECOVERY.md); it
does not copy original media, include secrets, replace production databases as
an automated recovery, or claim that a destination survives host loss until
separately authorized host acceptance proves the failure domain. Workstation
pull is repository capability until later E3 host acceptance. A
repository-native generic systemd service bundle exists under `deploy/systemd/`,
the superseded Fedora guide remains in [docs/FEDORA_SERVICE.md](docs/FEDORA_SERVICE.md),
and the current Ubuntu NUC deployment workflow is documented in
[docs/UBUNTU_NUC_DEPLOYMENT.md](docs/UBUNTU_NUC_DEPLOYMENT.md). A routine
immutable release-update contract (`deploy/ubuntu/kronika-release`) is
accepted through
[ADR-0060](docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md);
refreshing the NUC to the exact public `main` SHA is normal routine operation
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)).
A production release was previously accepted at commit
`aec2f0091c10aed2fc2033dac154a0d9651b2b6d` from
`/opt/framenest/releases/aec2f0091c10aed2fc2033dac154a0d9651b2b6d` over
Tailscale Serve only; that fact is dated history, and the deployed NUC state
is read back through `kronika-release status`.

## Local Database Foundation

Kronika reads `KRONIKA_DATABASE_PATH` through the centralized settings boundary.

Administrative and production CLI commands never read a `.env` file from the
caller's working directory. An environment file is applied only when
explicitly requested through `KRONIKA_ENV_FILE` (or the explicit
`load_settings(env_file=...)` parameter); a missing or unreadable explicit
file fails closed with a sanitized error, and process environment variables
keep the highest precedence.

The browser-development launcher uses a persistent development database at:

```text
~/Library/Application Support/Kronika/development/catalog.sqlite3
```

If an explicit absolute `KRONIKA_DATABASE_PATH` is already set, the launcher
honors it. The launcher applies packaged Alembic migrations to the selected
development database during `./kronika start`.

The current default is temporary development behavior:

```text
Path(tempfile.gettempdir()) / "kronika-development" / "catalog.sqlite3"
```

This fallback is intentionally outside the repository and is not the final production storage policy. Persistent deployments are expected to set an explicit absolute `KRONIKA_DATABASE_PATH`. `~` is expanded before validation. Relative paths are rejected with sanitized errors.

Inspect migration status without creating a missing database:

```text
poetry run kronika-db status
```

Explicitly upgrade the configured database to the packaged Alembic head:

```text
poetry run kronika-db migrate
```

Normal `poetry run kronika-server` startup does not apply migrations.
Migration remains explicit through `kronika-db`. Revisions through `0007` add
the initial catalog and metadata tables. Revisions `0008` through `0018` add
the durable upload, validation, byte-identity, duplicate, publication, catalog,
automatic-analysis, still-image, classification, movie-identification, and
analysis-history foundations. Revision `0019` adds the durable YouTube
manual-acquisition claim and provenance lifecycle without fabricating
historical claims. A resumable quarantine upload transport now exists but
remains disabled until `KRONIKA_UPLOAD_QUARANTINE_ROOT` is configured. It
writes only to server quarantine, validates completed uploads, and gates later
exact copies in `duplicate_pending` until the user explicitly keeps or discards
that copy. With an explicitly configured safe publication library, the
application automatically reconciles and atomically publishes eligible
uploads, including cleanup retry after a durable publication. Multiprocess
publication or catalog leases/fencing and broader media-transfer product UX
remain future work. The media catalog foundation supports explicit idempotent
import from selected scan candidates, persistent display title and canonical
content tags, catalog retrieval with display-title search plus canonical-tag
AND filtering, an automatic built-in `Processed` collection entered by the
first durable tag save and cleared when all tags are removed, and browser
editing of the current title, description, and tag metadata. There is still no
arbitrary user-created collection schema, general collection manager,
suggested filenames, complete Cover Studio, imported/AI/series covers, cover
candidates, premium gallery data, user, or authentication schema. A
durable manual cover foundation (migration `0022`) is implemented for one
accepted cover per logical medium
([ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)).

## YouTube Operator Ingestion

Cookie-free public single-video ingestion is an owner-operated loopback
boundary. It is enabled only when upload quarantine, a safe publication
library, and a pre-existing private `KRONIKA_YOUTUBE_ACQUISITION_ROOT` are
configured. The root must be absolute, non-symlinked, and disjoint from the
database, quarantine, preview cache, and every registered media root. The
server owns durable claims, one downloader subprocess at a time, restart
recovery, staging cleanup, and the handoff into the existing upload lifecycle.
The CLI never receives downloader output or filesystem paths.

```text
./kronika youtube ingest URL
./kronika youtube status CLAIM_ID
./kronika youtube retry CLAIM_ID
```

These are CachyOS development-launcher forms. On the NUC production host the
release-local `kronika-youtube` console entry point is the operator
interface under the protected production environment and explicit release
working directory; it does not require Fish. See the operator command
execution contract in
[docs/UBUNTU_NUC_DEPLOYMENT.md](docs/UBUNTU_NUC_DEPLOYMENT.md).

The server independently validates each URL. Only supported HTTPS YouTube
single-video forms are accepted; playlists, authenticated or live media,
cookies, browser profiles, sidecars, and transcoding are excluded. A successful
new item begins with `content_category=general` and
`acquisition_source=youtube_manual_claim`. Byte duplicates reuse the existing
logical medium and location without overwriting editable metadata, while the
claim retains immutable provenance. Automatic AI analysis is suppressed for
this source.

Run the deterministic no-network acceptance demonstration:

```fish
# [CachyOS / fish]
poetry run python tests/support/youtube_fake_demo.py
#------------------------------------------------------
```

## Device Catalog CLI

The `kronika-catalog` command is a development/operator boundary for local device and library registry operations. It does not migrate automatically and is not the final desktop UX. Device and library registration commands exist today; library registration does not scan directory contents.

After migrating the database and registering a device:

```text
poetry run kronika-db migrate
poetry run kronika-catalog device register --display-name "My MacBook"
poetry run kronika-catalog device list
poetry run kronika-catalog device get --id "<device-id>"
poetry run kronika-catalog library register \
  --device-id "<device-id>" \
  --display-name "Videos" \
  --root "$HOME/Videos"
poetry run kronika-catalog library list
poetry run kronika-catalog library get --id "<library-id>"
poetry run kronika-catalog library scan-preview \
  --id "<library-id>"
```

Optional bounded limits:

```text
--max-entries
--max-candidates
```

Library registration requires an already registered owning device and a database migrated to the packaged head. It checks that the supplied local root path exists and is a directory. It does not enumerate files, resolve symlinks to their targets, or scan the library contents.

`library scan-preview` requires the same migrated database and an already registered library. The preview is read-only, writes no media records, skips nested symlinks and dot-prefixed entries, classifies candidates by extension only, and may return relative candidate paths. This remains a development/operator boundary, not the final desktop UX.

`library analyze-preview` requires the same migrated database, an already registered library, and one explicit relative MP4 or GIF candidate path. The command is read-only, uses optional local `ffprobe` and `ffmpeg` executables when present, returns bounded technical metadata plus at most three exact-distinct representative PNG frames in memory only, writes no media records, leaves no extracted frame files on disk, and performs no provider or network calls. This remains a development/operator boundary, not the final desktop UX.

`library suggest-preview` requires the same migrated database, an already registered library, one explicit relative MP4 or GIF candidate path, `--confirm-cloud-upload`, and `NVIDIA_API_KEY` in the process environment. The command reuses local read-only preparation, derives bounded in-memory JPEG images from the local PNG representative frames for NVIDIA NIM transport when explicitly confirmed, returns one validated non-persistent suggestion preview, performs no catalog or filesystem mutation, and does not persist suggestions. This remains a development/operator boundary, not the final desktop UX.

```text
poetry run kronika-catalog library analyze-preview \
  --id "<library-id>" \
  --path "relative/path.mp4"
```

```text
poetry run kronika-catalog library suggest-preview \
  --id "<library-id>" \
  --path "relative/path.mp4" \
  --provider nvidia-nim \
  --model nvidia/nemotron-3-nano-omni-30b-a3b-reasoning \
  --confirm-cloud-upload
```

## Portable Media Sidecar CLI

The `kronika-sidecar` command is a thin operator adapter over the accepted
portable media sidecar v1 projection. During normal operation the SQLite
catalog remains authoritative. A sidecar is an explicit, deterministic,
versioned projection beside one chosen media copy; it is not live catalog write
authority, not a second catalog, and not bidirectional synchronization.

Export and compare require explicit `--media-id` and `--location-id` and a
database at migration head. `KRONIKA_DATABASE_PATH` follows the existing
settings boundary. Validate requires only `--path` and does not load or require
the catalog. There is no implicit location selection, multi-copy fan-out,
sidecar-to-catalog import, rebuild, drift repair, or metadata Save coupling.
Ordinary metadata Save does not write sidecars. Missing sidecars do not
invalidate catalog state. This command is not a production deployment surface.

The adjacent filename current writers use is `{media_filename}.kronika.json`,
for example `clip.mp4.kronika.json` beside `clip.mp4`. Readers still accept a
historical `{media_filename}.framenest.json` beside the same media file.

Successful commands print exactly one JSON object plus a trailing newline to
stdout and exit `0`. Compare `missing` is a completed observation, not an
error. Failures print exactly one JSON object to stderr, leave stdout empty,
and exit `1`. Output does not include absolute paths, catalog paths, library
roots, or sidecar contents.

```text
poetry run kronika-sidecar export \
  --media-id "<media-id>" \
  --location-id "<location-id>"
poetry run kronika-sidecar validate --path "movies/clip.mp4.kronika.json"
poetry run kronika-sidecar compare \
  --media-id "<media-id>" \
  --location-id "<location-id>"
```

## Structured Logging

The direct Kronika server process emits one compact JSON object per application-owned log line to `stderr`.

Logging uses a Kronika-owned JSON formatter and centralized redaction boundary. Uvicorn access logging is initially disabled for privacy. There are no log files, rotation, retention enforcement, remote shipping, or correlation middleware yet.

Launcher, interpreter, shell, supervisor, and future service-manager diagnostics remain separate output sources. Captured combined `stderr` from a wrapped command must not automatically be treated as entirely application-generated.

## Product Vision

Kronika is intended to help people acquire, organize, browse, and maintain personal video and animated-media libraries while keeping local ownership central.

The current approved product direction includes:

- A visually premium gallery as a flagship experience.
- Media acquisition through replaceable source adapters.
- Local-first ownership through an authoritative Kronika server process that
  may run locally or on the Ubuntu NUC.
- Privacy-aware AI assistance later, after core library behavior and safety boundaries are established.
- Multiple libraries, multiple devices, and one logical media item that may exist in multiple physical locations.
- An Intel NUC development-and-testing machine
  ([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md))
  for authoritative catalog serving, with further remote-access, transfer, and
  archive workflows remaining future work.

## Architectural Direction

The current conceptual direction is:

- A shared HTML/CSS/JavaScript UI that can run in browser development mode and later in a native system WebView.
- Tauri v2 as the accepted future desktop shell for normal local desktop operation.
- Python for domain, filesystem, downloader, metadata, server, and media-processing capabilities.
- PWA or browser access where appropriate.
- An authoritative Kronika server process with browser, desktop, and remote
  clients.
- External VLC first for playback, with embedded libVLC considered later.
- Workspace remote access through Tailscale Serve to
  `/run/kronika/kronika.sock` rather than router port-forwarding. The
  local-only published-reader implementation from
  [ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
is retained baseline history. ADR-0082 keeps public composition off and
excludes new Kronika records. ADR-0083 keeps internet publication disabled and
uses administrator approval for the household Timeline. Public rollout is
outside this stage.

The accepted desktop and distributed-media direction remains documentation-led
for Tauri packaging. No Tauri scaffold or installer exists yet. The Ubuntu NUC
is the development-and-testing machine routinely refreshed toward public `main`
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md));
the previously accepted release `aec2f0091c10aed2fc2033dac154a0d9651b2b6d`
noted above is dated history. Multi-device synchronization and transfer remain
later scope.

Accepted implementation foundations so far:

- CPython 3.13 ([ADR-0001](docs/adr/0001-supported-python-version.md))
- Poetry ([ADR-0002](docs/adr/0002-python-environment-and-dependency-manager.md))
- `pydantic-settings` ([ADR-0007](docs/adr/0007-settings-library.md))
- FastAPI ([ADR-0003](docs/adr/0003-initial-server-api-framework.md))
- Uvicorn as the initial ASGI runtime ([ADR-0008](docs/adr/0008-asgi-runtime.md)), installed and wired for loopback-first local development
- SQLAlchemy Core and Alembic for the initial SQLite migration foundation ([ADR-0010](docs/adr/0010-initial-persistence-foundation.md)), installed and wired behind explicit database commands
- Application-owned UUIDv4 stable domain identities with category-specific pure-domain types ([ADR-0011](docs/adr/0011-stable-domain-identities.md))
- Packaged vanilla local web application delivery through the existing FastAPI process ([ADR-0017](docs/adr/0017-initial-local-web-application-delivery.md))
- Same-origin local media-analysis preview API with inline bounded base64 PNG frames ([ADR-0018](docs/adr/0018-local-media-analysis-preview-api.md))
- VLM transport JPEG derivatives and NVIDIA documented instruct mode for the suggestion prototype ([ADR-0019](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md))
- Explicit on-demand editable AI suggestion review ([ADR-0020](docs/adr/0020-on-demand-ai-suggestion-review.md))
- Tauri v2 desktop shell direction ([ADR-0021](docs/adr/0021-tauri-desktop-shell.md))
- Selective media placement direction ([ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md)); server-authority portions are superseded by the authoritative server/client state model ([ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md))
- Manual-first metadata and multi-model AI draft workspace direction ([ADR-0023](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md), [AI_WORKSPACE.md](AI_WORKSPACE.md))
- Manual Cover Studio and AI cover candidate direction ([ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md), [COVER_PIPELINE.md](COVER_PIPELINE.md))
- Minimum persistent media catalog foundation ([ADR-0025](docs/adr/0025-minimum-persistent-media-catalog-foundation.md))
- Explicit idempotent scan-candidate import ([ADR-0026](docs/adr/0026-explicit-idempotent-scan-candidate-import.md))
- Persistent display title and canonical tags ([ADR-0027](docs/adr/0027-persistent-display-title-and-canonical-tags.md))
- Catalog read model and search semantics ([ADR-0028](docs/adr/0028-catalog-read-model-and-search-semantics.md))
- Automatic built-in `Processed` workflow collection from durable tag saves ([ADR-0030](docs/adr/0030-automatic-processed-collection.md))
- Fedora systemd service foundation, superseded for the active deployment target by the Ubuntu NUC deployment foundation ([ADR-0031](docs/adr/0031-fedora-systemd-service-foundation.md), [ADR-0032](docs/adr/0032-ubuntu-nuc-deployment-foundation.md))
- Durable quarantine upload sessions, bounded validation, lifecycle orchestration, canonical byte identity, exact-duplicate disposition, atomic upload publication and cataloging, source-aware analysis suppression, content classification, and YouTube manual acquisition ([ADR-0037](docs/adr/0037-durable-upload-session-and-safe-ingest-foundation.md), [ADR-0038](docs/adr/0038-bounded-upload-media-validation.md), [ADR-0039](docs/adr/0039-lifecycle-owned-upload-validation-orchestration.md), [ADR-0040](docs/adr/0040-canonical-upload-byte-identity-foundation.md), [ADR-0041](docs/adr/0041-exact-byte-upload-duplicate-disposition.md), [ADR-0042](docs/adr/0042-atomic-upload-publication.md), [ADR-0043](docs/adr/0043-upload-to-catalog-transaction.md), [ADR-0044](docs/adr/0044-durable-automatic-post-catalog-analysis.md), [ADR-0045](docs/adr/0045-content-classification-and-movie-identification.md), [ADR-0046](docs/adr/0046-youtube-manual-ingestion-and-provenance.md))

Exact future frontend framework or compiled toolchain, desktop/Tauri packaging choices, IPC design, sidecar bundling, data schema, identity database encoding, deployment model, production update mechanisms, and many server operational details remain subject to later documented decisions.

## Security and Privacy Principles

Kronika must keep secrets out of Git. Real environment files, API keys, tokens, cookies, private keys, and service credentials must not be committed.

Local desktop operation must not require a server. Workspace remote features
are expected to use Tailscale as the network boundary. A second public
published-reader composition is accepted architecture direction in
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
and is not shipped. Backend services must not be exposed publicly by default.

Security decisions should favor least privilege, explicit confirmation for destructive actions, and clear separation between local user data, generated runtime state, and version-controlled source files.

## Development Methodology

Kronika development follows Analytic Programming through the pinned `.ap/`
Git submodule. The current AP gitlink is:

```text
7478ddb07d2c3911f79e1aa1441f0115a31c45d8
```

Universal AP protocol files live under `.ap/`. Kronika-specific operating
rules live in [AGENTS.md](AGENTS.md) outside the managed AP block. Worker
runtime and exact-source rules live in
[docs/WORKER_EXECUTION_CONTRACT.md](docs/WORKER_EXECUTION_CONTRACT.md). Verify AP
integration with:

```text
./.ap/ap doctor
```

AP updates are explicit, reviewed, and committed as `.ap` gitlink changes.
Kronika does not consume AP `main` dynamically. Permanent `BOOT_*` and
`NEXT_*` files are not part of the current live repository model. Orchestrator
rotation normally uses a generated professional restoration prompt. A repository
handoff is exceptional context, not task authority, and Michal is not expected
to manually create `handout` commits by default.

Core working principles remain:

- Inspect before changing.
- Use the repository as the source of truth.
- Keep Worker tasks bounded and evidence-based.
- Make the smallest change that satisfies the authorized task.
- Verify behavior with tests or direct evidence before claiming success.
- Use public commit verification once commits are authorized.

## Development Targets

Initial development and testing targets Apple Silicon macOS.

The Ubuntu NUC is the current Kronika development-and-testing machine on
Ubuntu Server 24.04 on the Intel NUC6i5SYH
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)):
its accepted role remains development/test for this one product, routinely
refreshed toward public `main`. Capture remains parked. ADR-0083 adds a future
research-provider credential gate and limits the planned reset to exact old
test-database objects. Current host state and live provider readiness still
require preflight. Broader cross-platform support remains an architectural
requirement, and a future Ubuntu VPS remains a portability target.

## Documentation Map

Current foundation files:

- [`.gitignore`](.gitignore) defines the first repository safety perimeter.
- [`.gitattributes`](.gitattributes) defines cross-platform text and binary handling.
- [`.editorconfig`](.editorconfig) defines baseline editor formatting.
- [`SECURITY.md`](SECURITY.md) defines the initial pre-alpha security policy.
- [`docs/INFOSEC.md`](docs/INFOSEC.md) records the repository-local infosec hardening manual, audit record, and read-only operator diagnostics; it claims no deployed state.
- [`docs/ACCEPTANCE_DUAL_AUDIENCE.md`](docs/ACCEPTANCE_DUAL_AUDIENCE.md) is the step-by-step Cooperator acceptance guide for the dual-audience boundary, including the deployment-freeze annex.
- [`PRODUCT.md`](PRODUCT.md) defines the approved product vision, users, outcomes, experience principles, capabilities, and non-goals.
- [`SPEC.md`](SPEC.md) defines the initial normative product and system requirements.
- [`ROADMAP.md`](ROADMAP.md) defines the staged evidence-based development roadmap.
- [`DESKTOP.md`](DESKTOP.md) records accepted desktop shell architecture and UX direction.
- [`DEVELOPMENT.md`](DEVELOPMENT.md) describes the local browser-development launcher workflow.
- [`docs/WORKER_EXECUTION_CONTRACT.md`](docs/WORKER_EXECUTION_CONTRACT.md) defines Worker runtime, `.venv`, exact-source evidence, and test invocation rules.
- [`SERVER.md`](SERVER.md) records accepted optional server and NUC aggregation direction.
- [`GALLERY.md`](GALLERY.md) records accepted gallery product and UX direction.
- [`AI_WORKSPACE.md`](AI_WORKSPACE.md) records accepted manual-first metadata and multi-model AI workspace direction.
- [`COVER_PIPELINE.md`](COVER_PIPELINE.md) records accepted Cover Studio and cover candidate direction.
- [`AGENTS.md`](AGENTS.md) defines Kronika-specific agent operating rules.
- [`.ap/README.md`](.ap/README.md), [`.ap/INTEGRATION.md`](.ap/INTEGRATION.md), and [`.ap/AP.md`](.ap/AP.md) define the pinned canonical Analytic Programming protocol integration.
- [`.ap/AP_ORCHESTRATOR.md`](.ap/AP_ORCHESTRATOR.md) defines the universal Orchestrator handbook.
- [`.ap/AP_WORKER.md`](.ap/AP_WORKER.md) defines the universal Worker handbook.
- [`docs/NUC_HOST_BASELINE.md`](docs/NUC_HOST_BASELINE.md) preserves sanitized command-observed NUC host baseline facts.
- [`docs/UBUNTU_NUC_CLEAN_INSTALL.md`](docs/UBUNTU_NUC_CLEAN_INSTALL.md) describes a future fresh or replacement NUC installation and recovery, without claiming a completed installation.
- [`docs/ARCHITECTURE_FOUNDATION_EVIDENCE.md`](docs/ARCHITECTURE_FOUNDATION_EVIDENCE.md) collects primary-source evidence for the first architecture decisions. It is not an ADR and does not approve any option.
- [`docs/adr/README.md`](docs/adr/README.md) indexes accepted architecture decision records.
- [`docs/adr/0001-supported-python-version.md`](docs/adr/0001-supported-python-version.md) records the accepted CPython 3.13 runtime decision.
- [`docs/adr/0002-python-environment-and-dependency-manager.md`](docs/adr/0002-python-environment-and-dependency-manager.md) records the accepted Poetry dependency and environment management decision.
- [`docs/adr/0003-initial-server-api-framework.md`](docs/adr/0003-initial-server-api-framework.md) records the accepted FastAPI initial server API framework decision.
- [`docs/adr/0004-repository-layout.md`](docs/adr/0004-repository-layout.md) records the accepted hybrid staged monorepo repository layout decision.
- [`docs/adr/0005-configuration-strategy.md`](docs/adr/0005-configuration-strategy.md) records the accepted layered configuration strategy decision.
- [`docs/adr/0006-macos-python-interpreter-provider.md`](docs/adr/0006-macos-python-interpreter-provider.md) records the accepted macOS interpreter provider decision.
- [`docs/adr/0007-settings-library.md`](docs/adr/0007-settings-library.md) records the accepted `pydantic-settings` decision.
- [`docs/adr/0008-asgi-runtime.md`](docs/adr/0008-asgi-runtime.md) records the accepted Uvicorn ASGI runtime decision.
- [`docs/adr/0009-structured-logging-approach.md`](docs/adr/0009-structured-logging-approach.md) records the accepted structured logging decision.
- [`docs/adr/0010-initial-persistence-foundation.md`](docs/adr/0010-initial-persistence-foundation.md) records the accepted SQLAlchemy Core and Alembic SQLite persistence foundation decision.
- [`docs/adr/0011-stable-domain-identities.md`](docs/adr/0011-stable-domain-identities.md) records the accepted stable domain identity decision.
- [`docs/adr/0012-initial-device-registry.md`](docs/adr/0012-initial-device-registry.md) records the accepted initial device registry decision.
- [`docs/adr/0013-initial-library-registry.md`](docs/adr/0013-initial-library-registry.md) records the accepted initial library registry decision.
- [`docs/adr/0014-safe-library-scan-preview.md`](docs/adr/0014-safe-library-scan-preview.md) records the accepted read-only library scan preview decision.
- [`docs/adr/0015-deterministic-local-media-analysis-preparation.md`](docs/adr/0015-deterministic-local-media-analysis-preparation.md) records the accepted local media-analysis preparation decision.
- [`docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md`](docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md) records the accepted provider-neutral media suggestion preview decision.
- [`docs/adr/0017-initial-local-web-application-delivery.md`](docs/adr/0017-initial-local-web-application-delivery.md) records the accepted packaged local web shell delivery decision.
- [`docs/adr/0018-local-media-analysis-preview-api.md`](docs/adr/0018-local-media-analysis-preview-api.md) records the accepted same-origin local media-analysis preview API decision.
- [`docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md`](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md) records the accepted VLM JPEG derivative and NVIDIA instruct-mode decision.
- [`docs/adr/0020-on-demand-ai-suggestion-review.md`](docs/adr/0020-on-demand-ai-suggestion-review.md) records the accepted on-demand editable AI suggestion review decision.
- [`docs/adr/0021-tauri-desktop-shell.md`](docs/adr/0021-tauri-desktop-shell.md) records the accepted Tauri v2 desktop shell direction.
- [`docs/adr/0022-selective-media-placement-and-server-aggregation.md`](docs/adr/0022-selective-media-placement-and-server-aggregation.md) records the accepted selective placement direction.
- [`docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md`](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md) records the accepted manual-first metadata and multi-model AI draft decision.
- [`docs/adr/0024-cover-studio-and-ai-cover-candidates.md`](docs/adr/0024-cover-studio-and-ai-cover-candidates.md) records the accepted Cover Studio and AI cover candidate decision.
- [`docs/adr/0025-minimum-persistent-media-catalog-foundation.md`](docs/adr/0025-minimum-persistent-media-catalog-foundation.md) records the accepted minimum persistent media catalog foundation decision.
- [`docs/adr/0026-explicit-idempotent-scan-candidate-import.md`](docs/adr/0026-explicit-idempotent-scan-candidate-import.md) records the accepted explicit idempotent scan-candidate import decision.
- [`docs/adr/0027-persistent-display-title-and-canonical-tags.md`](docs/adr/0027-persistent-display-title-and-canonical-tags.md) records the accepted persistent display-title and canonical-tag decision.
- [`docs/adr/0028-catalog-read-model-and-search-semantics.md`](docs/adr/0028-catalog-read-model-and-search-semantics.md) records the accepted catalog read model and search semantics decision.
- [`docs/adr/0029-persistent-plain-text-media-description.md`](docs/adr/0029-persistent-plain-text-media-description.md) records the accepted persistent plain-text media description decision.
- [`docs/adr/0030-automatic-processed-collection.md`](docs/adr/0030-automatic-processed-collection.md) records the accepted automatic built-in `Processed` workflow collection decision.
- [`docs/adr/0031-fedora-systemd-service-foundation.md`](docs/adr/0031-fedora-systemd-service-foundation.md) records the historical Fedora systemd service foundation superseded by ADR-0032 for the active deployment target.
- [`docs/adr/0032-ubuntu-nuc-deployment-foundation.md`](docs/adr/0032-ubuntu-nuc-deployment-foundation.md) records the accepted Ubuntu NUC deployment foundation.
- [`docs/adr/0033-catalog-backup-and-recovery-foundation.md`](docs/adr/0033-catalog-backup-and-recovery-foundation.md) records the accepted catalog backup and recovery foundation.
- [`docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md`](docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md) records mounted-filesystem off-device catalog copy and restore verification.
- [`docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md`](docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md) records operator-workstation pull-based catalog snapshot recovery.
- [`docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md`](docs/adr/0058-independent-mullvad-egress-and-operator-network-recovery.md) records independent Mullvad egress and operator network recovery.
- [`docs/OPERATOR_NETWORK.md`](docs/OPERATOR_NETWORK.md) is the durable operator contract for Mullvad egress controls.
- [`docs/adr/0050-durable-manual-cover-foundation.md`](docs/adr/0050-durable-manual-cover-foundation.md) records the accepted durable manual cover foundation.
- [`docs/adr/0034-canonical-analytic-programming-integration.md`](docs/adr/0034-canonical-analytic-programming-integration.md) records the accepted pinned canonical AP submodule integration.
- [`docs/adr/0035-authoritative-server-and-client-state-model.md`](docs/adr/0035-authoritative-server-and-client-state-model.md) records the accepted authoritative server/client state model.
- [`docs/adr/0042-atomic-upload-publication.md`](docs/adr/0042-atomic-upload-publication.md) records the accepted atomic upload publication decision.
- [`docs/adr/0043-upload-to-catalog-transaction.md`](docs/adr/0043-upload-to-catalog-transaction.md) records the accepted upload-to-catalog transaction.
- [`docs/adr/0044-durable-automatic-post-catalog-analysis.md`](docs/adr/0044-durable-automatic-post-catalog-analysis.md) records the accepted automatic-analysis lifecycle.
- [`docs/adr/0045-content-classification-and-movie-identification.md`](docs/adr/0045-content-classification-and-movie-identification.md) records the accepted content classification and movie-identification decision.
- [`docs/adr/0046-youtube-manual-ingestion-and-provenance.md`](docs/adr/0046-youtube-manual-ingestion-and-provenance.md) records the accepted YouTube manual-ingestion provenance lifecycle.
- [`docs/adr/0064-x-save-category-and-public-photo-acquisition.md`](docs/adr/0064-x-save-category-and-public-photo-acquisition.md) records X Save category, public JPEG/PNG photo acquisition, and honest companion Save outcomes.
- [`docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md`](docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md) records administrator-owned X automatic generic analysis (default-off).
- [`docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md`](docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md) records the companion review inbox and the first four `companion_mutation` routes.
- [`docs/adr/0079-administrator-automatic-analysis-runtime-setting.md`](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md) records the administrator companion Settings overlay and the fifth `companion_mutation` route.
- [`docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md`](docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md) records the ordinary alias-edit affordance, per-field AI suggestion strips, and the workspace `GET /api/media/{media_id}/ai-suggestions` read route.
- [`docs/adr/0078-gallery-card-ai-per-field-review.md`](docs/adr/0078-gallery-card-ai-per-field-review.md) records the Gallery card Analyze (brain-symbol) control as a per-field review path through the existing Edit dialog.
- [`docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md`](docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md) records immediate newest-suggestion reveal, removal of Load, dropdown switching, and in-modal card/editor analysis progress.
- [`docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md`](docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md) records companion review Save publication when ready.
- [`docs/adr/0069-five-tag-generic-media-suggestion-contract.md`](docs/adr/0069-five-tag-generic-media-suggestion-contract.md) records the live generic v4 1–5 tag contract.
- [`docs/adr/0070-companion-exclusion-of-movie-workflows.md`](docs/adr/0070-companion-exclusion-of-movie-workflows.md) records companion exclusion of movie workflows.
- [`docs/adr/0071-native-side-panel-review-inbox-chrome.md`](docs/adr/0071-native-side-panel-review-inbox-chrome.md) records native side-panel review inbox chrome.
- [`docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md`](docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md) records unread-inbox and title-bar history chrome; named two-list statements are succeeded by ADR-0073.
- [`docs/adr/0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md`](docs/adr/0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md) records merged companion history, pending visibility, the `x`/`𝕏` seed tag, and preserving Apply; mixed-inbox and pending-overlay statements are succeeded by ADR-0076.
- [`docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md`](docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md) records hosted history clicks, the administrator analyzed-only inbox, ordinary own-history, and own-item opened.

## Non-Goals for the Current Stage

The current stage does not provide:

- A complete mobile application.
- Cloud backup.
- A transcoding cluster.
- Embedded libVLC.
- AI-generated covers.
- Public internet exposure.
- A complete premium gallery, arbitrary user-created collections, suggested filenames, complete Cover Studio, imported/AI/series covers, cover candidates, persistent AI Drafts, persistent media scanner, or generalized downloader UI.
- VPS or public-Internet production hosting beyond the current owner-operated Ubuntu NUC Tailscale Serve deployment.

## License

A license has not yet been selected.

Although this repository may be publicly readable, it is not currently offered under an open-source license unless and until a license file is added.

## Contributing

Contribution guidelines are not finalized while the architecture and repository foundation are being established. Public issues and proposals should avoid sensitive information and should stay within the currently documented project scope.
