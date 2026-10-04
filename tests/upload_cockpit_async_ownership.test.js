const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const INDEX_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const INDEX_SOURCE = fs.readFileSync(INDEX_PATH, "utf8");
const RECOVERY_KEY = "framenest.upload.recovery.v1";
const RECOVERY_KEY_CURRENT = "kronika.upload.recovery.v1";

const UPLOAD_A = "11111111-1111-4111-8111-111111111111";
const UPLOAD_B = "22222222-2222-4222-8222-222222222222";
const UPLOAD_C = "33333333-3333-4333-8333-333333333333";
const MEDIA_A = 101;
const MEDIA_B = 202;
const LOCATION_A = 1001;
const LOCATION_B = 2002;

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((promiseResolve, promiseReject) => {
    resolve = promiseResolve;
    reject = promiseReject;
  });
  return { promise, resolve, reject };
}

function flushPromises() {
  return new Promise((resolve) => {
    setImmediate(resolve);
  });
}

async function flushAll() {
  await flushPromises();
  await flushPromises();
  await flushPromises();
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function snapshot(
  id,
  state,
  receivedSizeBytes,
  declaredSizeBytes = 8,
  displayFilename = "sample.gif",
) {
  return {
    id,
    state,
    display_filename: displayFilename,
    declared_size_bytes: declaredSizeBytes,
    received_size_bytes: receivedSizeBytes,
    expires_at: 999999,
    failure_code: null,
  };
}

function makeFile(name = "sample.gif", size = 8, lastModified = 1234) {
  return {
    name,
    size,
    lastModified,
    slice(start, end) {
      return {
        name,
        start,
        end,
        size: end - start,
      };
    },
  };
}

function requestMethod(options) {
  return options && options.method ? String(options.method).toUpperCase() : "GET";
}

function createFetchController() {
  const calls = [];
  const queued = [];

  function enqueue(match, result) {
    queued.push({ match, result });
  }

  async function fetch(url, options = {}) {
    const call = {
      url: String(url),
      method: requestMethod(options),
      options,
    };
    calls.push(call);
    const index = queued.findIndex((entry) => entry.match(call));
    if (index !== -1) {
      const [entry] = queued.splice(index, 1);
      const result = typeof entry.result === "function" ? entry.result(call) : entry.result;
      return result && typeof result.then === "function" ? result : Promise.resolve(result);
    }
    return defaultFetchResponse(call);
  }

  function defaultFetchResponse(call) {
    if (call.url === "/health") return response({ status: "ok" });
    if (call.url === "/api/ai/media-suggestion-capability") {
      return response({ available: false, status: "not_configured" });
    }
    if (call.url === "/api/status/cloud") {
      return response({ server: "connected", connection: "loopback" });
    }
    if (call.url === "/api/uploads/capability") {
      return response({
        uploads_enabled: true,
        max_total_size_bytes: 1024,
        max_chunk_size_bytes: 4,
        session_ttl_seconds: 60,
      });
    }
    if (call.url === "/api/canonical-tags") return response({ tags: [] });
    if (call.url.startsWith("/api/media?")) {
      return response({ items: [], total: 0, offset: 0, limit: 30, q: "" });
    }
    if (call.url === "/api/libraries") return response({ libraries: [] });
    return response({ error: { code: "UPLOAD_SESSION_NOT_FOUND" } }, 404);
  }

  return {
    calls,
    fetch,
    enqueue,
    clearCalls() {
      calls.length = 0;
    },
    matching(method, url) {
      return calls.filter((call) => call.method === method && call.url === url);
    },
    matchingPrefix(method, prefix) {
      return calls.filter((call) => call.method === method && call.url.startsWith(prefix));
    },
  };
}

function createClassList() {
  const values = new Set();
  return {
    add(...names) {
      names.forEach((name) => values.add(name));
    },
    remove(...names) {
      names.forEach((name) => values.delete(name));
    },
    toggle(name, force) {
      if (force === undefined ? !values.has(name) : force) {
        values.add(name);
        return true;
      }
      values.delete(name);
      return false;
    },
    contains(name) {
      return values.has(name);
    },
  };
}

function canReceiveProgrammaticFocus(element) {
  if (element.hidden || element.disabled) return false;
  if (element.hasAttribute("tabindex")) return true;
  if (["BUTTON", "INPUT", "SELECT", "TEXTAREA"].includes(element.tagName)) return true;
  return element.tagName === "A" && element.hasAttribute("href");
}

function isInNormalTabSequence(element) {
  if (!canReceiveProgrammaticFocus(element)) return false;
  const tabindex = element.getAttribute("tabindex");
  return tabindex === null || Number(tabindex) >= 0;
}

function createElement(document, tagName = "div") {
  const attributes = new Map();
  const listeners = new Map();
  const element = {
    tagName: String(tagName).toUpperCase(),
    children: [],
    parentNode: null,
    dataset: {},
    style: {},
    classList: createClassList(),
    contains(node) {
      let current = node;
      while (current) {
        if (current === this) return true;
        current = current.parentNode;
      }
      return false;
    },
    hidden: false,
    disabled: false,
    _textContent: "",
    get textContent() {
      if (this.children.length > 0) {
        return this.children.map((child) => {
          if (child == null) return "";
          if (typeof child === "string") return child;
          return child.textContent || "";
        }).join("");
      }
      return this._textContent;
    },
    set textContent(value) {
      this._textContent = String(value);
      this.children = [];
    },
    value: "",
    checked: false,
    files: [],
    title: "",
    type: "",
    max: 100,
    href: "",
    src: "",
    focusCount: 0,
    showModalCount: 0,
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    removeEventListener(type, listener) {
      if (!listener) {
        listeners.delete(type);
        return;
      }
      const retained = (listeners.get(type) || []).filter((candidate) => candidate !== listener);
      if (retained.length > 0) listeners.set(type, retained);
      else listeners.delete(type);
    },
    dispatchEvent(event) {
      if (!event.target) event.target = this;
      let current = this;
      while (current) {
        event.currentTarget = current;
        const currentListeners = current.__listeners.get(event.type) || [];
        for (const listener of [...currentListeners]) {
          listener(event);
          if (event.immediatePropagationStopped) break;
        }
        if (event.propagationStopped || event.bubbles === false) break;
        current = current.parentNode;
      }
      event.currentTarget = null;
      return !event.defaultPrevented;
    },
    append(...nodes) {
      nodes.forEach((node) => {
        if (node && typeof node === "object") node.parentNode = this;
      });
      this.children.push(...nodes);
    },
    appendChild(node) {
      if (node && typeof node === "object") node.parentNode = this;
      this.children.push(node);
      return node;
    },
    replaceChildren(...nodes) {
      this.children.forEach((node) => {
        if (node && node.parentNode === this) node.parentNode = null;
      });
      nodes.forEach((node) => {
        if (node && typeof node === "object") node.parentNode = this;
      });
      this.children = [...nodes];
    },
    remove() {},
    focus() {
      if (!canReceiveProgrammaticFocus(this)) return;
      document.activeElement = this;
      this.focusCount += 1;
    },
    setAttribute(name, value) {
      const normalizedName = String(name).toLowerCase();
      attributes.set(normalizedName, String(value));
      if (normalizedName === "hidden") this.hidden = true;
      if (normalizedName === "disabled") this.disabled = true;
      if (normalizedName === "href") this.href = String(value);
      if (normalizedName === "type") this.type = String(value);
      if (normalizedName === "open" && this.tagName === "DIALOG") {
        document.__modalStack = document.__modalStack.filter((dialog) => dialog !== this);
        document.__modalStack.push(this);
      }
    },
    removeAttribute(name) {
      const normalizedName = String(name).toLowerCase();
      attributes.delete(normalizedName);
      if (normalizedName === "hidden") this.hidden = false;
      if (normalizedName === "disabled") this.disabled = false;
      if (normalizedName === "href") this.href = "";
      if (normalizedName === "open" && this.tagName === "DIALOG") {
        document.__modalStack = document.__modalStack.filter((dialog) => dialog !== this);
      }
    },
    hasAttribute(name) {
      return attributes.has(String(name).toLowerCase());
    },
    getAttribute(name) {
      const normalizedName = String(name).toLowerCase();
      return attributes.has(normalizedName) ? attributes.get(normalizedName) : null;
    },
    querySelector() {
      return createElement(document);
    },
    querySelectorAll() {
      return [];
    },
    closest() {
      return null;
    },
    cloneNode() {
      return createElement(document, tagName);
    },
    showModal() {
      this.showModalCount += 1;
      attributes.set("open", "");
      document.__modalStack = document.__modalStack.filter((dialog) => dialog !== this);
      document.__modalStack.push(this);
    },
    close() {
      attributes.delete("open");
      document.__modalStack = document.__modalStack.filter((dialog) => dialog !== this);
    },
    __listeners: listeners,
  };
  return element;
}

function applySelectorDefaults(selector, element) {
  if (selector.startsWith("#")) {
    const id = selector.slice(1).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    const markup = INDEX_SOURCE.match(new RegExp(`<([a-z][a-z0-9-]*)\\b([^>]*\\bid="${id}"[^>]*)>`, "i"));
    if (markup) {
      element.tagName = markup[1].toUpperCase();
      for (const attribute of markup[2].matchAll(/([a-z][a-z0-9:-]*)(?:="([^"]*)")?/gi)) {
        element.setAttribute(attribute[1], attribute[2] === undefined ? "" : attribute[2]);
      }
    }
  }
  if (selector === "#upload-cancel-button") {
    element.textContent = "Cancel";
  }
  if (selector === "#upload-duplicate-keep-button") {
    element.textContent = "Keep as separate item";
  }
  if (selector === "#upload-duplicate-discard-button") {
    element.textContent = "Discard duplicate";
  }
}

function createDocument() {
  const elements = new Map();
  const listeners = new Map();
  const selectorParents = new Map([
    ["#confirmation-dialog-title", "#confirmation-dialog"],
    ["#confirmation-dialog-message", "#confirmation-dialog"],
    ["#confirmation-dismiss-button", "#confirmation-dialog"],
    ["#confirmation-confirm-button", "#confirmation-dialog"],
    ["#upload-dialog-title", "#upload-dialog"],
    ["#upload-close-button", "#upload-dialog"],
    ["#upload-file-input", "#upload-dialog"],
    ["#upload-message", "#upload-dialog"],
    ["#upload-start-button", "#upload-dialog"],
    ["#upload-pause-button", "#upload-dialog"],
    ["#upload-resume-button", "#upload-dialog"],
    ["#upload-duplicate-keep-button", "#upload-dialog"],
    ["#upload-duplicate-discard-button", "#upload-dialog"],
    ["#upload-cancel-button", "#upload-dialog"],
    ["#metadata-workspace-title", "#metadata-dialog"],
    ["#metadata-close-button", "#metadata-dialog"],
    ["#metadata-title-input", "#metadata-dialog"],
    ["#metadata-description-input", "#metadata-dialog"],
    ["#metadata-tag-search-input", "#metadata-dialog"],
    ["#metadata-save-button", "#metadata-dialog"],
    ["#metadata-ai-analyze-button", "#metadata-dialog"],
    ["#metadata-ai-suggestion-dropdown", "#metadata-dialog"],
    ["#metadata-ai-suggestion-toggle", "#metadata-ai-suggestion-dropdown"],
    ["#metadata-ai-suggestion-list", "#metadata-ai-suggestion-dropdown"],
    ["#metadata-ai-suggestion-toggle-label", "#metadata-ai-suggestion-toggle"],
    ["#metadata-ai-title-strip", "#metadata-dialog"],
    ["#metadata-ai-description-strip", "#metadata-dialog"],
    ["#metadata-ai-tags-strip", "#metadata-dialog"],
    ["#metadata-ai-heading", "#metadata-dialog"],
    ["#metadata-ai-filename-note", "#metadata-dialog"],
    ["#metadata-ai-details-toggle", "#metadata-dialog"],
    ["#metadata-ai-origin", "#metadata-dialog"],
    ["#metadata-durable-ai-suggestion", "#metadata-dialog"],
    ["#metadata-durable-ai-title", "#metadata-dialog"],
    ["#metadata-durable-ai-description", "#metadata-dialog"],
    ["#metadata-durable-ai-genres", "#metadata-dialog"],
    ["#metadata-durable-ai-tags", "#metadata-dialog"],
    ["#metadata-durable-ai-collection-row", "#metadata-dialog"],
    ["#metadata-durable-ai-collection", "#metadata-dialog"],
    ["#metadata-durable-ai-filename-row", "#metadata-dialog"],
    ["#metadata-durable-ai-filename", "#metadata-dialog"],
    ["#metadata-ai-capability", "#metadata-dialog"],
    ["#metadata-ai-status", "#metadata-dialog"],
    ["#metadata-discard-button", "#metadata-dialog"],
    ["#media-details-close", "#media-details-dialog"],
    ["#media-details-edit", "#media-details-dialog"],
    ["#status-close-button", "#status-dialog"],
  ]);
  const document = {
    activeElement: null,
    parentNode: null,
    __listeners: listeners,
    __modalStack: [],
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    removeEventListener(type, listener) {
      const retained = (listeners.get(type) || []).filter((candidate) => candidate !== listener);
      if (retained.length > 0) listeners.set(type, retained);
      else listeners.delete(type);
    },
    querySelector(selector) {
      if (!elements.has(selector)) {
        const element = createElement(document);
        applySelectorDefaults(selector, element);
        elements.set(selector, element);
        const parentSelector = selectorParents.get(selector);
        if (parentSelector) {
          element.parentNode = document.querySelector(parentSelector);
        } else {
          element.parentNode = document;
        }
      }
      return elements.get(selector);
    },
    querySelectorAll() {
      return [];
    },
    createElement(tagName) {
      return createElement(document, tagName);
    },
    createElementNS(_namespace, tagName) {
      return createElement(document, tagName);
    },
    createTextNode(value) {
      return { textContent: String(value) };
    },
    createDocumentFragment() {
      return createElement(document, "fragment");
    },
  };
  document.activeElement = createElement(document);
  return document;
}

function createLocalStorage() {
  const values = new Map();
  return {
    getItem(key) {
      return values.has(key) ? values.get(key) : null;
    },
    setItem(key, value) {
      values.set(key, String(value));
    },
    removeItem(key) {
      values.delete(key);
    },
    clear() {
      values.clear();
    },
  };
}

async function createHarness() {
  const fetchController = createFetchController();
  const document = createDocument();
  const timers = [];
  const windowListeners = new Map();
  const context = {
    console,
    document,
    fetch: fetchController.fetch,
    localStorage: createLocalStorage(),
    URL: {
      createObjectURL: () => "blob:framenest-test",
      revokeObjectURL: () => {},
    },
    Blob: global.Blob,
    Uint8Array: global.Uint8Array,
    Date: global.Date,
    JSON: global.JSON,
    Number: global.Number,
    String: global.String,
    Boolean: global.Boolean,
    Math: global.Math,
    Set: global.Set,
    Map: global.Map,
    RegExp: global.RegExp,
    Array: global.Array,
    Object: global.Object,
    Promise: global.Promise,
    encodeURIComponent,
    URLSearchParams,
    atob: global.atob || ((value) => Buffer.from(value, "base64").toString("binary")),
    setTimeout(callback, delay) {
      const timer = { callback, delay, active: true };
      timers.push(timer);
      return timer;
    },
    clearTimeout(timer) {
      if (timer) timer.active = false;
    },
    confirm() {
      throw new Error("native confirmation must not be invoked");
    },
    __listeners: windowListeners,
    parentNode: null,
    addEventListener(type, listener) {
      if (!windowListeners.has(type)) windowListeners.set(type, []);
      windowListeners.get(type).push(listener);
    },
    removeEventListener(type, listener) {
      const retained = (windowListeners.get(type) || []).filter((candidate) => candidate !== listener);
      if (retained.length > 0) windowListeners.set(type, retained);
      else windowListeners.delete(type);
    },
    dispatchEvent(event) {
      if (!event.target) event.target = context;
      event.currentTarget = context;
      for (const listener of [...(windowListeners.get(event.type) || [])]) listener(event);
      event.currentTarget = null;
      return !event.defaultPrevented;
    },
  };
  document.parentNode = context;
  context.window = context;
  context.self = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(APP_SOURCE, context, { filename: APP_PATH });
  await flushAll();
  const harness = {
    context,
    document,
    fetchController,
    timers,
    run(code) {
      return vm.runInContext(code, context);
    },
    async flush() {
      await flushAll();
    },
    runNextTimer() {
      const timer = timers.find((candidate) => candidate.active);
      assert.ok(timer, "expected an active timer");
      timer.active = false;
      timer.callback();
      return timer.delay;
    },
  };
  seedAdminMetadataIdentity(harness);
  fetchController.clearCalls();
  return harness;
}

function stateOf(harness) {
  return harness.run(`({
    generation: uploadState.generation,
    uploadId: uploadState.uploadId,
    snapshotState: uploadState.snapshot ? uploadState.snapshot.state : null,
    received: uploadState.snapshot ? uploadState.snapshot.received_size_bytes : null,
    actionKind: uploadState.actionOwner ? uploadState.actionOwner.kind : null,
    preparing: uploadState.preparing,
    running: uploadState.running,
    paused: uploadState.paused,
    completing: uploadState.completing,
    hasLoopOwner: Boolean(uploadState.uploadLoopOwner),
    hasCompletionOwner: Boolean(uploadState.completionOwner),
    hasPollOwner: Boolean(uploadState.pollOwner),
    hasPollTimer: Boolean(uploadState.pollTimer),
    pollRetryDelayMs: uploadState.pollRetryDelayMs,
    publicationPollAttempts: uploadState.publicationPollAttempts,
    message: uploadState.message,
    stateLabel: document.querySelector("#upload-state-label").textContent,
    visibleMessage: document.querySelector("#upload-message").textContent,
    byteCount: document.querySelector("#upload-byte-count").textContent,
    fileName: document.querySelector("#upload-file-name").textContent,
    startHidden: document.querySelector("#upload-start-button").hidden,
    pauseHidden: document.querySelector("#upload-pause-button").hidden,
    resumeHidden: document.querySelector("#upload-resume-button").hidden,
    duplicateKeepHidden: document.querySelector("#upload-duplicate-keep-button").hidden,
    duplicateDiscardHidden: document.querySelector("#upload-duplicate-discard-button").hidden,
    cancelHidden: document.querySelector("#upload-cancel-button").hidden,
    startDisabled: document.querySelector("#upload-start-button").disabled,
    pauseDisabled: document.querySelector("#upload-pause-button").disabled,
    resumeDisabled: document.querySelector("#upload-resume-button").disabled,
    duplicateKeepDisabled: document.querySelector("#upload-duplicate-keep-button").disabled,
    duplicateDiscardDisabled: document.querySelector("#upload-duplicate-discard-button").disabled,
    cancelDisabled: document.querySelector("#upload-cancel-button").disabled,
    focusedStatus: document.activeElement === document.querySelector("#upload-message"),
    cancelLabel: document.querySelector("#upload-cancel-button").textContent,
    duplicateKeepLabel: document.querySelector("#upload-duplicate-keep-button").textContent,
    duplicateDiscardLabel: document.querySelector("#upload-duplicate-discard-button").textContent,
    recovery: window.localStorage.getItem("${RECOVERY_KEY_CURRENT}"),
  })`);
}

function confirmationStateOf(harness) {
  return JSON.parse(JSON.stringify(harness.run(`({
    open: document.querySelector("#confirmation-dialog").hasAttribute("open"),
    activeRequestId: activeConfirmationRequest ? activeConfirmationRequest.id : null,
    title: document.querySelector("#confirmation-dialog-title").textContent,
    message: document.querySelector("#confirmation-dialog-message").textContent,
    dismissLabel: document.querySelector("#confirmation-dismiss-button").textContent,
    confirmLabel: document.querySelector("#confirmation-confirm-button").textContent,
    destructive: document.querySelector("#confirmation-confirm-button").classList.contains("danger-button"),
    focusedDismiss: document.activeElement === document.querySelector("#confirmation-dismiss-button"),
    focusedConfirm: document.activeElement === document.querySelector("#confirmation-confirm-button"),
  })`)));
}

function makeCatalogItem(mediaId = MEDIA_A, locationId = LOCATION_A) {
  return {
    media_id: mediaId,
    display_title: `Media ${mediaId}`,
    media_kind: "video",
    collection_key: null,
    created_at_ms: 1,
    tags: [],
    locations: [{
      location_id: locationId,
      availability: "available",
      relative_path: `media-${mediaId}.mp4`,
    }],
  };
}

function makeMetadataPayload({
  mediaId = MEDIA_A,
  title = "Persisted title",
  description = "Persisted description",
  tagKeys = ["persisted"],
} = {}) {
  return {
    media_id: mediaId,
    display_title: title,
    description,
    tags: tagKeys.map((key) => ({ key, display_name: key })),
    collection_key: null,
    processed_at_ms: null,
  };
}

function setMetadataWorkspace(
  harness,
  {
    item = null,
    mediaId = MEDIA_A,
    locationId = LOCATION_A,
    baselineTitle = "Persisted title",
    currentTitle = "Unsaved title",
    baselineDescription = "Persisted description",
    currentDescription = "Unsaved description",
    baselineTags = ["persisted"],
    currentTags = ["persisted", "unsaved"],
    scope = "",
  } = {},
) {
  harness.context.__metadataItem = item || makeCatalogItem(mediaId, locationId);
  harness.context.__metadataItem.display_title = baselineTitle;
  harness.context.__metadataFixture = {
    baselineTitle,
    currentTitle,
    baselineDescription,
    currentDescription,
    baselineTags,
    currentTags,
    scope,
  };
  harness.run(`
    catalogState.collection = __metadataFixture.scope;
    metadataWorkspace = {
      openMediaId: __metadataItem.media_id,
      openItem: snapshotCatalogItemForMetadata(__metadataItem),
      loading: false,
      saving: false,
      unavailable: false,
      notFound: false,
      statusOverride: null,
      analyzing: false,
      analysisFailureCode: "",
      aiSuggestionApplied: false,
      suggestedFilename: "",
      editMode: "canonical",
      baseline: {
        displayTitle: __metadataFixture.baselineTitle,
        description: __metadataFixture.baselineDescription,
        tagKeys: [...__metadataFixture.baselineTags],
        collectionKey: null,
        processedAtMs: null,
      },
      current: {
        displayTitle: __metadataFixture.currentTitle,
        description: __metadataFixture.currentDescription,
        tagKeys: [...__metadataFixture.currentTags],
        collectionKey: null,
        processedAtMs: null,
      },
    };
    advanceMetadataWorkspaceRevision();
    document.querySelector("#metadata-dialog").setAttribute("open", "");
    renderMetadataWorkspace();
  `);
  harness.fetchController.clearCalls();
}

function metadataStateOf(harness) {
  return JSON.parse(JSON.stringify(harness.run(`({
    mediaId: metadataWorkspace.openMediaId,
    openItemMediaId: metadataWorkspace.openItem ? metadataWorkspace.openItem.media_id : null,
    locationId: metadataAiLocation() ? metadataAiLocation().location_id : null,
    revision: metadataWorkspaceRevision,
    currentTitle: metadataWorkspace.current.displayTitle,
    currentDescription: metadataWorkspace.current.description,
    currentTags: [...metadataWorkspace.current.tagKeys],
    baselineTitle: metadataWorkspace.baseline.displayTitle,
    baselineDescription: metadataWorkspace.baseline.description,
    baselineTags: [...metadataWorkspace.baseline.tagKeys],
    suggestedFilename: metadataWorkspace.suggestedFilename,
    dirty: metadataWorkspace.openMediaId === null ? false : metadataIsDirty(),
    saving: metadataWorkspace.saving,
    analyzing: metadataWorkspace.analyzing,
    aiSuggestionApplied: metadataWorkspace.aiSuggestionApplied,
    metadataOpen: document.querySelector("#metadata-dialog").hasAttribute("open"),
    status: document.querySelector("#metadata-status").textContent,
    scope: catalogState.collection,
    saveRequestToken: metadataSaveRequestToken,
    saveOwnerToken: metadataSaveOwner ? metadataSaveOwner.token : null,
    saveOwnerMediaId: metadataSaveOwner ? metadataSaveOwner.mediaId : null,
    confirmationRequestId: activeConfirmationRequest ? activeConfirmationRequest.id : null,
    focusedClose: document.activeElement === document.querySelector("#metadata-close-button"),
    focusedAnalyze: document.activeElement === document.querySelector("#metadata-ai-analyze-button"),
    focusedTitle: document.activeElement === document.querySelector("#metadata-title-input"),
    focusedDismiss: document.activeElement === document.querySelector("#confirmation-dismiss-button"),
    beforeUnloadAttached: metadataBeforeUnloadAttached,
    analysisProgressHidden: document.querySelector("#metadata-ai-progress").hidden,
    analysisFailureCode: metadataWorkspace.analysisFailureCode || "",
    analyzeButtonHidden: document.querySelector("#metadata-ai-analyze-button").hidden,
    titleStripHidden: document.querySelector("#metadata-ai-title-strip").hidden,
    filenameNoteHidden: document.querySelector("#metadata-ai-filename-note").hidden,
    selectHidden: document.querySelector("#metadata-ai-suggestion-dropdown").hidden,
    suggestionCount: metadataSuggestionList.items.length,
    selectedRunId: metadataSuggestionList.selectedRunId,
    collectionKey: metadataWorkspace.current.collectionKey,
    aiStatus: document.querySelector("#metadata-ai-status").textContent,
    aiHeading: document.querySelector("#metadata-ai-heading").textContent,
  })`)));
}

function seedAdminMetadataIdentity(harness) {
  harness.run(`
    identityState.resolved = true;
    identityState.available = true;
    identityState.audience = "tailscale_workspace";
    identityState.login = "admin@example.com";
    identityState.displayName = "Admin";
    identityState.role = "admin";
    identityState.capabilities = new Set([
      "analysis.run",
      "media.workflow.read",
      "metadata.canonical.write",
      "gallery.read",
      "media.workspace.read",
      "upload.submit",
      "upload.manage",
    ]);
  `);
}

function enableMetadataAi(harness) {
  seedAdminMetadataIdentity(harness);
  harness.run(`renderAiCapability({
    available: true,
    provider_id: "test-provider",
    provider_display_name: "Test provider",
    model_id: "test-model",
    prompt_version: "test-prompt",
    execution: "server",
    status: "available",
    configured: true,
    credential_available: true,
    requires_explicit_confirmation: true,
  }); updateMetadataControls()`);
  harness.fetchController.clearCalls();
}

function metadataAiEndpoint(mediaId = MEDIA_A, locationId = LOCATION_A) {
  return `/api/media/${mediaId}/locations/${locationId}/ai-suggestion-preview`;
}

function metadataEndpoint(mediaId = MEDIA_A) {
  return `/api/media/${mediaId}/metadata`;
}

function automaticAnalysisEndpoint(mediaId = MEDIA_A) {
  return `/api/media/${mediaId}/automatic-analysis`;
}

function analyzedAutomaticPayload(overrides = {}) {
  return {
    media_id: String(MEDIA_A),
    state: "analyzed",
    automatic_analysis_enabled: true,
    analysis_definition: "automatic_post_catalog",
    result: {
      title: "Durable AI title",
      description: "Durable AI description",
      collection: "MustStayInformational",
      tags: ["durable-tag"],
      suggested_filename: "durable-ai.mp4",
      confidence: 0.8,
      evidence: ["frame"],
      uncertainties: [],
    },
    ...overrides,
  };
}

function suggestionListItem(overrides = {}) {
  return {
    analysisRunId: "run-1",
    completedAtMs: 1,
    providerId: "test-provider",
    modelId: "test-model",
    promptVersion: "v1",
    title: "Durable AI title",
    description: "Durable AI description",
    tags: [
      {
        value: "durable-tag",
        status: "mapped",
        key: "durable-tag",
        displayName: "durable-tag",
      },
    ],
    suggestedFilename: "",
    ...overrides,
  };
}

function seedMetadataSuggestionList(harness, items = [suggestionListItem()]) {
  seedAdminMetadataIdentity(harness);
  harness.context.__suggestionItems = items;
  harness.run(`
    metadataSuggestionList = {
      mediaId: metadataWorkspace.openMediaId,
      fetching: false,
      items: __suggestionItems,
      selectedRunId: __suggestionItems[0] ? __suggestionItems[0].analysisRunId : null,
      errorMessage: "",
      movieExcluded: false,
    };
    renderMetadataWorkspace();
  `);
  harness.fetchController.clearCalls();
}

function dispatch(element, type, values = {}) {
  const event = {
    type,
    bubbles: true,
    defaultPrevented: false,
    propagationStopped: false,
    immediatePropagationStopped: false,
    preventDefault() {
      this.defaultPrevented = true;
    },
    stopPropagation() {
      this.propagationStopped = true;
    },
    stopImmediatePropagation() {
      this.immediatePropagationStopped = true;
      this.propagationStopped = true;
    },
    ...values,
  };
  element.dispatchEvent(event);
  return event;
}

function activateConfirmation(harness, action) {
  const selector = action === "confirm"
    ? "#confirmation-confirm-button"
    : "#confirmation-dismiss-button";
  return dispatch(harness.document.querySelector(selector), "click");
}

function dismissConfirmationWithEscape(harness) {
  return dispatch(harness.document.querySelector("#confirmation-dialog"), "cancel");
}

function pressEscapeInBrowserOrder(harness, target = harness.document.activeElement) {
  const keydown = dispatch(target, "keydown", { key: "Escape" });
  const cancelEvents = [];
  if (!keydown.defaultPrevented) {
    const openDialogs = [...harness.document.__modalStack].reverse();
    for (const dialog of openDialogs) {
      if (!dialog.hasAttribute("open")) continue;
      const cancel = dispatch(dialog, "cancel", { bubbles: false });
      cancelEvents.push({ dialog, event: cancel });
      if (!cancel.defaultPrevented) dialog.close();
    }
  }
  return { keydown, cancelEvents };
}

function dispatchBrowserCancelSequence(harness) {
  const cancelEvents = [];
  const openDialogs = [...harness.document.__modalStack].reverse();
  for (const dialog of openDialogs) {
    if (!dialog.hasAttribute("open")) continue;
    const cancel = dispatch(dialog, "cancel", { bubbles: false });
    cancelEvents.push({ dialog, event: cancel });
    if (!cancel.defaultPrevented) dialog.close();
  }
  return cancelEvents;
}

function dispatchBeforeUnload(harness) {
  const event = {
    type: "beforeunload",
    defaultPrevented: false,
    returnValue: undefined,
    preventDefault() {
      this.defaultPrevented = true;
    },
  };
  harness.context.dispatchEvent(event);
  return event;
}

function galleryCardTitle(harness, index) {
  const card = harness.document.querySelector("#catalog-results").children[index];
  return card.children[1].children[0].children[0].textContent;
}

function prepareDirtyMetadataSwitch(harness, { hidden = true } = {}) {
  const itemA = makeCatalogItem(MEDIA_A, LOCATION_A);
  const itemB = makeCatalogItem(MEDIA_B, LOCATION_B);
  itemA.display_title = "Persisted A";
  itemB.display_title = "Persisted B";
  harness.context.__galleryA = itemA;
  harness.context.__mediaBItem = itemB;
  harness.run("renderCatalogSuccess({ items: [__galleryA, __mediaBItem], total: 2, offset: 0, limit: 30, q: '' })");
  setMetadataWorkspace(harness, {
    item: itemA,
    baselineTitle: "Persisted A",
    currentTitle: "Unsaved A",
    baselineDescription: "Persisted description A",
    currentDescription: "Unsaved description A",
    baselineTags: ["persisted"],
    currentTags: ["persisted", "unsaved"],
  });
  harness.context.__metadataOpenerA = harness.document.createElement("button");
  harness.context.__metadataOpenerB = harness.document.createElement("button");
  harness.run(`
    metadataWorkspace.suggestedFilename = "unsaved-a.mp4";
    metadataWorkspace.aiSuggestionApplied = true;
    advanceMetadataWorkspaceRevision();
    renderMetadataWorkspace();
    metadataOpenerElement = __metadataOpenerA;
    globalThis.__workspaceA = metadataWorkspace;
    globalThis.__openItemA = metadataWorkspace.openItem;
    globalThis.__baselineA = metadataWorkspace.baseline;
    globalThis.__currentA = metadataWorkspace.current;
  `);
  if (hidden) {
    harness.document.querySelector("#metadata-dialog").removeAttribute("open");
  }
  harness.context.__metadataOpenerB.focus();
  harness.fetchController.clearCalls();
}

function assertDirtyMetadataStatePreserved(before, after) {
  for (const key of [
    "mediaId",
    "openItemMediaId",
    "locationId",
    "revision",
    "currentTitle",
    "currentDescription",
    "currentTags",
    "baselineTitle",
    "baselineDescription",
    "baselineTags",
    "suggestedFilename",
    "dirty",
    "saving",
    "analyzing",
    "aiSuggestionApplied",
    "scope",
    "beforeUnloadAttached",
  ]) {
    assert.deepEqual(after[key], before[key], `expected preserved metadata field ${key}`);
  }
}

function setSelectedFile(harness, file = makeFile()) {
  harness.context.__file = file;
  harness.run("resetUploadForFile(__file)");
  harness.fetchController.clearCalls();
}

function setActiveUpload(
  harness,
  {
    id = UPLOAD_A,
    state = "receiving",
    received = 0,
    size = 8,
    file = makeFile("sample.gif", size),
  } = {},
) {
  harness.context.__file = file;
  harness.context.__snapshot = snapshot(id, state, received, size, file.name);
  harness.run(`
    resetUploadForFile(__file);
    uploadState.uploadId = __snapshot.id;
    uploadState.snapshot = __snapshot;
    uploadState.fileNameHint = __snapshot.display_filename;
    uploadState.expectedSizeBytes = __snapshot.declared_size_bytes;
    saveUploadRecovery(__snapshot);
    renderUploadCockpit();
  `);
  harness.fetchController.clearCalls();
}

function enqueue(harness, method, url, result) {
  harness.fetchController.enqueue(
    (call) => call.method === method && call.url === url,
    result,
  );
}

function enqueueStatus(harness, uploadId, result) {
  enqueue(harness, "GET", `/api/uploads/${encodeURIComponent(uploadId)}`, result);
}

function configureControlScenario(harness, scenario) {
  const file = scenario.hasFile === false ? null : makeFile("scenario.gif", 8);
  const currentSnapshot = scenario.snapshotState
    ? snapshot(
      UPLOAD_A,
      scenario.snapshotState,
      scenario.received === undefined ? 8 : scenario.received,
      8,
      "scenario.gif",
    )
    : null;
  harness.context.__controlScenario = {
    file,
    snapshot: currentSnapshot,
    actionKind: scenario.actionKind || null,
    preparing: Boolean(scenario.preparing),
    running: Boolean(scenario.running),
    paused: Boolean(scenario.paused),
    needsReselection: Boolean(scenario.needsReselection),
    completing: Boolean(scenario.completing),
  };
  harness.run(`
    resetUploadForFile(__controlScenario.file);
    uploadState.uploadId = __controlScenario.snapshot ? __controlScenario.snapshot.id : null;
    uploadState.snapshot = __controlScenario.snapshot;
    uploadState.fileNameHint = __controlScenario.snapshot ? __controlScenario.snapshot.display_filename : "";
    uploadState.expectedSizeBytes = __controlScenario.snapshot ? __controlScenario.snapshot.declared_size_bytes : 0;
    uploadState.preparing = __controlScenario.preparing;
    uploadState.running = __controlScenario.running;
    uploadState.paused = __controlScenario.paused;
    uploadState.needsReselection = __controlScenario.needsReselection;
    uploadState.completing = __controlScenario.completing;
    uploadState.actionOwner = __controlScenario.actionKind
      ? { ...currentUploadContext(), kind: __controlScenario.actionKind }
      : null;
    renderUploadCockpit();
  `);
}

function assertExactUploadActions(harness, scenarioName, expectedActions) {
  const state = stateOf(harness);
  const expected = new Set(expectedActions);
  for (const action of [
    "start",
    "pause",
    "resume",
    "duplicateKeep",
    "duplicateDiscard",
    "cancel",
  ]) {
    const available = expected.has(action);
    assert.equal(state[`${action}Hidden`], !available, `${scenarioName}: ${action} hidden`);
    assert.equal(state[`${action}Disabled`], !available, `${scenarioName}: ${action} disabled`);
  }
  assert.equal(
    expected.has("pause") && expected.has("resume"),
    false,
    `${scenarioName}: Pause and Resume must never be simultaneously available`,
  );
}

test("Production Upload cancellation contains no native browser confirmation", () => {
  assert.equal(APP_SOURCE.includes("window.confirm"), false);
  assert.equal(/(^|[^A-Za-z0-9_.])confirm\s*\(/m.test(APP_SOURCE), false);
});

test("Upload picker accepts GIF, MP4, JPEG, and PNG with truthful guidance", () => {
  const acceptMatch = INDEX_SOURCE.match(
    /<input id="upload-file-input"[^>]*accept="([^"]+)"/,
  );
  assert.ok(acceptMatch, "upload-file-input with accept attribute present");
  const accept = acceptMatch[1].split(",").map((token) => token.trim()).filter(Boolean);
  for (const expected of [
    ".gif",
    ".mp4",
    ".jpg",
    ".jpeg",
    ".png",
    "image/gif",
    "video/mp4",
    "image/jpeg",
    "image/png",
  ]) {
    assert.ok(accept.includes(expected), `accept missing ${expected}`);
  }
  assert.ok(INDEX_SOURCE.includes(`id="upload-file-hint"`));
  const hint = INDEX_SOURCE.match(
    /<p id="upload-file-hint"[^>]*>(.*?)<\/p>/,
  );
  assert.ok(hint, "upload file hint present");
  assert.match(hint[1], /GIF/);
  assert.match(hint[1], /MP4/);
  assert.match(hint[1], /JPEG/);
  assert.match(hint[1], /PNG/);
  assert.equal(APP_SOURCE.includes("Select one local GIF or MP4."), false);
  assert.ok(APP_SOURCE.includes("Select one local GIF, MP4, JPEG, or PNG."));
});

test("Upload controls expose the exact action set for every accepted lifecycle state", async () => {
  const h = await createHarness();
  const scenarios = [
    { name: "No file", hasFile: false, stateLabel: "No file selected", actions: [] },
    { name: "Valid selected file", stateLabel: "Ready", actions: ["start"] },
    { name: "Preparing before session creation", preparing: true, actionKind: "start", stateLabel: "Preparing", actions: [] },
    { name: "Preparing after session creation", snapshotState: "created", received: 0, preparing: true, actionKind: "start", stateLabel: "Preparing", actions: ["cancel"] },
    { name: "Uploading", snapshotState: "receiving", received: 4, running: true, stateLabel: "Uploading", actions: ["pause", "cancel"] },
    { name: "Pausing", snapshotState: "receiving", received: 4, running: true, paused: true, stateLabel: "Pausing", actions: ["cancel"] },
    { name: "Paused", snapshotState: "receiving", received: 4, paused: true, stateLabel: "Paused", actions: ["resume", "cancel"] },
    { name: "Reselection required", hasFile: false, snapshotState: "receiving", received: 4, needsReselection: true, stateLabel: "Reselect file to resume", actions: ["cancel"] },
    { name: "Compatible file reselected", snapshotState: "receiving", received: 4, stateLabel: "Ready to resume", actions: ["resume", "cancel"] },
    { name: "Created session ready to resume", snapshotState: "created", received: 0, stateLabel: "Ready to resume", actions: ["resume", "cancel"] },
    { name: "Received", snapshotState: "received", stateLabel: "Validating", actions: ["cancel"] },
    { name: "Validating", snapshotState: "validating", stateLabel: "Validating", actions: [] },
    {
      name: "Duplicate pending",
      snapshotState: "duplicate_pending",
      stateLabel: "Duplicate found",
      actions: ["duplicateKeep", "duplicateDiscard"],
    },
    { name: "Publish pending", snapshotState: "publish_pending", stateLabel: "Publishing", actions: [] },
    { name: "Published", snapshotState: "published", stateLabel: "Published", actions: [] },
    { name: "Cataloged", snapshotState: "cataloged", stateLabel: "Completed", actions: [] },
    { name: "Rejected", snapshotState: "rejected", stateLabel: "Failed", actions: [] },
    { name: "Failed", snapshotState: "failed", stateLabel: "Failed", actions: [] },
    { name: "Expired", snapshotState: "expired", stateLabel: "Failed", actions: [] },
    { name: "Cancelled", snapshotState: "cancelled", stateLabel: "Cancelled", actions: [] },
    { name: "Cancelling", snapshotState: "received", actionKind: "cancel", stateLabel: "Cancelling", actions: [] },
  ];

  for (const scenario of scenarios) {
    configureControlScenario(h, scenario);
    const state = stateOf(h);
    assert.equal(state.stateLabel, scenario.stateLabel, `${scenario.name}: visible state`);
    assertExactUploadActions(h, scenario.name, scenario.actions);
  }

  configureControlScenario(h, scenarios[0]);
  assert.equal(stateOf(h).fileName, "", "idle controls remain absent with no selected file");
});

test("Duplicate pending renders the approved copy and exactly two duplicate actions", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "duplicate_pending", received: 8 });

  const state = stateOf(h);
  assert.equal(state.stateLabel, "Duplicate found");
  assert.equal(state.visibleMessage, "Exact duplicate found.");
  assert.equal(state.duplicateKeepLabel, "Keep as separate item");
  assert.equal(state.duplicateDiscardLabel, "Discard duplicate");
  assertExactUploadActions(h, "duplicate review", ["duplicateKeep", "duplicateDiscard"]);
});

test("Keep separate is single-owner, sends no bytes, and reaches awaiting publication", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "duplicate_pending", received: 8 });
  const resolution = deferred();
  enqueue(h, "POST", `/api/uploads/${UPLOAD_A}/duplicate-resolution`, resolution.promise);
  h.document.querySelector("#upload-duplicate-keep-button").focus();

  const first = h.run("handleKeepDuplicate()");
  const repeated = h.run("handleKeepDuplicate()");
  await h.flush();

  const pending = stateOf(h);
  assert.equal(pending.actionKind, "duplicate-keep");
  assert.equal(pending.duplicateKeepHidden, false);
  assert.equal(pending.duplicateDiscardHidden, false);
  assert.equal(pending.duplicateKeepDisabled, true);
  assert.equal(pending.duplicateDiscardDisabled, true);
  const calls = h.fetchController.matching(
    "POST",
    `/api/uploads/${UPLOAD_A}/duplicate-resolution`,
  );
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), { resolution: "keep_separate" });
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);

  const kept = snapshot(UPLOAD_A, "publish_pending", 8);
  delete kept.display_filename;
  resolution.resolve(response(kept));
  await Promise.all([first, repeated]);
  await h.flush();

  const settled = stateOf(h);
  assert.equal(settled.snapshotState, "publish_pending");
  assert.equal(settled.visibleMessage, "Validated. Awaiting publication. Not yet available in Gallery.");
  assert.equal(JSON.parse(settled.recovery).last_known_state, "publish_pending");
  assert.equal(settled.fileName, "sample.gif");
  assert.equal(settled.focusedStatus, true);
});

test("Duplicate discard confirmation preserves review on dismissal and removes only after approval", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "duplicate_pending", received: 8 });
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  const trigger = h.document.querySelector("#upload-duplicate-discard-button");
  trigger.focus();

  const escaped = h.run("handleDiscardDuplicate()");
  await h.flush();
  let confirmation = confirmationStateOf(h);
  assert.equal(confirmation.title, "Discard duplicate?");
  assert.equal(
    confirmation.message,
    "This uploaded copy will be removed. The earlier upload is not affected.",
  );
  assert.equal(confirmation.dismissLabel, "Keep reviewing");
  assert.equal(confirmation.confirmLabel, "Discard duplicate");
  assert.equal(confirmation.destructive, true);
  assert.equal(confirmation.focusedDismiss, true);
  dismissConfirmationWithEscape(h);
  await escaped;
  assert.equal(stateOf(h).snapshotState, "duplicate_pending");
  assert.equal(h.document.activeElement, trigger);
  assert.equal(h.fetchController.matchingPrefix("POST", "/api/uploads/").length, 0);

  const backdrop = h.run("handleDiscardDuplicate()");
  await h.flush();
  dispatch(h.document.querySelector("#confirmation-dialog"), "click", {
    target: h.document.querySelector("#confirmation-dialog"),
  });
  await backdrop;
  assert.equal(stateOf(h).snapshotState, "duplicate_pending");
  assert.equal(h.document.activeElement, trigger);

  const resolution = deferred();
  enqueue(h, "POST", `/api/uploads/${UPLOAD_A}/duplicate-resolution`, resolution.promise);
  const confirmed = h.run("handleDiscardDuplicate()");
  await h.flush();
  activateConfirmation(h, "confirm");
  await h.flush();

  confirmation = confirmationStateOf(h);
  assert.equal(confirmation.open, false);
  assert.equal(stateOf(h).actionKind, "duplicate-discard");
  const calls = h.fetchController.matching(
    "POST",
    `/api/uploads/${UPLOAD_A}/duplicate-resolution`,
  );
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), { resolution: "discard" });
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);

  const cancelled = snapshot(UPLOAD_A, "cancelled", 8);
  delete cancelled.display_filename;
  resolution.resolve(response(cancelled));
  await confirmed;
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "cancelled");
  assert.equal(stateOf(h).recovery, null);
  assert.equal(stateOf(h).focusedStatus, true);
});

test("Overlapping duplicate activation is fenced and server discard truth wins a delayed Keep conflict", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "duplicate_pending", received: 8 });
  const keepResponse = deferred();
  enqueue(h, "POST", `/api/uploads/${UPLOAD_A}/duplicate-resolution`, keepResponse.promise);

  const keep = h.run("handleKeepDuplicate()");
  const discard = h.run("handleDiscardDuplicate()");
  await h.flush();
  assert.equal(confirmationStateOf(h).open, false);
  assert.equal(
    h.fetchController.matching("POST", `/api/uploads/${UPLOAD_A}/duplicate-resolution`).length,
    1,
  );

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "cancelled", 8)));
  keepResponse.resolve(response({ error: { code: "UPLOAD_SESSION_STATE_CONFLICT" } }, 409));
  await Promise.all([keep, discard]);
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "cancelled");
  assert.equal(stateOf(h).actionKind, null);
  assert.equal(stateOf(h).recovery, null);
});

test("Stale duplicate Keep and Discard success, error, and finally cannot mutate a newer upload", async () => {
  for (const operation of ["keep", "discard"]) {
    for (const outcome of ["success", "error"]) {
      const h = await createHarness();
      setActiveUpload(h, { id: UPLOAD_A, state: "duplicate_pending", received: 8 });
      const resolution = deferred();
      enqueue(h, "POST", `/api/uploads/${UPLOAD_A}/duplicate-resolution`, resolution.promise);

      let pending;
      if (operation === "keep") {
        pending = h.run("handleKeepDuplicate()");
      } else {
        pending = h.run("handleDiscardDuplicate()");
        await h.flush();
        activateConfirmation(h, "confirm");
      }
      await h.flush();
      setActiveUpload(h, { id: UPLOAD_B, state: "receiving", received: 0 });
      if (outcome === "success") {
        resolution.resolve(response(snapshot(
          UPLOAD_A,
          operation === "keep" ? "publish_pending" : "cancelled",
          8,
        )));
      } else {
        resolution.reject(new Error("synthetic stale network failure"));
      }
      await pending;
      await h.flush();

      const state = stateOf(h);
      const label = `${operation} ${outcome}`;
      assert.equal(state.uploadId, UPLOAD_B, `${label}: newer upload identity`);
      assert.equal(state.snapshotState, "receiving", `${label}: newer state`);
      assert.equal(state.actionKind, null, `${label}: stale finally ownership`);
      assert.match(state.recovery, new RegExp(UPLOAD_B), `${label}: newer recovery`);
    }
  }
});

test("Duplicate review is reconstructed from recovery without PATCH or re-upload", async () => {
  const h = await createHarness();
  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "recovered.gif",
    expected_size_bytes: 8,
    last_modified_hint: 1234,
    last_known_state: "duplicate_pending",
  }));
  enqueueStatus(h, UPLOAD_A, response(snapshot(
    UPLOAD_A,
    "duplicate_pending",
    8,
    8,
    "recovered.gif",
  )));

  await h.run("restoreUploadRecovery()");
  await h.flush();

  const state = stateOf(h);
  assert.equal(state.snapshotState, "duplicate_pending");
  assert.equal(state.visibleMessage, "Exact duplicate found.");
  assertExactUploadActions(h, "recovered duplicate", ["duplicateKeep", "duplicateDiscard"]);
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(h.fetchController.matching("POST", `/api/uploads/${UPLOAD_A}/complete`).length, 0);
});

test("A disappearing upload action moves focus to the live status", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "receiving", received: 4 });
  h.run(`
    uploadState.running = true;
    renderUploadCockpit();
    document.querySelector("#upload-pause-button").focus();
    if (document.activeElement !== document.querySelector("#upload-pause-button")) {
      throw new Error("visible native Pause action should be focusable");
    }
    handlePauseUpload();
  `);

  const state = stateOf(h);
  assert.equal(state.pauseHidden, true);
  assert.equal(state.focusedStatus, true);
  assert.equal(state.stateLabel, "Pausing");
  assert.equal(h.run('document.querySelector("#upload-message").focusCount'), 1);
  h.run("renderUploadCockpit(); renderUploadCockpit()");
  assert.equal(h.run('document.querySelector("#upload-message").focusCount'), 1);
});

test("The centralized status is programmatically focusable but absent from the normal tab sequence", async () => {
  const h = await createHarness();
  assert.equal(h.run('document.querySelector("#upload-message").getAttribute("tabindex")'), "-1");
  assert.equal(isInNormalTabSequence(h.document.querySelector("#upload-message")), false);
  h.run('document.querySelector("#upload-message").focus()');
  assert.equal(stateOf(h).focusedStatus, true);
});

test("Polling preserves unrelated focus and never repeatedly focuses the upload status", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "received", received: 8 });
  h.run(`
    document.querySelector("#upload-dialog").setAttribute("open", "");
    document.querySelector("#upload-close-button").focus();
    scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }));
  `);
  assert.equal(
    h.run('document.activeElement === document.querySelector("#upload-close-button")'),
    true,
  );

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "received", 8)));
  h.runNextTimer();
  await h.flush();
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "validating", 8)));
  h.runNextTimer();
  await h.flush();

  assert.equal(
    h.run('document.activeElement === document.querySelector("#upload-close-button")'),
    true,
  );
  assert.equal(h.run('document.querySelector("#upload-message").focusCount'), 0);
});

test("Rendering without a disappearing focused action does not focus the upload status", async () => {
  const h = await createHarness();
  setActiveUpload(h, { state: "receiving", received: 4 });
  h.run(`
    uploadState.running = true;
    renderUploadCockpit();
    document.querySelector("#upload-cancel-button").focus();
    renderUploadCockpit();
  `);
  assert.equal(
    h.run('document.activeElement === document.querySelector("#upload-cancel-button")'),
    true,
  );
  assert.equal(h.run('document.querySelector("#upload-message").focusCount'), 0);
});

test("Reusable confirmation populates, settles once, resets, and restores focus", async () => {
  const h = await createHarness();
  const trigger = h.document.querySelector("#upload-cancel-button");
  trigger.hidden = false;
  trigger.disabled = false;
  trigger.focus();
  h.context.__confirmationSettlements = 0;
  const result = h.run(`requestConfirmation({
    title: "Remove item?",
    message: "This action cannot be undone.",
    dismissLabel: "Keep item",
    confirmLabel: "Remove item",
    destructive: true,
  }).then((accepted) => {
    __confirmationSettlements += 1;
    return accepted;
  })`);

  assert.deepEqual(confirmationStateOf(h), {
    open: true,
    activeRequestId: 1,
    title: "Remove item?",
    message: "This action cannot be undone.",
    dismissLabel: "Keep item",
    confirmLabel: "Remove item",
    destructive: true,
    focusedDismiss: true,
    focusedConfirm: false,
  });

  activateConfirmation(h, "dismiss");
  dismissConfirmationWithEscape(h);
  assert.equal(await result, false);
  await h.flush();
  assert.equal(h.context.__confirmationSettlements, 1);
  assert.deepEqual(confirmationStateOf(h), {
    open: false,
    activeRequestId: null,
    title: "",
    message: "",
    dismissLabel: "",
    confirmLabel: "",
    destructive: false,
    focusedDismiss: false,
    focusedConfirm: false,
  });
  assert.equal(h.document.activeElement, trigger);
});

test("Reusable confirmation rejects overlap and stale settlement cannot affect a later request", async () => {
  const h = await createHarness();
  const first = h.run(`requestConfirmation({
    title: "First request",
    message: "First message",
    dismissLabel: "Wait",
    confirmLabel: "Continue",
    destructive: true,
  })`);
  h.run("globalThis.__staleConfirmationRequest = activeConfirmationRequest");
  const overlapping = h.run(`requestConfirmation({
    title: "Overlapping request",
    message: "Must not replace the active request",
    dismissLabel: "No",
    confirmLabel: "Yes",
  })`);

  assert.equal(await overlapping, false);
  assert.equal(confirmationStateOf(h).title, "First request");
  assert.equal(confirmationStateOf(h).activeRequestId, 1);
  activateConfirmation(h, "confirm");
  assert.equal(await first, true);

  const later = h.run(`requestConfirmation({
    title: "Later request",
    message: "Later message",
    dismissLabel: "Stay",
    confirmLabel: "Proceed",
    destructive: false,
  })`);
  h.run("settleConfirmation(__staleConfirmationRequest, true)");
  assert.equal(confirmationStateOf(h).open, true);
  assert.equal(confirmationStateOf(h).title, "Later request");
  assert.equal(confirmationStateOf(h).destructive, false);
  const escapeEvent = dismissConfirmationWithEscape(h);
  assert.equal(escapeEvent.defaultPrevented, true);
  assert.equal(await later, false);
});

test("Confirmation traps action focus and Escape leaves the parent Upload modal open", async () => {
  const h = await createHarness();
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  const result = h.run(`requestConfirmation({
    title: "Cancel upload?",
    message: "Uploaded progress will be discarded.",
    dismissLabel: "Keep upload",
    confirmLabel: "Cancel upload",
    destructive: true,
  })`);

  assert.equal(confirmationStateOf(h).focusedDismiss, true);
  const shiftTab = dispatch(h.document.querySelector("#confirmation-dialog"), "keydown", {
    key: "Tab",
    shiftKey: true,
  });
  assert.equal(shiftTab.defaultPrevented, true);
  assert.equal(confirmationStateOf(h).focusedConfirm, true);
  const tab = dispatch(h.document.querySelector("#confirmation-dialog"), "keydown", {
    key: "Tab",
    shiftKey: false,
  });
  assert.equal(tab.defaultPrevented, true);
  assert.equal(confirmationStateOf(h).focusedDismiss, true);

  dismissConfirmationWithEscape(h);
  assert.equal(await result, false);
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
});

test("Keep upload and Escape preserve upload ownership, offset, modal, and trigger focus", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  h.run(`
    document.querySelector("#upload-dialog").setAttribute("open", "");
    document.querySelector("#upload-cancel-button").focus();
  `);
  const initialGeneration = stateOf(h).generation;
  const keepUpload = h.run("handleCancelUpload()");

  assert.deepEqual(
    {
      title: confirmationStateOf(h).title,
      message: confirmationStateOf(h).message,
      dismissLabel: confirmationStateOf(h).dismissLabel,
      confirmLabel: confirmationStateOf(h).confirmLabel,
      destructive: confirmationStateOf(h).destructive,
    },
    {
      title: "Cancel upload?",
      message: "Uploaded progress will be discarded.",
      dismissLabel: "Keep upload",
      confirmLabel: "Cancel upload",
      destructive: true,
    },
  );
  assert.equal(stateOf(h).actionKind, null);
  assert.equal(stateOf(h).generation, initialGeneration);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);

  activateConfirmation(h, "dismiss");
  await keepUpload;
  assert.equal(stateOf(h).snapshotState, "receiving");
  assert.equal(stateOf(h).received, 4);
  assert.equal(stateOf(h).generation, initialGeneration);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
  assert.equal(h.document.activeElement, h.document.querySelector("#upload-cancel-button"));

  const escape = h.run("handleCancelUpload()");
  dismissConfirmationWithEscape(h);
  await escape;
  assert.equal(stateOf(h).snapshotState, "receiving");
  assert.equal(stateOf(h).received, 4);
  assert.equal(stateOf(h).generation, initialGeneration);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
  assert.equal(h.document.activeElement, h.document.querySelector("#upload-cancel-button"));
});

test("Upload cancellation revalidates eligibility after asynchronous confirmation", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  const cancellation = h.run("handleCancelUpload()");
  h.context.__validatingSnapshot = snapshot(UPLOAD_A, "validating", 8);
  h.run(`
    uploadState.snapshot = __validatingSnapshot;
    renderUploadCockpit();
  `);

  activateConfirmation(h, "confirm");
  await cancellation;
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "validating");
  assert.equal(stateOf(h).received, 8);
  assert.equal(stateOf(h).actionKind, null);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);
});

test("Confirmation backdrop dismissal resolves false without closing the Upload modal", async () => {
  const h = await createHarness();
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  const result = h.run(`requestConfirmation({
    title: "Leave upload?",
    message: "The upload will remain available.",
    dismissLabel: "Stay",
    confirmLabel: "Leave",
  })`);

  const dialog = h.document.querySelector("#confirmation-dialog");
  dispatch(dialog, "click", { target: dialog });
  assert.equal(await result, false);
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
});

test("Confirmation content click bubbles with a descendant target and does not dismiss", async () => {
  const h = await createHarness();
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  const result = h.run(`requestConfirmation({
    title: "Leave upload?",
    message: "The upload will remain available.",
    dismissLabel: "Stay",
    confirmLabel: "Leave",
  })`);
  const dialog = h.document.querySelector("#confirmation-dialog");
  const title = h.document.querySelector("#confirmation-dialog-title");
  let observedTarget = null;
  let observedCurrentTarget = null;
  dialog.addEventListener("click", (event) => {
    observedTarget = event.target;
    observedCurrentTarget = event.currentTarget;
  });

  dispatch(title, "click");

  assert.equal(observedTarget, title);
  assert.equal(observedCurrentTarget, dialog);
  assert.equal(confirmationStateOf(h).open, true);
  assert.equal(confirmationStateOf(h).activeRequestId, 1);
  dispatch(dialog, "click", { target: dialog });
  assert.equal(await result, false);
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
});

test("Confirmation throwing open and cleanup fail closed, release ownership, and permit a later request", async () => {
  const h = await createHarness();
  const trigger = h.document.querySelector("#upload-cancel-button");
  trigger.hidden = false;
  trigger.disabled = false;
  trigger.focus();
  h.run(`
    globalThis.__workingShowModal = document.querySelector("#confirmation-dialog").showModal;
    globalThis.__workingConfirmationClose = document.querySelector("#confirmation-dialog").close;
    globalThis.__confirmationCloseAttempts = 0;
    document.querySelector("#confirmation-dialog").showModal = function showModalFailure() {
      this.setAttribute("open", "");
      throw new Error("synthetic showModal failure");
    };
    document.querySelector("#confirmation-dialog").close = function closeFailure() {
      globalThis.__failedConfirmationRequest = activeConfirmationRequest;
      globalThis.__confirmationCloseAttempts += 1;
      throw new Error("synthetic close failure");
    };
  `);

  let failed;
  assert.doesNotThrow(() => {
    failed = h.run(`requestConfirmation({
      title: "Dangerous request",
      message: "This must fail closed.",
      dismissLabel: "Stay",
      confirmLabel: "Proceed",
      destructive: true,
    })`);
  });

  assert.equal(await failed, false);
  const failedState = confirmationStateOf(h);
  const restoredTriggerFocus = h.document.activeElement === trigger;
  assert.equal(h.run("__confirmationCloseAttempts"), 1);

  h.run(`
    document.querySelector("#confirmation-dialog").showModal = __workingShowModal;
    document.querySelector("#confirmation-dialog").close = __workingConfirmationClose;
  `);
  const later = h.run(`requestConfirmation({
    title: "Later request",
    message: "This request must own the dialog.",
    dismissLabel: "No",
    confirmLabel: "Yes",
    destructive: false,
  })`);
  assert.equal(confirmationStateOf(h).open, true);
  assert.equal(confirmationStateOf(h).activeRequestId, 2);
  assert.equal(confirmationStateOf(h).destructive, false);
  h.run("settleConfirmation(__failedConfirmationRequest, true)");
  assert.equal(confirmationStateOf(h).activeRequestId, 2);
  assert.equal(confirmationStateOf(h).open, true);

  assert.deepEqual(failedState, {
    open: false,
    activeRequestId: null,
    title: "",
    message: "",
    dismissLabel: "",
    confirmLabel: "",
    destructive: false,
    focusedDismiss: false,
    focusedConfirm: false,
  });
  assert.equal(restoredTriggerFocus, true);
  activateConfirmation(h, "confirm");
  assert.equal(await later, true);
});

test("Metadata current save owns its request, response, baseline, and one post-save close", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const saveResponse = deferred();
  const catalogResponse = deferred();
  enqueue(h, "PUT", metadataEndpoint(), saveResponse.promise);
  h.fetchController.enqueue(
    (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
    catalogResponse.promise,
  );
  const initialMetadataRequestToken = h.run("metadataRequestToken");

  const saving = h.run("handleSaveMetadata()");
  await h.flush();

  const calls = h.fetchController.matching("PUT", metadataEndpoint());
  assert.equal(calls.length, 1);
  assert.deepEqual(JSON.parse(calls[0].options.body), {
    display_title: "Unsaved title",
    description: "Unsaved description",
    tag_keys: ["persisted", "unsaved"],
    content_category: "general",
    genres: [],
    creator_attribution_kind: null,
    creator_stable_id: null,
    creator_handle: null,
    creator_display_name: null,
  });
  assert.equal(metadataStateOf(h).saving, true);

  saveResponse.resolve(response({
    metadata: makeMetadataPayload({
      title: "Unsaved title",
      description: "Unsaved description",
      tagKeys: ["persisted", "unsaved"],
    }),
  }));
  await h.flush();

  const applied = metadataStateOf(h);
  assert.equal(applied.metadataOpen, true);
  assert.equal(applied.saving, true);
  assert.equal(applied.currentTitle, "Unsaved title");
  assert.equal(applied.baselineTitle, "Unsaved title");
  assert.equal(applied.baselineDescription, "Unsaved description");
  assert.deepEqual(applied.baselineTags, ["persisted", "unsaved"]);
  assert.equal(applied.dirty, false);

  catalogResponse.resolve(response({ items: [], total: 0, offset: 0, limit: 30, q: "" }));
  await saving;
  await h.flush();

  const closed = metadataStateOf(h);
  assert.equal(closed.metadataOpen, false);
  assert.equal(closed.mediaId, null);
  assert.equal(closed.saving, false);
  assert.equal(closed.saveOwnerToken, null);
  assert.equal(h.run("metadataRequestToken"), initialMetadataRequestToken + 1);
  assert.equal(h.fetchController.matching("PUT", metadataEndpoint()).length, 1);
});

test("Metadata stale cross-media save cannot mutate, report against, confirm, or close a newer workspace", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const saveResponse = deferred();
  enqueue(h, "PUT", metadataEndpoint(MEDIA_A), saveResponse.promise);
  const savingA = h.run("handleSaveMetadata()");
  await h.flush();

  const closingA = h.run("closeMetadataWorkspace()");
  activateConfirmation(h, "confirm");
  assert.equal(await closingA, true);
  h.context.__mediaBItem = makeCatalogItem(MEDIA_B, LOCATION_B);
  enqueue(h, "GET", metadataEndpoint(MEDIA_B), response(makeMetadataPayload({
    mediaId: MEDIA_B,
    title: "Persisted B",
    description: "Description B",
    tagKeys: ["persisted-b"],
  })));
  await h.run("handleOpenMetadataWorkspace(__mediaBItem, null)");
  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Unsaved B";
  dispatch(titleInput, "input");
  const before = metadataStateOf(h);

  saveResponse.resolve(response({
    metadata: makeMetadataPayload({
      mediaId: MEDIA_A,
      title: "Saved A",
      description: "Saved description A",
      tagKeys: ["saved-a"],
    }),
  }));
  await savingA;
  await h.flush();

  const after = metadataStateOf(h);
  assert.equal(after.mediaId, MEDIA_B);
  assert.equal(after.openItemMediaId, MEDIA_B);
  assert.equal(after.locationId, LOCATION_B);
  assert.equal(after.currentTitle, before.currentTitle);
  assert.equal(after.currentDescription, before.currentDescription);
  assert.deepEqual(after.currentTags, before.currentTags);
  assert.equal(after.baselineTitle, before.baselineTitle);
  assert.equal(after.dirty, true);
  assert.equal(after.metadataOpen, true);
  assert.equal(after.status, before.status);
  assert.equal(after.confirmationRequestId, null);
  assert.equal(after.saveOwnerToken, null);
  assert.equal(h.document.activeElement, titleInput);
});

test("Metadata stale same-media save response preserves a newer edit and baseline", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const saveResponse = deferred();
  enqueue(h, "PUT", metadataEndpoint(), saveResponse.promise);
  const saving = h.run("handleSaveMetadata()");
  await h.flush();

  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Newer same-media edit";
  dispatch(titleInput, "input");
  saveResponse.resolve(response({
    metadata: makeMetadataPayload({ title: "Stale saved title" }),
  }));
  await saving;
  await h.flush();

  const state = metadataStateOf(h);
  assert.equal(state.currentTitle, "Newer same-media edit");
  assert.equal(state.baselineTitle, "Persisted title");
  assert.equal(state.dirty, true);
  assert.equal(state.saving, false);
  assert.equal(state.saveOwnerToken, null);
  assert.equal(state.metadataOpen, true);
});

test("Metadata save response rejects a replaced media identity independently of revision", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const saveResponse = deferred();
  enqueue(h, "PUT", metadataEndpoint(MEDIA_A), saveResponse.promise);
  const saving = h.run("handleSaveMetadata()");
  await h.flush();

  h.context.__replacementItem = makeCatalogItem(MEDIA_B, LOCATION_A);
  h.run(`
    metadataWorkspace.openMediaId = __replacementItem.media_id;
    metadataWorkspace.openItem = __replacementItem;
    metadataWorkspace.current.displayTitle = "Replacement B";
    metadataWorkspace.baseline.displayTitle = "Persisted B";
  `);
  saveResponse.resolve(response({
    metadata: makeMetadataPayload({ mediaId: MEDIA_A, title: "Saved A" }),
  }));
  await saving;
  await h.flush();

  const state = metadataStateOf(h);
  assert.equal(state.mediaId, MEDIA_B);
  assert.equal(state.openItemMediaId, MEDIA_B);
  assert.equal(state.locationId, LOCATION_A);
  assert.equal(state.currentTitle, "Replacement B");
  assert.equal(state.baselineTitle, "Persisted B");
  assert.equal(state.metadataOpen, true);
});

test("Metadata post-save close cannot capture fresh authority after the saved workspace is replaced", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const saveResponse = deferred();
  const catalogResponse = deferred();
  enqueue(h, "PUT", metadataEndpoint(MEDIA_A), saveResponse.promise);
  h.fetchController.enqueue(
    (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
    catalogResponse.promise,
  );
  const savingA = h.run("handleSaveMetadata()");
  await h.flush();
  saveResponse.resolve(response({
    metadata: makeMetadataPayload({
      mediaId: MEDIA_A,
      title: "Unsaved title",
      description: "Unsaved description",
      tagKeys: ["persisted", "unsaved"],
    }),
  }));
  await h.flush();
  assert.equal(metadataStateOf(h).dirty, false);

  const closingA = h.run("closeMetadataWorkspace()");
  assert.equal(await closingA, true);
  h.context.__mediaBItem = makeCatalogItem(MEDIA_B, LOCATION_B);
  enqueue(h, "GET", metadataEndpoint(MEDIA_B), response(makeMetadataPayload({
    mediaId: MEDIA_B,
    title: "Persisted B",
    description: "Description B",
    tagKeys: ["persisted-b"],
  })));
  await h.run("handleOpenMetadataWorkspace(__mediaBItem, null)");
  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Unsaved B";
  dispatch(titleInput, "input");
  const before = metadataStateOf(h);

  catalogResponse.resolve(response({ items: [], total: 0, offset: 0, limit: 30, q: "" }));
  await savingA;
  await h.flush();

  const after = metadataStateOf(h);
  assert.equal(after.mediaId, MEDIA_B);
  assert.equal(after.currentTitle, before.currentTitle);
  assert.equal(after.baselineTitle, before.baselineTitle);
  assert.equal(after.dirty, true);
  assert.equal(after.metadataOpen, true);
  assert.equal(after.confirmationRequestId, null);
  assert.equal(h.document.activeElement, titleInput);
});

test("Metadata stale save success, error, and finally cannot release a newer owner", async (t) => {
  for (const staleOutcome of ["success", "error"]) {
    await t.test(staleOutcome, async () => {
      const h = await createHarness();
      setMetadataWorkspace(h);
      const saveAResponse = deferred();
      enqueue(h, "PUT", metadataEndpoint(MEDIA_A), saveAResponse.promise);
      const savingA = h.run("handleSaveMetadata()");
      await h.flush();

      const closingA = h.run("closeMetadataWorkspace()");
      activateConfirmation(h, "confirm");
      assert.equal(await closingA, true);
      h.context.__mediaBItem = makeCatalogItem(MEDIA_B, LOCATION_B);
      enqueue(h, "GET", metadataEndpoint(MEDIA_B), response(makeMetadataPayload({
        mediaId: MEDIA_B,
        title: "Persisted B",
        description: "Description B",
        tagKeys: ["persisted-b"],
      })));
      await h.run("handleOpenMetadataWorkspace(__mediaBItem, null)");
      const titleInput = h.document.querySelector("#metadata-title-input");
      titleInput.value = "Unsaved B";
      dispatch(titleInput, "input");

      const saveBResponse = deferred();
      enqueue(h, "PUT", metadataEndpoint(MEDIA_B), saveBResponse.promise);
      const savingB = h.run("handleSaveMetadata()");
      await h.flush();
      const ownerB = metadataStateOf(h).saveOwnerToken;
      assert.ok(ownerB);

      if (staleOutcome === "success") {
        saveAResponse.resolve(response({
          metadata: makeMetadataPayload({ mediaId: MEDIA_A, title: "Saved A" }),
        }));
      } else {
        saveAResponse.reject(new Error("synthetic stale save failure"));
      }
      await savingA;
      await h.flush();

      const whileBIsSaving = metadataStateOf(h);
      assert.equal(whileBIsSaving.mediaId, MEDIA_B);
      assert.equal(whileBIsSaving.currentTitle, "Unsaved B");
      assert.equal(whileBIsSaving.saving, true);
      assert.equal(whileBIsSaving.status, "Saving...");
      assert.equal(whileBIsSaving.saveOwnerToken, ownerB);
      assert.equal(whileBIsSaving.saveOwnerMediaId, MEDIA_B);
      assert.equal(whileBIsSaving.confirmationRequestId, null);

      saveBResponse.resolve(response({
        metadata: makeMetadataPayload({
          mediaId: MEDIA_B,
          title: "Unsaved B",
          description: "Description B",
          tagKeys: ["persisted-b"],
        }),
      }));
      await savingB;
      await h.flush();
      assert.equal(metadataStateOf(h).mediaId, null);
      assert.equal(metadataStateOf(h).saveOwnerToken, null);
    });
  }
});

test("Metadata safe action, Escape, and backdrop preserve the dirty workspace and focus", async (t) => {
  for (const dismissal of ["safe action", "Escape", "backdrop"]) {
    await t.test(dismissal, async () => {
      const h = await createHarness();
      setMetadataWorkspace(h);
      const closeButton = h.document.querySelector("#metadata-close-button");
      closeButton.focus();
      const before = metadataStateOf(h);
      const closing = h.run("closeMetadataWorkspace()");

      if (dismissal === "safe action") activateConfirmation(h, "dismiss");
      else if (dismissal === "Escape") dismissConfirmationWithEscape(h);
      else dispatch(h.document.querySelector("#confirmation-dialog"), "click", {
        target: h.document.querySelector("#confirmation-dialog"),
      });

      assert.equal(await closing, false);
      const after = metadataStateOf(h);
      assert.equal(after.mediaId, before.mediaId);
      assert.equal(after.openItemMediaId, before.openItemMediaId);
      assert.equal(after.currentTitle, before.currentTitle);
      assert.equal(after.currentDescription, before.currentDescription);
      assert.deepEqual(after.currentTags, before.currentTags);
      assert.equal(after.dirty, true);
      assert.equal(after.metadataOpen, true);
      assert.equal(after.focusedClose, true);
      assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 0);
    });
  }
});

test("Nested Escape ownership keeps dirty Metadata open through Chromium keydown and cancel ordering", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const closeButton = h.document.querySelector("#metadata-close-button");
  closeButton.focus();
  let settlements = 0;
  const closing = h.run("closeMetadataWorkspace()").then((result) => {
    settlements += 1;
    return result;
  });

  const misplacedParentKeydown = dispatch(closeButton, "keydown", { key: "Escape" });
  assert.equal(misplacedParentKeydown.defaultPrevented, true);
  assert.equal(confirmationStateOf(h).open, true);
  assert.equal(metadataStateOf(h).metadataOpen, true);

  const browserEscape = pressEscapeInBrowserOrder(
    h,
    h.document.querySelector("#confirmation-dismiss-button"),
  );
  assert.equal(browserEscape.keydown.defaultPrevented, true);
  assert.equal(browserEscape.keydown.propagationStopped, true);
  assert.equal(browserEscape.cancelEvents.length, 0);
  assert.equal(await closing, false);
  await h.flush();

  const afterKeydown = metadataStateOf(h);
  assert.equal(settlements, 1);
  assert.equal(afterKeydown.metadataOpen, true);
  assert.equal(afterKeydown.currentTitle, "Unsaved title");
  assert.equal(afterKeydown.currentDescription, "Unsaved description");
  assert.deepEqual(afterKeydown.currentTags, ["persisted", "unsaved"]);
  assert.equal(afterKeydown.dirty, true);
  assert.equal(afterKeydown.focusedClose, true);
  assert.equal(h.document.__modalStack.at(-1), h.document.querySelector("#metadata-dialog"));

  const cancelFallback = h.run("closeMetadataWorkspace()");
  const cancelEvents = dispatchBrowserCancelSequence(h);
  assert.equal(cancelEvents.length, 2);
  assert.equal(cancelEvents[0].dialog, h.document.querySelector("#confirmation-dialog"));
  assert.equal(cancelEvents[1].dialog, h.document.querySelector("#metadata-dialog"));
  assert.equal(cancelEvents[0].event.defaultPrevented, true);
  assert.equal(cancelEvents[1].event.defaultPrevented, true);
  assert.equal(await cancelFallback, false);
  await h.flush();
  assert.equal(metadataStateOf(h).metadataOpen, true);
  assert.equal(metadataStateOf(h).dirty, true);
});

test("Nested Upload Escape dismisses only confirmation and sends zero DELETE requests", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  h.run(`
    document.querySelector("#upload-dialog").showModal();
    document.querySelector("#upload-cancel-button").focus();
  `);
  const cancellation = h.run("handleCancelUpload()");

  const browserEscape = pressEscapeInBrowserOrder(
    h,
    h.document.querySelector("#confirmation-dismiss-button"),
  );
  assert.equal(browserEscape.keydown.defaultPrevented, true);
  assert.equal(browserEscape.cancelEvents.length, 0);
  await cancellation;
  await h.flush();

  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), true);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);
  assert.equal(stateOf(h).snapshotState, "receiving");
  assert.equal(stateOf(h).received, 4);
  assert.equal(h.document.activeElement, h.document.querySelector("#upload-cancel-button"));
});

test("Ordinary Escape still closes active Details, Upload, and clean Metadata dialogs", async () => {
  const h = await createHarness();
  h.run(`
    document.querySelector("#media-details-dialog").showModal();
    document.querySelector("#media-details-close").focus();
  `);
  const detailsEscape = pressEscapeInBrowserOrder(h);
  assert.equal(detailsEscape.keydown.defaultPrevented, true);
  assert.equal(h.run('document.querySelector("#media-details-dialog").hasAttribute("open")'), false);

  h.run(`
    document.querySelector("#upload-dialog").showModal();
    document.querySelector("#upload-close-button").focus();
  `);
  const uploadEscape = pressEscapeInBrowserOrder(h);
  assert.equal(uploadEscape.keydown.defaultPrevented, true);
  await h.flush();
  assert.equal(h.run('document.querySelector("#upload-dialog").hasAttribute("open")'), false);

  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  h.document.querySelector("#metadata-title-input").focus();
  const metadataEscape = pressEscapeInBrowserOrder(h);
  assert.equal(metadataEscape.keydown.defaultPrevented, true);
  await h.flush();
  assert.equal(metadataStateOf(h).mediaId, null);
  assert.equal(metadataStateOf(h).metadataOpen, false);
  assert.equal(confirmationStateOf(h).open, false);
});

test("Same-media hidden dirty workspace reopens without confirmation, reload, or field loss", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  h.document.querySelector("#metadata-dialog").removeAttribute("open");
  h.fetchController.clearCalls();

  const reopening = h.run("handleOpenMetadataWorkspace(__metadataItem, document.querySelector('#metadata-close-button'))");
  assert.equal(confirmationStateOf(h).open, false);
  await reopening;

  const state = metadataStateOf(h);
  assert.equal(state.mediaId, MEDIA_A);
  assert.equal(state.metadataOpen, true);
  assert.equal(state.currentTitle, "Unsaved title");
  assert.equal(state.currentDescription, "Unsaved description");
  assert.deepEqual(state.currentTags, ["persisted", "unsaved"]);
  assert.equal(state.dirty, true);
  assert.equal(state.focusedTitle, true);
  assert.equal(confirmationStateOf(h).open, false);
  assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_A)).length, 0);
  assert.equal(h.fetchController.matching("PUT", metadataEndpoint(MEDIA_A)).length, 0);
});

test("Different-media dirty switch preserves lifecycle ownership across rejected and accepted outcomes", async (t) => {
  for (const dismissal of ["Keep editing", "Escape", "backdrop"]) {
    await t.test(`hidden A reopens after ${dismissal}`, async () => {
      const h = await createHarness();
      prepareDirtyMetadataSwitch(h);
      const before = metadataStateOf(h);
      const metadataDialog = h.document.querySelector("#metadata-dialog");
      const initialShowModalCount = metadataDialog.showModalCount;
      const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
      assert.equal(confirmationStateOf(h).open, true);

      if (dismissal === "Keep editing") {
        activateConfirmation(h, "dismiss");
      } else if (dismissal === "Escape") {
        const escape = pressEscapeInBrowserOrder(
          h,
          h.document.querySelector("#confirmation-dismiss-button"),
        );
        assert.equal(escape.keydown.defaultPrevented, true);
        assert.equal(escape.cancelEvents.length, 0);
      } else {
        dispatch(h.document.querySelector("#confirmation-dialog"), "click", {
          target: h.document.querySelector("#confirmation-dialog"),
        });
      }

      await switching;
      await h.flush();
      const after = metadataStateOf(h);
      assertDirtyMetadataStatePreserved(before, after);
      assert.equal(confirmationStateOf(h).open, false);
      assert.equal(after.confirmationRequestId, null);
      assert.equal(after.metadataOpen, true);
      assert.equal(metadataDialog.showModalCount, initialShowModalCount + 1);
      assert.equal(after.focusedTitle, true);
      assert.equal(h.run("document.activeElement.parentNode === document.querySelector('#metadata-dialog')"), true);
      assert.equal(h.run("metadataWorkspace === __workspaceA"), true);
      assert.equal(h.run("metadataWorkspace.openItem === __openItemA"), true);
      assert.equal(h.run("metadataWorkspace.baseline === __baselineA"), true);
      assert.equal(h.run("metadataWorkspace.current === __currentA"), true);
      assert.equal(h.run("metadataOpenerElement === __metadataOpenerA"), true);
      assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 0);
      assert.equal(h.fetchController.matchingPrefix("PUT", "/api/media/").length, 0);
      assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 0);
      assert.equal(h.run("__galleryA.display_title"), "Persisted A");
      assert.equal(h.run("__mediaBItem.display_title"), "Persisted B");
      assert.equal(galleryCardTitle(h, 0), "Persisted A");
      assert.equal(galleryCardTitle(h, 1), "Persisted B");
      const beforeUnload = dispatchBeforeUnload(h);
      assert.equal(beforeUnload.defaultPrevented, true);
      assert.equal(beforeUnload.returnValue, "");
    });
  }

  await t.test("visible A is neither double-opened nor unnecessarily refocused or reset", async () => {
    const h = await createHarness();
    prepareDirtyMetadataSwitch(h, { hidden: false });
    const before = metadataStateOf(h);
    const metadataDialog = h.document.querySelector("#metadata-dialog");
    const titleInput = h.document.querySelector("#metadata-title-input");
    titleInput.focus();
    const initialShowModalCount = metadataDialog.showModalCount;
    const focusCountBeforeDismissal = titleInput.focusCount;
    const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
    activateConfirmation(h, "dismiss");
    await switching;

    const after = metadataStateOf(h);
    assertDirtyMetadataStatePreserved(before, after);
    assert.equal(after.metadataOpen, true);
    assert.equal(metadataDialog.showModalCount, initialShowModalCount);
    assert.equal(titleInput.focusCount, focusCountBeforeDismissal + 1);
    assert.equal(h.run("metadataWorkspace === __workspaceA"), true);
    assert.equal(h.run("metadataWorkspace.current === __currentA"), true);
    assert.equal(h.run("metadataOpenerElement === __metadataOpenerA"), true);
    assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 0);
  });

  await t.test("stale rejected result cannot reopen A over a newer hidden workspace", async () => {
    const h = await createHarness();
    prepareDirtyMetadataSwitch(h);
    const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
    const itemC = makeCatalogItem(303, 3003);
    setMetadataWorkspace(h, {
      item: itemC,
      baselineTitle: "Persisted C",
      currentTitle: "Unsaved C",
      baselineDescription: "Persisted description C",
      currentDescription: "Unsaved description C",
      baselineTags: ["persisted-c"],
      currentTags: ["persisted-c", "unsaved-c"],
    });
    h.context.__metadataOpenerC = h.document.createElement("button");
    h.run("metadataOpenerElement = __metadataOpenerC; globalThis.__workspaceC = metadataWorkspace");
    h.document.querySelector("#metadata-dialog").removeAttribute("open");
    const newerBefore = metadataStateOf(h);
    const initialShowModalCount = h.document.querySelector("#metadata-dialog").showModalCount;

    activateConfirmation(h, "dismiss");
    await switching;

    const after = metadataStateOf(h);
    assertDirtyMetadataStatePreserved(newerBefore, after);
    assert.equal(after.mediaId, 303);
    assert.equal(after.metadataOpen, false);
    assert.equal(h.document.querySelector("#metadata-dialog").showModalCount, initialShowModalCount);
    assert.equal(h.run("metadataWorkspace === __workspaceC"), true);
    assert.equal(h.run("metadataOpenerElement === __metadataOpenerC"), true);
    assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 0);
  });

  await t.test("stale rejected result cannot reopen A after its catalog scope changes", async () => {
    const h = await createHarness();
    prepareDirtyMetadataSwitch(h);
    const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
    h.run("catalogState.collection = PROCESSED_COLLECTION");
    const newerBefore = metadataStateOf(h);
    const initialShowModalCount = h.document.querySelector("#metadata-dialog").showModalCount;

    activateConfirmation(h, "dismiss");
    await switching;

    const after = metadataStateOf(h);
    assertDirtyMetadataStatePreserved(newerBefore, after);
    assert.equal(after.mediaId, MEDIA_A);
    assert.equal(after.scope, "processed");
    assert.equal(after.metadataOpen, false);
    assert.equal(h.document.querySelector("#metadata-dialog").showModalCount, initialShowModalCount);
    assert.equal(h.run("metadataWorkspace === __workspaceA"), true);
    assert.equal(h.run("metadataOpenerElement === __metadataOpenerA"), true);
    assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 0);
  });

  await t.test("affirmative current-context discard opens B exactly once", async () => {
    const h = await createHarness();
    prepareDirtyMetadataSwitch(h);
    enqueue(h, "GET", metadataEndpoint(MEDIA_B), response(makeMetadataPayload({
      mediaId: MEDIA_B,
      title: "Persisted B",
      description: "Description B",
      tagKeys: ["persisted-b"],
    })));

    const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
    assert.equal(confirmationStateOf(h).open, true);
    activateConfirmation(h, "confirm");
    await switching;
    await h.flush();

    const after = metadataStateOf(h);
    assert.equal(after.mediaId, MEDIA_B);
    assert.equal(after.openItemMediaId, MEDIA_B);
    assert.equal(after.currentTitle, "Persisted B");
    assert.equal(after.metadataOpen, true);
    assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 1);
    assert.equal(h.fetchController.matching("PUT", metadataEndpoint(MEDIA_A)).length, 0);
    assert.equal(h.run("metadataOpenerElement === __metadataOpenerB"), true);
  });

  await t.test("stale affirmative result cannot switch a newer hidden workspace to B", async () => {
    const h = await createHarness();
    prepareDirtyMetadataSwitch(h);
    const switching = h.run("handleOpenMetadataWorkspace(__mediaBItem, __metadataOpenerB)");
    const itemC = makeCatalogItem(303, 3003);
    setMetadataWorkspace(h, {
      item: itemC,
      baselineTitle: "Persisted C",
      currentTitle: "Newer unsaved C",
      baselineDescription: "Persisted description C",
      currentDescription: "Newer unsaved description C",
      baselineTags: ["persisted-c"],
      currentTags: ["persisted-c", "newer-c"],
    });
    h.run("globalThis.__workspaceC = metadataWorkspace");
    h.document.querySelector("#metadata-dialog").removeAttribute("open");
    const newerBefore = metadataStateOf(h);

    activateConfirmation(h, "confirm");
    await switching;

    const after = metadataStateOf(h);
    assertDirtyMetadataStatePreserved(newerBefore, after);
    assert.equal(after.mediaId, 303);
    assert.equal(after.metadataOpen, false);
    assert.equal(h.run("metadataWorkspace === __workspaceC"), true);
    assert.equal(h.fetchController.matching("GET", metadataEndpoint(MEDIA_B)).length, 0);
  });
});

test("Gallery presentation remains persisted until Save and isolated to the intended card", async (t) => {
  await t.test("safe dismissal, same-media reopen, and discard", async () => {
    const h = await createHarness();
    const itemA = makeCatalogItem(MEDIA_A, LOCATION_A);
    const itemB = makeCatalogItem(MEDIA_B, LOCATION_B);
    itemA.display_title = "Persisted A";
    itemB.display_title = "Persisted B";
    h.context.__galleryA = itemA;
    h.context.__galleryB = itemB;
    h.run("renderCatalogSuccess({ items: [__galleryA, __galleryB], total: 2, offset: 0, limit: 30, q: '' })");
    setMetadataWorkspace(h, {
      item: itemA,
      baselineTitle: "Persisted A",
      currentTitle: "Persisted A",
      currentDescription: "Persisted description",
      currentTags: ["persisted"],
    });
    const titleInput = h.document.querySelector("#metadata-title-input");
    titleInput.value = "Persisted A TEMP";
    dispatch(titleInput, "input");
    h.run('metadataWorkspace.suggestedFilename = "temporary-name.mp4"; renderMetadataWorkspace()');

    assert.equal(h.run("metadataWorkspace.openItem !== __galleryA"), true);
    assert.equal(h.run("__galleryA.display_title"), "Persisted A");
    assert.equal(galleryCardTitle(h, 0), "Persisted A");
    assert.equal(galleryCardTitle(h, 1), "Persisted B");

    const safeClose = h.run("closeMetadataWorkspace()");
    activateConfirmation(h, "dismiss");
    assert.equal(await safeClose, false);
    assert.equal(galleryCardTitle(h, 0), "Persisted A");
    h.document.querySelector("#metadata-dialog").removeAttribute("open");
    await h.run("handleOpenMetadataWorkspace(__galleryA, null)");
    assert.equal(metadataStateOf(h).currentTitle, "Persisted A TEMP");
    assert.equal(galleryCardTitle(h, 0), "Persisted A");

    h.fetchController.enqueue(
      (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
      response({ items: [itemA, itemB], total: 2, offset: 0, limit: 30, q: "" }),
    );
    const discard = h.run("closeMetadataWorkspace()");
    activateConfirmation(h, "confirm");
    assert.equal(await discard, true);
    await h.flush();
    assert.equal(galleryCardTitle(h, 0), "Persisted A");
    assert.equal(galleryCardTitle(h, 1), "Persisted B");
  });

  await t.test("successful Save updates A once and never changes B", async () => {
    const h = await createHarness();
    const itemA = makeCatalogItem(MEDIA_A, LOCATION_A);
    const itemB = makeCatalogItem(MEDIA_B, LOCATION_B);
    itemA.display_title = "Persisted A";
    itemB.display_title = "Persisted B";
    const savedA = { ...itemA, display_title: "Persisted A TEMP" };
    h.context.__galleryA = itemA;
    h.context.__galleryB = itemB;
    h.run("renderCatalogSuccess({ items: [__galleryA, __galleryB], total: 2, offset: 0, limit: 30, q: '' })");
    setMetadataWorkspace(h, {
      item: itemA,
      baselineTitle: "Persisted A",
      currentTitle: "Persisted A",
      baselineDescription: "Description A",
      currentDescription: "Description A",
      baselineTags: ["persisted"],
      currentTags: ["persisted"],
    });
    const titleInput = h.document.querySelector("#metadata-title-input");
    titleInput.value = "Persisted A TEMP";
    dispatch(titleInput, "input");
    const descriptionInput = h.document.querySelector("#metadata-description-input");
    descriptionInput.value = "Description A TEMP";
    dispatch(descriptionInput, "input");
    h.run('metadataWorkspace.current.tagKeys = ["persisted", "temporary"]; advanceMetadataWorkspaceRevision(); updateMetadataControls()');
    enqueue(h, "PUT", metadataEndpoint(MEDIA_A), response({
      metadata: makeMetadataPayload({
        mediaId: MEDIA_A,
        title: "Persisted A TEMP",
        description: "Description A TEMP",
        tagKeys: ["persisted", "temporary"],
      }),
    }));
    for (let index = 0; index < 2; index += 1) {
      h.fetchController.enqueue(
        (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
        response({ items: [savedA, itemB], total: 2, offset: 0, limit: 30, q: "" }),
      );
    }

    assert.equal(galleryCardTitle(h, 0), "Persisted A");
    await h.run("handleSaveMetadata()");
    await h.flush();
    assert.equal(h.fetchController.matching("PUT", metadataEndpoint(MEDIA_A)).length, 1);
    assert.equal(galleryCardTitle(h, 0), "Persisted A TEMP");
    assert.equal(galleryCardTitle(h, 1), "Persisted B");
  });
});

test("Native beforeunload guard follows dirty, dismissal, reopen, Save, and Discard lifecycle", async (t) => {
  await t.test("dirty and safe dismissal remain guarded without application confirmation", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    assert.equal(metadataStateOf(h).beforeUnloadAttached, true);
    const dirtyUnload = dispatchBeforeUnload(h);
    assert.equal(dirtyUnload.defaultPrevented, true);
    assert.equal(dirtyUnload.returnValue, "");
    assert.equal(confirmationStateOf(h).open, false);

    const closing = h.run("closeMetadataWorkspace()");
    pressEscapeInBrowserOrder(h, h.document.querySelector("#confirmation-dismiss-button"));
    assert.equal(await closing, false);
    await h.flush();
    assert.equal(metadataStateOf(h).beforeUnloadAttached, true);
    h.document.querySelector("#metadata-dialog").removeAttribute("open");
    await h.run("handleOpenMetadataWorkspace(__metadataItem, null)");
    assert.equal(metadataStateOf(h).beforeUnloadAttached, true);
    assert.equal(dispatchBeforeUnload(h).defaultPrevented, true);
    assert.equal(confirmationStateOf(h).open, false);
  });

  await t.test("clean and saved workspaces are unguarded", async () => {
    const clean = await createHarness();
    setMetadataWorkspace(clean, {
      currentTitle: "Persisted title",
      currentDescription: "Persisted description",
      currentTags: ["persisted"],
    });
    assert.equal(metadataStateOf(clean).beforeUnloadAttached, false);
    assert.equal(dispatchBeforeUnload(clean).defaultPrevented, false);

    const saved = await createHarness();
    setMetadataWorkspace(saved);
    enqueue(saved, "PUT", metadataEndpoint(), response({
      metadata: makeMetadataPayload({
        title: "Unsaved title",
        description: "Unsaved description",
        tagKeys: ["persisted", "unsaved"],
      }),
    }));
    await saved.run("handleSaveMetadata()");
    await saved.flush();
    assert.equal(metadataStateOf(saved).beforeUnloadAttached, false);
    assert.equal(dispatchBeforeUnload(saved).defaultPrevented, false);
    assert.equal(confirmationStateOf(saved).open, false);
  });

  await t.test("confirmed Discard clears the guard", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    const closing = h.run("closeMetadataWorkspace()");
    activateConfirmation(h, "confirm");
    assert.equal(await closing, true);
    await h.flush();
    assert.equal(metadataStateOf(h).beforeUnloadAttached, false);
    assert.equal(dispatchBeforeUnload(h).defaultPrevented, false);
    assert.equal(confirmationStateOf(h).open, false);
  });
});

test("Metadata current affirmative discard closes exactly once despite repeated triggering", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const initialRequestToken = h.run("metadataRequestToken");
  const first = h.run("closeMetadataWorkspace()");
  const repeated = h.run("closeMetadataWorkspace()");

  activateConfirmation(h, "confirm");
  assert.equal(await first, true);
  assert.equal(await repeated, false);
  await h.flush();

  assert.equal(metadataStateOf(h).mediaId, null);
  assert.equal(metadataStateOf(h).metadataOpen, false);
  assert.equal(h.run("metadataRequestToken"), initialRequestToken + 1);
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 1);
});

test("Metadata newer edit revision invalidates a pending affirmative discard", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const closing = h.run("closeMetadataWorkspace()");
  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Newer unsaved title";
  dispatch(titleInput, "input");

  activateConfirmation(h, "confirm");
  assert.equal(await closing, false);

  const state = metadataStateOf(h);
  assert.equal(state.mediaId, MEDIA_A);
  assert.equal(state.currentTitle, "Newer unsaved title");
  assert.equal(state.dirty, true);
  assert.equal(state.metadataOpen, true);
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 0);
});

test("Metadata media identity change invalidates a pending affirmative discard", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const closing = h.run("closeMetadataWorkspace()");
  h.context.__newerMetadataItem = makeCatalogItem(MEDIA_B, LOCATION_B);
  h.run(`
    metadataWorkspace.openMediaId = __newerMetadataItem.media_id;
    metadataWorkspace.openItem = __newerMetadataItem;
    metadataWorkspace.current.displayTitle = "Newer media edits";
    document.querySelector("#metadata-title-input").value = "Newer media edits";
    advanceMetadataWorkspaceRevision();
  `);

  activateConfirmation(h, "confirm");
  assert.equal(await closing, false);

  const state = metadataStateOf(h);
  assert.equal(state.mediaId, MEDIA_B);
  assert.equal(state.openItemMediaId, MEDIA_B);
  assert.equal(state.currentTitle, "Newer media edits");
  assert.equal(state.metadataOpen, true);
});

test("Metadata scope or workspace replacement invalidates a pending affirmative discard", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  const closing = h.run("closeMetadataWorkspace()");
  h.run("setCatalogScope(PROCESSED_COLLECTION)");

  activateConfirmation(h, "confirm");
  assert.equal(await closing, false);

  const state = metadataStateOf(h);
  assert.equal(state.mediaId, MEDIA_A);
  assert.equal(state.scope, "processed");
  assert.equal(state.currentTitle, "Unsaved title");
  assert.equal(state.metadataOpen, true);
});

test("Metadata AI safe action, Escape, and backdrop issue zero requests and preserve workspace", async (t) => {
  for (const dismissal of ["safe action", "Escape", "backdrop"]) {
    await t.test(dismissal, async () => {
      const h = await createHarness();
      setMetadataWorkspace(h);
      enableMetadataAi(h);
      const analyzeButton = h.document.querySelector("#metadata-ai-analyze-button");
      analyzeButton.focus();
      const before = metadataStateOf(h);
      const analysis = h.run("handleAnalyzeMetadataByAi()");

      if (dismissal === "safe action") activateConfirmation(h, "dismiss");
      else if (dismissal === "Escape") dismissConfirmationWithEscape(h);
      else dispatch(h.document.querySelector("#confirmation-dialog"), "click", {
        target: h.document.querySelector("#confirmation-dialog"),
      });

      await analysis;
      await h.flush();
      const after = metadataStateOf(h);
      assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
      assert.equal(after.mediaId, before.mediaId);
      assert.equal(after.currentTitle, before.currentTitle);
      assert.equal(after.currentDescription, before.currentDescription);
      assert.deepEqual(after.currentTags, before.currentTags);
      assert.equal(after.analyzing, false);
      assert.equal(after.metadataOpen, true);
      assert.equal(after.focusedAnalyze, true);
    });
  }
});

test("Metadata AI uses one revalidated media-location identity and one request owner", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  enableMetadataAi(h);
  const pendingResponse = deferred();
  enqueue(h, "POST", metadataAiEndpoint(), pendingResponse.promise);

  const first = h.run("handleAnalyzeMetadataByAi()");
  const repeated = h.run("handleAnalyzeMetadataByAi()");
  activateConfirmation(h, "confirm");
  activateConfirmation(h, "confirm");
  await h.flush();

  const calls = h.fetchController.matching("POST", metadataAiEndpoint());
  assert.equal(calls.length, 1);
  assert.equal(h.fetchController.matchingPrefix("POST", `/api/media/${MEDIA_B}/`).length, 0);
  assert.equal(metadataStateOf(h).analyzing, true);

  pendingResponse.resolve(response({
    media_id: MEDIA_A,
    location_id: LOCATION_A,
    suggestion: {
      title: "AI title",
      description: "AI description",
      tags: [],
      suggested_filename: "ai-title.mp4",
    },
  }));
  await Promise.all([first, repeated]);
  await h.flush();

  const state = metadataStateOf(h);
  assert.equal(state.analyzing, false);
  assert.equal(state.aiSuggestionApplied, false);
  assert.equal(state.currentTitle, "Unsaved title");
  assert.equal(state.currentDescription, "Unsaved description");
  assert.equal(state.suggestedFilename, "ai-title.mp4");
  assert.match(state.aiStatus, /Suggestion ready/);
  assert.equal(state.analysisProgressHidden, true);
});

test("Metadata AI endpoint cannot mix live media with a captured location", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  enableMetadataAi(h);
  enqueue(h, "POST", metadataAiEndpoint(MEDIA_A, LOCATION_A), response({
    media_id: MEDIA_A,
    location_id: LOCATION_A,
    suggestion: {
      title: "Captured identity title",
      description: "Captured identity description",
      tags: [],
      suggested_filename: "captured.mp4",
    },
  }));
  const analysis = h.run("handleAnalyzeMetadataByAi()");
  h.run(`
    globalThis.__metadataMediaReads = 0;
    Object.defineProperty(metadataWorkspace, "openMediaId", {
      configurable: true,
      get() {
        globalThis.__metadataMediaReads += 1;
        return globalThis.__metadataMediaReads === 1 ? ${MEDIA_A} : ${MEDIA_B};
      },
    });
  `);

  activateConfirmation(h, "confirm");
  await analysis;
  await h.flush();

  assert.equal(h.fetchController.matching("POST", metadataAiEndpoint(MEDIA_A, LOCATION_A)).length, 1);
  assert.equal(h.fetchController.matching("POST", metadataAiEndpoint(MEDIA_B, LOCATION_A)).length, 0);
  assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 1);
});

test("Metadata AI stale same-media error and finally cannot release a newer request owner", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  enableMetadataAi(h);
  const requestAResponse = deferred();
  const requestBResponse = deferred();
  enqueue(h, "POST", metadataAiEndpoint(), requestAResponse.promise);
  enqueue(h, "POST", metadataAiEndpoint(), requestBResponse.promise);

  const requestA = h.run("handleAnalyzeMetadataByAi()");
  activateConfirmation(h, "confirm");
  await h.flush();
  h.run(`
    metadataWorkspace.analyzing = false;
    advanceMetadataWorkspaceRevision();
    updateMetadataControls();
  `);
  const requestB = h.run("handleAnalyzeMetadataByAi()");
  activateConfirmation(h, "confirm");
  await h.flush();
  const ownerB = h.run("metadataAiRequestToken");
  assert.equal(h.fetchController.matching("POST", metadataAiEndpoint()).length, 2);
  assert.equal(metadataStateOf(h).analyzing, true);

  requestAResponse.reject(new Error("synthetic stale AI failure"));
  await requestA;
  await h.flush();

  assert.equal(h.run("metadataAiRequestToken"), ownerB);
  assert.equal(metadataStateOf(h).analyzing, true);
  assert.equal(h.document.querySelector("#metadata-ai-status").textContent, "");
  assert.equal(metadataStateOf(h).analysisProgressHidden, false);

  requestBResponse.resolve(response({
    media_id: MEDIA_A,
    location_id: LOCATION_A,
    suggestion: {
      title: "New owner title",
      description: "New owner description",
      tags: [],
      suggested_filename: "new-owner.mp4",
    },
  }));
  await requestB;
  await h.flush();

  const state = metadataStateOf(h);
  assert.equal(state.analyzing, false);
  assert.equal(state.aiSuggestionApplied, false);
  assert.equal(state.currentTitle, "Unsaved title");
  assert.equal(state.currentDescription, "Unsaved description");
  assert.equal(state.suggestedFilename, "new-owner.mp4");
  assert.match(state.aiStatus, /Suggestion ready/);
});

test("Metadata AI stale response media, location, and capability cannot apply", async (t) => {
  for (const replacement of ["media", "location", "capability"]) {
    await t.test(replacement, async () => {
      const h = await createHarness();
      setMetadataWorkspace(h);
      enableMetadataAi(h);
      const pendingResponse = deferred();
      enqueue(h, "POST", metadataAiEndpoint(), pendingResponse.promise);
      const analysis = h.run("handleAnalyzeMetadataByAi()");
      activateConfirmation(h, "confirm");
      await h.flush();

      if (replacement === "media") {
        h.context.__replacementItem = makeCatalogItem(MEDIA_B, LOCATION_B);
        h.run(`
          metadataWorkspace.openMediaId = __replacementItem.media_id;
          metadataWorkspace.openItem = __replacementItem;
          metadataWorkspace.current.displayTitle = "Replacement media title";
          metadataWorkspace.analyzing = false;
          advanceMetadataWorkspaceRevision();
        `);
      } else if (replacement === "location") {
        h.context.__replacementItem = makeCatalogItem(MEDIA_A, LOCATION_B);
        h.run("metadataWorkspace.openItem = __replacementItem");
      } else {
        h.run('renderAiCapability({ available: false, status: "not_configured" })');
      }

      pendingResponse.resolve(response({
        suggestion: {
          title: "Stale AI title",
          description: "Stale AI description",
          tags: [],
          suggested_filename: "stale-ai.mp4",
        },
      }));
      await analysis;
      await h.flush();

      const state = metadataStateOf(h);
      assert.equal(state.aiSuggestionApplied, false);
      assert.equal(state.suggestedFilename, "");
      assert.notEqual(state.currentTitle, "Stale AI title");
      assert.equal(state.analyzing, false);
      if (replacement === "media") {
        assert.equal(state.mediaId, MEDIA_B);
        assert.equal(state.locationId, LOCATION_B);
        assert.equal(state.currentTitle, "Replacement media title");
      } else if (replacement === "location") {
        assert.equal(state.mediaId, MEDIA_A);
        assert.equal(state.locationId, LOCATION_B);
      }
    });
  }
});

test("Metadata AI stale media or location context issues zero requests and preserves newer workspace", async (t) => {
  await t.test("media identity", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    enableMetadataAi(h);
    const analysis = h.run("handleAnalyzeMetadataByAi()");
    h.context.__newerAiItem = makeCatalogItem(MEDIA_B, LOCATION_A);
    h.run(`
      metadataWorkspace.openMediaId = __newerAiItem.media_id;
      metadataWorkspace.openItem = __newerAiItem;
      metadataWorkspace.current.displayTitle = "Newer media title";
      document.querySelector("#metadata-title-input").value = "Newer media title";
      advanceMetadataWorkspaceRevision();
    `);

    activateConfirmation(h, "confirm");
    await analysis;
    await h.flush();

    assert.equal(h.fetchController.matching("POST", metadataAiEndpoint(MEDIA_B, LOCATION_A)).length, 0);
    assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
    assert.equal(metadataStateOf(h).mediaId, MEDIA_B);
    assert.equal(metadataStateOf(h).currentTitle, "Newer media title");
    assert.equal(metadataStateOf(h).analyzing, false);
  });

  await t.test("location identity", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    enableMetadataAi(h);
    const analysis = h.run("handleAnalyzeMetadataByAi()");
    h.context.__newerLocationItem = makeCatalogItem(MEDIA_A, LOCATION_B);
    h.run("metadataWorkspace.openItem = __newerLocationItem");

    activateConfirmation(h, "confirm");
    await analysis;
    await h.flush();

    assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
    assert.equal(metadataStateOf(h).mediaId, MEDIA_A);
    assert.equal(metadataStateOf(h).locationId, LOCATION_B);
    assert.equal(metadataStateOf(h).analyzing, false);
  });
});

test("Metadata AI capability or analyzing ownership change invalidates pending confirmation", async (t) => {
  await t.test("capability", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    enableMetadataAi(h);
    const analysis = h.run("handleAnalyzeMetadataByAi()");
    h.run('renderAiCapability({ available: false, status: "not_configured" })');

    activateConfirmation(h, "confirm");
    await analysis;
    await h.flush();

    assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
    assert.equal(metadataStateOf(h).analyzing, false);
  });

  await t.test("analysis ownership", async () => {
    const h = await createHarness();
    setMetadataWorkspace(h);
    enableMetadataAi(h);
    const analysis = h.run("handleAnalyzeMetadataByAi()");
    h.run(`
      metadataAiRequestToken += 1;
      metadataWorkspace.analyzing = true;
      advanceMetadataWorkspaceRevision();
    `);

    activateConfirmation(h, "confirm");
    await analysis;
    await h.flush();

    assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
    assert.equal(metadataStateOf(h).analyzing, true);
  });
});

test("Metadata AI response cannot overwrite a newer edit revision", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  enableMetadataAi(h);
  const pendingResponse = deferred();
  enqueue(h, "POST", metadataAiEndpoint(), pendingResponse.promise);
  const analysis = h.run("handleAnalyzeMetadataByAi()");
  activateConfirmation(h, "confirm");
  await h.flush();
  assert.equal(h.fetchController.matching("POST", metadataAiEndpoint()).length, 1);

  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Edit made while AI was running";
  dispatch(titleInput, "input");
  pendingResponse.resolve(response({
    suggestion: {
      title: "Stale AI title",
      description: "Stale AI description",
      tags: [],
      suggested_filename: "stale.mp4",
    },
  }));
  await analysis;
  await h.flush();

  const state = metadataStateOf(h);
  assert.equal(state.currentTitle, "Edit made while AI was running");
  assert.equal(state.aiSuggestionApplied, false);
  assert.equal(state.suggestedFilename, "");
  assert.equal(state.analyzing, false);
  assert.equal(state.metadataOpen, true);
});

test("Seeded suggestion list immediately reveals the selected suggestion without Load", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  seedMetadataSuggestionList(h);
  const state = metadataStateOf(h);
  assert.equal(state.aiHeading, "AI suggestions");
  assert.equal(state.selectHidden, false);
  assert.equal(state.titleStripHidden, false);
  assert.equal(state.currentTitle, "Persisted title");
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media/").length, 0);
  assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
  assert.equal(h.fetchController.matching("PUT", metadataEndpoint()).length, 0);
  assert.equal(INDEX_SOURCE.includes('id="metadata-load-ai-suggestion-button"'), false);
  assert.equal(APP_SOURCE.includes("handleLoadDurableAiSuggestion"), false);
});

test("Copying one suggested field leaves other Current values and Save uses metadata PUT", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  seedMetadataSuggestionList(h);
  h.run('copySuggestionFieldToCurrent("title")');
  await h.flush();

  assert.equal(metadataStateOf(h).currentTitle, "Durable AI title");
  assert.equal(metadataStateOf(h).currentDescription, "Persisted description");
  assert.deepEqual(metadataStateOf(h).currentTags, ["persisted"]);
  assert.equal(h.fetchController.matching("PUT", metadataEndpoint()).length, 0);

  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Edited after copy";
  dispatch(titleInput, "input");
  enqueue(h, "PUT", metadataEndpoint(), response({
    status: "updated",
    metadata: makeMetadataPayload({
      title: "Edited after copy",
      description: "Persisted description",
      tagKeys: ["persisted"],
    }),
  }));
  h.fetchController.enqueue(
    (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
    response({ items: [], total: 0, offset: 0, limit: 30, q: "" }),
  );

  const saving = h.run("handleSaveMetadata()");
  await h.flush();
  await saving;
  await h.flush();

  const puts = h.fetchController.matching("PUT", metadataEndpoint());
  assert.equal(puts.length, 1);
  assert.equal(JSON.parse(puts[0].options.body).display_title, "Edited after copy");
  assert.equal(JSON.parse(puts[0].options.body).suggested_filename, undefined);
  assert.equal(h.fetchController.matching("POST", metadataAiEndpoint()).length, 0);
});

test("Dropdown change immediately switches strips and issues zero provider calls", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h);
  seedMetadataSuggestionList(h, [
    suggestionListItem({ analysisRunId: "run-1", title: "First title" }),
    suggestionListItem({ analysisRunId: "run-2", title: "Second title" }),
  ]);
  assert.equal(metadataStateOf(h).titleStripHidden, false);

  h.run('selectMetadataSuggestion("run-2")');
  await h.flush();

  const after = metadataStateOf(h);
  assert.equal(after.selectedRunId, "run-2");
  assert.equal(after.titleStripHidden, false);
  assert.equal(after.currentTitle, "Unsaved title");
  assert.equal(h.fetchController.matchingPrefix("POST", "/api/media/").length, 0);
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/companion/review-inbox/").length, 0);
  assert.equal(confirmationStateOf(h).open, false);
});

test("Mapped suggested tag already-added, remove, re-add, and repeat own revisions exactly", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted", "durable-tag"],
  });
  seedMetadataSuggestionList(h, [
    suggestionListItem({
      tags: [
        { value: "durable-tag", status: "mapped", key: "durable-tag", displayName: "durable-tag" },
        { value: "mystery", status: "unknown", key: null, displayName: "mystery" },
      ],
    }),
  ]);
  const initialRevision = metadataStateOf(h).revision;
  const alreadyAdded = h.run(`(() => {
    const button = document.querySelector("#metadata-ai-tags-strip").children[0];
    return {
      pressed: button.getAttribute("aria-pressed"),
      label: button.getAttribute("aria-label"),
      selectedClass: button.classList.contains("metadata-suggestion-tag--already-added"),
    };
  })()`);
  assert.equal(alreadyAdded.pressed, "true");
  assert.match(alreadyAdded.label, /already added to Current/);
  assert.equal(alreadyAdded.selectedClass, true);
  h.run('copySuggestionFieldToCurrent("tag", "durable-tag")');
  assert.equal(metadataStateOf(h).revision, initialRevision);
  assert.deepEqual(metadataStateOf(h).currentTags, ["persisted", "durable-tag"]);
  assert.match(metadataStateOf(h).aiStatus, /already in Current/);

  h.run('removeSelectedMetadataTag("durable-tag")');
  const removedRevision = metadataStateOf(h).revision;
  assert.equal(removedRevision, initialRevision + 1);
  assert.deepEqual(metadataStateOf(h).currentTags, ["persisted"]);
  assert.equal(
    h.run('document.querySelector("#metadata-ai-tags-strip").children[0].getAttribute("aria-pressed")'),
    "false",
  );
  h.run('copySuggestionFieldToCurrent("tag", "durable-tag")');
  const readdedRevision = metadataStateOf(h).revision;
  assert.deepEqual(metadataStateOf(h).currentTags, ["persisted", "durable-tag"]);
  assert.equal(readdedRevision, removedRevision + 1);
  h.run('copySuggestionFieldToCurrent("tag", "durable-tag")');
  assert.equal(metadataStateOf(h).revision, readdedRevision);
  assert.deepEqual(metadataStateOf(h).currentTags, ["persisted", "durable-tag"]);
  assert.equal(h.fetchController.matching("PUT", metadataEndpoint()).length, 0);
  assert.equal(h.fetchController.matching("POST", "/api/canonical-tags").length, 0);
});

test("Stored suggestion stays loadable when Analyze is unavailable", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  seedMetadataSuggestionList(h);
  h.run(`
    aiCapability = {
      available: false,
      provider_id: "",
      provider_display_name: "",
      model_id: "",
      prompt_version: "",
      execution: "server",
      status: "not_configured",
      configured: false,
      credential_available: false,
      last_connection_test: null,
      last_status_check: null,
      requires_explicit_confirmation: true,
    };
    renderMetadataWorkspace();
  `);
  const state = metadataStateOf(h);
  assert.equal(state.analyzeButtonHidden, true);
  assert.equal(state.selectHidden, false);
  assert.equal(state.titleStripHidden, false);
});

test("Available Analyze remains distinct while stored suggestion stays loadable", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  seedMetadataSuggestionList(h);
  enableMetadataAi(h);
  h.run("renderMetadataWorkspace()");
  const state = metadataStateOf(h);
  assert.equal(state.analyzeButtonHidden, false);
  assert.equal(state.titleStripHidden, false);
  assert.equal(h.document.querySelector("#metadata-ai-analyze-button").disabled, false);
});

test("Suggested filename is an immediately visible informational note and is excluded from metadata Save", async () => {
  const h = await createHarness();
  setMetadataWorkspace(h, {
    currentTitle: "Persisted title",
    currentDescription: "Persisted description",
    currentTags: ["persisted"],
  });
  seedMetadataSuggestionList(h, [
    suggestionListItem({ suggestedFilename: "durable-ai.mp4" }),
  ]);
  assert.equal(metadataStateOf(h).filenameNoteHidden, false);
  assert.match(
    h.document.querySelector("#metadata-ai-filename-note").textContent,
    /Suggested filename: durable-ai.mp4/,
  );
  const phantomInput = h.document.querySelector("#metadata-ai-filename-input");
  phantomInput.value = "moj-test-nazov.gif";
  dispatch(phantomInput, "input");
  assert.equal(metadataStateOf(h).suggestedFilename, "");

  const titleInput = h.document.querySelector("#metadata-title-input");
  titleInput.value = "Edited title";
  dispatch(titleInput, "input");
  enqueue(h, "PUT", metadataEndpoint(), response({
    status: "updated",
    metadata: makeMetadataPayload({
      title: "Edited title",
      description: "Persisted description",
      tagKeys: ["persisted"],
    }),
  }));
  h.fetchController.enqueue(
    (call) => call.method === "GET" && call.url.startsWith("/api/media?"),
    response({ items: [], total: 0, offset: 0, limit: 30, q: "" }),
  );
  const saving = h.run("handleSaveMetadata()");
  await h.flush();
  await saving;
  await h.flush();
  const body = JSON.parse(h.fetchController.matching("PUT", metadataEndpoint())[0].options.body);
  assert.equal(body.suggested_filename, undefined);
  assert.equal(body.collection, undefined);
  assert.deepEqual(Object.keys(body).sort(), [
    "content_category",
    "creator_attribution_kind",
    "creator_display_name",
    "creator_handle",
    "creator_stable_id",
    "description",
    "display_title",
    "genres",
    "tag_keys",
  ]);
});

test("Cancel upload confirms once, sends one DELETE, fences stale work, and focuses status", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  h.run(`
    document.querySelector("#upload-dialog").setAttribute("open", "");
    document.querySelector("#upload-cancel-button").focus();
  `);
  const cancellation = deferred();
  enqueue(h, "DELETE", `/api/uploads/${UPLOAD_A}`, cancellation.promise);
  const cancelPromise = h.run("handleCancelUpload()");
  const duplicatePromise = h.run("handleCancelUpload()");
  activateConfirmation(h, "confirm");
  activateConfirmation(h, "confirm");
  await h.flush();

  await duplicatePromise;
  assert.equal(stateOf(h).stateLabel, "Cancelling");
  assert.equal(stateOf(h).cancelHidden, true);
  assert.equal(stateOf(h).focusedStatus, true);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 1);

  cancellation.resolve(response(snapshot(UPLOAD_A, "cancelled", 4)));
  await cancelPromise;
  await h.flush();

  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 1);
  assert.equal(stateOf(h).snapshotState, "cancelled");
  assert.equal(stateOf(h).stateLabel, "Cancelled");
  assert.equal(stateOf(h).cancelHidden, true);
});

test("Repeated destructive activation cannot send a second DELETE", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  const cancellation = deferred();
  enqueue(h, "DELETE", `/api/uploads/${UPLOAD_A}`, cancellation.promise);

  const first = h.run("handleCancelUpload()");
  const repeatedHandler = h.run("handleCancelUpload()");
  activateConfirmation(h, "confirm");
  activateConfirmation(h, "confirm");
  await h.flush();

  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 1);
  cancellation.resolve(response(snapshot(UPLOAD_A, "cancelled", 4)));
  await Promise.all([first, repeatedHandler]);
});

test("Start is synchronously fenced and stale create responses cannot replace newer state", async () => {
  const h = await createHarness();
  setSelectedFile(h, makeFile("first.gif", 8));
  const capability = deferred();
  const create = deferred();
  enqueue(h, "GET", "/api/uploads/capability", capability.promise);
  enqueue(h, "POST", "/api/uploads", create.promise);

  const first = h.run("handleStartUpload()");
  const second = h.run("handleStartUpload()");
  await h.flush();

  assert.equal(stateOf(h).actionKind, "start");
  assert.equal(h.fetchController.matching("GET", "/api/uploads/capability").length, 1);
  assert.equal(h.fetchController.matching("POST", "/api/uploads").length, 0);

  capability.resolve(response({
    uploads_enabled: true,
    max_total_size_bytes: 1024,
    max_chunk_size_bytes: 4,
    session_ttl_seconds: 60,
  }));
  await h.flush();

  assert.equal(h.fetchController.matching("POST", "/api/uploads").length, 1);

  setSelectedFile(h, makeFile("second.gif", 8, 5678));
  create.resolve(response(snapshot(UPLOAD_A, "created", 0)));
  await Promise.all([first, second]);
  await h.flush();

  assert.equal(stateOf(h).uploadId, null);
  assert.equal(stateOf(h).hasLoopOwner, false);
});

test("Resume is synchronously fenced and stale status cannot start an upload loop", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 0 });
  const status = deferred();
  enqueueStatus(h, UPLOAD_A, status.promise);

  const first = h.run("handleResumeUpload()");
  const second = h.run("handleResumeUpload()");
  await h.flush();

  assert.equal(stateOf(h).actionKind, "resume");
  assert.equal(h.fetchController.matching("GET", `/api/uploads/${UPLOAD_A}`).length, 1);

  setSelectedFile(h, makeFile("new.gif", 8, 9999));
  status.resolve(response(snapshot(UPLOAD_A, "receiving", 0)));
  await Promise.all([first, second]);
  await h.flush();

  assert.equal(stateOf(h).uploadId, null);
  assert.equal(stateOf(h).hasLoopOwner, false);
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 0);
});

test("Resume starts only one PATCH loop when the status owner is current", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 0 });
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 0)));
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 0)));
  const patch = deferred();
  enqueue(h, "PATCH", `/api/uploads/${UPLOAD_A}`, patch.promise);

  const first = h.run("handleResumeUpload()");
  const second = h.run("handleResumeUpload()");
  await h.flush();

  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 1);
  patch.resolve(response(snapshot(UPLOAD_A, "receiving", 4)));
  await first;
  await second;
});

test("Delayed status from upload A is ignored after upload B becomes active", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 0 });
  const delayedStatus = deferred();
  enqueueStatus(h, UPLOAD_A, delayedStatus.promise);

  const refresh = h.run("refreshUploadStatus(uploadState.uploadId)");
  await h.flush();

  setActiveUpload(h, { id: UPLOAD_B, state: "receiving", received: 0 });
  delayedStatus.resolve(response(snapshot(UPLOAD_A, "publish_pending", 8)));
  await refresh;
  await h.flush();

  assert.equal(stateOf(h).uploadId, UPLOAD_B);
  assert.equal(stateOf(h).snapshotState, "receiving");
});

test("Cancel invalidates a delayed PATCH and prevents another chunk from being scheduled", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 0 });
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 0)));
  const patch = deferred();
  enqueue(h, "PATCH", `/api/uploads/${UPLOAD_A}`, patch.promise);

  const loop = h.run("runUploadLoop(currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }))");
  await h.flush();
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 1);

  enqueue(h, "DELETE", `/api/uploads/${UPLOAD_A}`, response(snapshot(UPLOAD_A, "cancelled", 0)));
  const cancellation = h.run("handleCancelUpload()");
  activateConfirmation(h, "confirm");
  await cancellation;
  await h.flush();
  assert.equal(stateOf(h).snapshotState, "cancelled");

  patch.resolve(response(snapshot(UPLOAD_A, "receiving", 4)));
  await loop;
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "cancelled");
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 1);
});

test("Completion has one owner and stale completion cannot overwrite a newer upload", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 8 });
  const completion = deferred();
  enqueue(h, "POST", `/api/uploads/${UPLOAD_A}/complete`, completion.promise);

  const first = h.run("completeUploadIfReady(currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }))");
  const second = h.run("completeUploadIfReady(currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }))");
  await h.flush();

  assert.equal(h.fetchController.matching("POST", `/api/uploads/${UPLOAD_A}/complete`).length, 1);
  assert.equal(stateOf(h).hasCompletionOwner, true);

  setActiveUpload(h, { id: UPLOAD_B, state: "receiving", received: 0 });
  completion.resolve(response(snapshot(UPLOAD_A, "received", 8)));
  await Promise.all([first, second]);
  await h.flush();

  assert.equal(stateOf(h).uploadId, UPLOAD_B);
  assert.equal(stateOf(h).snapshotState, "receiving");
});

test("Polling retries transient failures with bounded backoff and stops for unknown states", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "received", received: 8, file: makeFile("sample.gif", 8) });
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  h.run("scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }))");
  assert.equal(stateOf(h).hasPollTimer, true);

  enqueueStatus(h, UPLOAD_A, Promise.reject(new Error("temporary network failure")));
  const firstDelay = h.runNextTimer();
  assert.equal(firstDelay, 1200);
  await h.flush();

  assert.equal(stateOf(h).hasPollOwner, true);
  assert.equal(stateOf(h).pollRetryDelayMs, 2400);
  assert.match(stateOf(h).message, /temporarily unavailable/);

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "validating", 8)));
  const retryDelay = h.runNextTimer();
  assert.equal(retryDelay, 2400);
  await h.flush();

  assert.equal(stateOf(h).pollRetryDelayMs, 1200);
  assert.equal(stateOf(h).hasPollOwner, true);

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "future_state", 8)));
  h.runNextTimer();
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "future_state");
  assert.equal(stateOf(h).hasPollOwner, false);
  assert.equal(stateOf(h).hasPollTimer, false);
});

test("Recovery parsing rejects malformed, invalid, unsafe, and terminal records", async () => {
  const h = await createHarness();
  h.context.localStorage.setItem(RECOVERY_KEY, "{bad json");
  assert.equal(h.run("loadUploadRecovery()"), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);

  for (const expectedSizeBytes of [-1, 0, 1.5, 9007199254740992]) {
    h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
      upload_id: UPLOAD_A,
      file_name_hint: "sample.gif",
      expected_size_bytes: expectedSizeBytes,
      last_modified_hint: 1,
      last_known_state: "receiving",
    }));
    assert.equal(h.run("loadUploadRecovery()"), null);
    assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
  }

  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: "not-an-upload-id",
    file_name_hint: "sample.gif",
    expected_size_bytes: 8,
    last_known_state: "receiving",
  }));
  await h.run("restoreUploadRecovery()");
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/uploads/not-an-upload-id").length, 0);

  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "sample.gif",
    expected_size_bytes: 8,
    last_known_state: "publish_pending",
  }));
  assert.equal(h.run("loadUploadRecovery().last_known_state"), "publish_pending");
  assert.notEqual(h.context.localStorage.getItem(RECOVERY_KEY), null);

  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "sample.gif",
    expected_size_bytes: 8,
    last_known_state: "published",
  }));
  assert.equal(h.run("loadUploadRecovery()"), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
});

test("Publication polling is bounded while pending; published keeps polling until cataloged", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "publish_pending", received: 8 });
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  h.run("scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))");

  for (let attempt = 1; attempt <= 25; attempt += 1) {
    enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "publish_pending", 8)));
    assert.equal(h.runNextTimer(), 1200);
    await h.flush();
  }

  let state = stateOf(h);
  assert.equal(state.publicationPollAttempts, 25);
  assert.equal(state.hasPollOwner, false);
  assert.equal(state.hasPollTimer, false);
  assert.match(state.visibleMessage, /Publication is still pending/);
  assert.equal(JSON.parse(state.recovery).last_known_state, "publish_pending");

  h.run("uploadState.publicationPollAttempts = 0; scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))");
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "published", 8)));
  h.runNextTimer();
  await h.flush();

  state = stateOf(h);
  assert.equal(state.snapshotState, "published");
  assert.equal(state.stateLabel, "Published");
  assert.equal(state.visibleMessage, "Published. Awaiting cataloging. Not yet available in Gallery.");
  assert.equal(state.hasPollOwner, true);
  assert.equal(state.hasPollTimer, true);
  assert.equal(state.recovery, null);
  assert.equal(
    h.run("uploadShouldPoll(uploadState.snapshot)"),
    true,
  );
  h.fetchController.clearCalls();
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 0);

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "cataloged", 8)));
  h.runNextTimer();
  await h.flush();

  state = stateOf(h);
  assert.equal(state.snapshotState, "cataloged");
  assert.equal(state.stateLabel, "Completed");
  assert.equal(state.visibleMessage, "Cataloged. Available in Gallery.");
  assert.equal(state.hasPollOwner, false);
  assert.equal(state.hasPollTimer, false);
  assert.equal(state.recovery, null);
  assert.equal(
    h.run("uploadShouldPoll(uploadState.snapshot)"),
    false,
  );
  assert.equal(
    h.run("uploadState.galleryCatalogRefreshUploadId"),
    UPLOAD_A,
  );
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 1);

  h.run("applyUploadSnapshot(uploadState.snapshot, currentUploadContext({ uploadId: uploadState.uploadId }))");
  await h.flush();
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/media?").length, 1);

  for (const terminalState of ["rejected", "failed", "cancelled", "expired"]) {
    const terminalHarness = await createHarness();
    setActiveUpload(terminalHarness, { id: UPLOAD_A, state: "publish_pending", received: 8 });
    terminalHarness.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
    terminalHarness.run(
      "scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))",
    );
    enqueueStatus(terminalHarness, UPLOAD_A, response(snapshot(UPLOAD_A, terminalState, 8)));
    terminalHarness.runNextTimer();
    await terminalHarness.flush();
    const terminal = stateOf(terminalHarness);
    assert.equal(terminal.snapshotState, terminalState);
    assert.equal(terminal.hasPollOwner, false);
    assert.equal(terminal.hasPollTimer, false);
    assert.equal(
      terminalHarness.run("uploadShouldPoll(uploadState.snapshot)"),
      false,
    );
  }
});

test("Recovery not-found is cleared and persisted recovery stores no private bytes, handles, paths, or raw errors", async () => {
  const h = await createHarness();
  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_C,
    file_name_hint: "missing.gif",
    expected_size_bytes: 8,
    last_modified_hint: 1,
    last_known_state: "receiving",
  }));
  enqueueStatus(h, UPLOAD_C, response({ error: { code: "UPLOAD_SESSION_NOT_FOUND" } }, 404));
  await h.run("restoreUploadRecovery()");
  await h.flush();

  assert.equal(stateOf(h).uploadId, null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
  assert.match(stateOf(h).message, /expired or is unavailable/i);

  setActiveUpload(h, { id: UPLOAD_A, state: "receiving", received: 4 });
  const stored = JSON.parse(stateOf(h).recovery);
  assert.deepEqual(Object.keys(stored).sort(), [
    "expected_size_bytes",
    "file_name_hint",
    "last_known_state",
    "last_modified_hint",
    "upload_id",
  ]);
  const serialized = JSON.stringify(stored);
  for (const forbidden of ["file_bytes", "payload_base64", "file_handle", "filesystem_path", "server_path", "storage_key", "traceback"]) {
    assert.equal(serialized.includes(forbidden), false);
  }

  h.context.__terminal = snapshot(UPLOAD_A, "failed", 8);
  h.run("applyUploadSnapshot(__terminal, currentUploadContext({ uploadId: uploadState.uploadId, file: uploadState.file }))");
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
});

test("Recovered upload is ready after source reselection and waits for explicit Resume before PATCH", async () => {
  const h = await createHarness();
  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "sample.gif",
    expected_size_bytes: 8,
    last_modified_hint: 1234,
    last_known_state: "receiving",
  }));
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 4)));

  await h.run("restoreUploadRecovery()");
  await h.flush();

  assert.equal(stateOf(h).snapshotState, "receiving");
  assert.equal(stateOf(h).received, 4);
  assert.equal(stateOf(h).stateLabel, "Reselect file to resume");
  assert.equal(stateOf(h).resumeDisabled, true);

  h.context.__file = makeFile("sample.gif", 8, 1234);
  h.run(`
    const input = document.querySelector("#upload-file-input");
    input.files = [__file];
    handleUploadFileSelection();
  `);
  await h.flush();

  const reselected = stateOf(h);
  assert.equal(reselected.received, 4);
  assert.equal(reselected.byteCount, "4 B / 8 B");
  assert.equal(reselected.stateLabel, "Ready to resume");
  assert.notEqual(reselected.stateLabel, "Uploading");
  assert.equal(reselected.visibleMessage, "Ready to resume.");
  assert.equal(reselected.visibleMessage.includes("Source file reselected"), false);
  assert.equal(reselected.pauseHidden, true);
  assert.equal(reselected.resumeHidden, false);
  assert.equal(reselected.resumeDisabled, false);
  assert.equal(reselected.cancelHidden, false);
  assert.equal(reselected.cancelLabel, "Cancel");
  assert.equal(h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`).length, 0);

  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 4)));
  enqueueStatus(h, UPLOAD_A, response(snapshot(UPLOAD_A, "receiving", 4)));
  enqueue(h, "PATCH", `/api/uploads/${UPLOAD_A}`, response(snapshot(UPLOAD_A, "receiving", 8)));

  await h.run("handleResumeUpload()");
  await h.flush();

  const patchCalls = h.fetchController.matching("PATCH", `/api/uploads/${UPLOAD_A}`);
  assert.equal(patchCalls.length, 1);
  assert.equal(patchCalls[0].options.headers["Upload-Offset"], "4");
  assert.equal(patchCalls[0].options.body.start, 4);
  assert.equal(patchCalls[0].options.body.end, 8);
});


test("ordinary cataloged completion awaits review and does not track automatic analysis", async () => {
  const h = await createHarness();
  h.run(`
    identityState.resolved = true;
    identityState.available = true;
    identityState.capabilities = new Set(["gallery.read", "upload.submit"]);
    automaticAnalysisCapability.automatic_analysis_enabled = true;
  `);
  setActiveUpload(h, { id: UPLOAD_A, state: "published", received: 8 });
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  h.run("scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))");
  enqueueStatus(h, UPLOAD_A, response({
    ...snapshot(UPLOAD_A, "cataloged", 8),
    media_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
  }));
  h.runNextTimer();
  await h.flush();
  const state = stateOf(h);
  assert.match(state.visibleMessage, /awaits administrator review/i);
  assert.match(state.visibleMessage, /not public/i);
  assert.equal(state.visibleMessage.includes("Available in Gallery"), false);
  assert.equal(h.fetchController.matchingPrefix("GET", "/api/ai/").length, 0);
  assert.equal(h.run("metadataWorkspace.openMediaId"), null);
});

test("ordinary identity never shows duplicate-found UI", async () => {
  const h = await createHarness();
  h.run(`
    identityState.resolved = true;
    identityState.available = true;
    identityState.capabilities = new Set(["gallery.read", "upload.submit"]);
  `);
  setActiveUpload(h, { state: "duplicate_pending", received: 8 });
  h.run("renderUploadCockpit()");
  const state = stateOf(h);
  assert.equal(state.stateLabel, "Validating");
  assert.equal(state.visibleMessage.includes("Exact duplicate found"), false);
  assert.equal(state.duplicateKeepHidden, true);
  assert.equal(state.duplicateDiscardHidden, true);
});

test("active poll 404 clears stale recovery without server cancel and preserves transient network failure", async () => {
  const h = await createHarness();
  setActiveUpload(h, { id: UPLOAD_A, state: "publish_pending", received: 8 });
  h.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  h.run("scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))");
  enqueueStatus(h, UPLOAD_A, response({ error: { code: "UPLOAD_SESSION_NOT_FOUND" } }, 404));
  h.runNextTimer();
  await h.flush();
  let state = stateOf(h);
  assert.equal(state.uploadId, null);
  assert.equal(state.snapshotState, null);
  assert.equal(state.recovery, null);
  assert.match(state.message, /expired or is unavailable/i);
  assert.equal(h.fetchController.matching("DELETE", `/api/uploads/${UPLOAD_A}`).length, 0);

  const network = await createHarness();
  setActiveUpload(network, { id: UPLOAD_A, state: "publish_pending", received: 8 });
  network.run('document.querySelector("#upload-dialog").setAttribute("open", "")');
  network.run("scheduleUploadPolling(currentUploadContext({ uploadId: uploadState.uploadId }))");
  enqueueStatus(network, UPLOAD_A, Promise.reject(new Error("temporary network failure")));
  network.runNextTimer();
  await network.flush();
  state = stateOf(network);
  assert.equal(state.uploadId, UPLOAD_A);
  assert.notEqual(state.recovery, null);
  assert.equal(state.hasPollOwner, true);
  assert.match(state.message, /temporarily unavailable/i);
});

test("Class 4: upload recovery reads the retired key only when the current key is absent", async () => {
  const h = await createHarness();
  const record = (state) =>
    JSON.stringify({
      upload_id: UPLOAD_A,
      file_name_hint: "sample.gif",
      expected_size_bytes: 8,
      last_modified_hint: 1,
      last_known_state: state,
    });

  h.context.localStorage.setItem(RECOVERY_KEY, record("receiving"));
  assert.equal(h.run("loadUploadRecovery().upload_id"), UPLOAD_A, "retired only");

  h.context.localStorage.setItem(RECOVERY_KEY_CURRENT, record("publish_pending"));
  assert.equal(
    h.run("loadUploadRecovery().last_known_state"),
    "publish_pending",
    "both present: the current key wins"
  );

  h.context.localStorage.setItem(RECOVERY_KEY, record("receiving"));
  assert.equal(
    h.run("loadUploadRecovery().last_known_state"),
    "publish_pending",
    "the current key still wins"
  );
});

test("Class 4: a rejected recovery removes both spellings so the fallback cannot resurrect it", async () => {
  const h = await createHarness();
  h.context.localStorage.setItem(RECOVERY_KEY, "{bad json");
  h.context.localStorage.setItem(RECOVERY_KEY_CURRENT, "{bad json");
  assert.equal(h.run("loadUploadRecovery()"), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY_CURRENT), null);

  h.context.localStorage.setItem(RECOVERY_KEY, JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "sample.gif",
    expected_size_bytes: 8,
    last_known_state: "published",
  }));
  assert.equal(h.run("loadUploadRecovery()"), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY), null);
  assert.equal(h.context.localStorage.getItem(RECOVERY_KEY_CURRENT), null);
});

test("Class 4: upload recovery writes the current key and leaves the retired key untouched", async () => {
  const h = await createHarness();
  const seeded = JSON.stringify({
    upload_id: UPLOAD_A,
    file_name_hint: "seeded.gif",
    expected_size_bytes: 8,
    last_modified_hint: 1,
    last_known_state: "receiving",
  });
  const persist = (id, received) => {
    h.context.__snapshot = snapshot(id, "receiving", received, 8, "written.gif");
    h.run(`
      uploadState.uploadId = __snapshot.id;
      uploadState.fileNameHint = __snapshot.display_filename;
      uploadState.expectedSizeBytes = __snapshot.declared_size_bytes;
      saveUploadRecovery(__snapshot);
    `);
  };

  h.context.localStorage.setItem(RECOVERY_KEY, seeded);
  persist(UPLOAD_B, 2);
  assert.equal(
    JSON.parse(h.context.localStorage.getItem(RECOVERY_KEY_CURRENT)).upload_id,
    UPLOAD_B,
    "a write when only the retired key existed creates the current key"
  );
  assert.equal(
    h.context.localStorage.getItem(RECOVERY_KEY),
    seeded,
    "and does not modify the retired key"
  );

  h.context.localStorage.setItem(RECOVERY_KEY, "{stale retired copy}");
  persist(UPLOAD_B, 4);
  assert.equal(
    h.context.localStorage.getItem(RECOVERY_KEY),
    "{stale retired copy}",
    "a write when both keys exist still does not touch the retired key"
  );
  assert.equal(
    JSON.parse(h.context.localStorage.getItem(RECOVERY_KEY_CURRENT)).upload_id,
    UPLOAD_B
  );
});
