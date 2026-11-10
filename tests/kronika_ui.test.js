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
const SHELL_START = APP_SOURCE.indexOf("/* KRONIKA_SHELL_START */");
const SHELL_END = APP_SOURCE.indexOf("/* KRONIKA_SHELL_END */");
const SHELL_SOURCE = APP_SOURCE.slice(SHELL_START, SHELL_END);
const ENDPOINT_SOURCE = APP_SOURCE.match(/const MEDIA_CATALOG_ENDPOINT = "[^"]+";/)[0];
const PREVIEW_URL_START = APP_SOURCE.indexOf("function mediaGalleryPreviewUrl(");
const PREVIEW_URL_END = APP_SOURCE.indexOf("\n}", PREVIEW_URL_START) + 2;
const PREVIEW_URL_SOURCE = APP_SOURCE.slice(PREVIEW_URL_START, PREVIEW_URL_END);
assert.match(PREVIEW_URL_SOURCE, /gallery-preview/);

function jsonResponse(payload, status = 200, text) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
    text: async () => (text == null ? JSON.stringify(payload) : text),
  };
}

class El {
  constructor(tag, id = "") {
    this.tag = tag;
    this.id = id;
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.value = "";
    this.checked = false;
    this.type = "";
    this.href = "";
    this.rel = "";
    this.target = "";
    this.srcdoc = "";
    this.src = "";
    this.width = 0;
    this.height = 0;
    this.className = "";
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.listeners = new Map();
    this.isConnected = true;
    this.focused = false;
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
    if (name === "src") this.src = "";
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  removeEventListener(type, listener) {
    const entries = this.listeners.get(type);
    if (!entries) return;
    const index = entries.indexOf(listener);
    if (index >= 0) entries.splice(index, 1);
  }

  remove() {
    if (!this.parentNode) return;
    const index = this.parentNode.children.indexOf(this);
    if (index >= 0) this.parentNode.children.splice(index, 1);
    this.parentNode = null;
  }

  appendChild(node) {
    node.parentNode = this;
    const doc = this.ownerDocument || node.ownerDocument || null;
    const assign = (current) => {
      current.ownerDocument = doc;
      current.children.forEach(assign);
    };
    if (doc) assign(node);
    this.children.push(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  focus() {
    const previous = this.ownerDocument && this.ownerDocument.activeElement;
    if (previous && previous !== this) previous.focused = false;
    this.focused = true;
    if (this.ownerDocument) this.ownerDocument.activeElement = this;
    const listeners = this.listeners.get("focus");
    if (listeners) listeners.forEach((listener) => listener({ type: "focus", target: this }));
  }

  querySelectorAll(selector) {
    const found = [];
    const visit = (node) => {
      if (node !== this && matches(node, selector)) found.push(node);
      node.children.forEach(visit);
    };
    this.children.forEach(visit);
    return found;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

function matches(node, selector) {
  if (selector === "[data-kronika-view]") return node.attributes.has("data-kronika-view");
  if (selector === ".kronika-nav") return node.className.split(/\s+/).includes("kronika-nav");
  if (selector === ".header-search") return node.className.split(/\s+/).includes("header-search");
  if (selector === ".kronika-nav a") return node.tag === "a" && Boolean(node.getAttribute("data-kronika-nav"));
  if (selector === ".kronika-nav a[data-kronika-nav]") return node.tag === "a" && node.attributes.has("data-kronika-nav");
  if (selector === "#kronika-timeline-filters [data-kronika-filter]") {
    return node.attributes.has("data-kronika-filter");
  }
  if (selector === ".kronika-card") return node.className.split(/\s+/).includes("kronika-card");
  if (selector === ".kronika-card__preview") {
    return node.className.split(/\s+/).includes("kronika-card__preview");
  }
  if (selector === "img") return node.tag === "img";
  if (selector === "small") return node.tag === "small";
  if (selector === "time") return node.tag === "time";
  if (selector.startsWith("#") && !selector.includes(" ")) return node.id === selector.slice(1);
  if (selector.startsWith(".") && !selector.includes(" ")) {
    return node.className.split(/\s+/).includes(selector.slice(1));
  }
  return false;
}

function createHarness(fetchImpl, options = {}) {
  const byId = {};
  const documentListeners = [];
  const hashListeners = [];
  const windowListeners = [];
  const ids = [
    "kronika-timeline",
    "kronika-timeline-heading",
    "kronika-timeline-status",
    "kronika-timeline-results",
    "kronika-timeline-filters",
    "kronika-timeline-prev",
    "kronika-timeline-next",
    "kronika-timeline-retry",
    "kronika-history",
    "kronika-history-heading",
    "kronika-history-status",
    "kronika-history-results",
    "kronika-history-questions",
    "kronika-history-records-tab",
    "kronika-history-prev",
    "kronika-history-next",
    "kronika-history-retry",
    "kronika-question",
    "kronika-question-heading",
    "kronika-question-form",
    "kronika-question-input",
    "kronika-question-error",
    "kronika-question-status",
    "kronika-question-submit",
    "kronika-question-retry",
    "kronika-consent",
    "kronika-capabilities-status",
    "kronika-request",
    "kronika-request-heading",
    "kronika-request-status",
    "kronika-request-cancel",
    "kronika-request-resume",
    "kronika-request-open-answer",
    "kronika-record",
    "kronika-record-heading",
    "kronika-record-question",
    "kronika-record-status",
    "kronika-record-retry",
    "kronika-record-citations",
    "kronika-document-frame",
    "kronika-review",
    "kronika-review-heading",
    "kronika-review-status",
    "kronika-review-results",
    "kronika-review-detail",
    "kronika-review-version",
    "kronika-review-approve",
    "kronika-review-withdraw",
    "kronika-review-reload",
    "kronika-review-readiness",
    "kronika-review-media",
    "kronika-review-private",
    "kronika-review-shared",
    "kronika-review-requests",
    "kronika-review-prev",
    "kronika-review-next",
    "kronika-unknown",
    "kronika-unknown-heading",
    "kronika-nav",
    "kronika-nav-review",
    "catalog-browser",
    "header-search",
    "main",
  ];
  ids.forEach((id) => {
    byId[id] = new El("div", id);
  });
  const views = {
    "kronika-timeline": "timeline",
    "kronika-history": "history",
    "kronika-question": "question",
    "kronika-request": "request",
    "kronika-record": "record",
    "kronika-review": "review",
    "kronika-unknown": "unknown",
  };
  Object.entries(views).forEach(([id, name]) => {
    byId[id].setAttribute("data-kronika-view", name);
    byId[id].hidden = true;
  });
  byId["kronika-nav"].className = "kronika-nav";
  byId["header-search"].className = "header-search";
  const browse = new El("div");
  browse.className = "kronika-nav__browse";
  const actions = new El("div");
  actions.className = "kronika-nav__actions";
  byId["kronika-nav"].appendChild(browse);
  byId["kronika-nav"].appendChild(actions);
  const navGroups = {
    timeline: browse,
    gallery: browse,
    history: browse,
    search: actions,
    research: actions,
    review: actions,
  };
  ["timeline", "gallery", "history", "search", "research", "review"].forEach((name) => {
    const link = new El("a", `kronika-nav-${name}`);
    link.setAttribute("data-kronika-nav", name);
    navGroups[name].appendChild(link);
    byId[link.id] = link;
  });
  ["all", "search", "research", "media", "general", "meme", "movie", "youtube"].forEach((name) => {
    const button = new El("button");
    button.setAttribute("data-kronika-filter", name);
    byId["kronika-timeline-filters"].appendChild(button);
  });
  const calls = [];
  const detailsCalls = [];
  const catalogLoads = [];
  const timers = [];
  const storage = options.storage instanceof Map ? options.storage : new Map();
  const identityState = {
    resolved: true,
    audience: "trusted_loopback",
    login: options.login || "alice",
    capabilities: new Set(options.capabilities || ["research.run", "records.approve"]),
  };
  const location = {
    _hash: options.hash || "",
    get hash() {
      return this._hash;
    },
    set hash(value) {
      this._hash = String(value);
      hashListeners.forEach((listener) => listener());
    },
  };
  const document = {
    hidden: false,
    body: byId.main,
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => {
      const node = new El(tag);
      node.ownerDocument = document;
      return node;
    },
    contains: () => true,
    addEventListener: (type, listener) => documentListeners.push({ type, listener }),
    querySelector: (selector) => document.querySelectorAll(selector)[0] || null,
    querySelectorAll: (selector) => {
      const found = [];
      const seen = new Set();
      const visit = (node) => {
        if (!node || seen.has(node)) return;
        seen.add(node);
        if (matches(node, selector)) found.push(node);
        node.children.forEach(visit);
      };
      Object.values(byId).forEach(visit);
      return found;
    },
    activeElement: null,
  };
  const attachOwner = (node) => {
    node.ownerDocument = document;
    node.children.forEach(attachOwner);
  };
  Object.values(byId).forEach(attachOwner);
  const context = {
    TextEncoder,
    URL,
    URLSearchParams,
    console,
    setTimeout: (fn, delay) => {
      const id = timers.length + 1;
      timers.push({ id, fn, delay });
      return id;
    },
    clearTimeout: (id) => {
      const index = timers.findIndex((timer) => timer.id === id);
      if (index >= 0) timers.splice(index, 1);
    },
    crypto: globalThis.crypto,
    AbortController,
    document,
    location,
    window: {
      addEventListener: (type, listener) => {
        if (type === "hashchange") hashListeners.push(listener);
        else windowListeners.push({ type, listener });
      },
    },
    sessionStorage: {
      getItem: (key) => (storage.has(key) ? storage.get(key) : null),
      setItem: (key, value) => storage.set(key, String(value)),
    },
    fetch: async (url, init) => {
      calls.push({ url: String(url), init });
      return fetchImpl(String(url), init, calls);
    },
    identityState,
    identityHasCapability: (capability) => identityState.capabilities.has(capability),
    isPublicPublishedAudience: () => identityState.audience === "public_published",
    framenestJSONHeaders: () => ({ Accept: "application/json", "Content-Type": "application/json" }),
    framenestMutationHeaders: (headers) => {
      const merged = Object.assign({}, headers || {});
      merged["X-Kronika-Request"] = "1";
      return merged;
    },
    loadCatalog: () => catalogLoads.push("catalog"),
    loadCatalogTags: () => catalogLoads.push("tags"),
    openDetailsDialog: (item) => detailsCalls.push(item),
    closeDetailsDialog: () => detailsCalls.push("close"),
    metadataDirtyForBeforeUnload: () => Boolean(options.dirty),
    confirmDiscardDirtyMetadata: async () => (options.discard === false ? null : { accepted: true }),
    closeMetadataWorkspaceWithContext: () => true,
    storage,
    calls,
    detailsCalls,
    catalogLoads,
    timers,
    byId,
    documentListeners,
    windowListeners,
  };
  vm.createContext(context);
  vm.runInContext(
    `"use strict";\n${ENDPOINT_SOURCE}\n${PREVIEW_URL_SOURCE}\n${SHELL_SOURCE}`,
    context,
  );
  return context;
}

async function settle() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

test("shell markup keeps the gallery and sandboxed answer frame", () => {
  assert.match(INDEX_SOURCE, /<title>Kronika<\/title>/);
  assert.match(INDEX_SOURCE, /class="brand-mark"[^>]*>K</);
  assert.match(INDEX_SOURCE, /class="brand-wordmark">Kronika</);
  assert.match(INDEX_SOURCE, /href="#\/timeline"/);
  assert.match(INDEX_SOURCE, /href="#\/gallery"/);
  assert.match(INDEX_SOURCE, /id="catalog-browser"/);
  assert.match(INDEX_SOURCE, /id="kronika-document-frame"[^>]*sandbox=""/);
  assert.match(INDEX_SOURCE, /referrerpolicy="no-referrer"/);
  assert.match(APP_SOURCE, /KronikaCompanionWeb\.onOpenDetails/);
  assert.match(APP_SOURCE, /openDetailsDialog\(\{\s*media_id:\s*mediaId\s*\}/);
  assert.doesNotMatch(APP_SOURCE, /console\.log/);
  assert.doesNotMatch(APP_SOURCE, /indexedDB/);
  assert.doesNotMatch(APP_SOURCE, /location\.search/);
});

test("empty hash selects Timeline and details stay reachable", async () => {
  const harness = createHarness(async (url) => {
    if (!url.startsWith("/api/timeline")) return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
    return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
  });
  harness.kronikaStartNavigation();
  await settle();
  assert.equal(harness.kronikaParseRoute("").name, "timeline");
  assert.equal(harness.byId["kronika-timeline"].hidden, false);
  assert.equal(harness.byId["catalog-browser"].hidden, true);
  harness.location.hash = "#/gallery";
  await settle();
  assert.equal(harness.byId["catalog-browser"].hidden, false);
  assert.equal(harness.catalogLoads.includes("catalog"), true);
  harness.location.hash = "#/details/media-1";
  await settle();
  assert.equal(harness.detailsCalls.at(-1).media_id, "media-1");
});

test("timeline requests only its endpoint, including for an administrator", async () => {
  const harness = createHarness(async (url) => {
    if (url.startsWith("/api/timeline")) {
      return jsonResponse({
        items: [{
          record_id: "11111111-1111-4111-8111-111111111111",
          kind: "search",
          display_title: "Approved title",
          timeline_entered_at_ms: 1_700_000_000_000,
          content_category: null,
        }],
        total: 1,
        limit: 24,
        offset: 0,
      });
    }
    return jsonResponse({
      items: [{ record_id: "secret", kind: "search", display_title: "Hidden admin" }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const titles = harness.byId["kronika-timeline-results"].querySelectorAll(".kronika-chronicle__title").map((link) => link.textContent);
  assert.deepEqual(titles, ["Approved title"]);
  assert.equal(harness.calls.every((call) => call.url.startsWith("/api/timeline")), true);
  assert.equal(harness.byId["kronika-timeline-status"].textContent.includes("Hidden"), false);
});

test("personal history and the timeline use separate endpoints", async () => {
  const harness = createHarness(async (url) => {
    if (url.startsWith("/api/research-requests")) {
      return jsonResponse({
        items: [
          { operation_id: "op-failed", kind: "search", state: "failed", prompt: "Broken question", record_id: null },
          { operation_id: "op-cancelled", kind: "research", state: "cancelled", prompt: "Stopped question", record_id: null },
          { operation_id: "op-running", kind: "search", state: "running", prompt: "Live question", record_id: null },
        ],
        total: 3,
        limit: 24,
        offset: 0,
      });
    }
    return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
  });
  harness.kronikaStartNavigation();
  await settle();
  harness.location.hash = "#/history";
  await settle();
  const text = harness.byId["kronika-history-results"].children.map((row) => (
    row.querySelector(".kronika-chronicle__status").children[1].textContent
  ));
  assert.equal(text.includes("Failed"), true);
  assert.equal(text.includes("Cancelled"), true);
  assert.equal(harness.byId["kronika-timeline-results"].children.length, 0);
});

test("capabilities distinguish disabled, unavailable, and enabled states", async () => {
  async function load(payload, status = 200) {
    const harness = createHarness(async () => {
      if (status !== 200) return jsonResponse({}, status);
      return jsonResponse(payload);
    });
    harness.location.hash = "#/search";
    await harness.kronikaFollowHash();
    return harness.byId["kronika-capabilities-status"].textContent;
  }
  assert.match(await load({ enabled: false }), /turned off/);
  assert.match(await load({}, 503), /unavailable/);
  assert.match(await load({ enabled: true, provider_id: null }), /not ready/);
  const enabled = await load({
    enabled: true,
    provider_id: "openai-responses",
    retention_notice: "Answers are stored locally.",
    search: { budget_reservation_usd_micros: 25 },
  });
  assert.match(enabled, /Answers are stored locally/);
  assert.match(enabled, /not a guaranteed invoice cap/);
  assert.match(enabled, /does not confirm that the provider is ready/);
});

test("submission retries keep the attempt id until the question changes", async () => {
  const bodies = [];
  const harness = createHarness(async (_url, init) => {
    if (init && init.method === "POST") {
      bodies.push(JSON.parse(init.body));
      throw new Error("lost");
    }
    return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
  });
  harness.kronikaStartNavigation();
  await settle();
  harness.byId["kronika-question-input"].value = "Same question";
  harness.byId["kronika-consent"].checked = true;
  await harness.kronikaSubmitQuestion({ preventDefault() {} });
  await harness.kronikaSubmitQuestion({ preventDefault() {} });
  await harness.kronikaRetrySubmission();
  assert.equal(bodies.length, 3);
  assert.equal(new Set(bodies.map((body) => body.client_request_id)).size, 1);
  assert.equal(bodies[0].prompt, "Same question");
  assert.equal(bodies[0].consent_version, "kronika-research-v1");
  assert.equal(harness.calls.filter((call) => call.init && call.init.method === "POST").every((call) => (
    call.init.headers["X-Kronika-Request"] === "1" &&
    call.init.headers["X-FrameNest-Request"] === undefined
  )), true);
  const stored = harness.storage.get("kronika.research.attempt.v1");
  assert.equal(stored.includes("Same question"), false);
  const firstId = bodies[0].client_request_id;
  harness.byId["kronika-question-input"].value = "Changed question";
  harness.byId["kronika-question-input"].listeners.get("input").forEach((listener) => listener());
  harness.byId["kronika-consent"].checked = true;
  harness.fetch = async (_url, init) => {
    if (init && init.method === "POST") {
      bodies.push(JSON.parse(init.body));
      return jsonResponse({ operation_id: "op-new", state: "running" }, 202);
    }
    return jsonResponse({ operation_id: "op-new", state: "running", record_id: null });
  };
  await harness.kronikaSubmitQuestion({ preventDefault() {} });
  await settle();
  assert.notEqual(bodies.at(-1).client_request_id, firstId);
});

test("polling stays serial, pauses after transport loss, and does not invent cancellation", async () => {
  let release = null;
  let fetches = 0;
  const harness = createHarness(async () => {
    fetches += 1;
    if (fetches === 1) {
      return new Promise((resolve) => {
        release = () => resolve(jsonResponse({ operation_id: "op-1", state: "running", record_id: null }));
      });
    }
    return jsonResponse({ operation_id: "op-1", state: "cancel_requested", record_id: null });
  });
  const pending = harness.kronikaShowRequest("op-1");
  await harness.kronikaPollOnce();
  assert.equal(fetches, 1);
  release();
  await pending;
  assert.equal(harness.timers.length, 1);
  assert.equal(harness.timers[0].delay, 5000);
  const timer = harness.timers[0];
  timer.fn();
  await settle();
  assert.match(harness.byId["kronika-request-status"].textContent, /Cancellation requested/);
  assert.equal(harness.byId["kronika-request-status"].textContent.includes("Cancelled."), false);

  let failures = 0;
  const pausing = createHarness(async () => {
    failures += 1;
    if (failures === 1) return jsonResponse({ operation_id: "op-2", state: "running", record_id: null });
    throw new Error("transport");
  });
  await pausing.kronikaShowRequest("op-2");
  for (let attempt = 0; attempt < 3; attempt += 1) {
    pausing.timers[pausing.timers.length - 1].fn();
    await settle();
  }
  assert.match(pausing.byId["kronika-request-status"].textContent, /Resume updates/);
  assert.equal(pausing.byId["kronika-request-status"].textContent.includes("Failed."), false);
});

test("render html stays in the sandboxed frame and citations are not fetched", async () => {
  const harness = createHarness(async (url) => {
    assert.equal(url.includes("example.test"), false);
    if (url.endsWith("/render")) return jsonResponse(null, 200, "<p>Safe answer</p>");
    return jsonResponse({
      record: { record_id: "11111111-1111-4111-8111-111111111111", kind: "search" },
      version: 2,
      document: {
        question_text: "Visible question",
        citations: [{ title: "Paper", url: "https://example.test/paper" }],
      },
    });
  });
  await harness.kronikaLoadRecord("11111111-1111-4111-8111-111111111111");
  const frame = harness.byId["kronika-document-frame"];
  assert.match(frame.srcdoc, /default-src 'none'/);
  assert.match(frame.srcdoc, /Safe answer/);
  assert.equal(harness.byId["kronika-record-question"].textContent, "Visible question");
  const link = harness.byId["kronika-record-citations"].children[0].children[0];
  assert.equal(link.href, "https://example.test/paper");
  assert.equal(link.rel, "noopener noreferrer");
  assert.equal(harness.calls.some((call) => call.url.includes("example.test")), false);
});

test("stale approval requires reload before another action", async () => {
  let version = 3;
  const posts = [];
  const harness = createHarness(async (url, init) => {
    if (init && init.method === "POST") {
      posts.push(JSON.parse(init.body));
      return jsonResponse({ error: { code: "RECORD_CONFLICT", message: "changed" } }, 409);
    }
    return jsonResponse({
      record: {
        record_id: "11111111-1111-4111-8111-111111111111",
        kind: "search",
        visibility: "private",
        completed_at_ms: 10,
      },
      version,
      document: null,
    });
  });
  await harness.kronikaLoadReviewDetail("11111111-1111-4111-8111-111111111111");
  await harness.kronikaSubmitApproval("approve");
  assert.deepEqual(posts, [{ action: "approve", expected_version: 3 }]);
  await harness.kronikaSubmitApproval("approve");
  assert.equal(posts.length, 1);
  version = 4;
  harness.fetch = async (url, init) => {
    if (init && init.method === "POST") {
      posts.push(JSON.parse(init.body));
      return jsonResponse({ record_id: url, version: 5, changed: true });
    }
    return jsonResponse({
      record: {
        record_id: "11111111-1111-4111-8111-111111111111",
        kind: "search",
        visibility: "private",
        completed_at_ms: 10,
      },
      version,
      document: null,
    });
  };
  await harness.kronikaReloadReview();
  await harness.kronikaSubmitApproval("approve");
  assert.equal(posts.at(-1).expected_version, 4);
});

test("a failed refresh keeps only the previous authorized page", async () => {
  let fail = false;
  const harness = createHarness(async () => {
    if (fail) throw new Error("offline");
    return jsonResponse({
      items: [{
        record_id: "11111111-1111-4111-8111-111111111111",
        kind: "media",
        display_title: "Kept title",
        media_id: "22222222-2222-4222-8222-222222222222",
        timeline_entered_at_ms: 20,
        content_category: "general",
      }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  const route = harness.kronikaParseRoute("#/timeline");
  await harness.kronikaLoadTimeline(route);
  fail = true;
  await harness.kronikaLoadTimeline(route);
  assert.equal(
    harness.byId["kronika-timeline-results"].querySelector(".kronika-chronicle__title").textContent,
    "Kept title",
  );
  assert.match(harness.byId["kronika-timeline-status"].textContent, /not refreshed/);
  assert.equal(harness.byId["kronika-timeline-retry"].hidden, false);
});

test("a same-query Timeline refresh failure keeps Retry without stealing focus", async () => {
  let fail = false;
  const harness = createHarness(async (url) => {
    if (fail) throw new Error("offline");
    return jsonResponse({
      items: [{
        item_id: "media:kept",
        record_id: "11111111-1111-4111-8111-111111111111",
        kind: "media",
        display_title: "Kept title",
        media_id: "media-kept",
        content_category: "general",
        created_at_ms: 20,
        status: "analyzed",
        visibility: "private",
        publication_state: "unpublished",
      }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  harness.location._hash = "#/timeline?filter=media&q=kept&offset=24";
  const route = harness.kronikaParseRoute(harness.location.hash);
  await harness.kronikaStartNavigation();
  await settle();
  await harness.kronikaLoadTimeline(route);
  const firstUrl = harness.calls.at(-1).url;
  fail = true;
  const focus = new El("button");
  focus.focused = true;
  harness.document.activeElement = focus;
  await harness.kronikaLoadTimeline(route);
  assert.equal(
    harness.byId["kronika-timeline-results"].querySelector(".kronika-chronicle__title").textContent,
    "Kept title",
  );
  const retry = harness.byId["kronika-timeline-retry"];
  assert.equal(retry.hidden, false);
  const before = harness.calls.length;
  retry.listeners.get("click")[0]();
  await settle();
  assert.equal(harness.calls.length, before + 1);
  assert.equal(harness.calls.at(-1).url, firstUrl);
  assert.equal(harness.document.activeElement, focus);
  assert.equal(focus.focused, true);
  assert.equal(retry.focused, false);
});

test("a changed-query Timeline failure does not retain another query's rows", async () => {
  let fail = false;
  const harness = createHarness(async (url) => {
    if (fail) throw new Error("offline");
    return jsonResponse({
      items: [{
        item_id: "question:kept",
        kind: "search",
        display_title: "Kept title",
        record_id: "record-kept",
        operation_id: "kept",
        created_at_ms: 20,
        status: "saved",
        visibility: "private",
        publication_state: "unpublished",
      }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  fail = true;
  harness.location._hash = "#/timeline?filter=search";
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline?filter=search"));
  assert.equal(harness.byId["kronika-timeline-results"].children.length, 0);
  assert.equal(harness.byId["kronika-timeline-results"].textContent.includes("Kept title"), false);
  assert.equal(harness.byId["kronika-timeline-retry"].hidden, false);
  assert.doesNotMatch(harness.byId["kronika-timeline-status"].textContent, /not refreshed/);
});

test("Timeline chronicle header is hidden from assistive tech and rows label their fields", async () => {
  assert.match(INDEX_SOURCE, /class="kronika-chronicle-header" aria-hidden="true"/);
  assert.match(INDEX_SOURCE, /kronika-chronicle-header[\s\S]*<ol id="kronika-timeline-results"/);
  assert.match(STYLES_SOURCE, /@media \(max-width: 720px\)[\s\S]*\.kronika-chronicle-header \{\s*display: none;/);
  assert.match(
    STYLES_SOURCE,
    /@media \(max-width: 720px\)[\s\S]*#kronika-timeline \.kronika-chronicle__label \{[\s\S]*position: static !important;/,
  );
  const harness = createHarness(async () => jsonResponse({
    items: [{
      item_id: "question:op-1",
      kind: "search",
      display_title: "Saved question",
      record_id: "record-1",
      operation_id: "op-1",
      created_at_ms: Date.UTC(2026, 0, 2, 3, 4, 5),
      status: "saved",
      visibility: "private",
      publication_state: "unpublished",
    }],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const row = harness.byId["kronika-timeline-results"].children[0];
  const labels = row.querySelectorAll(".kronika-chronicle__label").map((node) => node.textContent);
  assert.deepEqual(labels, ["Kind"]);
  labels.forEach((label) => {
    const node = row.querySelectorAll(".kronika-chronicle__label").find((item) => item.textContent === label);
    assert.equal(node.className.includes("visually-hidden"), true);
  });
  assert.equal(row.querySelector(".kronika-chronicle__title").textContent, "Saved question");
  assert.equal(row.querySelector("time"), null);
  assert.equal(row.querySelector(".kronika-chronicle__added"), null);
  const header = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf('class="kronika-chronicle-header"'),
    INDEX_SOURCE.indexOf('id="kronika-timeline-results"'),
  );
  assert.equal(header.includes("Added"), false);
  assert.match(header, /Preview[\s\S]*Item[\s\S]*Kind[\s\S]*Status[\s\S]*Visibility/);
  assert.doesNotMatch(INDEX_SOURCE, /<select id="kronika-timeline-(kind|status-filter|visibility|publication)"/);
  assert.match(INDEX_SOURCE, /id="kronika-timeline-kind"[\s\S]*aria-haspopup="listbox"/);
  assert.match(INDEX_SOURCE, /id="kronika-timeline-search"/);
  assert.match(INDEX_SOURCE, /id="kronika-timeline-search-clear"/);
});

test("identity loss clears private content and dirty navigation can be cancelled", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }), {
    dirty: true,
    discard: false,
  });
  harness.kronikaNoteIdentity();
  harness.byId["kronika-document-frame"].srcdoc = "<p>secret</p>";
  harness.byId["kronika-record-question"].textContent = "secret question";
  harness.identityState.login = "";
  harness.kronikaNoteIdentity();
  assert.equal(harness.byId["kronika-document-frame"].srcdoc, "");
  assert.equal(harness.byId["kronika-record-question"].textContent, "");
  harness.location._hash = "#/gallery";
  await harness.kronikaFollowHash();
  assert.equal(harness.location.hash, "#/timeline");
  assert.equal(harness.catalogLoads.length, 0);
});

test("reload recovery reuses the request id only when the fingerprint matches", async () => {
  const storage = new Map();
  const bodiesA = [];
  const harnessA = createHarness(async (_url, init) => {
    if (init && init.method === "POST") {
      bodiesA.push(JSON.parse(init.body));
      throw new Error("lost");
    }
    return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
  }, { storage });
  harnessA.kronikaStartNavigation();
  await settle();
  harnessA.byId["kronika-question-input"].value = "Synthetic question";
  harnessA.byId["kronika-consent"].checked = true;
  await harnessA.kronikaSubmitQuestion({ preventDefault() {} });
  assert.equal(bodiesA.length, 1);
  const storedRaw = storage.get("kronika.research.attempt.v1");
  const stored = JSON.parse(storedRaw);
  assert.deepEqual(Object.keys(stored).sort(), ["fingerprint", "id", "operationId"]);
  assert.equal(stored.operationId, "");
  assert.equal(stored.fingerprint.length > 0, true);
  assert.equal(storedRaw.includes("Synthetic question"), false);

  async function recoveredContext(login, question) {
    const bodies = [];
    const harness = createHarness(async (_url, init) => {
      if (init && init.method === "POST") {
        bodies.push(JSON.parse(init.body));
        return jsonResponse({ operation_id: "op-recovered", state: "running", record_id: null }, 202);
      }
      return jsonResponse({
        enabled: true,
        provider_id: "openai-responses",
        retention_notice: "Answers are stored locally.",
        search: { budget_reservation_usd_micros: 1 },
      });
    }, { storage: new Map(storage), login });
    await harness.kronikaShowQuestion(harness.kronikaParseRoute("#/search"));
    return { bodies, harness };
  }

  const same = await recoveredContext("alice", "Synthetic question");
  assert.equal(same.bodies.length, 0);
  assert.match(same.harness.byId["kronika-question-status"].textContent, /Re-enter the same question/);
  same.harness.byId["kronika-question-input"].value = "Synthetic question";
  same.harness.byId["kronika-consent"].checked = true;
  await same.harness.kronikaSubmitQuestion({ preventDefault() {} });
  assert.equal(same.bodies.length, 1);
  assert.equal(same.bodies[0].client_request_id, bodiesA[0].client_request_id);
  assert.equal(same.bodies[0].prompt, "Synthetic question");
  assert.equal(same.bodies[0].consent_version, "kronika-research-v1");
  assert.equal(same.bodies[0].kind, "search");

  const changed = await recoveredContext("alice", "Different question");
  changed.harness.byId["kronika-question-input"].value = "Different question";
  changed.harness.byId["kronika-consent"].checked = true;
  await changed.harness.kronikaSubmitQuestion({ preventDefault() {} });
  assert.equal(changed.bodies.length, 1);
  assert.notEqual(changed.bodies[0].client_request_id, bodiesA[0].client_request_id);

  const otherLogin = await recoveredContext("bob", "Synthetic question");
  otherLogin.harness.byId["kronika-question-input"].value = "Synthetic question";
  otherLogin.harness.byId["kronika-consent"].checked = true;
  await otherLogin.harness.kronikaSubmitQuestion({ preventDefault() {} });
  assert.equal(otherLogin.bodies.length, 1);
  assert.notEqual(otherLogin.bodies[0].client_request_id, bodiesA[0].client_request_id);
});

function mediaItem(index, overrides = {}) {
  return Object.assign({
    record_id: `record-${index}`,
    kind: "media",
    display_title: `Media ${index}`,
    media_id: `media-${index}`,
    preview_location_id: `location-${index}`,
    timeline_entered_at_ms: 1_700_000_000_000 + index,
    content_category: "general",
  }, overrides);
}

function fireImage(node, type) {
  const listeners = (node.listeners.get(type) || []).slice();
  listeners.forEach((listener) => listener());
}

test("media cards use one lazy decorative preview and preserve their link names", async () => {
  const harness = createHarness(async (url) => {
    assert.equal(url.startsWith("/api/timeline"), true);
    return jsonResponse({
      items: [mediaItem(1)],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const card = harness.byId["kronika-timeline-results"].children[0];
  const title = card.children[1];
  assert.equal(title.href, "#/details/media-1");
  assert.equal(title.textContent, "Media 1");
  assert.equal(card.children[2].children[0].textContent, "Kind");
  assert.equal(card.children[2].children[1].textContent, "General");
  assert.equal(card.tag, "li");
  const images = card.querySelectorAll(".kronika-card__preview");
  assert.equal(images.length, 1);
  const image = images[0];
  assert.equal(image.tag, "img");
  assert.equal(image.getAttribute("alt"), "");
  assert.equal(image.getAttribute("aria-hidden"), "true");
  assert.equal(image.getAttribute("loading"), "lazy");
  assert.equal(image.getAttribute("decoding"), "async");
  assert.equal(image.width, 36);
  assert.equal(image.height, 36);
  assert.equal(image.getAttribute("data-kronika-preview"), "loading");
  assert.equal(image.src, harness.mediaGalleryPreviewUrl("media-1", "location-1"));
  assert.equal(image.getAttribute("tabindex"), null);
  assert.equal(harness.calls.length, 1);
});

test("question history and non-media cards never request previews", async () => {
  const harness = createHarness(async (url) => {
    if (url.startsWith("/api/research-requests")) {
      return jsonResponse({
        items: [{
          operation_id: "op-1",
          kind: "search",
          state: "saved",
          prompt: "Saved question",
          record_id: "record-q",
        }],
        total: 1,
        limit: 24,
        offset: 0,
      });
    }
    if (url.startsWith("/api/my/records")) {
      return jsonResponse({
        items: [{
          record_id: "record-s",
          kind: "search",
          display_title: "Saved question",
          visibility: "private",
        }],
        total: 1,
        limit: 24,
        offset: 0,
      });
    }
    return jsonResponse({
      items: [{
        record_id: "record-s",
        kind: "search",
        display_title: "Timeline question",
        timeline_entered_at_ms: 1_700_000_000_000,
        content_category: null,
      }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  await harness.kronikaLoadHistory(harness.kronikaParseRoute("#/history"));
  const historyResults = harness.byId["kronika-history-results"];
  assert.equal(historyResults.children.length, 1);
  assert.equal(historyResults.querySelectorAll(".kronika-card__preview").length, 0);
  await harness.kronikaLoadHistory(harness.kronikaParseRoute("#/history/records"));
  assert.equal(historyResults.children.length, 1);
  assert.equal(historyResults.querySelectorAll(".kronika-card__preview").length, 0);
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  assert.equal(
    harness.byId["kronika-timeline-results"].querySelectorAll(".kronika-card__preview").length,
    0,
  );
  assert.equal(harness.calls.some((call) => call.url.includes("gallery-preview")), false);
  assert.equal(harness.calls.some((call) => call.url.includes("/api/my/records")), false);
  assert.equal(harness.calls.every((call) => (
    call.url.startsWith("/api/research-requests")
    || call.url.startsWith("/api/timeline")
  )), true);
});

test("preview load reveals the image and every error retains the kind icon", async () => {
  const harness = createHarness(async () => jsonResponse({
    items: [mediaItem(1), mediaItem(2)],
    total: 2,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const cards = harness.byId["kronika-timeline-results"].children;
  const loaded = cards[0].querySelector(".kronika-card__preview");
  assert.equal(loaded.getAttribute("data-kronika-preview"), "loading");
  fireImage(loaded, "load");
  assert.equal(loaded.getAttribute("data-kronika-preview"), "ready");
  assert.equal(cards[0].querySelectorAll(".kronika-card__preview").length, 1);
  assert.equal((loaded.listeners.get("load") || []).length, 0);
  const failed = cards[1].querySelector(".kronika-card__preview");
  fireImage(failed, "error");
  assert.equal(failed.getAttribute("data-kronika-preview"), "fallback");
  assert.equal(failed.src, "");
  assert.equal((failed.listeners.get("error") || []).length, 0);
  assert.equal(cards[1].getAttribute("data-kronika-kind"), "general");
  assert.equal(cards[1].querySelector(".kronika-chronicle__title").textContent, "Media 2");
  assert.equal(cards[0].getAttribute("data-kronika-kind"), "general");
});

test("24 media cards require no media detail discovery or automatic retries", async () => {
  const items = Array.from({ length: 24 }, (_value, index) => mediaItem(index + 1));
  const harness = createHarness(async (url) => {
    assert.equal(url.startsWith("/api/timeline"), true);
    return jsonResponse({ items, total: 24, limit: 24, offset: 0 });
  });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const results = harness.byId["kronika-timeline-results"];
  assert.equal(results.children.length, 24);
  const images = results.querySelectorAll(".kronika-card__preview");
  assert.equal(images.length, 24);
  images.forEach((image, index) => {
    assert.equal(
      image.src,
      harness.mediaGalleryPreviewUrl(`media-${index + 1}`, `location-${index + 1}`),
    );
  });
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.timers.length, 0);
  fireImage(images[0], "error");
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.timers.length, 0);
  assert.equal(harness.calls.some((call) => call.url.includes("/api/media/")), false);
  assert.equal(harness.calls.some((call) => call.url.includes("/api/records/")), false);
});

test("navigation and identity changes retire previews and ignore late events", async () => {
  const harness = createHarness(async () => jsonResponse({
    items: [mediaItem(1)],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  harness.kronikaNoteIdentity();
  const results = harness.byId["kronika-timeline-results"];
  const late = results.children[0].querySelector(".kronika-card__preview");
  const lateLoad = late.listeners.get("load")[0];
  assert.equal(typeof lateLoad, "function");
  harness.location._hash = "#/gallery";
  await harness.kronikaFollowHash();
  assert.equal(late.src, "");
  assert.equal(late.parentNode, null);
  lateLoad();
  assert.notEqual(late.getAttribute("data-kronika-preview"), "ready");

  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const identityImage = results.children[0].querySelector(".kronika-card__preview");
  const identityLate = identityImage.listeners.get("load")[0];
  assert.equal(typeof identityLate, "function");
  harness.identityState.login = "bob";
  harness.kronikaNoteIdentity();
  assert.equal(results.children.length, 0);
  assert.equal(identityImage.src, "");
  identityLate();
  assert.notEqual(identityImage.getAttribute("data-kronika-preview"), "ready");
});

test("superseded Timeline and History requests cannot repaint a departed view", async () => {
  let timelineResolve = null;
  let questionCalls = 0;
  let firstQuestionResolve = null;
  let secondQuestionResolve = null;
  const harness = createHarness(async (url) => {
    if (url.startsWith("/api/timeline")) {
      return new Promise((resolve) => {
        timelineResolve = resolve;
      });
    }
    if (url.startsWith("/api/research-requests")) {
      questionCalls += 1;
      return new Promise((resolve) => {
        if (questionCalls === 1) firstQuestionResolve = resolve;
        else secondQuestionResolve = resolve;
      });
    }
    return jsonResponse({ items: [], total: 0, limit: 24, offset: 0 });
  });
  const pendingTimeline = harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  await settle();
  const timelineCall = harness.calls.find((call) => call.url.startsWith("/api/timeline"));
  harness.location._hash = "#/gallery";
  await harness.kronikaFollowHash();
  assert.equal(timelineCall.init.signal.aborted, true);
  timelineResolve(jsonResponse({
    items: [mediaItem(1)],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await pendingTimeline;
  assert.equal(harness.byId["kronika-timeline-results"].children.length, 0);

  const pendingQuestions = harness.kronikaLoadHistory(harness.kronikaParseRoute("#/history"));
  await settle();
  const questionCall = harness.calls.find((call) => call.url.startsWith("/api/research-requests"));
  const pendingFolded = harness.kronikaLoadHistory(harness.kronikaParseRoute("#/history/records"));
  assert.equal(questionCall.init.signal.aborted, true);
  firstQuestionResolve(jsonResponse({
    items: [{
      operation_id: "op-late",
      kind: "search",
      state: "saved",
      prompt: "Late question",
      record_id: "record-late",
    }],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await pendingQuestions;
  secondQuestionResolve(jsonResponse({
    items: [{
      operation_id: "op-current",
      kind: "search",
      state: "saved",
      prompt: "Current question",
      record_id: "record-current",
    }],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await pendingFolded;
  const historyResults = harness.byId["kronika-history-results"];
  assert.equal(historyResults.children.length, 1);
  assert.equal(historyResults.querySelector(".kronika-chronicle__title").textContent, "Current question");
  assert.equal(harness.calls.some((call) => call.url.includes("/api/my/records")), false);
});

test("cancelled dirty navigation preserves the current preview page", async () => {
  const harness = createHarness(async () => jsonResponse({
    items: [mediaItem(1)],
    total: 1,
    limit: 24,
    offset: 0,
  }), { dirty: true, discard: false });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const results = harness.byId["kronika-timeline-results"];
  const image = results.querySelector(".kronika-card__preview");
  fireImage(image, "load");
  assert.equal(image.getAttribute("data-kronika-preview"), "ready");
  harness.location._hash = "#/gallery";
  await harness.kronikaFollowHash();
  assert.equal(harness.location.hash, "#/timeline");
  assert.equal(results.children.length, 1);
  assert.equal(image.parentNode, results.children[0].children[0]);
  assert.equal(image.getAttribute("data-kronika-preview"), "ready");
  assert.notEqual(image.src, "");
  assert.equal(harness.calls[0].init.signal.aborted, false);
  assert.equal(harness.catalogLoads.length, 0);
});

test("visibility changes keep card previews and in-flight lists while pagehide retires", async () => {
  let secondResolve = null;
  let timelineCalls = 0;
  const harness = createHarness(async (url) => {
    assert.equal(url.startsWith("/api/timeline"), true);
    timelineCalls += 1;
    if (timelineCalls === 1) {
      return jsonResponse({
        items: [mediaItem(1)],
        total: 2,
        limit: 24,
        offset: 0,
      });
    }
    return new Promise((resolve) => {
      secondResolve = resolve;
    });
  });
  harness.kronikaStartNavigation();
  await settle();
  const results = harness.byId["kronika-timeline-results"];
  const image = results.querySelector(".kronika-card__preview");
  fireImage(image, "load");
  assert.equal(image.getAttribute("data-kronika-preview"), "ready");
  const firstCall = harness.calls[0];
  const fireVisibility = () => {
    harness.documentListeners
      .filter((entry) => entry.type === "visibilitychange")
      .forEach((entry) => entry.listener());
  };

  harness.document.hidden = true;
  fireVisibility();
  assert.equal(image.getAttribute("data-kronika-preview"), "ready");
  assert.notEqual(image.src, "");
  assert.equal(image.parentNode, results.children[0].children[0]);
  assert.equal(firstCall.init.signal.aborted, false);
  harness.document.hidden = false;
  fireVisibility();
  assert.equal(image.getAttribute("data-kronika-preview"), "ready");
  assert.equal(results.children.length, 1);

  const pending = harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline?offset=24"));
  await settle();
  assert.equal(timelineCalls, 2);
  const secondCall = harness.calls.at(-1);
  harness.document.hidden = true;
  fireVisibility();
  assert.equal(secondCall.init.signal.aborted, false);
  harness.document.hidden = false;
  fireVisibility();
  assert.equal(secondCall.init.signal.aborted, false);

  secondResolve(jsonResponse({
    items: [mediaItem(2)],
    total: 2,
    limit: 24,
    offset: 24,
  }));
  await pending;
  const secondImage = results.querySelector(".kronika-card__preview");
  fireImage(secondImage, "load");
  assert.equal(secondImage.getAttribute("data-kronika-preview"), "ready");

  const pageHideListeners = harness.windowListeners.filter((entry) => entry.type === "pagehide");
  assert.equal(pageHideListeners.length, 2);
  pageHideListeners.forEach((entry) => entry.listener());
  assert.equal(secondImage.src, "");
  assert.equal(secondImage.parentNode, null);
  assert.equal(secondCall.init.signal.aborted, true);
});

test("thumbnail CSS changes only the Timeline and History icon slots", () => {
  const selectorLines = STYLES_SOURCE.split("\n").filter((line) => (
    line.includes(".kronika-card__preview") || line.includes("data-kronika-preview")
  ));
  assert.equal(selectorLines.length >= 4, true);
  selectorLines.forEach((line) => {
    const selectors = line.replace(/\s*\{\s*$/, "").split(",");
    selectors.forEach((selector) => {
      const trimmed = selector.trim();
      if (!trimmed || trimmed.startsWith("/*")) return;
      assert.match(trimmed, /^#kronika-(timeline|history)-results /);
    });
  });
  assert.match(STYLES_SOURCE, /\.kronika-card \{[^}]*padding: 16px 16px 14px 64px;/s);
  assert.match(STYLES_SOURCE, /\.kronika-card::before \{[^}]*width: 36px;[^}]*height: 36px;/s);
  assert.equal(STYLES_SOURCE.includes("#kronika-review-results .kronika-card__preview"), false);
});

test("Timeline renders a compact chronicle with distinct status and visibility", async () => {
  const harness = createHarness(async () => jsonResponse({
    items: [{
      item_id: "question:op-1",
      kind: "search",
      display_title: "Saved question",
      record_id: "record-1",
      operation_id: "op-1",
      created_at_ms: Date.UTC(2026, 0, 2, 3, 4, 5),
      status: "saved",
      visibility: "private",
      publication_state: "unpublished",
    }],
    total: 1,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const row = harness.byId["kronika-timeline-results"].children[0];
  assert.equal(row.tag, "li");
  assert.equal(row.querySelector(".kronika-chronicle__status").children[0].textContent, "✅");
  assert.equal(row.querySelector(".kronika-chronicle__status").children[0].getAttribute("aria-hidden"), "true");
  assert.equal(row.querySelector(".kronika-chronicle__status").children[1].textContent, "Saved");
  assert.equal(row.querySelector(".kronika-chronicle__status").children[1].className.includes("visually-hidden"), true);
  assert.equal(row.querySelector(".kronika-chronicle__visibility").children[0].textContent, "🔒");
  assert.equal(row.querySelector(".kronika-chronicle__visibility").children[1].textContent, "Private · Unpublished");
  assert.equal(row.querySelector(".kronika-chronicle__visibility").children[1].className.includes("visually-hidden"), true);
  assert.equal(row.querySelector("time"), null);
  assert.equal(row.children.length, 5);
});

test("Timeline rows link media, saved records and unfinished requests", async () => {
  const harness = createHarness(async () => jsonResponse({
    items: [
      { item_id: "media:m", kind: "media", media_id: "media-1", display_title: "Clip", content_category: "general", created_at_ms: 1, status: "not_requested", visibility: "private", publication_state: "unpublished" },
      { item_id: "question:saved", kind: "search", record_id: "record-1", operation_id: "saved", display_title: "Saved", created_at_ms: 2, status: "saved", visibility: "family", publication_state: "approved" },
      { item_id: "question:live", kind: "research", operation_id: "live", display_title: "Live", created_at_ms: 3, status: "running", visibility: "private", publication_state: "unpublished" },
    ],
    total: 3,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const links = harness.byId["kronika-timeline-results"].querySelectorAll(".kronika-chronicle__title");
  assert.deepEqual(links.map((link) => link.href), [
    "#/details/media-1",
    "#/records/record-1",
    "#/requests/live",
  ]);
});

test("Timeline text stays inert and list loading performs no detail fan-out", async () => {
  const harness = createHarness(async (url) => {
    assert.equal(url.startsWith("/api/timeline"), true);
    return jsonResponse({
      items: [{
        item_id: "question:op",
        kind: "search",
        display_title: "<script>alert(1)</script>",
        record_id: "record-1",
        operation_id: "op",
        created_at_ms: 1,
        status: "saved",
        visibility: "private",
        publication_state: "unpublished",
      }],
      total: 1,
      limit: 24,
      offset: 0,
    });
  });
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const title = harness.byId["kronika-timeline-results"].querySelector(".kronika-chronicle__title");
  assert.equal(title.textContent, "<script>alert(1)</script>");
  assert.equal(harness.calls.length, 1);
  assert.equal(harness.calls.some((call) => call.url.includes("/api/records/")), false);
  assert.equal(harness.calls.some((call) => call.url.includes("/api/research-requests/")), false);
});

test("Timeline filters preserve route state and reset pagination", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  const route = harness.kronikaParseRoute("#/timeline?filter=media&q=clip&status=failed&visibility=private&publication=unpublished&offset=24");
  assert.equal(route.filter, "media");
  assert.equal(route.q, "clip");
  assert.equal(route.status, "failed");
  assert.equal(route.visibility, "private");
  assert.equal(route.publication, "unpublished");
  assert.equal(route.offset, 24);
  assert.equal(harness.kronikaParseRoute("#/timeline?status=nope").name, "unavailable");
});

function appendTimelineMenu(harness, id, panelId, options) {
  const menu = new El("div");
  menu.className = "kronika-menu";
  const trigger = new El("button", id);
  trigger.className = "kronika-menu__trigger";
  trigger.value = options[0].value;
  trigger.setAttribute("aria-expanded", "false");
  trigger.setAttribute("aria-controls", panelId);
  const glyph = new El("span");
  glyph.className = "kronika-menu__glyph";
  glyph.textContent = options[0].glyph;
  const value = new El("span");
  value.className = "kronika-menu__value";
  value.textContent = options[0].label;
  trigger.appendChild(glyph);
  trigger.appendChild(value);
  const panel = new El("ul", panelId);
  panel.className = "kronika-menu__panel";
  panel.hidden = true;
  options.forEach((option, index) => {
    const button = new El("button");
    button.className = "kronika-menu__option";
    button.setAttribute("data-value", option.value);
    button.setAttribute("data-glyph", option.glyph);
    button.setAttribute("data-label", option.label);
    button.setAttribute("aria-selected", index === 0 ? "true" : "false");
    panel.appendChild(button);
  });
  menu.appendChild(trigger);
  menu.appendChild(panel);
  harness.byId["kronika-timeline-filters"].appendChild(menu);
  harness.byId[id] = trigger;
  harness.byId[panelId] = panel;
  return { trigger, panel };
}

function keyEvent(key) {
  return { key, preventDefault() {} };
}

test("Timeline filters use a keyboard listbox and reset the page", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  const kind = appendTimelineMenu(harness, "kronika-timeline-kind", "kronika-timeline-kind-list", [
    { value: "all", glyph: "🌐", label: "All" },
    { value: "search", glyph: "💬", label: "Search" },
  ]);
  const search = new El("input", "kronika-timeline-search");
  search.value = "clip";
  harness.byId["kronika-timeline-search"] = search;
  const clear = new El("button", "kronika-timeline-search-clear");
  harness.byId["kronika-timeline-search-clear"] = clear;
  harness.byId["kronika-timeline-filters"].appendChild(search);
  harness.byId["kronika-timeline-filters"].appendChild(clear);
  harness.location._hash = "#/timeline?offset=24";
  harness.kronikaStartNavigation();
  await settle();
  search.value = "clip";
  kind.trigger.listeners.get("click")[0]();
  assert.equal(kind.panel.hidden, false);
  assert.equal(kind.trigger.getAttribute("aria-expanded"), "true");
  assert.equal(kind.panel.children[0].focused, true);
  kind.panel.listeners.get("keydown")[0](keyEvent("ArrowDown"));
  assert.equal(kind.panel.children[1].focused, true);
  assert.equal(kind.panel._kronikaIndex, 1);
  kind.panel.listeners.get("keydown")[0](keyEvent("Enter"));
  await settle();
  assert.equal(kind.trigger.value, "search");
  assert.equal(kind.trigger.querySelector(".kronika-menu__value").textContent, "Search");
  assert.equal(kind.panel.hidden, true);
  assert.equal(kind.trigger.focused, true);
  assert.equal(harness.location.hash, "#/timeline?filter=search&q=clip");
  kind.trigger.listeners.get("keydown")[0](keyEvent("ArrowDown"));
  assert.equal(kind.panel.hidden, false);
  kind.panel.listeners.get("keydown")[0](keyEvent("Escape"));
  assert.equal(kind.panel.hidden, true);
  assert.equal(kind.trigger.focused, true);
  kind.trigger.listeners.get("click")[0]();
  const outside = new El("div");
  harness.documentListeners.find((entry) => entry.type === "click").listener({ target: outside });
  assert.equal(kind.panel.hidden, true);
  clear.listeners.get("click")[0]();
  await settle();
  assert.equal(search.value, "");
  assert.equal(harness.location.hash, "#/timeline?filter=search");
});

test("Timeline listbox Enter and Space commit the option focused by Tab", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  const kind = appendTimelineMenu(harness, "kronika-timeline-kind", "kronika-timeline-kind-list", [
    { value: "all", glyph: "🌐", label: "All" },
    { value: "search", glyph: "💬", label: "Search" },
    { value: "media", glyph: "🎞️", label: "Media" },
  ]);
  const status = appendTimelineMenu(harness, "kronika-timeline-status-filter", "kronika-timeline-status-list", [
    { value: "", glyph: "✨", label: "Any status" },
    { value: "failed", glyph: "⚠️", label: "Failed" },
  ]);
  harness.location._hash = "#/timeline?offset=24";
  harness.kronikaStartNavigation();
  await settle();
  kind.trigger.listeners.get("click")[0]();
  assert.equal(kind.panel._kronikaIndex, 0);
  kind.panel.children[2].focus();
  assert.equal(kind.panel.children[2].focused, true);
  assert.equal(kind.panel._kronikaIndex, 2);
  kind.panel.listeners.get("keydown")[0](keyEvent("Enter"));
  await settle();
  assert.equal(kind.trigger.value, "media");
  assert.equal(kind.panel.hidden, true);
  assert.equal(kind.trigger.focused, true);
  assert.equal(harness.location.hash, "#/timeline?filter=media");
  kind.trigger.listeners.get("click")[0]();
  kind.panel.children[1].focus();
  kind.panel.listeners.get("keydown")[0](keyEvent("ArrowDown"));
  assert.equal(kind.panel._kronikaIndex, 2);
  kind.panel.listeners.get("keydown")[0](keyEvent(" "));
  await settle();
  assert.equal(kind.trigger.value, "media");
  kind.trigger.listeners.get("keydown")[0](keyEvent("Enter"));
  assert.equal(status.panel.hidden, true);
  status.trigger.listeners.get("click")[0]();
  assert.equal(kind.panel.hidden, true);
  assert.equal(status.panel.hidden, false);
  status.panel.children[1].focus();
  status.panel.listeners.get("keydown")[0](keyEvent("Escape"));
  assert.equal(status.panel.hidden, true);
  assert.equal(status.trigger.focused, true);
  status.trigger.listeners.get("click")[0]();
  const outside = new El("div");
  harness.documentListeners.find((entry) => entry.type === "click").listener({ target: outside });
  assert.equal(status.panel.hidden, true);
});

test("grouped navigation keeps routes, current state and capability hiding", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  const nav = harness.byId["kronika-nav"];
  assert.equal(nav.children.length, 2);
  assert.equal(nav.children[0].className, "kronika-nav__browse");
  assert.equal(nav.children[1].className, "kronika-nav__actions");
  assert.deepEqual(
    nav.children[0].children.map((link) => link.getAttribute("data-kronika-nav")),
    ["timeline", "gallery", "history"],
  );
  assert.deepEqual(
    nav.children[1].children.map((link) => link.getAttribute("data-kronika-nav")),
    ["search", "research", "review"],
  );
  harness.kronikaStartNavigation();
  await settle();
  assert.equal(nav.hidden, false);
  assert.equal(harness.byId["kronika-nav-timeline"].getAttribute("aria-current"), "page");
  assert.equal(harness.byId["kronika-nav-review"].hidden, false);
  harness.byId["kronika-nav-search"].listeners.get("click")[0]();
  harness.location.hash = "#/search";
  await settle();
  assert.equal(harness.byId["kronika-nav-search"].getAttribute("aria-current"), "page");
  assert.equal(harness.byId["kronika-nav-timeline"].getAttribute("aria-current"), null);
  assert.equal(harness.byId["kronika-nav-search"].focused, true);

  const withoutReview = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }), {
    capabilities: ["research.run"],
  });
  withoutReview.kronikaStartNavigation();
  await settle();
  assert.equal(withoutReview.byId["kronika-nav-review"].hidden, true);

  const publicHarness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  publicHarness.identityState.audience = "public_published";
  publicHarness.kronikaStartNavigation();
  await settle();
  assert.equal(publicHarness.byId["kronika-nav"].hidden, true);
  assert.equal(publicHarness.byId["kronika-timeline"].hidden, true);
});

test("Timeline search edits chrome only until an explicit submit or clear", async () => {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  appendTimelineMenu(harness, "kronika-timeline-kind", "kronika-timeline-kind-list", [
    { value: "all", glyph: "🌐", label: "All" },
    { value: "media", glyph: "🎞️", label: "Media" },
  ]);
  appendTimelineMenu(harness, "kronika-timeline-status-filter", "kronika-timeline-status-list", [
    { value: "", glyph: "✨", label: "Any status" },
    { value: "failed", glyph: "⚠️", label: "Failed" },
  ]);
  appendTimelineMenu(harness, "kronika-timeline-visibility", "kronika-timeline-visibility-list", [
    { value: "", glyph: "✨", label: "Any visibility" },
    { value: "private", glyph: "🔒", label: "Private" },
  ]);
  appendTimelineMenu(harness, "kronika-timeline-publication", "kronika-timeline-publication-list", [
    { value: "", glyph: "✨", label: "Any publication" },
    { value: "unpublished", glyph: "🔒", label: "Unpublished" },
  ]);
  const search = new El("input", "kronika-timeline-search");
  const clear = new El("button", "kronika-timeline-search-clear");
  const submit = new El("button", "kronika-timeline-search-submit");
  search.ownerDocument = harness.document;
  clear.ownerDocument = harness.document;
  submit.ownerDocument = harness.document;
  harness.byId["kronika-timeline-search"] = search;
  harness.byId["kronika-timeline-search-clear"] = clear;
  harness.byId["kronika-timeline-search-submit"] = submit;
  harness.location._hash = "#/timeline?filter=media&status=failed&visibility=private&publication=unpublished&offset=24";
  harness.kronikaStartNavigation();
  await settle();
  const afterLoad = harness.calls.length;
  search.value = "  clip  ";
  search.listeners.get("input")[0]();
  assert.equal(clear.hidden, false);
  assert.equal(harness.calls.length, afterLoad);
  assert.equal(harness.catalogLoads.length, 0);
  harness.byId["kronika-timeline-filters"].listeners.get("submit")[0]({ preventDefault() {} });
  await settle();
  assert.equal(
    harness.location.hash,
    "#/timeline?filter=media&q=clip&status=failed&visibility=private&publication=unpublished",
  );
  assert.equal(submit.focused, true);
  assert.equal(harness.catalogLoads.length, 0);
  const sameHashCalls = harness.calls.length;
  harness.byId["kronika-timeline-filters"].listeners.get("submit")[0]({ preventDefault() {} });
  await settle();
  assert.equal(harness.calls.length > sameHashCalls, true);
  clear.listeners.get("click")[0]();
  await settle();
  assert.equal(search.value, "");
  assert.equal(clear.hidden, true);
  assert.equal(search.focused, true);
  assert.equal(
    harness.location.hash,
    "#/timeline?filter=media&status=failed&visibility=private&publication=unpublished",
  );
  search.value = "draft";
  search.focus();
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline?q=server"));
  assert.equal(search.value, "draft");
  assert.equal(harness.catalogLoads.length, 0);
});

test("shell source separates browsing links, timeline search and column alignment", () => {
  const headerEnd = INDEX_SOURCE.indexOf("</header>");
  const navStart = INDEX_SOURCE.indexOf('<nav class="kronika-nav"');
  const mainStart = INDEX_SOURCE.indexOf('<main id="main"');
  assert.equal(INDEX_SOURCE.match(/<nav class="kronika-nav"/g).length, 1);
  assert.ok(headerEnd < navStart && navStart < mainStart);
  assert.match(INDEX_SOURCE, /class="kronika-nav__browse" role="group" aria-label="Browse"/);
  assert.match(INDEX_SOURCE, /class="kronika-nav__actions" role="group" aria-label="Actions"/);
  const filters = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf('id="kronika-timeline-filters"'),
    INDEX_SOURCE.indexOf('id="kronika-timeline-status"'),
  );
  const order = [
    'id="kronika-timeline-publication"',
    'id="kronika-timeline-search"',
    'id="kronika-timeline-kind"',
    'id="kronika-timeline-status-filter"',
    'id="kronika-timeline-visibility"',
  ].map((id) => filters.indexOf(id));
  assert.deepEqual(order, [...order].sort((left, right) => left - right));
  assert.ok(order.every((index) => index >= 0));
  assert.match(filters, /role="search" aria-label="Search timeline"/);
  assert.match(filters, /placeholder="Search timeline"/);
  assert.match(filters, /aria-label="Search timeline titles and questions"/);
  assert.match(filters, /id="kronika-timeline-search-submit"[^>]*type="submit"[^>]*aria-label="Search timeline"/);
  assert.match(filters, /class="header-search__clear-slot"/);
  assert.equal(filters.match(/<li role="presentation"><button class="kronika-menu__option"/g).length, 22);
  assert.match(filters, /data-value="legacy_published" data-glyph="👁️"/);
  assert.doesNotMatch(filters, /data-glyph="🕰️"/);
  assert.match(INDEX_SOURCE, /id="kronika-nav-timeline"[^>]*>[\s\S]*?🕰️/);
  assert.match(INDEX_SOURCE, /class="kronika-hero__icon" aria-hidden="true">🕰️/);
  assert.match(APP_SOURCE, /publication_state === "legacy_published"\) return "👁️"/);
  assert.match(STYLES_SOURCE, /\.kronika-chronicle__title,\s*\.kronika-chronicle__title:hover,\s*\.kronika-chronicle__title:focus,\s*\.kronika-chronicle__title:focus-visible \{\s*text-decoration: none;/);
  assert.match(STYLES_SOURCE, /\.kronika-chronicle__title:focus-visible \{\s*outline: 2px solid var\(--accent\);/);
  assert.match(STYLES_SOURCE, /@media \(min-width: 1100px\) \{\s*#kronika-timeline \.kronika-chronicle-filters,\s*#kronika-timeline \.kronika-chronicle-header,\s*#kronika-timeline \.kronika-chronicle__item \{\s*grid-template-columns: 4\.75rem minmax\(0, 1fr\) 9\.5rem 9\.5rem 10\.5rem;/);
  assert.match(STYLES_SOURCE, /#kronika-timeline-publication\.kronika-menu__trigger \{\s*border-color: var\(--accent-border\);/);
  assert.match(STYLES_SOURCE, /\.header-search__clear-slot \{\s*display: flex;\s*flex: 0 0 44px;/);
  assert.match(STYLES_SOURCE, /\.kronika-nav__actions a \{\s*[^}]*border-radius: 0;/);
  assert.doesNotMatch(STYLES_SOURCE, /#kronika-timeline \{\s*overflow-x: hidden;/);
});

test("Timeline chronicle uses glyphs with accessible text and a quiet hover", async () => {
  assert.match(
    STYLES_SOURCE,
    /\.kronika-chronicle-header,\s*\.kronika-chronicle__item \{\s*display: grid;\s*grid-template-columns: 4\.75rem minmax\(0, 1fr\) 5\.5rem 4\.5rem 6\.25rem;\s*column-gap: 20px;/,
  );
  assert.match(STYLES_SOURCE, /\.kronika-chronicle-header > span \{\s*white-space: nowrap;/);
  assert.match(STYLES_SOURCE, /\.kronika-chronicle__item \{\s*[^}]*transition: background-color 160ms ease, box-shadow 160ms ease;/);
  assert.match(
    STYLES_SOURCE,
    /@media \(prefers-reduced-motion: reduce\) \{\s*\.kronika-chronicle__item,\s*\.kronika-menu__panel \{\s*animation: none;\s*transition: none;/,
  );
  assert.match(
    STYLES_SOURCE,
    /@media \(max-width: 720px\) \{[\s\S]*\.kronika-chronicle__item \{\s*grid-template-columns: 36px minmax\(0, 1fr\);[\s\S]*\.kronika-chronicle__kind,\s*\.kronika-chronicle__status,\s*\.kronika-chronicle__visibility \{\s*grid-column: 2;/,
  );
  assert.equal(STYLES_SOURCE.includes(".kronika-chronicle__added"), false);
  const harness = createHarness(async () => jsonResponse({
    items: [
      { item_id: "media:1", kind: "media", media_id: "1", display_title: "Quiet", created_at_ms: 1, status: "not_requested", visibility: "private", publication_state: "unpublished" },
      { item_id: "media:2", kind: "media", media_id: "2", display_title: "Working", created_at_ms: 2, status: "analyzing", visibility: "private", publication_state: "unpublished" },
      { item_id: "media:3", kind: "media", media_id: "3", display_title: "Done", created_at_ms: 3, status: "analyzed", visibility: "family", publication_state: "approved" },
      { item_id: "media:4", kind: "media", media_id: "4", display_title: "Broken", created_at_ms: 4, status: "failed", visibility: "private", publication_state: "withdrawn" },
      { item_id: "question:5", kind: "search", display_title: "Stopped", created_at_ms: 5, status: "cancelled", visibility: "family", publication_state: "legacy_published" },
    ],
    total: 5,
    limit: 24,
    offset: 0,
  }));
  await harness.kronikaLoadTimeline(harness.kronikaParseRoute("#/timeline"));
  const rows = harness.byId["kronika-timeline-results"].children;
  const signal = (row) => [
    row.querySelector(".kronika-chronicle__status").children[0].textContent,
    row.querySelector(".kronika-chronicle__status").children[1].textContent,
    row.querySelector(".kronika-chronicle__visibility").children[0].textContent,
    row.querySelector(".kronika-chronicle__visibility").children[1].textContent,
  ];
  assert.deepEqual(signal(rows[0]), ["💤", "Unanalysed", "🔒", "Private · Unpublished"]);
  assert.deepEqual(signal(rows[1]), ["⏳", "Analysing", "🔒", "Private · Unpublished"]);
  assert.deepEqual(signal(rows[2]), ["✅", "Analysed", "🏠", "Household · Approved"]);
  assert.deepEqual(signal(rows[3]), ["⚠️", "Analysis failed", "↩", "Private · Withdrawn"]);
  assert.deepEqual(signal(rows[4]), ["🚫", "Cancelled", "👁️", "Household · Legacy publication"]);
  rows.forEach((row) => {
    assert.equal(row.querySelector("time"), null);
    assert.equal(row.querySelector(".kronika-chronicle__status").children[0].getAttribute("aria-hidden"), "true");
    assert.equal(row.querySelector(".kronika-chronicle__visibility").children[1].className.includes("visually-hidden"), true);
  });
});

test("History is one Questions chronicle and the records address folds back", async () => {
  assert.doesNotMatch(INDEX_SOURCE, /id="kronika-history-records-tab"|href="#\/history\/records"/);
  assert.match(INDEX_SOURCE, /kronika-history-header" aria-hidden="true"/);
  assert.match(INDEX_SOURCE, /<ol id="kronika-history-results" class="kronika-chronicle"/);
  assert.match(INDEX_SOURCE, /Your own questions, answers and saved records/);
  const header = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf("kronika-history-header"),
    INDEX_SOURCE.indexOf('id="kronika-history-results"'),
  );
  assert.equal(header.includes("Added"), false);
  assert.match(header, /Question[\s\S]*Kind[\s\S]*Status[\s\S]*Visibility/);
  assert.match(
    STYLES_SOURCE,
    /#kronika-history \.kronika-history-header,\s*#kronika-history \.kronika-chronicle__item \{\s*grid-template-columns: minmax\(0, 1fr\) 5\.5rem 4\.5rem 6\.25rem;/,
  );
  assert.match(
    STYLES_SOURCE,
    /@media \(max-width: 720px\) \{\s*#kronika-history \.kronika-history-header \{\s*display: none;[\s\S]*#kronika-history \.kronika-chronicle__item \{\s*grid-template-columns: minmax\(0, 1fr\);/,
  );
  assert.match(STYLES_SOURCE, /#kronika-history \{\s*overflow-x: hidden;/);
  assert.match(
    STYLES_SOURCE,
    /\.kronika-chronicle__item \{\s*[^}]*transition: background-color 160ms ease, box-shadow 160ms ease;/,
  );
  assert.match(
    STYLES_SOURCE,
    /@media \(prefers-reduced-motion: reduce\) \{\s*\.kronika-chronicle__item,\s*\.kronika-menu__panel \{\s*animation: none;\s*transition: none;/,
  );
  const route = harnessRoute("#/history/records?offset=24");
  assert.equal(route.name, "history");
  assert.equal(route.path, "/history");
  assert.equal(route.offset, 24);
});

function harnessRoute(hash) {
  const harness = createHarness(async () => jsonResponse({ items: [], total: 0, limit: 24, offset: 0 }));
  return harness.kronikaParseRoute(hash);
}

test("History question rows keep links, glyphs and private text", async () => {
  const harness = createHarness(async (url) => {
    assert.equal(url.startsWith("/api/research-requests"), true);
    assert.equal(url.includes("/api/my/records"), false);
    return jsonResponse({
      items: [
        { operation_id: "op-saved", kind: "search", state: "saved", prompt: "Saved question", record_id: "record-saved" },
        { operation_id: "op-live", kind: "research", state: "running", prompt: "Live question", record_id: null },
        {
          operation_id: "op-shared",
          kind: "search",
          state: "saved",
          prompt: "Shared question",
          record_id: "record-shared",
          visibility: "family",
          publication_state: "approved",
        },
      ],
      total: 3,
      limit: 24,
      offset: 0,
    });
  });
  harness.location._hash = "#/history/records?offset=24";
  await harness.kronikaFollowHash();
  assert.equal(harness.location.hash, "#/history?offset=24");
  assert.equal(harness.byId["kronika-history-heading"].focused, true);
  assert.equal(harness.calls.length, 1);
  assert.match(harness.calls[0].url, /\/api\/research-requests\?/);
  assert.match(harness.calls[0].url, /offset=24/);
  const rows = harness.byId["kronika-history-results"].children;
  assert.equal(rows.length, 3);
  rows.forEach((row) => {
    assert.equal(row.tag, "li");
    assert.equal(row.className.includes("kronika-chronicle__item"), true);
    assert.equal(row.children.length, 4);
    assert.equal(row.querySelector("time"), null);
    assert.equal(row.querySelector(".kronika-chronicle__kind").children[0].textContent, "Kind");
    assert.equal(row.querySelector(".kronika-chronicle__kind").children[0].className.includes("visually-hidden"), true);
    assert.equal(row.querySelector(".kronika-chronicle__status").children[0].getAttribute("aria-hidden"), "true");
    assert.equal(row.querySelector(".kronika-chronicle__visibility").children[1].className.includes("visually-hidden"), true);
  });
  assert.equal(rows[0].querySelector(".kronika-chronicle__title").textContent, "Saved question");
  assert.equal(rows[0].querySelector(".kronika-chronicle__title").href, "#/records/record-saved");
  assert.equal(rows[0].querySelector(".kronika-chronicle__kind").children[1].textContent, "Search");
  assert.equal(rows[0].querySelector(".kronika-chronicle__status").children[0].textContent, "✅");
  assert.equal(rows[0].querySelector(".kronika-chronicle__status").children[1].textContent, "Saved");
  assert.equal(rows[0].querySelector(".kronika-chronicle__visibility").children[0].textContent, "🔒");
  assert.equal(rows[0].querySelector(".kronika-chronicle__visibility").children[1].textContent, "Private");
  assert.equal(rows[1].querySelector(".kronika-chronicle__title").href, "#/requests/op-live");
  assert.equal(rows[1].querySelector(".kronika-chronicle__kind").children[1].textContent, "Research");
  assert.equal(rows[1].querySelector(".kronika-chronicle__status").children[0].textContent, "⏳");
  assert.equal(rows[1].querySelector(".kronika-chronicle__status").children[1].textContent, "Working");
  assert.equal(rows[2].querySelector(".kronika-chronicle__visibility").children[0].textContent, "🏠");
  assert.equal(rows[2].querySelector(".kronika-chronicle__visibility").children[1].textContent, "Household · Approved");
});
