# ADR-0087: Complete Visibility-Aware Timeline Chronicle

## Status

`Accepted`

## Decision Date

2026-11-09

## Context

[ADR-0083](0083-modular-research-providers-and-administrator-curated-timeline.md)
made the Timeline the landing page and limited it to administrator-approved
records, including when the viewer is an administrator. The Cooperator accepted
a complete chronicle instead: everything already added to Kronika that the
signed-in identity may read, in a compact list rather than a second Gallery.

## Decision

The Timeline remains Kronika's landing page. It is the complete chronological
overview of existing catalog media and Search/Research content that the
signed-in identity is already authorized to read.

This supersedes only ADR-0083's sentence:

> It contains only administrator-approved records, including when the viewer is an administrator.

Timeline membership is no longer a household-publication gate. It includes
readable private, unanalysed, unfinished, failed and unpublished content.

The Timeline is a read projection, not a new persistent content entity or an
event log. One logical medium appears once. One submitted question appears once
throughout its durable request and completed-document lifecycle.

The owner and application administrator see current authorized content. Other
household members see only the content already available through existing
approved projections or existing unbound legacy-publication access. Approval,
withdrawal and binding precedence retain their existing effects. Unauthenticated
callers remain denied.

The chronicle orders items by their original catalog or request creation time,
newest first, then stable item identity. Approval does not move an item to the
top. Existing `timeline_entered_at_ms` values retain their historical
first-household-approval meaning and are not rewritten.

The compact Timeline list shows kind, title, creation date, lifecycle status
and visibility or publication state. Gallery remains the media working view.
Personal History remains the caller's question and research history and the
existing personal-record view.

Administrator approval remains required for household publication of completed
common records and successfully analyzed bound media. Private work does not
become household-visible merely because it appears on its owner's or an
administrator's Timeline.

The preview route, audience policy, quarantine, validation, identity model and
internet-publication prohibition remain unchanged. Timeline reads neither start
generation nor request derivative generation.

### Transition and compatibility

- No content migration, backfill, reset, binding creation or historical-row rewrite.
- Existing media becomes visible through the read union when already authorized.
- Existing saved questions collapse to their completed-record representation.
- Withdrawal removes cross-owner household access; the item remains visible to its owner and an administrator as private or withdrawn.
- Reapproval changes the household projection and publication state without changing chronicle identity or creation ordering.
- Reanalysis may change the owner or administrator's current status while the household retains its approved result.
- Existing unbound publication access is preserved as a legacy path. Listing an item neither grants approval nor invokes publication.
- `/api/timeline` keeps its URL and page envelope and receives the chronicle item contract. This is a coordinated pre-alpha API change. No compatibility adapter or second Timeline endpoint is added.
- `/api/my/records`, administrator records, record detail and render, and research request contracts retain their existing behavior.

ADR-0083's other decisions and its historical body remain unchanged.

## Consequences

The main page can show private and unfinished work to the caller who may already
read it. Counts, search and filters use the same authorization as the rows.
Every linked detail, preview, content and download request still reauthorizes.
The mixed-source ordering and literal search may scan; a later index requires
its own migration decision. Offset pages can move between separate HTTP requests.

## Revisit

Revisit this decision if household publication should again be the only Timeline
membership, if the chronicle should become an event log, or if a measured
production catalog requires a new ordering index.

## Rejected alternatives

- Keep the approved-only Timeline and add another "All items" destination.
- Insert synthetic common records for every medium.
- Merge Gallery and History in the browser.
- Append every location, analysis run and approval as an item.
- Broaden household access to achieve completeness.
