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
const COMMAND_SEARCH_INPUT_SETUP_START = APP_SOURCE.indexOf("if (commandSearchInput) {");
const COMMAND_SEARCH_INPUT_SETUP_END = APP_SOURCE.indexOf("\nif (commandSearchClear) {", COMMAND_SEARCH_INPUT_SETUP_START);
assert.notEqual(COMMAND_SEARCH_INPUT_SETUP_START, -1, "missing command Search input setup");
assert.notEqual(COMMAND_SEARCH_INPUT_SETUP_END, -1, "missing command Search input setup boundary");
const COMMAND_SEARCH_INPUT_SETUP = APP_SOURCE.slice(COMMAND_SEARCH_INPUT_SETUP_START, COMMAND_SEARCH_INPUT_SETUP_END);

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
      if (escaped) {
        escaped = false;
      } else if (character === "\\") {
        escaped = true;
      } else if (character === quote) {
        quote = null;
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

class TestEvent {
  constructor(type, { key = "", bubbles = true, detail = 0, pointerType = "" } = {}) {
    this.type = type;
    this.key = key;
    this.bubbles = bubbles;
    this.detail = detail;
    this.pointerType = pointerType;
    this.target = null;
    this.currentTarget = null;
    this.defaultPrevented = false;
    this.propagationStopped = false;
  }

  preventDefault() {
    this.defaultPrevented = true;
  }

  stopPropagation() {
    this.propagationStopped = true;
  }
}

class TestClassList {
  constructor(element) {
    this.element = element;
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

  toggle(name, force) {
    const enabled = force === undefined ? !this.values.has(name) : Boolean(force);
    if (enabled) this.values.add(name);
    else this.values.delete(name);
    return enabled;
  }

  toString() {
    return [...this.values].join(" ");
  }
}

function selectorMatcher(selector) {
  const match = selector.match(/^\.([a-z0-9_-]+)(?:\[data-tag-key="([^"]+)"\])?$/i);
  if (!match) throw new Error(`unsupported test selector: ${selector}`);
  return (element) => element.classList.contains(match[1])
    && (match[2] === undefined || element.dataset.tagKey === match[2]);
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
    this.classList = new TestClassList(this);
    this.hidden = false;
    this.disabled = false;
    this.textContent = "";
    this.type = "";
    this.value = "";
    this.focusCalls = 0;
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
    let current = this;
    while (current) {
      event.currentTarget = current;
      for (const listener of [...(current.listeners.get(event.type) || [])]) {
        listener(event);
      }
      if (!event.bubbles || event.propagationStopped) break;
      current = current.parentNode;
    }
    event.currentTarget = null;
    return !event.defaultPrevented;
  }

  click() {
    if (this.disabled) return;
    this.dispatchEvent(new TestEvent("click"));
  }

  focus() {
    if (!this.hidden && !this.disabled && this.ownerDocument.contains(this)) {
      this.focusCalls += 1;
      this.ownerDocument.activeElement = this;
    }
  }

  append(...nodes) {
    nodes.forEach((node) => this.appendChild(node));
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  replaceChildren(...nodes) {
    this.children.forEach((node) => {
      if (node.contains(this.ownerDocument.activeElement)) {
        this.ownerDocument.activeElement = this.ownerDocument.body;
      }
      node.parentNode = null;
    });
    this.children = [];
    this.append(...nodes);
  }

  setAttribute(name, value) {
    this.attributes.set(String(name).toLowerCase(), String(value));
  }

  getAttribute(name) {
    const key = String(name).toLowerCase();
    return this.attributes.has(key) ? this.attributes.get(key) : null;
  }

  contains(candidate) {
    let current = candidate;
    while (current) {
      if (current === this) return true;
      current = current.parentNode;
    }
    return false;
  }

  querySelectorAll(selector) {
    const matches = selectorMatcher(selector);
    const results = [];
    const visit = (node) => {
      node.children.forEach((child) => {
        if (matches(child)) results.push(child);
        visit(child);
      });
    };
    visit(this);
    return results;
  }

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] || null;
  }
}

class TestDocument {
  constructor() {
    this.body = new TestElement(this, "body");
    this.activeElement = this.body;
  }

  createElement(tagName) {
    return new TestElement(this, tagName);
  }

  contains(candidate) {
    return this.body.contains(candidate);
  }
}

function keyboardActivate(button, key) {
  const initialScrollY = button.ownerDocument.scrollY || 0;
  const keydown = new TestEvent("keydown", { key });
  button.dispatchEvent(keydown);
  if (!keydown.defaultPrevented && key === "Enter") button.click();
  const keyup = new TestEvent("keyup", { key });
  button.dispatchEvent(keyup);
  if (!keyup.defaultPrevented && key === " ") button.click();
  if (button.tagName !== "BUTTON" && key === " " && !keydown.defaultPrevented) {
    button.ownerDocument.scrollY = initialScrollY + 100;
  }
}

function pointerActivate(button, { detail = 1, pointerType = "mouse" } = {}) {
  button.dispatchEvent(new TestEvent("click", { detail, pointerType }));
}

function mountCardTagInteractions(h, tags) {
  h.context.item = { tags };
  const tagRegion = h.run("renderCatalogCardTags(item)");
  const card = h.document.createElement("article");
  const title = h.document.createElement("button");
  const mediaSurface = h.document.createElement("button");
  const contextualAction = h.document.createElement("button");
  const activations = { card: 0, cardKeyboard: 0, details: 0, playback: 0, contextual: 0 };
  card.addEventListener("click", () => { activations.card += 1; });
  card.addEventListener("keydown", () => { activations.cardKeyboard += 1; });
  title.addEventListener("click", () => { activations.details += 1; });
  mediaSurface.addEventListener("click", () => { activations.playback += 1; });
  contextualAction.addEventListener("click", () => { activations.contextual += 1; });
  card.append(mediaSurface, title, tagRegion, contextualAction);
  h.catalogResults.appendChild(card);
  return { tagRegion, activations };
}

function createInteractionHarness() {
  const document = new TestDocument();
  const catalogTagFilters = document.createElement("div");
  const catalogResults = document.createElement("div");
  const commandSearchInput = document.createElement("input");
  const commandSearchClear = document.createElement("button");
  const commandSearchSuggestions = document.createElement("ul");
  const catalogStateEmpty = document.createElement("p");
  document.body.append(catalogTagFilters, catalogResults, commandSearchInput, commandSearchClear, commandSearchSuggestions, catalogStateEmpty);
  const context = {
    console,
    document,
    URLSearchParams,
    catalogTagFilters,
    catalogResults,
    commandSearchInput,
    commandSearchClear,
    commandSearchSuggestions,
    catalogStateEmpty,
    catalogStateEmptyMessage: null,
    setTimeout,
    clearTimeout,
  };
  context.globalThis = context;
  vm.createContext(context);
  const functions = [
    "semanticArraysEqual",
    "selectedTagDefinition",
    "snapshotCatalogQueryState",
    "buildCatalogQueryParams",
    "setCatalogSearchText",
    "mediaCreatorAttributionFields",
    "mediaHasCreatorAttribution",
    "mediaCreatorChipLabel",
    "catalogCreatorFilterIsActive",
    "setCatalogCreatorFilter",
    "appendCatalogCreatorChip",
    "renderCatalogCardTags",
    "reconcileCatalogSelectedCard",
    "renderCatalogEmptyState",
    "renderCatalogTagFilterStates",
    "catalogTagDisplayName",
    "renderActiveCatalogTagFilters",
    "focusCatalogFilterChip",
    "catalogTagActivationShouldFocusChip",
    "activateCatalogTagFilter",
    "toggleCatalogCardTagFilter",
    "removeCatalogTagFilter",
    "deriveCatalogFallbackTitle",
    "closeCommandSearchSuggestions",
    "renderCommandSearchSuggestions",
  ].map(productionFunction).join("\n");
  vm.runInContext(`
    const CATALOG_PAGE_SIZE_OPTIONS = [10, 30, 60, 90];
    const CATALOG_PAGE_SIZE = 30;
    let catalogState = { q: "", tagKeys: [], collection: "", contentCategory: "", acquisitionSource: "", creatorAttributionKind: "", creatorStableId: "", creatorHandle: "", limit: 30, offset: 0, total: 0 };
    let canonicalTagDefinitions = [];
    let metadataWorkspace = { openMediaId: null };
    let catalogLoadCalls = 0;
    let commandSearchDebounceTimer = null;
    let commandSearchRequestToken = 0;
    let commandSearchActiveIndex = -1;
    let commandSearchCurrentSuggestions = [];
    function loadCatalog() { catalogLoadCalls += 1; }
    function syncCatalogFilterControls() {}
    ${functions}
    ${COMMAND_SEARCH_INPUT_SETUP}
  `, context, { filename: APP_PATH });
  return {
    context,
    document,
    catalogTagFilters,
    catalogResults,
    commandSearchInput,
    commandSearchClear,
    commandSearchSuggestions,
    catalogStateEmpty,
    run(code) {
      return vm.runInContext(code, context);
    },
  };
}

function createRequestHarness(fetch) {
  const document = new TestDocument();
  const catalogTagFilters = document.createElement("div");
  const commandSearchInput = document.createElement("input");
  const commandSearchClear = document.createElement("button");
  const commandSearchSuggestions = document.createElement("ul");
  commandSearchSuggestions.hidden = true;
  document.body.append(catalogTagFilters, commandSearchInput, commandSearchClear, commandSearchSuggestions);
  const pendingTimers = new Map();
  let nextTimerId = 1;
  const context = {
    console,
    fetch,
    URLSearchParams,
    catalogTagFilters,
    commandSearchInput,
    commandSearchClear,
    commandSearchSuggestions,
    setTimeout(callback, delay) {
      const timerId = nextTimerId;
      nextTimerId += 1;
      pendingTimers.set(timerId, { callback, delay });
      return timerId;
    },
    clearTimeout(timerId) {
      pendingTimers.delete(timerId);
    },
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
    "setCatalogSearchText",
    "removeCatalogTagFilter",
    "closeCommandSearchSuggestions",
    "resetCatalogSearchState",
    "resetCatalogToAllMedia",
    "loadCatalog",
  ].map(productionFunction).join("\n");
  vm.runInContext(`
    const CATALOG_PAGE_SIZE_OPTIONS = [10, 30, 60, 90];
    const CATALOG_PAGE_SIZE = 30;
    const MEDIA_CATALOG_ENDPOINT = "/api/media";
    let catalogRequestToken = 0;
    let catalogRequestOwner = null;
    let commandSearchDebounceTimer = null;
    let commandSearchRequestToken = 0;
    let commandSearchActiveIndex = -1;
    let commandSearchCurrentSuggestions = [];
    let catalogState = { q: "", tagKeys: [], collection: "", contentCategory: "", acquisitionSource: "", creatorAttributionKind: "", creatorStableId: "", creatorHandle: "", limit: 30, offset: 0, total: 0 };
    const catalogPrevButton = { disabled: false };
    const catalogNextButton = { disabled: false };
    const catalogPageSummary = { textContent: "" };
    let catalogVisibleState = "idle";
    let renderedPages = [];
    function renderActiveCatalogTagFilters() {}
    function renderCatalogTagFilterStates() {}
    function syncCatalogFilterControls() {}
    function advanceMetadataWorkspaceRevision() {}
    function showCatalogState(state) { catalogVisibleState = state; }
    function renderCatalogSuccess(page) {
      renderedPages.push(page.marker);
      catalogState.total = page.total;
      catalogState.offset = page.offset;
      catalogState.limit = page.limit;
      catalogVisibleState = "success";
    }
    ${functions}
    ${COMMAND_SEARCH_INPUT_SETUP}
  `, context, { filename: APP_PATH });
  return {
    context,
    commandSearchInput,
    commandSearchClear,
    commandSearchSuggestions,
    pendingTimers,
    run(code) {
      return vm.runInContext(code, context);
    },
  };
}

test("Search markup keeps an exact prominent-purpose placeholder and an independent accessible name", () => {
  const inputStart = INDEX_SOURCE.indexOf('id="command-search-input"');
  const input = INDEX_SOURCE.slice(inputStart, INDEX_SOURCE.indexOf(">", inputStart));
  assert.match(input, /placeholder="Search titles and tags"/);
  assert.match(input, /aria-label="Search catalog by title or tag"/);
  assert.notEqual("Search titles and tags", "Search catalog by title or tag");
  assert.match(INDEX_SOURCE, /header-search__prompt[^>]*aria-hidden="true"[^>]*>&gt;</);
  assert.match(INDEX_SOURCE, /id="catalog-tag-filters"[^>]*aria-label="Active tag filters"/);
});

test("Active filter chips share the unified Search control and use a bounded single-line rail", () => {
  const controlStart = INDEX_SOURCE.indexOf('<div class="header-search__control">');
  const filtersStart = INDEX_SOURCE.indexOf('id="catalog-tag-filters"');
  const suggestionsEnd = INDEX_SOURCE.indexOf("</ul>", filtersStart);
  const controlEnd = INDEX_SOURCE.indexOf("</div>", suggestionsEnd);
  const railStart = STYLES_SOURCE.indexOf(".catalog-tag-filters {");
  const railEnd = STYLES_SOURCE.indexOf(".catalog-filter-chip {", railStart);
  const railRule = STYLES_SOURCE.slice(railStart, railEnd);

  assert.ok(controlStart < filtersStart && filtersStart < suggestionsEnd && suggestionsEnd < controlEnd);
  assert.match(railRule, /flex-wrap: nowrap/);
  assert.match(railRule, /overflow-x: auto/);
  assert.match(railRule, /max-width: 360px/);
  assert.doesNotMatch(railRule, /flex-wrap: wrap/);
});

test("Terminal Search prompt uses a stable reduced-motion fallback", () => {
  const promptStart = STYLES_SOURCE.indexOf(".header-search__prompt {");
  const promptEnd = STYLES_SOURCE.indexOf(".header-search__input {", promptStart);
  const promptRule = STYLES_SOURCE.slice(promptStart, promptEnd);
  const reducedStart = STYLES_SOURCE.lastIndexOf("@media (prefers-reduced-motion: reduce)");
  const reducedRule = STYLES_SOURCE.slice(reducedStart);

  assert.match(promptRule, /font-weight: 900/);
  assert.match(promptRule, /header-search-prompt-pulse 1s ease-in-out infinite alternate/);
  assert.match(reducedRule, /\.header-search__prompt\s*{[^}]*animation: none;[^}]*opacity: 0\.82;/s);
});

test("Canonical card tags compose with text as ordered AND query parameters without duplicates", () => {
  const h = createInteractionHarness();
  h.run(`canonicalTagDefinitions = [
    { key: "alpha", display_name: "Alpha" },
    { key: "beta", display_name: "Beta" }
  ]`);
  const searchOffset = h.run("catalogState.offset = 60; setCatalogSearchText('needle'); catalogState.offset");
  assert.equal(searchOffset, 0);
  h.run("catalogState.offset = 60; activateCatalogTagFilter('alpha'); activateCatalogTagFilter('beta'); activateCatalogTagFilter('alpha')");

  const state = JSON.parse(h.run("JSON.stringify({ q: catalogState.q, tagKeys: catalogState.tagKeys, offset: catalogState.offset, loads: catalogLoadCalls })"));
  assert.deepEqual(state, { q: "needle", tagKeys: ["alpha", "beta"], offset: 0, loads: 2 });
  const params = h.run("buildCatalogQueryParams().toString()");
  assert.equal(params, "q=needle&tag=alpha&tag=beta&limit=30&offset=0");
  assert.equal(h.catalogTagFilters.children.length, 2);
  assert.equal(h.catalogTagFilters.hidden, false);
  assert.equal(h.catalogTagFilters.children[0].getAttribute("aria-label"), "Remove Alpha tag filter");
});

test("Pointer Gallery tag activation toggles without forcing chip focus or card actions", () => {
  const pointerCases = [
    { pointerType: "mouse", detail: 1 },
    { pointerType: "touch", detail: 0 },
    { pointerType: "pen", detail: 0 },
  ];
  pointerCases.forEach((pointer) => {
    const h = createInteractionHarness();
    const { tagRegion, activations } = mountCardTagInteractions(h, [{ key: "alpha", display_name: "Alpha" }]);
    const tagButton = tagRegion.children[0];
    tagButton.focus();

    pointerActivate(tagButton, pointer);

    const chip = h.catalogTagFilters.children[0];
    assert.deepEqual(activations, { card: 0, cardKeyboard: 0, details: 0, playback: 0, contextual: 0 });
    assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha");
    assert.equal(tagButton.getAttribute("aria-pressed"), "true");
    assert.equal(chip.focusCalls, 0, `${pointer.pointerType} activation must not focus the chip`);
    assert.equal(h.document.activeElement, tagButton);

    h.run("catalogState.offset = 60");
    pointerActivate(tagButton, pointer);

    assert.deepEqual(activations, { card: 0, cardKeyboard: 0, details: 0, playback: 0, contextual: 0 });
    assert.equal(h.run("catalogState.tagKeys.length"), 0);
    assert.equal(h.run("catalogState.offset"), 0);
    assert.equal(h.run("catalogLoadCalls"), 2);
    assert.equal(tagButton.getAttribute("aria-pressed"), "false");
    assert.equal(h.catalogTagFilters.hidden, true);
    assert.equal(chip.focusCalls, 0, `${pointer.pointerType} removal must not focus a detached chip`);
    assert.equal(h.document.activeElement, tagButton);
  });
});

test("Native keyboard tag addition is isolated from card actions and focuses the active chip", () => {
  const h = createInteractionHarness();
  const { tagRegion, activations } = mountCardTagInteractions(h, [{ key: "alpha", display_name: "Alpha" }]);

  const tagButton = tagRegion.children[0];
  assert.equal(tagRegion.getAttribute("role"), "group");
  assert.equal(tagRegion.getAttribute("aria-label"), "Media tags");
  assert.equal(tagButton.tagName, "BUTTON");
  assert.equal(tagButton.getAttribute("aria-label"), "Filter Gallery by Alpha");
  assert.equal(tagButton.getAttribute("aria-pressed"), "false");
  keyboardActivate(tagButton, " ");

  assert.deepEqual(activations, { card: 0, cardKeyboard: 0, details: 0, playback: 0, contextual: 0 });
  assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha");
  assert.equal(tagButton.getAttribute("aria-pressed"), "true");
  assert.equal(h.document.activeElement, h.catalogTagFilters.children[0]);
  assert.equal(h.catalogTagFilters.children[0].focusCalls, 1);
  assert.equal(h.catalogTagFilters.children[0].getAttribute("aria-label"), "Remove Alpha tag filter");

  assert.equal(h.document.scrollY || 0, 0);
});

test("Keyboard card-tag removal focuses next, previous, then Search without detached focus", () => {
  const cases = [
    { active: ["alpha", "beta", "gamma"], remove: "beta", expected: "gamma", key: " " },
    { active: ["alpha", "beta"], remove: "beta", expected: "alpha", key: "Enter" },
    { active: ["alpha"], remove: "alpha", expected: "search", key: "Enter" },
  ];
  cases.forEach(({ active, remove, expected, key }) => {
    const h = createInteractionHarness();
    h.run(`canonicalTagDefinitions = [
      { key: "alpha", display_name: "Alpha" },
      { key: "beta", display_name: "Beta" },
      { key: "gamma", display_name: "Gamma" }
    ]; ${active.map((key) => `activateCatalogTagFilter("${key}")`).join("; ")}`);
    const { tagRegion, activations } = mountCardTagInteractions(h, [
      { key: "alpha", display_name: "Alpha" },
      { key: "beta", display_name: "Beta" },
      { key: "gamma", display_name: "Gamma" },
    ]);
    const tagButton = tagRegion.children.find((button) => button.dataset.tagKey === remove);
    assert.equal(tagButton.getAttribute("aria-pressed"), "true");
    tagButton.focus();
    h.run("catalogState.offset = 90");

    keyboardActivate(tagButton, key);

    assert.deepEqual(activations, { card: 0, cardKeyboard: 0, details: 0, playback: 0, contextual: 0 });
    assert.equal(tagButton.getAttribute("aria-pressed"), "false");
    assert.equal(h.run("catalogState.offset"), 0);
    assert.equal(h.document.scrollY || 0, 0);
    assert.ok(h.document.contains(h.document.activeElement), "focus must remain on an attached element");
    if (expected === "search") {
      assert.equal(h.document.activeElement, h.commandSearchInput);
    } else {
      assert.equal(h.document.activeElement.dataset.tagKey, expected);
    }
  });
});

test("Card-tag add remove add cycles stay duplicate-free and synchronize aria-pressed", () => {
  const h = createInteractionHarness();
  const { tagRegion } = mountCardTagInteractions(h, [{ key: "alpha", display_name: "Alpha" }]);
  const tagButton = tagRegion.children[0];

  pointerActivate(tagButton);
  pointerActivate(tagButton);
  pointerActivate(tagButton);

  assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha");
  assert.equal(h.catalogTagFilters.children.length, 1);
  assert.equal(tagButton.getAttribute("aria-pressed"), "true");
  assert.equal(h.run("catalogLoadCalls"), 3);
});

test("Pointer provenance and unrelated interrupted pointer events do not contaminate later keyboard activation", () => {
  const h = createInteractionHarness();
  const { tagRegion } = mountCardTagInteractions(h, [
    { key: "alpha", display_name: "Alpha" },
    { key: "beta", display_name: "Beta" },
  ]);
  pointerActivate(tagRegion.children[0], { pointerType: "mouse", detail: 1 });
  const alphaChip = h.catalogTagFilters.children[0];
  assert.equal(alphaChip.focusCalls, 0);

  const unrelated = h.document.createElement("button");
  unrelated.dispatchEvent(new TestEvent("pointerdown", { pointerType: "pen", detail: 0 }));
  keyboardActivate(tagRegion.children[1], "Enter");

  const betaChip = h.catalogTagFilters.children[1];
  assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha,beta");
  assert.equal(alphaChip.focusCalls, 0);
  assert.equal(betaChip.focusCalls, 1);
  assert.equal(h.document.activeElement, betaChip);
});

test("Search tag suggestions preserve pointer focus and provide deterministic keyboard chip focus", () => {
  const pointerHarness = createInteractionHarness();
  pointerHarness.commandSearchInput.value = "alp";
  pointerHarness.run("setCatalogSearchText('alp'); renderCommandSearchSuggestions([], [{ key: 'alpha', display_name: 'Alpha' }], [])");
  const pointerSuggestion = pointerHarness.commandSearchSuggestions.children[0];
  pointerHarness.commandSearchInput.focus();
  pointerActivate(pointerSuggestion, { pointerType: "touch", detail: 0 });

  const pointerChip = pointerHarness.catalogTagFilters.children[0];
  assert.equal(pointerHarness.run("catalogState.tagKeys.join(',')"), "alpha");
  assert.equal(pointerHarness.run("catalogState.q"), "");
  assert.equal(pointerHarness.commandSearchInput.value, "");
  assert.equal(pointerHarness.run("buildCatalogQueryParams().toString()"), "tag=alpha&limit=30&offset=0");
  assert.equal(pointerChip.focusCalls, 0);
  assert.equal(pointerHarness.document.activeElement, pointerHarness.commandSearchInput);
  assert.equal(pointerHarness.commandSearchSuggestions.hidden, true);

  const keyboardHarness = createInteractionHarness();
  keyboardHarness.commandSearchInput.value = "bet";
  keyboardHarness.run("setCatalogSearchText('bet'); renderCommandSearchSuggestions([], [{ key: 'beta', display_name: 'Beta' }], []); commandSearchActiveIndex = 0");
  keyboardHarness.commandSearchInput.dispatchEvent(new TestEvent("keydown", { key: "Enter" }));

  const keyboardChip = keyboardHarness.catalogTagFilters.children[0];
  assert.equal(keyboardHarness.run("catalogState.tagKeys.join(',')"), "beta");
  assert.equal(keyboardHarness.run("catalogState.q"), "");
  assert.equal(keyboardHarness.commandSearchInput.value, "");
  assert.equal(keyboardChip.focusCalls, 1);
  assert.equal(keyboardHarness.document.activeElement, keyboardChip);
  assert.equal(keyboardHarness.commandSearchSuggestions.hidden, true);
});

test("Chip removal prefers next, then previous, then Search focus and permits removing all tags", () => {
  const h = createInteractionHarness();
  h.run(`canonicalTagDefinitions = [
    { key: "alpha", display_name: "Alpha" },
    { key: "beta", display_name: "Beta" },
    { key: "gamma", display_name: "Gamma" }
  ]; activateCatalogTagFilter("alpha"); activateCatalogTagFilter("beta"); activateCatalogTagFilter("gamma")`);

  keyboardActivate(h.catalogTagFilters.children[1], "Enter");
  assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha,gamma");
  assert.equal(h.document.activeElement.dataset.tagKey, "gamma");

  keyboardActivate(h.document.activeElement, "Enter");
  assert.equal(h.run("catalogState.tagKeys.join(',')"), "alpha");
  assert.equal(h.document.activeElement.dataset.tagKey, "alpha");

  keyboardActivate(h.document.activeElement, "Enter");
  assert.equal(h.run("catalogState.tagKeys.length"), 0);
  assert.equal(h.catalogTagFilters.hidden, true);
  assert.equal(h.document.activeElement, h.commandSearchInput);
});

test("Selected-card and empty-result presentation reconcile with filtered results", () => {
  const h = createInteractionHarness();
  const selected = h.document.createElement("article");
  selected.className = "catalog-card";
  selected.dataset.mediaId = "media-a";
  const other = h.document.createElement("article");
  other.className = "catalog-card";
  other.dataset.mediaId = "media-b";
  h.catalogResults.append(selected, other);
  h.run('metadataWorkspace.openMediaId = "media-a"; reconcileCatalogSelectedCard()');
  assert.equal(selected.classList.contains("catalog-card--selected"), true);
  assert.equal(other.classList.contains("catalog-card--selected"), false);

  h.catalogResults.replaceChildren(other);
  h.run("reconcileCatalogSelectedCard(); catalogState.q = 'needle'; catalogState.tagKeys = ['alpha']; renderCatalogEmptyState()");
  assert.equal(other.classList.contains("catalog-card--selected"), false);
  assert.equal(h.catalogStateEmpty.textContent, "No media match the current search and tag filters.");

  h.run("catalogState.q = ''; catalogState.tagKeys = []; catalogState.contentCategory = 'meme'; renderCatalogEmptyState()");
  assert.equal(h.catalogStateEmpty.textContent, "No media match the active filters.");
});

test("Catalog request owners reject stale success, error, and finally work under adversarial timing", async () => {
  const requests = [];
  const h = createRequestHarness((url) => {
    const pending = deferred();
    requests.push({ url: String(url), pending });
    return pending.promise;
  });

  const oldRequest = h.run("loadCatalog()");
  h.run("setCatalogSearchText('new title'); catalogState.tagKeys = ['alpha', 'beta']; catalogState.collection = 'processed'; catalogState.contentCategory = 'movie'; catalogState.acquisitionSource = ''; catalogState.offset = 30");
  const newRequest = h.run("loadCatalog()");
  assert.equal(requests[1].url, "/api/media?q=new+title&tag=alpha&tag=beta&collection=processed&content_category=movie&limit=30&offset=30");

  requests[0].pending.resolve(response({ marker: "old", items: [], total: 1, limit: 30, offset: 0, q: "" }));
  await oldRequest;
  assert.equal(h.run("catalogRequestOwner.token"), 2, "stale finally must not release the newer owner");
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);

  requests[1].pending.resolve(response({ marker: "new", items: [], total: 1, limit: 30, offset: 30, q: "new title" }));
  await newRequest;
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), ["new"]);
  assert.equal(h.run("catalogRequestOwner"), null);

  const staleError = h.run("setCatalogSearchText('older error'); loadCatalog()");
  h.run("setCatalogSearchText('current'); catalogState.tagKeys = []; catalogState.collection = ''; catalogState.offset = 0");
  const current = h.run("loadCatalog()");
  requests[3].pending.resolve(response({ marker: "current", items: [], total: 0, limit: 30, offset: 0, q: "current" }));
  await current;
  requests[2].pending.reject(new Error("stale network failure"));
  await staleError;

  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), ["new", "current"]);
  assert.equal(h.run("catalogVisibleState"), "success");
  assert.notEqual(h.run("catalogPageSummary.textContent"), "Catalog page unavailable.");
});

test("A stale catalog response cannot restore results for a removed active tag", async () => {
  const requests = [];
  const h = createRequestHarness((url) => {
    const pending = deferred();
    requests.push({ url: String(url), pending });
    return pending.promise;
  });
  h.run("catalogState.tagKeys = ['alpha']; catalogState.offset = 30");
  const staleTaggedRequest = h.run("loadCatalog()");
  assert.equal(requests[0].url, "/api/media?tag=alpha&limit=30&offset=30");

  assert.equal(h.run("removeCatalogTagFilter('alpha')"), true);
  assert.equal(h.run("catalogState.tagKeys.length"), 0);
  assert.equal(h.run("catalogState.offset"), 0);
  assert.equal(requests[1].url, "/api/media?limit=30&offset=0");

  requests[0].pending.resolve(response({ marker: "stale tagged", items: [{ media_id: "old" }], total: 1, limit: 30, offset: 30 }));
  await staleTaggedRequest;
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);
  assert.equal(h.run("catalogState.tagKeys.length"), 0);

  requests[1].pending.resolve(response({ marker: "current untagged", items: [], total: 0, limit: 30, offset: 0 }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), ["current untagged"]);
});

test("All media reset invalidates pending search work and rejects a pre-reset catalog response", async () => {
  const requests = [];
  const h = createRequestHarness((url) => {
    const pending = deferred();
    requests.push({ url: String(url), pending });
    return pending.promise;
  });

  const staleRequest = h.run("setCatalogSearchText('stale'); catalogState.tagKeys = ['alpha']; catalogState.collection = 'processed'; catalogState.contentCategory = 'movie'; catalogState.acquisitionSource = ''; catalogState.offset = 30; loadCatalog()");
  h.commandSearchInput.value = "stale";
  h.commandSearchInput.dispatchEvent(new TestEvent("input"));
  const staleDebounce = [...h.pendingTimers.values()][0];
  h.commandSearchSuggestions.hidden = false;
  h.commandSearchSuggestions.appendChild(h.commandSearchInput.ownerDocument.createElement("li"));

  h.run("resetCatalogToAllMedia()");
  assert.equal(h.pendingTimers.size, 0);
  assert.equal(h.commandSearchInput.value, "");
  assert.equal(h.commandSearchClear.hidden, true);
  assert.equal(h.commandSearchSuggestions.hidden, true);
  assert.equal(h.commandSearchSuggestions.children.length, 0);
  assert.equal(h.run("catalogState.q"), "");
  assert.equal(h.run("catalogState.tagKeys.length"), 0);
  assert.equal(h.run("catalogState.collection"), "");
  assert.equal(h.run("catalogState.contentCategory"), "");
  assert.equal(h.run("catalogState.acquisitionSource"), "");
  assert.equal(h.run("catalogState.offset"), 0);
  assert.equal(requests[1].url, "/api/media?limit=30&offset=0");

  staleDebounce.callback();
  assert.equal(h.run("catalogState.q"), "", "a cleared debounce cannot restore the old query");
  assert.equal(requests.length, 2, "a stale debounce cannot issue a duplicate reset load");

  requests[0].pending.resolve(response({ marker: "stale", items: [{ media_id: "old" }], total: 1, limit: 30, offset: 30 }));
  await staleRequest;
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);

  requests[1].pending.resolve(response({ marker: "reset", items: [], total: 0, limit: 30, offset: 0, q: "" }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), ["reset"]);
});

test("A stale catalog response cannot cross Gallery category or source changes", async () => {
  const requests = [];
  const h = createRequestHarness((url) => {
    const pending = deferred();
    requests.push({ url: String(url), pending });
    return pending.promise;
  });

  const staleRequest = h.run("loadCatalog()");
  h.run("catalogState.contentCategory = 'youtube'; catalogState.acquisitionSource = ''; loadCatalog()");
  assert.equal(requests[1].url, "/api/media?content_category=youtube&limit=30&offset=0");

  requests[0].pending.resolve(response({ marker: "stale", items: [], total: 1, limit: 30, offset: 0 }));
  await staleRequest;
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);

  requests[1].pending.resolve(response({ marker: "current", items: [], total: 0, limit: 30, offset: 0 }));
  await new Promise((resolve) => setImmediate(resolve));
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), ["current"]);
});

test("Raw Search changes reject the current catalog owner before the debounce claims a successor", async () => {
  const requests = [];
  const h = createRequestHarness((url) => {
    const pending = deferred();
    requests.push({ url: String(url), pending });
    return pending.promise;
  });

  const staleSuccess = h.run("loadCatalog()");
  h.commandSearchInput.value = "raw successor";
  h.commandSearchInput.dispatchEvent(new TestEvent("input"));
  assert.equal(h.pendingTimers.size, 1, "the real Search debounce must remain pending");
  assert.equal(requests.length, 1, "the successor request must not be claimed yet");
  requests[0].pending.resolve(response({ marker: "stale raw success", items: [{ media_id: "old" }], total: 91, limit: 30, offset: 0 }));
  await staleSuccess;

  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);
  assert.equal(h.run("catalogState.total"), 0);
  assert.notEqual(h.run("catalogVisibleState"), "success");
  h.run("clearTimeout(commandSearchDebounceTimer); commandSearchDebounceTimer = null");
  assert.equal(h.pendingTimers.size, 0);

  h.run("setCatalogSearchText('error owner')");
  const staleError = h.run("loadCatalog()");
  h.commandSearchInput.value = "raw error successor";
  h.commandSearchInput.dispatchEvent(new TestEvent("input"));
  assert.equal(h.pendingTimers.size, 1);
  assert.equal(requests.length, 2);
  requests[1].pending.reject(new Error("stale raw Search failure"));
  await staleError;

  assert.notEqual(h.run("catalogVisibleState"), "error");
  assert.notEqual(h.run("catalogPageSummary.textContent"), "Catalog page unavailable.");
  h.run("clearTimeout(commandSearchDebounceTimer); commandSearchDebounceTimer = null");
  assert.equal(h.pendingTimers.size, 0);

  h.run("setCatalogSearchText('release owner')");
  const releaseProbe = h.run("loadCatalog()");
  const releaseOwnerToken = h.run("catalogRequestOwner.token");
  h.run("setCatalogSearchText('raw release successor'); claimCatalogRequest()");
  const newerOwnerToken = h.run("catalogRequestOwner.token");
  assert.ok(newerOwnerToken > releaseOwnerToken);
  requests[2].pending.resolve(response({ marker: "stale release", items: [], total: 7, limit: 30, offset: 0 }));
  await releaseProbe;

  assert.equal(h.run("catalogRequestOwner.token"), newerOwnerToken, "stale finally must not release the newer owner");
  assert.deepEqual(JSON.parse(h.run("JSON.stringify(renderedPages)")), []);
});
