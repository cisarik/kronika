# Kronika Roadmap

## Roadmap Principles

This roadmap is staged and evidence-based. It does not promise release dates.

Each phase should begin only when its entry conditions are satisfied and should close only with the listed exit evidence.

The roadmap distinguishes completed foundation, immediate next work, planned
phases, long-term scope, frozen or parked product logical wholes, and
explicitly deferred work. Frozen and parked wholes below are durable backlog
preservation, not active authorization and not ADR mutations.

## Active Kronika Sequence

[ADR-0082](docs/adr/0082-kronika-one-product-and-private-records.md) fixes
the one-product direction.
[ADR-0083](docs/adr/0083-modular-research-providers-and-administrator-curated-timeline.md)
revises Search and Research delivery, administrator access and Timeline entry.
This sequence takes precedence over the older phase ordering below. One
implementation grant executes one row. Independent acceptance, publication,
credential provisioning, host preflight, reset and live provider calls require
their own bounded authority. A row does not grant them.

S0 and the published S1-S3 repository sources remain prior rows. They are not
reopened here. The S3 host remainder stays parked with the capture-mode rows
below. The active order is:

```text
S4-D -> S4-A -> S6 -> S4-B -> S7-P -> S8 -> S9 -> S10
```

| Slice | Useful outcome | Evidence before advancing |
|---|---|---|
| S4-D | Durable documentation of modular research providers, the selected native OpenAI route, question and answer history, administrator read-all, administrator approval and the shared-only Timeline | Documentation contradiction review, exact allowlist, unchanged AP pin; E1 |
| S4-A | Provider contracts, capability registry, configuration and a fake adapter. The real provider stays disabled | Import boundaries, configuration compatibility, selection snapshot, typed errors and rejection of the parked provider; E2 |
| S6 | Common records, owner, administrator and household policy, and the approval service. No S3 or S5 dependency | Migration and all-route authorization tests, approval conflicts and public exclusion; fresh authorization review; E3 |
| S4-B | Native OpenAI adapter, durable jobs, budgets, cancellation, cleanup and credential-deployment source. No live key or host mutation | Crash and idempotency, egress, secret handling, budget arithmetic and cleanup against a fake transport; fresh provider-boundary review; E3 |
| S7-P | Atomic question and answer save, personal-history APIs, media-success integration and safe document rendering | Duplicate completion, full output, preserved media review, approved revisions and safe rendering; E3 |
| S8 | Shared Timeline landing, personal history, Search and Research forms, administrator review and Kronika presentation. Gallery retained | Node behavior tests, safe rendering, Gallery, image, GIF and playback regressions; E2 |
| S9 | Integrated acceptance on the new empty catalog, then a separately provisioned provider and household UX acceptance. Parked capture completion is not a gate | Fresh integrated review; separate publication, read-only host preflight, exact reset and live-call grants; Cooperator acceptance on the exact public-main NUC release; E3 |
| S9-R | Administrator-managed research settings and versioned pricing: a bounded four-model catalog, immutable request pricing, shared configuration compare-and-set, atomic submission claims and fail-closed accounting | Focused catalog, arithmetic, refresh, replay, concurrency, preservation and authorization regressions; fresh independent audit, publication, NUC refresh and rendered acceptance |
| S10 | Rename old Kronika to `cli_chatgpt`, FrameNest to `kronika`, update source references; the former repository remains active for continued development (Cooperator decision, no archive) | Explicit rename and publication authority, exact refs, history and release verification; no force or history rewrite |

S6, S4-B and S7-P have implementation foundations in this repository. S8 is
on public main with the Timeline, personal history, Search and Research forms
and administrator review. Gallery remains a separate view. S9 integrated
acceptance, the empty-catalog reset and live-provider acceptance are closed.
S9-R administrator research settings and versioned pricing are published.
Research remains disabled by default. S10 is complete: this repository is
`cisarik/kronika`; the former capture repository is `cisarik/cli_chatgpt` and
remains active for continued development.

Parked capture work has no authority on the active route:

| Slice | Preserved outcome | Resume condition |
|---|---|---|
| S3 host remainder | Capture service sources and the release-helper extension stay in the tree. Host login and ask acceptance are not complete | A future explicit decision, after a read-only host preflight |
| Capture-mode S4 | Selective Search and Research restore inside the parked capture module is not the active research route | A future explicit decision, after the capture module proves complete Search and Research |
| S5 | Bounded ZIP activation. Existing JPEG, ZIP and budget code stays in place and is not external-research upload support | A future explicit decision and semantic ZIP evidence |
| S7-C | Capture application integration, including bridge submission and capture-specific media integration | A future explicit decision. S7-P does not switch media analysis to the research provider |

Active proofs are administrator read of product records without secret or host
access; ordinary cross-owner denial; shared Timeline exclusion until
administrator approval; personal history of questions and answers, including
unfinished work; no automatic provider fallback or generation retry; complete
safe Search and Research rendering; and research disabled by default. Parked
capture proofs remain requirements of those parked rows: no automatic resend,
browser continuity, ZIP rejection before ChatGPT contact, and one packaged
capture implementation. They do not gate the active order.

Use the existing baseline-bound AP Python route and `node --test`, with no new
test toolchain. S4-D is documentation only. NUC, browser and live-provider
facts require later preflight or bounded live evidence, not historical traces.
Preserve the AP pin and upgrade ledger. Personal-photo AI, old-database import,
native share apps, providers other than the selected OpenAI Responses provider
and the parked capture module, internet publication and production hardening
are outside this sequence. The existing release helper remains the only
deployment system. ADR-0085 is the sole-identity authority; it is implemented as
an ordered sequence of bounded cuts rather than a mass `kronika` rename.

## Earlier MacBook MVP Convergence (Foundation History)

The following phases preserve earlier foundation status and the longer-term
horizon. They do not replace the active Kronika sequence, authorize internet
publication, or override ADR-0083 ownership, administrator access and Timeline
rules for new records.

The minimum logical-media and physical-location persistence foundation and
explicit idempotent import from selected scan candidates now exist on MacBook.
The persistent display-title and canonical-tag core now exists, and imported
media can now be reached through a catalog browser with display-title search,
canonical-tag AND filters, and a manual `Current` metadata workspace for title,
optional plain-text description, and ordered tag assignment. An automatic
built-in `Processed` workflow collection derived from durable tag saves is
implemented through [ADR-0030](docs/adr/0030-automatic-processed-collection.md)
and migration `0007`; arbitrary
user-created collections and suggested filename remain future decisions not yet
authorized by a subsequent slice.

The near-term convergence sequence is:

1. persistent local media catalog foundation;
2. logical media and physical locations;
3. explicit idempotent import from selected scan candidates;
4. canonical tags and title/tag metadata;
5. searchable catalog browser;
6. manual title/description/tag metadata detail;
7. Cover Studio and derivatives;
8. persistent premium gallery;
9. multi-model AI workspace;
10. optional AI cover experiments;
11. later Tauri and bounded NUC deployment/aggregation work.

This sequence preserves broader cross-platform goals while keeping the immediate
critical path focused on a polished and functional macOS MVP.

## Phase 0 — Repository and Protocol Foundation

Status: completed before this task.

Goal: establish a safe public repository and working protocol.

Key deliverables: public repository, safety perimeter, security policy,
canonical pinned Analytic Programming integration through `.ap/`, root
[AGENTS.md](AGENTS.md) for Kronika-specific rules, and product foundation.

Entry conditions: empty verified repository.

Exit evidence: committed foundation files through `PRODUCT.md`.

Boundaries: no application code, package scaffolding, or framework selection.

## Phase 1 — Normative Product Foundation

Status: completed by Stage A of this task.

Goal: convert approved product direction into normative requirements and a staged plan.

Key deliverables: [PRODUCT.md](PRODUCT.md), [SPEC.md](SPEC.md), and [ROADMAP.md](ROADMAP.md).

Entry conditions: product foundation committed.

Exit evidence: specification and roadmap committed.

Boundaries: unresolved architecture decisions remain unresolved.

## Phase 2 — Architecture Decision Package

Status: in progress.

Goal: prepare individual ADR evidence before scaffolding and record accepted architecture decisions one at a time.

Accepted so far:

- Supported Python version: CPython 3.13 through [ADR-0001](docs/adr/0001-supported-python-version.md).
- Python environment and dependency manager: Poetry through [ADR-0002](docs/adr/0002-python-environment-and-dependency-manager.md).
- Initial server API framework: FastAPI through [ADR-0003](docs/adr/0003-initial-server-api-framework.md).
- Hybrid staged repository layout through [ADR-0004](docs/adr/0004-repository-layout.md).
- Configuration strategy: layered configuration with explicit precedence through [ADR-0005](docs/adr/0005-configuration-strategy.md).
- macOS Python interpreter provider: `uv` for CPython 3.13.14 on Apple Silicon macOS through [ADR-0006](docs/adr/0006-macos-python-interpreter-provider.md).
- Python settings library: `pydantic-settings` through [ADR-0007](docs/adr/0007-settings-library.md).
- Initial ASGI runtime: Uvicorn through [ADR-0008](docs/adr/0008-asgi-runtime.md).
- Initial structured logging approach: standard-library `logging` with a Kronika-owned JSON formatter and redaction boundary through [ADR-0009](docs/adr/0009-structured-logging-approach.md); implementation complete.
- Initial SQLite persistence and migration foundation: synchronous SQLAlchemy Core with Alembic through [ADR-0010](docs/adr/0010-initial-persistence-foundation.md); minimal explicit migration implementation complete.
- Stable domain identities: application-owned UUIDv4 values with category-specific pure-domain types through [ADR-0011](docs/adr/0011-stable-domain-identities.md); minimal identity primitives implemented.
- Local web application delivery through packaged vanilla HTML/CSS/JavaScript assets through [ADR-0017](docs/adr/0017-initial-local-web-application-delivery.md); implementation complete.
- Local media-analysis preview API through [ADR-0018](docs/adr/0018-local-media-analysis-preview-api.md); implementation complete.
- VLM JPEG derivatives and NVIDIA instruct mode through [ADR-0019](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md); implementation complete for the prototype boundary.
- On-demand editable AI suggestion review through [ADR-0020](docs/adr/0020-on-demand-ai-suggestion-review.md); implementation complete as a non-persistent pre-alpha review.
- Tauri desktop shell direction through [ADR-0021](docs/adr/0021-tauri-desktop-shell.md); not implemented.
- Selective media placement direction through [ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md), with server-authority portions superseded by [ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md); not implemented beyond current local catalog foundations.
- Manual-first metadata and multi-model AI draft direction through [ADR-0023](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md); partially implemented for manual `Current` display title, plain-text description, and ordered canonical tags. Collection, suggested filename, persistent AI drafts, multi-model draft comparison, inline model picker, and draft promotion workflows remain unimplemented.
- Cover Studio and AI cover candidate direction through [ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md); not implemented.
- Minimum persistent media catalog foundation through [ADR-0025](docs/adr/0025-minimum-persistent-media-catalog-foundation.md); implementation complete for logical media and physical locations only.
- Explicit idempotent scan-candidate import through [ADR-0026](docs/adr/0026-explicit-idempotent-scan-candidate-import.md); implementation complete for one selected scan candidate at a time.
- Persistent display-title and canonical-tag core through [ADR-0027](docs/adr/0027-persistent-display-title-and-canonical-tags.md); implementation complete for API-level title/tag persistence.
- Catalog read model and search semantics through [ADR-0028](docs/adr/0028-catalog-read-model-and-search-semantics.md); implementation complete for read-only imported-media listing, display-title search, canonical-tag AND filters, deterministic ordering, and bounded offset pagination.
- Automatic built-in `Processed` workflow collection from durable tag saves through [ADR-0030](docs/adr/0030-automatic-processed-collection.md); accepted and implemented through migration `0007`, with one zero-or-one collection membership per medium, and no arbitrary collection CRUD or general collection manager.
- Fedora systemd service foundation through [ADR-0031](docs/adr/0031-fedora-systemd-service-foundation.md), superseded for the active deployment target by the Ubuntu NUC deployment foundation through [ADR-0032](docs/adr/0032-ubuntu-nuc-deployment-foundation.md); accepted and implemented as repository-local service source material, a non-secret environment template, a read-only database-readiness gate, and an Ubuntu operator runbook. The catalog backup and restore-to-new-destination foundation is accepted through [ADR-0033](docs/adr/0033-catalog-backup-and-recovery-foundation.md). Automated catalog backup/retention/restore-verification is accepted through [ADR-0052](docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md), and a routine immutable NUC release-update contract (`deploy/ubuntu/kronika-release`) is accepted through [ADR-0060](docs/adr/0060-repeatable-immutable-nuc-release-update-contract.md); refreshing the NUC to the exact public `main` SHA is normal routine operation ([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)). Public `main` and the deployed NUC release may differ; the authoritative mutable readback is `kronika-release status`. Further NUC security hardening, AppArmor/UFW completion, production database replacement automation, media second-copy backup, and secret-recovery drills remain open.
- Canonical Analytic Programming integration through a pinned `.ap/` Git submodule and managed `AGENTS.md` block through [ADR-0034](docs/adr/0034-canonical-analytic-programming-integration.md); accepted and implemented. Universal AP protocol files live under `.ap/`, Kronika-specific rules live in `AGENTS.md`, and permanent BOOT/NEXT files are no longer live repository artifacts.
- Authoritative server and client state model through [ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md); accepted as product architecture direction. The server process is authoritative for catalog and server-owned state and may run locally or later on the NUC; browser, desktop, and remote interfaces are clients. Broader authenticated upload, synchronization, client cache/download, per-user Trash, categories, language metadata, and playback extensions remain unimplemented beyond the current trusted-loopback upload foundation.
- Durable upload sessions, bounded validation, lifecycle-owned validation, canonical byte identity, exact-duplicate disposition, atomic single-process storage-publication recovery, specialized `published -> cataloged` catalog creation, optional durable automatic post-catalog AI analysis, first-class still-image (`jpg`/`png`) media kinds, first-class content classification plus bounded movie identification, owner-operated YouTube manual ingestion, and a separate durable content-publication boundary with a responsive single-item administrator workflow through [ADR-0037](docs/adr/0037-durable-upload-session-and-safe-ingest-foundation.md), [ADR-0038](docs/adr/0038-bounded-upload-media-validation.md), [ADR-0039](docs/adr/0039-lifecycle-owned-upload-validation-orchestration.md), [ADR-0040](docs/adr/0040-canonical-upload-byte-identity-foundation.md), [ADR-0041](docs/adr/0041-exact-byte-upload-duplicate-disposition.md), [ADR-0042](docs/adr/0042-atomic-upload-publication.md), [ADR-0043](docs/adr/0043-upload-to-catalog-transaction.md), [ADR-0044](docs/adr/0044-durable-automatic-post-catalog-analysis.md), [ADR-0045](docs/adr/0045-content-classification-and-movie-identification.md), [ADR-0046](docs/adr/0046-youtube-manual-ingestion-and-provenance.md), and [ADR-0049](docs/adr/0049-durable-content-publication-boundary.md); implemented through migration `0021`.

The initial scaffold decision gate is complete. A Poetry package scaffold, centralized configuration boundary, FastAPI application factory, typed health endpoint, contract tests, Uvicorn runtime dependency, startup wiring, and a runnable loopback-only server command now exist.

Broader architecture decisions still open include category and language metadata schema, metadata/tag/search schema, cover and thumbnail cache implementation details, desktop sidecar IPC, initial authentication boundary, offline client cache semantics, multiprocess publication or catalog leases or fencing, media-tool distribution strategy, and Ubuntu NUC host-acceptance details beyond the initial systemd service foundation. Portable media sidecar v1 format, projection, validation, and compare exist through [ADR-0059](docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md); import, rebuild, and synchronization remain open.

Persistence strategy is accepted through [ADR-0010](docs/adr/0010-initial-persistence-foundation.md). The minimal SQLAlchemy/Alembic migration foundation is implemented. The current schema head is revision `0035`. Migration `0034` adds private Kronika records, immutable completed question/answer documents, and administrator approval projections; it is accepted and published. Migration `0035` adds durable research requests, the single active slot, provider accounting operations and budget holds; it is implemented and published, with live research disabled by default. The previous head was revision `0033`: catalog tables and the automatic built-in `Processed` collection are established through `0007`; `0008` through `0018` add durable upload, validation, byte identity, duplicate disposition, storage publication, catalog, automatic-analysis, still-image, classification, movie-identification, and analysis-history foundations; `0019` adds durable YouTube manual-acquisition provenance; `0020` adds durable security audit events; `0021` adds durable content publication with legacy backfill; `0022` adds the durable manual cover foundation per [ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md); `0023` extends the accepted cover source contract to timeless still-image (JPEG/PNG) sources; `0024` adds catalog-removal receipts; `0025` adds upload-session ownership and duplicate mode for ordinary-user submission; `0026` adds YouTube requester ownership; `0027` adds YouTube creator taxonomy fields; `0028` adds requester-private X acquisition; `0029` adds the caller-private per-user media alias overlay; `0030` adds claim-level X Save category plus public JPEG/PNG photo acquisition; and `0031` adds companion-review open state and field-source receipts (`ContentPublicationOrigin.COMPANION_REVIEW`); `0032` adds `companion_review_tag_sources` without historical per-tag backfill. Gallery and Details remain canonical.

Stable identity strategy is accepted through [ADR-0011](docs/adr/0011-stable-domain-identities.md). Pure domain identity primitives exist, and minimal logical media, physical location, device, and library entities exist. Storage volume and series entities remain future work beyond identity values.

Key deliverables: remaining broader architecture ADRs and evidence as needed before later implementation phases.

Entry conditions: [SPEC.md](SPEC.md) and this roadmap are accepted.

Exit evidence: broader architecture package completed without silently selecting unresolved options beyond accepted ADRs.

Boundaries: Phase 2 remains `in progress` until the broader architecture package is completed. Application code is not implemented by this decision gate alone.

## Phase 3 — Domain and Metadata Core

Status: partially implemented.

Goal: define and test the core domain model and durable metadata behavior.

Implemented so far:

- Stable identity format accepted through [ADR-0011](docs/adr/0011-stable-domain-identities.md)
- Immutable pure-domain identity primitives for logical media, physical locations, devices, libraries, storage volumes, and series
- Minimal pure-domain `Device` entity and local device registry core accepted through [ADR-0012](docs/adr/0012-initial-device-registry.md)
- Minimal pure-domain `Library`, `LibraryRoot`, and device-local root-locator model with local library registry core accepted through [ADR-0013](docs/adr/0013-initial-library-registry.md)
- Minimal pure-domain `LogicalMedia`, `MediaLocation`, relative-path, media-kind, and availability-state model accepted through [ADR-0025](docs/adr/0025-minimum-persistent-media-catalog-foundation.md)

Still required for phase exit:

- Storage volume and series entities beyond identity values
- Manual metadata detail beyond display title, plain-text description, and ordered canonical tags
- Durable metadata roundtrip behavior beyond the current SQLite/API/browser title/description/tag slice
- Sidecar contracts
- Exact roundtrip tests for durable metadata behavior

Entry conditions: relevant ADRs accepted.

Exit evidence: tests proving domain rules, identity behavior, sidecar roundtrip, and no implementation claims without passing evidence.

Boundaries: no gallery, downloader, playback, or multi-device server behavior beyond what domain tests require.

## Phase 4 — Server-First Development Skeleton on macOS

Status: shipped/closed for the skeleton boundary; later product surfaces continue in later phases.

Goal: create the first local development server skeleton on macOS.

Implemented so far:

- Poetry package scaffold
- Centralized `pydantic-settings` configuration boundary with loopback-safe default host
- FastAPI application factory with typed `GET /health`
- In-process API contract tests and import-boundary tests
- Uvicorn runtime dependency and startup wiring
- Runnable loopback-only server process verified by tests and command output
- Runtime health smoke verification
- Packaged local web shell at `GET /` with same-origin assets
- Read-only registered-library listing API
- Same-origin scan-preview API
- Same-origin local media-analysis preview API
- Same-origin explicit AI capability and media-suggestion preview API
- Structured logging foundation per [ADR-0009](docs/adr/0009-structured-logging-approach.md)
- Persistence strategy accepted through [ADR-0010](docs/adr/0010-initial-persistence-foundation.md)
- Minimal SQLAlchemy Core/Alembic persistence foundation with `KRONIKA_DATABASE_PATH`, packaged revisions `0001` through `0007`, explicit `kronika-db status`, and explicit `kronika-db migrate`
- Initial local device registry core with pure-domain `Device`, application repository port, SQLAlchemy Core adapter, and `devices` table through revision `0002`
- Initial local library registry core with pure-domain `Library`, `LibraryRoot`, application repository port, SQLAlchemy Core adapter, and `libraries` table through revision `0003`
- Minimum persistent media catalog foundation with pure-domain logical media and physical locations, application repository port, SQLAlchemy Core adapter, and `logical_media` plus `physical_media_locations` tables through revision `0004`
- Persistent display-title and canonical-tag core with pure-domain metadata values, application repository port, SQLAlchemy Core adapter, and `canonical_tags`, `media_metadata`, plus `media_canonical_tags` tables through revision `0005`
- Automatic built-in `Processed` workflow collection derived from durable tag saves with nullable `collection_key` and `processed_at_ms` columns added in revision `0007`
- Read-only imported-media catalog browser with display-title search, repeated canonical-tag AND filters, deterministic ordering, and bounded offset pagination
- Durable content-publication truth through revision `0021`, published-only ordinary Gallery and direct-media audience enforcement, and a responsive capability-gated administrator list with atomic single-item publication
- Development operator catalog CLI (`kronika-catalog`) for device register, get, and list operations
- Library catalog CLI commands for local library register, get, and list with lexical root-path preparation
- Explicit idempotent scan-candidate import through same-origin API and packaged browser action

Still required for phase exit:

- Manual metadata detail beyond persistent title, description, and tag data and the current catalog browser

The next bounded implementation step should build on imported media records without adding gallery, cover, or filesystem mutation scope prematurely.

Key deliverables: loopback-only local development server skeleton, health endpoint, configuration boundary, structured logging, SQLite development catalog, migration mechanism, and tests.

Entry conditions: server/API/database/repository-layout ADRs accepted.

Exit evidence: local tests and command output showing loopback-only behavior and basic health/config/database boundaries.

Boundaries: server-first implementation priority MUST NOT make the desktop product dependent on a remote NUC or public cloud service.

## Phase 5 — Local Catalog and Library Scanning

Status: partially implemented.

Goal: register and scan local libraries safely.

Implemented within this phase:

- library registration through the development catalog CLI;
- safe read-only library scan preview through `kronika-catalog library scan-preview` per [ADR-0014](docs/adr/0014-safe-library-scan-preview.md);
- deterministic read-only local media-analysis preparation through `kronika-catalog library analyze-preview` per [ADR-0015](docs/adr/0015-deterministic-local-media-analysis-preparation.md);
- explicit opt-in NVIDIA NIM media suggestion preview through `kronika-catalog library suggest-preview` per [ADR-0016](docs/adr/0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md);
- packaged browser library listing, scan preview, explicit scan-candidate import, local media-analysis preview, capability discovery, and editable non-persistent AI suggestion review.
- minimum logical-media and physical-location persistence through revision `0004`.
- explicit idempotent import from selected scan candidates through [ADR-0026](docs/adr/0026-explicit-idempotent-scan-candidate-import.md).
- persistent display-title and canonical content tags through [ADR-0027](docs/adr/0027-persistent-display-title-and-canonical-tags.md).
- read-only catalog retrieval, display-title search, canonical-tag AND filters, deterministic ordering, and bounded offset pagination through [ADR-0028](docs/adr/0028-catalog-read-model-and-search-semantics.md).
- browser manual `Current` metadata workspace for one selected imported medium, including persistent display-title edit/clear, optional plain-text description edit/clear, content category, read-only acquisition provenance, structured creator attribution chips, movie genres when categorized as movie, canonical-tag search, selected-tag removal and reordering, explicit canonical-tag creation, dirty/discard protection covering title, description, classification, and tag changes, and catalog refresh after successful save.
- Gallery high-level filters for Memes, Movies, and YouTube as content-category query dimensions, plus creator-chip filtering by structured attribution identity, per [ADR-0045](docs/adr/0045-content-classification-and-movie-identification.md) and [ADR-0055](docs/adr/0055-youtube-creator-taxonomy-and-immutable-provenance.md).
- bounded movie-identification analysis profile with reasoning ON, contact-sheet derivative transport, and durable run separation from generic analysis.
- automatic built-in `Processed` workflow collection entered by the first durable tag save, cleared when all tags are removed, with a virtual `All media` Catalog scope and an optional `Processed` Catalog scope in the packaged browser.
- ordinary alias-edit affordance with per-field AI suggestion strips revealed
  immediately from the newest stored run, with a dropdown that switches
  suggestions without Load, reading `GET /api/media/{media_id}/ai-suggestions`
  ([ADR-0077](docs/adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md),
  [ADR-0080](docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md)).

Still unimplemented within this phase:

- arbitrary user-created collections and a general collection manager;
- suggested filename editing and physical rename;
- browser metadata fields beyond display title, plain-text description, content category, read-only acquisition provenance, structured creator attribution, movie genres, and ordered canonical tags;
- availability tracking;
- storage capacity reporting;
- rebuildable local index persistence;
- sidecar-to-catalog import and rebuild behavior.

The portable sidecar v1 contract, one-location projection, adjacent filesystem store, and `kronika-sidecar` export/validate/compare commands exist through [ADR-0059](docs/adr/0059-portable-media-sidecar-roundtrip-foundation.md).

Key deliverables: library registration, safe scanning, persistent metadata collection, logical media and physical locations, canonical tags, title/tag search, availability tracking, storage capacity reporting, rebuildable local index, and tests.

Entry conditions: domain and local database foundations exist.

Exit evidence: deterministic fixtures and filesystem tests showing non-destructive scanning and rebuildable index behavior.

Boundaries: no destructive organization by default; no file mutation is required for the minimum persistent catalog.

## Phase 6 — Naming, Tagging, and Portable Metadata

Status: partially implemented.

Goal: implement canonical organization rules and durable metadata.

The bounded portable sidecar v1 contract and one-location projection foundation
is implemented: closed schema and codec, catalog projection, secure adjacent
filesystem store, and operator export/validate/compare. Later work in this
phase remains unimplemented: sidecar-to-catalog import/rebuild, drift repair,
automatic Save projection, multi-location fan-out, directory naming, native OS
tags, dry-run organization, and cross-device synchronization.

Key deliverables: canonical tags, directory naming, sidecars, native tag adapters, dry runs, migrations, drift detection, and repair workflows.

Entry conditions: metadata contracts and path portability rules are accepted.

Exit evidence: tests for naming, tag projection, sidecar durability, dry-run previews, and safe migrations.

Boundaries: no silent rename or migration execution.

## Phase 7 — Media Acquisition

Status: partially implemented.

Goal: implement the first adapter-based acquisition workflow.

Key deliverables: yt-dlp adapter, source inspection, metadata preview, progress, cancellation, temporary state, finalization, archive identity, structured errors, and tests.

Implemented within this phase:

- ordinary-user private upload submission and administrator review boundary;
- requester-private YouTube acquisition and administrator promotion;
- YouTube/X creator taxonomy and immutable source-derived provenance;
- requester-private X meme acquisition for native X video, animated-GIF-like
  media delivered as video, and public JPEG/PNG photos
  ([ADR-0064](docs/adr/0064-x-save-category-and-public-photo-acquisition.md),
  [ADR-0065](docs/adr/0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md));
- unpacked Manifest V3 X companion origin trust, requester-private meme picker,
  and synthetic composer attach ([ADR-0061](docs/adr/0061-x-meme-browser-companion.md));
- merged administrator title-bar history of pending and analyzed items above the
  surviving hosted iframe, color-coded analyzed versus pending rows, blank
  success status, toolbar badge from `unopened_count`, MV3 `alarms`
  (`kronika.review-inbox`, 1 minute), and a sibling review overlay
  ([ADR-0071](docs/adr/0071-native-side-panel-review-inbox-chrome.md),
  [ADR-0072](docs/adr/0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md),
  [ADR-0073](docs/adr/0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md));
- administrator-owned X automatic generic analysis policy, still default-off
  ([ADR-0066](docs/adr/0066-administrator-owned-x-automatic-generic-analysis.md)).
- administrator automatic-analysis runtime setting as the fifth
  `companion_mutation` route (`PUT /api/admin/settings/automatic-analysis`),
  toggleable from the companion Settings overlay without restart
  ([ADR-0079](docs/adr/0079-administrator-automatic-analysis-runtime-setting.md)).

The historical Save-triggered publication rule in
[ADR-0068](docs/adr/0068-companion-review-save-and-readiness-triggered-publication.md)
was succeeded by the implemented administrator publication PUT in
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md).
Companion Save/Apply does not publish. ADR-0082 excludes new Kronika records
from internet publication. ADR-0083 keeps that exclusion. Household Timeline
entry is administrator approval, not owner-direct sharing and not the
ADR-0074 publication path. Neither earlier contract authorizes that approval.

Still required for phase exit: broader adapter coverage, generalized downloader
UI, and additional source adapters beyond the shipped YouTube/X foundations.

Entry conditions: media acquisition boundaries and packaging/update strategy are decided.

Exit evidence: deterministic tests or controlled fixtures proving adapter isolation and safe finalization.

Boundaries: domain logic must not depend directly on yt-dlp and no unnecessary transcoding by default.

## Phase 8 — Covers and Thumbnail Pipeline

Status: partially started.

The durable manual cover foundation is implemented (migration `0022`,
[ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md)): one accepted
manually selected cover per logical medium from an available GIF, MP4, or
still-image source, durable immutable artifacts, regenerable
`cover-thumbnail-jpeg-v1` derivatives, source-version fencing,
optimistic-concurrency replacement, admin-only authoring via
`metadata.canonical.write` with `media.cover_set` audit, and
publication-visibility-gated thumbnail delivery with Gallery cover priority and
existing fallback. Timeline selection, the explicit `Set as cover` action,
replacement confirmation, and ephemeral preview are implemented for the manual
GIF/MP4 slice. Migration `0023` extends the accepted cover source contract to
timeless still-image (JPEG/PNG) sources selected through the existing browser
upload cockpit; still-image authoring renders a bounded image preview with no
timeline, scrubber, or timestamp selector.

Goal: support durable covers and reproducible derived thumbnails.

Key deliverables: timeline selection, cover import, durable original cover storage, derived cache, reproducibility checks, series covers, and tests.

Still required for phase exit: complete Cover Studio, imported-image covers,
series covers, cover candidates, and full thumbnail lifecycle/eviction.

Entry conditions: media metadata and storage layout are stable enough for cover references.

Exit evidence: roundtrip tests for selected timestamps, imported covers, derived thumbnails, and cover provenance.

Boundaries: AI-generated covers remain later scope.

## Phase 9 — Premium Local Gallery

Status: partially implemented.

Goal: build the first real scalable local gallery.

The Gallery card brain-symbol Analyze control is edit-then-analyze: after
confirmation it opens the existing Edit dialog immediately, shows in-modal
progress, and resolves proposal strips in place with no bulk last-write-wins
save
([ADR-0078](docs/adr/0078-gallery-card-ai-per-field-review.md),
[ADR-0080](docs/adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md)).

Key deliverables: cover-driven gallery, logical-item cards, local and remote-only card states, title search, multi-tag AND filtering, removable active filters, series views, storage/device state, short-media preview, accessibility support, reduced motion, reduced transparency, and lower-resource modes.

Entry conditions: domain, metadata, covers, and local catalog are testable.

Exit evidence: working local gallery backed by testable local data and verified accessibility/performance behavior without unsupported numeric promises.

Boundaries: do not select a frontend framework outside approved ADRs.

## Phase 10 — Playback

Status: partially implemented.

Goal: open local and later remote media through a playback abstraction.

Key deliverables: external VLC backend, VLC availability checks, failure reporting, authorized remote URL support later, and separate inline-preview backend.

Entry conditions: player invocation and playback boundary decisions accepted.

Exit evidence: tests or controlled verification showing backend separation and clear failure handling.

Boundaries: embedded libVLC remains deferred.

## Phase 11 — Intel NUC Ubuntu Deployment

Status: partially implemented; the NUC is the disposable development-and-testing machine routinely refreshed toward public `main` ([ADR-0075](docs/adr/0075-nuc-development-test-target-and-routine-release-refresh.md)), while NUC security hardening remains open.

Goal: deploy and harden the server foundation on Ubuntu Server 24.04 on the Intel NUC6i5SYH development-and-testing machine.

Key deliverables: Ubuntu NUC deployment runbook, exact-release workflow, secure CPython 3.13 provisioning, hardware/storage inspection, hardening, AppArmor/UFW context, service user, systemd hardening, explicit migration and readiness, and catalog backup/recovery documentation.

Entry conditions: exact commit or release selected, backups and rollback planned, service user and release installation prepared, and real host authority granted in a later bounded task.

Exit evidence: documented deployment checks and verified service behavior on the NUC.

Boundaries: no destructive disk commands in roadmap tasks; no public listener, router port forwarding, public SSH exposure, Tailscale setup, authentication implementation, provider-secret integration, production database replacement, media backup, or retention automation without separate accepted tasks.

## Phase 12 — Tailscale-Only Remote Access

Status: partially implemented.

Goal: enable private remote access without public exposure.

Key deliverables: Tailscale provisioning as deployment infrastructure, Serve boundary, loopback backend exposure, authorization design, no Funnel, and no public ports.

Entry conditions: server deployment boundary and authorization decisions accepted.

Exit evidence: verification that remote access is private, authorized, and not publicly exposed.

Boundaries: no router port forwarding and no Tailscale Funnel against the
workspace socket in the approved direction.
[ADR-0074](docs/adr/0074-dual-audience-public-published-and-tailscale-workspace-boundary.md)
accepts a second, local-only `public_published_uds` published-reader
composition; that reader is implemented-for-backend and is not exposed
externally (no public bind, TLS listener, Funnel, or NUC enablement). The
remaining public-origin rollout successors are not shipped and are superseded
for the active Kronika sequence by ADR-0082: public composition stays off.

## Phase 13 — Authoritative Multi-Device Catalog

Status: planned.

Goal: serve authoritative catalog and server-owned state to browser, desktop,
and remote clients while preserving selective media placement.

Key deliverables: device synchronization, global logical media visibility,
remote-only cards backed by metadata/covers, global locations, offline state,
per-user visibility state, client cache/download semantics, conflict handling,
and browser/PWA global view direction.

Entry conditions: local catalog foundations, authoritative server deployment,
authentication/capability design, and synchronization decisions are ready.

Exit evidence: tests proving known locations, offline state, and conflict behavior.

Boundaries: automatic full-media replication is not the default; automatic
global synchronization remains deferred until explicitly designed; per-user
Trash must not delete server originals.

## Phase 14 — Streaming, Download, and Transfer

Status: planned; the bounded trusted-loopback atomic publication foundation is implemented.

Goal: support safe remote media operations.

Key deliverables: direct play first, explicit stream/download/archive actions,
explicit client cache/download, authenticated server-managed ingest/upload,
copy/move operations, verification, deduplication safeguards, remote download,
`Download + Copy to Clipboard` through native desktop capability, truthful
progress, cancellation, and partial-failure recovery.

Entry conditions: remote access, authorization, and transfer model are accepted.

Exit evidence: tests and controlled transfer evidence showing destination verification and final-copy protection.

Boundaries: no source deletion before verified destination success. Upload must
use server-selected placement with quarantine, validation, limits, safe
filenames, duplicate detection, atomic publication, and failure cleanup. Clients
must not select arbitrary server filesystem paths.

## Phase 15 — AI-Assisted Workflows

Status: partially implemented; multi-model draft comparison remains frozen.

Goal: add optional user-controlled AI assistance.

Key deliverables: manual-first metadata workspace, multi-model AI draft comparison, naming/tagging assistance, suspicious filename analysis, representative-frame selection, provider adapters, privacy modes, and confirmation workflows.

Entry conditions: persistent catalog and manual metadata detail exist; provider-adapter decisions, secret storage decisions, and privacy UX are accepted.

Exit evidence: tests and reviews showing no unsolicited cloud upload and no provider secrets in ordinary clients.

Boundaries: suggestions require confirmation, draft promotion is not persistence, and AI remains optional.

## Phase 16 — AI-Generated Covers

Status: long-term planned.

Goal: add optional generated cover workflows.

Key deliverables: generated cover candidates, provenance, confirmation, replacement safeguards, and rollback information.

Entry conditions: manual Cover Studio, cover candidates, and AI provider boundaries are mature.

Exit evidence: user-confirmed cover generation flow with provenance and no automatic replacement.

Boundaries: no generated cover candidate replaces an approved cover without explicit human acceptance.

## Phase 17 — Backup and Encrypted Cloud Restore

Status: partially implemented; catalog backup create/verify/restore, automated
retention/restore-verification, mounted-filesystem off-device
copy/restore-verification, and operator-workstation pull-based snapshot
foundations exist through
[ADR-0033](docs/adr/0033-catalog-backup-and-recovery-foundation.md),
[ADR-0052](docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md),
[ADR-0056](docs/adr/0056-off-device-catalog-backup-copy-and-restore-verification.md),
and
[ADR-0057](docs/adr/0057-operator-workstation-pull-based-catalog-snapshot.md).
Media second-copy, encrypted cloud restore, proven physical host-loss survival,
and production-replacement automation remain open.

Goal: provide future backup and restore without losing local-first ownership.

Key deliverables: metadata-first restore beyond the SQLite catalog bundle, media second-copy workflows, production database replacement, retention automation, multiple providers, encryption, recovery workflows, and documentation.

Entry conditions: stable metadata, identity, server, and security foundations.

Exit evidence: restore tests proving metadata and integrity behavior.

Boundaries: not part of early implementation beyond the current catalog-only foundation and not a cloud-only direction.

## Current Active Logical Whole

No product implementation logical whole is declared active in living roadmap
state after the closed `Requester-Private X Meme Acquisition` baseline at
`aec2f0091c10aed2fc2033dac154a0d9651b2b6d`.

Expected near-term strategic work, when separately authorized, includes
`NUC Security Hardening` before any VPS deployment. That whole is **not**
automatically active here and must not be started without a fresh Orchestrator
grant.

Do not reopen the closed wholes listed below merely because older roadmap prose
was stale.

## Closed Product Logical Wholes

The following named wholes are closed ancestors. Do not treat them as current
active unfinished work:

| Logical whole | Status note |
| --- | --- |
| Processed Publish Workflow and Responsive Admin List View | `CLOSED` |
| Admin Media Selection and Bounded Batch Actions | `CLOSED` (feat commit `3f89b8b`) |
| Administrator Media Removal / safe catalog retirement | `CLOSED` (feat commit `deb8b7c`) |
| Automated Catalog Backup/Retention/Restore Verification | `CLOSED` (feat commit `455a174`; [ADR-0052](docs/adr/0052-automated-catalog-backup-retention-and-restore-verification.md)) |
| Ordinary-User Upload Submission / Administrator Review Boundary | `CLOSED` (feat commit `68eb98f`; [ADR-0053](docs/adr/0053-ordinary-user-upload-submission-and-administrator-review-boundary.md)) |
| Requester-Private YouTube Acquisition / Administrator Promotion | `CLOSED` (feat commit `3948a0e`; [ADR-0054](docs/adr/0054-requester-private-youtube-acquisition-and-promotion-boundary.md)) |
| YouTube and X Taxonomy / Creator Attribution / AI Tagging Contract | `CLOSED` (feat commit `4350a04`; [ADR-0055](docs/adr/0055-youtube-creator-taxonomy-and-immutable-provenance.md)) |
| Requester-Private X Meme Acquisition | `CLOSED` at public/canonical `aec2f0091c10aed2fc2033dac154a0d9651b2b6d` |

## Frozen and Parked Product Logical Wholes

The following product logical wholes are preserved for future fresh
Orchestrator and Worker authorization. They are outside the current MVP path
unless explicitly reopened. Do not mix them into ordinary documentation or
hardening work.

Kronika product work remains prioritized before stable AP upgrade work,
`cisarik/ap_experimental`, kiosk, general NUC desktop work, native mobile
applications, VPS showcase, and observability/Grafana. That ordering is
priority context only; it does not authorize those later tracks here.

### Bounded Multi-Model Metadata Draft Comparison and Promotion

Status: `FROZEN — separate future logical whole, outside the current MVP path`

Preservation notes:

- persistent multi-model AI drafts, inline model picker, draft comparison, and
  draft promotion remain unimplemented and must not be treated as immediately
  actionable;
- related accepted direction remains in
  [ADR-0023](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md)
  and [AI_WORKSPACE.md](AI_WORKSPACE.md);
- do not reopen this whole inside ordinary metadata, Gallery, or acquisition
  tasks.

### Movie Identification, Reasoning Lifecycle and Movies Taxonomy

Status: `FROZEN — separate future logical whole, outside the current MVP path`

Goals when reopened:

- reliably identify a movie title from supplied movie media;
- use provider reasoning only where materially required;
- reconstruct and verify the complete reasoning lifecycle;
- verify how the application waits for reasoning and the final result;
- verify structured output handling;
- investigate the prior `PROVIDER_INVALID_RESPONSE`;
- determine whether the failure came from provider response shape, reasoning
  lifecycle, timeout/wait handling, parsing, validation, or orchestration;
- classify the movie genre;
- generate subtags belonging to the dedicated Kronika `Movies` category or
  taxonomy;
- preserve exact run-to-result association;
- preserve provider privacy and explicit owner authorization.

Explicit boundaries:

- do not combine this with ordinary still-image analysis;
- do not combine this with meme analysis;
- do not combine this with short-video analysis;
- do not reintroduce it into the current AI quick-action flow;
- do not use movie identification as a provider acceptance fixture;
- automatic movie analysis remains prohibited.

Future reopening requirements:

- fresh ORCHESTRATOR;
- fresh Worker;
- separate fixtures;
- separate provider acceptance;
- explicit reasoning and structured-output architecture gate;
- Codex 5.6 Sol Extra High may be appropriate for the initial
  architecture/reasoning investigation;
- Extra High must not be used automatically for every later bounded
  correction.

Related accepted architecture for classification and movie-identification
profile direction remains in
[ADR-0045](docs/adr/0045-content-classification-and-movie-identification.md).
This frozen whole does not amend that ADR.

### Processed Publish Workflow and Admin List View

Status: `CLOSED — delivered as Processed Publish Workflow and Responsive Admin
List View + production deployment`

Page-scoped administrator selection and bounded batch actions were delivered as
the successor whole `Admin Media Selection and Bounded Batch Actions` and are
also closed. Do not reopen either whole from stale roadmap text.

Owner goals recorded for this whole:

- responsive admin-only list view;
- compact small thumbnails;
- checkboxes;
- multi-select;
- clear states: unpublished; analyzed and unpublished; published;
- dark-green visual state for analyzed and unpublished;
- bulk first analysis;
- explicit bulk publish;
- truthful per-item and aggregate progress;
- safe partial-failure handling;
- published media remains in the main user-facing Gallery;
- unpublished media remains admin-only;
- optional subdued or semi-transparent admin representation where product
  evidence supports it;
- analysis must never automatically publish;
- publishing remains an explicit admin action.

Likely concerns recorded for this whole and its successor, without
pre-designing beyond authorized work:

- durable publish state;
- authorization and audit actions;
- ordinary-user visibility;
- idempotency;
- duplicate protection;
- bulk progress;
- partial success;
- retry semantics;
- provider cost control;
- selective owner acceptance.

Explicit boundaries recorded for this whole:

- do not retrofit bulk architecture into the individual card quick action;
- do not claim that canonical AI metadata save equals publication;
- do not mix YouTube acquisition;
- do not mix per-user metadata;
- do not mix kiosk behavior.

Existing automatic `Processed` workflow-collection membership from durable tag
saves ([ADR-0030](docs/adr/0030-automatic-processed-collection.md)) is not
publication and must not be treated as this closed publish workflow.

### Responsive Mobile-Web Polish

Status: `PARKED — current responsive web scope accepted as sufficient for now`

Preservation notes:

- the currently accepted header and Gallery responsive behavior are sufficient
  for the current MVP stage;
- do not open a new general responsive/mobile-web logical whole without new
  direct owner-visible evidence;
- responsive correctness is still required locally for every touched UI
  surface;
- screenshot-led UI/UX production polish remains deferred;
- the future Processed admin list view must be designed responsively within its
  own logical whole;
- test narrow phone, ordinary phone, tablet/intermediate, and desktop widths
  when relevant;
- one phone width plus one desktop width is insufficient evidence;
- direct owner screenshots at intermediate widths have higher authority than
  Worker summary.

This parking does not claim that all future mobile-web work is complete.
Native Android/iOS architecture remains separate and later.

## Deferred Early Non-Goals

Deferred early non-goals include mobile-native completeness, public hosting,
transcoding cluster, embedded libVLC, automatic global synchronization, every
source adapter, multi-user SaaS, automatic self-updates, VeraCrypt UI, Kiosk,
and screenshot-led UI/UX production polish.

These items MUST NOT be pulled into early implementation without explicit
Orchestrator and Cooperator approval.
