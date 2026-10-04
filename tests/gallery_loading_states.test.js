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

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

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

class TestClassList {
  constructor() {
    this.values = new Set();
  }

  setFromString(value) {
    this.values = new Set(String(value).split(/\s+/).filter(Boolean));
  }

  add(...names) {
    names.forEach((name) => this.values.add(name));
  }

  remove(...names) {
    names.forEach((name) => this.values.delete(name));
  }

  contains(name) {
    return this.values.has(name);
  }

  toString() {
    return [...this.values].join(" ");
  }
}

class TestElement {
  constructor(document, tagName) {
    this.ownerDocument = document;
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.dataset = {};
    this.attributes = new Map();
    this.listeners = new Map();
    this.classList = new TestClassList();
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.type = "";
    this.value = "";
  }

  get className() {
    return this.classList.toString();
  }

  set className(value) {
    this.classList.setFromString(value);
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }

  dispatchEvent(event) {
    if (!event.target) event.target = this;
    for (const listener of [...(this.listeners.get(event.type) || [])]) {
      listener(event);
    }
    return true;
  }

  click() {
    if (this.disabled) return;
    this.dispatchEvent({ type: "click", target: this });
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

  setAttribute(name, value) {
    this.attributes.set(String(name).toLowerCase(), String(value));
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return this.attributes.has(key) ? this.attributes.get(key) : null;
  }

  querySelector() {
    return null;
  }

  querySelectorAll() {
    return [];
  }
}

class TestDocument {
  constructor(elementsById) {
    this.elementsById = elementsById;
    this.body = new TestElement(this, "body");
  }

  createElement(tagName) {
    return new TestElement(this, tagName);
  }

  getElementById(id) {
    return this.elementsById[id] || null;
  }

  querySelector(selector) {
    const idMatch = selector.match(/^#([A-Za-z0-9_-]+)$/);
    if (idMatch) return this.getElementById(idMatch[1]);
    return null;
  }
}

function createCatalogHarness(fetchImpl) {
  const catalogBrowser = new TestElement(null, "section");
  catalogBrowser.id = "catalog-browser";
  const catalogStateLoading = new TestElement(null, "div");
  catalogStateLoading.id = "catalog-state-loading";
  catalogStateLoading.setAttribute("role", "status");
  catalogStateLoading.setAttribute("aria-busy", "true");
  const spinner = new TestElement(null, "span");
  spinner.className = "library-state__spinner";
  spinner.setAttribute("aria-hidden", "true");
  const loadingMessage = new TestElement(null, "span");
  loadingMessage.className = "library-state__message";
  loadingMessage.textContent = "Loading media…";
  catalogStateLoading.appendChild(spinner);
  catalogStateLoading.appendChild(loadingMessage);
  const catalogStateEmpty = new TestElement(null, "div");
  catalogStateEmpty.id = "catalog-state-empty";
  catalogStateEmpty.hidden = true;
  const catalogStateEmptyMessage = new TestElement(null, "p");
  catalogStateEmptyMessage.id = "catalog-state-empty-message";
  catalogStateEmpty.appendChild(catalogStateEmptyMessage);
  const catalogStateUnavailable = new TestElement(null, "div");
  catalogStateUnavailable.id = "catalog-state-unavailable";
  catalogStateUnavailable.hidden = true;
  const catalogStateError = new TestElement(null, "div");
  catalogStateError.id = "catalog-state-error";
  catalogStateError.hidden = true;
  catalogStateError.setAttribute("role", "alert");
  const errorMessage = new TestElement(null, "p");
  errorMessage.className = "library-state__message";
  errorMessage.textContent = "Media could not be loaded.";
  const catalogRetryButton = new TestElement(null, "button");
  catalogRetryButton.id = "catalog-retry-button";
  catalogRetryButton.type = "button";
  catalogRetryButton.textContent = "Retry";
  catalogStateError.appendChild(errorMessage);
  catalogStateError.appendChild(catalogRetryButton);
  const catalogResults = new TestElement(null, "div");
  catalogResults.id = "catalog-results";
  catalogResults.hidden = true;
  const catalogPrevButton = new TestElement(null, "button");
  const catalogNextButton = new TestElement(null, "button");
  const catalogPageSummary = new TestElement(null, "p");
  const catalogPageSizeSelect = new TestElement(null, "select");
  const elementsById = {
    "catalog-browser": catalogBrowser,
    "catalog-state-loading": catalogStateLoading,
    "catalog-state-empty": catalogStateEmpty,
    "catalog-state-empty-message": catalogStateEmptyMessage,
    "catalog-state-unavailable": catalogStateUnavailable,
    "catalog-state-error": catalogStateError,
    "catalog-retry-button": catalogRetryButton,
    "catalog-results": catalogResults,
  };
  const document = new TestDocument(elementsById);
  catalogBrowser.ownerDocument = document;
  Object.values(elementsById).forEach((element) => {
    element.ownerDocument = document;
  });

  const context = {
    console,
    document,
    fetch: fetchImpl,
    URLSearchParams,
    catalogBrowser,
    catalogStateLoading,
    catalogStateEmpty,
    catalogStateEmptyMessage,
    catalogStateUnavailable,
    catalogStateError,
    catalogRetryButton,
    catalogResults,
    catalogPrevButton,
    catalogNextButton,
    catalogPageSummary,
    catalogPageSizeSelect,
  };
  context.globalThis = context;
  vm.createContext(context);

  const functions = [
    "semanticArraysEqual",
    "snapshotCatalogQueryState",
    "buildCatalogQueryParams",
    "claimCatalogRequest",
    "catalogRequestOwnerIsCurrent",
    "releaseCatalogRequest",
    "showCatalogState",
    "renderCatalogEmptyState",
    "setCatalogPagination",
    "syncCatalogPageSizeControl",
    "cleanupCatalogCardMedia",
    "reconcileCatalogSelectedCard",
    "renderCatalogTagFilterStates",
    "renderCatalogSuccess",
    "loadCatalog",
  ].map((name) => {
    if (name === "setCatalogPagination") {
      return "function setCatalogPagination(page) { catalogPageSummary.textContent = `Page for ${page.total}`; }";
    }
    if (name === "syncCatalogPageSizeControl") {
      return "function syncCatalogPageSizeControl() {}";
    }
    if (name === "cleanupCatalogCardMedia") {
      return "function cleanupCatalogCardMedia() {}";
    }
    if (name === "reconcileCatalogSelectedCard") {
      return "function reconcileCatalogSelectedCard() {}";
    }
    if (name === "renderCatalogTagFilterStates") {
      return "function renderCatalogTagFilterStates() {}";
    }
    if (name === "renderCatalogSuccess") {
      return `
        function renderCatalogSuccess(page) {
          catalogResults.replaceChildren();
          catalogState.total = page.total;
          catalogState.offset = page.offset;
          catalogState.limit = page.limit;
          setCatalogPagination(page);
          if (page.items.length === 0) {
            renderCatalogEmptyState();
            showCatalogState("empty");
            return;
          }
          page.items.forEach((item) => {
            const card = document.createElement("article");
            card.textContent = item.media_id;
            catalogResults.appendChild(card);
          });
          showCatalogState("success");
        }
      `;
    }
    return productionFunction(name);
  }).join("\n");

  vm.runInContext(`
    const CATALOG_PAGE_SIZE_OPTIONS = [10, 30, 60, 90];
    const CATALOG_PAGE_SIZE = 30;
    const MEDIA_CATALOG_ENDPOINT = "/api/media";
    let catalogRequestToken = 0;
    let catalogRequestOwner = null;
    let catalogState = {
      q: "",
      tagKeys: [],
      collection: "",
      contentCategory: "",
      acquisitionSource: "",
      limit: 30,
      offset: 0,
      total: 0,
    };
    let lastCatalogLoadPromise = null;
    ${functions}
    catalogRetryButton.addEventListener("click", () => {
      lastCatalogLoadPromise = loadCatalog();
    });
  `, context, { filename: APP_PATH });

  return {
    catalogBrowser,
    catalogStateLoading,
    catalogStateEmpty,
    catalogStateEmptyMessage,
    catalogStateError,
    catalogRetryButton,
    catalogResults,
    catalogPageSummary,
    spinner,
    loadingMessage,
    errorMessage,
    run(code) {
      return vm.runInContext(code, context);
    },
  };
}

test("Gallery markup exposes polished loading, empty, and sanitized error states", () => {
  const loadingBlockStart = INDEX_SOURCE.indexOf('id="catalog-state-loading"');
  const resultsBlockStart = INDEX_SOURCE.indexOf('id="catalog-results"');
  assert.ok(loadingBlockStart !== -1 && resultsBlockStart > loadingBlockStart);
  const catalogStateMarkup = INDEX_SOURCE.slice(loadingBlockStart, resultsBlockStart);
  assert.match(catalogStateMarkup, /library-state__spinner/);
  assert.match(catalogStateMarkup, /Loading media…/);
  assert.match(catalogStateMarkup, /aria-hidden="true"/);
  assert.match(catalogStateMarkup, /id="catalog-state-empty-message"/);
  assert.match(catalogStateMarkup, /id="catalog-retry-button"/);
  assert.match(catalogStateMarkup, /Media could not be loaded\./);
  assert.doesNotMatch(catalogStateMarkup, /Loading\.\.\./);
  assert.doesNotMatch(catalogStateMarkup, /Loading catalog media\.\.\./);
  assert.match(STYLES_SOURCE, /\.library-state__spinner\s*\{/);
  assert.match(STYLES_SOURCE, /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.library-state__spinner/);
  assert.match(STYLES_SOURCE, /\.library-state--error\s*\{/);
  assert.match(STYLES_SOURCE, /min-height:\s*220px/);
});

test("Loading state renders with status semantics while the Gallery request is pending", async () => {
  const pending = deferred();
  const h = createCatalogHarness(async () => pending.promise);
  const loadPromise = h.run("loadCatalog()");

  assert.equal(h.catalogStateLoading.hidden, false);
  assert.equal(h.catalogResults.hidden, true);
  assert.equal(h.catalogStateEmpty.hidden, true);
  assert.equal(h.catalogStateError.hidden, true);
  assert.equal(h.catalogStateLoading.getAttribute("role"), "status");
  assert.equal(h.catalogStateLoading.getAttribute("aria-busy"), "true");
  assert.equal(h.catalogBrowser.getAttribute("aria-busy"), "true");
  assert.equal(h.loadingMessage.textContent, "Loading media…");
  assert.equal(h.spinner.getAttribute("aria-hidden"), "true");
  assert.equal(h.catalogPageSummary.textContent, "Loading catalog page...");

  pending.resolve(response({ items: [{ media_id: "media-1" }], total: 1, limit: 30, offset: 0, q: "" }));
  await loadPromise;

  assert.equal(h.catalogStateLoading.hidden, true);
  assert.equal(h.catalogResults.hidden, false);
  assert.equal(h.catalogBrowser.getAttribute("aria-busy"), "false");
  assert.equal(h.catalogStateLoading.getAttribute("aria-busy"), "false");
  assert.equal(h.catalogResults.children.length, 1);
});

test("Empty state is truthful for unfiltered and filtered zero-result responses", async () => {
  const responses = [
    response({ items: [], total: 0, limit: 30, offset: 0, q: "" }),
    response({ items: [], total: 0, limit: 30, offset: 0, q: "" }),
  ];
  let call = 0;
  const h = createCatalogHarness(async () => responses[call++]);

  await h.run("loadCatalog()");
  assert.equal(h.catalogStateEmpty.hidden, false);
  assert.equal(h.catalogStateLoading.hidden, true);
  assert.equal(h.catalogStateError.hidden, true);
  assert.equal(h.catalogStateEmptyMessage.textContent, "No media are available in this catalog view.");

  h.run("catalogState.contentCategory = 'meme'");
  await h.run("loadCatalog()");
  assert.equal(h.catalogStateEmpty.hidden, false);
  assert.equal(h.catalogStateEmptyMessage.textContent, "No media match the active filters.");
});

test("Error state stays sanitized and Retry reloads only the Gallery fetch", async () => {
  const calls = [];
  const h = createCatalogHarness(async (url) => {
    calls.push(String(url));
    if (calls.length === 1) {
      return response({ error: { code: "INTERNAL", message: "/srv/media/secret.db stacktrace token=abc" } }, 500);
    }
    return response({ items: [{ media_id: "media-ok" }], total: 1, limit: 30, offset: 0, q: "" });
  });

  await h.run("loadCatalog()");
  assert.equal(h.catalogStateError.hidden, false);
  assert.equal(h.catalogStateLoading.hidden, true);
  assert.equal(h.catalogStateEmpty.hidden, true);
  assert.equal(h.errorMessage.textContent, "Media could not be loaded.");
  assert.equal(h.catalogStateError.getAttribute("role"), "alert");
  assert.doesNotMatch(h.errorMessage.textContent, /\/srv\/media|stacktrace|token=/i);
  assert.equal(h.catalogPageSummary.textContent, "Catalog page unavailable.");
  assert.equal(calls.length, 1);

  h.catalogRetryButton.click();
  await h.run("lastCatalogLoadPromise");
  assert.equal(calls.length, 2);
  assert.equal(h.catalogStateError.hidden, true);
  assert.equal(h.catalogResults.hidden, false);
  assert.equal(h.catalogResults.children.length, 1);
  assert.equal(h.catalogResults.children[0].textContent, "media-ok");
});
