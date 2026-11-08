# Kronika UX Audit

## Status

Classification: product UX audit and change record.

Consumers: Cooperator, Orchestrator, Workers and contributors touching the web
shell or the browser companion.

Retention: remains while the findings below guide UI work. Git history is the
archive for superseded rounds.

Relationship: historical product record of one UX round. Not task authority.

Authority: Cooperator observations from that round, already implemented in
01dda1f..cb5fa4d. The documentation grant may add this metadata and must not
treat the file as a new product grant.

Discoverability: one inbound link from DEVELOPMENT.md.

Cleanup owner: ORCHESTRATOR, by leaving the file as frozen history rather than
deleting it.

Cleanup trigger: closure of ux-refresh-acceptance-and-promotion, or a later UX
whole that supersedes the findings.

## Scope

The audit covered the served web shell (`src/kronika/adapters/api/web`), the
browser companion (`extension/`) and the server paths those surfaces depend
on. It was done against the live NUC release with Playwright at desktop
(1440 px), phone (390 px) and side-panel (400 px) widths. Colors and the
Gallery card design were kept as they were.

## Findings and resolutions

| # | Finding | Resolution |
|---|---------|------------|
| 1 | The header carried twelve text buttons with internal names ("Analysis proposals", "X review", "My X downloads"), overflowed and overlapped the Gallery search. | Two icon menus (＋ Add media, ⚙️ Administration) with descriptive items, a 🗂️ contributions button, status icons and an avatar. Menus hide when the identity has no permitted item. |
| 2 | Two rows of identical-looking buttons served as navigation and filters; no hierarchy. | Icon tab bar for primary navigation, a bottom tab bar on narrow screens and in the side panel, icon chips for filters. |
| 3 | Page headings had a permanent focus outline, no explanation of the page. | Page heroes with an icon and one-line purpose; programmatic heading focus no longer draws an outline. |
| 4 | Search/Research showed raw budget "micros" and legal text above the form. | Composer with a primary Ask action; privacy and cost text collapsed; budgets shown in dollars. |
| 5 | Text-only action buttons in dialogs and cockpits. | Leading icons through `data-icon`, keeping visible and accessible labels unchanged. |
| 6 | Gallery card overlay icons were nearly invisible on light media. | 40 px glass buttons with a 2 px accent border and shadow; hover and focus are fully opaque. |
| 7 | Many cards showed "Preview unavailable." because previews were only produced by the operator CLI. | `GalleryPreviewBackfillCoordinator` generates missing previews in the server process (bounded per pass, failures backed off). Gallery GET requests still never generate. Disable with `KRONIKA_GALLERY_PREVIEW_BACKFILL_ENABLED=false`. |
| 8 | Companion toolbar icons were 1×1 transparent placeholders; first run was an empty panel. | Real icons, a welcome screen with inline connect. |
| 9 | Companion "Disconnect" was a large title-bar button. | Disconnect moved into Settings; the title bar has a 📤 upload button that opens the hosted upload dialog (`open_upload` web message). |
| 10 | The X save button covered the X player's fullscreen control. | Smaller round button; on video hosts it sits 76 px above the bottom edge, clear of the control bar and scrubber. |
| 11 | YouTube could only be requested by pasting a URL into the web app. | A YouTube content script adds a Save button in the player's top-left corner, following the player's auto-hide, and tracks the request until it completes. The `/api/youtube/requests` POST route accepts the pinned companion origin, like the X route. |

## Recommendations not yet implemented

- Show media thumbnails on Timeline and History cards for media records.
- Offer automatic upload start after a file is chosen, keeping duplicate review.
- Surface completed companion downloads (X and YouTube) as a notification in
  the side panel history.
- Add a Settings view in the web shell that explains roles and capabilities to
  ordinary household members.
