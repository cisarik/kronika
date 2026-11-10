# ADR-0088: Timeline as the Single Overview

## Status

`Accepted`

## Decision Date

2026-11-10

## Context

[ADR-0083](0083-modular-research-providers-and-administrator-curated-timeline.md)
and [ADR-0087](0087-complete-visibility-aware-timeline-chronicle.md) kept a
separate personal History view beside the Timeline. The Cooperator rendered the
shell at `bbf776c462e1eb5eccb91eb027a85eee9055d2ec` and decided that History
repeats the Timeline: saved and unfinished Search and Research items already
appear there.

## Decision

The Timeline is the single authorized overview of catalog media and Search and
Research content the signed-in identity may already read. The separate History
navigation link and page are removed.

`#/history` and `#/history/records` resolve to the Timeline. Search, Research
and capability-gated Review remain action links. Gallery, Details and playback
remain a separate working view.

Question and answer records, their APIs and authorization stay. This decision
does not delete data, change payloads, or add a second list.

Timeline search occupies the same header slot as Gallery search. Only the
active view's field is visible, and each view keeps its own query behavior.
The Timeline has no visible hero, lede or household-sharing note.

This supersedes only the separate-History-view statements in ADR-0083 and
ADR-0087. Their other decisions and historical bodies remain unchanged.

## Consequences

The shell no longer offers a History destination. Owners and administrators
read saved and unfinished questions from the Timeline under the existing
authorization rules. Household members still see only approved projections or
existing unbound legacy-publication access. Internet publication stays off.

## Revisit when

A later decision needs a caller-only question list that the Timeline cannot
express without changing authorization or payloads.
