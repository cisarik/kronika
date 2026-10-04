const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const REPO = path.resolve(__dirname, "..");
const companion = require(path.join(REPO, "extension/shared/messages.js"));
const messagesSource = fs.readFileSync(
  path.join(REPO, "extension/shared/messages.js"),
  "utf8"
);
const workerSource = fs.readFileSync(
  path.join(REPO, "extension/background/service_worker.js"),
  "utf8"
);
const sidebarSource = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
const saveJsSource = fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8");
const pickerJsSource = fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8");
const sidebarHtml = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.html"), "utf8");
const sidebarCss = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.css"), "utf8");
const reviewSource = fs.readFileSync(path.join(REPO, "extension/ui/review.js"), "utf8");
const reviewHtml = fs.readFileSync(path.join(REPO, "extension/ui/review.html"), "utf8");
const reviewCss = fs.readFileSync(path.join(REPO, "extension/ui/review.css"), "utf8");
const manifest = JSON.parse(
  fs.readFileSync(path.join(REPO, "extension/manifest.json"), "utf8")
);

const ORIGIN = "https://nuc-1.example.ts.net";
const MEDIA_A = "11111111-1111-4111-8111-111111111111";
const MEDIA_B = "22222222-2222-4222-8222-222222222222";
const RUN_NEW = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const RUN_OLD = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const CLAIM_ID = "cccccccc-cccc-4ccc-8ccc-cccccccccccc";
const ALARM = companion.REVIEW_INBOX.alarmName;

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

function jsonResponse(status, body) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
    headers: { get() { return null; } },
  };
}

function createChromeFake(options) {
  const storage = Object.assign({}, (options && options.storage) || {});
  const alarms = {};
  const state = {
    storage,
    alarms,
    badgeText: "",
    badgeWrites: [],
    fetchCalls: [],
    installed: [],
    startup: [],
    alarmListeners: [],
    messageListeners: [],
    connectedPorts: [],
    mediaBody:
      (options && options.mediaBody) || new Uint8Array([1, 2, 3, 4]),
    mediaStatus: (options && options.mediaStatus) || 200,
    mediaContentLength:
      options && options.mediaContentLength !== undefined
        ? options.mediaContentLength
        : 4,
    inboxStatus: (options && options.inboxStatus) || 200,
    companionMediaStatus: (options && options.companionMediaStatus) || 200,
    companionMediaBody: (options && options.companionMediaBody) || null,
    inboxBody:
      (options && options.inboxBody) || {
        items: [],
        unopened_count: 0,
        next_cursor: null,
      },
    inboxResponses: (options && options.inboxResponses) || null,
    inboxResponseIndex: 0,
    identityStatus: (options && options.identityStatus) || 200,
    identityBody:
      (options && options.identityBody) || {
        capabilities: ["media.workflow.read", "x.request"],
      },
    claimBody: (options && options.claimBody) || { state: "completed", assets: [] },
    detailStatus: (options && options.detailStatus) || 200,
    detailBody: (options && options.detailBody) || {},
    openedStatus: (options && options.openedStatus) || 200,
    openedBody: (options && options.openedBody) || { unopened: false },
    applyStatus: (options && options.applyStatus) || 200,
    applyBody:
      (options && options.applyBody) || {
        publication: { status: "not_ready", state: "unpublished", ready: false },
      },
  };

  function pickStorage(keys) {
    if (typeof keys === "string") {
      const out = {};
      out[keys] = storage[keys];
      return out;
    }
    if (Array.isArray(keys)) {
      const out = {};
      keys.forEach((key) => {
        out[key] = storage[key];
      });
      return out;
    }
    if (keys && typeof keys === "object") {
      const out = {};
      Object.keys(keys).forEach((key) => {
        out[key] = Object.prototype.hasOwnProperty.call(storage, key) ? storage[key] : keys[key];
      });
      return out;
    }
    return Object.assign({}, storage);
  }

  const chrome = {
    runtime: {
      getURL(rel) {
        return rel;
      },
      lastError: null,
      onInstalled: {
        addListener(fn) {
          state.installed.push(fn);
        },
      },
      onStartup: {
        addListener(fn) {
          state.startup.push(fn);
        },
      },
      onMessage: {
        addListener(fn) {
          state.messageListeners.push(fn);
        },
      },
      onConnect: {
        addListener() {},
      },
    },
    tabs: {
      connect(_tabId, info) {
        const port = {
          name: (info && info.name) || "",
          posted: [],
          disconnected: false,
          onMessage: { addListener() {}, removeListener() {} },
          onDisconnect: { addListener() {} },
          postMessage(message) {
            port.posted.push(message);
          },
          disconnect() {
            port.disconnected = true;
          },
        };
        state.connectedPorts.push(port);
        return port;
      },
      sendMessage() {
        return Promise.resolve({ ok: true });
      },
    },
    sidePanel: {
      setPanelBehavior() {
        return Promise.resolve();
      },
    },
    storage: {
      local: {
        get(keys) {
          return Promise.resolve(pickStorage(keys));
        },
        set(values) {
          Object.assign(storage, values);
          return Promise.resolve();
        },
        remove(keys) {
          (Array.isArray(keys) ? keys : [keys]).forEach((key) => {
            delete storage[key];
          });
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
      contains() {
        return Promise.resolve(false);
      },
    },
    alarms: {
      create(name, info) {
        alarms[name] = Object.assign({ name: name }, info || {});
        return Promise.resolve();
      },
      clear(name) {
        const existed = Object.prototype.hasOwnProperty.call(alarms, name);
        delete alarms[name];
        return Promise.resolve(existed);
      },
      onAlarm: {
        addListener(fn) {
          state.alarmListeners.push(fn);
        },
      },
    },
    action: {
      setBadgeText(detail) {
        const text = detail && typeof detail.text === "string" ? detail.text : "";
        state.badgeText = text;
        state.badgeWrites.push(text);
        return Promise.resolve();
      },
    },
  };

  function mediaResponse() {
    const status = state.mediaStatus;
    const body = state.mediaBody;
    return {
      ok: status >= 200 && status < 300,
      status,
      arrayBuffer: async () => body.buffer.slice(body.byteOffset, body.byteOffset + body.byteLength),
      body: {
        getReader() {
          let sent = false;
          return {
            read() {
              if (sent) {
                return Promise.resolve({ done: true, value: undefined });
              }
              sent = true;
              return Promise.resolve({ done: false, value: body });
            },
            cancel() {},
          };
        },
      },
      headers: {
        get(name) {
          if (String(name).toLowerCase() === "content-length") {
            return state.mediaContentLength === null ? null : String(state.mediaContentLength);
          }
          if (String(name).toLowerCase() === "content-type") {
            return "application/octet-stream";
          }
          return null;
        },
      },
    };
  }

  async function fetchImpl(url, init) {
    state.fetchCalls.push({ url: String(url), init: init || {} });
    const href = String(url);
    if (/\/api\/media\/[^/]+\/locations\/[^/]+\/(content|gallery-preview)(?:\?|$)/.test(href)) {
      return mediaResponse();
    }
    if (href.indexOf("/api/x/companion/media") !== -1) {
      return jsonResponse(
        state.companionMediaStatus,
        state.companionMediaBody || { companion_api_version: companion.API_VERSION, items: [] }
      );
    }
    if (href.indexOf("/api/identity/me") !== -1) {
      return jsonResponse(state.identityStatus, state.identityBody);
    }
    if (href.indexOf("/api/x/requests/") !== -1) {
      return jsonResponse(200, state.claimBody);
    }
    if (/\/api\/companion\/review-inbox\/[^/?]+\/apply(?:\?|$)/.test(href)) {
      return jsonResponse(state.applyStatus, state.applyBody);
    }
    if (/\/api\/companion\/review-inbox\/[^/?]+\/opened(?:\?|$)/.test(href)) {
      return jsonResponse(state.openedStatus, state.openedBody);
    }
    if (/\/api\/companion\/review-inbox\/[^/?]+(?:\?|$)/.test(href)) {
      return jsonResponse(state.detailStatus, state.detailBody);
    }
    if (href.indexOf("/api/companion/own-history") !== -1) {
      if (Array.isArray(state.inboxResponses)) {
        const response = state.inboxResponses[state.inboxResponseIndex];
        state.inboxResponseIndex += 1;
        return jsonResponse(response.status, response.body);
      }
      return jsonResponse(state.inboxStatus, state.inboxBody);
    }
    if (href.indexOf("/api/companion/review-inbox") !== -1) {
      if (Array.isArray(state.inboxResponses)) {
        const response = state.inboxResponses[state.inboxResponseIndex];
        state.inboxResponseIndex += 1;
        return jsonResponse(response.status, response.body);
      }
      return jsonResponse(state.inboxStatus, state.inboxBody);
    }
    return jsonResponse(404, {});
  }

  return { chrome, state, fetchImpl };
}

function loadWorker(options) {
  const fake = createChromeFake(options);
  const context = {
    chrome: fake.chrome,
    FrameNestCompanion: companion,
    fetch: fake.fetchImpl,
    importScripts() {},
    setTimeout,
    clearTimeout,
    AbortController,
    btoa: global.btoa || ((value) => Buffer.from(value, "binary").toString("base64")),
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
  const source = workerSource.replace(/importScripts\([^)]+\);\s*/, "");
  vm.runInContext(source, context);
  return { context, state: fake.state, chrome: fake.chrome };
}

function loadReviewInbox(options) {
  const runtime =
    (options && options.runtime) ||
    {
      id: "sidebar-test",
      lastError: null,
      getURL(rel) {
        return "chrome-extension://abc/" + rel;
      },
      sendMessage(_message, callback) {
        callback({ ok: true });
      },
    };
  const context = {
    FrameNestCompanion: companion,
    document: {
      getElementById() {
        return null;
      },
    },
    chrome: { runtime },
    window: {},
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
  return context.FrameNestReviewInbox;
}

function loadReviewOverlay(options) {
  const runtime =
    (options && options.runtime) ||
    {
      id: "review-test",
      lastError: null,
      getURL(rel) {
        return "chrome-extension://abc/" + rel;
      },
      sendMessage() {},
    };
  const context = {
    FrameNestCompanion: companion,
    document: {
      getElementById() {
        return null;
      },
    },
    chrome: { runtime },
    window: {},
    location: { hash: "", protocol: "chrome-extension:", origin: "chrome-extension://abc" },
    Object,
    Boolean,
    String,
    Number,
    Array,
    Date,
    Promise,
    URL,
    setTimeout,
    clearTimeout,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(reviewSource, context);
  return context.FrameNestReviewOverlay;
}

function fakeListNode() {
  const nodes = [];
  const list = {
    childNodes: nodes,
    get firstChild() {
      return nodes[0] || null;
    },
    removeChild(node) {
      const index = nodes.indexOf(node);
      if (index >= 0) {
        nodes.splice(index, 1);
      }
      return node;
    },
    appendChild(node) {
      nodes.push(node);
      return node;
    },
    ownerDocument: {
      createTextNode(value) {
        return { textContent: value == null ? "" : String(value) };
      },
      createElement(tag) {
        const childNodes = [];
        const attributes = {};
        const node = {
          tagName: String(tag).toUpperCase(),
          childNodes,
          attributes,
          dataset: {},
          setAttribute(name, value) {
            attributes[name] = String(value);
            if (name === "data-media-id") {
              this.dataset.mediaId = String(value);
            }
          },
          getAttribute(name) {
            return attributes[name] || null;
          },
          appendChild(child) {
            childNodes.push(child);
            return child;
          },
        };
        let text = "";
        Object.defineProperty(node, "textContent", {
          get() {
            if (childNodes.length) {
              return childNodes.map((child) => child.textContent || "").join("");
            }
            return text;
          },
          set(value) {
            text = value == null ? "" : String(value);
          },
        });
        return node;
      },
    },
  };
  return list;
}

function fakeReviewChromeNodes() {
  const attributes = { "aria-expanded": "false" };
  const allAttributes = {
    "aria-expanded": "false",
    "aria-label": "Show full companion history",
  };
  return {
    toggle: {
      disabled: false,
      setAttribute(name, value) {
        attributes[name] = String(value);
      },
      getAttribute(name) {
        return attributes[name] || null;
      },
    },
    history: { hidden: true },
    historyList: fakeListNode(),
    allButton: {
      hidden: true,
      disabled: false,
      textContent: "All",
      setAttribute(name, value) {
        allAttributes[name] = String(value);
      },
      getAttribute(name) {
        return allAttributes[name] || null;
      },
    },
    expandedList: fakeListNode(),
  };
}

test("manifest display name and side-panel wordmark carry one identical brand", () => {
  const wordmark = sidebarHtml.match(
    /class="title-bar__wordmark"[^>]*>([^<]*)</
  );
  assert.ok(wordmark, "the side panel must keep its wordmark element");
  const wordmarkText = wordmark[1].trim();
  assert.ok(wordmarkText.length > 0, "the wordmark must not be emptied");

  const brand = manifest.name.split(/\s+/)[0];
  assert.equal(
    wordmarkText,
    brand,
    "one-sided rename guard: the manifest display name and the side-panel " +
      "wordmark must carry the same brand word"
  );

  for (const [surface, text] of [
    ["manifest.name", manifest.name],
    ["manifest.description", manifest.description],
    ["manifest.action.default_title", manifest.action.default_title],
    ["sidebar wordmark", wordmarkText],
    ["sidebar <title>", (sidebarHtml.match(/<title>([^<]*)<\/title>/) || [])[1]],
    ["sidebar origin label", (sidebarHtml.match(/<label for="origin">([^<]*)</) || [])[1]],
    [
      "sidebar frame title",
      (sidebarHtml.match(/<iframe id="frame" title="([^"]*)"/) || [])[1],
    ],
    ["EXTENSION_CONTEXT_RECOVERY_COPY", companion.EXTENSION_CONTEXT_RECOVERY_COPY],
  ]) {
    assert.doesNotMatch(
      text || "",
      /FrameNest/i,
      `${surface} must not name the retired brand`
    );
  }

  for (const [surface, text] of [
    ["manifest.action.default_title", manifest.action.default_title],
    ["sidebar origin label", (sidebarHtml.match(/<label for="origin">([^<]*)</) || [])[1]],
    ["sidebar frame title", (sidebarHtml.match(/<iframe id="frame" title="([^"]*)"/) || [])[1]],
    ["EXTENSION_CONTEXT_RECOVERY_COPY", companion.EXTENSION_CONTEXT_RECOVERY_COPY],
  ]) {
    assert.ok(
      (text || "").includes(brand),
      `${surface} must name the same brand as the manifest display name`
    );
  }
});

test("every companion and served-prose display surface carries the manifest brand", () => {
  const brand = manifest.name.split(/\s+/)[0];

  // Every display carrier on the surfaces this cut owns, resolved by parsing the
  // artefact rather than by matching known literals. `jsLiterals` yields every
  // string literal; `htmlCarriers` yields every inter-element text node and
  // every quoted attribute value.
  function jsLiterals(relative) {
    const source = fs.readFileSync(path.join(REPO, relative), "utf8");
    const literals = [];
    const pattern = /(["'`])((?:\\.|(?!\1)[^\\\n])*)\1/g;
    let match;
    while ((match = pattern.exec(source)) !== null) {
      literals.push(match[2]);
    }
    return literals;
  }

  function htmlCarriers(relative) {
    const html = fs.readFileSync(path.join(REPO, relative), "utf8");
    const carriers = [];
    const attribute = /([A-Za-z_:][-A-Za-z0-9_:.]*)\s*=\s*(?:"([^"]*)"|'([^']*)')/g;
    let match;
    while ((match = attribute.exec(html)) !== null) {
      carriers.push({
        kind: `attr:${match[1]}`,
        text: match[2] !== undefined ? match[2] : match[3],
      });
    }
    const withoutComments = html.replace(/<!--[\s\S]*?-->/g, (match) => " ".repeat(match.length));
    const withoutScripts = withoutComments.replace(
      /<script\b[^>]*>[\s\S]*?<\/script>/gi,
      (match) => " ".repeat(match.length)
    );
    const withoutStyles = withoutScripts.replace(
      /<style\b[^>]*>[\s\S]*?<\/style>/gi,
      (match) => " ".repeat(match.length)
    );
    const tag = /<\/?[A-Za-z][^>]*>/g;
    let cursor = 0;
    while ((match = tag.exec(withoutStyles)) !== null) {
      const text = withoutStyles.slice(cursor, match.index).replace(/\s+/g, " ").trim();
      if (/[A-Za-z]/.test(text)) carriers.push({ kind: "text", text });
      cursor = match.index + match[0].length;
    }
    const tail = withoutStyles.slice(cursor).replace(/\s+/g, " ").trim();
    if (/[A-Za-z]/.test(tail)) carriers.push({ kind: "text", text: tail });
    return carriers;
  }

  // Machine-read spellings, not display text: the CSS and DOM hooks, the port
  // name, the localStorage and alarm keys, the protocol and API-version strings
  // and the retained mutation-header spelling. C7 owns the hooks, C4 the keys,
  // C3 the protocol strings and C7 the header; none of them is display text, so
  // they are excluded here by exact spelling rather than by file. The match is
  // deliberately case-sensitive: `frameNestOrigin` and `FrameNestCompanion`
  // style camel-case names never contain `FrameNest`, so excluding them here
  // would only hide a display string that regressed to the retired brand.
  const MACHINE_READ = /framenest|X-FrameNest/;

  const surfaces = [
    ["extension/shared/messages.js", "literal"],
    ["extension/ui/save.js", "literal"],
    ["extension/ui/picker.js", "literal"],
    ["extension/ui/sidebar.js", "literal"],
    ["extension/content/x_adapter.js", "literal"],
    ["src/kronika/adapters/api/web/app.js", "literal"],
    ["extension/ui/save.html", "carrier"],
    ["extension/ui/picker.html", "carrier"],
  ];

  let brandCarrying = 0;
  for (const [relative, kind] of surfaces) {
    const carriers =
      kind === "literal"
        ? jsLiterals(relative).map((text) => ({ kind: "literal", text }))
        : htmlCarriers(relative);
    const display = carriers.filter(
      (carrier) => /[A-Za-z]/.test(carrier.text) && !MACHINE_READ.test(carrier.text)
    );
    assert.ok(
      display.length > 0,
      `${relative} must still expose letter-bearing display text, or this guard is vacuous`
    );
    for (const carrier of display) {
      assert.doesNotMatch(
        carrier.text,
        /FrameNest/,
        `${relative} ${carrier.kind} must not name the retired brand: ${carrier.text}`
      );
    }
    brandCarrying += display.filter((carrier) => carrier.text.includes(brand)).length;
  }

  // The manifest name, the side-panel wordmark, the recovery copy, the origin
  // label and the connection status and aria strings are the surfaces a person
  // reads first. Each must name the brand the manifest names, so a one-sided
  // rename of any single one of them fails here.
  for (const [surface, text] of [
    ["manifest.name", manifest.name],
    ["sidebar wordmark", (sidebarHtml.match(/class="title-bar__wordmark"[^>]*>([^<]*)</) || [])[1]],
    ["EXTENSION_CONTEXT_RECOVERY_COPY", companion.EXTENSION_CONTEXT_RECOVERY_COPY],
    ["sidebar origin label", (sidebarHtml.match(/<label for="origin">([^<]*)</) || [])[1]],
    [
      "ui/save.js UPGRADE_MESSAGE",
      (saveJsSource.match(/const UPGRADE_MESSAGE = "([^"]*)"/) || [])[1],
    ],
    ["ui/picker.js disconnectedStatus", extractNamedFunction(pickerJsSource, "disconnectedStatus")],
  ]) {
    assert.ok(
      (text || "").includes(brand),
      `${surface} must name the same brand as the manifest display name`
    );
  }

  for (const name of [
    "framingFailureCopy",
    "companionHostMissingCopy",
    "automaticAnalysisErrorCopy",
    "syncChromeAction",
    "promptConnectInSettings",
    "connect",
  ]) {
    const body = extractNamedFunction(sidebarSource, name);
    assert.ok(
      body.includes(brand),
      `sidebar connection copy ${name}() must name the manifest brand`
    );
  }

  assert.ok(
    brandCarrying >= surfaces.length,
    `each owned surface must keep at least one brand-carrying display string, saw ${brandCarrying}`
  );
});

test("manifest adds alarms, keeps action, and does not add notifications or overlay WAR", () => {
  assert.deepEqual(manifest.permissions.sort(), ["alarms", "sidePanel", "storage"]);
  assert.equal((manifest.permissions || []).includes("alarms"), true);
  assert.equal((manifest.permissions || []).includes("notifications"), false);
  assert.equal((manifest.permissions || []).includes("tabs"), false);
  assert.equal("host_permissions" in manifest, false);
  assert.equal("externally_connectable" in manifest, false);
  assert.equal(typeof manifest.action, "object");
  assert.equal("default_popup" in manifest.action, false);
  assert.equal(manifest.action.default_title, "Kronika companion");
  const war = manifest.web_accessible_resources[0].resources.slice().sort();
  assert.deepEqual(war, [
    "ui/picker.css",
    "ui/picker.html",
    "ui/picker.js",
    "ui/save.css",
    "ui/save.html",
    "ui/save.js",
  ]);
  assert.equal(war.includes("ui/sidebar.html"), false);
  assert.equal(war.includes("ui/sidebar.js"), false);
  assert.equal(war.includes("ui/review.html"), false);
  assert.equal(war.includes("ui/review.js"), false);
  assert.equal(war.includes("ui/review.css"), false);
  assert.equal(fs.existsSync(path.join(REPO, "extension/ui/review.html")), true);
  assert.equal(fs.existsSync(path.join(REPO, "extension/ui/review.js")), true);
  assert.equal(fs.existsSync(path.join(REPO, "extension/ui/review.css")), true);
  assert.doesNotMatch(reviewSource, /innerHTML/);
  assert.doesNotMatch(reviewSource, /\bfetch\s*\(/);
  assert.doesNotMatch(reviewSource, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(reviewSource, /chrome\.storage/);
});

test("review overlay protocol uses UUID pathFor and dropUnknown rejects unknown types", () => {
  assert.equal(companion.TYPES.REVIEW_INBOX, "review_inbox");
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.REVIEW_INBOX,
    })
  );
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.REVIEW_INBOX_DETAIL,
    })
  );
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.REVIEW_INBOX_OPENED,
    })
  );
  assert.ok(
    companion.dropUnknown({
      v: companion.PROTOCOL,
      type: companion.TYPES.REVIEW_INBOX_APPLY,
    })
  );
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "review_apply" }), null);
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "mark_opened" }), null);
  assert.equal(companion.dropUnknown({ v: companion.PROTOCOL, type: "review_opened" }), null);
  assert.equal(companion.pathFor("reviewInbox"), "/api/companion/review-inbox");
  assert.equal(
    companion.pathFor("reviewInbox", { url: "https://evil.example", claimId: "../x" }),
    "/api/companion/review-inbox"
  );
  assert.equal(companion.pathFor("https://evil.example"), null);
  assert.equal(
    companion.pathFor("reviewInboxDetail", { mediaId: MEDIA_A }),
    "/api/companion/review-inbox/" + MEDIA_A
  );
  assert.equal(
    companion.pathFor("reviewInboxOpened", { mediaId: MEDIA_A }),
    "/api/companion/review-inbox/" + MEDIA_A + "/opened"
  );
  assert.equal(
    companion.pathFor("reviewInboxApply", { mediaId: MEDIA_A }),
    "/api/companion/review-inbox/" + MEDIA_A + "/apply"
  );
  assert.equal(companion.pathFor("reviewInboxDetail", { mediaId: "not-a-uuid" }), null);
  assert.equal(companion.pathFor("reviewInboxOpened", { mediaId: "../" + MEDIA_A }), null);
  assert.equal(
    companion.pathFor("reviewInboxApply", { mediaId: "https://evil.example/" + MEDIA_A }),
    null
  );
  assert.equal(
    companion.pathFor("reviewInboxDetail", { mediaId: MEDIA_A, url: "https://evil.example" }),
    "/api/companion/review-inbox/" + MEDIA_A
  );
  assert.equal(companion.reviewInboxQuerySuffix(1), "?limit=1");
  assert.doesNotMatch(workerSource, /fetch\(payload\.url\)/);
  assert.doesNotMatch(workerSource, /fetch\(message\.url\)/);
  assert.match(workerSource, /fetchJson\("identity"/);
  assert.match(workerSource, /\/api\/companion\/own-history/);
  assert.match(workerSource, /fetchJson\("reviewInboxDetail"/);
  assert.match(workerSource, /fetchJson\("reviewInboxOpened"/);
  assert.match(workerSource, /fetchJson\("reviewInboxApply"/);
  assert.doesNotMatch(workerSource, /setInterval/);
  assert.doesNotMatch(workerSource, /chrome\.notifications/);
  assert.doesNotMatch(extractNamedFunction(workerSource, "reviewInbox"), /method:\s*"POST"/);
});

test("shared extension-context classifier is exact and exposes one recovery copy", () => {
  const signature = companion.EXTENSION_CONTEXT_INVALIDATED_SIGNATURE;
  const copy = companion.EXTENSION_CONTEXT_RECOVERY_COPY;
  assert.equal(signature, "Extension context invalidated");
  assert.equal(copy, "Kronika was reloaded. Refresh X and reopen the side panel.");
  assert.equal(companion.isExtensionContextInvalidated(null), true);
  assert.equal(companion.isExtensionContextInvalidated({ id: "" }), true);
  assert.equal(
    companion.isExtensionContextInvalidated(
      { id: "abc" },
      new Error("Unchecked runtime.lastError: Extension context invalidated.")
    ),
    true
  );
  assert.equal(
    companion.isExtensionContextInvalidated(
      { id: "abc" },
      { message: "Extension context invalidated while sending" }
    ),
    true
  );
  assert.equal(
    companion.isExtensionContextInvalidated({ id: "abc" }, { message: "Receiving end does not exist" }),
    false
  );
  assert.equal(
    companion.isExtensionContextInvalidated({ id: "abc" }, "Extension context invalidated"),
    false
  );
});

test("badge text uses unopened_count bounds and never a title", () => {
  assert.equal(companion.badgeTextForUnopenedCount(1), "1");
  assert.equal(companion.badgeTextForUnopenedCount(99), "99");
  assert.equal(companion.badgeTextForUnopenedCount(100), "99+");
  assert.equal(companion.badgeTextForUnopenedCount(0), "");
  assert.equal(companion.badgeTextForUnopenedCount(-4), "");
  assert.equal(companion.badgeTextForUnopenedCount("12"), "");
  assert.equal(
    companion.unopenedCountFromBody({
      unopened_count: 2,
      items: [{ title: "Secret title" }, { title: "Other" }, { title: "Third" }],
    }),
    2
  );
  assert.notEqual(
    companion.badgeTextForUnopenedCount(
      companion.unopenedCountFromBody({
        unopened_count: 2,
        items: [1, 2, 3, 4, 5],
      })
    ),
    "5"
  );
});

test("named one-minute alarm is created on configure and cleared on reset", async () => {
  const worker = loadWorker({});
  const configured = await worker.context.configureOrigin({ origin: ORIGIN });
  assert.equal(configured.ok, true);
  assert.ok(worker.state.alarms[ALARM]);
  assert.equal(worker.state.alarms[ALARM].periodInMinutes, 1);
  assert.equal(worker.state.alarms[ALARM].name, ALARM);
  worker.state.inboxBody = { items: [], unopened_count: 7, next_cursor: null };
  await worker.context.refreshReviewInboxBadge();
  assert.equal(worker.state.badgeText, "7");
  const reset = await worker.context.resetState();
  assert.equal(reset.ok, true);
  assert.equal(worker.state.alarms[ALARM], undefined);
  assert.equal(worker.state.badgeText, "");
  assert.equal(worker.state.storage[companion.REVIEW_INBOX.awaitingKey], undefined);
});

test("alarm is ensured on install and startup when a valid origin is stored", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  assert.ok(worker.state.installed.length >= 1);
  assert.ok(worker.state.startup.length >= 1);
  Object.keys(worker.state.alarms).forEach((name) => {
    delete worker.state.alarms[name];
  });
  worker.state.installed.forEach((fn) => fn());
  await worker.context.ensureReviewInboxAlarm();
  assert.ok(worker.state.alarms[ALARM]);
  assert.equal(worker.state.alarms[ALARM].periodInMinutes, 1);
  delete worker.state.alarms[ALARM];
  worker.state.startup.forEach((fn) => fn());
  await worker.context.ensureReviewInboxAlarm();
  assert.ok(worker.state.alarms[ALARM]);
});

test("badge refresh uses unopened_count, limit=1, and clears on 0, 403, and failure", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  worker.state.inboxBody = {
    items: [
      {
        media_id: MEDIA_A,
        title: "Leaked title",
        analysis_run_id: RUN_NEW,
        completed_at_ms: 1,
        unopened: true,
      },
      {
        media_id: MEDIA_B,
        title: "Second",
        analysis_run_id: RUN_OLD,
        completed_at_ms: 0,
        unopened: true,
      },
    ],
    unopened_count: 1,
    next_cursor: null,
  };
  await worker.context.refreshReviewInboxBadge();
  const badgeCall = worker.state.fetchCalls.find((call) =>
    call.url.indexOf("/api/companion/review-inbox") !== -1
  );
  assert.ok(badgeCall);
  assert.equal(badgeCall.url, ORIGIN + "/api/companion/review-inbox?limit=1");
  assert.equal(badgeCall.init.method || "GET", "GET");
  assert.equal(badgeCall.init.headers["X-FrameNest-Request"], "1");
  assert.equal(worker.state.badgeText, "1");
  assert.equal(worker.state.badgeText.indexOf("Leaked"), -1);

  worker.state.inboxBody = { items: [{ title: "x" }], unopened_count: 0, next_cursor: null };
  await worker.context.refreshReviewInboxBadge();
  assert.equal(worker.state.badgeText, "");

  worker.state.inboxBody = { items: [], unopened_count: 140, next_cursor: null };
  await worker.context.refreshReviewInboxBadge();
  assert.equal(worker.state.badgeText, "99+");

  worker.state.inboxStatus = 403;
  worker.state.inboxBody = {
    items: [{ title: "Should not badge" }],
    unopened_count: 9,
  };
  await worker.context.refreshReviewInboxBadge();
  assert.equal(worker.state.badgeText, "");

  worker.state.inboxStatus = 500;
  await worker.context.refreshReviewInboxBadge();
  assert.equal(worker.state.badgeText, "");
});

test("ordinary badge refresh uses own-history limit=1 and does not call inbox", async () => {
  const worker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    identityBody: { capabilities: ["x.request"] },
  });
  worker.state.inboxBody = { items: [], unopened_count: 3, next_cursor: null };
  await worker.context.refreshReviewInboxBadge();
  const ownCall = worker.state.fetchCalls.find((call) =>
    call.url.indexOf("/api/companion/own-history") !== -1
  );
  assert.ok(ownCall);
  assert.equal(ownCall.url, ORIGIN + "/api/companion/own-history?limit=1");
  assert.equal(ownCall.init.method || "GET", "GET");
  assert.equal(worker.state.badgeText, "3");
  assert.equal(
    worker.state.fetchCalls.some((call) =>
      /\/api\/companion\/review-inbox(?:\?|$)/.test(call.url)
    ),
    false
  );
});

test("alarm handler refreshes the badge and ignores other alarm names", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  assert.equal(worker.state.alarmListeners.length, 1);
  worker.state.inboxBody = { items: [], unopened_count: 4, next_cursor: null };
  worker.state.badgeText = "stay";
  worker.state.alarmListeners[0]({ name: "other.alarm" });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(worker.state.badgeText, "stay");
  worker.state.alarmListeners[0]({ name: ALARM });
  await new Promise((resolve) => setTimeout(resolve, 20));
  assert.equal(worker.state.badgeText, "4");
});

test("REVIEW_INBOX hides titles on 403 and does not fetch caller URLs", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  worker.state.inboxBody = {
    items: [
      {
        media_id: MEDIA_A,
        title: "Admin title",
        created_at_ms: 5,
        analyzed: true,
        analysis_run_id: RUN_NEW,
        completed_at_ms: 10,
        unopened: true,
      },
    ],
    unopened_count: 3,
    next_cursor: null,
  };
  const listed = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.REVIEW_INBOX,
    payload: { url: "https://evil.example/steal" },
  });
  assert.equal(listed.ok, true);
  assert.equal(listed.forbidden, false);
  assert.equal(listed.unopened_count, 3);
  assert.equal(listed.items.length, 1);
  assert.equal(listed.items[0].title, "Admin title");
  const listCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.equal(listCall.url, ORIGIN + "/api/companion/review-inbox?limit=100");
  assert.equal(listCall.url.indexOf("evil.example"), -1);

  worker.state.inboxStatus = 403;
  worker.state.inboxBody = {
    items: [{ title: "Ordinary must not see this" }],
    unopened_count: 8,
  };
  const denied = await worker.context.reviewInbox();
  assert.equal(denied.ok, false);
  assert.equal(denied.forbidden, true);
  assert.equal(Array.isArray(denied.items), true);
  assert.equal(denied.items.length, 0);
  assert.equal(denied.unopened_count, 0);
  assert.equal(JSON.stringify(denied).indexOf("Ordinary must not see this"), -1);
  assert.equal(worker.state.badgeText, "");
});

test("REVIEW_INBOX aggregates encoded cursor pages in server order", async () => {
  const cursor = "next page +/&=";
  const worker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    inboxResponses: [
      {
        status: 200,
        body: {
          items: [
            {
              media_id: MEDIA_A,
              title: "Pending placeholder",
              created_at_ms: 20,
              analyzed: false,
              analysis_run_id: null,
              completed_at_ms: null,
              unopened: false,
            },
          ],
          unopened_count: 1,
          next_cursor: cursor,
        },
      },
      {
        status: 200,
        body: {
          items: [
            {
              media_id: MEDIA_A,
              title: "Analyzed replacement",
              created_at_ms: 20,
              analyzed: true,
              analysis_run_id: RUN_NEW,
              completed_at_ms: 21,
              unopened: true,
            },
            {
              media_id: MEDIA_B,
              title: "Older",
              created_at_ms: 5,
              analyzed: true,
              analysis_run_id: RUN_OLD,
              completed_at_ms: 10,
              unopened: false,
            },
          ],
          unopened_count: 1,
          next_cursor: null,
        },
      },
    ],
  });
  const result = await worker.context.reviewInbox();
  assert.equal(result.ok, true);
  assert.equal(result.unopened_count, 1);
  assert.equal(result.history_source, "review-inbox");
  assert.deepEqual(
    Array.from(result.items, (item) => item.title),
    ["Analyzed replacement", "Older"]
  );
  const listCalls = worker.state.fetchCalls.filter((call) =>
    call.url.indexOf("/api/companion/review-inbox") !== -1
  );
  assert.equal(listCalls.length, 2);
  const first = new URL(listCalls[0].url);
  const second = new URL(listCalls[1].url);
  assert.equal(first.searchParams.get("limit"), "100");
  assert.equal(first.searchParams.has("cursor"), false);
  assert.equal(second.searchParams.get("limit"), "100");
  assert.equal(second.searchParams.get("cursor"), cursor);
  assert.equal(worker.state.badgeText, "1");
});

test("REVIEW_INBOX rejects cursor cycles and later-page failures without partial titles", async () => {
  const firstPage = {
    items: [
      {
        media_id: MEDIA_A,
        title: "Must not escape",
        created_at_ms: 5,
        analyzed: true,
        analysis_run_id: RUN_NEW,
        completed_at_ms: 20,
        unopened: true,
      },
    ],
    unopened_count: 1,
    next_cursor: "repeat",
  };
  const cycleWorker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    inboxResponses: [
      { status: 200, body: firstPage },
      {
        status: 200,
        body: { items: [], unopened_count: 1, next_cursor: "repeat" },
      },
    ],
  });
  cycleWorker.state.badgeText = "9";
  const cycle = await cycleWorker.context.reviewInbox();
  assert.equal(cycle.ok, false);
  assert.equal(cycle.error, "cursor_cycle");
  assert.deepEqual(Array.from(cycle.items), []);
  assert.equal(JSON.stringify(cycle).includes("Must not escape"), false);
  const cycleListCalls = cycleWorker.state.fetchCalls.filter((call) =>
    call.url.indexOf("/api/companion/review-inbox") !== -1
  );
  assert.equal(cycleListCalls.length, 2);
  assert.equal(cycleWorker.state.badgeText, "");

  const failedWorker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    inboxResponses: [
      { status: 200, body: firstPage },
      { status: 500, body: { items: [{ title: "also private" }] } },
    ],
  });
  failedWorker.state.badgeText = "8";
  const failed = await failedWorker.context.reviewInbox();
  assert.equal(failed.ok, false);
  assert.equal(failed.status, 500);
  assert.deepEqual(Array.from(failed.items), []);
  assert.equal(JSON.stringify(failed).includes("Must not escape"), false);
  assert.equal(failedWorker.state.badgeText, "");
});

test("awaiting-analysis stores media UUIDs for 30 minutes and does not change badge math", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  const now = Date.now();
  worker.state.claimBody = {
    state: "completed",
    x_post_id: "123456789",
    assets: [
      { media_id: MEDIA_A, state: "cataloged", title: "must not persist" },
      { media_id: "not-a-uuid", state: "cataloged" },
    ],
  };
  const snapshot = await worker.context.pollClaim({ claimId: CLAIM_ID });
  assert.equal(snapshot.ok, true);
  const awaiting = worker.state.storage[companion.REVIEW_INBOX.awaitingKey];
  assert.equal(awaiting.length, 1);
  assert.equal(awaiting[0].media_id, MEDIA_A);
  assert.equal("title" in awaiting[0], false);
  assert.ok(awaiting[0].expires_at_ms >= now + companion.REVIEW_INBOX.awaitingMs - 50);
  assert.ok(awaiting[0].expires_at_ms <= now + companion.REVIEW_INBOX.awaitingMs + 2000);

  worker.state.inboxBody = {
    items: [
      {
        media_id: MEDIA_B,
        title: "Other item",
        created_at_ms: 1,
        analyzed: true,
        analysis_run_id: RUN_NEW,
        completed_at_ms: 1,
        unopened: true,
      },
    ],
    unopened_count: 0,
    next_cursor: null,
  };
  const stillHint = await worker.context.reviewInbox();
  assert.equal(stillHint.unopened_count, 0);
  assert.equal(worker.state.badgeText, "");
  assert.equal(stillHint.awaiting.length, 1);
  assert.equal(stillHint.awaiting[0].media_id, MEDIA_A);

  worker.state.inboxBody.items[0].media_id = MEDIA_A;
  const pruned = await worker.context.reviewInbox();
  assert.equal(pruned.awaiting.length, 0);
  assert.equal(worker.state.badgeText, "");

  const expired = companion.normalizeAwaitingRecords(
    [{ media_id: MEDIA_B, expires_at_ms: 1, title: "nope" }],
    Date.now()
  );
  assert.deepEqual(expired, []);
});

test("title-bar merged history has the accepted DOM, ARIA, and status contract", () => {
  const titleBarAt = sidebarHtml.indexOf('class="title-bar"');
  const titleBarEnd = sidebarHtml.indexOf("</div>", titleBarAt);
  const toggleAt = sidebarHtml.indexOf('id="review-history-toggle"');
  const wordmarkAt = sidebarHtml.indexOf('class="title-bar__wordmark"');
  const settingsAt = sidebarHtml.indexOf('id="settings-open"');
  const actionAt = sidebarHtml.indexOf('id="chrome-action"');
  const historyAt = sidebarHtml.indexOf('id="review-history"');
  const statusAt = sidebarHtml.indexOf('id="shell-status"');
  const frameAt = sidebarHtml.indexOf('id="frame"');
  assert.ok(titleBarAt >= 0 && toggleAt > titleBarAt && toggleAt < titleBarEnd);
  assert.ok(wordmarkAt > toggleAt && settingsAt > wordmarkAt && actionAt > settingsAt);
  assert.ok(historyAt > titleBarEnd && statusAt > historyAt && frameAt > statusAt);
  assert.match(
    sidebarHtml,
    /id="review-history-toggle"[\s\S]*?type="button"[\s\S]*?aria-label="Toggle companion history"[\s\S]*?aria-expanded="false"[\s\S]*?aria-controls="review-history"[\s\S]*?disabled/
  );
  assert.match(sidebarHtml, /id="review-history-list"[^>]+aria-label="Companion history"/);
  assert.match(sidebarHtml, /<ul id="review-history-list"/);
  assert.match(
    sidebarHtml,
    /id="review-history-all"[\s\S]*?aria-label="Show full companion history"[\s\S]*?>\s*All\s*</
  );
  assert.match(sidebarHtml, /id="review-history-expanded"[^>]+aria-label="Full companion history"/);
  assert.doesNotMatch(sidebarHtml, /<ol\b/);
  assert.doesNotMatch(sidebarHtml, /id="review-inbox(?:-list)?"/);
  assert.doesNotMatch(sidebarHtml, />\s*Review inbox\s*</);
  assert.doesNotMatch(sidebarHtml, /No analyzed items\./);
  assert.doesNotMatch(sidebarHtml, /Awaiting analysis/);
  assert.match(sidebarHtml, /id="review-dialog"/);
  assert.match(sidebarHtml, /id="review-frame"/);
  assert.match(sidebarSource, /ui\/review\.html/);
  assert.doesNotMatch(sidebarSource, /explicitCollapsedKey|seenRunIdKey/);
  assert.doesNotMatch(sidebarSource, /setText\(shellStatus,\s*"Connected"/);
  assert.match(sidebarSource, new RegExp(`Connect ${manifest.name.split(/\s+/)[0]} in Settings`));
  assert.match(sidebarSource, /setText\(shellStatus, "Cleared"\)/);
  assert.match(sidebarSource, /setText\(shellStatus, "Attached"\)/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "openReviewOverlay"), /clearFrame/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "closeReviewOverlay"), /clearFrame/);
  assert.match(sidebarCss, /\.title-bar__history-toggle\s*{[\s\S]*?position:\s*absolute;[\s\S]*?inset:\s*0;/);
  assert.match(sidebarCss, /\.title-bar__wordmark\s*{[\s\S]*?pointer-events:\s*none/);
  assert.match(sidebarCss, /\.title-bar__settings\s*{[\s\S]*?z-index:\s*2/);
  assert.match(sidebarCss, /\.title-bar__action\s*{[\s\S]*?z-index:\s*2/);
  assert.match(sidebarCss, /\.shell-status:empty\s*{[\s\S]*?display:\s*none/);
  assert.match(sidebarCss, /\.review-list:empty\s*{[\s\S]*?display:\s*none/);
  assert.match(sidebarCss, /list-style:\s*none/);
  assert.match(sidebarCss, /--accent:\s*#00ff41;/);
  assert.match(sidebarCss, /--accent-border:\s*rgba\(0,\s*255,\s*65,\s*0\.42\);/);
  assert.match(sidebarCss, /--accent-soft:\s*rgba\(0,\s*255,\s*65,\s*0\.12\);/);
  assert.doesNotMatch(sidebarCss, /--history-neon:/);
  assert.doesNotMatch(sidebarCss, /--history-green-1:/);
  assert.doesNotMatch(sidebarCss, /--chrome-green:/);
  const titleBarChrome = sidebarCss.match(/\.title-bar\s*\{[\s\S]*?\n\}/);
  assert.ok(titleBarChrome, "title-bar fill rule");
  assert.match(titleBarChrome[0], /background:\s*var\(--surface-input\)/);
  assert.match(titleBarChrome[0], /border-bottom:\s*1px solid var\(--accent-border\)/);
  assert.doesNotMatch(titleBarChrome[0], /background:\s*var\(--accent\s*\)/);
  assert.doesNotMatch(titleBarChrome[0], /background:\s*#00ff41/);
  assert.doesNotMatch(titleBarChrome[0], /--history-green/);
  assert.doesNotMatch(titleBarChrome[0], /--chrome-green/);
  const titleBarAction = sidebarCss.match(/\.title-bar__action\s*\{[\s\S]*?\n\}/);
  assert.ok(titleBarAction, "title-bar action rule");
  assert.match(titleBarAction[0], /background:\s*transparent/);
  assert.match(titleBarAction[0], /border:\s*1px solid var\(--accent-border\)/);
  assert.doesNotMatch(titleBarAction[0], /--accent-strong/);
  assert.doesNotMatch(titleBarAction[0], /background:\s*#00ff41/);
  assert.doesNotMatch(sidebarCss, /\.review-list--compact\s*>\s*li:nth-child\(/);
  const historyAll = sidebarCss.match(/\.review-history-all\s*\{[\s\S]*?\n\}/);
  assert.ok(historyAll, "All fill rule");
  assert.match(historyAll[0], /background:\s*var\(--surface-input\)/);
  assert.match(historyAll[0], /border:\s*1px solid var\(--accent-border\)/);
  assert.match(historyAll[0], /color:\s*var\(--accent\)/);
  assert.match(historyAll[0], /padding:\s*5px 7px/);
  assert.doesNotMatch(historyAll[0], /background:\s*#00ff41/);
  assert.doesNotMatch(historyAll[0], /--history-green/);
  assert.doesNotMatch(historyAll[0], /--chrome-green/);
  assert.doesNotMatch(historyAll[0], /--history-neon/);
  assert.match(
    sidebarCss,
    /\.review-list--compact\s*\{[\s\S]*?background:\s*transparent/
  );
  const compactHover = sidebarCss.match(
    /\.review-list--compact\s*>\s*li\s*>\s*button:hover[\s\S]*?\.review-history-all:focus-visible\s*\{[\s\S]*?\n\}/
  );
  assert.ok(compactHover, "compact/All hover fill");
  assert.match(compactHover[0], /border-color:\s*var\(--accent\)/);
  assert.match(compactHover[0], /background:\s*var\(--accent-soft\)/);
  assert.doesNotMatch(compactHover[0], /background:\s*#00ff41/);
  assert.doesNotMatch(compactHover[0], /--history-neon/);
  assert.doesNotMatch(compactHover[0], /--history-green/);
  assert.match(sidebarCss, /max-height:\s*8\.5rem/);
  assert.match(sidebarCss, /overflow-y:\s*auto/);
  assert.match(
    sidebarCss,
    /\.review-list \.review-history-button--analyzed\s*{[\s\S]*?border-color:\s*var\(--accent-border\);[\s\S]*?background:\s*transparent;[\s\S]*?color:\s*var\(--accent\);/
  );
  assert.match(
    sidebarCss,
    /\.review-list \.review-history-button--analyzed\.review-history-button--unopened\s*{[\s\S]*?border-color:\s*rgba\(0,\s*255,\s*65,\s*0\.82\);[\s\S]*?background:\s*var\(--accent-soft\);/
  );
  assert.match(
    sidebarCss,
    /\.review-list \.review-history-button--pending\s*{[\s\S]*?border-color:\s*var\(--line\);[\s\S]*?background:\s*transparent;[\s\S]*?color:\s*var\(--text-muted\);/
  );
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "hideInboxSection"), /clearFrame/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "hideInboxSection"), /frame/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "setReviewHistoryExpanded"), /frame/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "renderReviewCollections"), /frame/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "hideReviewCollections"), /frame/);
  assert.doesNotMatch(sidebarSource, /innerHTML/);
  assert.doesNotMatch(sidebarSource, /\bfetch\s*\(/);
  assert.doesNotMatch(sidebarSource, /addEventListener\(["'](?:hover|mouseover|mouseenter|focus|focusin)["']/);
  assert.match(sidebarSource, /setInterval/);
  assert.match(sidebarSource, /visibilitychange/);
  assert.match(sidebarSource, /TYPES\.REVIEW_INBOX/);
  assert.match(sidebarSource, /onReviewListClick\(event, reviewHistoryList\)/);
  assert.match(sidebarSource, /onReviewListClick\(event, reviewHistoryExpanded\)/);
  assert.match(extractNamedFunction(sidebarSource, "onReviewListClick"), /activateHistoryItem/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "openHostedDetails"), /openReviewOverlay/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "openHostedDetails"), /review\.html/);
  assert.doesNotMatch(sidebarSource, /reviewInboxList|unreadItems/);
});

test("merged history renders analyzed and pending rows and never mutates the iframe", () => {
  const inbox = loadReviewInbox();
  assert.equal(inbox.POLL_MS, 15000);
  const items = [
    {
      media_id: MEDIA_A,
      title: "<img src=x onerror=alert(1)>",
      created_at_ms: 3,
      analyzed: true,
      analysis_run_id: RUN_NEW,
      completed_at_ms: 9,
      unopened: true,
    },
    {
      media_id: MEDIA_B,
      title: "Older",
      created_at_ms: 1,
      analyzed: false,
      analysis_run_id: null,
      completed_at_ms: null,
      unopened: false,
    },
  ];
  const nodes = fakeReviewChromeNodes();
  const hostFrame = { hidden: false, src: ORIGIN };
  const result = inbox.renderCollections(nodes, items);
  assert.deepEqual(
    Array.from(result.historyItems, (item) => item.analysis_run_id),
    [RUN_NEW, null]
  );
  assert.equal(result.compact.length, 1);
  assert.equal(result.expanded.length, 1);
  assert.equal(nodes.historyList.childNodes.length, 1);
  assert.equal(nodes.historyList.childNodes[0].textContent, "<img src=x onerror=alert(1)>");
  assert.doesNotMatch(nodes.historyList.childNodes[0].textContent, /^\d+\.\s/);
  assert.equal(nodes.historyList.childNodes[0].tagName, "LI");
  assert.equal(nodes.historyList.childNodes[0].childNodes[0].tagName, "BUTTON");
  assert.equal(
    nodes.historyList.childNodes[0].childNodes[0].className,
    "review-history-button review-history-button--analyzed review-history-button--unopened"
  );
  assert.equal(nodes.allButton.hidden, false);
  assert.equal(nodes.allButton.textContent, "All");
  assert.equal(nodes.expandedList.childNodes.length, 1);
  assert.equal(
    nodes.expandedList.childNodes[0].childNodes[0].className,
    "review-history-button review-history-button--pending"
  );
  assert.equal(
    nodes.historyList.childNodes[0].childNodes[0].getAttribute("data-media-id"),
    MEDIA_A
  );
  assert.equal(nodes.history.hidden, true);
  assert.equal(nodes.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(inbox.setHistoryExpanded(nodes.toggle, nodes.history, true), true);
  assert.equal(nodes.toggle.getAttribute("aria-expanded"), "true");
  assert.equal(nodes.history.hidden, false);
  assert.equal(inbox.setHistoryExpanded(nodes.toggle, nodes.history, false), false);
  assert.equal(nodes.history.hidden, true);

  const openedItems = items.map((item) => Object.assign({}, item, { unopened: false }));
  inbox.renderCollections(nodes, openedItems);
  assert.equal(nodes.historyList.childNodes.length, 1);
  assert.equal(
    nodes.historyList.childNodes[0].childNodes[0].className,
    "review-history-button review-history-button--analyzed"
  );
  assert.equal(nodes.expandedList.childNodes.length, 1);
  inbox.hideCollections(nodes, true);
  assert.equal(nodes.toggle.disabled, true);
  assert.equal(nodes.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(nodes.history.hidden, true);
  assert.equal(nodes.historyList.childNodes.length, 0);
  assert.equal(nodes.expandedList.childNodes.length, 0);
  assert.equal(nodes.allButton.hidden, true);
  assert.equal(inbox.setHistoryExpanded(nodes.toggle, nodes.history, true), false);
  assert.equal(hostFrame.hidden, false);
  assert.equal(hostFrame.src, ORIGIN);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "renderReviewInboxList"), /innerHTML/);
});

function sidebarShellNode(id, extra) {
  const attrs = {};
  const listeners = {};
  const node = {
    id,
    hidden: (extra && extra.hidden) === true,
    disabled: (extra && extra.disabled) === true,
    checked: false,
    value: "",
    textContent: "",
    open: false,
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    removeAttribute(name) {
      delete attrs[name];
    },
    addEventListener(type, fn) {
      listeners[type] = listeners[type] || [];
      listeners[type].push(fn);
    },
    dispatchClick() {
      (listeners.click || []).forEach((fn) => fn({ target: node }));
    },
    querySelectorAll() {
      return [];
    },
    focus() {},
    show() {
      node.open = true;
    },
    close() {
      node.open = false;
    },
  };
  return node;
}

function loadSidebarShell(options) {
  const settings = options || {};
  const historyList = fakeListNode();
  historyList.addEventListener = function addTestListener() {};
  const expandedList = fakeListNode();
  expandedList.addEventListener = function addTestListener() {};
  const framePosts = [];
  const frameWindow = {
    postMessage(message, targetOrigin) {
      framePosts.push({ message: message, targetOrigin: targetOrigin });
    },
  };
  const frameNode = sidebarShellNode("frame", { hidden: true });
  // Only opt in, so the default harness keeps the iframe detached exactly as
  // before and every existing assertion is unaffected.
  if (settings.captureFrames === true) {
    frameNode.contentWindow = frameWindow;
  }
  const windowListeners = {};
  const storedOriginState = {};
  if (typeof settings.storedOrigin === "string") {
    storedOriginState[companion.STORAGE.origin.name] = settings.storedOrigin;
  }
  const nodes = {
    origin: sidebarShellNode("origin"),
    "shell-status": sidebarShellNode("shell-status"),
    frame: frameNode,
    "chrome-action": sidebarShellNode("chrome-action"),
    "settings-dialog": sidebarShellNode("settings-dialog"),
    "settings-open": sidebarShellNode("settings-open"),
    "settings-close": sidebarShellNode("settings-close"),
    "settings-save": sidebarShellNode("settings-save", { disabled: true }),
    "admin-settings": sidebarShellNode("admin-settings", { hidden: true }),
    "automatic-analysis-enabled": sidebarShellNode("automatic-analysis-enabled", { disabled: true }),
    "automatic-analysis-error": sidebarShellNode("automatic-analysis-error", { hidden: true }),
    "automatic-analysis-confirm": sidebarShellNode("automatic-analysis-confirm", { hidden: true }),
    "automatic-analysis-confirm-ok": sidebarShellNode("automatic-analysis-confirm-ok"),
    "automatic-analysis-confirm-cancel": sidebarShellNode("automatic-analysis-confirm-cancel"),
    "review-history-toggle": sidebarShellNode("review-history-toggle"),
    "review-history": sidebarShellNode("review-history", { hidden: true }),
    "review-history-list": historyList,
    "review-history-all": sidebarShellNode("review-history-all"),
    "review-history-expanded": expandedList,
    "review-dialog": sidebarShellNode("review-dialog"),
    "review-frame": sidebarShellNode("review-frame"),
  };
  const context = {
    FrameNestCompanion: companion,
    document: {
      hidden: false,
      getElementById(id) {
        return nodes[id] || null;
      },
      addEventListener() {},
    },
    chrome: {
      runtime: {
        id: "sidebar-shell-test",
        lastError: null,
        getURL(rel) {
          return "chrome-extension://abc/" + rel;
        },
        sendMessage(message, callback) {
          if (typeof settings.sendMessageResponse === "function") {
            settings.sendMessageResponse(message, callback);
          }
        },
      },
      storage: {
        local: {
          get(_keys, callback) {
            callback(Object.assign({}, storedOriginState));
          },
        },
      },
    },
    window: {
      addEventListener(type, fn) {
        windowListeners[type] = windowListeners[type] || [];
        windowListeners[type].push(fn);
      },
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
  return {
    nodes: nodes,
    inbox: context.FrameNestReviewInbox,
    framePosts: framePosts,
    frameWindow: frameWindow,
    storedOrigin: settings.storedOrigin,
    windowListeners: windowListeners,
    // Deliver one synthetic postMessage from the framed web host to the live
    // window listener the sidebar registered, and report what it posted back.
    deliverWebMessage(value, type, payload, origin) {
      const listeners = windowListeners.message || [];
      assert.equal(listeners.length, 1, "exactly one window message listener");
      listeners[0]({
        source: frameWindow,
        origin: origin === undefined ? settings.storedOrigin : origin,
        data: { v: value, type: type, payload: payload },
      });
      return framePosts.splice(0, framePosts.length);
    },
  };
}

test("history stays closed by default and only the toggle changes it across refreshes", () => {
  const shell = loadSidebarShell();
  const nodes = shell.nodes;
  const items = [
    {
      media_id: MEDIA_A,
      title: "First",
      created_at_ms: 2,
      analyzed: true,
      analysis_run_id: RUN_NEW,
      completed_at_ms: 5,
      unopened: true,
    },
  ];
  const chromeNodes = {
    toggle: nodes["review-history-toggle"],
    history: nodes["review-history"],
    historyList: nodes["review-history-list"],
    allButton: nodes["review-history-all"],
    expandedList: nodes["review-history-expanded"],
  };
  shell.inbox.renderCollections(chromeNodes, items);
  assert.equal(chromeNodes.history.hidden, true);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(chromeNodes.toggle.disabled, false);
  assert.equal(chromeNodes.allButton.hidden, false);

  chromeNodes.toggle.dispatchClick();
  assert.equal(chromeNodes.history.hidden, false);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "true");

  shell.inbox.renderCollections(chromeNodes, items);
  assert.equal(chromeNodes.history.hidden, false);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "true");

  chromeNodes.toggle.dispatchClick();
  assert.equal(chromeNodes.history.hidden, true);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "false");

  shell.inbox.renderCollections(chromeNodes, items);
  assert.equal(chromeNodes.history.hidden, true);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "false");

  shell.inbox.renderCollections(chromeNodes, []);
  assert.equal(chromeNodes.history.hidden, true);
  assert.equal(chromeNodes.toggle.disabled, true);
  assert.equal(chromeNodes.allButton.hidden, true);

  shell.inbox.renderCollections(chromeNodes, items);
  assert.equal(chromeNodes.history.hidden, true);
  assert.equal(chromeNodes.toggle.getAttribute("aria-expanded"), "false");
  assert.equal(chromeNodes.toggle.disabled, false);
});

test("compact analyzed history is newest-first, capped at five, then All", () => {
  const inbox = loadReviewInbox();
  const media = [
    MEDIA_A,
    MEDIA_B,
    "33333333-3333-4333-8333-333333333333",
    "44444444-4444-4444-8444-444444444444",
    "55555555-5555-4555-8555-555555555555",
    "66666666-6666-4666-8666-666666666666",
    "77777777-7777-4777-8777-777777777777",
  ];
  const items = media.map((mediaId, index) => ({
    media_id: mediaId,
    title: "Synthetic analyzed " + String.fromCharCode(65 + index),
    created_at_ms: index + 1,
    analyzed: true,
    analysis_run_id: RUN_NEW,
    completed_at_ms: index + 1,
    unopened: true,
  }));
  items.push({
    media_id: "88888888-8888-4888-8888-888888888888",
    title: "Synthetic pending",
    created_at_ms: 99,
    analyzed: false,
    analysis_run_id: null,
    completed_at_ms: null,
    unopened: false,
  });
  const partitioned = inbox.partitionHistoryItems(items);
  assert.equal(partitioned.compact.length <= inbox.COMPACT_ANALYZED_LIMIT, true);
  assert.equal(partitioned.compact.length, 5);
  assert.deepEqual(
    partitioned.compact.map((item) => item.title),
    [
      "Synthetic analyzed G",
      "Synthetic analyzed F",
      "Synthetic analyzed E",
      "Synthetic analyzed D",
      "Synthetic analyzed C",
    ]
  );
  assert.equal(
    partitioned.compact.every((item) => item.analyzed === true),
    true
  );
  assert.equal(partitioned.expanded.length, 3);
  assert.equal(partitioned.expanded[0].title, "Synthetic pending");
  assert.equal(partitioned.expanded[0].analyzed, false);
  const nodes = fakeReviewChromeNodes();
  const rendered = inbox.renderCollections(nodes, items);
  assert.equal(rendered.compact.length, 5);
  assert.equal(nodes.historyList.childNodes.length, 5);
  assert.equal(nodes.historyList.childNodes[0].childNodes[0].textContent, "Synthetic analyzed G");
  Array.from(nodes.historyList.childNodes).forEach((li) => {
    assert.doesNotMatch(li.childNodes[0].textContent, /^\d+\.\s/);
    assert.match(
      li.childNodes[0].className,
      /review-history-button--analyzed review-history-button--unopened/
    );
  });
  assert.equal(nodes.allButton.hidden, false);
  assert.equal(nodes.allButton.disabled, false);
  assert.equal(nodes.allButton.textContent, "All");
  assert.equal(nodes.allButton.getAttribute("aria-label"), "Show full companion history");
  assert.equal(inbox.setAllExpanded(nodes, true), true);
  assert.equal(nodes.allButton.getAttribute("aria-expanded"), "true");
  assert.equal(nodes.expandedList.hidden, false);
  assert.equal(nodes.expandedList.childNodes.length, 3);
  assert.match(
    nodes.expandedList.childNodes[0].childNodes[0].className,
    /review-history-button--pending/
  );
  assert.match(
    nodes.expandedList.childNodes[1].childNodes[0].className,
    /review-history-button--analyzed/
  );
});

test("ordinary own-history compact is newest five of any state", () => {
  const inbox = loadReviewInbox();
  const items = [
    {
      media_id: MEDIA_A,
      title: "Old analyzed",
      created_at_ms: 1,
      analyzed: true,
      analysis_run_id: RUN_NEW,
      completed_at_ms: 10,
      unopened: true,
    },
    {
      media_id: MEDIA_B,
      title: "Fresh pending",
      created_at_ms: 99,
      analyzed: false,
      analysis_run_id: null,
      completed_at_ms: null,
      unopened: false,
    },
  ];
  const partitioned = inbox.partitionHistoryItems(items, "own_activity");
  assert.equal(partitioned.compact[0].title, "Fresh pending");
  assert.equal(partitioned.compact[0].analyzed, false);
  assert.equal(partitioned.compact[1].title, "Old analyzed");
  const nodes = fakeReviewChromeNodes();
  inbox.renderCollections(nodes, items, "own_activity");
  assert.equal(nodes.historyList.childNodes[0].childNodes[0].textContent, "Fresh pending");
  assert.match(
    nodes.historyList.childNodes[0].childNodes[0].className,
    /review-history-button--pending/
  );
  assert.doesNotMatch(
    nodes.historyList.childNodes[0].childNodes[0].className,
    /unopened/
  );
});

test("side panel accepts both web protocol spellings and still emits the retired one", async () => {
  const retired = "framenest.companion.web.v1";
  const current = "kronika.companion.web.v1";
  const shell = loadSidebarShell({
    storedOrigin: ORIGIN,
    captureFrames: true,
    sendMessageResponse(message, callback) {
      callback({
        v: companion.PROTOCOL,
        type: message.type,
        ok: false,
        error: "composer_unbound",
      });
    },
  });

  // The retired spelling the NUC host still emits is accepted, and the reply
  // the side panel posts back carries that same retired spelling.
  const hello = shell.deliverWebMessage(retired, "web_ready");
  assert.equal(hello.length, 1);
  assert.equal(hello[0].message.v, retired);
  assert.equal(hello[0].message.type, "host_hello");
  assert.equal(hello[0].targetOrigin, ORIGIN);

  // The current spelling is accepted too, and the reply is still the retired one.
  const secondHello = shell.deliverWebMessage(current, "web_ready");
  assert.equal(secondHello.length, 1);
  assert.equal(secondHello[0].message.v, retired);
  assert.equal(secondHello[0].message.type, "host_hello");

  // A refused attach answers with the retired spelling as well.
  const refused = shell.deliverWebMessage(retired, "attach_request", { mediaId: "not-a-uuid" });
  assert.equal(refused.length, 1);
  assert.equal(refused[0].message.v, retired);
  assert.equal(refused[0].message.type, "attach_result");
  assert.equal(refused[0].message.payload.ok, false);
  assert.equal(refused[0].message.payload.error, "invalid_attach");

  // And so does a real attach round trip, including the host_ack path.
  const ack = shell.deliverWebMessage(current, "host_ack");
  assert.deepEqual(ack, []);
  const attached = shell.deliverWebMessage(current, "attach_request", {
    mediaId: MEDIA_A,
    locationId: MEDIA_B,
  });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(attached.length, 0);
  const results = shell.framePosts.splice(0, shell.framePosts.length);
  assert.equal(results.length, 1);
  assert.equal(results[0].message.v, retired);
  assert.equal(results[0].message.type, "attach_result");
  assert.equal(results[0].message.payload.ok, false);
  assert.equal(results[0].message.payload.error, "composer_unbound");
  assert.equal(results[0].targetOrigin, ORIGIN);

  // Anything that is neither spelling is refused, and nothing is posted back.
  [
    "framenest.companion.web.v2",
    "kronika.companion.web.v2",
    "framenest.companion.review.v1",
    "",
    null,
    undefined,
    7,
    [retired],
  ].forEach((value) => {
    assert.deepEqual(shell.deliverWebMessage(value, "web_ready"), [], "refused " + String(value));
    assert.deepEqual(
      shell.deliverWebMessage(value, "attach_request", { mediaId: MEDIA_A, locationId: MEDIA_B }),
      [],
      "refused " + String(value)
    );
  });
});

test("analyzed history click posts opened then open_details; pending is hosted without opened", () => {
  const inbox = loadReviewInbox();
  const details = [];
  const pending = [];
  const analyzed = {
    media_id: MEDIA_A,
    title: "Synthetic analyzed",
    analyzed: true,
    analysis_run_id: RUN_NEW,
  };
  assert.equal(inbox.historyClickKind(analyzed), "open_details");
  assert.equal(
    inbox.activateHistoryItem(
      analyzed,
      (id) => {
        details.push(id);
      },
      (id) => {
        pending.push(id);
      }
    ),
    "open_details"
  );
  assert.deepEqual(details, [MEDIA_A]);
  assert.deepEqual(pending, []);
  const waiting = { media_id: MEDIA_B, title: "Synthetic pending", analyzed: false };
  assert.equal(inbox.historyClickKind(waiting), "open_details");
  assert.equal(
    inbox.activateHistoryItem(
      waiting,
      (id) => {
        details.push(id);
      },
      (id) => {
        pending.push(id);
      }
    ),
    "open_details"
  );
  assert.deepEqual(details, [MEDIA_A, MEDIA_B]);
  assert.deepEqual(pending, []);
  const message = inbox.openDetailsMessage(MEDIA_A);
  assert.equal(message.v, "framenest.companion.web.v1");
  assert.equal(message.type, "open_details");
  assert.equal(message.payload.mediaId, MEDIA_A);
  assert.equal("mediaId" in message.payload, true);
  assert.equal(Object.prototype.hasOwnProperty.call(message.payload, "url"), false);
  assert.equal(inbox.openDetailsMessage("not-a-uuid"), null);
  assert.match(extractNamedFunction(sidebarSource, "openHostedDetails"), /postToFrame\(message, storedOrigin\)/);
  assert.match(extractNamedFunction(sidebarSource, "openHostedDetails"), /handshakeTimeoutCopy/);
  assert.doesNotMatch(extractNamedFunction(sidebarSource, "openHostedDetails"), /openReviewOverlay/);
  const clickSource = extractNamedFunction(sidebarSource, "onReviewListClick");
  assert.match(clickSource, /openHostedDetails/);
  assert.match(clickSource, /REVIEW_INBOX_OPENED/);
  const hostedIndex = clickSource.indexOf("openHostedDetails");
  const openedIndex = clickSource.indexOf("REVIEW_INBOX_OPENED");
  assert.ok(hostedIndex >= 0 && openedIndex > hostedIndex);
  assert.match(clickSource, /item\.analyzed === true/);
  assert.match(reviewSource, /No successful analysis yet\./);
});

test("cataloged asset helper keeps only UUID media ids", () => {
  assert.deepEqual(
    companion.catalogedMediaIdsFromAssets([
      { media_id: MEDIA_A, filename: "secret.mp4" },
      { media_id: MEDIA_A },
      { media_id: "https://evil.example" },
      { title: "no id" },
    ]),
    [MEDIA_A]
  );
  const pruned = companion.pruneAwaitingRecords(
    [
      { media_id: MEDIA_A, expires_at_ms: Date.now() + 60000 },
      { media_id: MEDIA_B, expires_at_ms: Date.now() + 60000 },
    ],
    [MEDIA_A],
    Date.now()
  );
  assert.equal(pruned.length, 1);
  assert.equal(pruned[0].media_id, MEDIA_B);
});

test("worker GET detail and POST opened/apply send W04 JSON only", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  worker.state.detailBody = {
    suggestions: [{ analysis_run_id: RUN_NEW, title: "Secret title" }],
    canonical: { display_title: "Now" },
  };
  const detail = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.REVIEW_INBOX_DETAIL,
    payload: { mediaId: MEDIA_A, url: "https://evil.example/steal" },
  });
  assert.equal(detail.ok, true);
  assert.equal(detail.forbidden, false);
  const detailCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.equal(detailCall.url, ORIGIN + "/api/companion/review-inbox/" + MEDIA_A);
  assert.equal((detailCall.init.method || "GET").toUpperCase(), "GET");
  assert.equal(detailCall.init.headers["X-FrameNest-Request"], "1");
  assert.equal(detailCall.url.indexOf("evil.example"), -1);

  const opened = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.REVIEW_INBOX_OPENED,
    payload: { mediaId: MEDIA_A, analysis_run_id: RUN_NEW, title: "ignore me" },
  });
  assert.equal(opened.ok, true);
  const openedCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.equal(openedCall.url, ORIGIN + "/api/companion/review-inbox/" + MEDIA_A + "/opened");
  assert.equal(openedCall.init.method, "POST");
  assert.deepEqual(JSON.parse(openedCall.init.body), { analysis_run_id: RUN_NEW });
  assert.equal("title" in JSON.parse(openedCall.init.body), false);

  const apply = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.REVIEW_INBOX_APPLY,
    payload: {
      mediaId: MEDIA_A,
      analysis_run_id: RUN_NEW,
      fields: ["display_title", "tags"],
      tag_keys: ["alpha", "beta"],
      display_title: "client title",
      description: "client description",
      url: "https://evil.example/apply",
    },
  });
  assert.equal(apply.ok, true);
  const applyCall = worker.state.fetchCalls[worker.state.fetchCalls.length - 1];
  assert.equal(applyCall.url, ORIGIN + "/api/companion/review-inbox/" + MEDIA_A + "/apply");
  assert.equal(applyCall.init.method, "POST");
  assert.equal(applyCall.init.headers["X-FrameNest-Request"], "1");
  const posted = JSON.parse(applyCall.init.body);
  assert.deepEqual(Object.keys(posted).sort(), ["analysis_run_id", "fields", "tag_keys"]);
  assert.deepEqual(posted, {
    analysis_run_id: RUN_NEW,
    fields: ["display_title", "tags"],
    tag_keys: ["alpha", "beta"],
  });
  assert.equal(applyCall.url.indexOf("evil.example"), -1);

  const rejected = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.REVIEW_INBOX_APPLY,
    payload: { mediaId: "not-a-uuid", analysis_run_id: RUN_NEW, fields: ["display_title"] },
  });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error, "invalid_apply");
});

test("opening overlay keeps #frame mounted and uses exact extension origin", () => {
  const inbox = loadReviewInbox();
  const hostFrame = { hidden: false, src: ORIGIN, removed: false };
  const dialog = {
    open: false,
    show() {
      this.open = true;
    },
    close() {
      this.open = false;
    },
    setAttribute() {},
    removeAttribute() {},
  };
  const overlayFrame = {
    attrs: {},
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
  };
  assert.equal(inbox.openOverlay(MEDIA_A, dialog, overlayFrame), true);
  assert.equal(dialog.open, true);
  assert.match(overlayFrame.attrs.src, /ui\/review\.html#media=/);
  assert.ok(String(overlayFrame.attrs.src).indexOf(MEDIA_A) !== -1);
  assert.equal(hostFrame.hidden, false);
  assert.equal(hostFrame.src, ORIGIN);
  inbox.closeOverlay(dialog, overlayFrame);
  assert.equal(dialog.open, false);
  assert.equal("src" in overlayFrame.attrs, false);
  assert.equal(hostFrame.hidden, false);
  const accepted = companion.acceptReviewOverlayMessage(
    {
      source: overlayFrame,
      origin: "chrome-extension://abc",
      data: { v: companion.REVIEW_OVERLAY.protocol, type: companion.REVIEW_OVERLAY.types.INBOX_REFRESH },
    },
    overlayFrame,
    "chrome-extension://abc"
  );
  assert.equal(accepted.type, companion.REVIEW_OVERLAY.types.INBOX_REFRESH);
  assert.equal(
    companion.acceptReviewOverlayMessage(
      {
        source: overlayFrame,
        origin: "chrome-extension://abc",
        data: { v: companion.REVIEW_OVERLAY.protocol, type: companion.REVIEW_OVERLAY.types.INBOX_REFRESH },
      },
      overlayFrame,
      "*"
    ),
    null
  );
  assert.doesNotMatch(sidebarSource, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(reviewSource, /postMessage\([^)]*,\s*["']\*["']/);
});

test("sidebar and review requests recover only invalidated contexts and disable affected UI", async () => {
  const invalidLastError = {
    id: "sidebar-test",
    lastError: { message: "Extension context invalidated." },
    getURL(rel) {
      return "chrome-extension://abc/" + rel;
    },
    sendMessage(_message, callback) {
      callback(undefined);
    },
  };
  const staleSidebar = loadReviewInbox({ runtime: invalidLastError });
  const staleResult = await staleSidebar.request(companion.TYPES.REVIEW_INBOX, {});
  assert.equal(staleResult.ok, false);
  assert.equal(staleResult.stale, true);
  assert.equal(staleResult.error, "extension_context_invalidated");

  const ordinarySidebar = loadReviewInbox({
    runtime: {
      id: "sidebar-test",
      lastError: { message: "Receiving end does not exist" },
      getURL(rel) {
        return "chrome-extension://abc/" + rel;
      },
      sendMessage(_message, callback) {
        callback(undefined);
      },
    },
  });
  const ordinary = await ordinarySidebar.request(companion.TYPES.REVIEW_INBOX, {});
  assert.equal(ordinary.error, "extension_unavailable");
  assert.equal(ordinary.stale, undefined);

  const invalidUrlSidebar = loadReviewInbox({
    runtime: {
      id: "sidebar-test",
      lastError: null,
      getURL() {
        throw new Error("Extension context invalidated.");
      },
      sendMessage() {},
    },
  });
  assert.equal(invalidUrlSidebar.overlayUrl(MEDIA_A), "");
  const unrelatedUrlSidebar = loadReviewInbox({
    runtime: {
      id: "sidebar-test",
      lastError: null,
      getURL() {
        throw new Error("unrelated runtime failure");
      },
      sendMessage() {},
    },
  });
  assert.throws(() => unrelatedUrlSidebar.overlayUrl(MEDIA_A), /unrelated runtime failure/);

  const staleReview = loadReviewOverlay({
    runtime: {
      id: "review-test",
      lastError: null,
      getURL(rel) {
        return "chrome-extension://abc/" + rel;
      },
      sendMessage() {
        throw new Error("Extension context invalidated.");
      },
    },
  });
  const reviewResult = await staleReview.request(companion.TYPES.REVIEW_INBOX_DETAIL, {
    mediaId: MEDIA_A,
  });
  assert.equal(reviewResult.stale, true);
  assert.equal(reviewResult.error, "extension_context_invalidated");

  const unrelatedReview = loadReviewOverlay({
    runtime: {
      id: "review-test",
      lastError: null,
      getURL(rel) {
        return "chrome-extension://abc/" + rel;
      },
      sendMessage() {
        throw new Error("unrelated review runtime failure");
      },
    },
  });
  await assert.rejects(
    unrelatedReview.request(companion.TYPES.REVIEW_INBOX_DETAIL, { mediaId: MEDIA_A }),
    /unrelated review runtime failure/
  );

  let state = null;
  const controller = loadReviewOverlay().createController({
    request: async () => ({
      ok: false,
      error: "extension_context_invalidated",
      stale: true,
    }),
    notifyParent() {},
    render(next) {
      state = next;
    },
  });
  await controller.open(MEDIA_A);
  assert.equal(state.runtimeStale, true);
  assert.equal(state.lastError, companion.EXTENSION_CONTEXT_RECOVERY_COPY);
  assert.equal(state.saveEnabled, false);
  controller.setField("display_title", true);
  assert.equal(state.fields.display_title, false);

  assert.match(sidebarSource, /handleRuntimeStale[\s\S]*chromeAction\.disabled = true/);
  assert.match(sidebarSource, /handleRuntimeStale[\s\S]*settingsSave\.disabled = true/);
  assert.match(reviewSource, /handleRuntimeStale[\s\S]*saveButton\.disabled = true/);
  assert.doesNotMatch(sidebarSource, /catch\s*\{\s*return\s+"";\s*\}/);
  assert.doesNotMatch(reviewSource, /catch\s*\{\s*return\s+"";\s*\}/);
});

test("dropdown label, field gates, chip removal, stay-open apply, and 403 wipe", async () => {
  const overlay = loadReviewOverlay();
  const label = overlay.formatRunLabel({
    completed_at_ms: Date.UTC(2026, 7, 23, 12, 0, 0),
    model_id: "meta/llama-test",
    title: "Suggested title",
  });
  assert.match(label, /meta\/llama-test/);
  assert.match(label, /Suggested title/);
  assert.match(label, / · /);
  assert.equal(overlay.parseMediaHash("#media=" + MEDIA_A), MEDIA_A);
  assert.equal(overlay.parseMediaHash("#url=https://x.com/a/status/1"), null);
  const tags = [
    { status: "mapped", key: "alpha", display_name: "Alpha", value: "Alpha" },
    { status: "unknown", key: null, value: "Nope", status: "unknown" },
    { status: "mapped", key: "beta", display_name: "Beta", value: "Beta" },
  ];
  const remaining = overlay.remainingMappedKeys(tags, { beta: true });
  assert.equal(remaining.length, 1);
  assert.equal(remaining[0], "alpha");
  assert.equal(overlay.tagsCanBeChecked(0), false);
  assert.equal(overlay.saveEnabled({ display_title: false, tags: false, description: false }, 2), false);
  assert.equal(overlay.saveEnabled({ display_title: true, tags: false, description: false }, 0), true);

  const calls = [];
  let lastState = null;
  const session = overlay.createController({
    request: async (type, payload) => {
      calls.push({ type, payload });
      if (type === companion.TYPES.REVIEW_INBOX_DETAIL) {
        return {
          ok: true,
          status: 200,
          forbidden: false,
          body: {
            suggestions: [
              {
                analysis_run_id: RUN_NEW,
                completed_at_ms: 2,
                model_id: "meta/llama-test",
                title: "New title",
                description: "New description",
                tags: tags,
              },
              {
                analysis_run_id: RUN_OLD,
                completed_at_ms: 1,
                model_id: "meta/llama-old",
                title: "Old title",
                description: "Old description",
                tags: tags,
              },
            ],
            canonical: { display_title: "Current", field_sources: {} },
            publication: { state: "unpublished", ready: false, missing_fields: ["display_title"] },
          },
        };
      }
      if (type === companion.TYPES.REVIEW_INBOX_OPENED) {
        return { ok: true, status: 200, forbidden: false, body: {} };
      }
      if (type === companion.TYPES.REVIEW_INBOX_APPLY) {
        if (payload && payload.fail) {
          return { ok: false, status: 409, forbidden: false, error: "conflict" };
        }
        return {
          ok: true,
          status: 200,
          forbidden: false,
          body: {
            canonical: { display_title: "Applied", field_sources: { display_title: { analysis_run_id: RUN_NEW } } },
            publication: { status: "not_ready", state: "unpublished", ready: false },
          },
        };
      }
      return { ok: false, error: "unexpected" };
    },
    notifyParent() {},
    render(state) {
      lastState = state;
    },
  });

  await session.open(MEDIA_A);
  assert.equal(lastState.overlayOpen, true);
  assert.equal(lastState.runId, RUN_NEW);
  assert.equal(
    calls.filter((call) => call.type === companion.TYPES.REVIEW_INBOX_OPENED).length,
    1
  );
  assert.equal(lastState.saveEnabled, false);
  session.setField("display_title", true);
  assert.equal(lastState.saveEnabled, true);
  session.removeChip("alpha");
  session.removeChip("beta");
  session.setField("tags", true);
  assert.equal(lastState.fields.tags, false);
  assert.equal(lastState.tagsDisabled, true);

  session.setField("display_title", true);
  const success = await session.save();
  assert.equal(success.ok, true);
  assert.equal(
    calls.filter((call) => call.type === companion.TYPES.REVIEW_INBOX_OPENED).length,
    1
  );
  const applyCall = calls.filter((call) => call.type === companion.TYPES.REVIEW_INBOX_APPLY).pop();
  assert.deepEqual(applyCall.payload.fields, ["display_title"]);
  assert.equal(applyCall.payload.display_title, undefined);
  assert.equal(applyCall.payload.description, undefined);
  assert.equal(lastState.overlayOpen, true);
  assert.equal(lastState.fields.display_title, false);
  assert.equal(lastState.canonical.display_title, "Applied");
  assert.equal(lastState.publication.status, "not_ready");

  session.setField("display_title", true);
  const previousCalls = calls.length;
  await session.selectRun(RUN_OLD);
  assert.equal(lastState.fields.display_title, false);
  assert.equal(lastState.runId, RUN_OLD);
  const afterSwitch = calls.slice(previousCalls);
  assert.equal(afterSwitch.some((call) => call.type === companion.TYPES.REVIEW_INBOX_OPENED), true);
  assert.equal(afterSwitch.some((call) => call.type === companion.TYPES.REVIEW_INBOX_APPLY), false);

  const forbiddenSession = overlay.createController({
    request: async () => ({ ok: false, status: 403, forbidden: true, error: "http_403" }),
    notifyParent() {},
    render(state) {
      lastState = state;
    },
  });
  await forbiddenSession.open(MEDIA_A);
  assert.equal(lastState.overlayOpen, false);
  assert.equal(lastState.suggestion, null);
  assert.equal(lastState.lastError, "");
  assert.doesNotMatch(reviewHtml, /<input[^>]+id="suggestion-title"/);
  assert.match(reviewHtml, /History \(run completion time\)/);
  assert.match(reviewCss, /--background/);
  assert.match(reviewCss, /#00ff41/);
});

test("pending review detail shows the waiting state without opened or apply requests", async () => {
  const overlay = loadReviewOverlay();
  const calls = [];
  let lastState = null;
  const session = overlay.createController({
    request: async (type, payload) => {
      calls.push({ type, payload });
      if (type === companion.TYPES.REVIEW_INBOX_DETAIL) {
        return {
          ok: true,
          status: 200,
          forbidden: false,
          body: {
            suggestions: [],
            canonical: { display_title: "Pending post", field_sources: {} },
            publication: { state: "unpublished", ready: false, missing_fields: [] },
          },
        };
      }
      return { ok: false, error: "unexpected" };
    },
    notifyParent() {},
    render(state) {
      lastState = state;
    },
  });

  const loaded = await session.open(MEDIA_A);
  assert.equal(loaded.ok, true);
  assert.equal(loaded.pending, true);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].type, companion.TYPES.REVIEW_INBOX_DETAIL);
  assert.equal(lastState.pending, true);
  assert.equal(lastState.suggestion, null);
  assert.equal(lastState.canonical.display_title, "Pending post");
  assert.equal(lastState.saveEnabled, false);
  assert.equal(lastState.tagsDisabled, true);

  session.setField("display_title", true);
  session.removeChip("alpha");
  assert.equal(lastState.fields.display_title, false);
  const selected = await session.selectRun(RUN_NEW);
  assert.equal(selected.ok, false);
  assert.equal(selected.error, "analysis_pending");
  const saved = await session.save();
  assert.equal(saved.ok, false);
  assert.equal(saved.error, "analysis_pending");
  assert.equal(calls.length, 1);
  assert.match(reviewSource, /No successful analysis yet\./);
  assert.match(reviewSource, /history\.disabled =[\s\S]*?state\.pending === true/);
  assert.match(reviewSource, /fieldTitle\.disabled =[\s\S]*?state\.pending === true/);
  assert.match(reviewSource, /fieldDescription\.disabled =[\s\S]*?state\.pending === true/);
});

test("Review Save retries opened after failure and blocks Apply until opened succeeds", async () => {
  const overlay = loadReviewOverlay();
  const calls = [];
  let openedAttempts = 0;
  let lastState = null;
  const session = overlay.createController({
    request: async (type, payload) => {
      calls.push({ type, payload });
      if (type === companion.TYPES.REVIEW_INBOX_DETAIL) {
        return {
          ok: true,
          status: 200,
          forbidden: false,
          body: {
            suggestions: [
              {
                analysis_run_id: RUN_NEW,
                completed_at_ms: 2,
                model_id: "meta/llama-test",
                title: "New title",
                description: "New description",
                tags: [],
              },
            ],
            canonical: { display_title: "Current", field_sources: {} },
            publication: { state: "unpublished", ready: false, missing_fields: [] },
          },
        };
      }
      if (type === companion.TYPES.REVIEW_INBOX_OPENED) {
        openedAttempts += 1;
        if (openedAttempts <= 2) {
          return { ok: false, status: 500, forbidden: false, error: "opened_failed" };
        }
        return { ok: true, status: 200, forbidden: false, body: { unopened: false } };
      }
      if (type === companion.TYPES.REVIEW_INBOX_APPLY) {
        return {
          ok: true,
          status: 200,
          forbidden: false,
          body: {
            canonical: { display_title: "Applied", field_sources: {} },
            publication: { state: "unpublished", ready: false, status: "not_ready" },
          },
        };
      }
      return { ok: false, error: "unexpected" };
    },
    notifyParent() {},
    render(state) {
      lastState = state;
    },
  });

  await session.open(MEDIA_A);
  assert.equal(openedAttempts, 1);
  session.setField("display_title", true);
  const blocked = await session.save();
  assert.equal(blocked.ok, false);
  assert.equal(openedAttempts, 2);
  assert.equal(
    calls.filter((call) => call.type === companion.TYPES.REVIEW_INBOX_APPLY).length,
    0
  );
  assert.equal(lastState.fields.display_title, true);
  assert.equal(lastState.lastError, "This review could not be marked opened.");

  const applied = await session.save();
  assert.equal(applied.ok, true);
  assert.equal(openedAttempts, 3);
  assert.equal(
    calls.filter((call) => call.type === companion.TYPES.REVIEW_INBOX_APPLY).length,
    1
  );
  assert.equal(lastState.fields.display_title, false);
  assert.equal(lastState.canonical.display_title, "Applied");
});

test("review receipts render tag sources without replacing field receipts", () => {
  const overlay = loadReviewOverlay();
  const receipts = fakeListNode();
  overlay.renderReceiptPanel(receipts, {
    field_sources: {
      tags: {
        analysis_run_id: RUN_NEW,
        model_id: "meta/llama-test",
        applied_at_ms: 9,
      },
      display_title: null,
    },
    tag_sources: {
      cats: {
        analysis_run_id: RUN_OLD,
        model_id: "meta/llama-old",
        applied_at_ms: 8,
      },
    },
  });
  const texts = receipts.childNodes.map((node) => node.textContent);
  assert.equal(texts.length, 2);
  assert.match(texts[0], /tags:/);
  assert.match(texts[0], new RegExp(RUN_NEW));
  assert.match(texts[1], /tag cats:/);
  assert.match(texts[1], new RegExp(RUN_OLD));
  assert.equal(typeof overlay.renderReceiptPanel, "function");
  assert.match(reviewSource, /getElementById\("receipts"\)/);
  assert.match(reviewSource, /tag_sources/);
  assert.doesNotMatch(reviewSource, /review-inbox-list/);
});

// ---------------------------------------------------------------------------
// KSI-IMPL-C2 - identity compatibility window
// ---------------------------------------------------------------------------

function headerSpellings(init) {
  const headers = (init && init.headers) || {};
  return {
    retired: headers["X-FrameNest-Request"],
    current: headers["X-Kronika-Request"],
  };
}

test("Class 1: every extension send site carries both mutation header spellings", async () => {
  const fetchJsonWorker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  await fetchJsonWorker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.IDENTITY,
  });
  const jsonCall = fetchJsonWorker.state.fetchCalls[fetchJsonWorker.state.fetchCalls.length - 1];
  assert.deepEqual(headerSpellings(jsonCall.init), { retired: "1", current: "1" });

  const previewWorker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  const preview = await previewWorker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.PREVIEW_FETCH,
    payload: {
      mediaId: MEDIA_A,
      locationId: MEDIA_B,
    },
  });
  assert.equal(preview.ok, true);
  const previewCall = previewWorker.state.fetchCalls[previewWorker.state.fetchCalls.length - 1];
  assert.deepEqual(headerSpellings(previewCall.init), { retired: "1", current: "1" });

  const attachWorker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  attachWorker.state.messageListeners[0](
    { v: companion.PROTOCOL, type: companion.TYPES.IDENTITY },
    { tab: { id: 7 }, origin: "https://x.com" },
    () => {}
  );
  await attachWorker.context.transferAttach(attachWorker.state.connectedPorts[0], {
    mediaId: MEDIA_A,
    locationId: MEDIA_B,
    filename: "attach.bin",
  });
  const attachCall = attachWorker.state.fetchCalls[attachWorker.state.fetchCalls.length - 1];
  assert.deepEqual(headerSpellings(attachCall.init), { retired: "1", current: "1" });

  assert.equal(
    (workerSource.match(/"X-FrameNest-Request": "1"/g) || []).length,
    1,
    "the retired header spelling must exist only in the shared constant"
  );
  assert.equal(
    (workerSource.match(/"X-Kronika-Request": "1"/g) || []).length,
    1,
    "the current header spelling must exist only in the shared constant"
  );
  assert.equal(
    (workerSource.match(/MUTATION_REQUEST_HEADERS/g) || []).length,
    4,
    "the declaration plus the three send sites"
  );
});

test("Class 2: the retired companion API version is accepted", async () => {
  const worker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    companionMediaBody: { companion_api_version: "framenest-companion.v1", items: [] },
  });
  const result = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.PICKER_QUERY,
    payload: {},
  });
  assert.equal(result.ok, true);
  assert.equal(result.page.companion_api_version, "framenest-companion.v1");
});

test("Class 2: the current companion API version is accepted", async () => {
  const worker = loadWorker({
    storage: { frameNestOrigin: ORIGIN },
    companionMediaBody: { companion_api_version: "kronika-companion.v1", items: [] },
  });
  const result = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.PICKER_QUERY,
    payload: {},
  });
  assert.equal(result.ok, true);
  assert.equal(result.page.companion_api_version, "kronika-companion.v1");
});

test("Class 2: any other companion API version still disables the picker", async () => {
  for (const version of ["framenest-companion.v2", "kronika-companion.v0", "", null, undefined, 7]) {
    const worker = loadWorker({
      storage: { frameNestOrigin: ORIGIN },
      companionMediaBody: { companion_api_version: version, items: [] },
    });
    const result = await worker.context.handle({
      v: companion.PROTOCOL,
      type: companion.TYPES.PICKER_QUERY,
      payload: {},
    });
    assert.equal(result.ok, false, String(version));
    assert.equal(result.error, "version_skew", String(version));
    assert.equal(result.disable, true, String(version));
  }
});

test("Class 3: the internal protocol names carry the current spelling and no retired copy", () => {
  assert.equal(companion.PROTOCOL, "kronika.companion.v1");
  assert.equal(companion.REVIEW_OVERLAY.protocol, "kronika.companion.review.v1");
  const bundle = [
    workerSource,
    sidebarSource,
    reviewSource,
    fs.readFileSync(path.join(REPO, "extension/content/x_adapter.js"), "utf8"),
    fs.readFileSync(path.join(REPO, "extension/shared/messages.js"), "utf8"),
    fs.readFileSync(path.join(REPO, "extension/ui/picker.js"), "utf8"),
    fs.readFileSync(path.join(REPO, "extension/ui/save.js"), "utf8"),
  ].join("\n");
  assert.doesNotMatch(bundle, /"framenest\.companion\.v1"/);
  assert.doesNotMatch(bundle, /"framenest\.companion\.review\.v1"/);
  assert.match(messagesSource, /const PROTOCOL = "kronika\.companion\.v1";/);
});

test("Class 4: the origin key is read from the retired spelling only when the current one is absent", async () => {
  const retiredOnly = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  const identity = await retiredOnly.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.IDENTITY,
  });
  assert.equal(identity.ok, true);
  assert.ok(
    retiredOnly.state.fetchCalls.some((call) => call.url.indexOf(ORIGIN) === 0),
    "a retired-only origin must still authorise a request"
  );

  const both = loadWorker({
    storage: { frameNestOrigin: "https://retired.example.ts.net", kronikaOrigin: ORIGIN },
  });
  const preferred = await both.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.IDENTITY,
  });
  assert.equal(preferred.ok, true);
  assert.ok(
    both.state.fetchCalls.every((call) => call.url.indexOf("https://retired.example.ts.net") !== 0),
    "the current spelling wins when both are present"
  );
});

test("Class 4: configuring the origin writes the current key and leaves the retired key untouched", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: "https://retired.example.ts.net" } });
  const result = await worker.context.handle({
    v: companion.PROTOCOL,
    type: companion.TYPES.CONFIGURE_ORIGIN,
    payload: { origin: ORIGIN },
  });
  assert.equal(result.ok, true);
  assert.equal(worker.state.storage.kronikaOrigin, ORIGIN);
  assert.equal(
    worker.state.storage.frameNestOrigin,
    "https://retired.example.ts.net",
    "the retired entry must survive the write untouched"
  );
});

test("Class 4: an explicit reset removes the retired origin and the retired alarm", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  assert.equal(worker.state.storage.kronikaOrigin, undefined);
  await worker.context.handle({ v: companion.PROTOCOL, type: companion.TYPES.RESET });
  assert.equal(worker.state.storage.frameNestOrigin, undefined);
  assert.equal(worker.state.alarms["framenest.review-inbox"], undefined);
});

test("Class 4: the review-inbox alarm is created under the current name and the retired name still refreshes", async () => {
  const worker = loadWorker({ storage: { frameNestOrigin: ORIGIN } });
  await worker.context.ensureReviewInboxAlarm();
  assert.equal(worker.state.alarms["kronika.review-inbox"].periodInMinutes, 1);
  worker.state.alarms["framenest.review-inbox"] = {
    name: "framenest.review-inbox",
    periodInMinutes: 1,
  };
  const before = worker.state.fetchCalls.length;
  worker.state.alarmListeners[0]({ name: "framenest.review-inbox" });
  await new Promise((resolve) => setImmediate(resolve));
  assert.ok(
    worker.state.fetchCalls.length > before,
    "the retired alarm name must still drive the badge refresh"
  );
});

test("Order independence: both spellings are authorised by the pre-C1 gate and by the C1 gate", () => {
  // Modelled from tailscale_ingress: the pre-C1 gate compares only its own
  // spelling and ignores any other header; the C1 gate accepts either spelling
  // and requires both to be 1 when both are present.
  function preC1Gate(headers) {
    const names = Object.keys(headers).map((name) => name.toLowerCase());
    return !names.includes("x-kronika-request") || headers["X-Kronika-Request"] === "1";
  }
  function c1Gate(headers) {
    const present = ["X-FrameNest-Request", "X-Kronika-Request"].filter(
      (name) => Object.prototype.hasOwnProperty.call(headers, name)
    );
    if (!present.length) {
      return false;
    }
    return present.every((name) => headers[name] === "1");
  }

  const sent = { "X-FrameNest-Request": "1", "X-Kronika-Request": "1", Accept: "application/json" };
  assert.equal(preC1Gate(sent), true, "the pre-C1 gate must still authorise the request");
  assert.equal(c1Gate(sent), true, "the C1 gate must authorise the request");
  assert.equal(c1Gate({ "X-Kronika-Request": "1" }), true, "a C1-era sender stays authorised");
  assert.equal(
    preC1Gate({ "X-FrameNest-Request": "1" }),
    true,
    "the pre-cut extension stays authorised against the pre-C1 gate"
  );
});
