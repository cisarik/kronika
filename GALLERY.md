# Kronika Gallery Product Reference

## Status

This is a living permanent product and UX reference. It records accepted gallery
direction and the current pre-alpha persistent-preview Gallery boundary.

Classification: living permanent product architecture/UX reference.

Consumers: Orchestrator, Worker, designers, implementers, maintainers, and
security reviewers.

Retention: remains while the gallery product subsystem exists.

Inbound links: [README.md](README.md), [PRODUCT.md](PRODUCT.md),
[SPEC.md](SPEC.md), [ROADMAP.md](ROADMAP.md), [ADR-0022](docs/adr/0022-selective-media-placement-and-server-aggregation.md),
[ADR-0035](docs/adr/0035-authoritative-server-and-client-state-model.md),
[ADR-0021](docs/adr/0021-tauri-desktop-shell.md),
[AI_WORKSPACE.md](AI_WORKSPACE.md), [COVER_PIPELINE.md](COVER_PIPELINE.md),
[ADR-0023](docs/adr/0023-manual-first-metadata-and-multi-model-ai-drafts.md),
[ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md), and
[ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md).

Cleanup/update owner: future explicitly authorized Worker under an Orchestrator
task. Git history remains the archive.

## Flagship Role

The gallery is a flagship Kronika capability. It should be premium, dark,
cover-first, fast, and useful for large personal collections. The current
repository implements a persistent media catalog and packaged Gallery/Details
browser with display-title search, canonical-tag AND filters, content-category
filters, creator-chip filtering, publication-gated ordinary visibility, durable
manual cover priority with regenerable cover thumbnails, and identity-only
playback/download handoff for available locations. Complete Cover Studio,
imported/AI/series covers, cover candidates, and full premium-gallery polish
remain later work.

The gallery should feel like a media product, not a generic administrative
dashboard.

## Media Detail And Metadata Workspace

The media detail direction is manual-first. The current packaged Catalog
browser includes a bounded manual media editor for one selected imported
medium. Opening the editor loads existing title, description, and tag metadata;
it must not trigger an AI call, save catalog metadata, or mutate the filesystem.
When no durable title exists, the browser may show a fallback label derived from the first
deterministic relative location, but the display title, physical filename,
library-relative path, suggested filename, catalog save, and future physical
rename operation remain separate concepts.

The current editor exposes ordinary `Title`, `Description`, `Tags`, `Save`, and
`Cancel` controls. Tag creation happens from the same search/add control and
uses hidden stable tag keys; those internal keys, collection assignment, and
`Processed` workflow state are not ordinary editor controls. The `Processed`
membership remains derived automatically from durable tag saves, not chosen
manually. The editor can explicitly run NVIDIA-backed analysis for an imported
available GIF or MP4 after confirmation. That request sends up to three
optimized preview frames plus bounded metadata, disables model reasoning for the
validated JSON request, and can replace the current unsaved `Title`,
`Description`, and `Tags` fields directly only after explicit confirmation. The
confirmation must state that current unsaved fields will be replaced, that the
result is not saved automatically, and that no physical rename occurs. A
successful analysis may reveal one compact editable `Suggested filename` field
inside the same editor; that field is not included in metadata Save and does
not rename the file. Save remains explicit and closes the editor on success.
Persistent drafts, model comparison, provider Settings, and explicit physical
rename remain future work.

The Details dialog now plays real local GIF and MP4 content through the
identity-only `GET /api/media/{media_id}/locations/{location_id}/content`
endpoint when the first supported catalog location is `available`. It selects
only by `media_id` and `location_id`; it never constructs playback URLs from
relative paths, absolute paths, or library roots. Video uses a native `<video>`
element with controls, metadata preload, and `playsinline`; animated images use
a real `<img>`.

Available local Gallery cards use persistent server-generated static JPEG
gallery preview derivatives through the identity-only
`GET /api/media/{media_id}/locations/{location_id}/gallery-preview` endpoint
when the derivative is ready. Initial card rendering does not require original
GIF or MP4 transfer, does not call local media-analysis preview, and does not
generate derivatives. Missing or unavailable derivatives use a compact
non-original fallback until explicit playback. Activating the card's media
surface by pointer or keyboard replaces the static derivative or fallback in
the same card with original GIF or MP4 content from the identity-only
`/content` endpoint. Static cards do not show a permanent central play icon,
corner badge, or playback overlay; the media surface itself remains the
accessible playback control with visible focus. Opening Details from the card
title continues to use original GIF/MP4 content and Details playback semantics.
Gallery-preview generation remains an explicit server-operator action. These
persistent gallery previews are not durable accepted covers, cover candidates,
catalog metadata, or Cover Studio state. Details uses a black player-first
surface. Native/VLC playback, managed downloads, and a broader media player are
future work.

Available supported GIF and MP4 cards also expose compact overlay actions on
the media surface: a brain `Analyze by AI` control in the top-right corner for
administrators on supported non-movie cards, Edit in the bottom-left corner, and Open original media in
the bottom-right corner. Cards do not display canonical tag chips, a hidden-tag
counter, or reserved tag space; the card body contains only the media title.
Open original media targets the identity-only
`GET /api/media/{media_id}/locations/{location_id}/content` endpoint in a new
tab or window, so the opened document is the direct GIF or MP4 resource rather
than a wrapper page. This browser transfer does not expose filesystem paths,
does not mutate the catalog, does not claim client-local availability, and does
not replace the separate explicit attachment-download API.

Supported non-movie GIF and MP4 cards show a direct `Analyze` action for
administrators with `analysis.run` and `metadata.canonical.write` upon hover
(and on coarse pointers). It runs or re-runs AI analysis and opens the
per-field review editor; it is not durable proof that analysis has or has not
happened, and it is not restricted to incomplete metadata. Hosted companion
Gallery and ordinary identities hide the card shortcut. When server AI is
available, the shortcut confirms cloud upload, opens the existing metadata
editor immediately, and resolves proposal strips beside Title, Description, and
Tags after the identity-only suggestion preview returns. Current canonical
values are not replaced. Persistence still requires `Save`, and the physical
file is not renamed. Dismissing the editor leaves canonical metadata unchanged.
When server AI is unavailable, the shortcut is natively disabled and does not
open the AI Status panel or send an analysis request.

Gallery cards do not display the internal built-in `Processed` workflow label
or timestamp. The `Processed` catalog scope and automatic persistence semantics
remain available as catalog behavior, but the card surface should not expose
that internal workflow state as a visible status block.

Details prioritizes the original player, clickable canonical tags, and the
persisted description. Details tag buttons activate the existing Gallery tag
filter while preserving the current text query and other selected filters.
Generic visible media-kind text and the prominent `Processed since` panel are
not part of the main Details presentation; processed data remains internal and
may appear inside Technical details. After acceptance of this presentation,
Gallery cards and Details visual presentation are frozen for the MVP except for
concrete defects.

The Gallery header and pagination should stay compact: the temporary `FN` mark
is sufficient non-interactive branding in the current shell, visible `Cloud`
and `🧠 AI` status labels should stay short while preserving accessible state
truth, and pagination should offer bounded page-size choices without verbose
navigation labels. The current browser omits visible `Catalog` and `Imported
media` headings for compactness.

Developer-oriented Library tools do not belong in the flagship Gallery
surface. Future library selection belongs in `Settings > General`.
Server-managed upload, catalog synchronization, server media storage, and
download-on-demand remain unresolved separate product and architecture scope.
Tag editing should support suggestions, keyboard and mouse navigation,
rounded removable chips, an explicit `×` control, visible
hover/focus/selected/AI-suggested/invalid states, case-insensitive duplicate
prevention, and immediate local filtering without progress UI for trivial local
search.

## Visual And Interaction Direction

The primary gallery surface should emphasize media covers, readable titles,
availability, collection context, and quick filtering. Interaction should feel
polished and responsive, with restrained motion and truthful state changes.

"Wow-quality" in Kronika means deliberate detail, strong cover presentation,
clear state, careful animation, and reduced friction. It does not mean fake
progress, excessive decoration, or obscuring metadata.

## Logical Item Cards

The gallery normally displays one logical media card rather than one duplicate
card per physical file. A card may summarize multiple known physical locations.
Users must be able to inspect the location list when needed.

Remote-only media cards should remain visible when metadata and covers are
available, even if the full media bytes are not stored locally.

## Location And Availability Badges

Cards should distinguish states such as:

- local and available;
- remote and available;
- known but offline;
- archived on server;
- missing;
- unverified;
- transferring;
- download available;
- stream available.

Badges must be clear without overwhelming the cover. The user should understand
whether an action will open local media, stream remote media, or require a
download.

## Covers And Thumbnails

Covers are durable user-visible representative images. Derived thumbnails are
cache artifacts derived from durable sources. Original approved covers and
reproducible derived thumbnails remain separate concepts.

The first durable manual cover foundation is implemented ([ADR-0050](docs/adr/0050-durable-manual-cover-foundation.md),
migration `0022`): an administrator can open `Choose cover` from the Details
surface, select a source frame on a server-authoritative timeline, preview the
ephemeral frame, and explicitly `Set as cover`. Still-image (JPEG/PNG) media
also support manual cover authoring (migration `0023`): the dialog renders a
bounded preview of the source image with no timeline, scrubber, or timestamp
selector, and `Set as cover` accepts the still image itself as its durable
cover. At most one accepted cover exists per logical medium. Gallery cards
prefer the validated `cover-thumbnail`
derivative when `cover_ready` is true and fall back to the existing
`gallery-preview`/fallback path; the existing preview is never reclassified as
the accepted cover. Full Cover Studio, imported-image covers, AI-generated
covers, series covers, and candidate management remain future work.

Remote-only cards may use synchronized cover identity/provenance and derived
JPEG thumbnails. The full video must not be downloaded merely to render a card.

Normal `Play` is conceptually separate from cover timestamp selection. A cover
timestamp may represent a useful image; playback should still start at the
normal beginning unless the user chooses another playback action.

The future Cover Studio is manual-first: the user scrubs a timeline, sees the
exact current timestamp, previews the selected source frame, and explicitly
uses `Set as cover`. Cover candidates may later include a selected source frame,
the currently accepted cover, an imported image, or an AI-generated image. A
candidate is not active merely because it was created or generated; explicit
human acceptance is required. These rules are recorded in
[COVER_PIPELINE.md](COVER_PIPELINE.md) and
[ADR-0024](docs/adr/0024-cover-studio-and-ai-cover-candidates.md).

## MEME Collection Experience

GIF and short MP4 meme collections are first-class. The semantic canonical tag
or collection is `Meme`; the UI may style the collection label as `MEME`.

A future NUC-hosted MEME archive may allow a desktop to show the full MEME
collection through synchronized metadata and covers while storing few or none of
the media bytes locally. Remote-only meme cards should clearly identify remote
availability and offer explicit stream or download actions.

## Search And Filtering

The gallery must eventually support title/name search and canonical tag
filtering. The current packaged browser has a catalog slice that searches
persisted display titles, filters repeated canonical tags with
AND/intersection semantics, and lets the user manually edit the current
display title and ordered canonical-tag assignment for one selected imported
medium. Multiple selected tags default to AND/intersection semantics. For
example, selected tags `Meme`, `Reaction`, and `Money` normally return items
containing all three tags.

The UI should use filter chips and removable active filters. Filters should
include:

- canonical tags;
- local/remote availability;
- device;
- library;
- media kind;
- collection;
- platform/source where available.

The current packaged browser implements a virtual `All media` Catalog scope and
an optional built-in `Processed` Catalog scope that restricts the page to media
whose durable tag saves entered `Processed`. Arbitrary user-created collection
filters remain future work. A future advanced OR mode may exist, but AND is the
default.

## Preview And Playback

GIF and short-video inline preview should be supported separately from full
playback. Inline preview should be lightweight, respectful of reduced motion,
and should not replace the full playback backend.

External VLC remains the first intended full-playback direction for long videos.
Embedded libVLC remains deferred.

## Future Actions

Future card or detail actions may include:

- `Stream`;
- `Download + Copy to Clipboard`;
- `Show locations`;
- `Reveal in Finder` or equivalent file manager;
- `Open in VLC`;
- `Edit metadata`;
- `Select cover`.

`Download + Copy to Clipboard` is a future native desktop capability for GIF and
short MP4 workflows. It must verify the downloaded representation before placing
it on the native clipboard and must provide fallbacks when direct paste is not
supported by the platform or target application.

## States

The gallery should define honest states for:

- initial loading;
- empty catalog;
- empty filter result;
- offline server;
- remote-only media;
- local unavailable media;
- transfer pending;
- transfer active;
- verification/finalization;
- transfer failed;
- media missing;
- cover missing;
- analysis pending;
- recoverable error.

State text should be actionable without exposing private paths, raw provider
responses, tokens, or raw diagnostics.

## Progress And Animation Rules

Determinate work should show real values when available: transferred bytes,
total bytes, percentage, speed, ETA, and verification/finalization state.

Indeterminate work may use restrained shimmer, masked text, pulse, or fade
animation. It must not show fabricated percentages, invented queue stages, or
false cancellation.

Reduced-motion users should receive static or simplified state changes.

## Accessibility

Gallery cards must support keyboard navigation, visible focus, meaningful
labels, screen-reader semantics, and non-color-only state distinctions. Tag
chips and active filters should be removable through keyboard and assistive
technology.

Inline previews must not autoplay in a way that violates reduced-motion
preferences. Motion should pause or simplify when requested.

## Performance Direction

The gallery should eventually support large libraries through lazy loading,
virtualization, cached derived thumbnails, incremental loading, and careful
state management. This document makes no unsupported numeric performance
promise.

## Boundary Model

Gallery metadata, media bytes, covers, and cache artifacts are separate:

- metadata describes logical media;
- physical locations describe where media bytes may exist;
- covers are durable selected representative images;
- derived thumbnails are reproducible cache artifacts;
- full media bytes are transferred, streamed, or opened only through explicit
  user actions or later approved automation.

This separation lets remote-only media remain visible without automatic full
byte replication.
