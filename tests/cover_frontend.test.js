const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const INDEX_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html");
const STYLES_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/styles.css");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const INDEX_SOURCE = fs.readFileSync(INDEX_PATH, "utf8");
const STYLES_SOURCE = fs.readFileSync(STYLES_PATH, "utf8");

function productionFunction(name) {
  const asyncMarker = `async function ${name}(`;
  const regularMarker = `function ${name}(`;
  const asyncStart = APP_SOURCE.indexOf(asyncMarker);
  const marker = asyncStart === -1 ? regularMarker : asyncMarker;
  const start = asyncStart === -1 ? APP_SOURCE.indexOf(marker) : asyncStart;
  assert.notEqual(start, -1, `missing production function ${name}`);
  const signatureEnd = APP_SOURCE.indexOf("\n", start);
  const bodyStart = APP_SOURCE.lastIndexOf("{", signatureEnd);
  assert.ok(bodyStart > start, `missing body for production function ${name}`);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = bodyStart; index < APP_SOURCE.length; index += 1) {
    const character = APP_SOURCE[index];
    const next = APP_SOURCE[index + 1];
    if (lineComment) {
      if (character === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (character === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (character === "/" && next === "/") {
      lineComment = true;
      index += 1;
      continue;
    }
    if (character === "/" && next === "*") {
      blockComment = true;
      index += 1;
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      if (quote === character) {
        quote = null;
      } else if (quote === null) {
        quote = character;
      }
      continue;
    }
    if (quote === "`" && character === "\\" && !escaped) {
      escaped = true;
      continue;
    }
    if (escaped) {
      escaped = false;
      continue;
    }
    if (character === "{") depth += 1;
    if (character === "}") {
      depth -= 1;
      if (depth === 0) return APP_SOURCE.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production function ${name}`);
}

function coverHarness(overrides = {}) {
  const context = {
    MEDIA_CATALOG_ENDPOINT: "/api/media",
    console,
    URLSearchParams,
    clearTimeout() {},
    setTimeout() {},
    coverTimeline: { hidden: false },
    coverCurrentTimestamp: { hidden: false },
    coverDialogState: {
      available: true,
      openItem: { media_id: "11111111-1111-4111-8111-111111111111" },
      currentLocation: { location_id: "22222222-2222-4222-8222-222222222222" },
      durationMs: 0,
      sourceVersion: "",
      currentCover: null,
      requestToken: 7,
      previewToken: 0,
      acceptToken: 0,
      selectedTimestampMs: 0,
      isImage: false,
      confirmingReplace: false,
      submitting: false,
    },
    ...overrides,
  };
  context.globalThis = context;
  vm.createContext(context);

  const functions = [
    "mediaCoverThumbnailUrl",
    "coverTimelineEndpoint",
    "coverFrameEndpoint",
    "coverMutationEndpoint",
    "coverAdminStateEndpoint",
    "coverReadoutText",
    "coverDurationText",
    "isCoverReadyItem",
    "coverPreviewCandidates",
    "coverContextStillCurrent",
    "sanitizedCoverMessage",
    "handleCoverErrorResponse",
    "coverTimelineFromPayload",
    "coverSubmittedTimestampMs",
    "applyCoverImageMode",
  ].map(productionFunction).join("\n");

  vm.runInContext(`
    function mediaContentUrl(mediaId, locationId) {
      return "/api/media/" + mediaId + "/locations/" + locationId + "/content";
    }
    function mediaGalleryPreviewUrl(mediaId, locationId) {
      return "/api/media/" + mediaId + "/locations/" + locationId + "/gallery-preview";
    }
    ${functions}
  `, context, { filename: APP_PATH });

  return {
    context,
    run(code) {
      return vm.runInContext(code, context);
    },
  };
}

test("cover endpoints are identity-only and never expose paths", () => {
  const h = coverHarness();
  const mediaId = "11111111-1111-4111-8111-111111111111";
  const locationId = "22222222-2222-4222-8222-222222222222";
  assert.equal(
    h.run(`mediaCoverThumbnailUrl("${mediaId}")`),
    `/api/media/${mediaId}/cover-thumbnail`,
  );
  assert.equal(
    h.run(`coverTimelineEndpoint("${mediaId}", "${locationId}")`),
    `/api/media/${mediaId}/locations/${locationId}/cover-timeline`,
  );
  assert.equal(
    h.run(`coverFrameEndpoint("${mediaId}", "${locationId}")`),
    `/api/media/${mediaId}/locations/${locationId}/cover-frame`,
  );
  assert.equal(
    h.run(`coverMutationEndpoint("${mediaId}", "${locationId}")`),
    `/api/media/${mediaId}/locations/${locationId}/cover`,
  );
  assert.equal(
    h.run(`coverAdminStateEndpoint("${mediaId}")`),
    `/api/admin/media/${mediaId}/cover`,
  );
  for (const value of [mediaId, locationId]) {
    assert.ok(h.run(`mediaCoverThumbnailUrl("${mediaId}").indexOf("/Users/") === -1`));
  }
});

test("cover readout formats exact HH:MM:SS.mmm timestamp", () => {
  const h = coverHarness();
  assert.equal(h.run(`coverReadoutText(0)`), "00:00:00.000");
  assert.equal(h.run(`coverReadoutText(250)`), "00:00:00.250");
  assert.equal(h.run(`coverReadoutText(61000)`), "00:01:01.000");
  assert.equal(h.run(`coverReadoutText(3661500)`), "01:01:01.500");
  assert.equal(h.run(`coverReadoutText(-5)`), "00:00:00.000");
  assert.equal(h.run(`coverDurationText(2000)`), "/ 00:00:02.000");
});

test("cover priority prefers validated cover thumbnail then falls back", () => {
  const h = coverHarness();
  const mediaId = "11111111-1111-4111-8111-111111111111";
  const locationId = "22222222-2222-4222-8222-222222222222";
  const videoWithCover = { media_id: mediaId, media_kind: "video", cover_ready: true };
  const videoPlain = { media_id: mediaId, media_kind: "video", cover_ready: false };
  const imageItem = { media_id: mediaId, media_kind: "image", cover_ready: false };
  const cutting = (item, locText = "null") => Array.from(
    h.run(`coverPreviewCandidates(${JSON.stringify(item)}, ${locText})`),
  );

  assert.deepStrictEqual(
    cutting(videoWithCover, `{ location_id: "${locationId}" }`),
    [
      `/api/media/${mediaId}/cover-thumbnail`,
      `/api/media/${mediaId}/locations/${locationId}/gallery-preview`,
    ],
  );
  assert.deepStrictEqual(
    cutting(videoPlain, `{ location_id: "${locationId}" }`),
    [`/api/media/${mediaId}/locations/${locationId}/gallery-preview`],
  );
  assert.deepStrictEqual(
    cutting(imageItem, `{ location_id: "${locationId}" }`),
    [`/api/media/${mediaId}/locations/${locationId}/content`],
  );
  assert.deepStrictEqual(cutting(videoPlain), []);
  assert.equal(h.run(`isCoverReadyItem(${JSON.stringify(videoWithCover)})`), true);
  assert.equal(h.run(`isCoverReadyItem(${JSON.stringify(videoPlain)})`), false);
});

test("stale cover responses are fenced by request ownership", () => {
  const h = coverHarness({ coverDialogState: {
    available: true,
    openItem: { media_id: "11111111-1111-4111-8111-111111111111" },
    currentLocation: null,
    durationMs: 0,
    sourceVersion: "",
    currentCover: null,
    requestToken: 9,
    previewToken: 0,
    acceptToken: 0,
    selectedTimestampMs: 0,
    confirmingReplace: false,
    submitting: false,
  } });
  assert.equal(
    h.run(`coverContextStillCurrent({ mediaId: "11111111-1111-4111-8111-111111111111", token: 9 })`),
    true,
  );
  assert.equal(
    h.run(`coverContextStillCurrent({ mediaId: "11111111-1111-4111-8111-111111111111", token: 8 })`),
    false,
  );
  assert.equal(
    h.run(`coverContextStillCurrent({ mediaId: "99999999-9999-4999-8999-999999999999", token: 9 })`),
    false,
  );
});

test("cover error messages are sanitized", () => {
  const h = coverHarness();
  const bad = { error: { code: "COVER_CONFLICT", message: "The accepted cover changed." } };
  assert.equal(h.run(`sanitizedCoverMessage(${JSON.stringify(bad)})`), "The accepted cover changed.");
  assert.equal(h.run(`sanitizedCoverMessage(null)`), "The cover operation could not be completed.");
  assert.equal(
    h.run(`sanitizedCoverMessage({ error: { code: "X", message: "/Users/example/private.mp4" } })`),
    "/Users/example/private.mp4",
  );
});

test("image timeline payload selects timeless image mode without a scrubber", () => {
  const h = coverHarness();
  assert.equal(
    h.run(`coverTimelineFromPayload({ media_kind: "image", duration_ms: 0 })`),
    true,
  );
  assert.equal(
    h.run(`coverTimelineFromPayload({ media_kind: "video", duration_ms: 2000 })`),
    false,
  );
  assert.equal(
    h.run(`coverTimelineFromPayload({ media_kind: "animated_image", duration_ms: 1000 })`),
    false,
  );
  assert.equal(h.run(`coverTimelineFromPayload(null)`), false);
});

test("timeless image mode hides the timeline and never submits a nonzero timestamp", () => {
  const h = coverHarness();
  h.run(`applyCoverImageMode(true)`);
  assert.equal(h.context.coverDialogState.isImage, true);
  assert.equal(h.context.coverTimeline.hidden, true);
  assert.equal(h.context.coverCurrentTimestamp.hidden, true);
  h.run(`applyCoverImageMode(false)`);
  assert.equal(h.context.coverDialogState.isImage, false);
  assert.equal(h.context.coverTimeline.hidden, false);
  assert.equal(h.context.coverCurrentTimestamp.hidden, false);
  assert.equal(h.run(`coverSubmittedTimestampMs(true, 500)`), 0);
  assert.equal(h.run(`coverSubmittedTimestampMs(false, 500)`), 500);
  assert.equal(h.run(`coverSubmittedTimestampMs(false, 0)`), 0);
});

test("cover dialog markup and styles are present and accessible", () => {
  for (const id of [
    "media-details-choose-cover",
    "cover-dialog",
    "cover-dialog-title",
    "cover-dialog-close",
    "cover-timeline-range",
    "cover-timestamp-readout",
    "cover-preview-button",
    "cover-preview-container",
    "cover-set-button",
    "cover-cancel-button",
    "cover-replace-confirm",
    "cover-dialog-status",
  ]) {
    assert.ok(INDEX_SOURCE.includes(`id="${id}"`), `missing id ${id}`);
  }
  assert.ok(INDEX_SOURCE.includes(`class="cover-timeline"`));
  assert.ok(INDEX_SOURCE.includes(`id="upload-file-hint"`));
  assert.ok(STYLES_SOURCE.includes(".cover-dialog"));
  assert.ok(STYLES_SOURCE.includes("prefers-reduced-motion"));
  assert.ok(INDEX_SOURCE.includes(`aria-live="polite"`));
});
