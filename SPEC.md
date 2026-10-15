# Kronika Specification

## 1. Document Status and Scope

Kronika is in foundation-stage pre-alpha development. This document defines normative requirements for future implementation.

A Poetry package foundation exists with centralized configuration, a FastAPI application factory, a typed health endpoint, a loopback-first Uvicorn runtime dependency and startup command, a minimal SQLAlchemy Core/Alembic SQLite migration foundation through schema head `0035`, pure domain identity primitives, local device and library registries, a minimum persistent logical-media and physical-location catalog foundation, persistent display-title and canonical-tag core, catalog retrieval with display-title search and canonical-tag AND filtering, a manual browser `Current` metadata workspace for display-title and ordered canonical-tag assignment, an automatic built-in `Processed` workflow collection derived from durable tag saves, read-only library scan preview, explicit idempotent scan-candidate import, local media-analysis preview, provider-neutral NVIDIA/Vercel suggestion preview, VLM JPEG derivative transport, a server-operated AI configuration and diagnostics CLI, a packaged local web shell, an explicit editable browser AI suggestion review, an administrator companion merged title-bar history of pending and analyzed generic items, a best-effort `x`/`𝕏` Save seed, and preserving review Apply, a trusted-loopback quarantine upload path with bounded validation, exact-duplicate disposition, and optional atomic publication to an explicitly selected server-owned library, ordinary-user private upload submission with administrator review, requester-private YouTube and X meme acquisition with administrator promotion, durable manual covers, automated catalog backup/retention/restore-verification foundations, mounted off-device catalog copy and operator-workstation pull-based catalog snapshot recovery foundations, Tailscale remote-access and identity foundations, a portable media sidecar v1 projection with `kronika-sidecar` export/validate/compare, and a repository-native systemd service foundation targeted at Ubuntu Server 24.04 on the Intel NUC6i5SYH development-and-testing machine ([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)). There is no completed desktop shell, arbitrary user-created collections, a general collection manager, suggested filenames, complete Cover Studio, imported/AI/series covers, cover candidates, persistent AI Drafts, or multi-model draft comparison/promotion yet. A durable manual cover foundation is implemented (migration `0022` per [ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)): one accepted manually selected cover per logical medium with durable artifacts and regenerable cover thumbnails. Unresolved architecture choices remain subject to future architecture decision records.

The implemented persistence head also includes durable content-publication
truth through migration `0021`, published-only ordinary catalog retrieval, a
shared direct-media audience policy, and a responsive capability-gated
single-item administrator publication workflow. Upload storage publication and
ordinary-audience content publication are separate concepts.

This specification translates approved direction from [PRODUCT.md](PRODUCT.md), [README.md](README.md), [AGENTS.md](AGENTS.md), and [SECURITY.md](SECURITY.md) into requirements. It does not select frameworks, schemas, protocols, or packaging tools.

## Accepted Kronika Transition Requirements

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) records
the one-product architecture.
[ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
is current for Search, Research, administrator access and Timeline entry.
This section governs that target. The existing implementation remains at
schema head `0035`. Requirements and implementation summaries below that
describe published-only Gallery or a public reader are pre-transition
contracts. Administrator read access to Kronika product records follows
ADR-0083, not those older publication workflows. Their presence is not a
claim that the transition is implemented.

### Product, Ownership and Approval

The existing repository MUST become one Kronika. ADR-0085 is the sole-identity
authority, and the ordered identity cuts MUST implement it as a bounded cut
sequence rather than a mass replacement. The capture module at
`src/kronika_capture`,
command `kronika-capture`, MUST remain one parked module. A second manager,
account system, family library or Git history MUST NOT be ported. Existing
Kronika JPEG preparation, deterministic ZIP and budget calculations MUST
remain in the application and MUST NOT be reused as research attachments.

A common record in the existing catalog MUST identify its type, content,
owner, visibility, creation time, first timeline-entry time and display name.
Media MUST keep its existing tables. Complete question and answer documents,
including Research reports, MUST use the same database. Owner identity MUST
come from verified Tailscale identity or an explicitly configured local owner,
never a client-supplied `user_id`.

Every new record MUST start private to its owner. An authenticated application
administrator MUST be able to read all product records, including private and
unfinished work. That privilege MUST be limited to application content. It
MUST NOT grant provider secrets, browser credentials or host administration.
Other ordinary household members MUST NOT read another owner's private or
unfinished records. Lists, counts, search, details, previews, playback,
downloads and direct APIs MUST enforce these rules.

Household visibility MUST change only through explicit administrator approval
of a completed question and answer record, or of successfully analyzed media.
Owners MUST NOT publish directly to the shared page. Internet publication
MUST stay disabled. Public composition MUST stay off and MUST NOT expose new
records.

### Timeline, Personal History and Completion

The Timeline MUST become the landing page using the existing shell, CSS and
controls, and MUST contain only administrator-approved records, including when
the viewer is an administrator. Personal history MUST be a separate view of
the caller's questions and answers, including complete Research reports and
unfinished work. Gallery MUST remain a separate working view with the existing
Details and player behavior and authorized unanalyzed media. No new frontend
framework or design system is introduced.

Media ownership MUST exist at catalog insertion. A medium MUST NOT receive a
shared timeline-entry time until successful validated analysis has been
approved by an administrator. One medium MUST have at most one card.
Reanalysis MUST keep the previous approved projection until the new successful
result is approved. Failure MUST NOT create a card or erase an earlier
successful result. Existing metadata-suggestion approval MUST remain required.
Successful analysis MUST NOT bypass it.

A complete Search or Research save MUST make the question and answer available
in personal history and in the administrator review inventory. That completion
MUST NOT by itself insert a shared Timeline card. Pending and failed work MUST
remain outside the shared Timeline. Approval MUST recheck the exact record
version. Withdrawal MUST remove the shared card and MUST retain personal
history.

The shared Timeline MUST order newest entry first, then stable ID order, with
default page size 24 and maximum 100. Filters MUST combine Search, Research
and existing media categories. GIF MUST remain a technical format rather than
replace Meme.

The packaged shell implements this navigation as an S8 candidate: hash routes
on the existing page, summary titles and list filters, and administrator
review. Summary lists carry a bounded display title and category and do not
carry answer text. Timeline titles for every caller come from the approved
projection. S8 is on public main. Research configuration stays disabled by
default. S9 live-provider acceptance and the empty-catalog reset are closed.

### Research Provider Contract and Resource Limits

Search and Research MUST use a provider-neutral application boundary. The
first provider MUST be the OpenAI Responses API, provider id
`openai-responses`, with native provider-managed research. Kronika MUST
supervise submit, poll, cancel, validate, save and cleanup. The provider MUST
execute the model and search loop. There MUST be no automatic fallback and no
automatic generation retry. Clients MUST NOT supply endpoint, model, tool or
credential fields. Research configuration MUST be disabled by default. An
absent research section MUST mean disabled and MUST NOT prevent ordinary
application startup. Media analysis provider selection MUST remain separate.

Verified application administrators select the research model only through
server settings and the approved catalog, per
[ADR-0084](docs/adr/0084-administrator-managed-research-settings-and-versioned-pricing.md).
Search and Research submissions MUST NOT select a provider, model, endpoint or
tools. Admission MUST persist an immutable provider, model and profile identity
that determines later pricing. Configuration changes MUST NOT reprice or
regenerate an existing attempt. Fresh configuration MUST be disabled, and the
accepted monetary defaults MUST also be upper bounds. Unknown accounting and
threshold overruns MUST block further generation pending reconciliation.

Only the submitted question text and fixed non-secret instructions MAY leave
the host. Research MUST NOT accept an attachment. The application MUST NOT
fetch arbitrary result URLs. Page views, history reads and metadata saves
MUST NOT start generation.

Application thresholds MUST be Search USD 0.50, Research USD 5, daily USD 10
and monthly USD 30. A provider monthly hard limit of USD 30 MUST be required
before live use. Delayed enforcement and possible overshoot of the application
thresholds are accepted and MUST be reported rather than hidden. A budget
reservation MUST NOT be described as an absolute invoice cap. Initial limits
MUST include reasoning effort `low` for Search and `high` for Research, tool
allowlist `web_search` only, `max_tool_calls` 3 and 20, `max_output_tokens`
4096 and 32768, deadlines 180 and 1800 seconds, global concurrency 1, prompt
maximum 16384 UTF-8 bytes, answer maximum 2097152 UTF-8 bytes, and citation
count maximum 200. A kill switch MUST block new admission and request
cancellation.

Local history of questions and answers, including complete Research reports,
MUST be retained. The retrieved remote response MUST be deleted after
validated local persistence or terminal reconciliation. That deletion MUST
NOT be described as erasure of all provider state. Standard provider
retention, including possible security retention after deletion, is accepted.
Zero Data Retention MUST NOT be required.

A successful result MUST have a terminal completed provider response, a
complete final answer, no refusal or incomplete marker, and evidence that web
search executed. The question MUST render as escaped text. The answer MUST
render through a bounded Markdown and HTML allowlist. The complete original
answer MUST be preserved. A formatting failure MUST display the entire escaped
answer. Scripts, event handlers, external resources and automatic citation
fetching MUST be prohibited. Generated HTML MUST NOT be injected into the main
application DOM. New APIs MUST join the existing explicit permission table and
mutation protection.

### Parked Capture and Transition Gates

The application and the parked capture module MUST remain separate processes
communicating through the loopback bridge. Capture MUST use one persistent
Chromium on Xvfb and one dedicated profile, with no per-task launch,
automatic stealth, or model and reasoning inspection or selection. Capture
MUST NOT fall back from the ChatGPT page to an external API. That prohibition
does not forbid the separate research provider above. A web restart or bridge
outage MUST NOT restart the browser. `needs_admin` MUST pause capture work
until explicit readiness-checked continuation. A possibly sent prompt MUST NOT
be automatically resent. Administrator waiting has a separate 30-minute limit.
Manual browser starts are at least five minutes apart, without a restart loop.

The bridge MUST use a per-install token, exact Host and Origin checks, no
wildcard CORS and `127.0.0.1` binding. While S5 stays parked, a future capture
ZIP MUST accept at most one validated stored ZIP, at most 32 MiB and 256 JPEG
frames, each at most 128 KiB and 480 px on its long side, with sequential
`frame-0001.jpg` names and rejection before browser contact. That ZIP path
MUST NOT become research input.

The old test databases MUST NOT be imported. A separately authorized reset
MUST identify exact database, WAL and SHM objects, stop writers, and create
the new empty database through normal migrations. It MUST NOT remove source
media, profiles, identity configuration, secrets or archives, or rewrite
migration history. Rollback restores previous code with a compatible empty
database, not the deleted test data. Personal photos and local photo AI,
native share apps, providers other than the selected research provider and the
parked capture module, internet publication and production hardening are
outside the active sequence. The exact order and acceptance gates are in
[ROADMAP.md](ROADMAP.md). S3 host completion, capture-mode Search and
Research, S5 ZIP activation and S7-C MUST NOT be required before S4-A, S6,
S4-B, S7-P, S8, S9 or S10.

## 2. Normative Language

The terms MUST, MUST NOT, SHOULD, SHOULD NOT, and MAY are normative.

MUST means the requirement is mandatory.

MUST NOT means the behavior is prohibited.

SHOULD means the requirement is expected unless a documented reason justifies an alternative.

SHOULD NOT means the behavior is discouraged unless a documented reason justifies it.

MAY means the behavior is optional.

## 3. Product Invariants

Kronika MUST remain local-first.

Local desktop gallery, metadata, search, and playback MUST remain usable without
a remote server where their required local server process, records, and media
are available.

The premium gallery and media-acquisition workflows MUST both remain flagship capabilities.

A remote server or public cloud service MUST NOT replace local ownership or
make local desktop operation unusable when the needed local server process,
local catalog/cache records, and local media are available.

Workspace remote Kronika communication MUST remain Tailscale-only.
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
ships a local-only `public_published_uds` published-reader composition; it is
not exposed externally and has no TLS, Funnel, or NUC bind. Funnel to the
workspace socket MUST remain forbidden. Kronika MUST NOT require router
port forwarding.

Backends MUST NOT be publicly exposed by default.

Provider secrets MUST NOT be distributed to ordinary clients.

Destructive operations MUST require safeguards.

The Kronika catalog MUST remain authoritative over projected native OS tags.

## 4. Core Domain Concepts

A logical media item is the conceptual media record shown to users and connected to metadata, covers, tags, series relationships, and zero or more physical locations.

A physical media location is one known stored copy or representation of a logical media item on a device, library, storage volume, and path.

A device is a machine or storage-capable host that may contain libraries, provide playback, or participate in transfer and availability reporting.

A library is an independently registered collection root with stable identity and policy.

A storage volume is a mounted or otherwise addressable storage unit with capacity, availability, filesystem, and ownership context where available.

Standalone media is a logical item that is not modeled as an episode in a series.

A series groups related episodes and may carry inherited metadata.

An episode is media that belongs to a series relationship.

A canonical tag is an English stable identifier used by Kronika for organization.

A source platform is structured metadata describing where media originated and is also projected as the final system tag in directory display names.

A cover is a user-visible representative image for standalone media, an episode, or a series.

A cover candidate is a proposed cover source or generated image that has not
yet replaced the accepted cover.

A derived thumbnail is a reproducible cache artifact derived from a durable source.

A sidecar manifest is portable durable metadata stored near media where feasible.
The v1 sidecar schema, codec, one-location export, schema/codec validation, and
read-only compare now exist per
[ADR-0059](docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md). A
sidecar is an explicit projection of selected catalog metadata, not live catalog
write authority. Sidecar-to-catalog import, local-index rebuild, synchronization,
drift repair, and conflict resolution remain unimplemented.

A Kronika server process is the authoritative API and state boundary for the
catalog and server-owned state. It may run locally on the same device as a
desktop client or later on the Ubuntu NUC.

A client is a browser, desktop shell, local NUC browser, or remote interface
that requests state and actions from the Kronika server API.

A local catalog/cache is local server or client-side state used for local
operation and offline visibility. SQLite remains authoritative in normal
operation. Sidecar-to-catalog rebuild and synchronization semantics remain
governed by ADR-0035 and future decisions.

A transfer is a copy, move, or related operation between locations.

An availability state describes whether a device, library, storage volume, or physical location is currently reachable and usable.

A desktop shell is the native application boundary that hosts the Kronika UI, supervises the local backend, and provides narrowly scoped native capabilities.

## 5. Stable Identity Requirements

Kronika MUST define stable identities for logical media, physical locations, devices, libraries, storage volumes, and series where applicable.

Kronika entity identities MUST initially use application-owned RFC 9562 UUID version 4 values as accepted by [ADR-0011](docs/adr/0011-stable-domain-identities.md).

Kronika MUST represent identity categories with category-specific domain types rather than interchangeable raw UUID aliases.

The canonical external identity representation MUST be lowercase hyphenated UUID text.

Kronika identity strings MUST be treated as opaque values. They MUST NOT be used to infer entity type, creation time, filesystem location, database row position, source platform, content hash, or semantic ordering.

Filesystem paths alone MUST NOT serve as globally stable media identity.

Database row IDs, SQLite row order, filesystem paths, filenames, source-platform IDs, content hashes, and device-local counters MUST NOT serve as Kronika entity identity.

The final database encoding, catalog schema, synchronization representation, hashing, deduplication, and identity reconciliation rules remain unresolved. The portable sidecar v1 schema exists per [ADR-0059](docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md).

## 6. Logical Media and Physical Locations

One logical media item MAY have zero or more physical locations.

The initial persistent logical media foundation MUST represent one conceptual
media item with a stable `MediaId` and one supported media kind. Persistent
display-title and canonical-tag metadata are stored separately from physical
locations.

Initial supported persisted media kinds are `video` and `animated_image`.

The initial persistent physical media-location foundation MUST represent one
known file location inside one registered library with a stable
`MediaLocationId`, the owning `MediaId`, the owning `LibraryId`, one normalized
library-relative path, an availability state, optional observed file size in
bytes, optional observed modification time in integer nanoseconds, and
non-negative row timestamps.

Initial supported physical-location availability states are `available`,
`offline`, `missing`, `unverified`, and `archived`.

Persisted physical-location paths MUST be library-relative, slash-separated,
non-empty, free of NUL characters, free of empty path segments, and free of `.`
or `..` traversal segments. Domain and repository logic MUST NOT resolve,
traverse, or inspect the filesystem to validate these persisted paths.

The physical filename MUST be derived from the final component of the relative
path and MUST NOT be duplicated in a separate persisted filename column.

The owning device MUST be determined through the registered library. Physical
media-location records MUST NOT duplicate `device_id`.

The exact `(library_id, relative_path)` pair MUST be unique. Two different
logical media items MUST NOT claim the same registered library-relative
location.

Foreign keys from physical locations to logical media and libraries MUST use
restrictive or no-action semantics. Destructive cascading deletion is not part
of the initial catalog foundation.

The gallery SHOULD normally present one logical item rather than every duplicate independently.

Users MUST be able to inspect all known copies.

Future physical-location extensions SHOULD record storage volume and richer
verification state where available.

Offline locations MUST remain known and MUST be visibly unavailable.

Duplicate handling MUST distinguish identical media from unrelated files with similar names.

No final known valid copy MAY be deleted without explicit policy and confirmation.

## 7. Libraries and Storage

Kronika MUST support multiple independent libraries.

Each library MUST have stable identity, a root directory, and an owning device.

Libraries and storage volumes SHOULD report online/offline state and writable/read-only state where available.

Kronika SHOULD report capacity, free space, filesystem, and mount information where available.

The interface SHOULD provide `All libraries` filtering.

Transfers SHOULD provide storage-impact previews before execution.

Kronika MUST NOT destructively initialize storage without explicit approval.

## 8. Standalone and Series Model

Standalone media MAY use a dedicated directory.

Series MAY share a directory.

Episodes SHOULD inherit series-level content tags where appropriate.

Users MUST be able to correct series detection.

Merge and split migrations MUST update paths, metadata, covers, OS tags, catalog records, and physical locations safely.

Migrations MUST be previewable and verifiable.

## 9. Canonical Tagging

Canonical tags MUST use stable English identifiers.

Persistent canonical tag keys are lowercase English ASCII slugs. The key is the
stable catalog identity. A canonical tag display name is separate presentation
text. A UUID tag identity MUST NOT be introduced for this slice.

Persistent display title is user-editable catalog metadata. It MUST remain
separate from physical filename and library-relative path, and saving or
clearing it MUST NOT mutate media files.

Initial display values SHOULD be English.

Content tags MUST be distinct from system and source metadata.

The source platform MUST be structured metadata and MUST be the final system tag in directory display names.

Native OS tags are synchronized projections and MUST NOT become the authoritative source.

Tag drift MUST be detectable and repairable.

Unsupported filesystems MUST degrade clearly rather than silently.

The automatic built-in `Processed` workflow collection is derived from durable
metadata saves, not from AI, VLM submission, suggestion generation, physical
rename, import, or filesystem time. The first successful durable metadata save
whose complete ordered tag list contains at least one canonical tag enters a
medium into `Processed` and records `processed_at_ms` as the current transition
time. Non-empty tag edits, title edits, and description edits MUST preserve an
existing `processed_at_ms` exactly. Removing all tags MUST clear `Processed`
membership and set `processed_at_ms` to null. Later re-tagging MUST assign a new
`processed_at_ms`. One medium MUST hold zero or one collection membership, the
only supported persisted collection key is `processed`, and no arbitrary
collection creation, rename, deletion, or manual assignment API exists. VLM
submission, AI suggestion generation, and physical rename MUST NOT change the
processing timestamp. Catalog metadata saves MUST NOT mutate media files.

Approved directory example:

```text
Reinventing Entropy - Compression is Intelligence [Math] [Compression] [YouTube]
```

## 10. Portable Naming

The title MUST remain the primary sort prefix.

Tags MUST remain at the end of display directory names.

The source platform MUST remain last.

Unsafe or non-portable characters MUST be normalized.

Collisions MUST be detected.

Renames MUST be planned before execution.

Cross-platform compatibility MUST include macOS, Linux, Windows, and common shared filesystems.

The exact normalization algorithm remains an ADR or design decision.

## 11. Metadata Durability

Portable sidecar manifests are durable metadata. The v1 explicit projection
schema, adjacent export, validation, and read-only compare now exist. SQLite
remains authoritative in normal operation. Sidecars do not overwrite the
catalog. Ordinary metadata Save does not write sidecars. Missing sidecars do
not invalidate catalog state.

Earlier index/cache language does not make the live catalog disposable during
normal operation. The existing database is authoritative; the accepted
one-time empty-database transition in ADR-0082 is limited to unwanted test data
and does not implement sidecar rebuild or import.

The local index SHOULD be rebuildable from durable metadata where feasible.
Sidecar-to-catalog import, rebuild, synchronization, drift repair, automatic
Save projection, and conflict resolution remain unimplemented.

Live SQLite databases MUST NOT be synchronized between devices as the sole durable metadata mechanism.

Sidecar writes MUST use versioning, validation, and atomic replacement.

The exact later import, rebuild, and synchronization contracts remain unresolved.

## 12. Dates and Provenance

Kronika MUST distinguish source publication or creation date, archive-added date, downloaded date, imported date, first-seen date, filesystem creation time, and filesystem modification time.

These concepts MUST NOT be silently conflated.

Metadata provenance SHOULD be retained where practical.

## 13. Cover Requirements

Kronika MUST support cover concepts for standalone media, episodes, and series.

Cover selection SHOULD support timeline frame selection and image import.

The original approved cover SHOULD be durable.

A selected timestamp MUST be stored in a stable time unit when a cover comes from media.

Derived thumbnails MUST be reproducible cache artifacts.

Frame number alone MUST NOT be the only durable reference.

### Durable Manual Cover Foundation (Implemented)

The first durable manual cover foundation is implemented through migration
`0022` and [ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md). It MUST:

- keep at most one currently accepted cover per logical medium, with sparse
  absence meaning no accepted cover;
- allow manual cover selection only from an existing available GIF, MP4, or
  still-image (JPEG/PNG) physical location; a still-image source is timeless,
  has no selectable timeline, and uses the canonical source timestamp `0`,
  which is never presented as a selected temporal frame;
- require explicit acceptance such as `Set as cover`; a previewed frame MUST
  NOT become the accepted cover;
- persist an exact integer-millisecond source timestamp and never use frame
  number as the durable reference;
- keep an accepted cover authoritative until replacement fully succeeds;
  cancellation, extraction failure, storage failure, database failure,
  authorization failure, a changed source, or a stale request MUST leave it
  unchanged;
- fence stale browser tabs with optimistic concurrency so a newer accepted
  cover is never silently overwritten;
- retain durable cover facts when the physical source location disappears,
  keeping the artifact usable without rereading the original;
- maintain strict separation between original media bytes, the durable accepted
  cover artifact (`KRONIKA_COVER_STORAGE_ROOT`), the regenerable
  `cover-thumbnail-jpeg-v1` derivative (`KRONIKA_COVER_THUMBNAIL_CACHE_PATH`),
  ephemeral frame previews, and playback position;
- keep normal Play starting at `00:00`, with cover timestamps never becoming
  playback state;
- require `metadata.canonical.write` for authoring, `gallery.read` plus the
  shared content-publication audience policy for ordinary cover thumbnail
  reads, and record `media.cover_set` audit before the accepted-cover mutation;
- expose no absolute paths, library roots, cache paths, or source filenames in
  any cover surface;
- compute Gallery `cover_ready` only from validated present cover thumbnails and
  never generate thumbnails on Gallery or read surfaces.

AI-generated covers are future scope.

An existing approved cover MUST NOT be overwritten without confirmation.

Cover provenance SHOULD be retained.

The manual cover workflow MUST require explicit acceptance such as `Set as
cover`.

A created or generated cover candidate MUST NOT become active automatically.

Only explicit human acceptance MAY activate a cover candidate and replace the
previous accepted cover.

Cover timestamp MUST NOT change normal playback start.

Ordinary Play MUST start at `00:00`; seeking to a cover timestamp is a separate
explicit action if offered later.

Final image formats and thumbnail sizes remain unresolved.

## 14. Gallery Requirements

The gallery MUST provide a premium dark and cover-driven experience.

For the pre-transition implementation only, ordinary Gallery queries return
only media with durable content-publication truth. Public query parameters
MUST NOT widen that audience.
Unpublished media MUST be indistinguishable from unknown media to callers
without the explicit administrator workflow-read capability.

ADR-0083 replaces that audience rule for Kronika records. Gallery may include
authorized unanalyzed media. An authenticated application administrator can
read Kronika product records, including private and unfinished work; that
privilege is application content only. The shared Timeline contains only
administrator-approved records. Timeline eligibility is separate from Gallery
visibility and from internet publication.

Catalog membership, `Processed`, AI-analysis state, upload storage publication,
and content publication MUST remain distinct. Content-publication readiness
requires a persisted trimmed title, a persisted trimmed description, and at
least one canonical tag. Publication MUST be explicit, single-item, atomic,
conditional, idempotent, and durable across later metadata regression.

The gallery SHOULD scale to large libraries through rendering strategies such as lazy loading and virtualization.

Search and filtering MUST be supported.

Filters SHOULD include libraries, devices, platforms, canonical tags, media type, series, and availability.

Multiple selected canonical tags MUST default to AND/intersection semantics. OR behavior MAY be added later only as an explicit advanced mode.

Card and series views SHOULD be supported.

Remote availability, transfer state, and download state SHOULD be visible.

Remote-only logical media SHOULD remain visible through metadata, covers, derived thumbnails, and availability summaries when those lightweight records are available without full media bytes.

Inline preview for short media SHOULD be supported separately from full playback.

Keyboard, mouse, and touch interaction SHOULD be supported.

Accessibility, reduced motion, reduced transparency, and lower-resource modes MUST be considered.

This document does not select a frontend framework.

## 15. GIF, Short Media, and MEME Requirements

Kronika SHOULD support GIF files and short videos as first-class media.

MEME collections SHOULD be first-class collections rather than secondary gallery content.

Inline looping preview SHOULD be supported.

Copy to Clipboard MAY be supported where platform capabilities permit.

Download and Download + Copy actions MAY be supported for remote media.

`Download + Copy to Clipboard` MUST verify any downloaded representation before placing it on the native clipboard and MUST provide fallback behavior when the platform or target application does not support direct paste.

Heuristic classification MAY be used, but users MUST be able to override it.

Kronika MUST NOT automatically upload short media or frames to an LLM provider
unless server-owned automatic post-catalog analysis is explicitly enabled
(`KRONIKA_AUTOMATIC_MEDIA_ANALYSIS_ENABLED` or the administrator companion
Settings overlay in
[ADR-0079](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md)).
That enablement is the consent
boundary for non-interactive cloud frame upload of newly cataloged media only.
Interactive suggestion preview continues to require explicit per-request
confirmation. Automatic analysis MUST NOT invent paid work for historical
catalog rows, MUST NOT roll back cataloging on provider failure, and MUST
persist a truthful sanitized lifecycle. A durable run left in `analyzing`
after process interruption MUST be treated as an ambiguous provider outcome
and MUST fail closed to terminal `failed` with sanitized
`ANALYSIS_OUTCOME_UNKNOWN` rather than automatically returning to `pending`
when that could repeat a possibly paid provider call. Kronika MUST NOT claim
generic exactly-once provider execution.

Full playback MUST remain separate from inline preview.

## 16. Media Acquisition

Media acquisition MUST use an adapter-based source architecture.

yt-dlp is the intended first adapter.

Domain logic MUST NOT depend directly on yt-dlp.

Acquisition SHOULD support metadata inspection, progress, cancellation, temporary or incomplete state, collision-safe filenames, safe final rename, structured errors, finalization verification, and source identity/archive tracking.

Kronika SHOULD NOT perform unnecessary transcoding by default.

The exact format policy remains unresolved.

## 17. Playback

Kronika MUST define a `MediaPlayerBackend` conceptual boundary.

External VLC is the first intended full-playback backend.

Playback SHOULD support local paths and authorized remote URLs.

VLC availability SHOULD be checked and failures SHOULD be reported clearly.

Embedded libVLC is future scope.

An `InlinePreviewBackend` MUST remain separate from full playback.

Domain logic MUST NOT hardcode VLC command construction.

Future playback SHOULD support fullscreen and truthful audio-track selection
where technically supported. Playback UI MUST use capability detection and
fallback rather than fake controls. Subtitle support is not currently required.
Kronika MUST NOT silently transcode originals.

This document does not define implementation syntax.

## 18. Authoritative Server and Client State

The Kronika server process MUST be authoritative for catalog records and
server-owned state.

Server-owned state includes server media originals, durable content-publication
state, canonical display title, description, canonical tags, future category
and language metadata, per-user visibility state, upload and ingest state,
server preview cache, authentication, and capability decisions.

Browser, desktop, local NUC browser, and future remote interfaces MUST be
clients of the server API. Clients MUST NOT infer administrator authority from
loopback, source IP, hostname, Tailscale membership, cookies, or same-machine
execution.

The server MAY run locally on the same device as a desktop client or remotely
on the Ubuntu NUC after deployment acceptance. Local-first operation MUST NOT
require public cloud dependence.

Local desktop operation SHOULD remain useful when the needed local server
process, local catalog/cache records, and local media are available.

Metadata, covers, availability state, and lightweight derived thumbnails MAY synchronize independently of full media bytes.

Kronika MUST NOT automatically replicate all media bytes to every device.

Explicit streaming, opening, or download of authorized media MAY be supported
without registering trusted permanent client-local availability.

Authenticated server-managed ingest/upload, catalog synchronization, explicit
client cache/download, per-user visibility state, server deletion requests,
global retirement, and physical purge of originals MUST remain distinct
operations.

The trusted-loopback upload foundation MUST use server-selected placement and
MUST include quarantine, validation, limits, safe server-owned names, duplicate
detection, atomic no-replace publication, and cleanup recovery. Clients MUST
NOT select arbitrary server filesystem paths. Publication MUST require an
explicit server-configured registered destination; absent configuration MUST
leave eligible work safely at `publish_pending`. Upload state `published` MUST
prove verified server-owned bytes and durable provenance but MUST NOT imply
catalog or Gallery visibility. A specialized atomic `published -> cataloged`
transaction MUST create logical media, a physical location, and
upload-publication linkage together before content can be published. Ordinary
Gallery eligibility additionally MUST require a durable content-publication row
per [ADR-0049](docs/adr/0049-durable-content-publication-boundary.md). Mapped
ordinary Tailscale users MAY complete direct uploads under capability
`upload.submit` per
[ADR-0053](docs/adr/0053-ordinary-user-upload-submission-and-administrator-review-boundary.md);
resulting catalog rows MUST remain content-unpublished until administrator
publication, and ordinary duplicate matches MUST NOT disclose matching
evidence or enter an externally observable `duplicate_pending` state.
Administrator `upload.manage` retains explicit duplicate keep/discard.
Catalog
persistence failure MUST leave
	the upload truthfully `published` and MUST NOT delete the durable published
	file.

Owner-operated YouTube manual ingestion MUST remain a source-specific,
loopback-only CLI and server lifecycle. The server MUST independently validate
one supported cookie-free public HTTPS YouTube video identity, persist an
immutable acquisition claim, use a shell-free pinned downloader subprocess in
a private claim-owned staging root, and hand only a stable exact-size artifact
to the existing upload transport. It MUST NOT bypass quarantine, validation,
canonical byte identity, publication, or catalog creation. Source-identity and
byte duplicates MUST reuse existing outcomes without creating accidental
logical media, while preserving each claim. YouTube ingestion MUST NOT use
cookies, browser profiles, arbitrary extractors, playlists, live media,
sidecars, transcoding, or automatic AI analysis.

Per-user Trash MUST be server-persisted visibility state and MUST NOT delete
the server original.

Administrator catalog removal MUST hard-delete one active logical-medium
aggregate after an authoritative consequence preview and fingerprint
confirmation, MUST retain all original media bytes, MUST persist a durable
removal receipt, MUST transition linked YouTube claims to `catalog_removed`,
MUST detach upload-publication catalog linkage without trapping identical
re-uploads, and MUST NOT implement Trash, soft-delete tombstones, batch
removal, or physical purge. See
[ADR-0051](docs/adr/0051-administrator-catalog-removal.md).

The synchronization protocol remains unresolved.

## 19. Network Boundary

Local-only functions need no Tailscale.

Workspace remote and cross-device functions MUST use Tailscale in the approved
direction unless explicitly superseded by an approved decision.
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
is that supersession only for a second, local-only `public_published_uds`
published-reader composition. That composition is implemented locally and is
not exposed externally.

An application backend SHOULD bind to loopback when exposed through Tailscale Serve.

Kronika MUST NOT require router port forwarding.

Kronika MUST NOT use Tailscale Funnel against the workspace socket
`/run/kronika/kronika.sock`.

The normal application process MUST NOT invoke privileged Tailscale provisioning on every startup.

Network identity alone is insufficient authorization.

Application capabilities must later be defined.

This document does not place current Tailscale command syntax in the specification.

### Pre-transition dual-audience contract

Historical direction and existing implementation contract recorded in
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md).
ADR-0082 supersedes public rollout for new Kronika records. ADR-0083
supersedes the ADR-0082 denial of administrator private-content reading and
keeps internet publication disabled. The following clauses describe the
retained baseline, not an instruction to enable its public composition or
expose new records. Household Timeline approval is not this publication path.
The local-only `public_published_uds` published-reader is implemented-for-backend.
These remaining dual-audience requirements MUST NOT be read as shipped public
bind, TLS, Funnel, or NUC exposure, except the sole publication-gate
requirement immediately below, which is implemented-for-backend.

The workspace remote path MUST remain authenticated Tailscale Serve to
`/run/kronika/kronika.sock` and `tailscale_uds`.

Administrator `PUT /api/admin/media/{media_id}/content-publication`, guarded
by `media.content.publish`, is the baseline's sole promotion and
unpublication path for every media type, including movies
(implemented-for-backend). The same PUT MUST treat an omitted body as
publish-preserving and `{"published": false}` as unpublication, returning
truthful `published`, `already_published`, `unpublished`, or
`already_unpublished` without mutating media bytes, metadata, aliases,
analysis state, or history rows (implemented-for-backend). Companion Apply
MUST keep metadata writes and MUST NOT publish.

Public callers MUST be identity-absent. Public capabilities MUST be exactly
`gallery.read` and `media.original.read`.

Workspace capabilities MUST add `media.workspace.read` for ordinary and
administrator roles (implemented-for-backend). `analysis.propose` for
ordinary and administrator roles (implemented-for-backend). Administrator-only
`metadata.alias.team.read` (implemented-for-backend).
Workspace unpublished reads MUST use a contributor-scoped
audience-extension model without ownership columns or personal libraries
(implemented-for-backend for the workspace media list, upload-attributed
content reads, and administrator contribution filter).
`POST /api/workspace/media/{media_id}/analysis-proposals`, guarded by
`analysis.propose`, MUST create a durable administrator-visible proposal and
audit event without calling a provider, enqueueing analysis, or toggling
automatic analysis (implemented-for-backend). Duplicate proposals for the
same medium MUST each create their own row. Unknown media MUST return the
same sanitized not-found as other catalog misses.
`GET /api/admin/analysis-proposals` MUST list open proposals newest first
under administrator `media.workflow.read`, including proposer login, media
title snapshot, publication/readiness, and status
(implemented-for-backend). Public callers MUST receive `404` for both
routes.
`GET /api/admin/media/{media_id}/aliases` MUST require both
`media.workflow.read` and `metadata.alias.team.read`, return the aggregated
caller-private overlays for that media without writing, and record a distinct
audit action (implemented-for-backend). Ordinary users MUST retain only their
own alias route. Public callers MUST receive `404`. Aliases MUST NOT appear
in public or ordinary workspace catalog payloads.
`GET /api/media/{media_id}/ai-suggestions` under `metadata.alias.write` MUST
remain the workspace per-field AI suggestion read route for ordinary and
administrator suggestion review; the newest stored run MUST be visible when
Edit opens, dropdown selection MUST switch the revealed suggestion without a
Load step or provider call, the website reads only, and no apply route exists
from this surface (implemented-for-backend).

## 20. Transfers and Duplicate Removal

Kronika SHOULD support Copy, Move, and Move while retaining lightweight local metadata.

Transfers MUST verify destination size, integrity, and readability where feasible before source deletion.

Source deletion MUST occur only after successful validation.

Full media transfer MUST be explicit or governed by a later accepted automation rule.

Operations SHOULD provide previews, progress, cancellation, transaction or audit state, final-copy protection, and clear partial-failure recovery.

This document does not choose a checksum algorithm.

## 21. Search and Filtering

Search and filtering SHOULD include canonical tags, platform, library, device, availability, duration, size, archive-added date, source date, media type, series, MEME classification, and local/remote state.

The search engine remains unresolved.

Title/name search SHOULD be supported by the first persistent gallery-capable catalog. The initial local SQLite catalog browser supports persisted display-title substring search and repeated canonical-tag filters with AND semantics as a bounded read-only slice. Multiple selected tags MUST use AND semantics by default. `All media` is a virtual Catalog scope and is not stored as a collection. An optional `collection=processed` query parameter restricts a catalog page to members of the built-in `Processed` workflow collection; All-media requests MUST omit it.

Future first-class categories SHOULD include `memes`, `youtube`, and `movies`.
Categories MUST be modeled as a dedicated facet rather than only canonical tags
or directory names once category persistence is implemented.

Movies MAY carry explicit language metadata such as English, Slovak, or Czech.
Language metadata SHOULD prefer container or audio metadata and user editing
before expensive AI analysis. Kronika MUST NOT automatically upload audio to
a cloud provider.

## 22. AI and Privacy

AI features MUST use replaceable provider adapters when implemented.

AI assistance MUST be optional.

Cloud operations MUST be explicit opt-in.

Users SHOULD understand what payload is sent.

Provider secrets SHOULD remain server-held where possible.

Provider secrets MUST NOT be returned to ordinary clients.

Server AI provider administration MUST use an operator boundary: the CLI
(`./kronika ai ...`) plus an authenticated administrator-only web surface
reached through `provider.operate` over the trusted Tailscale workspace
ingress. Ordinary browser clients MUST NOT configure AI providers, activate
models, enter provider API keys, receive provider API keys, or call external AI
providers directly.

Server AI configuration files MUST contain only schema-versioned non-secret
provider/model selection, declarative provider records (provider id, display
name, protocol, base URL, credential environment-variable **name**, declared
models, and declared capabilities), and safe timestamps. They MUST NOT contain
API keys, Authorization headers, cookies, provider responses, prompts, frame
data, media paths, or database paths.

AI configuration precedence MUST preserve deployment overrides: explicit
Kronika provider/model environment overrides first, then persisted non-secret
server AI configuration, then legacy NVIDIA compatibility when `NVIDIA_API_KEY`
is present and no explicit provider configuration exists, then unconfigured.

`./kronika ai status` MUST be network-free. `./kronika ai test` MUST be an
explicit text-only provider request, upload no media data, and persist only a
safe last-test category and timestamp.

Administrator-initiated `ping` MUST be an explicit text-only provider request;
administrator-initiated `pong` MUST send exactly one repository-owned synthetic
fixture image and MUST require explicit cloud-upload confirmation; neither MUST
use catalog media, persist a suggestion, or run without an explicit
administrator action. Analyze and the vision probe MUST refuse a selected model
that does not declare `vision_input`. A provider HTTP `403` MUST be reported as
credential or entitlement rejection, never as invalid output. Provider
declarations MUST be `https://` only for non-loopback hosts; local-gateway base
URLs remain deferred.

NVIDIA NIM and Vercel AI Gateway remain supported built-in server providers,
and the server supports operator-declared OpenAI-compatible provider records
(first instance: OpenCode Go at `https://opencode.ai/zen/go/v1`) persisted as
non-secret schema-versioned configuration. Vercel AI Gateway uses
`AI_GATEWAY_API_KEY` and preferred model `google/gemini-3.1-flash-lite`.
NVIDIA NIM keeps the existing `NVIDIA_API_KEY` credential boundary and default
model.

Suspicious filename analysis MUST occur only on user action.

Representative-frame selection MAY be used later.

Title and tag suggestions MUST require confirmation.

Kronika MUST NOT automatically rename media.

Kronika MUST NOT perform unsolicited cloud frame upload. Server-enabled
automatic post-catalog analysis is solicited only through that explicit
server-owned configuration boundary and MUST remain off by default.

Prompts MUST NOT include unrelated paths, cookies, keys, or logs.

Opening a media detail view MUST NOT trigger an AI call, catalog save, or
filesystem mutation.

Kronika MUST distinguish persisted catalog values, unsaved `Current` manual
working state, physical filename, library-relative path, AI draft, promoted
draft values, accepted current-page values, durable catalog save, suggested
filename, and future explicit physical rename operation.

Changing display title MUST NOT rename a file. Editing suggested filename MUST
NOT rename a file. Catalog save MUST NOT implicitly rename, move, delete, tag,
or reorganize media files.

`✅` or an equivalent per-field promotion action MUST copy selected AI draft
values into `Current`; it MUST NOT save to the catalog or mutate the filesystem.

AI drafts MUST NOT be automatically promoted, saved, tagged, renamed, or
assigned to a collection.

Canonical-tag editing SHOULD provide searchable local suggestions, keyboard and
mouse navigation, rounded removable chips, an explicit `×` control, visible
hover/focus/selected/AI-suggested/invalid states, case-insensitive duplicate
prevention, optional controlled new-tag creation, and immediate local filtering
without progress UI for trivial local filtering.

Capability-aware model filtering SHOULD use provider-neutral concepts such as
`vision_input`, `video_input`, `structured_text_output`, `image_generation`,
`image_editing`, `reference_image`, `local_execution`, and `cloud_execution`.
Browsing or highlighting models MUST NOT invoke a provider.

## 23. Secrets

Secrets MUST NOT be committed to Git.

Secrets MUST NOT be stored in plaintext sidecars.

Secrets MUST NOT be stored in ordinary SQLite fields.

Secrets MUST NOT be logged.

Secrets MUST NOT be stored in browser local storage.

Native or server secret storage requires a dedicated decision.

Replacement and removal workflows MUST NOT reveal the full stored secret.

## 24. Structured Errors and Logging

Kronika SHOULD use a structured component, operation, and error-code model.

Messages MUST be sanitized.

Tool and platform context SHOULD be included where safe.

Errors SHOULD describe retryability where possible.

Secrets MUST NOT be logged.

Log retention SHOULD be bounded.

Support bundles MUST be reviewed before sharing.

Cloud AI diagnostics require sanitization and user awareness.

## 25. Destructive Operations

Destructive operations include delete, move, deduplication, database clearing, library removal, global tag deletion, cover overwrite, final-copy deletion, and storage formatting.

Destructive operations MUST require previews, confirmations, verification, and rollback or recovery information where feasible.

## 26. Cross-Platform Requirements

Apple Silicon macOS is the first development and test environment.

Ubuntu Server 24.04 on the Intel NUC6i5SYH is the current Kronika
development-and-testing machine
([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)):
its accepted role remains development/test for this one product, routinely
refreshed toward public `main`. Capture is a planned separate process in that
product. ADR-0082 limits reset to the unwanted test databases under separate
authority; current host state MUST be verified by preflight.

Broader macOS, Linux, and Windows portability remains required.

Browser/PWA access remains conceptual direction.

Platform adapters MUST isolate OS-specific behavior.

### Desktop Shell Requirements

Kronika MUST become a desktop application for normal end-user use. Browser mode MAY remain for development, diagnostics, and controlled operator workflows, but it MUST NOT be the required normal end-user experience.

Tauri v2 is the accepted future desktop shell per [ADR-0021](docs/adr/0021-tauri-desktop-shell.md).

The desktop shell MUST display the Kronika UI in a native system WebView and MUST supervise the Python/FastAPI backend as a sidecar rather than duplicating domain or catalog logic.

The sidecar backend MUST bind only to loopback by default.

The desktop shell MUST enforce a single-instance lifecycle.

The desktop shell SHOULD provide a tray or macOS menu-bar presence with initial `Gallery`, `Settings`, and `Quit` actions.

Closing the main window SHOULD hide the window rather than terminate the application. `Quit` MUST stop the supervised backend cleanly.

Native capabilities MUST be least-privilege and allowlisted. The WebView MUST NOT receive unrestricted filesystem, shell, process, or arbitrary-command access.

Native file selection, directory selection, save/export destinations, file-manager reveal, VLC opening, notifications, and clipboard operations MAY be exposed only through explicit scoped capabilities.

MacBook-first implementation is accepted, but platform-specific behavior MUST remain isolated so later Windows and Linux validation remains possible.

### Distributed Media Requirements

Selective media placement direction is recorded in
[ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md).
The authoritative server/client state model is recorded in
[ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md).

The Intel NUC server MAY act as an authoritative Kronika server, archive
node, remote streaming/download source, transfer receiver, centralized
AI-provider boundary, and future backup participant after deployment
acceptance.

The NUC MUST NOT make Kronika a public-cloud dependency or make local clients
unusable when the needed local server process, local catalog/cache records, and
local media are available.

Remote-only media cards SHOULD be visible from metadata and covers without requiring full media download.

Workspace future remote access MUST follow the Tailscale-only direction unless
explicitly superseded. [ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
ships a local-only `public_published_uds` published-reader composition; it is
not exposed externally.

### Progress Requirements

Determinate operations SHOULD show real transferred bytes, total bytes, percentage, speed, ETA, and verification/finalization state when available.

Indeterminate operations MAY use restrained animation, shimmer, masked text, pulse, or fade effects, but MUST NOT show fabricated percentages, invented backend stages, or false cancellation.

### Supported Python Runtime

Kronika MUST target CPython 3.13 as the initial supported runtime.

Future project metadata MUST constrain supported Python to an equivalent of `>=3.13,<3.14` unless a later ADR supersedes [ADR-0001](docs/adr/0001-supported-python-version.md).

Local tests, continuous integration, and server deployment verification MUST run under Python 3.13.

Project commands MUST use an isolated project environment independent of a developer's global Python installation once project tooling exists.

The exact 3.13 patch release is pinned by accepted project tooling and ADR-0001; it MAY advance within the 3.13 series after tests pass.

### Python Dependency and Environment Management

Kronika MUST use Poetry as the Python dependency and virtual-environment manager per [ADR-0002](docs/adr/0002-python-environment-and-dependency-manager.md).

Future Python project metadata MUST be stored in `pyproject.toml`.

The Kronika application MUST commit `poetry.lock` for reproducible development, test, continuous integration, and deployment dependency resolution.

Python dependencies MUST NOT be installed globally for Kronika.

Project commands MUST execute through the Poetry-managed isolated CPython 3.13 environment.

Lockfile updates MUST occur only in explicit bounded tasks and MUST be accompanied by test evidence and verification that `pyproject.toml` and `poetry.lock` remain synchronized.

Local tests, continuous integration, and Ubuntu deployment MUST install from the committed lock-based Poetry environment.

Poetry virtual-environment location remains unresolved until the authorized scaffold task.

### Repository Layout

Kronika MUST use the hybrid staged monorepo layout per [ADR-0004](docs/adr/0004-repository-layout.md).

The initial Python project MUST live at repository root with root `pyproject.toml`, root `poetry.lock`, `src/kronika/`, root `tests/`, and `docs/`.

Poetry package mode MUST be used. The importable package name MUST be `kronika`.

The initial internal boundaries MUST be `domain`, `application`, `adapters/api`, and `infrastructure` under `src/kronika/`.

`domain` MUST NOT import FastAPI or infrastructure adapters. `application` MUST NOT depend on FastAPI route objects. `adapters/api` MAY depend on FastAPI and application interfaces.

Tests MUST be organized under `tests/unit/`, `tests/integration/`, and `tests/contract/` and MUST reflect these boundaries.

Package imports MUST be resolved through the Poetry-managed installed project environment. Reliance on `sys.path` manipulation MUST NOT be the standard import mechanism.

Empty future web or desktop scaffolds MUST NOT be created. Future `web/` and `desktop/` top-level boundaries MAY be added only when real implementation work begins.

### Configuration Strategy

Kronika MUST use a layered configuration model with explicit precedence per [ADR-0005](docs/adr/0005-configuration-strategy.md).

Precedence from lowest to highest authority MUST be: safe program defaults; optional committed non-secret configuration; ignored local `.env` values; process environment variables; future approved secret-store values.

The `.env` layer MUST be applied only when explicitly requested through `KRONIKA_ENV_FILE` or an explicit `load_settings(env_file=...)` argument. Configuration loading MUST NOT implicitly probe the caller's working directory for a `.env` file. An explicitly requested environment file that is missing, unreadable, or unloadable MUST fail closed with a sanitized error that does not disclose the file path or contents.

The default server host MUST be `127.0.0.1`. Public bind addresses MUST NOT be the default.

Configuration MUST be loaded through a centralized boundary. Application and domain logic MUST NOT read environment variables directly throughout the codebase. The domain layer MUST remain independent of the concrete configuration library.

Configuration values MUST be typed and validated. Invalid configuration MUST fail clearly with sanitized errors.

Secrets MUST NOT be committed or stored in normal version-controlled configuration. Secret values MUST be redacted from representations, logs, errors, and ordinary API responses.

Local `.env` files MUST remain Git-ignored. Process environment variables MUST override `.env` and lower-precedence values. Production deployment MUST NOT depend on a developer `.env` file.

Tests MUST use deterministic, isolated configuration state and MUST NOT rely on the developer's real shell environment.

### Initial Server API Framework

Migration `0019` adds the source-specific YouTube acquisition claim per
[ADR-0046](docs/adr/0046-youtube-manual-ingestion-and-provenance.md). The
server-owned coordinator reuses the existing upload lifecycle and suppresses
automatic analysis for this source.

Migration `0027` adds the semantic `youtube` content category, structured
creator attribution fields, and immutable ordinary-Save treatment of
`acquisition_source` per
[ADR-0055](docs/adr/0055-youtube-creator-taxonomy-and-immutable-provenance.md).
It performs no historical backfill.

Migration `0028` adds requester-private X meme acquisition. First-release X
support is native X video and animated-GIF-like media delivered as video.
X acquisition is requester-private with
explicit administrator promotion/publication. Ordinary and unmapped X, and
YouTube, MUST remain fail-closed for automatic analysis. Administrator-owned X
MAY enqueue generic analysis when
`KRONIKA_AUTOMATIC_MEDIA_ANALYSIS_ENABLED` is true or the administrator
runtime overlay is enabled per
[ADR-0066](docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md)
and
[ADR-0079](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md).
Companion review apply MAY publish with origin `companion_review` when
readiness holds per
[ADR-0068](docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md).
NIM completion MUST NOT publish. Persisted source-derived metadata for
`acquisition_source = x_manual_claim` is protected through normal Save
for acquisition source and X creator attribution (kind, stable ID where
present, handle where present, and display name where present). Canonical
`content_category` on X media uses the claim’s requested value when present,
otherwise the media-type default, and may be corrected by an administrator per
[ADR-0064](docs/adr/0064-x-save-category-and-public-photo-acquisition.md) and
[ADR-0065](docs/adr/0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md).
There is no separate X content category. `x_author` remains schema-compatible creator attribution.

Migration `0029` adds a caller-private per-user media alias overlay and pending
alias rows on X claims per
[ADR-0062](docs/adr/0062-per-user-media-alias-overlay.md). Authenticated Gallery
and Details display the caller's overlay when a non-empty row exists; missing
overlay fields and anonymous/public callers remain canonical `media_metadata`.
First catalog may seed canonical title,
description, and selected existing tags from that pending alias per
[ADR-0065](docs/adr/0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md);
later Save updates the caller-private overlay only.

Migration `0030` adds nullable claim-level `requested_content_category` and
public JPEG/PNG X photo acquisition with source-key/variant continuity per
[ADR-0064](docs/adr/0064-x-save-category-and-public-photo-acquisition.md).
The new extension omits `content_category`; old explicit clients remain
compatible. WebP photographs are
rejected without transcoding. Administrator metadata Save may correct canonical
category; acquisition source and X creator provenance remain immutable.

Migration `0031` adds companion-review open-state and field-source receipts and
extends content-publication origin to `companion_review` per
[ADR-0067](docs/adr/0067-administrator-companion-review-inbox-and-mutation-trust.md)
and
[ADR-0068](docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md).
This whole does not deploy that schema or enable automatic analysis on the
Ubuntu NUC.

Migration `0032` adds `companion_review_tag_sources` for per-tag companion
review provenance without historical backfill.

Migration `0033` adds `media_analysis_proposals` for durable ordinary-user
analysis proposals without provider execution or enqueue.

The companion review surface MUST expose `GET /api/companion/review-inbox`,
`GET /api/companion/review-inbox/{media_id}`,
`GET /api/companion/own-history`,
`POST /api/companion/review-inbox/{media_id}/opened`, and
`POST /api/companion/review-inbox/{media_id}/apply`. The side panel MUST render
one title-bar history (`#review-history-toggle`,
`#review-history`, `#review-history-list`) per
[ADR-0076](docs/adr/0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md).
Administrator inbox items MUST be analyzed-only. Ordinary own-history MUST list
only that actor’s cataloged X Saves. Inbox items MUST include `created_at_ms` and
`analyzed`; `analysis_run_id` and
`completed_at_ms` MUST be null only when `analyzed` is false. History MUST start
collapsed without a persisted preference, preserve server order across every
cursor page, and fail the complete list rather than expose partial titles on a
later-page error or repeated cursor. Empty history MUST consume no height.
Clicking a row MUST NOT remove it. Every history row MUST post hosted
`open_details` without gating the iframe on opened HTTP. Pending rows MUST NOT
send an opened mutation. The hosted iframe MUST
remain mounted. A row MUST mark opened only after durable opened success and
refresh; Review Save MUST ensure opened before Apply and MUST retain selections
and block Apply when that opened retry fails. Successful connection MUST render
no status line; failure guidance, `Cleared`, and `Attached` remain visible. The
toolbar badge MUST derive only from server `unopened_count`; pending rows MUST
NOT increment it. Ordinary identities MUST receive own-history rather than inbox
list/detail/apply. Exactly five `companion_mutation` routes exist: X submit, X
retry, review opened, review apply, and
`PUT /api/admin/settings/automatic-analysis`. GET inbox and GET own-history routes MUST
work with an empty
`companion_extension_origins` allowlist. Mutations that carry the extension
Origin MUST fail closed when that allowlist is empty. `GET /api/canonical-tags`
with `surface=x-companion-save` MUST best-effort seed the fixed `x` / `𝕏` pair
without failing the list. Companion apply MUST preserve current tags and append
submitted mapped AI keys, MUST refuse an empty Tags selection, MUST map only
existing catalog tags, MUST follow the live generic v4 1–5 submitted-tag
contract per
[ADR-0069](docs/adr/0069-five-tag-generic-media-suggestion-contract.md), MUST
reject a combined vector above 32 tags with HTTP 409
`COMPANION_REVIEW_TAG_LIMIT_CONFLICT`, and MUST exclude movie media per
[ADR-0070](docs/adr/0070-companion-exclusion-of-movie-workflows.md). Ordinary
`GET /api/media` MUST remain published-only. Website Publish MUST remain
`admin_explicit`.

Kronika MUST use FastAPI for the initial server HTTP API adapter per [ADR-0003](docs/adr/0003-initial-server-api-framework.md).

Kronika MUST use synchronous SQLAlchemy 2.x Core with Alembic for the initial local SQLite persistence and migration foundation per [ADR-0010](docs/adr/0010-initial-persistence-foundation.md). SQLAlchemy ORM mapped entities, SQLModel, and async SQLite access are not accepted for the initial foundation. A minimal migration foundation and explicit database command boundary exist. A minimal device domain entity and local device registry exist per [ADR-0012](docs/adr/0012-initial-device-registry.md). A minimal library domain entity, device-local root-locator model, and local library registry exist per [ADR-0013](docs/adr/0013-initial-library-registry.md), with canonical UUID text storage for catalog tables and lexical device-local root paths represented by explicit `posix` or `windows` flavor plus canonical absolute path text. A minimum persistent logical-media and physical-location foundation exists per [ADR-0025](docs/adr/0025-minimum-persistent-media-catalog-foundation.md), with canonical UUID text storage, explicit repository boundaries, migration `0004`, and no automatic migration. Persistent display title and canonical content tags exist per [ADR-0027](docs/adr/0027-persistent-display-title-and-canonical-tags.md), with migration `0005`, sparse metadata rows, ordered tag assignments, stable English tag keys, and no file mutation. A nullable plain-text description column is added in migration `0006`. An automatic built-in `Processed` workflow collection is added per [ADR-0030](docs/adr/0030-automatic-processed-collection.md), with nullable `collection_key` and `processed_at_ms` columns added in migration `0007`, one medium holding zero or one collection membership, the built-in `processed` key as the only supported collection key, and no arbitrary collection CRUD or general collection manager. A dedicated catalog read model exists per [ADR-0028](docs/adr/0028-catalog-read-model-and-search-semantics.md), with display-title substring search, repeated canonical-tag AND filters, deterministic ordering, bounded offset pagination, and catalog-safe location data. The packaged browser includes a manual `Current` metadata workspace that loads one imported medium by media ID, keeps persisted baseline values distinct from unsaved form state and filename fallback labels, edits or clears display title, edits or clears an optional plain-text description, locally searches existing canonical tags, selects, removes, and explicitly reorders up to 32 ordered tag assignments, creates canonical tag definitions only when the actor has `metadata.canonical.write`, saves administrator Current through the existing metadata API and ordinary Current through `PUT /api/media/{id}/alias`, refreshes the active catalog query after successful canonical save, and does not rename, move, delete, analyze, upload, or mutate media files. A bounded, deterministic, read-only, non-persistent library scan preview exists per [ADR-0014](docs/adr/0014-safe-library-scan-preview.md); candidate classification is extension-hint based only. Explicit idempotent scan-candidate import exists per [ADR-0026](docs/adr/0026-explicit-idempotent-scan-candidate-import.md); it requires an explicit selected candidate, reruns the bounded scan, creates one logical media item plus one physical location atomically when absent, returns an existing location for repeated imports, and performs no filesystem mutation. A bounded, deterministic, read-only, provider-neutral local media-analysis preparation boundary exists per [ADR-0015](docs/adr/0015-deterministic-local-media-analysis-preparation.md); it inspects one explicit MP4 or GIF candidate through optional external `ffprobe` and `ffmpeg` executables, returns bounded technical metadata, prepares at most three exact-distinct representative PNG frames in memory only, and writes no media records. A bounded, explicit opt-in, non-persistent media suggestion preview exists per [ADR-0016](docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md); it reuses local preparation, requires explicit cloud confirmation, derives bounded JPEG VLM images for NVIDIA NIM transport per [ADR-0019](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md), validates one untrusted suggestion preview, and performs no catalog or filesystem mutation. An explicit same-origin browser AI suggestion review exists per [ADR-0020](docs/adr/0020-on-demand-ai-suggestion-review.md); capability discovery is sanitized and provider-free, suggestion requests require explicit confirmation, the browser receives no credential, raw prompt, raw provider response, frame payload, absolute media path, or database path, and accept/reject actions are session-only with no mutation endpoint. Durable sidecars have a closed v1 projection schema, codec, one-location export, validation, and read-only compare per [ADR-0059](docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md); sidecar-to-catalog import, rebuild, Save coupling, multi-copy fan-out, and synchronization remain unimplemented. Storage volumes, arbitrary user-created collections, a general collection manager, collection CRUD, manual collection assignment, suggested filenames, complete Cover Studio, imported/AI/series covers, cover candidates, persistent AI Drafts, gallery persistence, synchronization, per-user visibility state, and multi-device server workflows remain unimplemented. A durable manual cover foundation (migration `0022`) is implemented for one accepted cover per logical medium, distinct from the unimplemented Cover Studio/candidate features. The trusted-loopback upload foundation through migrations `0014` and `0015` provides durable quarantine sessions, bounded validation, canonical byte identity, exact-duplicate disposition, lifecycle-owned single-process publication recovery, verified server-owned originals, a specialized atomic `published -> cataloged` transaction that creates exactly one logical media item and one physical location per successful catalog commit, and an optional durable automatic post-catalog AI analysis lifecycle per [ADR-0044](docs/adr/0044-durable-automatic-post-catalog-analysis.md) that remains off by default, never invents historical paid work, and never rolls back cataloging on provider failure.

Migration `0021` adds the separate content-publication relation per
[ADR-0049](docs/adr/0049-durable-content-publication-boundary.md), backfills
existing logical media, leaves later catalog rows unpublished, and supplies a
shared audience policy plus capability-gated administrator list and audited
single-item publication API.

Migration `0022` adds the durable manual cover foundation per
[ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md): a sparse accepted
`media_covers` relation, admin-only manual source-frame authoring from an
available GIF or MP4 source, durable immutable cover artifacts, regenerable
cover thumbnails, source-version fencing, optimistic-concurrency replacement,
and publication-visibility-gated thumbnail delivery. Migration `0023` extends
the accepted cover source contract to still-image (JPEG/PNG) sources: such
sources are timeless, keep the canonical source timestamp `0` (never presented
as a selected temporal frame), carry no fabricated duration, and flow through
the same durable-artifact, thumbnail, fencing, replacement, audit, and audience
boundaries as GIF and MP4 covers.

Domain and application logic MUST remain independent of FastAPI and MUST be testable without starting an HTTP server.

API route handlers MUST remain thin and SHOULD delegate to application services or use cases rather than contain filesystem, media-processing, database, downloader, or destructive-operation logic directly.

API request and response contracts MUST be typed.

HTTP and validation errors MUST be translated into structured, sanitized responses that do not leak secrets, absolute private paths, or raw stack traces to ordinary clients.

The API MUST support dependency replacement or injection for tests.

The initial server MUST be configurable for loopback-only binding and MUST NOT be publicly exposed by default.

Long-running downloads, media processing, scans, and transfers MUST NOT be implemented as unbounded synchronous work inside route handlers.

Future implementation MUST include API contract tests and domain tests that do not import FastAPI.

## 27. Accessibility and Performance

Kronika SHOULD support reduced motion, reduced transparency, keyboard access, focus visibility, touch targets, useful errors, responsive interaction, bounded background work, large-library scalability, and weaker-device degradation modes.

This specification makes no numeric performance promises.

## 28. Security Baseline

Kronika SHOULD use least privilege.

Kronika MUST NOT require routine root execution.

AppArmor and firewall protections MUST NOT be disabled as shortcuts.

Kronika SHOULD use path validation, input validation, explicit authorization, localhost service direction, secret redaction, safe updates, and later service hardening.

## 29. First Functional Vertical Slice

The first functional vertical slice SHOULD register one local library, import or download one media item, create durable metadata, index it locally, apply canonical tags, create the expected portable directory name, select or import a cover, generate derived thumbnail data, display it in a real gallery, open it through the playback abstraction, and prove roundtrip and filesystem behavior with tests.

The vertical slice is not implemented.

## 30. Verification Requirements

Implementation work SHOULD include unit tests, integration tests where boundaries require them, filesystem safety tests, path portability tests, manifest roundtrip tests, migration tests, failure-case tests, and deterministic fixtures.

Success MUST NOT be claimed without evidence.

## 31. Explicitly Deferred Decisions

Deferred decisions include frontend framework, frontend workspace tooling,
committed configuration file format, exact configuration schema,
operating-system secret-store implementation, manifest format, schema, IPC,
authentication above Tailscale, synchronization protocol, offline client cache
semantics, multiprocess publication or catalog leases or fencing, deferred multi-user
transactions, per-user visibility state,
category schema, language metadata schema, audio-track capability detection,
FFmpeg distribution, future yt-dlp update policy beyond the currently pinned
manual-ingestion dependency, player invocation,
general gallery thumbnail formats and sizes (the cover-thumbnail format and
size are established by [ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)),
full-text search, packaging/signing/update
mechanisms, telemetry, and license.

The supported Python minor version is recorded in [ADR-0001](docs/adr/0001-supported-python-version.md). Poetry dependency and environment management is recorded in [ADR-0002](docs/adr/0002-python-environment-and-dependency-manager.md). The initial server API framework is recorded in [ADR-0003](docs/adr/0003-initial-server-api-framework.md). Repository layout and Poetry package mode are recorded in [ADR-0004](docs/adr/0004-repository-layout.md). Configuration strategy is recorded in [ADR-0005](docs/adr/0005-configuration-strategy.md). The concrete settings library is recorded in [ADR-0007](docs/adr/0007-settings-library.md). The initial ASGI runtime is recorded in [ADR-0008](docs/adr/0008-asgi-runtime.md). The initial SQLite persistence and migration foundation is recorded in [ADR-0010](docs/adr/0010-initial-persistence-foundation.md). The stable entity identity format is recorded in [ADR-0011](docs/adr/0011-stable-domain-identities.md). Selective media placement is recorded in [ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md), with server-authority portions superseded by [ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md). Manual-first metadata and multi-model AI drafts are recorded in [ADR-0023](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md). Cover Studio and AI cover candidates are recorded in [ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md). The minimum persistent media catalog foundation is recorded in [ADR-0025](docs/adr/0025-minimum-persistent-media-catalog-foundation.md). Explicit idempotent scan-candidate import is recorded in [ADR-0026](docs/adr/0026-explicit-idempotent-scan-candidate-import.md). Persistent display title and canonical tags are recorded in [ADR-0027](docs/adr/0027-persistent-display-title-and-canonical-tags.md). Catalog read model and search semantics are recorded in [ADR-0028](docs/adr/0028-catalog-read-model-and-search-semantics.md). The automatic built-in `Processed` workflow collection is recorded in [ADR-0030](docs/adr/0030-automatic-processed-collection.md). The Fedora systemd service foundation is recorded in [ADR-0031](docs/adr/0031-fedora-systemd-service-foundation.md) and superseded for the active deployment target by the Ubuntu NUC deployment foundation in [ADR-0032](docs/adr/0032-ubuntu-nuc-deployment-foundation.md). Arbitrary user-created collections, a general collection manager, suggested filenames, complete Cover Studio, imported/AI/series covers, cover candidates, and persistent AI drafts remain unresolved and unimplemented. The durable manual cover foundation (migration `0022`) is implemented and does not extend to those features. Identity database encoding beyond canonical UUID text, sidecar manifest format, full-text search design, WAL and checkpoint details, media second-copy backup, remote multi-device conflict design, and further NUC security hardening remain open. Exact dependency versions, 3.13 patch pinning, Poetry virtual-environment location, Uvicorn version and extras, startup interface, process model, worker count, reload policy, trusted proxy configuration, structured logging, background jobs, authentication, and API versioning remain implementation concerns governed by those ADRs and later authorized tasks.

None of these may be silently selected during implementation.

## 32. Relationship to Other Documents

This specification is grounded in [README.md](README.md), [PRODUCT.md](PRODUCT.md), [SECURITY.md](SECURITY.md), [AGENTS.md](AGENTS.md), and the pinned canonical AP protocol under [`.ap/`](.ap/). The Kronika AP integration decision is recorded in [ADR-0034](docs/adr/0034-canonical-analytic-programming-integration.md).

Future ADRs should record architecture decisions. This document does not create ADRs.
