# Kronika X meme browser companion

This document is the operator and user setup guide for the unpacked Manifest
V3 companion under `extension/`. It does not authorize publication, NUC
deployment, signed-in X probing, or a yt-dlp pin change.

## What it does

An authenticated Kronika user can:

- hover (or keyboard-focus) a green **+** at the bottom-right of each image,
  video, or GIF in an eligible X post and click **Save to Kronika** to open
  a Save popup (Title, Description, Search tags, Save). Save from X is an
  Edit-media subset: alt-first Title, tall tweet Description, existing-tag
  search, one Save, no category radios. The header close **X**, Escape, or a click
  outside cancel without a request and restore focus to that **+**. Save
  submits the post permalink and an optional caller-private alias (title,
  description, and selected existing tag keys); it omits `content_category` and
  does not send a per-asset media URL. First catalog seeds canonical title,
  description, and selected tags from that alias; later Save updates the alias
  only. Public JPEG and PNG photographs catalog;
  WebP and other still formats fail closed without transcoding. Overlay copy
  distinguishes saving, saved, already saved, partial, failed, catalog-removed,
  and unknown transport; failed Save keeps the plus glyph and a danger border,
  not an × and not generic success. Everyone sees a single right-aligned
  **Save**.
- click into the reply composer to reveal a floating **+** on the right of
  "Post your reply" (not inserted into the X input row), then open an in-page
  Kronika search popup above that button;
- type `++` in that same reply composer (after start-of-field or whitespace,
  not inside `C++`) to open the picker and consume those two characters;
- type a search in that in-page picker (blank or cleared search lists no
  catalog hit and shows no preview chrome; on-screen arrows appear only after
  two or more hits), see the selected meme as one JPEG preview, and attach it
  with Enter or Attach onto the composer file input;
- open the toolbar side panel to use one title-bar companion history
  (`#review-history-toggle`, `#review-history`, `#review-history-list`) above
  the surviving hosted Kronika iframe. Administrators see newest analyzed
  titles first; ordinary requesters see their newest own Saves of any state.
  Every review row opens hosted
  media-details via `open_details`. Separate Recent downloads rows are inert.
  In that hosted
  Gallery, open-original stays bottom-right and Attach sits top-left on the card
  image. Ordinary browser tabs keep open-original only. Gallery 📎 attaches
  after the reply composer is focused, and the shell reports Attached only when
  that composer file input accepted the bytes.

## Review history

The side panel has one title-bar history (`#review-history-toggle`,
`#review-history`, `#review-history-list`, `#review-history-all`) above the
surviving iframe. There is no `#review-inbox` list. Routing uses
`GET /api/identity/me`: administrators (`media.workflow.read`) load
`GET /api/companion/review-inbox`; ordinary `x.request` identities load
`GET /api/companion/own-history`; otherwise history chrome hides. When connected
history exists, a compact strip of at most five titles sits directly under the
title bar. Administrator compact rows are newest analyzed items
(`completed_at_ms`); **All** reveals remaining analyzed items. Ordinary compact
rows are newest own cataloged X Saves of any state (analyzed `completed_at_ms`,
pending `created_at_ms`) so a fresh Save sits immediately under the title bar;
**All** reveals the remainder and controls review rows only. The sheet starts
closed on every panel load, does not persist that choice, and keeps the user's
current expanded or collapsed choice across refreshes while the panel stays
open. It expands directly under the title bar. Title bar, compact rows,
pending rows, and **All** use dark surface plus accent outline, not solid neon
fills. Analyzed rows use `review-history-button--analyzed`; unopened analyzed
rows also use `review-history-button--unopened`. Pending rows use
`review-history-button--pending` (muted outline) and stay plain: they never
carry `--unopened` and never increment the badge.

Administrator inbox is the global analyzed pool (website Analyze-by-AI
successes join; movies stay excluded). Ordinary own-history is requester-private
cataloged X Saves, all analysis states, movies excluded; Alice never sees Bob’s
saves. Suggestion-ready unpublished items can appear in administrator history
and the toolbar badge; they do not appear in the ordinary gallery. Clicking a
review row never removes it. Every review row posts hosted Kronika media-details via
the web-bridge type `open_details` (`storedOrigin`, never `*`). Download rows
do not. Analyzed clicks
also POST opened for that actor with the row’s `analysis_run_id` without gating
the iframe; pending clicks never POST opened. Hosted Details hide Analyze by AI
and keep the suggestions dropdown and strips for workspace actors with
`metadata.alias.write` or `metadata.canonical.write`. The newest stored
suggestion is visible without a Load step
([ADR-0080](adr/0080-immediate-editor-suggestion-reveal-and-in-modal-analysis.md)).
Standalone Analyze remains
administrator `analysis.run` and not hosted. Edit is shown for workspace
actors with `metadata.canonical.write` or `metadata.alias.write`
([ADR-0077](adr/0077-ordinary-alias-edit-affordance-and-per-field-ai-suggestions.md)).
Review Save retries opened before Apply when an earlier opened
request failed, retains selections and blocks Apply if that retry fails, and
does not issue a second opened mutation after success. Hover and keyboard focus
alone do not mark a row opened.

Recent downloads is a separate block inside the same sheet
(`#download-history`, `#download-history-list`). It shows at most five of the
signed-in requester's own X and YouTube request states, newest creation first,
from the first page of `GET /api/x/requests?limit=5` and
`GET /api/youtube/requests?limit=5`. X uses the claim title, or `X post <id>`
when that title is empty. YouTube uses `YouTube video <id>`. Status text is
Saving…, Saved, Saved privately, Partly saved, Already saved, Download failed,
or Unavailable. Saved reports the terminal Save result and does not claim that
new bytes were transferred. The rows are inert: no click, Details, retry, or
opened action. The block refreshes with the visible side panel, including
after the source tab has closed, and produces no alert while the panel is
closed. The toolbar badge stays review-only. If either enabled download read
fails, the whole Downloads block disappears and the loaded review history
remains. Rows leave the block when they fall outside the latest five
([ADR-0089](adr/0089-companion-own-download-status-history.md)).

Save from X best-effort seeds `GET /api/canonical-tags?surface=x-companion-save`
with the fixed `x` / `𝕏` pair and prepends that exact pair once when the list
contains it. Bare tag GET does not seed. Apply preserves current canonical tags
and appends newly submitted mapped AI keys, re-enumerating from 0; overflow at
32 tags is HTTP 409 `COMPANION_REVIEW_TAG_LIMIT_CONFLICT`. Detail and Apply
responses expose `canonical.tag_sources` and retain whole-field
`field_sources.tags`.

The successful connection status is blank. Configuration guidance, framing and
request failures, `Cleared`, and `Attached` still use `#shell-status`. A
complete-list failure hides history. Missing `x.request` (and missing
`media.workflow.read`) also hides and disables the toggle. The toolbar badge
remains the server `unopened_count` as `1`…`99` / `99+`, never a rendered length
or title; pending rows never increment it. Administrators refresh the badge from
inbox `limit=1`; ordinary identities refresh it from own-history `limit=1`.
Alarm `kronika.review-inbox` runs every 1 minute. There is no `notifications`
permission or second counter. The hosted `#frame` stays mounted and Attach
survives. Ingest Save remains Title→Tags→Description→Save with no radios or
Analyze. Exactly six `companion_mutation` routes remain: X submit, X retry,
review opened, review apply,
`PUT /api/admin/settings/automatic-analysis`, and `POST /api/youtube/requests`.
YouTube retry stays unflagged. A YouTube content script matches only
`https://www.youtube.com/*` and `https://m.youtube.com/*` and adds the
in-player Save control. Opened remains one of those
mutations; ordinary
callers may mark opened only on an owned cataloged X item (uniform 404
otherwise). Inbox list, detail, and apply stay administrator-only. Companion
Settings shows Administration and **Automatic media analysis** only for
`provider.operate`.

`ui/review.html` remains in the extension tree and is unused for history clicks.
It is not web-accessible and is not inside `#frame`. Ingest Save remains the
frozen capture form (no category radios).

GET inbox and GET own-history work with an empty origin allowlist. Mutations
that carry the extension Origin fail closed when the allowlist is empty. This
document does not authorize NUC deployment or enabling automatic analysis.

Save is a hover/focus overlay at the bottom-right of own media tiles, not an
action-row control. Click opens the Save popup instead of silently posting
`{ url }`. Text-only posts have no Save. Attach is shown when the
composer is focused, not on mouseover, and is not the side panel. The in-page
picker is compact quick attach with preview; it is not the full website.
Empty search shows no preview chrome; arrows appear after two or more hits;
typing `++` in the reply composer opens the picker and consumes the token.
After connect, the in-page picker has no Settings sheet; an empty origin tells
the user to connect Kronika in the side panel. Settings persists the origin
with Save under the origin field. Connected administrators also see
Administration with **Automatic media analysis**. The title bar has history,
Settings, and, once connected, Upload (📤). Upload posts `open_upload` only after
the sidebar handshake. That message opens the hosted Kronika upload dialog;
progress and terminal results stay in that dialog rather than in the side
panel. See [SPEC.md](../SPEC.md) §18. Disconnect stays in Settings: that button reads Connect
when disconnected and Disconnect when connected. Save writes settings and
connects. Disconnect clears the session. The toolbar opens
that shell, which iframes Kronika only after the stored origin is granted.
If the iframe does not load, the shell reports that Kronika did not load and
does not open a new tab. If the iframe loads without the companion host, the
library stays visible and the shell says this server cannot host companion
Attach yet. Per-asset Save targeting remains deferred.

Attach floats on the focused reply field; it is not inserted into the X input
row. Inline reply Attach is re-injected when X replaces the composer tree and the
previous button is no longer in the document. Save keeps a plus glyph when
the request fails; failure is the `title` / `aria-label` and a danger border,
not an ×.

The companion never clicks Post, never copies cookies or X credentials, and
never talks to Kronika from a content script.

## Stable unpacked ID and private-key custody

The committed `extension/manifest.json` `key` field pins a development public
key. The derived unpacked extension origin is:

```text
chrome-extension://omiihmnlkmieaafaphohakcgmbggppap
```

The matching private key is local-only at `private/companion-extension.pem.key`
(gitignored via `/private/`). Do not commit, log, or copy that file into chat,
issues, or deployment artifacts. Rotating the key changes the extension ID and
requires an allowlist update.

## Server allowlist (inert until set)

Default `KRONIKA_COMPANION_EXTENSION_ORIGINS` is empty. Six
`companion_mutation` routes may accept that exact `chrome-extension://` origin:
X submit, X retry, review opened, review apply, automatic-analysis
settings PUT, and YouTube submit. YouTube retry stays unflagged. GET inbox routes do not
require the allowlist in the same way; mutations that carry the extension Origin
fail closed when it is empty. Other browser mutations stay on the exact
Tailscale web origin. To enable the companion:

```text
KRONIKA_COMPANION_EXTENSION_ORIGINS=["chrome-extension://omiihmnlkmieaafaphohakcgmbggppap"]
```

Rollback: remove the key or set it to `[]` and restart. No CORS is enabled.

## Load in Brave or Chromium

1. Open `brave://extensions` or `chrome://extensions`.
2. Enable Developer mode.
3. Load unpacked and select the repository `extension/` directory.
4. Confirm the ID is `omiihmnlkmieaafaphohakcgmbggppap`.
5. Open the toolbar side panel. On first run or after Disconnect, open Settings
   in the green title bar, enter a Kronika HTTPS tailnet origin
   (`https://<node>.<tailnet>.ts.net`, no path). Save accepts that pasted origin
   and canonicalizes a trailing slash or host case. Then click Save under the
   origin field and grant the host permission when prompted. After Save,
   Settings closes, the success status stays blank, and the shell iframes
   that origin. Save stays disabled until the origin
   differs from the stored value. Connect and Disconnect live in Settings.
   The title bar shows Upload (📤) once connected; that control posts `open_upload`
   only after the sidebar handshake and opens the hosted upload dialog. Progress
   and results stay in that dialog. See [SPEC.md](../SPEC.md) §18.
   The in-page Search memes picker has no Settings sheet.
6. Use Disconnect in Settings to clear stored origin, in-flight
   claim ids, and the granted host permission. Settings then opens so reconnect
   stays in one place. Clearing the origin field does not Disconnect.

After using **Reload** for the unpacked extension, refresh already-open X tabs
and reopen the side panel before using Save or the picker. If an already-open X
tab or extension surface reaches the invalidated context first, Kronika shows
`Kronika was reloaded. Refresh X and reopen the side panel.`, closes partial
Save/picker hosts, and disables the affected controls. The notice is specific to
a missing runtime ID or the exact `Extension context invalidated` failure;
unrelated runtime failures keep their existing behavior.

Content scripts match `https://x.com/*` and `https://twitter.com/*` for X, and
`https://www.youtube.com/*` and `https://m.youtube.com/*` for YouTube.
`optional_host_permissions` remains `https://*.ts.net/*`. There is no
`host_permissions` key. The service worker has no X host permission.

## Residual gaps

- Live signed-in X DOM evidence is not part of repository acceptance.
- Independent INFOSEC R3 of the private status-bridge seam and CDN transport
  remains a later grant.
- Side-panel iframe framing against live Tailscale Serve or Brave Shields is
  a named residual until a later operator probe.
- A Kronika origin that does not ship `companion_host.js` can still appear
  in the side panel; Gallery Attach requires that host script on the served
  web.
- Larger-than-32-MiB attach uses optional `chrome.downloads` with Save As;
  it still does not auto-post.
