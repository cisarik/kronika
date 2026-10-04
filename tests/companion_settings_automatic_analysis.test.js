const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const REPO = path.resolve(__dirname, "..");
const companion = require(path.join(REPO, "extension/shared/messages.js"));
const workerSource = fs.readFileSync(
  path.join(REPO, "extension/background/service_worker.js"),
  "utf8"
);
const sidebarSource = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
const sidebarHtml = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.html"), "utf8");
const sidebarCss = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.css"), "utf8");

const ORIGIN = "https://nuc-1.example.ts.net";
const CONFIRM_COPY =
  "Turn on automatic media analysis? Newly captured administrator-owned X media will automatically send preview frames to the configured server-side AI provider and incur usage cost. YouTube and ordinary identities stay excluded.";

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: { get() { return null; } },
  };
}

async function flush() {
  for (let index = 0; index < 12; index += 1) {
    await Promise.resolve();
  }
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

function loadWorker(options) {
  const state = {
    origin: (options && options.origin) || ORIGIN,
    fetchCalls: [],
    identityBody:
      (options && options.identityBody) || {
        capabilities: ["provider.operate", "media.workflow.read"],
      },
    capabilityStatus: (options && options.capabilityStatus) || 200,
    capabilityBody:
      (options && options.capabilityBody) || { automatic_analysis_enabled: false },
    putStatus: (options && options.putStatus) || 200,
    putBody:
      (options && options.putBody) || { automatic_media_analysis_enabled: true },
  };
  async function fetchImpl(url, init) {
    state.fetchCalls.push({ url: String(url), init: init || {} });
    const href = String(url);
    if (href.indexOf("/api/identity/me") !== -1) {
      return jsonResponse(200, state.identityBody);
    }
    if (href.indexOf("/api/ai/automatic-analysis-capability") !== -1) {
      return jsonResponse(state.capabilityStatus, state.capabilityBody);
    }
    if (href.indexOf("/api/admin/settings/automatic-analysis") !== -1) {
      return jsonResponse(state.putStatus, state.putBody);
    }
    return jsonResponse(404, {});
  }
  const chrome = {
    runtime: {
      getURL(rel) {
        return rel;
      },
      lastError: null,
      onInstalled: { addListener() {} },
      onStartup: { addListener() {} },
      onMessage: { addListener() {} },
      onConnect: { addListener() {} },
    },
    sidePanel: {
      setPanelBehavior() {
        return Promise.resolve();
      },
    },
    storage: {
      local: {
        get() {
          return Promise.resolve({ frameNestOrigin: state.origin });
        },
        set() {
          return Promise.resolve();
        },
        remove() {
          return Promise.resolve();
        },
      },
    },
    permissions: {
      request() {
        return Promise.resolve(true);
      },
      remove() {
        return Promise.resolve(true);
      },
    },
    alarms: {
      create() {
        return Promise.resolve();
      },
      clear() {
        return Promise.resolve(true);
      },
      onAlarm: { addListener() {} },
    },
    action: {
      setBadgeText() {
        return Promise.resolve();
      },
    },
  };
  const context = {
    chrome,
    FrameNestCompanion: companion,
    fetch: fetchImpl,
    importScripts() {},
    setTimeout,
    clearTimeout,
    AbortController,
    URL,
    URLSearchParams,
    JSON,
    Date,
    Math,
    Number,
    String,
    Boolean,
    Array,
    Object,
    Promise,
    console,
  };
  context.self = context;
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(workerSource.replace(/importScripts\([^)]+\);\s*/, ""), context);
  return { context, state };
}

function makeEl(id, extras) {
  const extra = extras || {};
  const listeners = {};
  const node = {
    id,
    hidden: extra.hidden === true,
    disabled: extra.disabled === true,
    checked: extra.checked === true,
    value: extra.value || "",
    textContent: "",
    open: false,
    attrs: {},
    listeners,
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    dispatch(type, event) {
      const payload = event || { target: node, key: "" };
      (listeners[type] || []).forEach((fn) => fn(payload));
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    },
    setAttribute(name, value) {
      this.attrs[name] = String(value);
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
    querySelectorAll() {
      return [];
    },
    focus() {},
    show() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
  };
  return node;
}

function loadSidebar(options) {
  const opts = options || {};
  const nodes = {
    origin: makeEl("origin", { value: opts.origin || "" }),
    "shell-status": makeEl("shell-status"),
    frame: makeEl("frame", { hidden: true }),
    "chrome-action": makeEl("chrome-action"),
    "settings-dialog": makeEl("settings-dialog"),
    "settings-open": makeEl("settings-open"),
    "settings-close": makeEl("settings-close"),
    "settings-save": makeEl("settings-save", { disabled: true }),
    "admin-settings": makeEl("admin-settings", { hidden: true }),
    "automatic-analysis-enabled": makeEl("automatic-analysis-enabled", { disabled: true }),
    "automatic-analysis-error": makeEl("automatic-analysis-error", { hidden: true }),
    "automatic-analysis-confirm": makeEl("automatic-analysis-confirm", { hidden: true }),
    "automatic-analysis-confirm-ok": makeEl("automatic-analysis-confirm-ok"),
    "automatic-analysis-confirm-cancel": makeEl("automatic-analysis-confirm-cancel"),
    "review-history-toggle": makeEl("review-history-toggle"),
    "review-history": makeEl("review-history", { hidden: true }),
    "review-history-list": makeEl("review-history-list"),
    "review-history-all": makeEl("review-history-all"),
    "review-history-expanded": makeEl("review-history-expanded", { hidden: true }),
    "review-dialog": makeEl("review-dialog"),
    "review-frame": makeEl("review-frame"),
  };
  const state = {
    messages: [],
    identity: opts.identity || { ok: false, error: "not_configured" },
    capability: opts.capability || { ok: false, error: "not_configured" },
    put: opts.put || { ok: true, body: { automatic_media_analysis_enabled: true } },
    putImpl: opts.putImpl || null,
  };
  const runtime = {
    id: "sidebar-test",
    lastError: null,
    getURL(rel) {
      return "chrome-extension://abc/" + rel;
    },
    sendMessage(message, callback) {
      state.messages.push(message);
      const type = message && message.type;
      Promise.resolve().then(() => {
        if (type === companion.TYPES.IDENTITY) {
          callback(state.identity);
          return;
        }
        if (type === companion.TYPES.AUTOMATIC_ANALYSIS_CAPABILITY) {
          callback(state.capability);
          return;
        }
        if (type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS) {
          if (typeof state.putImpl === "function") {
            callback(state.putImpl(message.payload || {}));
            return;
          }
          callback(state.put);
          return;
        }
        if (type === companion.TYPES.REVIEW_INBOX) {
          callback({ ok: true, items: [], unopened_count: 0, history_source: "review-inbox" });
          return;
        }
        callback({ ok: true });
      });
    },
  };
  const chrome = {
    runtime,
    storage: {
      local: {
        get(keys, callback) {
          const stored = { frameNestOrigin: opts.origin || "" };
          if (typeof callback === "function") {
            callback(stored);
          }
          return Promise.resolve(stored);
        },
        set() {
          return Promise.resolve();
        },
        remove() {
          return Promise.resolve();
        },
      },
    },
  };
  const document = {
    hidden: false,
    getElementById(id) {
      return nodes[id] || null;
    },
    addEventListener() {},
  };
  const context = {
    FrameNestCompanion: companion,
    document,
    chrome,
    window: {
      addEventListener() {},
    },
    Object,
    Boolean,
    String,
    Number,
    Array,
    Date,
    Promise,
    setTimeout,
    clearTimeout,
    setInterval() {
      return 0;
    },
    clearInterval() {},
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(sidebarSource, context);
  return { nodes, state };
}

test("settings HTML keeps Administration below origin controls and hidden by default", () => {
  const originAt = sidebarHtml.indexOf('id="origin"');
  const saveAt = sidebarHtml.indexOf('id="settings-save"');
  const adminAt = sidebarHtml.indexOf('id="admin-settings"');
  const checkboxAt = sidebarHtml.indexOf('id="automatic-analysis-enabled"');
  assert.ok(originAt >= 0 && saveAt > originAt && adminAt > saveAt);
  assert.ok(checkboxAt > adminAt);
  assert.match(sidebarHtml, /id="admin-settings"[^>]*hidden/);
  assert.match(sidebarHtml, />\s*Administration\s*</);
  assert.match(sidebarHtml, />\s*Automatic media analysis\s*/);
  assert.match(sidebarHtml, new RegExp(CONFIRM_COPY.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")));
  assert.match(sidebarCss, /\.settings-dialog__admin/);
  assert.doesNotMatch(sidebarSource, /window\.confirm/);
  assert.doesNotMatch(sidebarSource, /\bfetch\s*\(/);
  assert.match(sidebarSource, /TYPES\.AUTOMATIC_ANALYSIS_CAPABILITY/);
  assert.match(sidebarSource, /TYPES\.AUTOMATIC_ANALYSIS_SETTINGS/);
  assert.match(sidebarSource, /hasProviderOperateCapability/);
  assert.match(extractNamedFunction(sidebarSource, "refreshAdministration"), /IDENTITY/);
});

test("pathFor and identity helper expose the settings routes", () => {
  assert.equal(companion.TYPES.AUTOMATIC_ANALYSIS_CAPABILITY, "automatic_analysis_capability");
  assert.equal(companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS, "automatic_analysis_settings");
  assert.equal(
    companion.pathFor("automaticAnalysisCapability"),
    "/api/ai/automatic-analysis-capability"
  );
  assert.equal(
    companion.pathFor("automaticAnalysisSettings"),
    "/api/admin/settings/automatic-analysis"
  );
  assert.equal(companion.hasProviderOperateCapability({ capabilities: ["provider.operate"] }), true);
  assert.equal(companion.hasProviderOperateCapability({ capabilities: ["x.request"] }), false);
  assert.equal(companion.hasProviderOperateCapability({}), false);
});

test("service worker PUT enable requires confirm and sends the mutation header", async () => {
  const worker = loadWorker();
  const denied = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS,
    payload: { automatic_media_analysis_enabled: true },
  });
  assert.equal(denied.ok, false);
  assert.equal(denied.error, "confirm_required");
  assert.equal(worker.state.fetchCalls.length, 0);
  const enabled = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS,
    payload: { automatic_media_analysis_enabled: true, confirm_cloud_upload: true },
  });
  assert.equal(enabled.ok, true);
  const putCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.equal(putCall.url, ORIGIN + "/api/admin/settings/automatic-analysis");
  assert.equal(putCall.init.method, "PUT");
  assert.equal(putCall.init.headers["X-FrameNest-Request"], "1");
  assert.deepEqual(JSON.parse(putCall.init.body), {
    automatic_media_analysis_enabled: true,
    confirm_cloud_upload: true,
  });
  const disabled = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS,
    payload: { automatic_media_analysis_enabled: false },
  });
  assert.equal(disabled.ok, true);
  const offCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.deepEqual(JSON.parse(offCall.init.body), {
    automatic_media_analysis_enabled: false,
  });
  assert.equal(worker.context.capabilitiesFromBody({ capabilities: ["provider.operate"] }).providerOperate, true);
});

test("Administration is hidden when disconnected or ordinary", async () => {
  const disconnected = loadSidebar({ origin: "" });
  disconnected.nodes["settings-open"].dispatch("click");
  await flush();
  assert.equal(disconnected.nodes["admin-settings"].hidden, true);
  assert.equal(
    disconnected.state.messages.some((message) => message.type === companion.TYPES.IDENTITY),
    false
  );

  const ordinary = loadSidebar({
    origin: ORIGIN,
    identity: { ok: true, body: { capabilities: ["x.request"] } },
    capability: { ok: true, body: { automatic_analysis_enabled: false } },
  });
  ordinary.nodes["settings-open"].dispatch("click");
  await flush();
  assert.equal(ordinary.nodes["admin-settings"].hidden, true);
  assert.equal(
    ordinary.state.messages.some(
      (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_CAPABILITY
    ),
    false
  );
  assert.equal(
    ordinary.state.messages.some(
      (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS
    ),
    false
  );
});

test("admin confirm dismiss does not PUT; confirm enable and disable do", async () => {
  const harness = loadSidebar({
    origin: ORIGIN,
    identity: { ok: true, body: { capabilities: ["provider.operate"] } },
    capability: { ok: true, body: { automatic_analysis_enabled: false } },
    put: { ok: true, body: { automatic_media_analysis_enabled: true } },
  });
  harness.nodes["settings-open"].dispatch("click");
  await flush();
  assert.equal(harness.nodes["admin-settings"].hidden, false);
  assert.equal(harness.nodes["automatic-analysis-enabled"].checked, false);
  assert.equal(harness.nodes["automatic-analysis-enabled"].disabled, false);

  harness.nodes["automatic-analysis-enabled"].checked = true;
  harness.nodes["automatic-analysis-enabled"].dispatch("change");
  await flush();
  assert.equal(harness.nodes["automatic-analysis-confirm"].hidden, false);
  assert.equal(
    harness.state.messages.some(
      (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS
    ),
    false
  );
  harness.nodes["automatic-analysis-confirm-cancel"].dispatch("click");
  await flush();
  assert.equal(harness.nodes["automatic-analysis-enabled"].checked, false);
  assert.equal(harness.nodes["automatic-analysis-confirm"].hidden, true);
  assert.equal(
    harness.state.messages.some(
      (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS
    ),
    false
  );

  harness.nodes["automatic-analysis-enabled"].checked = true;
  harness.nodes["automatic-analysis-enabled"].dispatch("change");
  harness.nodes["automatic-analysis-confirm-ok"].dispatch("click");
  await flush();
  const enable = harness.state.messages.filter(
    (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS
  );
  assert.equal(enable.length, 1);
  assert.equal(enable[0].payload.automatic_media_analysis_enabled, true);
  assert.equal(enable[0].payload.confirm_cloud_upload, true);
  assert.equal(harness.nodes["automatic-analysis-enabled"].checked, true);

  harness.state.put = { ok: true, body: { automatic_media_analysis_enabled: false } };
  harness.nodes["automatic-analysis-enabled"].checked = false;
  harness.nodes["automatic-analysis-enabled"].dispatch("change");
  await flush();
  const disable = harness.state.messages.filter(
    (message) => message.type === companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS
  );
  assert.equal(disable.length, 2);
  assert.equal(disable[1].payload.automatic_media_analysis_enabled, false);
  assert.equal(disable[1].payload.confirm_cloud_upload, undefined);
});

test("settings PUT error shows a message and reverts the checkbox", async () => {
  const harness = loadSidebar({
    origin: ORIGIN,
    identity: { ok: true, body: { capabilities: ["provider.operate"] } },
    capability: { ok: true, body: { automatic_analysis_enabled: false } },
    put: { ok: false, error: "network_failed", status: 0 },
  });
  harness.nodes["settings-open"].dispatch("click");
  await flush();
  harness.nodes["automatic-analysis-enabled"].checked = true;
  harness.nodes["automatic-analysis-enabled"].dispatch("change");
  harness.nodes["automatic-analysis-confirm-ok"].dispatch("click");
  await flush();
  assert.equal(harness.nodes["automatic-analysis-enabled"].checked, false);
  assert.equal(harness.nodes["automatic-analysis-error"].hidden, false);
  assert.match(harness.nodes["automatic-analysis-error"].textContent, /Could not reach Kronika/);
});
