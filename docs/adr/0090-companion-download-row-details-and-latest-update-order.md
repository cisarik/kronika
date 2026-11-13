# ADR-0090: Companion Download Row Details and Latest-Update Order

## Status

`Accepted`

## Decision Date

2026-11-13

## Context

[ADR-0089](0089-companion-own-download-status-history.md) added a separate
Recent downloads block of the signed-in requester's own X and YouTube request
states. That decision made every download row inert and ordered the block by
newest creation. Rendered use showed that an own cataloged save should open
hosted Details, including after the save was already read, and that a request
updated after it was created should rise above older creations.

## Decision

A download row whose projection carries an own cataloged media id opens hosted
Details through the existing `open_details` web-bridge message. The row stays
in the block, does not post opened, and does not retry. A row without a media
id stays inert: no click, link, or button.

The projection keeps source, request id, label, and status, and adds exactly
`media_id` and `sort_at_ms`. `media_id` is the row's own cataloged media, or
null. An invalid non-null media id becomes null. For X, that id is the first
asset in ascending ordinal whose state is `cataloged` and whose media id is a
UUID. For YouTube, it is the request's media id when that value is a UUID.
`sort_at_ms` is a valid nonnegative `updated_at_ms`, otherwise a valid
`created_at_ms`. A row with neither is omitted.

The block still shows at most five rows. Each available request capability is
read once at `limit=50` and cursors are not followed. Rows are deduplicated by
source and request id, then ordered by `sort_at_ms` descending, source
ascending, and request id descending.

Static status text, the separate block, independent download-failure hiding,
and the review-only badge remain as ADR-0089 decided them.

This supersedes only ADR-0089's inert-row and creation-order statements.
ADR-0089's body stays unchanged. ADR-0076 review routing, hosted review clicks,
and the review-only badge remain.

## Consequences

The visible block can open an own cataloged medium and can show a recently
updated older request ahead of a newer creation that has not been updated.
The read stays bounded at fifty rows per source and five visible rows. No new
message type, route, permission, storage key, or mutation is added.

## Revisit when

A later decision needs a complete request archive or a closed-panel alert.
