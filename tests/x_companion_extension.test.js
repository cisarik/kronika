const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const REPO = path.resolve(__dirname, "..");
const companion = require(path.join(REPO, "extension/shared/messages.js"));
const contract = require(path.join(REPO, "extension/content/x_adapter_contract_v1.js"));
const adapterSource = fs.readFileSync(
  path.join(REPO, "extension/content/x_adapter.js"),
  "utf8"
);
const workerSource = fs.readFileSync(
  path.join(REPO, "extension/background/service_worker.js"),
  "utf8"
);
const manifest = JSON.parse(
  fs.readFileSync(path.join(REPO, "extension/manifest.json"), "utf8")
);
// Every user-visible companion string is pinned against the brand word carried
// by the extension display name, never against a spelled-out brand. A one-sided
// rename of the product or of a single surface therefore breaks the pin that
// owns it instead of passing silently.
const BRAND = manifest.name.split(/\s+/)[0];
const SAVE_TO_BRAND = `Save to ${BRAND}`;

test("unknown protocol versions and types are dropped", () => {
  assert.equal(companion.TYPES.DISMISS_PICKER, "dismiss_picker");
  assert.equal(companion.TYPES.PICKER_LAYOUT, "picker_layout");
  assert.equal(companion.TYPES.REVIEW_INBOX, "review_inbox");
  assert.equal(companion.dropUnknown({ v: "other", type: companion.TYPES.SAVE_POST }), null);
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "proxy_fetch" }), null);
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "dismiss_popup" }), null);
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "picker_resize" }), null);
  assert.equal(companion.dropUnknown({ url: "https://evil.example" }), null);
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.SAVE_POST,
      payload: { url: "https://x.com/a/status/1" },
    })
  );
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.DISMISS_PICKER,
    })
  );
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.PICKER_LAYOUT,
      payload: { compact: true },
    })
  );
});

test("service worker path templates reject caller-supplied URLs and ids", () => {
  assert.equal(companion.pathFor("content", { mediaId: "https://evil.example", locationId: "x" }), null);
  assert.equal(companion.pathFor("xRequest", { claimId: "../etc/passwd" }), null);
  assert.equal(
    companion.pathFor("content", {
      mediaId: "11111111-1111-4111-8111-111111111111",
      locationId: "22222222-2222-4222-8222-222222222222",
    }),
    "/api/media/11111111-1111-4111-8111-111111111111/locations/22222222-2222-4222-8222-222222222222/content"
  );
  assert.equal(companion.pathFor("identity"), "/api/identity/me");
  assert.equal(
    companion.pathFor("canonicalTags"),
    "/api/canonical-tags?surface=x-companion-save"
  );
  assert.equal(companion.pathFor("reviewInbox"), "/api/companion/review-inbox");
  assert.equal(
    companion.pathFor("reviewInbox", { url: "https://evil.example", claimId: "../etc/passwd" }),
    "/api/companion/review-inbox"
  );
  assert.match(workerSource, /pathFor\(/);
  assert.doesNotMatch(workerSource, /fetch\(payload\.url\)/);
  assert.doesNotMatch(workerSource, /fetch\(message\.url\)/);
});

test("X URL allowlist accepts only public https post permalinks", () => {
  assert.ok(companion.acceptXPostUrl("https://x.com/fixture/status/123456789"));
  assert.equal(companion.acceptXPostUrl("https://evil.example/fixture/status/123456789"), null);
  assert.equal(companion.acceptXPostUrl("https://x.com/i/web/status/123456789"), null);
  assert.equal(companion.acceptXPostUrl("https://x.com/fixture/status/123456789?foo=1"), null);
});

test("FrameNest origin canonicalizer accepts ordinary tailnet paste variants", () => {
  const canonical = "https://nuc.example.ts.net";
  const accepted = [
    "https://nuc.example.ts.net",
    "https://nuc.example.ts.net/",
    "https://Nuc.Example.ts.net",
    "HTTPS://nuc.example.ts.net",
    "https://nuc.example.ts.net:443",
    "http://nuc.example.ts.net",
    "nuc.example.ts.net",
    "  https://nuc.example.ts.net/  ",
  ];
  accepted.forEach((value) => {
    assert.equal(companion.canonicalizeFrameNestOrigin(value), canonical, value);
    assert.equal(companion.acceptFrameNestOrigin(value), true, value);
  });
  const rejected = [
    "https://example.ts.net",
    "https://nuc.example.ts.net/path",
    "https://nuc.example.ts.net?x=1",
    "http://127.0.0.1:8000",
    "https://127.0.0.1",
    "https://example.com",
    "",
  ];
  rejected.forEach((value) => {
    assert.equal(companion.canonicalizeFrameNestOrigin(value), null, JSON.stringify(value));
    assert.equal(companion.acceptFrameNestOrigin(value), false, JSON.stringify(value));
  });
  const sidebarJs = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
  assert.match(
    sidebarJs,
    new RegExp(
      `Use the ${BRAND} HTTPS tailnet origin \\(https://<node>\\.<tailnet>\\.ts\\.net\\), with no path\\.`
    )
  );
  assert.match(workerSource, /canonicalizeFrameNestOrigin/);
  assert.doesNotMatch(workerSource, /origins: \[origin \+ "\/\/\*"\]/);
});

test("companion alias title canonicalizer maps controls and omits empty titles", () => {
  assert.equal(companion.canonicalizeCompanionAliasTitle("A two\nline alt"), "A two line alt");
  assert.equal(companion.canonicalizeCompanionAliasTitle("  A two\nline alt  "), "A two line alt");
  assert.equal(companion.canonicalizeCompanionAliasTitle("A two\r\nline alt"), "A two line alt");
  assert.equal(companion.canonicalizeCompanionAliasTitle(""), null);
  assert.equal(companion.canonicalizeCompanionAliasTitle("   "), null);
  assert.equal(companion.canonicalizeCompanionAliasTitle("\n\n"), null);
  assert.equal(companion.canonicalizeCompanionAliasTitle(null), null);
  assert.equal(companion.canonicalizeCompanionAliasTitle(1), null);
  const longTitle = "A".repeat(300);
  const clipped = companion.canonicalizeCompanionAliasTitle(longTitle);
  assert.equal(typeof clipped, "string");
  assert.equal(Array.from(clipped).length, 240);
  assert.equal(clipped, "A".repeat(240));
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  assert.match(saveJs, /canonicalizeCompanionAliasTitle/);
  assert.match(workerSource, /canonicalizeCompanionAliasTitle/);
  assert.match(adapterSource, /canonicalizeCompanionAliasTitle/);
  assert.match(
    extractNamedFunction(adapterSource, "firstNonGenericName"),
    /canonicalizeCompanionAliasTitle/
  );
  assert.match(extractNamedFunction(saveJs, "aliasPayload"), /canonicalizeCompanionAliasTitle/);
  assert.match(extractNamedFunction(workerSource, "sanitizeAlias"), /canonicalizeCompanionAliasTitle/);
});

test("adapter contract has no Post-button or auto-submit path", () => {
  const serialized = JSON.stringify(contract);
  assert.equal(serialized.includes("tweetButton"), false);
  assert.equal("postButton" in contract, false);
  assert.doesNotMatch(adapterSource, /tweetButton/);
  assert.doesNotMatch(adapterSource, /form\.submit/);
  assert.doesNotMatch(adapterSource, /dispatchEvent\(new Event\(["']submit/);
  assert.match(adapterSource, /DataTransfer/);
  assert.match(adapterSource, /pollClaim/);
  assert.match(adapterSource, /RECOVER_INFLIGHT/);
});

test("manifest permissions stay minimized and omit X host access", () => {
  assert.deepEqual(manifest.permissions.sort(), ["alarms", "sidePanel", "storage"]);
  assert.deepEqual(manifest.optional_permissions, ["downloads"]);
  assert.deepEqual(manifest.optional_host_permissions, ["https://*.ts.net/*"]);
  assert.equal("host_permissions" in manifest, false);
  assert.equal("externally_connectable" in manifest, false);
  assert.equal((manifest.permissions || []).includes("tabs"), false);
  assert.equal((manifest.permissions || []).includes("alarms"), true);
  assert.equal((manifest.permissions || []).includes("notifications"), false);
  assert.equal((manifest.permissions || []).includes("cookies"), false);
  const matches = manifest.content_scripts[0].matches.sort();
  assert.deepEqual(matches, ["https://twitter.com/*", "https://x.com/*"]);
});

test("chunk reassembly stays bounded", () => {
  const first = companion.bytesFromBase64(Buffer.from("abc").toString("base64"));
  const second = companion.bytesFromBase64(Buffer.from("def").toString("base64"));
  const joined = companion.concatChunks([first, second], 6);
  assert.equal(Buffer.from(joined).toString(), "abcdef");
  assert.equal(companion.MAX_ATTACH_BYTES, 32 * 1024 * 1024);
});

test("service worker recovers inflight claim ids from storage", () => {
  assert.match(workerSource, /inflightClaims/);
  assert.match(workerSource, /RECOVER_INFLIGHT/);
  assert.match(workerSource, /postId/);
  assert.match(extractNamedFunction(workerSource, "normalizeInflightRecords"), /claimId/);
  assert.doesNotMatch(workerSource, /setInterval/);
});

test("adapter contract exposes frozen action-bar, Share, and media-host selectors", () => {
  assert.equal(contract.adapterVersion, 1);
  assert.ok(Object.isFrozen(contract.actionGroupSelectors));
  assert.ok(Object.isFrozen(contract.actionBarSignals));
  assert.ok(Object.isFrozen(contract.shareSelectors));
  assert.ok(Object.isFrozen(contract.mediaHostSelectors));
  assert.ok(Object.isFrozen(contract.tweetTextSelectors));
  assert.ok(Object.isFrozen(contract.composerChromeSelectors));
  assert.ok(Object.isFrozen(contract.composerMediaButtonSelectors));
  assert.deepEqual(contract.actionGroupSelectors, ["[role='group']"]);
  assert.deepEqual(contract.actionBarSignals, [
    "[data-testid='reply']",
    "[data-testid='retweet']",
    "[data-testid='like']",
  ]);
  assert.deepEqual(contract.shareSelectors, [
    "[data-testid='share']",
    "[aria-label='Share post']",
    "[aria-label='Share']",
  ]);
  assert.deepEqual(contract.mediaHostSelectors, [
    "[data-testid='tweetPhoto']",
    "[data-testid='videoPlayer']",
    "[data-testid='videoComponent']",
    "[data-framenest-media]",
  ]);
  assert.deepEqual(contract.tweetTextSelectors, [
    "[data-testid='tweetText']",
    "[data-framenest-tweet-text]",
  ]);
  assert.deepEqual(contract.composerChromeSelectors, ["[data-framenest-composer-chrome]"]);
  assert.ok(Object.isFrozen(contract.composerTextRowSelectors));
  assert.deepEqual(contract.composerTextRowSelectors, ["[data-framenest-composer-text-row]"]);
  assert.ok(contract.composerRoots.includes("[aria-label='Post your reply']"));
  assert.deepEqual(contract.composerMediaButtonSelectors, [
    "[data-testid='fileInput']",
    "[aria-label='Add photos or video']",
    "[aria-label='Media']",
  ]);
});

test("in-feed Save is a per-media hover overlay, not an action-row control", () => {
  const fixture = fs.readFileSync(
    path.join(REPO, "tests/support/x_fixtures/composer.html"),
    "utf8"
  );
  assert.match(fixture, /role="group"/);
  assert.match(fixture, /data-testid="reply"/);
  assert.match(fixture, /data-testid="retweet"/);
  assert.match(fixture, /data-testid="like"/);
  assert.match(fixture, /aria-label="Share post"/);
  assert.match(fixture, /data-testid="tweetPhoto"/);
  assert.match(fixture, /data-framenest-media/);
  assert.doesNotMatch(fixture, /data-framenest-companion/);

  assert.doesNotMatch(adapterSource, /writing-mode/);
  assert.doesNotMatch(adapterSource, /addButton\(\s*postRoot/);
  assert.doesNotMatch(adapterSource, /postRoot\.appendChild/);
  assert.doesNotMatch(adapterSource, /textContent\s*=\s*result/);
  assert.doesNotMatch(adapterSource, /textContent\s*=\s*["']Save to FrameNest["']/);
  assert.doesNotMatch(adapterSource, /textContent\s*=\s*["']Saving/);
  assert.doesNotMatch(adapterSource, /ownActionGroup/);
  assert.doesNotMatch(adapterSource, /shareActionColumn/);
  assert.doesNotMatch(adapterSource, /SAVE_DOWN_NUDGE/);
  assert.doesNotMatch(adapterSource, /insertAdjacentElement\("afterend"/);
  assert.doesNotMatch(adapterSource, /return "missing_bar"/);
  assert.doesNotMatch(adapterSource, /return "missing_share"/);
  assert.doesNotMatch(adapterSource, /adapter_drift/);
  assert.doesNotMatch(adapterSource, /style\.background = "transparent"/);
  assert.doesNotMatch(
    adapterSource,
    /\[data-framenest-companion='save'\][\s\S]{0,500}background:\s*transparent/
  );
  assert.match(adapterSource, /setAttribute\("data-framenest-companion", "save"\)/);
  assert.match(adapterSource, /setAttribute\("aria-label", name\)/);
  assert.match(adapterSource, /stopImmediatePropagation/);
  assert.match(adapterSource, /ownMediaHosts/);
  assert.match(adapterSource, /mediaHostSelectors/);
  assert.match(adapterSource, /data-framenest-media-host/);
  assert.match(adapterSource, /data-framenest-companion-style/);
  assert.match(adapterSource, /position: absolute/);
  const saveRule = companionStyleArrayRule(
    adapterSource,
    "[data-framenest-companion='save'] {"
  );
  assert.match(saveRule, /bottom:\s*8px/);
  assert.match(saveRule, /right:\s*8px/);
  assert.doesNotMatch(saveRule, /bottom:\s*0/);
  assert.doesNotMatch(saveRule, /right:\s*0;/);
  assert.doesNotMatch(saveRule, /top:\s*0/);
  assert.doesNotMatch(saveRule, /left:\s*0/);
  assert.doesNotMatch(saveRule, /overflow:\s*hidden/);
  assert.doesNotMatch(adapterSource, /\[data-framenest-companion='save'\][\s\S]{0,80}top:\s*0/);
  assert.doesNotMatch(adapterSource, /\[data-framenest-companion='save'\][\s\S]{0,80}left:\s*0/);
  assert.match(adapterSource, /\[data-framenest-media-host\] \[aria-label='Edit image'\]/);
  assert.match(adapterSource, /\[data-framenest-media-host\] \[title='Edit image'\]/);
  const editRule = companionStyleArrayRule(
    adapterSource,
    "[data-framenest-media-host] [aria-label='Edit image']"
  );
  assert.match(editRule, /display:\s*none\s*!important/);
  assert.doesNotMatch(editRule, /left:\s*8px/);
  assert.doesNotMatch(editRule, /bottom:\s*8px/);
  assert.match(adapterSource, /EDIT_IMAGE_NAME = "Edit image"/);
  assert.match(extractNamedFunction(adapterSource, "hideOverlappingEditImage"), /EDIT_IMAGE_NAME/);
  assert.match(extractNamedFunction(adapterSource, "hideEditImageControl"), /display/);
  assert.match(extractNamedFunction(adapterSource, "inHostAccessibleName"), /aria-label/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /requestSavePopupHandshake/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /onSaveIframeReady/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /postTextPrefillFrom/);
  assert.match(extractNamedFunction(adapterSource, "requestSavePopupHandshake"), /prefill/);
  assert.doesNotMatch(extractNamedFunction(adapterSource, "requestSavePopupHandshake"), /focus-category/);
  assert.match(extractNamedFunction(adapterSource, "requestSavePopupHandshake"), /framenest-save-host/);
  assert.match(extractNamedFunction(adapterSource, "requestSavePopupHandshake"), /title:/);
  assert.doesNotMatch(
    extractNamedFunction(adapterSource, "requestSavePopupHandshake"),
    /descriptionHeight/
  );
  assert.doesNotMatch(extractNamedFunction(adapterSource, "openSavePopup"), /iframe\.focus/);
  assert.doesNotMatch(extractNamedFunction(adapterSource, "openSavePopup"), /iframe\.tabIndex/);
  assert.doesNotMatch(extractNamedFunction(adapterSource, "openSavePopup"), /tabIndex\s*=/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /#url=/);
  assert.doesNotMatch(extractNamedFunction(adapterSource, "openSavePopup"), /media=/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /event\.key === "Enter"/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /isComposing/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /requestSavePopupSubmit/);
  assert.match(extractNamedFunction(adapterSource, "requestSavePopupSubmit"), /action:\s*"submit"/);
  assert.match(extractNamedFunction(adapterSource, "requestSavePopupSubmit"), /framenest-save-host/);
  assert.doesNotMatch(
    extractNamedFunction(adapterSource, "requestSavePopupSubmit"),
    /["']\*["']/
  );
  assert.doesNotMatch(adapterSource, /data-framenest-media-kind/);
  assert.doesNotMatch(
    extractNamedFunction(adapterSource, "requestSavePopupHandshake"),
    /["']\*["']/
  );
  assert.match(adapterSource, /opacity: 0/);
  assert.match(adapterSource, /pointer-events: none/);
  assert.match(adapterSource, /background: #000000/);
  assert.match(adapterSource, /style\.position = "relative"/);
  assert.match(adapterSource, /return "no_media"/);
  assert.match(adapterSource, /accepted\.submittedUrl/);
  assert.doesNotMatch(adapterSource, /pbs\.twimg\.com/);
  assert.match(adapterSource, /data-framenest-companion"\) === "save"/);
  assert.equal(companion.reduceXSaveOutcome({ ok: false }).name, `${SAVE_TO_BRAND} failed`);
  assert.match(adapterSource, /openSavePopup/);
  assert.match(adapterSource, /ui\/save\.html/);
  assert.doesNotMatch(adapterSource, /TYPES\.SAVE_POST, \{\s*url: accepted\.submittedUrl/);
  assert.doesNotMatch(adapterSource, /void savePost\(button, accepted\)/);
  assert.doesNotMatch(adapterSource, /M8 8l8 8/);
  assert.doesNotMatch(adapterSource, /M16 8l-8 8/);
  assert.match(adapterSource, /kind === "failed"[\s\S]{0,160}M12 6\.5v11M6\.5 12h11/);
  assert.match(adapterSource, /reduceXSaveOutcome/);
  assert.doesNotMatch(
    extractNamedFunction(adapterSource, "pollClaim"),
    /Saved to FrameNest/
  );
});

test("Save popup searches tags, pins Save, and does not execute Analyze", () => {
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  const saveCss = fs.readFileSync(path.join(REPO, "extension/ui/save.css"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  const saveFn = adapterSource.match(/function positionSavePopup\(\) \{[\s\S]*?\n  \}/);
  const attachFn = adapterSource.match(/function positionAttachPopup\(\) \{[\s\S]*?\n  \}/);
  assert.ok(saveFn);
  assert.ok(attachFn);
  assert.match(saveHtml, /placeholder="Search tags"/);
  assert.doesNotMatch(saveHtml, /Search or add a tag/);
  assert.match(saveHtml, /aria-label="Close"/);
  assert.match(saveHtml, />Save</);
  assert.doesNotMatch(saveHtml, /id="analyze"/);
  assert.doesNotMatch(saveHtml, /Save and analyze by AI/);
  assert.doesNotMatch(saveHtml, />Analyze by AI</);
  assert.match(saveHtml, /id="description"/);
  assert.match(saveHtml, /<textarea[^>]*id="description"/);
  assert.match(saveHtml, /maxlength="10000"/);
  assert.match(saveHtml, /id="title"[\s\S]*id="tag-search"[\s\S]*id="description"/);
  assert.match(saveHtml, /id="save"/);
  assert.doesNotMatch(saveHtml, /id="cancel"/);
  assert.doesNotMatch(saveHtml, />Cancel</);
  assert.doesNotMatch(saveHtml, /type="checkbox"/);
  assert.doesNotMatch(saveJs, /innerHTML/);
  assert.doesNotMatch(saveJs, /type\s*=\s*["']checkbox["']/);
  assert.match(saveJs, /payload\.description = descriptionValue/);
  assert.match(saveJs, /form\.addEventListener\("submit"[\s\S]*submitSave\(\)/);
  assert.doesNotMatch(saveJs, /id="analyze"/);
  assert.doesNotMatch(saveJs, /gateAnalyze/);
  assert.doesNotMatch(saveJs, /TYPES\.IDENTITY/);
  assert.match(saveJs, /TYPES\.SAVE_POST/);
  assert.doesNotMatch(saveJs, /analysis\.run/);
  assert.match(saveJs, /SUGGESTION_LIMIT = 8/);
  assert.doesNotMatch(saveJs, /\/api\/.*analys/i);
  assert.doesNotMatch(saveJs, /companion_mutation/);
  assert.doesNotMatch(saveJs, /TYPES\.[A-Z_]*ANALY/);
  assert.match(saveCss, /#ff4d4d/);
  assert.doesNotMatch(saveCss, /#f5f8f5/);
  assert.doesNotMatch(saveCss, /\.analyze/);
  assert.match(saveCss, /\.fields \{[\s\S]*?overflow:\s*visible/);
  assert.match(saveCss, /\.actions \{[\s\S]*?flex:\s*0 0 auto/);
  assert.match(saveCss, /\.actions \{[\s\S]*?justify-content:\s*flex-end/);
  assert.doesNotMatch(saveCss, /\.actions \{[\s\S]*?justify-content:\s*flex-start/);
  assert.match(saveFn[0], /Math\.min\(\s*360/);
  assert.match(saveFn[0], /innerHeight - 16/);
  assert.match(saveFn[0], /contentHeight/);
  assert.match(adapterSource, /SAVE_FRAME_BORDER_Y = 2/);
  assert.match(saveFn[0], /measured > 0 \? measured \+ SAVE_FRAME_BORDER_Y : 240/);
  assert.doesNotMatch(saveFn[0], /measured > 0 \? measured : 240/);
  assert.doesNotMatch(saveFn[0], /400 \+ textareaHeight/);
  assert.doesNotMatch(saveFn[0], /Math\.min\(\s*720/);
  assert.doesNotMatch(saveFn[0], /Math\.min\(\s*380/);
  assert.doesNotMatch(saveFn[0], /Math\.min\(\s*520/);
  assert.match(attachFn[0], /attachPopup\.compact/);
  assert.match(attachFn[0], /Math\.min\(\s*128/);
  assert.match(attachFn[0], /Math\.max\(\s*96/);
  assert.match(attachFn[0], /Math\.min\(\s*360/);
  assert.match(attachFn[0], /Math\.max\(\s*280/);
  assert.doesNotMatch(attachFn[0], /Math\.min\(\s*500/);
  assert.doesNotMatch(attachFn[0], /Math\.min\(\s*520/);
  assert.doesNotMatch(attachFn[0], /Math\.min\(\s*420/);
});

test("companion surfaces copy FrameNest gallery visual tokens", () => {
  const pickerCss = fs.readFileSync(path.join(REPO, "extension/ui/picker.css"), "utf8");
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const pickerJs = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  const tokenNames = [
    "--background",
    "--surface",
    "--surface-input",
    "--text",
    "--text-muted",
    "--text-soft",
    "--accent",
    "--accent-strong",
    "--accent-soft",
    "--accent-border",
    "--accent-glow",
    "--danger",
    "--focus",
    "--radius-sm",
    "--font-mono",
  ];
  const reviewCss = fs.readFileSync(path.join(REPO, "extension/ui/review.css"), "utf8");
  tokenNames.forEach((token) => {
    assert.match(pickerCss, new RegExp(token.replace(/-/g, "\\-")));
    assert.match(reviewCss, new RegExp(token.replace(/-/g, "\\-")));
  });
  assert.match(pickerCss, /#00ff41/);
  assert.match(reviewCss, /#00ff41/);
  assert.match(pickerCss, /#39ff14/);
  assert.match(reviewCss, /#39ff14/);
  assert.match(adapterSource, /#00ff41/);
  assert.match(adapterSource, /#39ff14/);
  assert.doesNotMatch(adapterSource, /copyActionColor/);
  assert.doesNotMatch(adapterSource, /getComputedStyle\([^)]+\)\.color/);
  assert.doesNotMatch(adapterSource, /rgba\(127, 127, 127, 0\.12\)/);
  assert.match(adapterSource, /GALLERY_ACCENT/);
  assert.match(pickerHtml, /header-search__control/);
  assert.match(pickerHtml, /header-search__prompt/);
  assert.match(pickerHtml, /id="preview"/);
  assert.match(pickerHtml, /id="preview-media"/);
  assert.match(pickerHtml, /id="preview-prev"/);
  assert.match(pickerHtml, /id="preview-next"/);
  assert.doesNotMatch(pickerJs, /innerHTML/);
  assert.doesNotMatch(pickerJs, /form\.submit/);
});

test("Attach floats on the focused reply field and opens an in-page popup", () => {
  const fixture = fs.readFileSync(
    path.join(REPO, "tests/support/x_fixtures/composer.html"),
    "utf8"
  );
  assert.ok(Object.isFrozen(contract.composerToolbarSelectors));
  assert.deepEqual(contract.composerToolbarSelectors, [
    "[data-testid='toolBar']",
    "[data-framenest-composer-toolbar]",
    "[role='toolbar']",
  ]);
  assert.ok(Object.isFrozen(contract.contentDisclosureSelectors));
  assert.deepEqual(contract.contentDisclosureSelectors, [
    "[aria-label='Content disclosure']",
    "[data-framenest-content-disclosure]",
  ]);
  assert.ok(Object.isFrozen(contract.bookmarkSelectors));
  assert.match(fixture, /data-testid="toolBar"/);
  assert.match(fixture, /data-framenest-composer-toolbar/);
  assert.match(fixture, /data-framenest-composer-chrome/);
  assert.match(fixture, /data-framenest-composer-text-row/);
  assert.match(fixture, /data-framenest-composer-row-canary/);
  assert.match(fixture, /id="inline-reply-only-editable"/);
  assert.match(fixture, /id="inline-reply-deep"/);
  assert.match(fixture, /Post your reply/);
  const inlineOnly = fixture.slice(
    fixture.indexOf('id="inline-reply-only-editable"'),
    fixture.indexOf('id="inline-reply-deep"')
  );
  assert.doesNotMatch(inlineOnly, /data-framenest-composer-text-row/);
  assert.doesNotMatch(inlineOnly, /data-framenest-composer-chrome/);
  assert.match(inlineOnly, /contenteditable="true"/);
  assert.match(inlineOnly, /data-testid="toolBar"/);
  assert.match(fixture, /data-testid="bookmark"/);
  assert.match(fixture, /aria-label="Content disclosure"/);
  assert.match(fixture, /data-framenest-content-disclosure/);
  assert.doesNotMatch(adapterSource, /addButton\(/);
  assert.doesNotMatch(adapterSource, /composerRoot\.appendChild/);
  assert.doesNotMatch(adapterSource, /composerChrome\.appendChild/);
  assert.doesNotMatch(adapterSource, /textRow\.appendChild/);
  assert.doesNotMatch(adapterSource, /ensureContainingBlock\(textRow\)/);
  assert.doesNotMatch(adapterSource, /insertAttachAfterDisclosure/);
  assert.doesNotMatch(adapterSource, /findContentDisclosure/);
  assert.doesNotMatch(adapterSource, /disclosureActionColumn/);
  assert.doesNotMatch(adapterSource, /tweetButton/);
  assert.doesNotMatch(adapterSource, /form\.submit/);
  assert.doesNotMatch(adapterSource, /dispatchEvent\(new Event\(["']submit/);
  assert.doesNotMatch(adapterSource, /openPicker/);
  assert.doesNotMatch(adapterSource, /sidePanel/);
  assert.doesNotMatch(adapterSource, /\bfetch\s*\(/);
  assert.match(adapterSource, /findComposerChrome/);
  assert.match(adapterSource, /findComposerTextRow/);
  assert.match(adapterSource, /composerChromeSelectors/);
  assert.match(adapterSource, /composerTextRowSelectors/);
  assert.match(adapterSource, /setAttribute\("data-framenest-companion", "attach"\)/);
  assert.match(adapterSource, /setAttribute\("aria-label", ATTACH_NAME\)/);
  assert.match(adapterSource, /document\.documentElement\.appendChild\(button\)/);
  assert.match(adapterSource, /addEventListener\("focusin", show, true\)/);
  assert.match(adapterSource, /addEventListener\("focus", show, true\)/);
  assert.match(adapterSource, /addEventListener\("focusin", onComposerFocusIn, true\)/);
  assert.match(adapterSource, /addEventListener\("scroll", repositionVisibleAttaches, true\)/);
  assert.match(adapterSource, /data-framenest-attach-visible/);
  assert.match(adapterSource, /data-framenest-companion-popup-host/);
  assert.match(adapterSource, /attachShadow\(\{\s*mode:\s*"closed"\s*\}\)/);
  assert.match(adapterSource, /runtimeUrl\("ui\/picker\.html"\)/);
  assert.match(adapterSource, /getBoundingClientRect/);
  assert.match(adapterSource, /enoughAbove/);
  assert.match(adapterSource, /::-webkit-inner-spin-button/);
  assert.match(adapterSource, /::-webkit-outer-spin-button/);
  const attachRule = adapterSource.match(
    /\[data-framenest-companion='attach'\] \{[\s\S]*?\n\}/
  );
  assert.ok(attachRule);
  assert.match(attachRule[0], /position:\s*fixed/);
  assert.doesNotMatch(attachRule[0], /position:\s*absolute/);
  assert.doesNotMatch(adapterSource, /textContent\s*=\s*["']Attach from FrameNest["']/);
  assert.doesNotMatch(adapterSource, /textContent\s*=\s*ATTACH_NAME/);
  assert.doesNotMatch(
    adapterSource,
    /\[data-framenest-composer[^\]]*\]:hover[^\n]*\[data-framenest-companion='attach'\]/
  );
});

test("in-page picker iframe WAR is match-limited to X hosts", () => {
  const dumped = JSON.stringify(manifest);
  assert.equal(dumped.includes("<all_urls>"), false);
  const war = manifest.web_accessible_resources;
  assert.ok(Array.isArray(war));
  assert.equal(war.length, 1);
  const entry = war[0];
  assert.deepEqual(entry.matches.slice().sort(), ["https://twitter.com/*", "https://x.com/*"]);
  assert.equal(entry.matches.join(" ").includes("ts.net"), false);
  assert.deepEqual(entry.resources.slice().sort(), [
    "ui/picker.css",
    "ui/picker.html",
    "ui/picker.js",
    "ui/save.css",
    "ui/save.html",
    "ui/save.js",
  ]);
});

test("picker is search-first without a Settings dialog", () => {
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const pickerJs = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  const pickerCss = fs.readFileSync(path.join(REPO, "extension/ui/picker.css"), "utf8");
  assert.doesNotMatch(pickerHtml, /Search titles/);
  assert.doesNotMatch(pickerJs, /Search titles/);
  assert.doesNotMatch(pickerHtml, /class="picker-brand"/);
  assert.doesNotMatch(pickerHtml, /class="brand-mark"/);
  assert.doesNotMatch(pickerHtml, /<h1>FrameNest<\/h1>/);
  assert.doesNotMatch(pickerHtml, />FN<\/span>/);
  assert.match(pickerHtml, /Search memes/);
  assert.doesNotMatch(pickerHtml, /aria-label="Settings"/);
  assert.doesNotMatch(pickerHtml, /id="settings-tab-origin"/);
  assert.doesNotMatch(pickerHtml, /id="settings-dialog"/);
  assert.doesNotMatch(pickerHtml, /class="settings-dialog"/);
  assert.doesNotMatch(pickerHtml, /id="origin"/);
  assert.doesNotMatch(pickerHtml, /<dialog/);
  assert.doesNotMatch(pickerCss, /\.settings-dialog/);
  assert.doesNotMatch(pickerCss, /\.picker-settings/);
  assert.match(pickerJs, new RegExp(`Connect ${BRAND} in the side panel`));
  assert.doesNotMatch(pickerJs, /showModal/);
  assert.doesNotMatch(pickerJs, /openSettings/);
  assert.doesNotMatch(pickerJs, /closeSettings/);
  assert.doesNotMatch(pickerJs, /renderConnection/);
  assert.doesNotMatch(pickerJs, /TYPES\.CONFIGURE_ORIGIN/);
  assert.doesNotMatch(pickerJs, /TYPES\.RESET/);
  assert.doesNotMatch(pickerJs, /innerHTML/);
  assert.doesNotMatch(pickerJs, /form\.submit/);
  assert.doesNotMatch(adapterSource, /tweetButton/);
});

test("in-page picker is search-first, compact, and dismisses on Escape", () => {
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const pickerJs = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  const pickerCss = fs.readFileSync(path.join(REPO, "extension/ui/picker.css"), "utf8");
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  const refreshFn = extractNamedFunction(pickerJs, "refresh");
  const attachCurrentFn = extractNamedFunction(pickerJs, "attachCurrent");
  const queryAt = refreshFn.indexOf("TYPES.PICKER_QUERY");
  const trimAt = refreshFn.indexOf('(search.value || "").trim()');
  const emptyAt = refreshFn.indexOf("if (!q)");
  assert.doesNotMatch(pickerHtml, /id="kind"/);
  assert.doesNotMatch(pickerHtml, /All kinds/);
  assert.doesNotMatch(pickerHtml, /<select/);
  assert.doesNotMatch(pickerCss, /#kind\b/);
  assert.doesNotMatch(pickerCss, /\.picker-toolbar/);
  assert.doesNotMatch(pickerJs, /getElementById\("kind"\)/);
  assert.doesNotMatch(pickerJs, /kind:\s*kind\.value/);
  assert.doesNotMatch(pickerHtml, /id="settings-dialog"/);
  assert.doesNotMatch(pickerHtml, /<dialog/);
  assert.match(pickerHtml, /\sid="search"[\s\S]*\sautofocus/);
  assert.match(pickerJs, /search\.focus\(\)/);
  assert.ok(trimAt >= 0 && emptyAt >= 0 && queryAt >= 0);
  assert.ok(trimAt < emptyAt && emptyAt < queryAt);
  assert.doesNotMatch(pickerHtml, /Type to search memes/);
  assert.doesNotMatch(pickerJs, /Type to search memes/);
  assert.doesNotMatch(pickerCss, /Type to search memes/);
  assert.doesNotMatch(pickerJs, /blankSearchStatus/);
  assert.match(refreshFn, /setText\(pickerStatus, ""\)/);
  assert.match(refreshFn, /No eligible memes/);
  assert.doesNotMatch(refreshFn, /kind:/);
  assert.match(pickerJs, /search\.addEventListener\("keydown"[\s\S]*?Enter[\s\S]*?attachCurrent/);
  assert.match(attachCurrentFn, /if \(runtimeStale \|\| !trimmedQuery\(\)\)/);
  assert.match(attachCurrentFn, /attachItem\(item\)/);
  assert.match(pickerJs, /key === "Escape"/);
  assert.match(pickerJs, /TYPES\.DISMISS_PICKER/);
  assert.match(workerSource, /TYPES\.DISMISS_PICKER/);
  assert.match(workerSource, /chrome\.tabs\.sendMessage\(boundTabId/);
  assert.match(adapterSource, /TYPES\.DISMISS_PICKER[\s\S]{0,80}closeAttachPopup\(\)/);
  assert.doesNotMatch(pickerJs, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(pickerJs, /targetOrigin:\s*["']\*["']/);
  assert.match(pickerCss, /html,\s*\nbody \{[\s\S]*?overflow:\s*hidden/);
  assert.match(pickerCss, /#picker \{[\s\S]*?overflow:\s*hidden/);
  assert.match(saveHtml, />Save</);
  assert.match(saveJs, /TYPES\.SAVE_POST/);
  assert.match(workerSource, /waitForPortAttachOutcome/);
  assert.match(workerSource, /payload\.attached === true/);
  assert.match(adapterSource, /payload: \{ attached: true/);
});

test("picker hides empty chrome, cycles arrows after two hits, and ++ opens attach", () => {
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const pickerJs = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  const pickerCss = fs.readFileSync(path.join(REPO, "extension/ui/picker.css"), "utf8");
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  assert.match(
    pickerHtml,
    /id="preview"[\s\S]*id="preview-nav"[\s\S]*id="preview-prev"[\s\S]*id="preview-next"[\s\S]*id="attach-selected"/
  );
  assert.doesNotMatch(pickerHtml, /id="kind"/);
  assert.doesNotMatch(pickerHtml, /id="settings-dialog"/);
  assert.doesNotMatch(pickerJs, /Type to search memes/);
  assert.doesNotMatch(pickerJs, /blankSearchStatus/);

  const flexBlock = pickerCss.match(/\.picker-preview \{[\s\S]*?\n\}/);
  assert.ok(flexBlock);
  assert.match(flexBlock[0], /display:\s*flex/);
  const hiddenRule = pickerCss.match(/\.picker-preview\[hidden\],\s*\n#preview\[hidden\] \{[\s\S]*?\n\}/);
  assert.ok(hiddenRule, "preview [hidden] must beat display:flex");
  assert.match(hiddenRule[0], /display:\s*none/);
  assert.ok(pickerCss.indexOf(flexBlock[0]) < pickerCss.indexOf(hiddenRule[0]));
  assert.match(pickerCss, /\.catalog-pagination__nav\[hidden\] \{[\s\S]*?display:\s*none/);
  assert.match(pickerCss, /#picker-status:empty/);

  const renderFn = extractNamedFunction(pickerJs, "renderPreview");
  const cycleFn = extractNamedFunction(pickerJs, "cycleHitsFromKey");
  const refreshFn = extractNamedFunction(pickerJs, "refresh");
  assert.match(renderFn, /previewNav\.hidden = !many/);
  assert.match(renderFn, /items\.length > 1/);
  assert.match(cycleFn, /event\.key === "ArrowLeft" && items\.length > 1/);
  assert.match(cycleFn, /event\.key === "ArrowRight" && items\.length > 1/);
  assert.match(cycleFn, /event\.preventDefault\(\)/);
  assert.match(pickerJs, /search\.addEventListener\("keydown"/);
  assert.match(pickerJs, /document\.addEventListener\("keydown"/);
  assert.match(pickerJs, /if \(event\.target === search\)/);
  assert.match(refreshFn, /if \(!q\)/);
  const emptyAt = refreshFn.indexOf("if (!q)");
  const queryAt = refreshFn.indexOf("TYPES.PICKER_QUERY");
  assert.ok(emptyAt >= 0 && queryAt > emptyAt);
  assert.match(pickerJs, /TYPES\.PICKER_LAYOUT/);
  assert.match(pickerJs, /compact: compact/);
  assert.doesNotMatch(pickerJs, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(adapterSource, /postMessage\([^)]*,\s*["']\*["']/);
  assert.match(workerSource, /TYPES\.PICKER_LAYOUT/);
  assert.match(workerSource, /forwardPickerLayout/);
  assert.match(adapterSource, /TYPES\.PICKER_LAYOUT/);
  assert.match(adapterSource, /applyPickerLayout/);

  const openFn = extractNamedFunction(adapterSource, "openAttachPopup");
  const keepAt = openFn.indexOf("keepOpen");
  const toggleCloseAt = openFn.indexOf("closeAttachPopup()");
  assert.ok(keepAt >= 0 && toggleCloseAt > keepAt);
  assert.match(openFn, /if \(keepOpen\) \{\s*return;/);
  assert.match(adapterSource, /openAttachPopup\(button, composerChrome, \{ keepOpen: true \}\)/);
  assert.match(adapterSource, /editable\.addEventListener\("beforeinput"/);
  assert.match(adapterSource, /editable\.addEventListener\("input"/);
  assert.doesNotMatch(adapterSource, /window\.addEventListener\("beforeinput"/);
  assert.doesNotMatch(adapterSource, /document\.addEventListener\("beforeinput"/);

  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  assert.equal(hooks.plusPlusTokenAtCaret("++"), true);
  assert.equal(hooks.plusPlusTokenAtCaret(" ++"), true);
  assert.equal(hooks.plusPlusTokenAtCaret("\n++"), true);
  assert.equal(hooks.plusPlusTokenAtCaret("hello ++"), true);
  assert.equal(hooks.plusPlusTokenAtCaret("C++"), false);
  assert.equal(hooks.plusPlusTokenAtCaret("foo++"), false);
  assert.equal(hooks.plusPlusTokenAtCaret("+"), false);
  assert.equal(hooks.plusPlusTokenAtCaret(""), false);
  const consumedEmpty = hooks.consumePlusPlusFromValue("++", 2);
  assert.equal(consumedEmpty.value, "");
  assert.equal(consumedEmpty.caret, 0);
  const consumedHello = hooks.consumePlusPlusFromValue("hello ++", 8);
  assert.equal(consumedHello.value, "hello ");
  assert.equal(consumedHello.caret, 6);
  assert.equal(hooks.consumePlusPlusFromValue("C++", 3), null);
  assert.equal(hooks.consumePlusPlusFromValue("foo++", 5), null);

  assert.match(saveHtml, />Save</);
  assert.match(saveJs, /TYPES\.SAVE_POST/);
  assert.match(workerSource, /waitForPortAttachOutcome/);
  assert.match(workerSource, /payload\.attached === true/);
  assert.match(adapterSource, /payload: \{ attached: true/);
});

function createMiniDom() {
  class MiniEl {
    constructor(tag, attrs) {
      this.tagName = String(tag).toUpperCase();
      this.attrs = {};
      Object.keys(attrs || {}).forEach((key) => {
        this.attrs[key] = String(attrs[key]);
      });
      this.parentElement = null;
      this.parentNode = null;
      this.childNodes = [];
      this.style = {};
      this.dataset = {};
      this.ownerDocument = null;
      this.files = null;
    }

    get children() {
      return this.childNodes.filter((node) => node && node.tagName);
    }

    getAttribute(name) {
      if (!Object.prototype.hasOwnProperty.call(this.attrs, name)) {
        return null;
      }
      return this.attrs[name];
    }

    setAttribute(name, value) {
      this.attrs[name] = String(value);
    }

    removeAttribute(name) {
      delete this.attrs[name];
    }

    hasAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name);
    }

    getBoundingClientRect() {
      return { top: 0, left: 0, right: 120, bottom: 40, width: 120, height: 40, x: 0, y: 0 };
    }

    matches(selector) {
      return matchesSelector(this, selector);
    }

    querySelector(selector) {
      return this.querySelectorAll(selector)[0] || null;
    }

    querySelectorAll(selector) {
      const out = [];
      const visit = (node) => {
        node.children.forEach((child) => {
          if (matchesSelector(child, selector)) {
            out.push(child);
          }
          visit(child);
        });
      };
      visit(this);
      return out;
    }

    contains(other) {
      if (other === this) {
        return true;
      }
      let node = other;
      while (node) {
        if (node === this) {
          return true;
        }
        node = node.parentElement;
      }
      return false;
    }

    appendChild(child) {
      if (child.parentElement) {
        child.parentElement.removeChild(child);
      }
      child.parentElement = this;
      child.parentNode = this;
      child.ownerDocument = this.ownerDocument;
      this.childNodes.push(child);
      return child;
    }

    removeChild(child) {
      const index = this.childNodes.indexOf(child);
      if (index >= 0) {
        this.childNodes.splice(index, 1);
      }
      child.parentElement = null;
      child.parentNode = null;
      return child;
    }

    addEventListener() {}

    removeEventListener() {}

    dispatchEvent() {
      return true;
    }

    focus() {
      if (this.ownerDocument) {
        this.ownerDocument.activeElement = this;
      }
    }
  }

  function matchesSelector(el, selector) {
    let rest = String(selector).trim();
    const tagMatch = rest.match(/^([a-zA-Z][\w-]*)/);
    if (tagMatch) {
      if (el.tagName !== tagMatch[1].toUpperCase()) {
        return false;
      }
      rest = rest.slice(tagMatch[1].length);
    }
    const attrRe = /\[([^\]]+)\]/g;
    let attr;
    let sawAttr = false;
    while ((attr = attrRe.exec(rest))) {
      sawAttr = true;
      const body = attr[1];
      const opMatch = body.match(/^([^\s=~^$*|]+)\s*(\*=|=)\s*(.*)$/);
      if (opMatch) {
        const name = opMatch[1];
        const op = opMatch[2];
        let value = opMatch[3].trim();
        if (
          (value.startsWith("'") && value.endsWith("'")) ||
          (value.startsWith('"') && value.endsWith('"'))
        ) {
          value = value.slice(1, -1);
        }
        const actual = el.getAttribute(name);
        if (op === "=" && actual !== value) {
          return false;
        }
        if (op === "*=" && (!actual || !actual.includes(value))) {
          return false;
        }
      } else if (el.getAttribute(body.trim()) == null) {
        return false;
      }
    }
    return Boolean(tagMatch || sawAttr);
  }

  const documentElement = new MiniEl("html");
  const body = new MiniEl("body");
  const documentRef = {
    documentElement,
    body,
    contains(node) {
      if (!node) {
        return false;
      }
      if (node === documentRef || node === documentElement || node === body) {
        return true;
      }
      return documentElement.contains(node);
    },
    createElement(tag) {
      const node = new MiniEl(tag);
      node.ownerDocument = documentRef;
      return node;
    },
    createElementNS(_ns, tag) {
      const node = new MiniEl(tag);
      node.ownerDocument = documentRef;
      return node;
    },
    querySelector(selector) {
      return documentElement.querySelector(selector);
    },
    querySelectorAll(selector) {
      return documentElement.querySelectorAll(selector);
    },
    getElementById(id) {
      const visit = (node) => {
        if (!node) {
          return null;
        }
        if (node.getAttribute && node.getAttribute("id") === id) {
          return node;
        }
        if (node.id === id) {
          return node;
        }
        const children = node.childNodes || [];
        for (let index = 0; index < children.length; index += 1) {
          const found = visit(children[index]);
          if (found) {
            return found;
          }
        }
        return null;
      };
      return visit(documentElement);
    },
    addEventListener() {},
    removeEventListener() {},
  };
  documentElement.ownerDocument = documentRef;
  body.ownerDocument = documentRef;
  documentElement.appendChild(body);
  const windowRef = {
    document: documentRef,
    getComputedStyle() {
      return { position: "relative" };
    },
    setTimeout() {
      return 0;
    },
    addEventListener() {},
    removeEventListener() {},
  };
  return { MiniEl, document: documentRef, window: windowRef, body };
}

function el(dom, tag, attrs, children) {
  const node = new dom.MiniEl(tag, attrs || {});
  node.ownerDocument = dom.document;
  (children || []).forEach((child) => {
    node.appendChild(child);
  });
  return node;
}

function loadAdapterHooks(dom, options) {
  const hooks = {};
  const sendMessage =
    options && typeof options.sendMessage === "function"
      ? options.sendMessage
      : function sendMessage() {};
  const hasRuntimeId = Boolean(
    options && Object.prototype.hasOwnProperty.call(options, "runtimeId")
  );
  const runtime = {
    id: hasRuntimeId ? options.runtimeId : "x-adapter-test",
    get lastError() {
      return (options && options.runtimeLastError) || null;
    },
    sendMessage(message, callback) {
      if (options && options.sendError) {
        throw options.sendError;
      }
      if (typeof callback === "function") {
        sendMessage(message, callback);
      }
    },
    onMessage: {
      addListener() {
        if (options && options.messageListenerError) {
          throw options.messageListenerError;
        }
      },
    },
    onConnect: {
      addListener() {
        if (options && options.connectListenerError) {
          throw options.connectListenerError;
        }
      },
    },
    getURL(resource) {
      if (options && options.getUrlError) {
        throw options.getUrlError;
      }
      return resource;
    },
  };
  const sandbox = {
    chrome: {
      runtime,
    },
    document: dom.document,
    window: dom.window,
    MutationObserver: class {
      observe() {}
      disconnect() {}
    },
    FrameNestCompanion: companion,
    FrameNestXAdapterContractV1: contract,
    FrameNestXAdapterTestHooks: hooks,
    File: class File {
      constructor(bits, name, options) {
        this.bits = bits;
        this.name = name;
        this.type = (options && options.type) || "";
      }
    },
    DataTransfer: class DataTransfer {
      constructor() {
        this.files = [];
        this.items = {
          add: (file) => {
            this.files = [file];
          },
        };
      }
    },
    Event: class Event {
      constructor(type, init) {
        this.type = type;
        this.bubbles = Boolean(init && init.bubbles);
      }
    },
  };
  sandbox.globalThis = sandbox;
  vm.runInNewContext(adapterSource, sandbox, { filename: "x_adapter.js" });
  return hooks;
}

test("invalidated X contexts become stale once, disable controls, and remove partial hosts", () => {
  const dom = createMiniDom();
  const save = el(dom, "button", { "data-framenest-companion": "save" });
  const attach = el(dom, "button", { "data-framenest-companion": "attach" });
  const partialSave = el(dom, "div", { "data-framenest-companion-save-host": "" });
  const partialPicker = el(dom, "div", { "data-framenest-companion-popup-host": "" });
  dom.body.appendChild(save);
  dom.body.appendChild(attach);
  dom.body.appendChild(partialSave);
  dom.body.appendChild(partialPicker);
  const hooks = loadAdapterHooks(dom, { runtimeId: "" });

  assert.equal(hooks.runtimeUrl("ui/save.html"), "");
  assert.equal(save.getAttribute("disabled"), "true");
  assert.equal(attach.getAttribute("disabled"), "true");
  assert.equal(save.getAttribute("aria-label"), companion.EXTENSION_CONTEXT_RECOVERY_COPY);
  assert.equal(attach.getAttribute("title"), companion.EXTENSION_CONTEXT_RECOVERY_COPY);
  assert.equal(dom.document.contains(partialSave), false);
  assert.equal(dom.document.contains(partialPicker), false);
  let notices = dom.document.querySelectorAll("[data-framenest-reload-notice]");
  assert.equal(notices.length, 1);
  assert.equal(notices[0].getAttribute("role"), "alert");
  assert.equal(notices[0].textContent, companion.EXTENSION_CONTEXT_RECOVERY_COPY);
  assert.equal(notices[0].style.position, "fixed");

  hooks.markStale(new Error("Extension context invalidated."));
  hooks.runtimeUrl("ui/picker.html");
  notices = dom.document.querySelectorAll("[data-framenest-reload-notice]");
  assert.equal(notices.length, 1);
});

test("Save and picker URL invalidation appends no partial iframe host", () => {
  const accepted = companion.acceptXPostUrl("https://x.com/fixture/status/123456789");
  const saveDom = createMiniDom();
  const saveButton = el(saveDom, "button", { "data-framenest-companion": "save" });
  saveDom.body.appendChild(saveButton);
  const saveHooks = loadAdapterHooks(saveDom, {
    getUrlError: new Error("Extension context invalidated."),
  });
  saveHooks.openSavePopup(saveButton, accepted);
  assert.equal(
    saveDom.document.querySelector("[data-framenest-companion-save-host]"),
    null
  );

  const pickerDom = createMiniDom();
  const pickerButton = el(pickerDom, "button", { "data-framenest-companion": "attach" });
  pickerDom.body.appendChild(pickerButton);
  const pickerHooks = loadAdapterHooks(pickerDom, {
    getUrlError: new Error("Extension context invalidated."),
  });
  pickerHooks.openAttachPopup(pickerButton, pickerDom.body);
  assert.equal(
    pickerDom.document.querySelector("[data-framenest-companion-popup-host]"),
    null
  );
});

test("runtime helpers preserve valid behavior and propagate unrelated errors", async () => {
  const validDom = createMiniDom();
  const validHooks = loadAdapterHooks(validDom, {
    sendMessage(_message, callback) {
      callback({ ok: true, value: "unchanged" });
    },
  });
  assert.equal(validHooks.runtimeUrl("ui/save.html"), "ui/save.html");
  assert.deepEqual(
    await validHooks.request(companion.TYPES.IDENTITY, {}),
    { ok: true, value: "unchanged" }
  );
  assert.equal(validHooks.addRuntimeListener("onMessage", () => {}), true);

  const invalidCallbackHooks = loadAdapterHooks(createMiniDom(), {
    runtimeLastError: { message: "Extension context invalidated." },
    sendMessage(_message, callback) {
      callback(undefined);
    },
  });
  const invalidCallback = await invalidCallbackHooks.request(companion.TYPES.IDENTITY, {});
  assert.equal(invalidCallback.stale, true);
  assert.equal(invalidCallback.error, "extension_context_invalidated");

  const ordinaryCallbackHooks = loadAdapterHooks(createMiniDom(), {
    runtimeLastError: { message: "Receiving end does not exist" },
    sendMessage(_message, callback) {
      callback(undefined);
    },
  });
  const ordinaryCallback = await ordinaryCallbackHooks.request(companion.TYPES.IDENTITY, {});
  assert.equal(ordinaryCallback.error, "extension_unavailable");
  assert.equal(ordinaryCallback.stale, undefined);

  const unrelatedUrlHooks = loadAdapterHooks(createMiniDom(), {
    getUrlError: new Error("unrelated getURL failure"),
  });
  assert.throws(() => unrelatedUrlHooks.runtimeUrl("ui/save.html"), /unrelated getURL failure/);

  const unrelatedSendHooks = loadAdapterHooks(createMiniDom(), {
    sendError: new Error("unrelated send failure"),
  });
  await assert.rejects(
    unrelatedSendHooks.request(companion.TYPES.IDENTITY, {}),
    /unrelated send failure/
  );

  const unrelatedListenerHooks = loadAdapterHooks(createMiniDom(), {
    messageListenerError: new Error("unrelated listener failure"),
  });
  assert.throws(
    () => unrelatedListenerHooks.addRuntimeListener("onMessage", () => {}),
    /unrelated listener failure/
  );
});

test("every X runtime operation routes through targeted guards and UI requests share recovery copy", () => {
  const saveSource = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  const pickerSource = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  assert.equal((adapterSource.match(/chrome\.runtime/g) || []).length, 1);
  assert.doesNotMatch(adapterSource, /chrome\.runtime\.(?:getURL|sendMessage|onMessage|onConnect|lastError)/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /runtimeUrl\("ui\/save\.html"\)/);
  assert.match(extractNamedFunction(adapterSource, "savePopupTargetOrigin"), /runtimeUrl\("ui\/save\.html"\)/);
  assert.match(extractNamedFunction(adapterSource, "openAttachPopup"), /runtimeUrl\("ui\/picker\.html"\)/);
  assert.match(adapterSource, /addRuntimeListener\("onMessage"/);
  assert.match(adapterSource, /addRuntimeListener\("onConnect"/);
  assert.match(extractNamedFunction(adapterSource, "request"), /guardInvalidatedRuntime/);
  assert.match(extractNamedFunction(adapterSource, "scan"), /if \(stale\)/);
  assert.match(extractNamedFunction(adapterSource, "injectSave"), /if \(stale\)/);
  assert.match(extractNamedFunction(adapterSource, "injectAttach"), /if \(stale\)/);
  assert.match(extractNamedFunction(saveSource, "request"), /guardInvalidatedRuntime/);
  assert.match(extractNamedFunction(pickerSource, "request"), /guardInvalidatedRuntime/);
  assert.match(saveSource, /setStatus\(formStatus, RELOAD_RECOVERY, "error"\)/);
  assert.match(saveSource, /setFormBusy\(true\)/);
  assert.match(pickerSource, /setText\(pickerStatus, RELOAD_RECOVERY\)/);
  assert.match(pickerSource, /attachSelected\.disabled = true/);
  assert.doesNotMatch(workerSource, /EXTENSION_CONTEXT_RECOVERY_COPY/);
});

function pathDataFrom(node, out) {
  const collected = out || [];
  if (node && node.attrs && node.attrs.d) {
    collected.push(node.attrs.d);
  }
  (node.childNodes || []).forEach((child) => {
    pathDataFrom(child, collected);
  });
  return collected;
}

test("injectAttach does not treat WeakSet membership as a permanent skip", () => {
  const injectFn = adapterSource.match(/function injectAttach\(composerRoot\) \{[\s\S]*?\n  \}/);
  assert.ok(injectFn);
  assert.match(injectFn[0], /injected\.has\(composerRoot\)/);
  assert.match(injectFn[0], /document\.contains/);
  assert.match(injectFn[0], /document\.documentElement\.appendChild\(button\)/);
  assert.doesNotMatch(injectFn[0], /if \(injected\.has\(composerRoot\)\) \{\s*return;/);
  assert.doesNotMatch(injectFn[0], /textRow\.appendChild/);
  assert.doesNotMatch(injectFn[0], /ensureContainingBlock/);
  assert.doesNotMatch(injectFn[0], /markStale/);
  assert.doesNotMatch(adapterSource, /hops < 12/);
  assert.match(adapterSource, /hops < 48/);
  assert.match(adapterSource, /composerChromeHasSignal/);
  assert.match(adapterSource, /composerMediaButtonSelectors/);
});

test("findComposerTextRow uses the non-editable parent when chrome has only the textbox", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const chrome = el(dom, "div", {}, [editable, toolbar]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  const foundChrome = hooks.findComposerChrome(editable);
  assert.equal(foundChrome, chrome);
  const row = hooks.findComposerTextRow(editable, foundChrome);
  assert.ok(row);
  assert.equal(row, chrome);
  assert.notEqual(row, editable);
  assert.equal(row.getAttribute("contenteditable"), null);
});

test("findComposerChrome reaches a toolbar ancestor beyond 12 hops", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  let inner = editable;
  for (let i = 0; i < 20; i += 1) {
    inner = el(dom, "div", {}, [inner]);
  }
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const chrome = el(dom, "div", {}, [inner, toolbar]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  assert.equal(hooks.findComposerChrome(editable), chrome);
  const row = hooks.findComposerTextRow(editable, chrome);
  assert.ok(row);
  assert.notEqual(row, editable);
  assert.equal(row.getAttribute("contenteditable"), null);
});

test("injectAttach re-injects after the attach node leaves the document", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const file = el(dom, "input", { type: "file", accept: "image/*,video/mp4" });
  const chrome = el(dom, "div", {}, [editable, toolbar, file]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  hooks.injectAttach(editable);
  const firstButton = dom.document.documentElement.querySelector("[data-framenest-companion='attach']");
  assert.ok(firstButton);
  assert.equal(firstButton.parentElement, dom.document.documentElement);
  assert.equal(chrome.querySelector("[data-framenest-companion='attach']"), null);
  firstButton.parentElement.removeChild(firstButton);
  assert.equal(dom.document.documentElement.querySelector("[data-framenest-companion='attach']"), null);
  assert.equal(dom.document.contains(firstButton), false);
  hooks.injectAttach(editable);
  const secondButton = dom.document.documentElement.querySelector("[data-framenest-companion='attach']");
  assert.ok(secondButton);
  assert.notEqual(secondButton, firstButton);
  assert.equal(secondButton.parentElement, dom.document.documentElement);
});

test("injectAttach does not mount inside the text row or set a host containing block", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const row = el(dom, "div", { "data-framenest-composer-text-row": "" }, [editable]);
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const file = el(dom, "input", { type: "file", accept: "image/*,video/mp4" });
  const chrome = el(dom, "div", { "data-framenest-composer-chrome": "" }, [row, toolbar, file]);
  dom.body.appendChild(chrome);
  dom.window.getComputedStyle = () => ({ position: "static" });
  const hooks = loadAdapterHooks(dom);
  hooks.injectAttach(editable);
  const button = dom.document.documentElement.querySelector("[data-framenest-companion='attach']");
  assert.ok(button);
  assert.equal(button.parentElement, dom.document.documentElement);
  assert.equal(row.querySelector("[data-framenest-companion='attach']"), null);
  assert.equal(chrome.querySelector("[data-framenest-companion='attach']"), null);
  assert.notEqual(row.style.position, "relative");
  assert.notEqual(chrome.style.position, "relative");
  assert.notEqual(editable.style.position, "relative");
  assert.equal(button.style.position, "fixed");
});

test("missing composer file input skips that composer without page-wide stale", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const chrome = el(dom, "div", {}, [editable, toolbar]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  hooks.injectAttach(editable);
  assert.equal(chrome.querySelector("[data-framenest-companion='attach']"), null);
  const save = el(dom, "button", { "data-framenest-companion": "save" });
  dom.body.appendChild(save);
  hooks.injectAttach(editable);
  assert.equal(save.getAttribute("disabled"), null);
});

test("failed Save overlay keeps the plus glyph", () => {
  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  const failed = hooks.saveIconSvg("failed");
  const idle = hooks.saveIconSvg("idle");
  const paths = pathDataFrom(failed);
  assert.ok(paths.includes("M12 6.5v11M6.5 12h11"));
  assert.equal(paths.some((d) => d.includes("M8 8l8 8") || d.includes("M16 8l-8 8")), false);
  assert.ok(pathDataFrom(idle).includes("M12 6.5v11M6.5 12h11"));
});

test("toolbar action opens the side-panel shell instead of a picker popup", () => {
  assert.equal("default_popup" in (manifest.action || {}), false);
  assert.equal(manifest.side_panel.default_path, "ui/sidebar.html");
  assert.equal("externally_connectable" in manifest, false);
  assert.equal("content_security_policy" in manifest, false);
  assert.match(workerSource, /enableSidePanelOnActionClick\(\);/);
  assert.match(workerSource, /onInstalled[\s\S]*enableSidePanelOnActionClick/);
  const sidebarHtml = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.html"), "utf8");
  const sidebarJs = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
  const sidebarCss = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.css"), "utf8");
  assert.match(sidebarHtml, /id="frame"/);
  assert.ok(sidebarHtml.indexOf('id="review-history-toggle"') < sidebarHtml.indexOf('id="review-history"'));
  assert.ok(sidebarHtml.indexOf('id="review-history"') < sidebarHtml.indexOf('id="frame"'));
  assert.equal(sidebarHtml.indexOf('id="review-inbox"'), -1);
  assert.equal(sidebarHtml.indexOf('id="review-inbox-list"'), -1);
  assert.match(sidebarHtml, /class="title-bar__wordmark">Kronika</);
  assert.doesNotMatch(sidebarHtml, /FrameNest/);
  assert.match(sidebarHtml, /id="chrome-action"/);
  assert.match(sidebarHtml, />Connect</);
  assert.doesNotMatch(sidebarHtml, />Reset</);
  assert.match(sidebarHtml, /id="settings-dialog"/);
  assert.match(sidebarHtml, /id="origin"/);
  assert.ok(sidebarHtml.indexOf('class="title-bar"') < sidebarHtml.indexOf('id="origin"'));
  assert.ok(sidebarHtml.indexOf('id="settings-dialog"') < sidebarHtml.indexOf('id="origin"'));
  assert.match(sidebarCss, /\.title-bar\s*\{/);
  assert.match(sidebarCss, /--accent:\s*#00ff41;/);
  assert.match(sidebarCss, /--accent-border:\s*rgba\(0,\s*255,\s*65,\s*0\.42\);/);
  assert.doesNotMatch(sidebarCss, /--history-neon:/);
  assert.doesNotMatch(sidebarCss, /--history-green-1:/);
  assert.doesNotMatch(sidebarCss, /--chrome-green:/);
  const titleBarChrome = sidebarCss.match(/\.title-bar\s*\{[\s\S]*?\n\}/);
  assert.ok(titleBarChrome, "title-bar fill");
  assert.match(titleBarChrome[0], /background:\s*var\(--surface-input\)/);
  assert.match(titleBarChrome[0], /border-bottom:\s*1px solid var\(--accent-border\)/);
  assert.doesNotMatch(titleBarChrome[0], /background:\s*#00ff41/);
  assert.doesNotMatch(titleBarChrome[0], /--chrome-green/);
  assert.doesNotMatch(titleBarChrome[0], /--history-green/);
  const titleBarAction = sidebarCss.match(/\.title-bar__action\s*\{[\s\S]*?\n\}/);
  assert.ok(titleBarAction, "title-bar action fill");
  assert.match(titleBarAction[0], /background:\s*transparent/);
  assert.match(titleBarAction[0], /border:\s*1px solid var\(--accent-border\)/);
  assert.doesNotMatch(titleBarAction[0], /--accent-strong/);
  assert.doesNotMatch(sidebarCss, /\.review-list--compact\s*>\s*li:nth-child\(/);
  const historyAll = sidebarCss.match(/\.review-history-all\s*\{[\s\S]*?\n\}/);
  assert.ok(historyAll, "compact/All fill rules");
  assert.match(historyAll[0], /background:\s*var\(--surface-input\)/);
  assert.match(historyAll[0], /border:\s*1px solid var\(--accent-border\)/);
  assert.doesNotMatch(historyAll[0], /background:\s*#00ff41/);
  assert.doesNotMatch(historyAll[0], /--chrome-green/);
  assert.doesNotMatch(historyAll[0], /--history-green/);
  const compactHover = sidebarCss.match(
    /\.review-list--compact\s*>\s*li\s*>\s*button:hover[\s\S]*?\.review-history-all:focus-visible\s*\{[\s\S]*?\n\}/
  );
  assert.ok(compactHover, "compact/All hover fill");
  assert.match(compactHover[0], /background:\s*var\(--accent-soft\)/);
  assert.match(compactHover[0], /border-color:\s*var\(--accent\)/);
  assert.doesNotMatch(compactHover[0], /background:\s*#00ff41/);
  assert.doesNotMatch(compactHover[0], /--history-neon/);
  assert.doesNotMatch(sidebarHtml, /ui\/picker\.html/);
  assert.doesNotMatch(sidebarJs, /window\.open/);
  assert.doesNotMatch(sidebarJs, /ui\/picker\.html/);
  assert.doesNotMatch(sidebarJs, /postMessage\([^)]*,\s*["']\*["']/);
  assert.match(sidebarJs, /acceptFrameNestOrigin/);
  assert.match(sidebarJs, /textContent = connected \? "Disconnect" : "Connect"/);
  assert.match(sidebarJs, /TYPES\.RESET/);
  assert.doesNotMatch(sidebarJs, /could not be framed/);
  const warResources = manifest.web_accessible_resources[0].resources;
  assert.equal(warResources.includes("ui/sidebar.html"), false);
  assert.equal(warResources.includes("ui/sidebar.js"), false);
  assert.equal(warResources.includes("ui/sidebar.css"), false);
  assert.equal(warResources.includes("ui/review.html"), false);
  assert.equal(warResources.includes("ui/review.js"), false);
  assert.equal(warResources.includes("ui/review.css"), false);
});

test("side-panel Settings is a sheet under the title bar, not a centered modal", () => {
  const sidebarHtml = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.html"), "utf8");
  const sidebarJs = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
  const sidebarCss = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.css"), "utf8");
  const titleBarAt = sidebarHtml.indexOf('class="title-bar"');
  const settingsAt = sidebarHtml.indexOf('id="settings-dialog"');
  const originAt = sidebarHtml.indexOf('id="origin"');
  const mainAt = sidebarHtml.indexOf('class="sidebar-main"');
  assert.ok(titleBarAt >= 0 && settingsAt > titleBarAt);
  assert.ok(originAt > settingsAt && settingsAt < mainAt);
  assert.match(sidebarHtml, /class="settings-dialog__sheet"/);
  assert.doesNotMatch(sidebarJs, /showModal/);
  assert.match(sidebarJs, /settingsDialog\.show\(\)/);
  assert.match(sidebarCss, /--title-bar-height:\s*36px;/);
  assert.match(sidebarCss, /\.title-bar\s*\{[\s\S]*?min-height:\s*var\(--title-bar-height\)/);
  const dialogBlock = sidebarCss.match(/\.settings-dialog\s*\{[\s\S]*?\n\}/);
  assert.ok(dialogBlock, "settings-dialog CSS block");
  assert.match(dialogBlock[0], /position:\s*fixed;/);
  assert.match(dialogBlock[0], /top:\s*var\(--title-bar-height\);/);
  assert.match(dialogBlock[0], /left:\s*0;/);
  assert.match(dialogBlock[0], /right:\s*0;/);
  assert.match(dialogBlock[0], /width:\s*100%;/);
  assert.match(dialogBlock[0], /margin:\s*0;/);
  assert.match(dialogBlock[0], /max-width:\s*none;/);
  assert.doesNotMatch(sidebarCss, /width:\s*min\(420px/);
  assert.doesNotMatch(sidebarCss, /::backdrop\s*\{[\s\S]*rgba\(0,\s*0,\s*0,\s*0\.6\)/);
});

test("side-panel Settings Save sits under origin; empty title-bar Connect opens Settings", () => {
  const sidebarHtml = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.html"), "utf8");
  const sidebarJs = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
  const sidebarCss = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.css"), "utf8");
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const originAt = sidebarHtml.indexOf('id="origin"');
  const settingsSaveAt = sidebarHtml.indexOf('id="settings-save"');
  const settingsDialogAt = sidebarHtml.indexOf('id="settings-dialog"');
  const chromeActionAt = sidebarHtml.indexOf('id="chrome-action"');
  const mainAt = sidebarHtml.indexOf('class="sidebar-main"');
  assert.ok(originAt > settingsDialogAt);
  assert.ok(settingsSaveAt > originAt && settingsSaveAt < mainAt);
  assert.ok(chromeActionAt >= 0 && chromeActionAt < settingsDialogAt);
  assert.doesNotMatch(sidebarHtml, /id="settings-connect"/);
  assert.doesNotMatch(sidebarHtml, /settings-dialog__origin-row/);
  assert.match(sidebarHtml, /id="settings-save"/);
  assert.match(sidebarHtml, /aria-label="Save settings"/);
  assert.match(sidebarHtml, />\s*Save\s*</);
  assert.match(sidebarHtml, /id="chrome-action"/);
  assert.match(sidebarHtml, />Connect</);
  assert.match(sidebarHtml, /Save writes settings/);
  assert.match(sidebarHtml, /Connect and Disconnect in the title bar/);
  assert.doesNotMatch(sidebarHtml, /Connect in Settings saves the origin/);
  assert.doesNotMatch(sidebarHtml, />Reset</);
  assert.doesNotMatch(sidebarCss, /settings-dialog__origin-row/);
  assert.doesNotMatch(sidebarCss, /settings-dialog__connect/);
  assert.match(sidebarCss, /\.settings-dialog__save/);
  assert.match(sidebarCss, /\.settings-dialog__save:disabled/);
  assert.match(sidebarJs, /getElementById\("settings-save"\)/);
  assert.match(sidebarJs, /settingsSave\.addEventListener\("click"/);
  assert.doesNotMatch(sidebarJs, /settings-connect/);
  assert.doesNotMatch(sidebarJs, /settingsConnect/);
  assert.match(sidebarJs, /function syncSettingsSave/);
  assert.match(sidebarJs, /originInput\.value\.trim\(\)/);
  assert.match(sidebarJs, /origin !== storedOrigin/);
  assert.match(sidebarJs, /settingsSave\.disabled = runtimeStale \|\| !origin \|\| !dirty/);
  assert.match(sidebarJs, /originInput\.addEventListener\("input", syncSettingsSave\)/);
  assert.match(sidebarJs, /originInput\.addEventListener\("change", syncSettingsSave\)/);
  assert.match(sidebarJs, /TYPES\.CONFIGURE_ORIGIN/);
  assert.match(sidebarJs, /textContent = connected \? "Disconnect" : "Connect"/);
  assert.match(sidebarJs, new RegExp(`Connect ${BRAND} in Settings`));
  assert.doesNotMatch(sidebarJs, /Connect FrameNest to open the library/);
  const connectFn = sidebarJs.match(/async function connect\(\) \{[\s\S]*?\n  \}/);
  assert.ok(connectFn, "connect()");
  assert.match(connectFn[0], /TYPES\.CONFIGURE_ORIGIN/);
  assert.match(connectFn[0], /closeSettings\(\)/);
  assert.match(connectFn[0], /hostFrame\(storedOrigin\)/);
  const resetFn = sidebarJs.match(/async function reset\(\) \{[\s\S]*?\n  \}/);
  assert.ok(resetFn, "reset()");
  assert.match(resetFn[0], /TYPES\.RESET/);
  assert.match(resetFn[0], /openSettings\(\)/);
  assert.match(resetFn[0], /clearFrame\(\)/);
  const chromeFn = sidebarJs.match(/function onChromeAction\(\) \{[\s\S]*?\n  \}/);
  assert.ok(chromeFn, "onChromeAction()");
  assert.match(chromeFn[0], /void reset\(\)/);
  assert.match(chromeFn[0], /promptConnectInSettings\(\)/);
  assert.match(chromeFn[0], /void connect\(\)/);
  assert.doesNotMatch(pickerHtml, /id="settings-dialog"/);
  assert.doesNotMatch(pickerHtml, /<dialog/);

  const mini = createMiniDom();
  const originInput = mini.document.createElement("input");
  originInput.setAttribute("id", "origin");
  originInput.value = "https://nuc.example.ts.net";
  const settingsSave = mini.document.createElement("button");
  settingsSave.setAttribute("id", "settings-save");
  let storedOrigin = "https://nuc.example.ts.net";
  let runtimeStale = false;
  function syncSettingsSave() {
    const origin = String(originInput.value || "").trim();
    const dirty = origin !== storedOrigin;
    settingsSave.disabled = runtimeStale || !origin || !dirty;
  }
  syncSettingsSave();
  assert.equal(settingsSave.disabled, true, "unchanged stored origin leaves Save disabled");
  originInput.value = "https://other.example.ts.net";
  syncSettingsSave();
  assert.equal(settingsSave.disabled, false, "dirty origin enables Save");
  originInput.value = "   ";
  syncSettingsSave();
  assert.equal(settingsSave.disabled, true, "empty origin disables Save");
  originInput.value = "https://other.example.ts.net";
  runtimeStale = true;
  syncSettingsSave();
  assert.equal(settingsSave.disabled, true, "stale runtime disables Save");
});

test("preview fetch stays UUID-only and picker keeps title when preview is absent", () => {
  assert.equal(companion.TYPES.PREVIEW_FETCH, "preview_fetch");
  assert.equal(
    companion.pathFor("preview", {
      mediaId: "11111111-1111-4111-8111-111111111111",
      locationId: "22222222-2222-4222-8222-222222222222",
    }),
    "/api/media/11111111-1111-4111-8111-111111111111/locations/22222222-2222-4222-8222-222222222222/gallery-preview"
  );
  assert.equal(
    companion.pathFor("preview", { mediaId: "https://evil.example", locationId: "22222222-2222-4222-8222-222222222222" }),
    null
  );
  assert.match(workerSource, /TYPES\.PREVIEW_FETCH/);
  assert.match(workerSource, /pathFor\("preview"/);
  assert.doesNotMatch(workerSource, /fetch\(payload\.url\)/);
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const pickerJs = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  assert.match(pickerHtml, /id="preview-media"/);
  assert.match(pickerJs, /TYPES\.PREVIEW_FETCH/);
  assert.match(pickerJs, /setText\(previewTitle, item\.display_title \|\| item\.media_id\)/);
  assert.match(pickerJs, /if \(!result\.ok \|\| typeof result\.base64 !== "string"/);
  assert.doesNotMatch(pickerJs, /innerHTML/);
  assert.doesNotMatch(pickerJs, /pbs\.twimg\.com/);
});

test("boundTabId binds only from X content-script origins", () => {
  assert.match(workerSource, /function isBindableComposerSender/);
  assert.doesNotMatch(
    workerSource,
    /if \(sender && sender\.tab && typeof sender\.tab\.id === "number"\) \{\s*boundTabId = sender\.tab\.id;/
  );
  const start = workerSource.indexOf("function isBindableComposerSender");
  const bodyStart = workerSource.indexOf("{", start);
  let depth = 0;
  let end = bodyStart;
  for (let index = bodyStart; index < workerSource.length; index += 1) {
    if (workerSource[index] === "{") depth += 1;
    if (workerSource[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = index + 1;
        break;
      }
    }
  }
  const context = { URL };
  vm.createContext(context);
  vm.runInContext(workerSource.slice(start, end), context);
  assert.equal(
    context.isBindableComposerSender({ tab: { id: 12 }, origin: "https://x.com" }),
    true
  );
  assert.equal(
    context.isBindableComposerSender({ tab: { id: 12 }, origin: "https://twitter.com" }),
    true
  );
  assert.equal(
    context.isBindableComposerSender({
      tab: { id: 12 },
      origin: "chrome-extension://omiihmnlkmieaafaphohakcgmbggppap",
    }),
    false
  );
  assert.equal(
    context.isBindableComposerSender({ tab: { id: 12 }, origin: "https://evil.example" }),
    false
  );
  assert.equal(context.isBindableComposerSender({ tab: { id: 12 } }), false);
  assert.equal(context.isBindableComposerSender({ origin: "https://x.com" }), false);
});

function companionStyleArrayRule(source, selectorNeedle) {
  const start = source.indexOf(selectorNeedle);
  assert.ok(start >= 0, selectorNeedle);
  const close = source.indexOf("\n    \"}\"", start);
  assert.ok(close > start, "rule close " + selectorNeedle);
  return source.slice(start, close + "\n    \"}\"".length);
}

function extractNamedFunction(source, name) {
  const start = source.indexOf("function " + name);
  assert.ok(start >= 0, name);
  const bodyStart = source.indexOf("{", start);
  let depth = 0;
  let end = bodyStart;
  for (let index = bodyStart; index < source.length; index += 1) {
    if (source[index] === "{") {
      depth += 1;
    }
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = index + 1;
        break;
      }
    }
  }
  return source.slice(start, end);
}

function fakeAttachPort() {
  const messageListeners = [];
  return {
    posted: [],
    onMessage: {
      addListener(fn) {
        messageListeners.push(fn);
      },
      removeListener(fn) {
        const index = messageListeners.indexOf(fn);
        if (index >= 0) {
          messageListeners.splice(index, 1);
        }
      },
    },
    onDisconnect: {
      addListener() {},
    },
    postMessage(message) {
      this.posted.push(message);
    },
    disconnect() {},
    emit(message) {
      messageListeners.slice().forEach((fn) => fn(message));
    },
  };
}

test("startAttach waits for composer ACK instead of returning ok after transfer", () => {
  const startFn = extractNamedFunction(workerSource, "startAttach");
  const waitFn = extractNamedFunction(workerSource, "waitForPortAttachOutcome");
  assert.match(startFn, /waitForPortAttachOutcome/);
  assert.match(startFn, /armAckTimeout/);
  assert.doesNotMatch(startFn, /await transferAttach\(port, payload\);\s*return \{ ok: true \};/);
  assert.doesNotMatch(startFn, /return \{ ok: true \}/);
  const waitAt = startFn.indexOf("waitForPortAttachOutcome");
  const transferAt = startFn.indexOf("await transferAttach");
  assert.ok(waitAt >= 0 && transferAt >= 0 && waitAt < transferAt);
  assert.match(waitFn, /payload\.attached === true/);
  assert.match(waitFn, /TYPES\.ERROR/);
  assert.match(waitFn, /attach_timeout/);
  assert.match(waitFn, /attach_disconnected/);
  assert.doesNotMatch(waitFn, /fallbackDownload/);
  const transferFn = extractNamedFunction(workerSource, "transferAttach");
  assert.match(transferFn, /return \{ ok: true \}/);
  assert.match(extractNamedFunction(workerSource, "fallbackDownload"), /chrome\.downloads\.download/);
});

test("waitForPortAttachOutcome resolves ACK true and composer_unbound ERROR", async () => {
  const context = { companion, setTimeout, clearTimeout };
  vm.createContext(context);
  vm.runInContext(extractNamedFunction(workerSource, "waitForPortAttachOutcome"), context);
  const ackPort = fakeAttachPort();
  const ackOutcome = context.waitForPortAttachOutcome(ackPort);
  ackPort.emit({
    v: companion.PROTOCOL,
    type: companion.TYPES.ACK,
    payload: { attached: true, bytes: 4 },
  });
  const acked = await ackOutcome.promise;
  assert.equal(acked.ok, true);

  const errPort = fakeAttachPort();
  const errOutcome = context.waitForPortAttachOutcome(errPort);
  errPort.emit({
    v: companion.PROTOCOL,
    type: companion.TYPES.ERROR,
    payload: { error: "composer_unbound" },
  });
  const unbound = await errOutcome.promise;
  assert.equal(unbound.ok, false);
  assert.equal(unbound.error, "composer_unbound");

  const failPort = fakeAttachPort();
  const failOutcome = context.waitForPortAttachOutcome(failPort);
  const failed = await failOutcome.fail("too_large_or_invalid");
  assert.equal(failed.ok, false);
  assert.equal(failed.error, "too_large_or_invalid");
});

test("port attach resolves a focused Post your reply file input without the + click", () => {
  const completeFn = extractNamedFunction(adapterSource, "completeAttachTransfer");
  const focusFn = extractNamedFunction(adapterSource, "onComposerFocusIn");
  const bindFn = extractNamedFunction(adapterSource, "bindComposerIfLive");
  const clickWriter = adapterSource.match(
    /button\.addEventListener\(type, \(event\) => \{[\s\S]*?boundComposer = \{ root: composerRoot, fileInput \};/
  );
  assert.ok(clickWriter, "in-page + still binds on click");
  assert.match(focusFn, /bindComposerIfLive\(editable\)/);
  assert.match(bindFn, /boundComposer = \{ root: composerRoot, fileInput \}/);
  assert.match(completeFn, /resolveLiveComposerFileInput/);
  assert.match(completeFn, /composer_unbound/);
  assert.doesNotMatch(completeFn, /fallbackDownload/);
  assert.doesNotMatch(adapterSource, /chrome\.downloads/);
  assert.doesNotMatch(adapterSource, /\bfetch\s*\(/);

  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const file = el(dom, "input", { type: "file", accept: "image/*,video/mp4" });
  const chrome = el(dom, "div", {}, [editable, toolbar, file]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  hooks.injectAttach(editable);
  assert.equal(hooks.resolveLiveComposerFileInput(), null);
  dom.document.activeElement = editable;
  assert.equal(hooks.resolveLiveComposerFileInput(), file);
  const port = fakeAttachPort();
  const result = hooks.completeAttachTransfer(port, "framenest-media.bin", "image/jpeg", [], 0);
  assert.equal(result.ok, true);
  assert.equal(file.files.length, 1);
  assert.equal(file.files[0].name, "framenest-media.bin");
  assert.equal(port.posted[0].type, companion.TYPES.ACK);
  assert.equal(port.posted[0].payload.attached, true);
});

test("focus binds the reply composer and detached input falls back to the live focused file input", () => {
  const dom = createMiniDom();
  const editable = el(dom, "div", {
    "data-testid": "tweetTextarea_0",
    contenteditable: "true",
    "aria-label": "Post your reply",
  });
  const toolbar = el(dom, "div", { "data-testid": "toolBar", role: "toolbar" });
  const firstFile = el(dom, "input", { type: "file", accept: "image/*,video/mp4" });
  const chrome = el(dom, "div", {}, [editable, toolbar, firstFile]);
  dom.body.appendChild(chrome);
  const hooks = loadAdapterHooks(dom);
  hooks.onComposerFocusIn({ target: editable });
  assert.equal(hooks.resolveLiveComposerFileInput(), firstFile);
  chrome.removeChild(firstFile);
  assert.equal(dom.document.contains(firstFile), false);
  assert.equal(hooks.resolveLiveComposerFileInput(), null);
  const secondFile = el(dom, "input", { type: "file", accept: "image/*,video/mp4" });
  chrome.appendChild(secondFile);
  dom.document.activeElement = editable;
  assert.equal(hooks.resolveLiveComposerFileInput(), secondFile);
});

test("unbound live file input posts composer_unbound without a download fallback", () => {
  const completeFn = extractNamedFunction(adapterSource, "completeAttachTransfer");
  assert.doesNotMatch(completeFn, /fallbackDownload/);
  assert.doesNotMatch(completeFn, /downloads/);
  const pickerHtml = fs.readFileSync(path.join(REPO, "extension/ui/picker.html"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  assert.doesNotMatch(pickerHtml, /id="settings-dialog"/);
  assert.doesNotMatch(pickerHtml, /<dialog/);
  assert.match(saveJs, /TYPES\.SAVE_POST/);
  assert.match(saveJs, /framenest-save-popup/);
  assert.doesNotMatch(saveJs, /completeAttachTransfer/);
  assert.doesNotMatch(saveJs, /waitForPortAttachOutcome/);
  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  const port = fakeAttachPort();
  const result = hooks.completeAttachTransfer(port, "framenest-media.bin", "image/jpeg", [], 0);
  assert.equal(result.ok, false);
  assert.equal(result.error, "composer_unbound");
  assert.equal(port.posted[0].type, companion.TYPES.ERROR);
  assert.equal(port.posted[0].payload.error, "composer_unbound");
});

test("Save popup is an Edit-media subset without radios, source, or on-open focus", () => {
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  const saveCss = fs.readFileSync(path.join(REPO, "extension/ui/save.css"), "utf8");
  assert.doesNotMatch(saveHtml, /category-fieldset/);
  assert.doesNotMatch(saveHtml, /name="category"/);
  assert.doesNotMatch(saveHtml, /<legend[^>]*>Category</);
  assert.doesNotMatch(saveHtml, /value="general"/);
  assert.doesNotMatch(saveHtml, /value="meme"/);
  assert.doesNotMatch(saveHtml, /value="movie"/);
  assert.doesNotMatch(saveHtml, /value="youtube"/);
  assert.doesNotMatch(saveHtml, /> X</);
  assert.doesNotMatch(saveHtml, /> General</);
  assert.doesNotMatch(saveHtml, /> Meme</);
  assert.doesNotMatch(saveHtml, /> Movie</);
  assert.doesNotMatch(saveHtml, /> YouTube</);
  assert.doesNotMatch(saveHtml, /Analyze/);
  assert.doesNotMatch(saveHtml, /id="source"/);
  assert.doesNotMatch(
    saveHtml,
    /Category describes the content and applies to every media item in this post/
  );
  assert.match(saveHtml, /id="title"[\s\S]*id="tag-search"[\s\S]*id="description"/);
  assert.doesNotMatch(saveJs, /contentCategory/);
  assert.doesNotMatch(saveJs, /defaultContentCategoryForMediaKind/);
  assert.doesNotMatch(saveJs, /focusCheckedCategory/);
  assert.doesNotMatch(saveJs, /focus-category/);
  assert.match(saveJs, /action === "prefill"/);
  assert.match(saveJs, /source !== "framenest-save-host"/);
  assert.match(saveJs, /applyPrefill\(data\)/);
  assert.match(saveJs, /action === "submit"/);
  assert.match(saveJs, /action === "submit"[\s\S]{0,80}submitSave\(\)/);
  assert.doesNotMatch(saveJs, /descriptionHeight/);
  assert.match(saveJs, /action: "size"/);
  assert.doesNotMatch(saveJs, /title\.focus/);
  assert.doesNotMatch(saveJs, /description\.focus/);
  assert.doesNotMatch(saveJs, /tagSearch\.focus/);
  assert.doesNotMatch(saveHtml, /autofocus/);
  assert.match(saveHtml, /\sid="title"[^>]*tabindex="-1"/);
  assert.match(saveHtml, /\sid="description"[^>]*tabindex="-1"/);
  assert.match(saveHtml, /id="tag-search"[\s\S]*?tabindex="-1"/);
  assert.doesNotMatch(saveJs, /pointerdown/);
  assert.doesNotMatch(saveJs, /overlayArmed/);
  assert.doesNotMatch(saveJs, /armOverlayFocus/);
  assert.match(saveJs, /notifyParent\("ready"\)/);
  assert.match(saveJs, /title\.addEventListener\("keydown"[\s\S]*Enter[\s\S]*submitSave\(\)/);
  assert.doesNotMatch(saveJs, /cycleCategory/);
  assert.doesNotMatch(saveJs, /categoryRadios/);
  assert.doesNotMatch(saveJs, /description\.addEventListener\("keydown"/);
  assert.match(saveJs, /event\.ctrlKey \|\| event\.metaKey/);
  assert.match(saveJs, /target === description && !event\.ctrlKey && !event\.metaKey/);
  assert.match(saveJs, /target === tagSearch && tagListOpen\(\) && activeSuggestion >= 0/);
  assert.match(saveJs, /tagListOpen\(\)/);
  assert.match(saveJs, /aria-busy/);
  assert.match(
    saveJs,
    new RegExp(`${BRAND} needs an update before this Save can complete\\.`)
  );
  assert.doesNotMatch(saveJs, /content_category/);
  assert.doesNotMatch(saveJs, /contentCategory:\s*null/);
  assert.doesNotMatch(saveCss, /\.category /);
  assert.doesNotMatch(saveCss, /accent-color: var\(--accent\)/);
  assert.match(saveCss, /textarea \{[\s\S]*?height:\s*120px/);
  assert.match(saveCss, /textarea \{[\s\S]*?min-height:\s*120px/);
  assert.match(saveCss, /textarea \{[\s\S]*?overflow-y:\s*auto/);
  assert.match(saveCss, /textarea \{[\s\S]*?resize:\s*none/);
  assert.doesNotMatch(saveCss, /max-height:\s*320px/);
  assert.match(saveCss, /\.fields \{[\s\S]*?flex:\s*0 0 auto/);
  assert.doesNotMatch(saveCss, /html,\s*body \{\s*height:\s*100%/);
  assert.match(saveCss, /html,\s*\nbody \{[\s\S]*?overflow:\s*hidden/);
  assert.match(saveCss, /\.header \{[\s\S]*?padding:\s*6px 10px 6px 12px/);
  assert.doesNotMatch(saveCss, /padding:\s*10px 10px 8px 12px/);
  assert.match(saveCss, /#save \{[\s\S]*background:\s*var\(--accent\)/);
  assert.equal(companion.savePopupDefaultContentCategory(), "general");
  assert.equal(companion.defaultContentCategoryForMediaKind("image"), "general");
  assert.equal(companion.defaultContentCategoryForMediaKind("video"), "general");
  assert.equal(companion.defaultContentCategoryForMediaKind("unknown"), "general");
});

test("Save live UX keeps + inset, hides Edit image, and searches canonical tags only", () => {
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  const saveCss = fs.readFileSync(path.join(REPO, "extension/ui/save.css"), "utf8");
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  assert.match(saveHtml, /placeholder="Search tags"/);
  assert.doesNotMatch(saveHtml, /Search or add a tag/);
  assert.doesNotMatch(saveJs, /Search or add a tag/);
  assert.doesNotMatch(saveJs, /createAndSelectMetadataTag/);
  assert.doesNotMatch(saveJs, /Add \u201c/);
  assert.doesNotMatch(saveJs, /Add "/);
  assert.match(saveHtml, /id="tag-search"/);
  assert.match(saveHtml, /name="fn-canonical-tag-query"/);
  assert.doesNotMatch(saveHtml, /name="tag-search"/);
  assert.doesNotMatch(saveHtml, /name="(?:name|given-name|family-name|sex|username)"/);
  assert.match(saveHtml, /id="tag-search"[\s\S]*?autocomplete="off"/);
  assert.match(saveCss, /\.tag-search-panel \{[\s\S]*?position:\s*relative/);
  assert.match(saveCss, /\.tag-suggestions \{[\s\S]*?position:\s*absolute/);
  assert.match(saveCss, /\.tag-suggestions \{[\s\S]*?flex-shrink:\s*0/);
  assert.match(saveCss, /\.tag-suggestions \{[\s\S]*?min-height:\s*2\.25rem/);
  assert.match(saveCss, /\.tag-suggestions \{[\s\S]*?padding:\s*4px/);
  assert.match(saveCss, /\.tag-suggestion \{[\s\S]*?min-height:\s*2\.25rem/);
  assert.match(saveCss, /\.tag-suggestion\[aria-selected="true"\]/);
  assert.match(saveCss, /\.tag-chip__remove \{[\s\S]*?border-radius:\s*50%/);
  assert.match(saveCss, /\.tag-chip__remove \{[\s\S]*?background:\s*var\(--accent\)/);
  assert.match(saveJs, /textContent = "×"/);
  assert.doesNotMatch(saveJs, /remove\.textContent = "X"/);
  assert.match(saveJs, /event\.key === "Enter"[\s\S]*tagListOpen\(\)[\s\S]*activeSuggestion/);
  assert.match(saveJs, /addTag\(item\)/);
  assert.match(saveJs, /submitSave\(\)/);
  assert.doesNotMatch(saveJs, /innerHTML/);
  const attachRule = adapterSource.match(
    /\[data-framenest-companion='attach'\] \{[\s\S]*?\n\}/
  );
  assert.ok(attachRule);
  assert.match(attachRule[0], /position:\s*fixed/);
  const dom = createMiniDom();
  const post = el(dom, "article", { "data-testid": "tweet" });
  post.appendChild(
    el(dom, "a", { href: "https://x.com/fixture/status/123456789", role: "link" })
  );
  const tweetText = el(dom, "div", { "data-testid": "tweetText" });
  tweetText.textContent = "First line of the post\nSecond line stays in description";
  post.appendChild(tweetText);
  const photo = el(dom, "div", { "data-testid": "tweetPhoto", "data-framenest-media": "" });
  photo.appendChild(el(dom, "img", { alt: "Cardano founder still" }));
  const edit = el(dom, "button", { "aria-label": "Edit image", title: "Edit image" });
  const overlayEdit = el(dom, "button", { "aria-label": "Edit image", title: "Edit image" });
  const profile = el(dom, "button", { "aria-label": "Edit profile" });
  photo.appendChild(edit);
  post.appendChild(photo);
  post.appendChild(overlayEdit);
  post.appendChild(profile);
  dom.body.appendChild(post);
  const hooks = loadAdapterHooks(dom);
  assert.equal(hooks.injectSave(post), "placed");
  assert.equal(edit.style.display, "none");
  assert.equal(overlayEdit.style.display, "none");
  assert.notEqual(edit.style.left, "8px");
  assert.equal(profile.style.display, undefined);
  assert.equal(profile.style.left, undefined);
  const save = photo.querySelector("[data-framenest-companion='save']");
  assert.ok(save);
  assert.equal(save.getAttribute("aria-label"), SAVE_TO_BRAND);
  const prefill = hooks.postTextPrefillFrom(post, photo);
  assert.equal(prefill.description, "First line of the post\nSecond line stays in description");
  assert.equal(prefill.title, "Cardano founder still");
  assert.equal(prefill.descriptionHeight, undefined);
  const emptyPost = el(dom, "article", { "data-testid": "tweet" });
  const emptyPhoto = el(dom, "div", { "data-testid": "tweetPhoto", "data-framenest-media": "" });
  emptyPhoto.appendChild(el(dom, "img", { alt: "Fallback tile alt" }));
  emptyPost.appendChild(emptyPhoto);
  const emptyPrefill = hooks.postTextPrefillFrom(emptyPost, emptyPhoto);
  assert.equal(emptyPrefill.description, "");
  assert.equal(emptyPrefill.title, "Fallback tile alt");
});

test("Save prefill canonicalizes synthetic multiline alt into a single-line title", () => {
  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  const post = el(dom, "article", { "data-testid": "tweet" });
  const photo = el(dom, "div", { "data-testid": "tweetPhoto" });
  photo.appendChild(el(dom, "img", { alt: "A two\nline alt" }));
  post.appendChild(photo);
  const prefill = hooks.postTextPrefillFrom(post, photo);
  assert.equal(prefill.title, "A two line alt");
  assert.equal(prefill.description, "");
});

test("Save prefill prefers non-generic alt, clamps height, and keeps hidden DOM text", () => {
  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  const genericPost = el(dom, "article", { "data-testid": "tweet" });
  const tweetText = el(dom, "div", { "data-testid": "tweetText" });
  tweetText.textContent = "Useful tweet sentence about the clip.";
  tweetText.innerText = "Useful tweet sentence";
  const genericPhoto = el(dom, "div", { "data-testid": "tweetPhoto" });
  genericPhoto.appendChild(el(dom, "img", { alt: "Image 1 of 4" }));
  genericPost.appendChild(tweetText);
  genericPost.appendChild(genericPhoto);
  const genericPrefill = hooks.postTextPrefillFrom(genericPost, genericPhoto);
  assert.equal(genericPrefill.title, "Useful tweet sentence about the clip");
  assert.equal(genericPrefill.description, "Useful tweet sentence about the clip.");
  assert.equal(genericPrefill.descriptionHeight, undefined);

  const videoPost = el(dom, "article", { "data-testid": "tweet" });
  const videoHost = el(dom, "div", { "data-testid": "videoPlayer" });
  videoHost.setAttribute("aria-label", "Embedded video");
  const video = el(dom, "video", { "aria-label": "Founder clip" });
  videoHost.appendChild(video);
  videoPost.appendChild(videoHost);
  const videoPrefill = hooks.postTextPrefillFrom(videoPost, videoHost);
  assert.equal(videoPrefill.title, "Founder clip");

  const blankPost = el(dom, "article", { "data-testid": "tweet" });
  const blankHost = el(dom, "div", { "data-testid": "tweetPhoto" });
  blankHost.appendChild(el(dom, "img", { alt: "Photo" }));
  blankPost.appendChild(blankHost);
  const blankPrefill = hooks.postTextPrefillFrom(blankPost, blankHost);
  assert.equal(blankPrefill.title, "");
  assert.equal(blankPrefill.description, "");
  assert.equal(blankPrefill.descriptionHeight, undefined);
});

test("Save title ignores companion plus inside the photo host", () => {
  assert.match(extractNamedFunction(adapterSource, "isCompanionChrome"), /data-framenest-companion/);
  assert.match(extractNamedFunction(adapterSource, "firstNonGenericName"), /isReservedSaveControlName/);
  assert.match(extractNamedFunction(adapterSource, "accessibleNameFrom"), /isCompanionChrome/);
  assert.match(extractNamedFunction(adapterSource, "positionSavePopup"), /contentHeight/);
  assert.match(extractNamedFunction(adapterSource, "positionSavePopup"), /SAVE_FRAME_BORDER_Y/);
  assert.match(extractNamedFunction(adapterSource, "openSavePopup"), /action === "size"/);
  assert.doesNotMatch(adapterSource, /tweetHeightFrom/);
  const dom = createMiniDom();
  const hooks = loadAdapterHooks(dom);
  const post = el(dom, "article", { "data-testid": "tweet" });
  const tweetText = el(dom, "div", { "data-testid": "tweetText" });
  tweetText.textContent = "Useful tweet sentence about the photo.";
  post.appendChild(tweetText);
  const photo = el(dom, "div", { "data-testid": "tweetPhoto" });
  photo.appendChild(el(dom, "img", { alt: "Image" }));
  photo.appendChild(
    el(dom, "button", {
      "data-framenest-companion": "save",
      "aria-label": SAVE_TO_BRAND,
    })
  );
  post.appendChild(photo);
  const prefill = hooks.postTextPrefillFrom(post, photo);
  assert.equal(prefill.title, "Useful tweet sentence about the photo");
  assert.notEqual(prefill.title, SAVE_TO_BRAND);
  assert.equal(prefill.description, "Useful tweet sentence about the photo.");

  const emptyPost = el(dom, "article", { "data-testid": "tweet" });
  const emptyPhoto = el(dom, "div", { "data-testid": "tweetPhoto" });
  emptyPhoto.appendChild(el(dom, "img", { alt: "Image" }));
  emptyPhoto.appendChild(
    el(dom, "button", {
      "data-framenest-companion": "save",
      "aria-label": SAVE_TO_BRAND,
    })
  );
  emptyPost.appendChild(emptyPhoto);
  const emptyPrefill = hooks.postTextPrefillFrom(emptyPost, emptyPhoto);
  assert.equal(emptyPrefill.title, "");
});

test("service worker posts alias without content_category and treats catalog_removed as terminal", () => {
  const savePostFn = extractNamedFunction(workerSource, "savePost");
  const terminalFn = extractNamedFunction(workerSource, "isTerminal");
  const persistFn = extractNamedFunction(workerSource, "persistInflight");
  assert.doesNotMatch(savePostFn, /acceptContentCategory\(payload\.contentCategory\)/);
  assert.doesNotMatch(savePostFn, /content_category/);
  assert.doesNotMatch(savePostFn, /invalid_category/);
  assert.match(savePostFn, /alias: alias/);
  assert.match(savePostFn, /ambiguous: true/);
  assert.doesNotMatch(savePostFn, /content_category:\s*null/);
  assert.doesNotMatch(savePostFn, /delete .*\.content_category/);
  assert.match(terminalFn, /catalog_removed/);
  assert.doesNotMatch(terminalFn, /failure_code/);
  assert.doesNotMatch(terminalFn, /X_NO_SUPPORTED_MEDIA/);
  assert.match(persistFn, /postId/);
  assert.match(extractNamedFunction(workerSource, "claimSnapshot"), /submissionResult/);
});

test("content-category helper and save-outcome reducer cover every terminal", () => {
  assert.deepEqual(companion.CONTENT_CATEGORIES, ["general", "meme", "movie", "youtube"]);
  assert.equal(companion.acceptContentCategory("general"), "general");
  assert.equal(companion.acceptContentCategory("meme"), "meme");
  assert.equal(companion.acceptContentCategory("movie"), "movie");
  assert.equal(companion.acceptContentCategory("youtube"), "youtube");
  assert.equal(companion.acceptContentCategory("General"), null);
  assert.equal(companion.acceptContentCategory("other"), null);
  assert.equal(companion.defaultContentCategoryForMediaKind("image"), "general");
  assert.equal(companion.defaultContentCategoryForMediaKind("video"), "general");
  assert.equal(companion.defaultContentCategoryForMediaKind("unknown"), "general");
  assert.equal(companion.reduceXSaveOutcome({ ok: true }).kind, "busy");
  assert.equal(companion.reduceXSaveOutcome({ ok: true }).name, `Saving to ${BRAND}…`);
  assert.equal(
    companion.reduceXSaveOutcome({ ok: true, state: "completed" }).name,
    `Saved to ${BRAND}`
  );
  assert.equal(
    companion.reduceXSaveOutcome({ ok: true, submissionResult: "reuse" }).name,
    `Already saved to ${BRAND}`
  );
  assert.equal(
    companion.reduceXSaveOutcome({ ok: true, state: "duplicate_resolved" }).name,
    `Already saved to ${BRAND}`
  );
  assert.equal(
    companion.reduceXSaveOutcome({
      ok: true,
      state: "completed_partial",
      successCount: 1,
      discoveredAssetCount: 3,
    }).name,
    `Partially saved to ${BRAND} (1 of 3)`
  );
  const failed = companion.reduceXSaveOutcome({ ok: true, state: "failed", terminal: true });
  assert.equal(failed.kind, "failed");
  assert.equal(failed.name, `${SAVE_TO_BRAND} failed`);
  assert.equal(
    companion.reduceXSaveOutcome({ ok: true, state: "catalog_removed", terminal: true }).name,
    `Saved item is no longer available in ${BRAND}`
  );
  const unknown = companion.reduceXSaveOutcome({ ok: false, error: "network_failed" });
  assert.equal(unknown.kind, "unknown");
  assert.equal(unknown.name, `Save status unknown—check ${BRAND}`);
  assert.equal(unknown.retainInflight, true);
  assert.equal(
    companion.reduceXSaveOutcome({ ok: false, error: "X_REQUEST_INVALID_CATEGORY" }).name,
    `${SAVE_TO_BRAND} failed—${BRAND} needs an update`
  );
  const closed = companion.reduceXSaveOutcome({ ok: true, terminal: true, state: "unexpected" });
  assert.equal(closed.kind, "unknown");
});

test("failed Save never paints the completed Saved to brand copy and mirrors every tile on the post", () => {
  const CLAIM = "11111111-1111-4111-8111-111111111111";
  const POST_ID = "123456789";
  const replies = [];
  const dom = createMiniDom();
  const post = el(dom, "article", { "data-testid": "tweet" });
  const link = el(dom, "a", {
    href: "https://x.com/fixture/status/" + POST_ID,
    role: "link",
  });
  const photo = el(dom, "div", { "data-testid": "tweetPhoto", "data-framenest-media": "" });
  const video = el(dom, "div", { "data-testid": "videoPlayer", "data-framenest-media": "" });
  post.appendChild(link);
  post.appendChild(photo);
  post.appendChild(video);
  dom.body.appendChild(post);
  const hooks = loadAdapterHooks(dom, {
    sendMessage(message, callback) {
      replies.push(message);
      if (message.type === companion.TYPES.POLL_CLAIM) {
        callback({
          ok: true,
          claimId: CLAIM,
          postId: POST_ID,
          state: "failed",
          terminal: true,
        });
        return;
      }
      callback({ ok: false, error: "empty_response" });
    },
  });
  assert.equal(hooks.injectSave(post), "placed");
  const placed = post.querySelectorAll("[data-framenest-companion='save']");
  assert.equal(placed.length, 2);
  const first = placed[0];
  const second = placed[1];
  assert.equal(first.getAttribute("data-framenest-post-id"), POST_ID);
  assert.equal(second.getAttribute("data-framenest-post-id"), POST_ID);
  hooks.applySaveResult(first, {
    ok: true,
    claimId: CLAIM,
    postId: POST_ID,
    state: "failed",
    terminal: true,
  });
  assert.equal(first.getAttribute("aria-label"), `${SAVE_TO_BRAND} failed`);
  assert.equal(second.getAttribute("aria-label"), `${SAVE_TO_BRAND} failed`);
  assert.equal(first.getAttribute("data-framenest-save-kind"), "failed");
  assert.equal(second.getAttribute("data-framenest-save-kind"), "failed");
  assert.equal(first.getAttribute("aria-label").includes(`Saved to ${BRAND}`), false);
  const failedIcon = hooks.saveIconSvg("failed");
  assert.ok(pathDataFrom(failedIcon).includes("M12 6.5v11M6.5 12h11"));
  assert.equal(
    pathDataFrom(failedIcon).some((d) => d.includes("M6.5 12.25l3.6 3.5")),
    false
  );
});

test("catalog_removed, reuse, partial, and unknown outcomes paint distinct copy", async () => {
  const POST_ID = "987654321";
  const CLAIM = "22222222-2222-4222-8222-222222222222";
  const dom = createMiniDom();
  const post = el(dom, "article", { "data-testid": "tweet" });
  post.appendChild(
    el(dom, "a", { href: "https://x.com/fixture/status/" + POST_ID, role: "link" })
  );
  const photo = el(dom, "div", { "data-testid": "tweetPhoto" });
  post.appendChild(photo);
  dom.body.appendChild(post);
  let pollState = "inspecting";
  const hooks = loadAdapterHooks(dom, {
    sendMessage(message, callback) {
      if (message.type === companion.TYPES.POLL_CLAIM) {
        callback({
          ok: true,
          claimId: CLAIM,
          postId: POST_ID,
          state: pollState,
          terminal: pollState !== "inspecting",
          successCount: 1,
          discoveredAssetCount: 2,
        });
        return;
      }
      if (message.type === companion.TYPES.RECOVER_INFLIGHT) {
        callback({
          ok: true,
          claimIds: [CLAIM],
          claims: [{ claimId: CLAIM, postId: POST_ID }],
        });
        return;
      }
      callback({ ok: false, error: "empty_response" });
    },
  });
  hooks.injectSave(post);
  const button = photo.querySelector("[data-framenest-companion='save']");
  hooks.applySaveResult(button, { ok: true, submissionResult: "reuse", state: "completed" });
  assert.equal(button.getAttribute("aria-label"), `Already saved to ${BRAND}`);
  hooks.applySaveResult(button, {
    ok: true,
    state: "completed_partial",
    successCount: 1,
    discoveredAssetCount: 2,
  });
  assert.equal(button.getAttribute("aria-label"), `Partially saved to ${BRAND} (1 of 2)`);
  hooks.applySaveResult(button, { ok: true, state: "catalog_removed", terminal: true });
  assert.equal(
    button.getAttribute("aria-label"),
    `Saved item is no longer available in ${BRAND}`
  );
  hooks.applySaveResult(button, { ok: false, error: "network_failed", ambiguous: true });
  assert.equal(button.getAttribute("aria-label"), `Save status unknown—check ${BRAND}`);
  hooks.applySaveResult(button, {
    ok: true,
    claimId: CLAIM,
    postId: POST_ID,
    state: "inspecting",
  });
  assert.equal(button.getAttribute("aria-label"), `Saving to ${BRAND}…`);
  assert.equal(button.getAttribute("aria-busy"), "true");
  pollState = "catalog_removed";
  await hooks.pollClaim(CLAIM, POST_ID, null, 0);
  assert.equal(
    button.getAttribute("aria-label"),
    `Saved item is no longer available in ${BRAND}`
  );
  await hooks.recover();
  assert.equal(button.getAttribute("aria-label"), `Saved item is no longer available in ${BRAND}`);
});

test("Escape restores focus to the initiating Save control", () => {
  assert.match(extractNamedFunction(adapterSource, "closeSavePopup"), /button\.focus\(\)/);
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  assert.match(saveJs, /tagListOpen\(\)[\s\S]*closeTagList\(\)[\s\S]*notifyParent\("cancel"\)/);
});

const COMPANION_X_DISPLAY = "\u{1D54F}";

function createSaveNode(tagName, attrs) {
  const attributes = Object.assign({}, attrs || {});
  const node = {
    tagName: String(tagName).toUpperCase(),
    attributes,
    childNodes: [],
    parentNode: null,
    disabled: false,
    value: attributes.value || "",
    type: attributes.type || "",
    className: attributes.class || attributes.className || "",
    maxLength: attributes.maxlength ? Number(attributes.maxlength) : undefined,
    style: {},
    listeners: {},
    ownerDocument: null,
    get children() {
      return this.childNodes.filter((child) => child && child.tagName);
    },
    get textContent() {
      if (this._text != null && !this.childNodes.length) {
        return this._text;
      }
      return this.childNodes
        .map((child) => (typeof child === "string" ? child : child.textContent || ""))
        .join("");
    },
    set textContent(value) {
      this.childNodes = [];
      this._text = value == null ? "" : String(value);
    },
    getAttribute(name) {
      if (!Object.prototype.hasOwnProperty.call(this.attributes, name)) {
        return null;
      }
      return this.attributes[name];
    },
    setAttribute(name, value) {
      this.attributes[name] = String(value);
      if (name === "class") {
        this.className = String(value);
      }
      if (name === "id") {
        this.id = String(value);
      }
    },
    removeAttribute(name) {
      delete this.attributes[name];
    },
    getBoundingClientRect() {
      return { top: 0, left: 0, right: 120, bottom: 80, width: 120, height: 80, x: 0, y: 0 };
    },
    appendChild(child) {
      if (child.parentNode && child.parentNode.removeChild) {
        child.parentNode.removeChild(child);
      }
      child.parentNode = this;
      child.ownerDocument = this.ownerDocument;
      this.childNodes.push(child);
      return child;
    },
    removeChild(child) {
      const index = this.childNodes.indexOf(child);
      if (index >= 0) {
        this.childNodes.splice(index, 1);
      }
      child.parentNode = null;
      return child;
    },
    replaceChildren() {
      this.childNodes.slice().forEach((child) => this.removeChild(child));
      Array.prototype.slice.call(arguments).forEach((child) => this.appendChild(child));
    },
    addEventListener(type, handler) {
      if (!this.listeners[type]) {
        this.listeners[type] = [];
      }
      this.listeners[type].push(handler);
    },
    dispatchEvent(event) {
      const handlers = this.listeners[event.type] || [];
      handlers.forEach((handler) => handler(event));
      if (this.parentNode && event.bubbles && !event._stopped) {
        this.parentNode.dispatchEvent(event);
      }
      return true;
    },
    click() {
      const event = createSaveEvent("click");
      this.dispatchEvent(event);
      if (this.type === "submit" && this.parentNode) {
        let form = this.parentNode;
        while (form && form.tagName !== "FORM") {
          form = form.parentNode;
        }
        if (form) {
          form.dispatchEvent(createSaveEvent("submit", { cancelable: true }));
        }
      }
    },
  };
  if (attributes.id) {
    node.id = attributes.id;
  }
  node.offsetHeight = 80;
  return node;
}

function createSaveEvent(type, init) {
  return {
    type,
    bubbles: Boolean(init && init.bubbles),
    cancelable: Boolean(init && init.cancelable),
    key: init && init.key,
    ctrlKey: Boolean(init && init.ctrlKey),
    metaKey: Boolean(init && init.metaKey),
    target: null,
    preventDefault() {},
  };
}

function visitSave(node, fn) {
  fn(node);
  (node.childNodes || []).forEach((child) => {
    if (child && child.tagName) {
      visitSave(child, fn);
    }
  });
}

async function loadSaveOverlay(options) {
  const tags = (options && options.tags) || [];
  const root = createSaveNode("html");
  const body = createSaveNode("body");
  const form = createSaveNode("form", { id: "save-form" });
  const title = createSaveNode("input", { id: "title" });
  const tagSearch = createSaveNode("input", { id: "tag-search" });
  const suggestions = createSaveNode("div", { id: "tag-suggestions" });
  const selectedTags = createSaveNode("div", { id: "selected-tags" });
  const tagsStatus = createSaveNode("p", { id: "tags-status" });
  const description = createSaveNode("textarea", { id: "description" });
  const formStatus = createSaveNode("p", { id: "form-status" });
  const closeButton = createSaveNode("button", { id: "close", type: "button" });
  const saveButton = createSaveNode("button", { id: "save", type: "submit" });
  form.appendChild(title);
  form.appendChild(tagSearch);
  form.appendChild(suggestions);
  form.appendChild(selectedTags);
  form.appendChild(tagsStatus);
  form.appendChild(description);
  form.appendChild(formStatus);
  form.appendChild(closeButton);
  form.appendChild(saveButton);
  body.appendChild(form);
  root.appendChild(body);
  const documentRef = {
    body,
    documentElement: root,
    getElementById(id) {
      let found = null;
      visitSave(root, (node) => {
        if (node.id === id || node.getAttribute("id") === id) {
          found = node;
        }
      });
      return found;
    },
    createElement(tag) {
      const node = createSaveNode(tag);
      node.ownerDocument = documentRef;
      return node;
    },
    addEventListener(type, handler) {
      if (!this.listeners) {
        this.listeners = {};
      }
      if (!this.listeners[type]) {
        this.listeners[type] = [];
      }
      this.listeners[type].push(handler);
    },
  };
  root.ownerDocument = documentRef;
  body.ownerDocument = documentRef;
  visitSave(root, (node) => {
    node.ownerDocument = documentRef;
  });
  const saves = [];
  const sandbox = {
    FrameNestCompanion: companion,
    document: documentRef,
    window: {
      location: { hash: "#url=" + encodeURIComponent("https://x.com/fixture/status/123456789") },
      parent: { postMessage() {} },
      addEventListener() {},
      requestAnimationFrame(callback) {
        callback();
        return 1;
      },
    },
    chrome: {
      runtime: {
        id: "save-overlay-test",
        lastError: null,
        sendMessage(message, callback) {
          if (message.type === companion.TYPES.CANONICAL_TAGS) {
            callback({ ok: true, body: { tags: tags } });
            return;
          }
          if (message.type === companion.TYPES.SAVE_POST) {
            saves.push(message.payload);
            callback({ ok: true, body: { claim_id: "claim" } });
          }
        },
      },
    },
    URLSearchParams,
    encodeURIComponent,
    Array,
    String,
    Boolean,
    Number,
    Object,
    Promise,
    setTimeout,
    clearTimeout,
  };
  sandbox.globalThis = sandbox;
  sandbox.window.document = documentRef;
  const saveSource = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  vm.runInNewContext(saveSource, sandbox, { filename: "save.js" });
  await new Promise((resolve) => setImmediate(resolve));
  await new Promise((resolve) => setImmediate(resolve));
  return {
    selectedTags,
    tagSearch,
    suggestions,
    form,
    saves,
    chipLabels() {
      return selectedTags.children.map((chip) => {
        const label = chip.children[0];
        return label ? label.textContent : chip.textContent;
      });
    },
    removeFirstChip() {
      const chip = selectedTags.children[0];
      assert.ok(chip);
      const remove = chip.children.find((child) => child.className === "tag-chip__remove");
      assert.ok(remove);
      remove.click();
    },
    addSuggestion(key) {
      tagSearch.value = key;
      tagSearch.dispatchEvent(createSaveEvent("input"));
      const match = suggestions.children.find((child) => child.textContent && child.textContent.length);
      assert.ok(match, "expected a tag suggestion");
      match.click();
    },
    submit() {
      form.dispatchEvent(createSaveEvent("submit", { cancelable: true }));
    },
  };
}

test("Save overlay pathFor uses companion seed surface and preselects X once", async () => {
  const saveJs = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
  const saveHtml = fs.readFileSync(path.join(REPO, "extension/ui/save.html"), "utf8");
  assert.equal(
    companion.pathFor("canonicalTags"),
    "/api/canonical-tags?surface=x-companion-save"
  );
  assert.doesNotMatch(saveJs, /youtube/i);
  assert.doesNotMatch(saveHtml, /youtube/i);
  assert.match(saveHtml, /id="title"[\s\S]*id="tag-search"[\s\S]*id="description"/);
  assert.doesNotMatch(saveHtml, /name="category"/);
  assert.doesNotMatch(saveHtml, /Analyze/);
  assert.doesNotMatch(saveJs, /TYPES\.[A-Z_]*ANALY/);

  const seededTags = [
    { key: "meme", display_name: "Meme" },
    { key: "x", display_name: COMPANION_X_DISPLAY },
  ];
  const present = await loadSaveOverlay({ tags: seededTags });
  assert.deepEqual(present.chipLabels(), [COMPANION_X_DISPLAY]);
  present.submit();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(present.saves.length, 1);
  assert.deepEqual(Array.from(present.saves[0].alias.tag_keys), ["x"]);

  const deselected = await loadSaveOverlay({ tags: seededTags });
  deselected.removeFirstChip();
  assert.deepEqual(deselected.chipLabels(), []);
  deselected.submit();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(deselected.saves.length, 1);
  assert.equal(deselected.saves[0].alias && deselected.saves[0].alias.tag_keys, undefined);

  const readded = await loadSaveOverlay({ tags: seededTags });
  readded.removeFirstChip();
  readded.addSuggestion("x");
  assert.deepEqual(readded.chipLabels(), [COMPANION_X_DISPLAY]);
  readded.submit();
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(Array.from(readded.saves[0].alias.tag_keys), ["x"]);

  const missing = await loadSaveOverlay({
    tags: [
      { key: "meme", display_name: "Meme" },
      { key: "x", display_name: "Twitter" },
    ],
  });
  assert.deepEqual(missing.chipLabels(), []);
  missing.submit();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(missing.saves[0].alias && missing.saves[0].alias.tag_keys, undefined);

  const absent = await loadSaveOverlay({ tags: [{ key: "meme", display_name: "Meme" }] });
  assert.deepEqual(absent.chipLabels(), []);
});

test("Class 4: the shared origin resolver takes the current key, then the retired key, then nothing", () => {
  const entry = companion.STORAGE.origin;
  assert.equal(entry.name, "kronikaOrigin");
  assert.equal(entry.retiredName, "frameNestOrigin");
  assert.deepEqual(companion.storageKeyRequest(entry), ["kronikaOrigin", "frameNestOrigin"]);

  assert.equal(
    companion.storageValue({ frameNestOrigin: "https://retired.example.ts.net" }, entry),
    "https://retired.example.ts.net",
    "retired only"
  );
  assert.equal(
    companion.storageValue(
      { frameNestOrigin: "https://retired.example.ts.net", kronikaOrigin: "https://now.example.ts.net" },
      entry,
    ),
    "https://now.example.ts.net",
    "both present: the current key wins"
  );
  assert.equal(companion.storageValue({}, entry), undefined, "neither present");
  assert.equal(companion.storageValue(null, entry), undefined, "no store at all");
  assert.equal(
    companion.storageValue({ kronikaOrigin: undefined, frameNestOrigin: "https://retired.example.ts.net" }, entry),
    "https://retired.example.ts.net",
    "an absent current key falls through to the retired key"
  );
  assert.equal(
    companion.storageValue({ kronikaOrigin: "https://now.example.ts.net", frameNestOrigin: undefined }, entry),
    "https://now.example.ts.net"
  );
});

test("Class 4: the picker and the side panel both read the origin through the shared resolver", () => {
  const pickerSource = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
  const sidebarSource = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
  for (const [name, source] of [["picker", pickerSource], ["sidebar", sidebarSource]]) {
    assert.match(source, /storageKeyRequest\(companion\.STORAGE\.origin\)/, name);
    assert.match(source, /storageValue\(stored, companion\.STORAGE\.origin\)/, name);
  }
  assert.match(
    pickerSource,
    /storageChangeFor\(changes, companion\.STORAGE\.origin\)/,
    "the picker must react to a write under either spelling"
  );
  assert.doesNotMatch(pickerSource, /changes\.frameNestOrigin/);
  assert.doesNotMatch(sidebarSource, /stored\.frameNestOrigin/);
  assert.doesNotMatch(sidebarSource, /\["frameNestOrigin"\]/);
});

test("Extension version is at least the dual-send revision so a reload is observable", () => {
  const parts = String(manifest.version).split(".");
  assert.equal(parts.length, 3, "the manifest version stays a three-part release version");
  const [major, minor] = parts.map((part) => Number(part));
  assert.ok(Number.isInteger(major) && Number.isInteger(minor), manifest.version);
  assert.ok(
    major > 0 || minor >= 2,
    "the dual-send revision must be distinguishable from the pre-cut 0.1.0 in the browser extension page"
  );
  assert.equal(manifest.name, "Kronika X Companion", "the display name is the single product identity");
  assert.doesNotMatch(manifest.name, /FrameNest/i);
  assert.doesNotMatch(manifest.description, /FrameNest/i);
  assert.doesNotMatch(manifest.action.default_title, /FrameNest/i);
});
