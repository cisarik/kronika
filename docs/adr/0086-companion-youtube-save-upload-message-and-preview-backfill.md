# ADR-0086: Companion YouTube Save, Upload Message, and Preview Backfill

## Status

`Accepted`

This ADR records behavior present at
`cb5fa4ddc06ab5ba4f2af48eed67b6eabf036738`. It does not change application
behavior, route policy, or the accepted security boundary.

## Decision Date

2026-11-08

## Context

[ADR-0067](0067-administrator-companion-review-inbox-and-mutation-trust.md)
recorded the first four `companion_mutation` routes.
[ADR-0079](0079-administrator-automatic-analysis-runtime-setting.md) added the
fifth, `PUT /api/admin/settings/automatic-analysis`, and left older ADR bodies
that said "exactly four" frozen. Living documents then said "exactly five."

The accepted companion and gallery work adds a YouTube save from the player,
a title-bar upload message, a YouTube content script, and an in-process
gallery-preview backfill. Those facts need one accepted decision. Historical
ADR bodies stay unchanged.

## Decision

1. **Sixth `companion_mutation` route.** `POST /api/youtube/requests` is the
   sixth flagged route. It requires capability `youtube.request`, records
   audit action `youtube.request.submit` before the action, and accepts only
   body confirmation `confirmation_method: "interactive"`. An omitted or
   non-interactive confirmation, including `yes_flag`, returns 400 and does
   not submit. `POST /api/youtube/requests/{request_id}/retry` stays
   unflagged.

2. **`open_upload`.** The hosted shell accepts `open_upload` only from the
   pinned extension origin and only when the message source is the parent
   window. The shell opens the upload dialog only when the upload control is
   visible. The companion title-bar upload control posts `open_upload` only
   after the sidebar handshake. Disconnect stays in Settings.

3. **In-process preview backfill.** `GalleryPreviewBackfillCoordinator` runs
   in-process on one thread when `gallery_preview_backfill_enabled` is true.
   The setting defaults to true and maps from
   `KRONIKA_GALLERY_PREVIEW_BACKFILL_ENABLED`. A pass generates at most 12
   items. A failed item is skipped for 60 passes. The coordinator shuts down
   inside the 20-second application shutdown budget. GET
   `/api/media/{media_id}/locations/{location_id}/gallery-preview` never
   generates a preview.

4. **YouTube content-script permissions.** The extension content script
   matches `https://www.youtube.com/*` and `https://m.youtube.com/*` only.
   `optional_host_permissions` stays `https://*.ts.net/*`. There is no
   `host_permissions` key.

## Superseded statements

No prior ADR body is edited. This ADR does not edit ADR-0064, ADR-0072,
ADR-0073, ADR-0076, ADR-0079, or ADR-0080. It supersedes the living "exactly
five `companion_mutation` routes" sentences. The accepted ADR bodies that
recorded four or five routes remain historical.

## Consequences

Companion YouTube save, the title-bar upload message, and in-process preview
backfill are accepted product behavior. Ordinary clients still receive no
provider secrets. Internet publication stays disabled.
