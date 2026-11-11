# ADR-0089: Companion Own Download Status History

## Status

`Accepted`

## Decision Date

2026-11-11

## Context

[ADR-0076](0076-companion-history-hosted-click-admin-analyzed-inbox-and-ordinary-own-history.md)
routes companion history to the administrator analyzed inbox or to ordinary
cataloged X Saves, and makes review rows open hosted Details. That history does
not show failed, uncataloged, or YouTube requests, and it does not tell an
administrator about that administrator's own downloads without turning foreign
analyzed titles into download activity.

The side panel already polls visible history and the server already lists owned
X and YouTube requests.

## Decision

The companion history sheet contains a separate read-only **Recent downloads**
block. It shows at most five of the signed-in requester's own X and YouTube
request states, newest creation first. An administrator's global analyzed inbox
stays separately authorized. That same administrator's own request reads use
the owned X and YouTube list APIs and are not derived from the inbox.

Rows are static status text. There is no unseen marker, counter,
acknowledgement, expiry, or new stored snapshot. Download rows are inert and
do not post hosted `open_details`.

`review_inbox` keeps its message type. A successful response adds `downloads`
with `ok` and at most five sanitized items. Download failure is
`{ok: false, items: []}`; a successful empty window is `{ok: true, items: []}`.
Handling is limited to the extension's own sidebar sender. A mismatched sender
fails before any network read and does not clear the toolbar badge.

Review pagination stays fail-closed. If either capability-enabled download read
fails, the Downloads block is hidden and the loaded review list and its server
badge count remain. Download outcomes do not change `unopened_count`.

This supplements ADR-0076 only for that block. ADR-0076's review routing,
hosted Details clicks, and review-only badge remain. Its historical body is
unchanged.

## Consequences

The visible panel reads at most the first page of each available own-request
list. Closing the source tab does not block a later sidebar refresh. A closed
panel produces no download alert. Reconnect, reset, and runtime invalidation
clear the displayed download rows. Overlapping refreshes and responses from an
older connection or identity context are discarded.

## Revisit when

A later decision needs a complete request archive, a closed-panel alert, or a
download row that opens Details.
