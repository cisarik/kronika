const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const REPO = path.resolve(__dirname, "..");
const companion = require(path.join(REPO, "extension/shared/messages.js"));
const webHost = require(path.join(REPO, "src/kronika/adapters/api/web/companion_host.js"));
const sidebarSource = fs.readFileSync(path.join(REPO, "extension/ui/sidebar.js"), "utf8");
const hostSource = fs.readFileSync(
  path.join(REPO, "src/kronika/adapters/api/web/companion_host.js"),
  "utf8"
);
const appSource = fs.readFileSync(
  path.join(REPO, "src/kronika/adapters/api/web/app.js"),
  "utf8"
);

const PIN = "chrome-extension://omiihmnlkmieaafaphohakcgmbggppap";
const ORIGIN = "https://nuc-1.example.ts.net";
const RETIRED_WEB_PROTOCOL = "framenest.companion.web.v1";
const CURRENT_WEB_PROTOCOL = "kronika.companion.web.v1";
const MEDIA_ID = "11111111-1111-4111-8111-111111111111";
const LOCATION_ID = "22222222-2222-4222-8222-222222222222";
const REFUSED_WEB_PROTOCOLS = [
  RETIRED_WEB_PROTOCOL,
  "framenest.companion.web.v2",
  "kronika.companion.web.v2",
  "framenest.companion.web",
  "kronika.companion.web",
  "framenest.companion.review.v1",
  "framenest.companion.v1",
  "companion.web.v1",
  "framenest.companion.web.v1 ",
  " framenest.companion.web.v1",
  "FRAMENEST.COMPANION.WEB.V1",
  "kronika.companion.web.v1 ",
  " kronika.companion.web.v1",
  "KRONIKA.COMPANION.WEB.V1",
  "unknown",
  "",
  null,
  undefined,
  1,
  true,
  [RETIRED_WEB_PROTOCOL],
  [CURRENT_WEB_PROTOCOL],
  { v: RETIRED_WEB_PROTOCOL },
  { v: CURRENT_WEB_PROTOCOL },
];

function createHostHarness() {
  const posted = [];
  const parent = {
    postMessage(message, targetOrigin) {
      posted.push({ message, targetOrigin });
    },
  };
  let receive;
  const window = {
    parent,
    addEventListener(type, listener) {
      assert.equal(type, "message");
      assert.equal(receive, undefined, "one host listener");
      receive = listener;
    },
  };
  const host = webHost.createHost({ window, parent });
  assert.equal(typeof receive, "function", "start registers the real listener");
  return {
    host,
    posted,
    send(v, type, payload, overrides) {
      return receive({ origin: PIN, source: parent, data: { v, type, payload }, ...overrides });
    },
  };
}

function webMessage(iframeWindow, origin, value, type) {
  return {
    source: iframeWindow,
    origin: origin,
    data: { v: value, type: type },
  };
}

function loadSidebarBridgeContext() {
  const context = {
    KronikaCompanion: companion,
    document: {
      getElementById() {
        return null;
      },
    },
    chrome: {},
    window: {},
    Object,
    Boolean,
    String,
    Promise,
    setTimeout,
    clearTimeout,
  };
  vm.createContext(context);
  vm.runInContext(sidebarSource, context);
  return context;
}

function loadSidebarBridge() {
  return loadSidebarBridgeContext().KronikaSidebarBridge;
}

test("host and extension emit and accept only the canonical spelling", () => {
  const bridge = loadSidebarBridge();
  assert.equal(webHost.PROTOCOL, CURRENT_WEB_PROTOCOL);
  assert.equal(bridge.WEB_PROTOCOL, CURRENT_WEB_PROTOCOL);
  assert.equal(Object.hasOwn(bridge, "RETIRED_WEB_PROTOCOL"), false);
  assert.deepEqual(webHost.ACCEPTED_WEB_PROTOCOLS, [CURRENT_WEB_PROTOCOL]);
  assert.deepEqual(Array.from(bridge.ACCEPTED_WEB_PROTOCOLS), [
    CURRENT_WEB_PROTOCOL,
  ]);
  assert.equal(Object.isFrozen(webHost.ACCEPTED_WEB_PROTOCOLS), true);
  assert.notEqual(webHost.PROTOCOL, companion.PROTOCOL);
  assert.equal(webHost.PINNED_EXTENSION_ORIGIN, PIN);
  assert.doesNotMatch(hostSource, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(sidebarSource, /postMessage\([^)]*,\s*["']\*["']/);
  assert.doesNotMatch(appSource, /addEventListener\(\s*["']message["']/);
  assert.equal(webHost.TYPES.OPEN_DETAILS, "open_details");
  assert.equal(bridge.WEB_TYPES.OPEN_DETAILS, "open_details");
  assert.doesNotMatch(hostSource, /framenest\.companion\.v1/);
});

test("WEB_READY is sent only to the pinned extension origin when framed", () => {
  const posted = [];
  const iframeWindow = {
    parent: {},
    addEventListener() {},
    postMessage() {},
  };
  iframeWindow.parent.postMessage = function postMessage(message, targetOrigin) {
    posted.push({ message, targetOrigin });
  };
  const host = webHost.createHost({ window: iframeWindow, parent: iframeWindow.parent });
  assert.equal(host.isHosted(), false);
  assert.equal(posted.length, 1);
  assert.equal(posted[0].targetOrigin, PIN);
  assert.equal(posted[0].message.v, webHost.PROTOCOL);
  assert.equal(posted[0].message.type, webHost.TYPES.WEB_READY);
});

test("a same-window page does not announce WEB_READY or become hosted", () => {
  const posted = [];
  const page = {
    parent: null,
    addEventListener() {},
  };
  page.parent = page;
  page.parent.postMessage = function postMessage(message, targetOrigin) {
    posted.push({ message, targetOrigin });
  };
  const host = webHost.createHost({ window: page, parent: page });
  assert.equal(host.isHosted(), false);
  assert.equal(posted.length, 0);
  for (const v of [RETIRED_WEB_PROTOCOL, CURRENT_WEB_PROTOCOL]) {
    host.handleMessage({
      origin: PIN,
      source: page,
      data: { v, type: webHost.TYPES.HOST_HELLO },
    });
    assert.equal(host.isHosted(), false);
    assert.equal(posted.length, 0);
  }
});

test("spoofed https parent cannot set hosted without the pinned extension origin", () => {
  const posted = [];
  const parentWindow = {
    postMessage(message, targetOrigin) {
      posted.push({ message, targetOrigin });
    },
  };
  const iframeWindow = {
    parent: parentWindow,
    addEventListener() {},
  };
  const host = webHost.createHost({ window: iframeWindow, parent: parentWindow });
  host.handleMessage({
    origin: "https://evil.example",
    source: parentWindow,
    data: { v: webHost.PROTOCOL, type: webHost.TYPES.HOST_HELLO },
  });
  assert.equal(host.isHosted(), false);
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: { v: webHost.PROTOCOL, type: webHost.TYPES.HOST_HELLO },
  });
  assert.equal(host.isHosted(), true);
  assert.equal(posted.some((entry) => entry.message.type === webHost.TYPES.HOST_ACK), true);
  posted.forEach((entry) => {
    assert.equal(entry.targetOrigin, PIN);
  });
});

test("ATTACH_REQUEST rejects non-UUIDs and forwards only catalog ids", async () => {
  const posted = [];
  const parentWindow = {
    postMessage(message, targetOrigin) {
      posted.push({ message, targetOrigin });
    },
  };
  const iframeWindow = {
    parent: parentWindow,
    addEventListener() {},
  };
  const host = webHost.createHost({ window: iframeWindow, parent: parentWindow });
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: { v: webHost.PROTOCOL, type: webHost.TYPES.HOST_HELLO },
  });
  const rejected = await host.attach("https://evil.example/media.bin", "not-a-uuid");
  assert.equal(rejected.ok, false);
  assert.equal(rejected.error, "invalid_attach");
  const attachPosts = posted.filter((entry) => entry.message.type === webHost.TYPES.ATTACH_REQUEST);
  assert.equal(attachPosts.length, 0);

  const mediaId = "11111111-1111-4111-8111-111111111111";
  const locationId = "22222222-2222-4222-8222-222222222222";
  const pending = host.attach(mediaId, locationId);
  const request = posted.filter((entry) => entry.message.type === webHost.TYPES.ATTACH_REQUEST).pop();
  assert.ok(request);
  assert.deepEqual(request.message.payload, { mediaId, locationId });
  assert.equal("url" in request.message.payload, false);
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: {
      v: webHost.PROTOCOL,
      type: webHost.TYPES.ATTACH_RESULT,
      payload: { ok: false, error: "composer_unbound" },
    },
  });
  const result = await pending;
  assert.equal(result.ok, false);
  assert.equal(result.error, "composer_unbound");
});

test("shell accepts only the framed stored origin and UUID attach ids", () => {
  const bridge = loadSidebarBridge();
  const iframeWindow = {};
  const otherWindow = {};
  assert.equal(
    bridge.acceptIncomingWebMessage(
      {
        source: otherWindow,
        origin: ORIGIN,
        data: { v: bridge.WEB_PROTOCOL, type: bridge.WEB_TYPES.WEB_READY },
      },
      iframeWindow,
      ORIGIN
    ),
    null
  );
  assert.equal(
    bridge.acceptIncomingWebMessage(
      {
        source: iframeWindow,
        origin: "https://evil.example",
        data: { v: bridge.WEB_PROTOCOL, type: bridge.WEB_TYPES.WEB_READY },
      },
      iframeWindow,
      ORIGIN
    ),
    null
  );
  assert.equal(
    bridge.acceptIncomingWebMessage(
      {
        source: iframeWindow,
        origin: PIN,
        data: { v: bridge.WEB_PROTOCOL, type: bridge.WEB_TYPES.WEB_READY },
      },
      iframeWindow,
      PIN
    ),
    null
  );
  const accepted = bridge.acceptIncomingWebMessage(
    {
      source: iframeWindow,
      origin: ORIGIN,
      data: { v: bridge.WEB_PROTOCOL, type: bridge.WEB_TYPES.WEB_READY },
    },
    iframeWindow,
    ORIGIN
  );
  assert.ok(accepted);
  assert.equal(accepted.type, bridge.WEB_TYPES.WEB_READY);
  assert.equal(
    bridge.attachIdsFromWebRequest({
      payload: { mediaId: "https://evil.example", locationId: "x", url: "https://evil.example/file" },
    }),
    null
  );
  const ids = bridge.attachIdsFromWebRequest({
    payload: {
      mediaId: "11111111-1111-4111-8111-111111111111",
      locationId: "22222222-2222-4222-8222-222222222222",
      url: "https://evil.example/file",
    },
  });
  assert.equal(ids.mediaId, "11111111-1111-4111-8111-111111111111");
  assert.equal(ids.locationId, "22222222-2222-4222-8222-222222222222");
  assert.equal(Object.prototype.hasOwnProperty.call(ids, "url"), false);
  assert.match(sidebarSource, /TYPES\.ATTACH_BEGIN/);
  assert.doesNotMatch(sidebarSource, /payload\.url/);
});

test("the extension refuses the retired companion web protocol", () => {
  const bridge = loadSidebarBridge();
  assert.equal(bridge.WEB_PROTOCOL, CURRENT_WEB_PROTOCOL);
  assert.equal(Object.hasOwn(bridge, "RETIRED_WEB_PROTOCOL"), false);
  assert.deepEqual(Array.from(bridge.ACCEPTED_WEB_PROTOCOLS), [
    CURRENT_WEB_PROTOCOL,
  ]);
  const iframeWindow = {};
  const accepted = bridge.acceptIncomingWebMessage(
    webMessage(iframeWindow, ORIGIN, RETIRED_WEB_PROTOCOL, bridge.WEB_TYPES.WEB_READY),
    iframeWindow,
    ORIGIN
  );
  assert.equal(accepted, null);
  assert.equal(bridge.acceptCompanionWebProtocol(RETIRED_WEB_PROTOCOL), false);
});

test("the extension accepts the canonical companion web protocol emitted by the host", () => {
  const bridge = loadSidebarBridge();
  assert.equal(bridge.CURRENT_WEB_PROTOCOL, CURRENT_WEB_PROTOCOL);
  const iframeWindow = {};
  const accepted = bridge.acceptIncomingWebMessage(
    webMessage(iframeWindow, ORIGIN, CURRENT_WEB_PROTOCOL, bridge.WEB_TYPES.WEB_READY),
    iframeWindow,
    ORIGIN
  );
  assert.ok(accepted);
  assert.equal(accepted.v, CURRENT_WEB_PROTOCOL);
  assert.equal(accepted.type, bridge.WEB_TYPES.WEB_READY);
  assert.equal(bridge.acceptCompanionWebProtocol(CURRENT_WEB_PROTOCOL), true);
});

test("any other companion web protocol spelling is refused with the existing shape", () => {
  const bridge = loadSidebarBridge();
  const iframeWindow = {};
  REFUSED_WEB_PROTOCOLS.forEach((value) => {
    assert.equal(
      bridge.acceptIncomingWebMessage(
        webMessage(iframeWindow, ORIGIN, value, bridge.WEB_TYPES.WEB_READY),
        iframeWindow,
        ORIGIN
      ),
      null,
      "refused " + JSON.stringify(value)
    );
    assert.equal(bridge.acceptCompanionWebProtocol(value), false, "refused " + String(value));
  });
});

test("every host send site emits canonical messages accepted by the extension", async (t) => {
  const { host, posted, send } = createHostHarness();
  send(CURRENT_WEB_PROTOCOL, "host_hello");
  const pending = host.attach(MEDIA_ID, LOCATION_ID);
  send(CURRENT_WEB_PROTOCOL, "attach_result", { ok: true });
  assert.deepEqual(await pending, { ok: true, error: undefined });
  assert.deepEqual(posted.map(({ message }) => message.type), [
    "web_ready", "host_ack", "attach_request",
  ]);
  t.diagnostic(JSON.stringify(posted.map(({ message }) => ({ type: message.type, v: message.v }))));
  const bridge = loadSidebarBridge();
  const iframeWindow = {};
  for (const { message, targetOrigin } of posted) {
    await t.test(message.type, () => {
      assert.equal(message.v, CURRENT_WEB_PROTOCOL);
      assert.equal(targetOrigin, PIN);
      const accepted = bridge.acceptIncomingWebMessage(
        { source: iframeWindow, origin: ORIGIN, data: message }, iframeWindow, ORIGIN
      );
      assert.equal(accepted, message);
      if (message.type === "attach_request") {
        assert.deepEqual(message.payload, { mediaId: MEDIA_ID, locationId: LOCATION_ID });
        assert.equal(bridge.attachIdsFromWebRequest(accepted).mediaId, MEDIA_ID);
      }
    });
  }
});

test("extension open_details emits the canonical spelling and reaches the host", () => {
  const inbox = loadSidebarBridgeContext().KronikaReviewInbox;
  const message = inbox.openDetailsMessage(MEDIA_ID);
  assert.ok(message);
  assert.equal(message.v, CURRENT_WEB_PROTOCOL);
  assert.equal(message.type, "open_details");
  assert.equal(inbox.openDetailsMessage("not-a-uuid"), null);
  const { host, send } = createHostHarness();
  const opened = [];
  host.onOpenDetails((mediaId) => opened.push(mediaId));
  send(message.v, message.type, message.payload);
  assert.deepEqual(opened, [MEDIA_ID]);
});

for (const protocol of [CURRENT_WEB_PROTOCOL]) {
  test(`host accepts HOST_HELLO with ${protocol} and contains listener failures`, () => {
    const { host, posted, send } = createHostHarness();
    const changes = [];
    host.onHostedChange(() => { throw new Error("synthetic listener failure"); });
    host.onHostedChange((hosted) => changes.push(hosted));
    send(protocol, "host_hello");
    assert.equal(host.isHosted(), true);
    assert.deepEqual(changes, [true]);
    send(protocol, "host_hello");
    assert.deepEqual(changes, [true], "a repeated hello does not repeat the change notification");
    assert.deepEqual(posted.map(({ message }) => message.type), ["web_ready", "host_ack", "host_ack"]);
  });

  test(`host accepts ATTACH_RESULT with ${protocol} without coercing payloads`, async () => {
    const { host, posted, send } = createHostHarness();
    send(CURRENT_WEB_PROTOCOL, "host_hello");
    for (const ids of [[MEDIA_ID, "not-a-uuid"], ["not-a-uuid", LOCATION_ID]]) {
      assert.deepEqual(await host.attach(...ids), { ok: false, error: "invalid_attach" });
      assert.equal(posted.length, 2, "neither invalid identifier emits an attach request");
    }
    for (const [payload, expected] of [
      [{ ok: true }, { ok: true, error: undefined }],
      [{ ok: false, error: "composer_unbound" }, { ok: false, error: "composer_unbound" }],
      [{ ok: "true", error: 42 }, { ok: false, error: undefined }],
      [null, { ok: false, error: undefined }],
    ]) {
      let result;
      const pending = host.attach(MEDIA_ID, LOCATION_ID).then((value) => { result = value; });
      send(protocol, "attach_result", payload);
      await Promise.resolve();
      assert.deepEqual(result, expected, "the accepted result resolves the pending attach");
      await pending;
    }
    assert.equal(send(protocol, "attach_result", { ok: true }), undefined, "no waiter is harmless");
  });

  test(`host accepts OPEN_DETAILS with ${protocol} and contains listener failures`, () => {
    const { host, send } = createHostHarness();
    const opened = [];
    host.onOpenDetails(() => { throw new Error("synthetic listener failure"); });
    host.onOpenDetails((mediaId) => opened.push(mediaId));
    for (const payload of [null, "invalid", {}, { mediaId: "not-a-uuid" }]) {
      assert.equal(send(protocol, "open_details", payload), undefined);
      assert.deepEqual(opened, []);
    }
    send(protocol, "open_details", { mediaId: MEDIA_ID });
    assert.deepEqual(opened, [MEDIA_ID]);
  });

  test(`host preserves origin, source and message-shape refusal with ${protocol}`, async () => {
    for (const overrides of [
      { origin: "https://evil.example" },
      { origin: "chrome-extension://untrusted" },
      { source: {} },
      { source: undefined },
      { data: null },
      { data: "invalid" },
      { data: { v: protocol, type: 1 } },
      { data: { v: protocol, type: "unknown" } },
    ]) {
      await assertHostRefuses(protocol, overrides);
    }
  });
}

async function assertHostRefuses(protocol, overrides) {
  const { host, posted, send } = createHostHarness();
  const opened = [];
  const changes = [];
  host.onOpenDetails((mediaId) => opened.push(mediaId));
  host.onHostedChange((hosted) => changes.push(hosted));
  assert.equal(send(protocol, "host_hello", undefined, overrides), undefined);
  assert.equal(host.isHosted(), false);
  assert.deepEqual(changes, []);
  assert.equal(posted.length, 1, "refused hello emits no acknowledgement");
  assert.deepEqual(await host.attach(MEDIA_ID, LOCATION_ID), { ok: false, error: "not_hosted" });

  // A valid control establishes a waiter; a refused result must leave it intact.
  send(CURRENT_WEB_PROTOCOL, "host_hello");
  assert.equal(host.isHosted(), true);
  let settled = false;
  const pending = host.attach(MEDIA_ID, LOCATION_ID).then((result) => {
    settled = true;
    return result;
  });
  assert.equal(send(protocol, "attach_result", { ok: true }, overrides), undefined);
  assert.equal(send(protocol, "open_details", { mediaId: MEDIA_ID }, overrides), undefined);
  await Promise.resolve();
  assert.equal(settled, false, "refused result does not consume the waiter");
  assert.deepEqual(opened, [], "refused details does not notify listeners");
  assert.equal(posted.length, 3, "refused messages emit nothing");
  send(CURRENT_WEB_PROTOCOL, "attach_result", { ok: false, error: "composer_unbound" });
  assert.deepEqual(await pending, { ok: false, error: "composer_unbound" });
  send(CURRENT_WEB_PROTOCOL, "open_details", { mediaId: MEDIA_ID });
  assert.deepEqual(opened, [MEDIA_ID], "a valid details control still opens");
}

test("host refuses the retired protocol, every near-miss and unknown on all three inbound branches", async () => {
  for (const protocol of REFUSED_WEB_PROTOCOLS) {
    await assertHostRefuses(protocol);
  }
});

test("open_details opens hosted media-details from the pinned extension only", () => {
  const opened = [];
  const posted = [];
  const parentWindow = {
    postMessage(message, targetOrigin) {
      posted.push({ message, targetOrigin });
    },
  };
  const iframeWindow = {
    parent: parentWindow,
    addEventListener() {},
  };
  const host = webHost.createHost({
    window: iframeWindow,
    parent: parentWindow,
    onOpenDetails(mediaId) {
      opened.push(mediaId);
    },
  });
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: { v: webHost.PROTOCOL, type: webHost.TYPES.HOST_HELLO },
  });
  const mediaId = "11111111-1111-4111-8111-111111111111";
  host.handleMessage({
    origin: "https://evil.example",
    source: parentWindow,
    data: {
      v: webHost.PROTOCOL,
      type: webHost.TYPES.OPEN_DETAILS,
      payload: { mediaId: mediaId },
    },
  });
  assert.deepEqual(opened, []);
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: {
      v: webHost.PROTOCOL,
      type: webHost.TYPES.OPEN_DETAILS,
      payload: { mediaId: "not-a-uuid" },
    },
  });
  assert.deepEqual(opened, []);
  host.handleMessage({
    origin: PIN,
    source: parentWindow,
    data: {
      v: webHost.PROTOCOL,
      type: webHost.TYPES.OPEN_DETAILS,
      payload: { mediaId: mediaId },
    },
  });
  assert.deepEqual(opened, [mediaId]);
  assert.equal(webHost.TYPES.OPEN_DETAILS, "open_details");
  const bridge = loadSidebarBridge();
  assert.equal(bridge.WEB_TYPES.OPEN_DETAILS, webHost.TYPES.OPEN_DETAILS);
  assert.match(appSource, /onOpenDetails/);
  assert.match(appSource, /openDetailsDialog\(\s*\{\s*media_id:\s*mediaId\s*\}/);
  posted.forEach((entry) => {
    assert.equal(entry.targetOrigin, PIN);
  });
});

test("sidebar Attached follows ATTACH_BEGIN result.ok", () => {
  const handle = sidebarSource.match(/async function handleAttachRequest\([\s\S]*?\n  \}/);
  assert.ok(handle, "handleAttachRequest()");
  assert.match(handle[0], /TYPES\.ATTACH_BEGIN/);
  assert.match(handle[0], /const ok = Boolean\(result && result\.ok\)/);
  assert.match(handle[0], /else if \(ok\) \{\s*setText\(shellStatus, "Attached"\);/);
  assert.match(handle[0], /error === "composer_unbound"/);
});

test("handshake timeout copy does not claim framing failed when the iframe loaded", () => {
  const bridge = loadSidebarBridge();
  assert.equal(bridge.handshakeTimeoutCopy(false), bridge.framingFailureCopy());
  assert.equal(bridge.handshakeTimeoutCopy(true), bridge.companionHostMissingCopy());
  assert.match(bridge.framingFailureCopy(), /did not load in this panel/);
  assert.match(bridge.companionHostMissingCopy(), /cannot host companion Attach yet/);
  assert.doesNotMatch(bridge.framingFailureCopy(), /could not be framed/i);
  assert.doesNotMatch(bridge.companionHostMissingCopy(), /could not be framed/i);
  assert.doesNotMatch(bridge.handshakeTimeoutCopy(true), /could not be framed/i);
  assert.doesNotMatch(sidebarSource, /could not be framed/);
  assert.match(sidebarSource, /frameLoaded = true/);
  assert.match(sidebarSource, /handshakeTimeoutCopy\(loaded\)/);
});

test("hosted Details hide Analyze by AI and keep suggestion chrome; standalone Analyze stays gated", () => {
  const start = appSource.indexOf("function updateMetadataControls(");
  assert.ok(start >= 0);
  const bodyStart = appSource.indexOf("{", start);
  let depth = 0;
  let end = bodyStart;
  for (let index = bodyStart; index < appSource.length; index += 1) {
    if (appSource[index] === "{") {
      depth += 1;
    }
    if (appSource[index] === "}") {
      depth -= 1;
      if (depth === 0) {
        end = index + 1;
        break;
      }
    }
  }
  const controls = appSource.slice(start, end);
  assert.match(controls, /identityAllowsAiAnalyze\(\)/);
  assert.match(controls, /identityAllowsAiSuggestionChrome\(\)/);
  assert.doesNotMatch(controls, /identityAllowsAiSuggestionLoadChrome\(\)/);
  assert.doesNotMatch(controls, /identityAllowsAiSuggestionsChrome\(\)/);
  assert.doesNotMatch(controls, /searchParams|URLSearchParams|hosted=/);
  const analyzeStart = appSource.indexOf("function identityAllowsAiAnalyze(");
  assert.ok(analyzeStart >= 0);
  const analyze = appSource.slice(analyzeStart, analyzeStart + 400);
  assert.match(analyze, /companionWebHosted\(\)/);
});
