# ADR-0092: Companion Download Unread State

## Status

`Accepted`

## Decision Date

2026-11-13

## Context

[ADR-0091](0091-companion-history-compact-five-and-all-expansion.md) puts one
download **All** after the five compact downloads and, once that control is
activated, shows review history with its own **All**. Rendered use showed two
**All** controls when downloads exist. The second control does not belong.
The same dedupe that omits a matching review row also removes the only unread
marker for a downloaded post that is still unread.

## Decision

When downloads exist, the download **All** is the only **All**. The review
**All** stays in the page and stays hidden. Activating the download **All**
reveals any retained download rows after the fifth, including the sixth and
the last retained row, and the complete review history: the compact review
list and the expanded review list. Activating it again returns to the
five-download sheet. The control remains a `type="button"` with
`aria-controls` and `aria-expanded` kept in sync, and its accessible name
remains **All**. It starts collapsed on each panel load and is not stored.
The title-bar history toggle still opens and closes the whole sheet.

If no downloads exist, review history keeps its compact and **All** behavior
and no empty download **All** is shown. If downloads exist and no review rows
remain, **All** reveals only the later downloads.

A review row whose `media_id` matches a download row stays omitted from both
review lists. When that review item is analyzed, unread (`unopened`), and has
an `analysis_run_id`, the download row shows the existing unread dot and
accent and an accessible Unread indication. The same click still opens hosted
Details through `open_details`. It also posts the existing
`REVIEW_INBOX_OPENED` request once for that analysis run, then refreshes, so
the marker and the review-only badge can clear. The row stays. No new route,
message, mutation, or badge counter is added.

A download row that does not match an analyzed unread item posts only
`open_details` when it has a media id. A row without a media id stays inert.
The toolbar badge and server `unopened_count` stay review-only.

This supplements ADR-0091 and supersedes only its review-**All** presentation
when downloads exist and the statement that a matched analyzed unread download
row does not post opened. The ADR-0090 and ADR-0091 bodies stay unchanged.

## Consequences

The compact sheet has one **All** when downloads exist, and a downloaded post
that is still unread keeps a visible unread state after its review row is
deduplicated. Opened remains the existing mutation. No message type, route,
permission, storage key, or badge counter is added.

## Revisit when

A later decision needs a download-specific badge or a second history control.
