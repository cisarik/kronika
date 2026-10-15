# Kronika Cover Pipeline

## Status

This is living subsystem documentation for the future manual Cover Studio,
cover candidates, and derivative-generation pipeline.

Classification: living subsystem documentation.

Intended consumers: COOPERATOR, ORCHESTRATOR, WORKER, and gallery/cover
implementation tasks.

Retention trigger: retain while the cover subsystem exists.

Inbound discoverability: [README.md](README.md), [PRODUCT.md](PRODUCT.md),
[GALLERY.md](GALLERY.md), [SPEC.md](SPEC.md), [ROADMAP.md](ROADMAP.md),
[ADR-0019](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md),
and [ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md).

Update owner: only a future explicitly authorized WORKER task under
ORCHESTRATOR authority.

## Current Implementation Boundary

Kronika currently has deterministic local media-analysis preparation,
representative PNG frames for explicit preview, ephemeral JPEG derivatives for
VLM transport per
[ADR-0019](docs/adr/0019-vlm-image-derivatives-and-nvidia-instruct-mode.md),
persistent server-owned gallery preview derivatives for imported available GIF
and MP4 locations, and a durable manual cover foundation (migration `0022`,
extended by migration `0023`): at most one accepted manually selected cover per
logical medium, an immutable server-owned durable JPEG artifact, and a separate
regenerable cover thumbnail, per
[ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md). Manual cover
authoring accepts available GIF, MP4, and still-image (JPEG/PNG) sources;
still-image sources are timeless and render without a timeline, scrubber, or
timestamp selector.

Kronika does not currently implement the complete Cover Studio, persistent
cover candidates or candidate history, imported image covers, or AI-generated
covers.

## Cover Concepts

A cover is the user-visible representative image for a logical media item,
episode, or series. A derived thumbnail is a reproducible cache artifact derived
from an accepted durable cover source.

The cover subsystem must distinguish:

- selected source frame;
- exact cover timestamp;
- accepted cover;
- active cover candidate;
- manual candidate;
- imported-image candidate;
- AI-generated candidate;
- derived thumbnails;
- playback position.

## Manual Cover Studio

The primary cover workflow is manual. The future Cover Studio should allow the
user to scrub a media timeline, see the exact current timestamp, preview the
selected frame, make fine keyboard adjustments conceptually, and explicitly run
`Set as cover`.

Kronika should not set a cover merely because the user scrubs the timeline or
because a candidate preview appears. Manual source-frame selection precedes AI
cover generation.

This document does not specify final key bindings.

## Exact Cover Timestamp

When a cover is selected from media, Kronika must retain an exact durable
cover timestamp in a stable time representation selected by a future
implementation decision.

The timestamp identifies the accepted source frame for deterministic derivative
generation. It is not a playback start marker and must not be conflated with
normal playback position.

## Playback Independence

Cover timestamp does not change normal playback start. Ordinary `Play` starts
at `00:00`.

Seeking to the cover timestamp may later be offered as a separate explicit
action, but cover selection must not silently change playback behavior.

## Accepted Cover And Candidates

The accepted cover is the currently user-approved representative image.

A cover candidate is a proposed replacement. Possible candidates include a
selected source frame, the currently accepted cover, a future imported image,
and a future AI-generated image.

Only one candidate is active at a time. Creating, importing, or generating a
candidate must not activate it automatically. The prior accepted cover remains
in force until the user explicitly accepts another candidate.

Activation requires explicit human acceptance. Rejection or discard of a
candidate leaves the accepted cover unchanged.

## Derivative Generation Responsibilities

After a source frame or image is accepted as the cover, Kronika should
generate deterministic derivatives for gallery cards and other display
consumers. The accepted source and its provenance remain distinct from derived
cache artifacts.

Derivative sizes, formats, profiles, cache paths, invalidation, rebuild
behavior, retention, and cleanup remain deferred.

Derivative generation must not imply that the full video should be downloaded
merely to render an already available remote-only gallery card. Remote-only
cards may use synchronized cover identity, provenance, and lightweight derived
thumbnails when those records are available.

## Persistent Gallery Preview Derivatives

The current persistent gallery preview derivative is a server-owned cache
artifact, not catalog metadata, a cover candidate, or an accepted cover. It is
safe to delete and regenerate from one currently available imported GIF or MP4
physical location.

The implemented static format is JPEG (`image/jpeg`). The algorithm version is
`gallery-preview-jpeg-v1`. It reuses local media-analysis preparation, selects
the first deterministic representative PNG frame from the existing 10%, 50%,
90% timestamp rule, converts alpha to RGB, preserves aspect ratio, limits the
long edge to 512 pixels, encodes at JPEG quality 82 with 4:2:0 subsampling,
non-progressive and non-optimized output, and validates a maximum encoded
payload of 524,288 bytes.

The cache root is configured centrally as `KRONIKA_GALLERY_PREVIEW_CACHE_PATH`
and defaults outside the repository under the local development data directory.
It must be absolute. Generation refuses to publish cache artifacts inside any
registered source-media root. Cache filenames are deterministic versioned keys
derived from the algorithm version, media ID, location ID, media kind, and the
current safely observed source size and mtime behind the registered-root
containment boundary. A source identity change or algorithm-version change
therefore produces a new expected derivative key.

Generation is explicit through the packaged `kronika-previews` console entry
point. Local CachyOS development may use the Fish launcher form; NUC production
must use the release-local console entry point under the operator contract in
[docs/UBUNTU_NUC_DEPLOYMENT.md](docs/UBUNTU_NUC_DEPLOYMENT.md):

```text
./kronika previews status
./kronika previews generate --library-id <library-id> --yes --max-items <n>
./kronika previews generate --all --yes --max-items <n>
kronika-previews status
kronika-previews generate --library-id <library-id> --yes --max-items <n>
kronika-previews generate --all --yes --max-items <n>
```

`status` is read-only and generation-free. `generate` displays a plan before
durable cache writes and requires interactive confirmation unless `--yes` is
provided. Publication writes a temporary file inside the verified cache root,
validates the JPEG, then atomically replaces the final artifact.

Browser delivery is identity-only through:

```text
GET /api/media/{media_id}/locations/{location_id}/gallery-preview
```

The endpoint validates the media/location/library relationship, requires a
currently available supported location, serves only an already generated current
derivative, performs no generation or catalog mutation, returns inline JPEG
bytes with an ETag, supports `If-None-Match`, and does not expose source paths,
cache paths, database paths, or cache filenames.

Deferred scope remains cleanup, eviction, accepted covers, Cover Studio, cover
candidates, AI cover generation, animated previews, background generation, and
arbitrary collection or cover management.

## Durable Manual Cover Foundation (Implemented)

The first durable manually selected cover workflow is implemented and recorded
in [ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md):

- one accepted cover per logical medium at most, stored in `media_covers`
  (migration `0022`); absence means no accepted cover;
- manual source-frame selection for existing available GIF and MP4 locations,
  and manual still-image selection for available JPEG/PNG locations (migration
  `0023`); still-image sources are timeless, keep the canonical source
  timestamp `0` that is never presented as a selected temporal frame, and
  carry no fabricated duration;
- server-authoritative duration, source observation, and arbitrary-timestamp
  extraction through the existing ffprobe/ffmpeg/process boundary for GIF/MP4
  sources; still-image sources are observed and decoded through the existing
  Pillow still-image boundary with no ffprobe/ffmpeg invocation and no fake
  timeline;
- an opaque versioned `source_version` that fences preview and acceptance
  against source replacement before and during extraction;
- explicit `Set as cover` (preview never accepts); the previous accepted cover
  remains authoritative until replacement fully succeeds;
- optimistic concurrency via `expected_revision`; a stale browser tab cannot
  silently overwrite a newer cover;
- durable immutable content-addressed JPEG artifacts under
  `KRONIKA_COVER_STORAGE_ROOT` (production-oriented
  `/var/lib/kronika/covers`);
- regenerable `cover-thumbnail-jpeg-v1` derivatives under
  `KRONIKA_COVER_THUMBNAIL_CACHE_PATH` (production-oriented
  `/var/cache/kronika/cover-thumbnails`), generated only on acceptance or via
  the explicit `kronika-covers` operator CLI;
- admin-only authoring through `metadata.canonical.write` with `media.cover_set`
  security-audit-before-mutation;
- ordinary cover thumbnail reads require `gallery.read` and obey the shared
  content-publication audience policy;
- Gallery `cover_ready` means a validated cover thumbnail is present; cards
  prefer it and fall back to the existing `gallery-preview`/fallback path;
- normal Play still begins at `00:00`; cover timestamps never become playback
  state.

Durable artifacts are excluded from the catalog backup bundle (cf.
[docs/BACKUP_AND_RECOVERY.md](docs/BACKUP_AND_RECOVERY.md)); the system does not
claim complete disaster durability for cover artifacts in this slice.

## Imported Image Covers

Imported image covers are future scope. An imported image should create a cover
candidate rather than automatically replacing the accepted cover.

The imported-image workflow, validation, copying, storage, normalization,
provenance, and derivative behavior remain deferred.

## AI-Generated Cover Candidates

`Generate with AI` is a separate future workflow from metadata VLM analysis. It
should show only compatible image-generation or image-editing models. When a
selected source frame exists and a model supports reference input, the workflow
may offer that frame as reference input.

Cloud generation requires explicit cloud confirmation. Local/cloud execution
must be labelled clearly.

AI generation creates a new candidate. It preserves manual candidates, clearly
identifies generated output as AI-generated, retains provider/model provenance
or supported authenticity metadata, and requires explicit review and
acceptance. It never automatically replaces the current cover.

Kronika must not assume one permanent NVIDIA image-generation model, one
fixed provider catalog, permanent free access, or universal support for
reference images.

## Capability-Aware Model Filtering

Cover generation should filter models by provider-neutral capabilities such as
`image_generation`, `image_editing`, `reference_image`, `local_execution`, and
`cloud_execution`.

Exact capability names, discovery contracts, compatibility registries, provider
refresh behavior, and fallback policies remain deferred.

Browsing or selecting a model must not invoke a provider. A separate explicit
final action starts generation.

## Confirmation And Review Boundaries

Cover-affecting actions require clear human boundaries:

- scrubbing previews a source frame but does not set it;
- creating a candidate does not activate it;
- generating an AI image does not activate it;
- accepting a candidate activates it;
- replacing the current cover requires explicit acceptance;
- cloud execution requires explicit confirmation.

No cover workflow may silently rename, move, delete, tag, reorganize, or upload
unrelated media.

## Provenance Requirements

Manual source-frame covers should retain enough provenance to regenerate
derivatives from the accepted source frame, including the exact cover timestamp.

AI-generated candidates should retain provider/model provenance or supported
authenticity metadata when available. Imported-image provenance remains a future
contract.

Provenance must not expose secrets, raw prompts, raw provider responses,
private absolute paths, database paths, or unrelated media metadata.

## Progress, Errors, And Cancellation

Cover extraction, derivative generation, and AI generation must report truthful
state. Determinate work may show real progress when real totals are available.
Indeterminate work may use restrained pending state but must not show fabricated
percentages, invented stages, or false cancellation.

True cancellation semantics for long-running cover or generation work remain
deferred until a job or background-work architecture accepts them. The UI must
not claim cancellation that the backend cannot honor.

Errors should be actionable and sanitized. They must not expose credentials,
raw provider responses, raw prompts, absolute private paths, database paths,
raw subprocess stderr, frame payloads, or stack traces to ordinary users.

## Implementation And Deferred Boundary

This document does not define:

- persistence schema;
- migration number;
- API endpoints;
- source timestamp storage unit;
- frame-extraction backend;
- output image format;
- derivative sizes;
- cache path;
- cache invalidation;
- imported-image workflow;
- provider/model selection;
- capability contract details;
- reference-image policy;
- prompt construction;
- output dimensions;
- aspect ratio;
- content-safety handling;
- provenance format;
- timeout;
- cancellation;
- cost or trial disclosure.

Those decisions require future bounded tasks.

## Security Invariants

- Manual cover selection is the baseline.
- AI-generated covers are optional future assistance.
- Cloud generation requires explicit confirmation.
- Provider credentials remain server-side.
- Creating a cover candidate does not activate it.
- The current accepted cover remains until explicit replacement.
- Normal playback starts at `00:00`.
- No cover workflow automatically mutates media files.
