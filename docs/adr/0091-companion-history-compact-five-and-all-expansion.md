# ADR-0091: Companion History Compact Five and All Expansion

## Status

`Accepted`

## Decision Date

2026-11-13

## Context

[ADR-0090](0090-companion-download-row-details-and-latest-update-order.md)
keeps own download rows clickable when they carry a cataloged media id and
orders them by the latest request update. Its presentation still stops at five
rows from a single read window. Rendered use showed that the sixth and later
retained downloads were unreachable, and that review-history rows repeated
download titles ahead of any control that could reveal the rest.

## Decision

`DOWNLOAD_HISTORY` keeps a compact limit of five and a retained read window of
fifty. Merge still deduplicates by source and request id and orders by
`sort_at_ms` descending, source ascending, and request id descending, then
returns up to the retained window. The download response may therefore carry
more than five rows. Each available request capability is still read once at
`limit=50` and cursors are not followed.

When downloads exist, the compact history sheet shows the five latest download
rows and one **All** control immediately after them. Review compact rows and
the review **All** stay hidden until that control is activated. Activating it
reveals any retained download rows after the fifth, including the last retained
row, and then the review history in its existing compact, **All**, and expanded
behavior. Activating it again returns to the five-download sheet. The control
is a `type="button"` with `aria-controls` and `aria-expanded` kept in sync, and
its accessible name is **All**. It starts collapsed on each panel load and is
not stored. The title-bar history toggle still opens and closes the whole
sheet.

If no downloads exist, review history keeps its current compact and **All**
behavior and no empty download **All** is shown. If downloads exist and no
review rows remain, **All** reveals only the later downloads.

A review row whose `media_id` matches any download row's `media_id` is omitted
from both the compact and expanded review lists. The download row is the only
representation of that media. A review row with a distinct media id remains
behind **All**. A download row without a media id stays inert and does not drop
a review row. The toolbar badge and server `unopened_count` stay review-only.

Download click, read, and inert behavior, review-row hosted Details, the
opened mutation, review **All**, failure isolation, single-flight refresh, and
stale-context handling remain as ADR-0090 and ADR-0089 decided them.

This supplements ADR-0090 and supersedes only its at-most-five and
single-window presentation statements. ADR-0090's body stays unchanged.

## Consequences

The compact sheet no longer repeats a download title as a review row, and the
retained download window is reachable behind one **All** control. The read
stays the first page at fifty rows per source. No new message type, route,
permission, storage key, mutation, or badge counter is added.

## Revisit when

A later decision needs a complete request archive or a closed-panel alert.
