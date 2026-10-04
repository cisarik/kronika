const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const INDEX_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const INDEX_SOURCE = fs.readFileSync(INDEX_PATH, "utf8");
const SHELL_START = APP_SOURCE.indexOf("/* KRONIKA_SHELL_START */");
const SHELL_END = APP_SOURCE.indexOf("/* KRONIKA_SHELL_END */");
const SHELL_SOURCE = APP_SOURCE.slice(SHELL_START, SHELL_END);

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
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  focus() {
    this.focused = true;
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
  if (selector === "small") return node.tag === "small";
  if (selector.startsWith("#") && !selector.includes(" ")) return node.id === selector.slice(1);
  return false;
}

function createHarness(fetchImpl, options = {}) {
  const byId = {};
  const documentListeners = [];
  const hashListeners = [];
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
  ["timeline", "gallery", "history", "search", "research", "review"].forEach((name) => {
    const link = new El("a", `kronika-nav-${name}`);
    link.setAttribute("data-kronika-nav", name);
    byId["kronika-nav"].appendChild(link);
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
    createElement: (tag) => new El(tag),
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
  };
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
    document,
    location,
    window: {
      addEventListener: (type, listener) => {
        if (type === "hashchange") hashListeners.push(listener);
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
    framenestMutationHeaders: (headers) => Object.assign({ "X-FrameNest-Request": "1" }, headers || {}),
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
  };
  vm.createContext(context);
  vm.runInContext(`"use strict";\n${SHELL_SOURCE}`, context);
  return context;
}

async function settle() {
  for (let index = 0; index < 20; index += 1) await Promise.resolve();
}

test("shell markup keeps the gallery and sandboxed answer frame", () => {
  assert.match(INDEX_SOURCE, /<title>Kronika<\/title>/);
  assert.match(INDEX_SOURCE, /class="brand-mark"[^>]*>FN</);
  assert.match(INDEX_SOURCE, /class="brand-wordmark">Kronika</);
  assert.match(INDEX_SOURCE, /href="#\/timeline"/);
  assert.match(INDEX_SOURCE, /href="#\/gallery"/);
  assert.match(INDEX_SOURCE, /id="catalog-browser"/);
  assert.match(INDEX_SOURCE, /id="kronika-document-frame"[^>]*sandbox=""/);
  assert.match(INDEX_SOURCE, /referrerpolicy="no-referrer"/);
  assert.match(APP_SOURCE, /FrameNestCompanionWeb\.onOpenDetails/);
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
  const titles = harness.byId["kronika-timeline-results"].children.map((card) => card.textContent || card.children[0].textContent);
  assert.deepEqual(titles, ["Approved title"]);
  assert.equal(harness.calls.every((call) => call.url.startsWith("/api/timeline")), true);
  assert.equal(harness.byId["kronika-timeline-status"].textContent.includes("Hidden"), false);
});

test("personal history keeps failed requests out of the timeline", async () => {
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
  const text = harness.byId["kronika-history-results"].children.map((card) => card.children[1].textContent);
  assert.equal(text.some((line) => line.includes("failed")), true);
  assert.equal(text.some((line) => line.includes("cancelled")), true);
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
    call.init.headers["X-FrameNest-Request"] === "1"
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
  assert.equal(harness.byId["kronika-timeline-results"].children[0].children[0].textContent, "Kept title");
  assert.match(harness.byId["kronika-timeline-status"].textContent, /not refreshed/);
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
