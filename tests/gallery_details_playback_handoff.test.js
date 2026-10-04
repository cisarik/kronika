const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");

function skipToFunctionBodyStart(source, functionStart) {
  const parameterStart = source.indexOf("(", functionStart);
  assert.notEqual(parameterStart, -1, "missing parameter list");
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  for (let index = parameterStart; index < source.length; index += 1) {
    const character = source[index];
    const next = source[index + 1];
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
    if (quote) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === quote) quote = null;
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
      quote = character;
      continue;
    }
    if (character === "(") depth += 1;
    if (character === ")") {
      depth -= 1;
      if (depth === 0) {
        const bodyStart = source.indexOf("{", index + 1);
        assert.notEqual(bodyStart, -1, "missing function body");
        return bodyStart;
      }
    }
  }
  throw new Error("unterminated parameter list");
}

function productionFunction(name) {
  const asyncMarker = `async function ${name}(`;
  const regularMarker = `function ${name}(`;
  const asyncStart = APP_SOURCE.indexOf(asyncMarker);
  const marker = asyncStart === -1 ? regularMarker : asyncMarker;
  const start = asyncStart === -1 ? APP_SOURCE.indexOf(marker) : asyncStart;
  assert.notEqual(start, -1, `missing production function ${name}`);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  const bodyStart = skipToFunctionBodyStart(APP_SOURCE, start);
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
    if (quote) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === quote) quote = null;
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
      quote = character;
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

class FakeElement {
  constructor(document, tagName) {
    this.ownerDocument = document;
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = "";
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.src = "";
    this.alt = "";
    this.title = "";
    this.loading = "";
    this.decoding = "";
    this.preload = "";
    this.playsInline = false;
    this.autoplay = false;
    this.muted = false;
    this.controls = false;
    this.loop = false;
    this.paused = true;
    this.ended = false;
    this.currentTime = 0;
    this.duration = Number.NaN;
    this.readyState = 0;
    this.onerror = null;
    this.onload = null;
    this.onloadeddata = null;
    this.onloadedmetadata = null;
    this.oncanplay = null;
    this.ontimeupdate = null;
    this.onpause = null;
    this.isConnected = true;
  }

  setAttribute(name, value) {
    this.attributes.set(String(name).toLowerCase(), String(value));
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return this.attributes.has(key) ? this.attributes.get(key) : null;
  }

  removeAttribute(name) {
    this.attributes.delete(String(name).toLowerCase());
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatchEvent(event) {
    if (!event.target) event.target = this;
    const propertyHandler = this[`on${event.type}`];
    if (typeof propertyHandler === "function") propertyHandler(event);
    for (const listener of [...(this.listeners.get(event.type) || [])]) {
      listener(event);
    }
    return true;
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  remove() {
    if (!this.parentNode) return;
    this.parentNode.children = this.parentNode.children.filter((child) => child !== this);
    this.parentNode = null;
  }

  replaceChildren(...nodes) {
    this.children.forEach((node) => {
      node.parentNode = null;
    });
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  querySelectorAll() {
    return [];
  }

  play() {
    this.paused = false;
    if (this.ended) {
      this.ended = false;
      this.currentTime = 0;
    }
    return Promise.resolve();
  }

  pause() {
    this.paused = true;
    if (typeof this.onpause === "function") {
      this.onpause({ type: "pause", target: this });
    }
  }

  load() {}
}

function createHarness() {
  const document = {
    createElement(tagName) {
      return new FakeElement(document, tagName);
    },
  };
  const detailsPreviewContainer = new FakeElement(document, "div");
  const context = {
    document,
    activeCardMediaSurface: null,
    activeCardMediaRestore: null,
    cardMediaElements: new Set(),
    videoPlaybackPositionByMediaId: new Map(),
    VIDEO_PLAYBACK_END_EPSILON_SECONDS: 0.35,
    detailsMediaToken: 0,
    detailsMediaElement: null,
    detailsCurrentItem: null,
    detailsPreviewContainer,
    Number,
    Math,
  };
  const names = [
    "normalizeVideoPlaybackPosition",
    "rememberVideoPlaybackPosition",
    "captureVideoPlaybackPosition",
    "applyStoredVideoPlaybackPosition",
    "captureActiveCardVideoPlaybackPosition",
    "mediaContentUrl",
    "mediaGalleryPreviewUrl",
    "selectSupportedAvailableLocation",
    "selectPlaybackLocation",
    "renderPreviewFallback",
    "renderPersistentPreview",
    "renderCardOriginalPlayback",
    "cleanupCatalogCardMedia",
    "cleanupDetailsMedia",
    "cardSurfaceVideoElement",
    "syncCardMediaSurfaceToggleState",
    "activateCardPlayback",
    "renderCatalogCardMediaSurface",
    "renderUnavailableCardMediaSurface",
    "renderDetailsMediaUnavailable",
    "renderDetailsMedia",
  ];
  vm.runInNewContext(
    [
      'const MEDIA_CATALOG_ENDPOINT = "/api/media";',
      ...names.map((name) => productionFunction(name)),
    ].join("\n"),
    context,
  );
  return { context, document, detailsPreviewContainer };
}

function mp4Item(overrides = {}) {
  return {
    media_id: "33333333-3333-4333-8333-333333333333",
    media_kind: "video",
    display_title: "Clip MP4",
    locations: [{
      location_id: "44444444-4444-4444-8444-444444444444",
      availability: "available",
      relative_path: "clips/clip.mp4",
    }],
    tags: [],
    ...overrides,
  };
}

function mediaChild(surface) {
  return surface.children.find((child) => child.tagName === "IMG" || child.tagName === "VIDEO") || null;
}

function openDetailsWithMetadata(context, item, { playWhenReady = false, duration = 120 } = {}) {
  context.renderDetailsMedia(item, { playWhenReady });
  const video = [...context.detailsPreviewContainer.children].find((child) => child.tagName === "VIDEO");
  assert.ok(video, "expected Details video element");
  video.duration = duration;
  video.readyState = 1;
  video.dispatchEvent({ type: "loadedmetadata" });
  video.dispatchEvent({ type: "loadeddata" });
  video.dispatchEvent({ type: "canplay" });
  return video;
}

test("Gallery video pause captures non-zero timestamp for Details handoff", () => {
  const { context } = createHarness();
  const item = mp4Item();
  const surface = context.renderCatalogCardMediaSurface(item);

  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });
  const playingVideo = mediaChild(surface);
  assert.equal(playingVideo.tagName, "VIDEO");
  playingVideo.currentTime = 12.5;
  playingVideo.duration = 90;

  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });
  assert.equal(playingVideo.paused, true);
  assert.equal(playingVideo.currentTime, 12.5);
  assert.equal(context.videoPlaybackPositionByMediaId.get(item.media_id), 12.5);

  const detailsVideo = openDetailsWithMetadata(context, item, { duration: 90 });
  assert.equal(detailsVideo.currentTime, 12.5);
  assert.notEqual(detailsVideo.currentTime, 0);
});

test("Details does not inherit timestamp from a different logical media ID", () => {
  const { context } = createHarness();
  const first = mp4Item();
  const second = mp4Item({
    media_id: "55555555-5555-4555-8555-555555555555",
    display_title: "Other Clip",
    locations: [{
      location_id: "66666666-6666-4666-8666-666666666666",
      availability: "available",
      relative_path: "clips/other.mp4",
    }],
  });
  const surface = context.renderCatalogCardMediaSurface(first);
  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });
  mediaChild(surface).currentTime = 18;
  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });

  const otherDetails = openDetailsWithMetadata(context, second, { duration: 60 });
  assert.equal(otherDetails.currentTime, 0);

  const sameDetails = openDetailsWithMetadata(context, first, { duration: 90 });
  assert.equal(sameDetails.currentTime, 18);
});

test("Invalid timestamps are ignored safely", () => {
  const { context } = createHarness();
  const item = mp4Item();
  for (const invalid of [Number.NaN, Number.POSITIVE_INFINITY, -3, undefined, null, "12"]) {
    context.rememberVideoPlaybackPosition(item.media_id, invalid);
  }
  assert.equal(context.videoPlaybackPositionByMediaId.has(item.media_id), false);

  context.videoPlaybackPositionByMediaId.set(item.media_id, Number.NaN);
  const detailsVideo = openDetailsWithMetadata(context, item, { duration: 40 });
  assert.equal(detailsVideo.currentTime, 0);
});

test("Reopening Details remains deterministic for the same media ID", () => {
  const { context } = createHarness();
  const item = mp4Item();
  context.rememberVideoPlaybackPosition(item.media_id, 7.25);

  const first = openDetailsWithMetadata(context, item, { duration: 50 });
  assert.equal(first.currentTime, 7.25);

  context.cleanupDetailsMedia();
  const second = openDetailsWithMetadata(context, item, { duration: 50 });
  assert.equal(second.currentTime, 7.25);
});

test("Details cleanup captures position before teardown and ignores reset-to-zero events", () => {
  const { context } = createHarness();
  const item = mp4Item();
  context.detailsCurrentItem = item;
  const video = openDetailsWithMetadata(context, item, { duration: 80 });
  video.currentTime = 11.5;
  video.dispatchEvent({ type: "timeupdate" });
  assert.equal(context.videoPlaybackPositionByMediaId.get(item.media_id), 11.5);

  // Simulate browser reset during src teardown after listeners are detached.
  const originalPause = video.pause.bind(video);
  video.pause = function pauseWithReset() {
    originalPause();
    this.currentTime = 0;
    if (typeof this.ontimeupdate === "function") {
      this.ontimeupdate({ type: "timeupdate", target: this });
    }
    if (typeof this.onpause === "function") {
      this.onpause({ type: "pause", target: this });
    }
  };

  context.cleanupDetailsMedia();
  assert.equal(context.videoPlaybackPositionByMediaId.get(item.media_id), 11.5);

  const reopened = openDetailsWithMetadata(context, item, { duration: 80 });
  assert.equal(reopened.currentTime, 11.5);
});

test("Timestamp near end resets to zero", () => {
  const { context } = createHarness();
  const item = mp4Item();
  context.rememberVideoPlaybackPosition(item.media_id, 59.9);
  const detailsVideo = openDetailsWithMetadata(context, item, { duration: 60 });
  assert.equal(detailsVideo.currentTime, 0);
  assert.equal(context.videoPlaybackPositionByMediaId.get(item.media_id), 0);
});

test("Cleanup captures Gallery position before card media is removed", () => {
  const { context } = createHarness();
  const item = mp4Item();
  const surface = context.renderCatalogCardMediaSurface(item);
  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });
  const video = mediaChild(surface);
  video.currentTime = 4.5;
  video.duration = 30;
  assert.equal(context.videoPlaybackPositionByMediaId.has(item.media_id), false);

  context.cleanupCatalogCardMedia();
  assert.equal(context.videoPlaybackPositionByMediaId.get(item.media_id), 4.5);
  assert.equal(mediaChild(surface).tagName, "IMG");
});

test("Shared map lets Details pause position apply when Gallery remounts video", () => {
  const { context } = createHarness();
  const item = mp4Item();
  context.rememberVideoPlaybackPosition(item.media_id, 9.5);
  const surface = context.renderCatalogCardMediaSurface(item);
  surface.dispatchEvent({ type: "click", preventDefault() {}, key: "" });
  const video = mediaChild(surface);
  assert.equal(video.tagName, "VIDEO");
  video.duration = 100;
  video.dispatchEvent({ type: "loadedmetadata" });
  assert.equal(video.currentTime, 9.5);
});
