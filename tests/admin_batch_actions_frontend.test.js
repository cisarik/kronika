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

function extractFunction(source, name) {
  const markers = [`async function ${name}(`, `function ${name}(`];
  const start = markers
    .map((marker) => source.indexOf(marker))
    .find((position) => position !== -1);
  assert.notEqual(start, undefined, `missing production function ${name}`);
  const headerOpen = source.indexOf("(", start);
  let depth = 0;
  let bodyOpen = -1;
  for (let index = headerOpen; index < source.length; index += 1) {
    if (source[index] === "(") depth += 1;
    if (source[index] === ")") depth -= 1;
    if (depth === 0) {
      bodyOpen = source.indexOf("{", index);
      break;
    }
  }
  assert.notEqual(bodyOpen, -1, `missing body for ${name}`);
  depth = 0;
  for (let index = bodyOpen; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production function ${name}`);
}

function extractVariable(source, name) {
  const markers = [`const ${name} = `, `let ${name} = `];
  const start = markers
    .map((marker) => source.indexOf(marker))
    .find((position) => position !== -1);
  assert.notEqual(start, undefined, `missing production variable ${name}`);
  let depth = 0;
  for (let index = start; index < source.length; index += 1) {
    const char = source[index];
    if (char === "{" || char === "[" || char === "(") depth += 1;
    if (char === "}" || char === "]" || char === ")") depth -= 1;
    if (char === ";" && depth === 0) return source.slice(start, index + 1);
  }
  throw new Error(`unterminated production variable ${name}`);
}

function deferred() {
  let resolve;
  const promise = new Promise((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

async function flush(times = 25) {
  for (let index = 0; index < times; index += 1) {
    await new Promise((resolve) => setTimeout(resolve, 0));
  }
}

class FakeElement {
  constructor(tagName = "div") {
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.className = "";
    this.textContent = "";
    this.type = "";
    this.disabled = false;
    this.hidden = false;
    this.checked = false;
    this.indeterminate = false;
    this.title = "";
    this.value = "";
    this.focused = false;
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  append(...nodes) {
    nodes.forEach((node) => this.appendChild(node));
  }

  replaceChildren(...nodes) {
    this.children.forEach((node) => {
      node.parentNode = null;
    });
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  setAttribute(name, value) {
    this.attributes.set(String(name).toLowerCase(), String(value));
  }

  getAttribute(name) {
    const value = this.attributes.get(String(name).toLowerCase());
    return value === undefined ? null : value;
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  focus() {
    this.focused = true;
  }

  matches(selector) {
    if (selector.startsWith(".")) {
      return this.className.split(/\s+/).includes(selector.slice(1));
    }
    const attributeMatch = selector.match(/^([a-z]+)\[data-([a-z-]+)\]$/);
    if (attributeMatch) {
      const datasetKey = attributeMatch[2].replace(/-([a-z])/g, (match, letter) => letter.toUpperCase());
      return this.tagName === attributeMatch[1].toUpperCase() && this.dataset[datasetKey] !== undefined;
    }
    return false;
  }

  querySelectorAll(selector) {
    const found = [];
    const walk = (node) => {
      node.children.forEach((child) => {
        if (child.matches(selector)) found.push(child);
        walk(child);
      });
    };
    walk(this);
    return found;
  }
}

const BATCH_FUNCTIONS = [
  "adminMediaTitle",
  "adminMissingFieldsLabel",
  "adminAnalysisPresentation",
  "automaticAnalysisStatusMessage",
  "automaticAnalysisEndpoint",
  "durableAnalysisEndpoint",
  "framenestMutationHeaders",
  "selectSupportedAvailableLocation",
  "adminBatchDriverActive",
  "adminPageItemById",
  "adminPageSelectedIds",
  "reconcileAdminBatchSelection",
  "resetAdminBatchForQueryChange",
  "clearAdminBatchSelection",
  "setAdminItemSelection",
  "setAdminPageSelection",
  "adminAnalysisEligibility",
  "renderAdminSelectControl",
  "adminBatchOutcomeLabel",
  "adminBatchProgressText",
  "renderAdminBatchOutcomes",
  "syncAdminSelectControls",
  "renderAdminBatchBar",
  "setAdminBatchInteractionLock",
  "createAdminBatchDriver",
  "cancelAdminBatchConfirmation",
  "adminBatchRecordForItem",
  "startAdminPublishBatch",
  "runAdminPublishBatch",
  "executeAdminPublishBatchItem",
  "startAdminAnalysisBatch",
  "runAdminAnalysisBatch",
  "executeAdminAnalysisEnqueue",
  "pollAdminAnalysisBatchItem",
  "applyAdminAnalysisTerminalState",
  "markAdminBatchItemsNotStarted",
  "requestAdminBatchStop",
  "finalizeAdminBatch",
  "invalidateAdminBatchOnTeardown",
  "applyAdminCatalogFilters",
];

const BATCH_VARIABLES = [
  "ADMIN_MEDIA_ENDPOINT",
  "MEDIA_METADATA_ENDPOINT_PREFIX",
  "ADMIN_ANALYSIS_BATCH_MAX_ITEMS",
  "AUTOMATIC_ANALYSIS_POLL_INTERVAL_MS",
  "AUTOMATIC_ANALYSIS_POLL_MAX_ATTEMPTS",
  "AUTOMATIC_ANALYSIS_TERMINAL_STATES",
  "ADMIN_BATCH_OUTCOME_LABELS",
];

function createBatchHarness({ fetchHandler = null, confirmResult = true } = {}) {
  const fetchLog = [];
  const confirmations = [];
  const reloads = [];
  const elements = {
    adminBatchBar: new FakeElement(),
    adminBatchSelectAll: new FakeElement("input"),
    adminBatchSelectionCount: new FakeElement("p"),
    adminBatchPublishButton: new FakeElement("button"),
    adminBatchAnalyzeButton: new FakeElement("button"),
    adminBatchClearButton: new FakeElement("button"),
    adminBatchStopButton: new FakeElement("button"),
    adminBatchHint: new FakeElement("p"),
    adminBatchProgress: new FakeElement("p"),
    adminBatchOutcomes: new FakeElement("ul"),
    adminMediaResults: new FakeElement(),
    adminMediaSearch: new FakeElement("input"),
    adminMediaPublicationFilter: new FakeElement("select"),
    adminMediaReadinessFilter: new FakeElement("select"),
    adminMediaAnalysisFilter: new FakeElement("select"),
    adminMediaContributorFilter: new FakeElement("input"),
    adminMediaRefreshButton: new FakeElement("button"),
    adminMediaPrevButton: new FakeElement("button"),
    adminMediaNextButton: new FakeElement("button"),
    adminMediaCloseButton: new FakeElement("button"),
  };
  const context = {
    document: {
      createElement: (tagName) => new FakeElement(tagName),
      contains: (element) => Boolean(element) && element.connected !== false,
    },
    window: { setTimeout: (resolve) => setTimeout(resolve, 0) },
    Set,
    Map,
    Boolean,
    Number,
    String,
    Object,
    Array,
    JSON,
    Promise,
    encodeURIComponent,
    fetch: async (url, options = {}) => {
      const call = {
        url,
        method: options.method || "GET",
        headers: options.headers || {},
        body: options.body || null,
      };
      fetchLog.push(call);
      if (!fetchHandler) return response({});
      return fetchHandler(call);
    },
    requestConfirmation: async (args) => {
      confirmations.push(args);
      return confirmResult;
    },
    loadAdminCatalog: async () => {
      reloads.push({});
      return true;
    },
    identityAllowsAdminWorkflow: () => true,
    adminCatalogState: {
      q: "",
      publication: "unpublished",
      readiness: "all",
      analysis: "all",
      contributor: "",
      limit: 24,
      offset: 0,
      total: 0,
      requestOwner: null,
      items: [],
      publishOwners: new Map(),
      actionStatusByMediaId: new Map(),
      loading: false,
      error: false,
    },
    adminBatchState: { selectedMediaIds: new Set(), driver: null },
    adminBatchTeardown: false,
    opener: new FakeElement("button"),
    ...elements,
  };
  context.globalThis = context;
  vm.createContext(context);
  const source = [
    ...BATCH_VARIABLES.map((name) => extractVariable(APP_SOURCE, name)),
    ...BATCH_FUNCTIONS.map((name) => extractFunction(APP_SOURCE, name)),
  ].join("\n");
  vm.runInContext(source, context);
  return { context, elements, fetchLog, confirmations, reloads };
}

function adminItem(overrides = {}) {
  return {
    media_id: "media-1",
    media_kind: "video",
    display_title: "Item One",
    locations: [{ location_id: "loc-1", library_id: "lib-1", availability: "available" }],
    publication_ready: true,
    missing_fields: [],
    analysis_state: "not_requested",
    content_publication_state: "unpublished",
    ...overrides,
  };
}

function seedItems(harness, items) {
  harness.context.adminCatalogState.items = items;
  items.forEach((item) => {
    const checkbox = new FakeElement("input");
    checkbox.className = "admin-media-select__input";
    checkbox.dataset.mediaId = item.media_id;
    harness.elements.adminMediaResults.appendChild(checkbox);
  });
}

function selectAll(harness) {
  vm.runInContext("setAdminPageSelection(true)", harness.context);
}

function publishCalls(fetchLog) {
  return fetchLog.filter((call) => call.url.includes("/content-publication"));
}

function analysisPosts(fetchLog) {
  return fetchLog.filter((call) => call.url.includes("/durable-analysis"));
}

function analysisPolls(fetchLog) {
  return fetchLog.filter((call) => call.url.includes("/automatic-analysis"));
}

test("page-scoped selection selects and deselects one item with an accurate count", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  assert.equal(vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context), true);
  assert.deepEqual(vm.runInContext("adminPageSelectedIds()", harness.context), ["m1"]);
  assert.equal(harness.elements.adminBatchSelectionCount.textContent, "1 selected");
  assert.equal(vm.runInContext('setAdminItemSelection("m1", false, null)', harness.context), true);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
  assert.equal(harness.elements.adminBatchSelectionCount.textContent, "0 selected");
});

test("select all on this page selects and deselects exactly the current page", () => {
  const harness = createBatchHarness();
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2" }),
    adminItem({ media_id: "m3" }),
  ]);
  selectAll(harness);
  assert.deepEqual(vm.runInContext("adminPageSelectedIds()", harness.context), ["m1", "m2", "m3"]);
  assert.equal(harness.elements.adminBatchSelectAll.checked, true);
  assert.equal(harness.elements.adminBatchSelectAll.indeterminate, false);
  assert.equal(vm.runInContext("setAdminPageSelection(false)", harness.context), true);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
  assert.equal(harness.elements.adminBatchSelectAll.checked, false);
});

test("select-all checkbox reports an indeterminate state for a partial selection", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  assert.equal(harness.elements.adminBatchSelectAll.checked, false);
  assert.equal(harness.elements.adminBatchSelectAll.indeterminate, true);
});

test("row checkboxes stay in sync with select-all, item deselection and clear", () => {
  const harness = createBatchHarness();
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2" }),
    adminItem({ media_id: "m3" }),
  ]);
  const boxes = () => harness.elements.adminMediaResults.querySelectorAll(".admin-media-select__input");
  selectAll(harness);
  assert.deepEqual(boxes().map((box) => box.checked), [true, true, true]);
  vm.runInContext('setAdminItemSelection("m2", false, null)', harness.context);
  assert.deepEqual(boxes().map((box) => box.checked), [true, false, true]);
  assert.equal(harness.elements.adminBatchSelectAll.indeterminate, true);
  vm.runInContext("setAdminPageSelection(false)", harness.context);
  assert.deepEqual(boxes().map((box) => box.checked), [false, false, false]);
});

test("selection deduplicates by canonical media id", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  selectAll(harness);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 1);
});

test("selection rejects identifiers that are not on the currently loaded page", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  assert.equal(vm.runInContext('setAdminItemSelection("other", true, null)', harness.context), false);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
});

test("selection reconciles against the loaded page after a catalog reload", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  selectAll(harness);
  harness.context.adminCatalogState.items = [adminItem({ media_id: "m2" })];
  vm.runInContext("reconcileAdminBatchSelection()", harness.context);
  assert.deepEqual([...harness.context.adminBatchState.selectedMediaIds], ["m2"]);
  harness.context.adminCatalogState.items = [adminItem({ media_id: "m3" })];
  vm.runInContext("reconcileAdminBatchSelection()", harness.context);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
});

test("applying admin filters resets the selection and finished batch state", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  harness.elements.adminMediaSearch.value = "needle";
  selectAll(harness);
  harness.context.adminBatchState.driver = { type: "publish", lifecycle: "done", items: [] };
  vm.runInContext("applyAdminCatalogFilters()", harness.context);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
  assert.equal(harness.context.adminBatchState.driver, null);
  assert.equal(harness.context.adminCatalogState.q, "needle");
  assert.equal(harness.context.adminCatalogState.offset, 0);
  assert.equal(harness.reloads.length, 1);
});

test("pagination and browser open reset the batch selection through the same query-change hook", () => {
  const nextHandler = APP_SOURCE.slice(
    APP_SOURCE.indexOf("adminMediaNextButton.addEventListener"),
    APP_SOURCE.indexOf("adminBatchSelectAll.addEventListener"),
  );
  assert.match(nextHandler, /resetAdminBatchForQueryChange\(\)/);
  const prevHandler = APP_SOURCE.slice(
    APP_SOURCE.indexOf("adminMediaPrevButton.addEventListener"),
    APP_SOURCE.indexOf("adminMediaNextButton.addEventListener"),
  );
  assert.match(prevHandler, /resetAdminBatchForQueryChange\(\)/);
  assert.match(
    extractFunction(APP_SOURCE, "openAdminMediaBrowser"),
    /resetAdminBatchForQueryChange\(\)/,
  );
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  assert.equal(vm.runInContext("resetAdminBatchForQueryChange()", harness.context), true);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
});

test("selection and query changes are locked while a batch driver is active", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  harness.context.adminBatchState.driver = { type: "publish", lifecycle: "running", items: [] };
  assert.equal(vm.runInContext('setAdminItemSelection("m2", true, null)', harness.context), false);
  assert.equal(vm.runInContext("setAdminPageSelection(true)", harness.context), false);
  assert.equal(vm.runInContext("clearAdminBatchSelection()", harness.context), false);
  assert.equal(vm.runInContext("resetAdminBatchForQueryChange()", harness.context), false);
  assert.deepEqual([...harness.context.adminBatchState.selectedMediaIds], ["m1"]);
  vm.runInContext("reconcileAdminBatchSelection()", harness.context);
  assert.deepEqual([...harness.context.adminBatchState.selectedMediaIds], ["m1"]);
  harness.context.adminBatchState.driver = { type: "analysis", lifecycle: "done", items: [] };
  assert.equal(vm.runInContext("clearAdminBatchSelection()", harness.context), true);
  assert.equal(harness.context.adminBatchState.driver, null);
});

test("admin batch controls exist only inside the capability-gated administrator surface", () => {
  const browserStart = INDEX_SOURCE.indexOf('id="admin-media-browser"');
  const browserEnd = INDEX_SOURCE.indexOf("</section>", browserStart);
  const batchBar = INDEX_SOURCE.indexOf('id="admin-media-batch-bar"');
  assert.ok(browserStart !== -1 && batchBar > browserStart && batchBar < browserEnd);
  assert.match(INDEX_SOURCE.slice(0, browserStart + 400), /hidden/);
  assert.match(
    extractFunction(APP_SOURCE, "startAdminPublishBatch"),
    /identityAllowsAdminWorkflow\(\)/,
  );
  assert.match(
    extractFunction(APP_SOURCE, "startAdminAnalysisBatch"),
    /identityAllowsAdminWorkflow\(\)/,
  );
  assert.equal(INDEX_SOURCE.includes("admin-batch-select-all"), true);
  const catalogSection = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf('id="catalog-browser"'),
    INDEX_SOURCE.indexOf('id="admin-media-browser"'),
  );
  assert.equal(catalogSection.includes("admin-batch"), false);
});

test("publish batch requires confirmation and reuses the existing route and mutation helper", async () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  const run = vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  await flush();
  assert.equal(harness.confirmations.length, 1);
  assert.equal(harness.confirmations[0].title, "Publish 1 selected item?");
  assert.equal(harness.confirmations[0].confirmLabel, "Publish selected");
  assert.equal(harness.confirmations[0].dismissLabel, "Cancel");
  assert.equal(harness.confirmations[0].destructive, false);
  assert.equal(harness.confirmations[0].focusReturn, harness.context.opener);
  await run;
  const calls = publishCalls(harness.fetchLog);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/admin/media/m1/content-publication");
  assert.equal(calls[0].method, "PUT");
  assert.equal(calls[0].headers["X-FrameNest-Request"], "1");
  const helper = extractFunction(APP_SOURCE, "executeAdminPublishBatchItem");
  assert.match(helper, /framenestMutationHeaders/);
  assert.match(helper, /ADMIN_MEDIA_ENDPOINT\}\/\$\{encodeURIComponent\(record\.mediaId\)\}\/content-publication/);
  assert.doesNotMatch(helper, /for \(|while \(|retry/i);
});

test("a dismissed confirmation starts no request and keeps the selection", async () => {
  const harness = createBatchHarness({ confirmResult: false });
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  await vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  assert.equal(harness.fetchLog.length, 0);
  assert.equal(harness.context.adminBatchState.driver, null);
  assert.deepEqual([...harness.context.adminBatchState.selectedMediaIds], ["m1"]);
  assert.equal(harness.context.opener.focused, true);
});

test("publish batch runs sequentially in page order with one in-flight request", async () => {
  const gates = [deferred(), deferred(), deferred()];
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      const index = publishCalls([call]).length === 0 ? -1 : ["m1", "m2", "m3"].indexOf(
        call.url.match(/media\/([^/]+)\/content-publication/)[1],
      );
      return gates[index].promise;
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1", display_title: "First" }),
    adminItem({ media_id: "m2", display_title: "Second" }),
    adminItem({ media_id: "m3", display_title: "Third" }),
  ]);
  selectAll(harness);
  const run = vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  await flush();
  assert.deepEqual(
    publishCalls(harness.fetchLog).map((call) => call.url),
    ["/api/admin/media/m1/content-publication"],
  );
  gates[0].resolve(response({ status: "published" }));
  await flush();
  assert.deepEqual(
    publishCalls(harness.fetchLog).map((call) => call.url),
    ["/api/admin/media/m1/content-publication", "/api/admin/media/m2/content-publication"],
  );
  gates[1].resolve(response({ status: "already_published" }));
  await flush();
  gates[2].resolve(response({ status: "published" }, 201));
  await run;
  assert.deepEqual(
    publishCalls(harness.fetchLog).map((call) => call.url),
    [
      "/api/admin/media/m1/content-publication",
      "/api/admin/media/m2/content-publication",
      "/api/admin/media/m3/content-publication",
    ],
  );
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["published", "already_published", "published"],
  );
  assert.equal(driver.items[1].message, "The server already lists this item as published.");
  assert.equal(harness.reloads.length, 1);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 0);
});

test("publish batch maps not_ready and failure outcomes and never retries", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("m1")) {
        return response(
          { error: { code: "PUBLICATION_NOT_READY", missing_fields: ["description", "tags"] } },
          409,
        );
      }
      if (call.url.includes("m2")) return response({ error: { code: "PUBLICATION_FAILED" } }, 500);
      return response({ status: "published" }, 201);
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1", publication_ready: false, missing_fields: ["description"] }),
    adminItem({ media_id: "m2" }),
    adminItem({ media_id: "m3" }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["not_ready", "failed", "published"],
  );
  assert.equal(driver.items[0].message, "Missing: description, canonical tag");
  assert.equal(driver.items[1].message, "Publication failed without changing the durable state.");
  const calls = publishCalls(harness.fetchLog);
  assert.equal(calls.length, 3, "one failure must not abort later items and never retries");
  assert.equal(calls.filter((call) => call.url.includes("m1")).length, 1);
  assert.equal(analysisPosts(harness.fetchLog).length, 0);
  assert.equal(analysisPolls(harness.fetchLog).length, 0);
  assert.equal(harness.reloads.length, 1);
});

test("publish batch reports authorization and network failures without changing durable truth", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("m1")) return response({ error: { code: "CAPABILITY_DENIED" } }, 403);
      throw new Error("offline");
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  selectAll(harness);
  await vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["failed", "failed"],
  );
  assert.equal(driver.items[0].message, "The current identity is not authorized to publish this item.");
  assert.equal(driver.items[1].message, "Publication could not reach the local server.");
});

test("publish Stop finishes the in-flight item and marks the rest as not started", async () => {
  const gates = [deferred(), deferred()];
  const harness = createBatchHarness({
    fetchHandler: (call) => (call.url.includes("m1") ? gates[0].promise : gates[1].promise),
  });
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2" }),
    adminItem({ media_id: "m3" }),
  ]);
  selectAll(harness);
  const run = vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  await flush();
  gates[0].resolve(response({ status: "published" }, 201));
  await flush();
  assert.equal(publishCalls(harness.fetchLog).length, 2);
  vm.runInContext("requestAdminBatchStop()", harness.context);
  assert.equal(harness.context.adminBatchState.driver.lifecycle, "stopping");
  assert.equal(harness.elements.adminBatchStopButton.disabled, true);
  gates[1].resolve(response({ status: "published" }, 201));
  await run;
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "stopped");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["published", "published", "not_started_due_to_stop"],
  );
  assert.equal(driver.items[2].message, "Not started; the batch was stopped before this item.");
  const summary = harness.elements.adminBatchProgress.textContent;
  assert.match(summary, /^Stopped: 2 published, 1 not started\./);
  assert.match(summary, /Completed changes remain applied\./);
  assert.equal(publishCalls(harness.fetchLog).length, 2, "no further request after Stop");
  assert.equal(harness.reloads.length, 1, "authoritative reload after stopped");
  assert.equal(harness.elements.adminMediaSearch.disabled, false, "controls restored after stop");
});

test("publish batch locks filters search pagination close and row controls while running", async () => {
  const gate = deferred();
  const harness = createBatchHarness({ fetchHandler: () => gate.promise });
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  const run = vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  await flush();
  const locked = [
    "adminMediaSearch",
    "adminMediaPublicationFilter",
    "adminMediaReadinessFilter",
    "adminMediaAnalysisFilter",
    "adminMediaContributorFilter",
    "adminMediaRefreshButton",
    "adminMediaPrevButton",
    "adminMediaNextButton",
    "adminMediaCloseButton",
  ];
  for (const key of locked) {
    assert.equal(harness.elements[key].disabled, true, `${key} must be locked`);
  }
  assert.equal(
    harness.elements.adminMediaResults.querySelectorAll(".admin-media-select__input")[0].disabled,
    true,
    "row checkboxes must be locked",
  );
  assert.equal(harness.elements.adminBatchPublishButton.disabled, true);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, true);
  assert.equal(harness.elements.adminBatchClearButton.disabled, true);
  assert.equal(harness.elements.adminBatchStopButton.hidden, false);
  assert.equal(harness.elements.adminBatchStopButton.disabled, false);
  gate.resolve(response({ status: "published" }, 201));
  await run;
  for (const key of locked) {
    assert.equal(harness.elements[key].disabled, false, `${key} must be restored`);
  }
  assert.equal(harness.elements.adminBatchStopButton.hidden, true, "Stop hides once finished");
});

test("publish batch surfaces truthful per-item outcomes and an aggregate summary", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("m1")) return response({ status: "published" }, 201);
      if (call.url.includes("m2")) return response({ status: "already_published" });
      return response({ error: { code: "PUBLICATION_NOT_READY", missing_fields: ["display_title"] } }, 409);
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1", display_title: "Alpha" }),
    adminItem({ media_id: "m2", display_title: "Beta" }),
    adminItem({ media_id: "m3", display_title: "Gamma", publication_ready: false }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  const outcomes = harness.elements.adminBatchOutcomes;
  assert.equal(outcomes.hidden, false);
  assert.equal(outcomes.children.length, 3);
  assert.equal(outcomes.children[0].children[0].textContent, "Alpha");
  assert.equal(outcomes.children[0].children[1].textContent, "Published — Published by the server.");
  assert.equal(
    outcomes.children[1].children[1].textContent,
    "Already published — The server already lists this item as published.",
  );
  assert.equal(
    outcomes.children[2].children[1].textContent,
    "Not ready — Missing: title",
  );
  assert.equal(
    harness.elements.adminBatchProgress.textContent,
    "Finished: 1 published, 1 already published, 1 not ready.",
  );
  assert.equal(
    outcomes.children[0].className,
    "admin-batch__outcome admin-batch__outcome--published",
  );
});

test("analysis eligibility accepts only not_requested items with exact counts", () => {
  const harness = createBatchHarness();
  seedItems(harness, [
    adminItem({ media_id: "m1", analysis_state: "not_requested" }),
    adminItem({ media_id: "m2", analysis_state: "pending" }),
    adminItem({ media_id: "m3", analysis_state: "analyzing" }),
    adminItem({ media_id: "m4", analysis_state: "analyzed" }),
    adminItem({ media_id: "m5", analysis_state: "failed" }),
    adminItem({ media_id: "m6", analysis_state: "not_requested" }),
  ]);
  selectAll(harness);
  const eligibility = vm.runInContext("adminAnalysisEligibility()", harness.context);
  assert.deepEqual([...eligibility.eligible], ["m1", "m6"]);
  assert.deepEqual([...eligibility.ineligible], ["m2", "m3", "m4", "m5"]);
});

test("analysis batch is disabled with zero eligible items and explains why", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1", analysis_state: "analyzed" })]);
  selectAll(harness);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, true);
  assert.match(harness.elements.adminBatchHint.textContent, /No selected item is eligible/);
  assert.equal(harness.elements.adminBatchHint.hidden, false);
});

test("analysis batch rejects more than 10 eligible items without silent trimming", async () => {
  const harness = createBatchHarness();
  const items = [];
  for (let index = 1; index <= 11; index += 1) {
    items.push(adminItem({ media_id: `m${index}` }));
  }
  seedItems(harness, items);
  selectAll(harness);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, true);
  assert.match(harness.elements.adminBatchHint.textContent, /11 selected items are eligible/);
  assert.match(harness.elements.adminBatchHint.textContent, /Narrow the selection to at most 10/);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  assert.equal(harness.confirmations.length, 0, "rejected before confirmation");
  assert.equal(harness.fetchLog.length, 0, "no request is sent");
  assert.equal(harness.context.adminBatchState.driver, null);
  assert.equal(harness.context.adminBatchState.selectedMediaIds.size, 11, "selection is not trimmed");
});

test("analysis batch of exactly 10 eligible items is allowed", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "analyzed" });
      return response({ state: "analyzed" });
    },
  });
  const items = [];
  for (let index = 1; index <= 10; index += 1) {
    items.push(adminItem({ media_id: `m${index}` }));
  }
  seedItems(harness, items);
  selectAll(harness);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, false);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  assert.equal(analysisPosts(harness.fetchLog).length, 10);
  assert.equal(harness.context.adminBatchState.driver.lifecycle, "done");
});

test("analysis confirmation states eligible and skipped counts with honest provider accounting", async () => {
  const harness = createBatchHarness({
    fetchHandler: () => response({ state: "analyzed" }),
  });
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2", analysis_state: "analyzed" }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  assert.equal(harness.confirmations.length, 1);
  const confirmation = harness.confirmations[0];
  assert.equal(confirmation.title, "Queue first analysis for 1 eligible item?");
  assert.match(confirmation.message, /1 selected item is not eligible and will be skipped\./);
  assert.match(confirmation.message, /may create one durable analysis run/);
  assert.match(
    confirmation.message,
    /Provider retries, when required, remain governed and recorded by the existing server policy\./,
  );
  assert.doesNotMatch(confirmation.message, /exactly one provider/i);
  assert.equal(confirmation.focusReturn, harness.context.opener);
});

test("analysis batch enqueues sequentially with terminal polling before the next item", async () => {
  const sequence = [];
  const polls = { m1: 0, m3: 0 };
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) {
        sequence.push(`POST ${call.url}`);
        assert.equal(call.method, "POST");
        assert.equal(call.headers["X-FrameNest-Request"], "1");
        assert.equal(JSON.parse(call.body).confirm_cloud_upload, true);
        return response({ state: "pending", media_id: call.url.split("/")[4] });
      }
      if (call.url.includes("automatic-analysis")) {
        const mediaId = call.url.match(/media\/([^/]+)\/automatic-analysis/)[1];
        polls[mediaId] += 1;
        sequence.push(`GET ${mediaId}#${polls[mediaId]}`);
        if (mediaId === "m1" && polls.m1 === 1) return response({ state: "analyzing" });
        return response({ state: "analyzed" });
      }
      throw new Error(`unexpected ${call.url}`);
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1", display_title: "Alpha" }),
    adminItem({ media_id: "m2", analysis_state: "analyzed", display_title: "Beta" }),
    adminItem({ media_id: "m3", display_title: "Gamma" }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const posts = analysisPosts(harness.fetchLog);
  assert.equal(posts.length, 2, "ineligible items never receive a POST");
  assert.equal(posts[0].url, "/api/media/m1/locations/loc-1/durable-analysis");
  assert.equal(posts[1].url, "/api/media/m3/locations/loc-1/durable-analysis");
  const postM1 = sequence.indexOf("POST /api/media/m1/locations/loc-1/durable-analysis");
  const postM3 = sequence.indexOf("POST /api/media/m3/locations/loc-1/durable-analysis");
  const lastPollM1 = sequence.lastIndexOf("GET m1#2");
  assert.ok(postM1 < lastPollM1 && lastPollM1 < postM3, "no next enqueue before terminal state");
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["analyzed", "ineligible", "analyzed"],
  );
  assert.match(driver.items[1].message, /only available before any analysis request/);
  assert.match(driver.items[1].message, /AI suggestion ready/);
  assert.equal(driver.items[0].message, "AI suggestion ready for review.");
  assert.equal(harness.reloads.length, 1);
});

test("analysis batch uses the deterministic supported and available location helper", () => {
  const helper = extractFunction(APP_SOURCE, "runAdminAnalysisBatch");
  assert.match(helper, /selectSupportedAvailableLocation\(item\)/);
  const context = {};
  vm.runInNewContext(extractFunction(APP_SOURCE, "selectSupportedAvailableLocation"), context);
  const unsupported = context.selectSupportedAvailableLocation(
    adminItem({ media_kind: "document" }),
  );
  assert.equal(unsupported, null);
  const unavailable = context.selectSupportedAvailableLocation(
    adminItem({ locations: [{ location_id: "loc-1", availability: "missing" }] }),
  );
  assert.equal(unavailable, null);
  const chosen = context.selectSupportedAvailableLocation(
    adminItem({
      locations: [
        { location_id: "loc-off", availability: "offline" },
        { location_id: "loc-on", availability: "available" },
      ],
    }),
  );
  assert.equal(chosen.location_id, "loc-on");
});

test("analysis batch records location_unavailable without sending a POST", async () => {
  const harness = createBatchHarness({
    fetchHandler: () => response({ state: "analyzed" }),
  });
  seedItems(harness, [
    adminItem({ media_id: "m1", locations: [] }),
    adminItem({ media_id: "m2" }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const posts = analysisPosts(harness.fetchLog);
  assert.equal(posts.length, 1);
  assert.equal(posts[0].url.includes("m2"), true);
  const driver = harness.context.adminBatchState.driver;
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["location_unavailable", "analyzed"],
  );
  assert.equal(driver.items[0].message, "No supported and available location exists for this item.");
});

test("analysis batch halts on provider unavailability with zero further requests", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) {
        return response({ error: { code: "AI_PROVIDER_NOT_CONFIGURED" } }, 503);
      }
      throw new Error(`unexpected ${call.url}`);
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2" }),
    adminItem({ media_id: "m3" }),
  ]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  assert.equal(analysisPosts(harness.fetchLog).length, 1, "no later analysis POST occurs");
  assert.equal(analysisPolls(harness.fetchLog).length, 0);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.equal(driver.providerUnavailable, true);
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["provider_unavailable", "not_started_provider_halt", "not_started_provider_halt"],
  );
  assert.equal(driver.items[0].message, "The AI provider is unavailable; no further items were queued.");
  const summary = harness.elements.adminBatchProgress.textContent;
  assert.match(summary, /provider unavailable/);
  assert.match(summary, /remaining eligible items were not queued/);
  assert.equal(harness.reloads.length, 1);
});

test("analysis batch maps enqueue failures per item and never retries automatically", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("m1")) return response({ error: { code: "ANALYSIS_REQUEST_UNAVAILABLE" } }, 503);
      if (call.url.includes("durable-analysis")) return response({ state: "analyzed" });
      return response({ state: "analyzed" });
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["failed", "analyzed"],
  );
  assert.equal(driver.items[0].message, "The analysis request was not accepted; no run was created by this item.");
  assert.equal(
    analysisPosts(harness.fetchLog).filter((call) => call.url.includes("m1")).length,
    1,
    "no automatic POST retry",
  );
  const helper = extractFunction(APP_SOURCE, "executeAdminAnalysisEnqueue");
  assert.match(helper, /confirm_cloud_upload: true/);
  assert.match(helper, /framenestMutationHeaders/);
  assert.doesNotMatch(helper, /for \(|while \(/);
});

test("analysis batch reports terminal failure with the server message", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      return response({ state: "failed", error_message: "AI provider is temporarily unavailable." });
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["failed"],
  );
  assert.equal(driver.items[0].message, "AI provider is temporarily unavailable.");
});

test("analysis batch reports status unavailability without claiming the run failed", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      return response({ error: { code: "ANALYSIS_STATUS_UNAVAILABLE" } }, 503);
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["status_unavailable", "status_unavailable"],
  );
  assert.match(driver.items[0].message, /continues under the server lifecycle/);
  assert.doesNotMatch(driver.items[0].message, /failed/i);
});

test("analysis batch ends a never-terminal poll as unavailable without retrying the POST", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      return response({ state: "analyzing" });
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "done");
  assert.equal(driver.items[0].status, "status_unavailable");
  assert.match(driver.items[0].message, /did not reach a terminal state within the polling window/);
  assert.equal(analysisPolls(harness.fetchLog).length, 40, "bounded polling window");
  assert.equal(analysisPosts(harness.fetchLog).length, 1);
});

test("analysis Stop keeps the queued run intact and marks remaining items not started", async () => {
  const firstPoll = deferred();
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      return firstPoll.promise;
    },
  });
  seedItems(harness, [
    adminItem({ media_id: "m1" }),
    adminItem({ media_id: "m2" }),
  ]);
  selectAll(harness);
  const run = vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  await flush();
  assert.equal(analysisPosts(harness.fetchLog).length, 1);
  vm.runInContext("requestAdminBatchStop()", harness.context);
  firstPoll.resolve(response({ state: "analyzing" }));
  await run;
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "stopped");
  assert.deepEqual(
    driver.items.map((record) => record.status),
    ["analyzing", "not_started_due_to_stop"],
  );
  assert.equal(analysisPosts(harness.fetchLog).length, 1, "no new analysis POST after Stop");
  assert.equal(
    harness.fetchLog.some((call) => call.method === "DELETE" || call.url.includes("cancel")),
    false,
    "no fake cancellation request",
  );
  const summary = harness.elements.adminBatchProgress.textContent;
  assert.match(summary, /Stopped:/);
  assert.match(summary, /1 still queued or analyzing/);
  assert.match(summary, /Already queued analysis continues under the server lifecycle\./);
  assert.equal(harness.reloads.length, 1);
});

test("analysis batch transitions records through real server states only", async () => {
  let pollCount = 0;
  const seen = [];
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      pollCount += 1;
      if (pollCount === 1) return response({ state: "pending" });
      if (pollCount === 2) return response({ state: "analyzing" });
      return response({ state: "analyzed" });
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  const harnessContext = harness.context;
  const originalRender = harness.elements.adminBatchOutcomes.replaceChildren;
  harness.elements.adminBatchOutcomes.replaceChildren = function (...nodes) {
    const record = harnessContext.adminBatchState.driver
      ? harnessContext.adminBatchState.driver.items[0]
      : null;
    if (record) seen.push(record.status);
    return originalRender.apply(this, nodes);
  };
  await vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.items[0].status, "analyzed");
  assert.deepEqual([...new Set(seen)], ["pending", "queueing", "queued", "analyzing", "analyzed"]);
});

test("page teardown stops scheduling without resubmission and reload keeps no batch state", async () => {
  const harness = createBatchHarness({
    fetchHandler: (call) => {
      if (call.url.includes("durable-analysis")) return response({ state: "pending" });
      return response({ state: "analyzing" });
    },
  });
  seedItems(harness, [adminItem({ media_id: "m1" }), adminItem({ media_id: "m2" })]);
  selectAll(harness);
  const run = vm.runInContext("startAdminAnalysisBatch(opener)", harness.context);
  await flush();
  vm.runInContext("invalidateAdminBatchOnTeardown()", harness.context);
  await run;
  assert.equal(analysisPosts(harness.fetchLog).length, 1, "no resubmission after teardown");
  assert.equal(harness.context.adminBatchTeardown, true);
  assert.match(APP_SOURCE, /window\.addEventListener\("pagehide", invalidateAdminBatchOnTeardown\)/);
  const batchSection = APP_SOURCE.slice(
    APP_SOURCE.indexOf("function adminBatchDriverActive"),
    APP_SOURCE.indexOf("function openAdminMediaBrowser"),
  );
  assert.doesNotMatch(batchSection, /localStorage|sessionStorage|indexedDB/i);
  const fresh = createBatchHarness();
  assert.equal(fresh.context.adminBatchState.selectedMediaIds.size, 0);
  assert.equal(fresh.context.adminBatchState.driver, null);
  assert.equal(fresh.fetchLog.length, 0, "a fresh page performs no automatic batch request");
});

test("per-item selection control is a semantic checkbox labelled by the media title", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1", display_title: "Alpha Clip" })]);
  const label = vm.runInContext(
    "renderAdminSelectControl(adminCatalogState.items[0])",
    harness.context,
  );
  assert.equal(label.tagName, "LABEL");
  const checkbox = label.children[0];
  assert.equal(checkbox.tagName, "INPUT");
  assert.equal(checkbox.type, "checkbox");
  assert.equal(checkbox.getAttribute("aria-label"), "Select Alpha Clip");
  assert.equal(checkbox.dataset.mediaId, "m1");
  assert.equal(typeof checkbox.listeners.get("change")[0], "function");
  checkbox.checked = true;
  checkbox.listeners.get("change")[0]();
  assert.deepEqual([...harness.context.adminBatchState.selectedMediaIds], ["m1"]);
  assert.match(extractFunction(APP_SOURCE, "renderAdminMediaItem"), /renderAdminSelectControl\(item\)/);
});

test("select all on this page keeps an accessible label and native semantics", () => {
  assert.match(
    INDEX_SOURCE,
    /<label class="admin-batch__select-all" for="admin-batch-select-all">[\s\S]*?<input id="admin-batch-select-all" type="checkbox">[\s\S]*?<span>Select all on this page<\/span>/,
  );
  assert.match(APP_SOURCE, /adminBatchSelectAll\.addEventListener\("change"/);
  assert.match(APP_SOURCE, /setAdminPageSelection\(adminBatchSelectAll\.checked\)/);
});

test("batch actions are semantic buttons with an explicit type", () => {
  for (const id of [
    "admin-batch-publish-button",
    "admin-batch-analyze-button",
    "admin-batch-clear-button",
    "admin-batch-stop-button",
  ]) {
    assert.match(INDEX_SOURCE, new RegExp(`<button id="${id}"[^>]*type="button"`));
  }
});

test("aggregate progress uses a polite live region separate from per-item outcomes", () => {
  assert.match(
    INDEX_SOURCE,
    /id="admin-batch-progress"[\s\S]*?role="status"[\s\S]*?aria-live="polite"[\s\S]*?aria-atomic="true"/,
  );
  assert.match(INDEX_SOURCE, /id="admin-batch-outcomes"/);
  assert.doesNotMatch(INDEX_SOURCE, /id="admin-batch-outcomes"[^>]*aria-live/);
  const poll = extractFunction(APP_SOURCE, "pollAdminAnalysisBatchItem");
  assert.match(poll, /if \(record\.status !== nextStatus\)/, "unchanged poll states must not re-render");
});

test("action surface stays inert without a selection and Stop only appears while running", () => {
  const harness = createBatchHarness();
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchPublishButton.disabled, true);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, true);
  assert.equal(harness.elements.adminBatchClearButton.disabled, true);
  assert.equal(harness.elements.adminBatchStopButton.hidden, true);
  assert.equal(harness.elements.adminBatchProgress.hidden, true);
  assert.equal(harness.elements.adminBatchOutcomes.hidden, true);
  vm.runInContext('setAdminItemSelection("m1", true, null)', harness.context);
  assert.equal(harness.elements.adminBatchPublishButton.disabled, false);
  assert.equal(harness.elements.adminBatchAnalyzeButton.disabled, false);
  assert.equal(harness.elements.adminBatchClearButton.disabled, false);
  harness.context.adminBatchState.driver = { type: "publish", lifecycle: "running", items: [] };
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchStopButton.hidden, false);
  assert.equal(harness.elements.adminBatchStopButton.textContent, "Stop");
  harness.context.adminBatchState.driver = { type: "publish", lifecycle: "stopping", items: [] };
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchStopButton.hidden, false);
  assert.equal(harness.elements.adminBatchStopButton.disabled, true);
  assert.equal(harness.elements.adminBatchStopButton.textContent, "Stopping…");
  harness.context.adminBatchState.driver = { type: "publish", lifecycle: "done", items: [] };
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchStopButton.hidden, true);
});

test("batch bar hides only when the page is empty and no batch state exists", () => {
  const harness = createBatchHarness();
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchBar.hidden, true);
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  vm.runInContext("renderAdminBatchBar()", harness.context);
  assert.equal(harness.elements.adminBatchBar.hidden, false);
});

test("a confirming batch shows prepared records without a false finished summary", () => {
  const harness = createBatchHarness({ confirmResult: false });
  seedItems(harness, [adminItem({ media_id: "m1" })]);
  selectAll(harness);
  vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  const driver = harness.context.adminBatchState.driver;
  assert.equal(driver.lifecycle, "confirming");
  assert.equal(harness.elements.adminBatchProgress.hidden, true);
  assert.equal(harness.elements.adminBatchProgress.textContent, "");
  assert.equal(harness.elements.adminBatchOutcomes.hidden, false);
  assert.equal(harness.elements.adminBatchOutcomes.children[0].children[1].textContent, "Waiting");
});

test("confirmation focus returns to the invoking action through the shared dialog", () => {
  for (const name of ["startAdminPublishBatch", "startAdminAnalysisBatch"]) {
    const source = extractFunction(APP_SOURCE, name);
    assert.match(source, /requestConfirmation\(\{/);
    assert.match(source, /focusReturn: opener/);
    assert.match(source, /dismissLabel: "Cancel"/);
  }
  const confirmation = extractFunction(APP_SOURCE, "settleConfirmation");
  assert.match(confirmation, /restoreConfirmationFocus\(request\.focusReturn\)/);
});

test("batch rendering uses safe DOM APIs only", () => {
  const outcomes = extractFunction(APP_SOURCE, "renderAdminBatchOutcomes");
  assert.doesNotMatch(outcomes, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
  assert.match(outcomes, /textContent/);
  const batchSection = APP_SOURCE.slice(
    APP_SOURCE.indexOf("function adminBatchDriverActive"),
    APP_SOURCE.indexOf("function openAdminMediaBrowser"),
  );
  assert.doesNotMatch(batchSection, /innerHTML|outerHTML|insertAdjacentHTML|document\.write/);
});

test("hostile item titles render as inert text in per-item outcomes", async () => {
  const hostile = '<img src=x onerror="window.__pwned = true">';
  const harness = createBatchHarness({
    fetchHandler: () => response({ status: "published" }, 201),
  });
  seedItems(harness, [adminItem({ media_id: "m1", display_title: hostile })]);
  selectAll(harness);
  await vm.runInContext("startAdminPublishBatch(opener)", harness.context);
  const outcomes = harness.elements.adminBatchOutcomes;
  assert.equal(outcomes.children.length, 1);
  const title = outcomes.children[0].children[0];
  assert.equal(title.children.length, 0, "title must not produce child markup");
  assert.equal(title.textContent, hostile);
  assert.equal(harness.context.window.__pwned, undefined);
});

test("batch styles reuse accepted tokens responsive ranges focus visibility and touch targets", () => {
  assert.match(STYLES_SOURCE, /\.admin-batch \{[\s\S]*?border: 1px solid var\(--line\)/);
  assert.match(STYLES_SOURCE, /\.admin-batch__select-all \{[\s\S]*?min-height: 44px/);
  assert.match(STYLES_SOURCE, /\.admin-media-select \{[\s\S]*?width: 44px;[\s\S]*?height: 44px/);
  assert.match(STYLES_SOURCE, /\.admin-media-select__input:focus-visible \{[\s\S]*?outline: 2px solid var\(--focus\)/);
  assert.match(STYLES_SOURCE, /@media \(max-width: 720px\) \{[\s\S]*?\.admin-batch__controls/);
  assert.match(STYLES_SOURCE, /@media \(max-width: 720px\) \{[\s\S]*?\.admin-batch__actions/);
  assert.match(STYLES_SOURCE, /\.admin-batch__outcomes \{[\s\S]*?overflow-y: auto/);
  assert.match(STYLES_SOURCE, /@media \(prefers-reduced-motion: reduce\)/);
});

test("single-item publish stays available only outside an active batch", () => {
  assert.match(
    extractFunction(APP_SOURCE, "renderAdminMediaItem"),
    /\|\| adminBatchDriverActive\(\)/,
  );
  const singleItem = extractFunction(APP_SOURCE, "mutateAdminContentPublication");
  assert.match(singleItem, /claimPublicationRequest/);
  assert.doesNotMatch(singleItem, /adminBatchState/);
});

test("catalog render path reconciles selection and refreshes the batch surface", () => {
  const render = extractFunction(APP_SOURCE, "renderAdminCatalogPage");
  const reconcile = render.indexOf("reconcileAdminBatchSelection()");
  const rows = render.indexOf("renderAdminMediaItem(item)");
  const bar = render.indexOf("renderAdminBatchBar()");
  assert.ok(reconcile !== -1 && rows !== -1 && bar !== -1);
  assert.ok(reconcile < rows, "reconcile runs before row rendering");
});

test("batch state is a single explicit selection model shared by both actions", () => {
  const stateDeclaration = extractVariable(APP_SOURCE, "adminBatchState");
  assert.match(stateDeclaration, /selectedMediaIds: new Set\(\)/);
  assert.match(stateDeclaration, /driver: null/);
  assert.match(extractVariable(APP_SOURCE, "adminBatchTeardown"), /adminBatchTeardown = false/);
  assert.match(extractVariable(APP_SOURCE, "ADMIN_ANALYSIS_BATCH_MAX_ITEMS"), /= 10/);
  const publishStart = extractFunction(APP_SOURCE, "startAdminPublishBatch");
  const analysisStart = extractFunction(APP_SOURCE, "startAdminAnalysisBatch");
  assert.match(publishStart, /adminPageSelectedIds\(\)/);
  assert.match(analysisStart, /adminPageSelectedIds\(\)/);
  assert.match(analysisStart, /adminAnalysisEligibility\(\)/);
  assert.match(analysisStart, /ADMIN_ANALYSIS_BATCH_MAX_ITEMS/);
});

test("no batch endpoint, cancellation, or client retry exists in the batch driver", () => {
  const batchSection = APP_SOURCE.slice(
    APP_SOURCE.indexOf("function adminBatchDriverActive"),
    APP_SOURCE.indexOf("function openAdminMediaBrowser"),
  );
  assert.doesNotMatch(batchSection, /method: "DELETE"/);
  assert.doesNotMatch(batchSection, /\/batch|batch-/i);
  assert.doesNotMatch(batchSection, /AbortController|\.abort\(|cancel-analysis|\/cancel/i);
  assert.match(batchSection, /content-publication/);
  assert.match(batchSection, /durableAnalysisEndpoint\(record\.mediaId, locationId\)/);
  assert.match(batchSection, /automaticAnalysisEndpoint\(record\.mediaId\)/);
  const poll = extractFunction(APP_SOURCE, "pollAdminAnalysisBatchItem");
  assert.match(poll, /AUTOMATIC_ANALYSIS_POLL_MAX_ATTEMPTS/);
  assert.match(poll, /AUTOMATIC_ANALYSIS_POLL_INTERVAL_MS/);
  assert.doesNotMatch(poll, /setInterval/);
});
