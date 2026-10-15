# Kronika Architecture Decision Records

## Purpose

Architecture Decision Records (ADRs) document **accepted** architecture decisions for Kronika. Each ADR explains the context, the decision, rationale, consequences, and how the decision may be revisited.

ADRs are normative for the decisions they record. They do not replace [SPEC.md](../../SPEC.md) product requirements, but they resolve architecture choices that implementation must follow.

## Relationship to evidence packages

[ADR-0082](0082-kronika-one-product-and-private-records.md) records the
accepted one-product Kronika transition, not completed implementation. It
supersedes conflicting publication/audience direction for new records while
preserving earlier ADRs as historical decisions and retaining their unaffected
foundations. Its relationship table identifies the affected boundaries; older
`Accepted` rows do not override that later decision.
[ADR-0083](0083-modular-research-providers-and-administrator-curated-timeline.md)
partially supersedes ADR-0082 for capture-only Search and Research, owner-only
private reading, direct owner sharing and completion-triggered Timeline entry.
The ADR-0082 historical reasoning stays in that file. Unaffected ADR-0082
decisions remain accepted. ADR-0083 is current where they conflict.
[ADR-0084](0084-administrator-managed-research-settings-and-versioned-pricing.md)
partially supersedes ADR-0083's fixed-model decision and the interpretation that
every bounded administrator adjustment requires another architecture decision;
it preserves the rest of ADR-0083.
[ADR-0085](0085-kronika-sole-identity.md) partially supersedes ADR-0082 by
citation, not by patch: the internal `framenest` package, migration history,
compatible HTTP headers and deployment identifiers staying, and the exclusion of
a mass `framenest` rename. It fixes Kronika as the sole product identity and
names the frozen residues. The ADR-0082 historical reasoning stays in that
file. Unaffected ADR-0082 decisions remain accepted. ADR-0085 is current where
they conflict.

Evidence packages such as [ARCHITECTURE_FOUNDATION_EVIDENCE.md](../ARCHITECTURE_FOUNDATION_EVIDENCE.md) collect primary-source research and provisional recommendations. Evidence packages are **not** accepted decisions. Only an ADR with status **Accepted** records project authority for an architecture choice.

## Permitted statuses

| Status | Meaning |
|---|---|
| **Proposed** | Drafted for review; not yet authoritative |
| **Accepted** | Explicitly approved and binding until superseded |
| **Superseded** | Replaced by a later ADR; retained for history |
| **Rejected** | Considered and not adopted; retained for history |

An accepted ADR may only be changed by a later ADR that supersedes it. Editing an accepted ADR in place without a superseding ADR is not permitted.

## Index

| ADR | Title | Status | Decision date | Link |
|---|---|---|---|---|
| 0001 | Supported Python Version | Accepted | 2026-06-23 | [0001-supported-python-version.md](0001-supported-python-version.md) |
| 0002 | Python Environment and Dependency Manager | Accepted | 2026-06-23 | [0002-python-environment-and-dependency-manager.md](0002-python-environment-and-dependency-manager.md) |
| 0003 | Initial Server API Framework | Accepted | 2026-06-23 | [0003-initial-server-api-framework.md](0003-initial-server-api-framework.md) |
| 0004 | Repository Layout | Accepted | 2026-06-23 | [0004-repository-layout.md](0004-repository-layout.md) |
| 0005 | Configuration Strategy | Accepted | 2026-06-23 | [0005-configuration-strategy.md](0005-configuration-strategy.md) |
| 0006 | macOS Python Interpreter Provider | Accepted | 2026-06-23 | [0006-macos-python-interpreter-provider.md](0006-macos-python-interpreter-provider.md) |
| 0007 | Python Settings Library | Accepted | 2026-06-24 | [0007-settings-library.md](0007-settings-library.md) |
| 0008 | Initial ASGI Runtime | Accepted | 2026-06-24 | [0008-asgi-runtime.md](0008-asgi-runtime.md) |
| 0009 | Initial Structured Logging Approach | Accepted | 2026-06-24 | [0009-structured-logging-approach.md](0009-structured-logging-approach.md) |
| 0010 | Initial SQLite Persistence and Migration Strategy | Accepted | 2026-06-24 | [0010-initial-persistence-foundation.md](0010-initial-persistence-foundation.md) |
| 0011 | Stable Domain Identities | Accepted | 2026-06-24 | [0011-stable-domain-identities.md](0011-stable-domain-identities.md) |
| 0012 | Initial Device Registry and Repository Boundary | Accepted | 2026-06-24 | [0012-initial-device-registry.md](0012-initial-device-registry.md) |
| 0013 | Initial Library Registry and Device-Local Root Locators | Accepted | 2026-06-24 | [0013-initial-library-registry.md](0013-initial-library-registry.md) |
| 0014 | Safe Read-Only Library Scan Preview | Accepted | 2026-06-24 | [0014-safe-library-scan-preview.md](0014-safe-library-scan-preview.md) |
| 0015 | Deterministic Local Media Analysis Preparation | Accepted | 2026-06-24 | [0015-deterministic-local-media-analysis-preparation.md](0015-deterministic-local-media-analysis-preparation.md) |
| 0016 | Provider-Neutral Media Suggestions and NVIDIA NIM Prototype | Accepted; live generic prompt contract succeeded by [ADR-0069](0069-five-tag-generic-media-suggestion-contract.md) | 2026-06-24 | [0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md](0016-provider-neutral-media-suggestions-and-nvidia-nim-prototype.md) |
| 0017 | Initial Local Web Application Delivery | Accepted | 2026-06-24 | [0017-initial-local-web-application-delivery.md](0017-initial-local-web-application-delivery.md) |
| 0018 | Local Media Analysis Preview API | Accepted | 2026-06-24 | [0018-local-media-analysis-preview-api.md](0018-local-media-analysis-preview-api.md) |
| 0019 | VLM Image Derivatives and NVIDIA Instruct Mode | Accepted | 2026-06-24 | [0019-vlm-image-derivatives-and-nvidia-instruct-mode.md](0019-vlm-image-derivatives-and-nvidia-instruct-mode.md) |
| 0020 | On-Demand AI Suggestion Review | Accepted | 2026-06-25 | [0020-on-demand-ai-suggestion-review.md](0020-on-demand-ai-suggestion-review.md) |
| 0021 | Tauri Desktop Shell | Accepted | 2026-06-25 | [0021-tauri-desktop-shell.md](0021-tauri-desktop-shell.md) |
| 0022 | Selective Media Placement and Server Aggregation | Accepted; server-authority portions superseded by [ADR-0035](0035-authoritative-server-and-client-state-model.md) | 2026-06-25 | [0022-selective-media-placement-and-server-aggregation.md](0022-selective-media-placement-and-server-aggregation.md) |
| 0023 | Manual-First Metadata and Multi-Model AI Drafts | Accepted | 2026-06-25 | [0023-manual-first-metadata-and-multi-model-ai-drafts.md](0023-manual-first-metadata-and-multi-model-ai-drafts.md) |
| 0024 | Cover Studio and AI Cover Candidates | Accepted | 2026-06-25 | [0024-cover-studio-and-ai-cover-candidates.md](0024-cover-studio-and-ai-cover-candidates.md) |
| 0025 | Minimum Persistent Media Catalog Foundation | Accepted | 2026-06-25 | [0025-minimum-persistent-media-catalog-foundation.md](0025-minimum-persistent-media-catalog-foundation.md) |
| 0026 | Explicit Idempotent Scan Candidate Import | Accepted | 2026-06-25 | [0026-explicit-idempotent-scan-candidate-import.md](0026-explicit-idempotent-scan-candidate-import.md) |
| 0027 | Persistent Display Title and Canonical Tags | Accepted | 2026-06-25 | [0027-persistent-display-title-and-canonical-tags.md](0027-persistent-display-title-and-canonical-tags.md) |
| 0028 | Catalog Read Model and Search Semantics | Accepted | 2026-06-26 | [0028-catalog-read-model-and-search-semantics.md](0028-catalog-read-model-and-search-semantics.md) |
| 0029 | Persistent Plain-Text Media Description | Accepted | 2026-06-26 | [0029-persistent-plain-text-media-description.md](0029-persistent-plain-text-media-description.md) |
| 0030 | Automatic Processed Collection from Durable Tag Saves | Accepted | 2026-06-26 | [0030-automatic-processed-collection.md](0030-automatic-processed-collection.md) |
| 0031 | Fedora systemd Service Foundation | Superseded by [ADR-0032](0032-ubuntu-nuc-deployment-foundation.md) | 2026-07-06 | [0031-fedora-systemd-service-foundation.md](0031-fedora-systemd-service-foundation.md) |
| 0032 | Ubuntu NUC Deployment Foundation | Accepted | 2026-07-08 | [0032-ubuntu-nuc-deployment-foundation.md](0032-ubuntu-nuc-deployment-foundation.md) — current “personal production server” role framing superseded by [ADR-0075](0075-nuc-development-test-target-and-routine-release-refresh.md) |
| 0033 | Catalog Backup and Recovery Foundation | Accepted | 2026-07-08 | [0033-catalog-backup-and-recovery-foundation.md](0033-catalog-backup-and-recovery-foundation.md) |
| 0034 | Canonical Analytic Programming Integration | Accepted | 2026-07-11 | [0034-canonical-analytic-programming-integration.md](0034-canonical-analytic-programming-integration.md) |
| 0035 | Authoritative Server and Client State Model | Accepted | 2026-07-11 | [0035-authoritative-server-and-client-state-model.md](0035-authoritative-server-and-client-state-model.md) |
| 0036 | Production AI Credentials via systemd Credentials | Accepted | 2026-07-11 | [0036-production-ai-credentials-via-systemd.md](0036-production-ai-credentials-via-systemd.md) |
| 0037 | Durable Upload Session and Safe Ingest Foundation | Accepted | 2026-07-14 | [0037-durable-upload-session-and-safe-ingest-foundation.md](0037-durable-upload-session-and-safe-ingest-foundation.md) |
| 0038 | Bounded Upload Media Validation | Accepted | 2026-07-14 | [0038-bounded-upload-media-validation.md](0038-bounded-upload-media-validation.md) |
| 0039 | Lifecycle-Owned Upload Validation Orchestration | Accepted | 2026-07-14 | [0039-lifecycle-owned-upload-validation-orchestration.md](0039-lifecycle-owned-upload-validation-orchestration.md) |
| 0040 | Canonical Upload Byte Identity Foundation | Accepted | 2026-07-15 | [0040-canonical-upload-byte-identity-foundation.md](0040-canonical-upload-byte-identity-foundation.md) |
| 0041 | Exact-Byte Upload Duplicate Disposition | Accepted | 2026-07-18 | [0041-exact-byte-upload-duplicate-disposition.md](0041-exact-byte-upload-duplicate-disposition.md) |
| 0042 | Atomic Upload Publication | Accepted | 2026-07-18 | [0042-atomic-upload-publication.md](0042-atomic-upload-publication.md) |
| 0043 | Published-to-Cataloged Upload Transaction | Accepted; Gallery-eligibility portion superseded by [ADR-0049](0049-durable-content-publication-boundary.md) | 2026-07-19 | [0043-upload-to-catalog-transaction.md](0043-upload-to-catalog-transaction.md) |
| 0044 | Durable Automatic Post-Catalog AI Analysis | Accepted; X never-enqueue carve-out succeeded by [ADR-0066](0066-administrator-owned-x-automatic-generic-analysis.md) | 2026-07-19 | [0044-durable-automatic-post-catalog-analysis.md](0044-durable-automatic-post-catalog-analysis.md) |
| 0045 | Content Classification, Acquisition Source, and Movie Identification | Accepted; companion scope supplemented by [ADR-0070](0070-companion-exclusion-of-movie-workflows.md) | 2026-07-22 | [0045-content-classification-and-movie-identification.md](0045-content-classification-and-movie-identification.md) |
| 0046 | YouTube Manual Ingestion and Provenance | Accepted | 2026-07-23 | [0046-youtube-manual-ingestion-and-provenance.md](0046-youtube-manual-ingestion-and-provenance.md) |
| 0047 | Operator CLI Configuration and Working-Directory Hygiene | Accepted | 2026-07-23 | [0047-operator-cli-configuration-and-working-directory-hygiene.md](0047-operator-cli-configuration-and-working-directory-hygiene.md) |
| 0048 | Tailscale Remote Access and Identity Foundation | Accepted | 2026-07-25 | [0048-tailscale-remote-access-and-identity-foundation.md](0048-tailscale-remote-access-and-identity-foundation.md) |
| 0049 | Durable Content Publication Boundary | Accepted; explicit-route-only publication superseded narrowly by [ADR-0068](0068-companion-review-save-and-readiness-triggered-publication.md) | 2026-07-29 | [0049-durable-content-publication-boundary.md](0049-durable-content-publication-boundary.md) |
| 0050 | Durable Manual Cover Foundation | Accepted | 2026-08-02 | [0050-durable-manual-cover-foundation.md](0050-durable-manual-cover-foundation.md) |
| 0051 | Administrator Catalog Removal and Safe Catalog Retirement | Accepted | 2026-08-04 | [0051-administrator-catalog-removal.md](0051-administrator-catalog-removal.md) |
| 0052 | Automated Catalog Backup, Retention and Restore Verification | Accepted | 2026-08-04 | [0052-automated-catalog-backup-retention-and-restore-verification.md](0052-automated-catalog-backup-retention-and-restore-verification.md) |
| 0053 | Ordinary-User Upload Submission and Administrator Review Boundary | Accepted | 2026-08-05 | [0053-ordinary-user-upload-submission-and-administrator-review-boundary.md](0053-ordinary-user-upload-submission-and-administrator-review-boundary.md) |
| 0054 | Requester-Private YouTube Acquisition and Promotion Boundary | Accepted | 2026-08-06 | [0054-requester-private-youtube-acquisition-and-promotion-boundary.md](0054-requester-private-youtube-acquisition-and-promotion-boundary.md) |
| 0055 | YouTube Category, Creator Attribution, and Immutable Acquisition Provenance | Accepted | 2026-08-06 | [0055-youtube-creator-taxonomy-and-immutable-provenance.md](0055-youtube-creator-taxonomy-and-immutable-provenance.md) |
| 0056 | Off-Device Catalog Backup Copy and Restore Verification | Accepted | 2026-08-08 | [0056-off-device-catalog-backup-copy-and-restore-verification.md](0056-off-device-catalog-backup-copy-and-restore-verification.md) |
| 0057 | Operator-Workstation Pull-Based Catalog Snapshot and Recovery | Accepted | 2026-08-08 | [0057-operator-workstation-pull-based-catalog-snapshot.md](0057-operator-workstation-pull-based-catalog-snapshot.md) |
| 0058 | Independent Mullvad Egress and Operator Network Recovery | Accepted | 2026-08-13 | [0058-independent-mullvad-egress-and-operator-network-recovery.md](0058-independent-mullvad-egress-and-operator-network-recovery.md) |
| 0059 | Portable Media Sidecar Round-Trip Foundation | Accepted | 2026-08-14 | [0059-portable-media-sidecar-roundtrip-foundation.md](0059-portable-media-sidecar-roundtrip-foundation.md) |
| 0060 | Repeatable Immutable NUC Release-Update Contract | Accepted; production-ceremony framing superseded by [ADR-0075](0075-nuc-development-test-target-and-routine-release-refresh.md) | 2026-08-15 | [0060-repeatable-immutable-nuc-release-update-contract.md](0060-repeatable-immutable-nuc-release-update-contract.md) |
| 0061 | X Meme Browser Companion Origin Trust | Accepted; “exactly two companion mutations” superseded by [ADR-0067](0067-administrator-companion-review-inbox-and-mutation-trust.md) | 2026-08-16 | [0061-x-meme-browser-companion.md](0061-x-meme-browser-companion.md) |
| 0062 | Per-User Media Alias Overlay | Accepted; named statements superseded by [ADR-0065](0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md); Edit affordance succeeded by [ADR-0077](0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md) | 2026-08-17 | [0062-per-user-media-alias-overlay.md](0062-per-user-media-alias-overlay.md) |
| 0063 | Companion Side-Panel Web Host | Accepted; iframe-only chrome succeeded by [ADR-0071](0071-native-side-panel-review-inbox-chrome.md) | 2026-08-17 | [0063-companion-side-panel-web-host.md](0063-companion-side-panel-web-host.md) |
| 0064 | X Save Category and Public Photo Acquisition | Accepted; named statements superseded by [ADR-0065](0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md) | 2026-08-21 | [0064-x-save-category-and-public-photo-acquisition.md](0064-x-save-category-and-public-photo-acquisition.md) |
| 0065 | X Save Edit Subset and Acquisition-Time Canonical Metadata Seed | Accepted | 2026-08-22 | [0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md](0065-x-save-edit-subset-and-acquisition-time-canonical-metadata-seed.md) |
| 0066 | Administrator-Owned X Automatic Generic Analysis | Accepted | 2026-08-23 | [0066-administrator-owned-x-automatic-generic-analysis.md](0066-administrator-owned-x-automatic-generic-analysis.md) |
| 0067 | Administrator Companion Review Inbox and Mutation Trust | Accepted; “mark-opened is administrator-only” succeeded for own-item opened by [ADR-0076](0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md) | 2026-08-23 | [0067-administrator-companion-review-inbox-and-mutation-trust.md](0067-administrator-companion-review-inbox-and-mutation-trust.md) |
| 0068 | Companion Review Save and Readiness-Triggered Publication | Accepted; “Tags replace, they do not union.” succeeded by [ADR-0073](0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md) | 2026-08-23 | [0068-companion-review-save-and-readiness-triggered-publication.md](0068-companion-review-save-and-readiness-triggered-publication.md) |
| 0069 | Five-Tag Generic Media Suggestion Contract | Accepted | 2026-08-23 | [0069-five-tag-generic-media-suggestion-contract.md](0069-five-tag-generic-media-suggestion-contract.md) |
| 0070 | Companion Exclusion of Movie Workflows | Accepted | 2026-08-23 | [0070-companion-exclusion-of-movie-workflows.md](0070-companion-exclusion-of-movie-workflows.md) |
| 0071 | Native Side-Panel Review Inbox Chrome | Accepted; collapsible toggle, `Review inbox` heading, and empty-copy chrome statements succeeded by [ADR-0072](0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md) | 2026-08-23 | [0071-native-side-panel-review-inbox-chrome.md](0071-native-side-panel-review-inbox-chrome.md) |
| 0072 | Native Side-Panel Unread Inbox and Title-Bar History Chrome | Accepted; separate unread/history lists, duplicate rows, analyzed-only history, and mark-every-row-opened statements succeeded by [ADR-0073](0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md) | 2026-08-23 | [0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md](0072-native-side-panel-unread-inbox-and-title-bar-history-chrome.md) |
| 0073 | Companion Merged History Chrome, Pending Visibility, 𝕏 Seed Tag, and Preserving Apply | Accepted; mixed-inbox, pending-overlay, and ordinary-403-hides-history statements succeeded by [ADR-0076](0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md) | 2026-08-24 | [0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md](0073-companion-merged-history-chrome-pending-visibility-x-seed-tag-and-preserving-apply.md) |
| 0074 | Dual-Audience Public Published and Tailscale Workspace Boundary | Accepted | 2026-08-25 | [0074-dual-audience-public-published-and-tailscale-workspace-boundary.md](0074-dual-audience-public-published-and-tailscale-workspace-boundary.md) |
| 0075 | NUC as Development-Test Target and Routine Release Refresh | Accepted | 2026-08-26 | [0075-nuc-development-test-target-and-routine-release-refresh.md](0075-nuc-development-test-target-and-routine-release-refresh.md) |
| 0076 | Companion History Hosted Click, Administrator Analyzed Inbox, and Ordinary Own-History | Accepted; “Edit remains `metadata.canonical.write`” succeeded by [ADR-0077](0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md) | 2026-08-26 | [0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md](0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md) |
| 0077 | Ordinary Alias Edit Affordance and Per-Field AI Suggestions | Accepted; Gallery card 🧠 bulk analyze-and-canonical-save succeeded by [ADR-0078](0078-gallery-card-ai-per-field-review.md); Load and hide-on-dropdown-change succeeded by [ADR-0080](0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md); Gallery/Details overlay display, ordinary suggestion-list GET, hosted suggestion chrome, and filename note succeeded in this file | 2026-08-26 | [0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md](0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md) |
| 0078 | Gallery Card AI Per-Field Review | Accepted; analyze-then-open order succeeded by [ADR-0080](0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md) | 2026-08-27 | [0078-gallery-card-ai-per-field-review.md](0078-gallery-card-ai-per-field-review.md) |
| 0079 | Administrator Automatic Analysis Runtime Setting | Accepted | 2026-08-27 | [0079-administrator-automatic-analysis-runtime-setting.md](0079-administrator-automatic-analysis-runtime-setting.md) |
| 0080 | Immediate Editor Suggestion Reveal and In-Modal Analysis | Accepted | 2026-08-29 | [0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md](0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md) |
| 0081 | Declarative OpenAI-Compatible Provider Registry and Administrator Vision Probe | Accepted | 2026-09-16 | [0081-declarative-openai-compatible-provider-registry-and-administrator-vision-probe.md](0081-declarative-openai-compatible-provider-registry-and-administrator-vision-probe.md) |
| 0082 | Kronika One Product and Private Records | Accepted; capture-only research, owner-only private reading, direct owner sharing and completion-triggered Timeline entry partially superseded by [ADR-0083](0083-modular-research-providers-and-administrator-curated-timeline.md) | 2026-09-23 | [0082-kronika-one-product-and-private-records.md](0082-kronika-one-product-and-private-records.md) |
| 0083 | Modular Research Providers and Administrator-Curated Timeline | Accepted; fixed-model decision partially superseded by [ADR-0084](0084-administrator-managed-research-settings-and-versioned-pricing.md) | 2026-09-26 | [0083-modular-research-providers-and-administrator-curated-timeline.md](0083-modular-research-providers-and-administrator-curated-timeline.md) |
| 0084 | Administrator-Managed Research Settings and Versioned Pricing | Accepted | 2026-09-30 | [0084-administrator-managed-research-settings-and-versioned-pricing.md](0084-administrator-managed-research-settings-and-versioned-pricing.md) |
| 0085 | Kronika Sole Identity | Accepted; ADR-0082 staged hold of the `framenest` package, migration history, HTTP headers and deployment identifiers, and the no-mass-rename exclusion, partially superseded by this ADR | 2026-10-02 | [0085-kronika-sole-identity.md](0085-kronika-sole-identity.md) |
