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
  let start = -1;
  for (const marker of markers) {
    start = source.indexOf(marker);
    if (start !== -1) break;
  }
  assert.notEqual(start, -1, `missing production function ${name}`);
  const headerOpen = source.indexOf("(", start);
  assert.notEqual(headerOpen, -1, `missing parameter list for ${name}`);
  let depth = 0;
  let headerClose = -1;
  for (let index = headerOpen; index < source.length; index += 1) {
    const character = source[index];
    if (character === "(") depth += 1;
    else if (character === ")") {
      depth -= 1;
      if (depth === 0) {
        headerClose = index;
        break;
      }
    }
  }
  assert.notEqual(headerClose, -1, `unterminated parameter list for ${name}`);
  const bodyOpen = source.indexOf("{", headerClose);
  assert.notEqual(bodyOpen, -1, `missing body for ${name}`);
  depth = 0;
  for (let index = bodyOpen; index < source.length; index += 1) {
    const character = source[index];
    if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production function ${name}`);
}

function response(payload, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => payload,
  };
}

function createIdentityControlMock() {
  const attrs = { "aria-label": "Tailscale identity status" };
  const classes = new Set(["status-button", "identity-badge"]);
  return {
    hidden: true,
    textContent: "",
    title: "Open Tailscale identity status",
    classList: {
      add(name) {
        classes.add(name);
      },
      remove(...names) {
        for (const name of names) classes.delete(name);
      },
      contains(name) {
        return classes.has(name);
      },
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    removeAttribute(name) {
      delete attrs[name];
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name) ? attrs[name] : null;
    },
  };
}

function createAdminOnlyRows() {
  return [
    { hidden: true, name: "connection" },
    { hidden: true, name: "hostname" },
    { hidden: true, name: "url" },
    { hidden: true, name: "provenance" },
  ];
}

function createIdentityHarness(fetchImpl) {
  const uploadOpenButton = { hidden: false };
  const detailsEditButton = { hidden: false };
  const adminMediaOpenButton = { hidden: false };
  const adminMediaBrowser = { hidden: true };
  const workspaceMediaOpenButton = { hidden: false };
  const workspaceMediaBrowser = { hidden: true };
  const identityBadge = createIdentityControlMock();
  const identityStatusName = { textContent: "" };
  const statusTailscaleAdminOnlyRows = createAdminOnlyRows();
  const context = {
    console,
    fetch: fetchImpl,
    Set,
    Object,
    Array,
    Boolean,
    uploadOpenButton,
    detailsEditButton,
    adminMediaOpenButton,
    adminMediaBrowser,
    workspaceMediaOpenButton,
    workspaceMediaBrowser,
    identityBadge,
    identityStatusName,
    statusTailscaleAdminOnlyRows,
    metadataControlCalls: 0,
  };
  context.updateMetadataControls = () => {
    context.metadataControlCalls += 1;
  };
  context.closeAdminMediaBrowser = () => {
    context.adminMediaBrowser.hidden = true;
  };
  context.closeWorkspaceMediaBrowser = () => {
    context.workspaceMediaBrowser.hidden = true;
  };
  context.globalThis = context;
  vm.createContext(context);
  const prelude = [
    "let identityState = {",
    "  resolved: false,",
    "  available: false,",
    '  audience: "",',
    '  login: "",',
    '  displayName: "",',
    '  role: "",',
    '  provenance: "",',
    "  capabilities: new Set(),",
    "};",
    'const AUDIENCE_ENDPOINT = "/api/audience/me";',
    extractFunction(APP_SOURCE, "identityHasCapability"),
    extractFunction(APP_SOURCE, "isPublicPublishedAudience"),
    extractFunction(APP_SOURCE, "isWorkspaceAudience"),
    extractFunction(APP_SOURCE, "identityAllowsAdminWorkflow"),
    extractFunction(APP_SOURCE, "identityAllowsYouTubeClaim"),
    extractFunction(APP_SOURCE, "identityAllowsYouTubeRequest"),
    extractFunction(APP_SOURCE, "identityAllowsCoverEditing"),
    extractFunction(APP_SOURCE, "identityAllowsMetadataEdit"),
    extractFunction(APP_SOURCE, "identityAllowsWorkspaceMedia"),
    extractFunction(APP_SOURCE, "resetAudienceState"),
    extractFunction(APP_SOURCE, "applyAudienceDocument"),
    extractFunction(APP_SOURCE, "framenestMutationHeaders"),
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
    extractFunction(APP_SOURCE, "applyTailscalePanelDensity"),
    extractFunction(APP_SOURCE, "renderIdentityBadge"),
    extractFunction(APP_SOURCE, "loadIdentity"),
  ].join("\n");
  vm.runInContext(prelude, context);
  return context;
}

function createStatusTabHarness(fetchImpl, locationStub, role = "admin") {
  const makeTab = (selected) => ({
    classList: {
      _active: selected,
      toggle(name, force) {
        if (name === "settings-dialog__tab--active") this._active = force;
      },
    },
    attrs: { "aria-selected": String(selected) },
    tabIndex: selected ? 0 : -1,
    setAttribute(name, value) {
      this.attrs[name] = String(value);
    },
    getAttribute(name) {
      return this.attrs[name] || null;
    },
    focus() {
      this.focused = true;
    },
    focused: false,
  });
  const makePanel = (hidden) => ({
    hidden,
    focus() {
      this.focused = true;
    },
    focused: false,
  });
  const fields = {};
  for (const id of [
    "statusTailscaleConnection",
    "statusTailscaleAccessMethod",
    "statusTailscaleHostname",
    "statusTailscaleUrl",
    "statusTailscaleHttps",
    "statusTailscaleLogin",
    "statusTailscaleDisplayName",
    "statusTailscaleRole",
    "statusTailscaleProvenance",
    "statusCloudServer",
    "statusCloudConnection",
    "statusCloudRemote",
  ]) {
    fields[id] = { textContent: "" };
  }
  const statusCloudRemoteRow = { hidden: true };
  const statusTailscaleAdminOnlyRows = createAdminOnlyRows();
  const statusDialog = {
    open: false,
    showModal() {
      this.open = true;
      this.setAttribute("open", "");
    },
    close() {
      this.open = false;
      this.removeAttribute("open");
    },
    attrs: {},
    setAttribute(name, value) {
      this.attrs[name] = value;
    },
    removeAttribute(name) {
      delete this.attrs[name];
    },
    hasAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name);
    },
  };
  const context = {
    console,
    fetch: fetchImpl,
    Set,
    Object,
    Array,
    Boolean,
    String,
    Math,
    location: locationStub,
    document: { activeElement: null },
    statusTabAi: makeTab(true),
    statusTabCloud: makeTab(false),
    statusTabTailscale: makeTab(false),
    statusPanelAi: makePanel(false),
    statusPanelCloud: makePanel(true),
    statusPanelTailscale: makePanel(true),
    statusDialog,
    statusCloudRemoteRow,
    statusTailscaleAdminOnlyRows,
    lastFocusedElementBeforeStatus: null,
    lastCloudStatusPayload: null,
    identityState: {
      resolved: true,
      available: true,
      login: role === "admin" ? "aecrypto@gmail.com" : "user@example.com",
      displayName: role === "admin" ? "ae crypto" : "Reader",
      role,
      provenance: "tailscale-serve",
      capabilities: new Set(["gallery.read"]),
    },
    aiStatusButton: { focus() {} },
    ...fields,
  };
  context.globalThis = context;
  vm.createContext(context);
  const prelude = [
    'const CLOUD_STATUS_ENDPOINT = "/api/status/cloud";',
    "let lastCloudStatusPayload = null;",
    "let lastFocusedElementBeforeStatus = null;",
    extractFunction(APP_SOURCE, "identityRoleLabel"),
    extractFunction(APP_SOURCE, "applyTailscalePanelDensity"),
    extractFunction(APP_SOURCE, "renderTailscaleConnectionFields"),
    extractFunction(APP_SOURCE, "renderTailscaleIdentityFields"),
    extractFunction(APP_SOURCE, "renderCloudStatus"),
    extractFunction(APP_SOURCE, "renderTailscaleStatus"),
    extractFunction(APP_SOURCE, "loadCloudStatus"),
    extractFunction(APP_SOURCE, "loadTailscaleStatus"),
    "async function loadAiCapability() { contextAiRefreshCount += 1; }",
    "let contextAiRefreshCount = 0;",
    extractFunction(APP_SOURCE, "setActiveStatusTab"),
    extractFunction(APP_SOURCE, "openStatusDialog"),
  ].join("\n");
  vm.runInContext(prelude, context);
  return context;
}

test("mutation helper always injects the FrameNest mutation header", () => {
  const context = createIdentityHarness(async () => response({}, 404));
  const merged = vm.runInContext(
    'framenestMutationHeaders({ Accept: "application/json", "Upload-Offset": "7" })',
    context,
  );
  assert.equal(merged["X-FrameNest-Request"], "1");
  assert.equal(merged.Accept, "application/json");
  assert.equal(merged["Upload-Offset"], "7");
});

test("Class 1: the web shell mutation helper injects both header spellings", () => {
  const context = createIdentityHarness(async () => response({}, 404));
  const merged = vm.runInContext(
    'framenestMutationHeaders({ Accept: "application/json", "Upload-Offset": "7" })',
    context,
  );
  assert.equal(merged["X-FrameNest-Request"], "1");
  assert.equal(merged["X-Kronika-Request"], "1");
  assert.equal(merged.Accept, "application/json");
  assert.equal(merged["Upload-Offset"], "7");

  const hostile = vm.runInContext(
    'framenestMutationHeaders({ "X-Kronika-Request": "0", "X-FrameNest-Request": "0" })',
    context,
  );
  assert.equal(
    hostile["X-FrameNest-Request"],
    "0",
    "a caller may still override the retired spelling, as before this cut"
  );
  assert.equal(
    hostile["X-Kronika-Request"],
    "1",
    "the current spelling is applied after the merge so a caller cannot weaken the gate"
  );

  const bare = vm.runInContext("framenestMutationHeaders()", context);
  assert.equal(bare["X-FrameNest-Request"], "1");
  assert.equal(bare["X-Kronika-Request"], "1");

  assert.ok(
    APP_SOURCE.includes('Object.assign({ "X-FrameNest-Request": "1" }, headers)'),
    "the retired spelling stays pinned in the shared helper"
  );
  assert.equal((APP_SOURCE.match(/"X-FrameNest-Request"/g) || []).length, 1);
  assert.equal((APP_SOURCE.match(/X-Kronika-Request/g) || []).length, 1);
});

test("Order independence: the shell request is authorised by the pre-C1 gate and by the C1 gate", () => {
  const context = createIdentityHarness(async () => response({}, 404));
  const sent = vm.runInContext(
    'framenestMutationHeaders({ Accept: "application/json" })',
    context,
  );

  // Modelled from tailscale_ingress before the dual-read cut: only its own
  // spelling is compared and an unknown header is ignored.
  function preC1Gate(headers) {
    const names = Object.keys(headers).map((name) => name.toLowerCase());
    return !names.includes("x-kronika-request") || headers["X-Kronika-Request"] === "1";
  }

  // Modelled from tailscale_ingress after the dual-read cut: either spelling
  // authorises, and both must be 1 when both are present.
  function c1Gate(headers) {
    const present = ["X-FrameNest-Request", "X-Kronika-Request"].filter(
      (name) => Object.prototype.hasOwnProperty.call(headers, name),
    );
    if (!present.length) {
      return false;
    }
    return present.every((name) => headers[name] === "1");
  }

  assert.equal(preC1Gate(sent), true, "a pre-C1 server still authorises this shell");
  assert.equal(c1Gate(sent), true, "a C1 server authorises this shell");
  assert.equal(
    preC1Gate({ "X-FrameNest-Request": "1" }),
    true,
    "the pre-cut shell stays authorised against a pre-C1 server"
  );
  assert.equal(c1Gate({ "X-FrameNest-Request": "1" }), true, "the pre-cut shell stays authorised against C1");
  assert.equal(c1Gate(sent), preC1Gate(sent), "the two gates agree on this shell");
});

test("every unsafe fetch call site sends the mutation header", () => {
  const mutationSites = APP_SOURCE.match(/method: "(?:POST|PUT|PATCH|DELETE)"/g) || [];
  const wrappedSites = APP_SOURCE.match(/headers: framenestMutationHeaders\(/g) || [];
  assert.ok(mutationSites.length > 0);
  assert.equal(mutationSites.length, wrappedSites.length);
  assert.equal((APP_SOURCE.match(/"X-FrameNest-Request"/g) || []).length, 1);
});

test("identity bootstrap runs first and gates the initial catalog load", () => {
  const bootstrap = APP_SOURCE.slice(APP_SOURCE.indexOf("const identityReady = loadIdentity();"));
  assert.ok(bootstrap.startsWith("const identityReady = loadIdentity();\nidentityReady.then(() => {"));
  assert.match(bootstrap, /identityReady\.then\(\(\) => \{/);
  assert.ok(bootstrap.includes("loadCatalog();"));
  assert.ok(bootstrap.includes("isPublicPublishedAudience()"));
});

test("identity fetch never sends the mutation header", () => {
  const loadIdentityBody = extractFunction(APP_SOURCE, "loadIdentity");
  assert.ok(!loadIdentityBody.includes("X-FrameNest-Request"));
  assert.ok(loadIdentityBody.includes('headers: { Accept: "application/json" }'));
});

test("header controls are compact icon-only with distinct identity name pill", () => {
  const headerStart = INDEX_SOURCE.indexOf('class="app-header"');
  const headerEnd = INDEX_SOURCE.indexOf("</header>", headerStart);
  const header = INDEX_SOURCE.slice(headerStart, headerEnd);
  assert.match(header, /id="upload-open-button"/);
  assert.match(header, /aria-label="Upload media"/);
  assert.match(header, /status-button--icon/);
  assert.match(header, />↑</);
  assert.ok(!/status-button__label">Upload</.test(header));
  assert.ok(!/>Local</.test(header));
  assert.match(header, /☁️/);
  assert.ok(!/status-button__label">Cloud</.test(header));
  assert.match(header, /🧠/);
  assert.ok(!/status-button__label">AI</.test(header));
  assert.ok(!/status-button__dot/.test(header));
  assert.match(header, /id="identity-status-name"/);
  assert.ok(!/identity-status-role/.test(header));
  assert.ok(!/identity-badge__sep/.test(header));
  assert.ok(STYLES_SOURCE.includes("grid-template-areas: \"brand search controls\""));
  assert.ok(STYLES_SOURCE.includes('"brand controls"'));
  assert.ok(STYLES_SOURCE.includes('"search search"'));
  assert.ok(STYLES_SOURCE.includes("@media (max-width: 900px)"));
  assert.ok(STYLES_SOURCE.includes("@media (max-width: 360px)"));
  assert.ok(STYLES_SOURCE.includes(".status-button--icon"));
  assert.ok(STYLES_SOURCE.includes(".status-button__glyph"));
  assert.ok(STYLES_SOURCE.includes("max-width: min(88px, 26vw)"));
  assert.ok(!/overflow-x:\s*hidden/.test(STYLES_SOURCE.match(/body\s*\{[^}]*\}/)?.[0] || ""));
  assert.ok(STYLES_SOURCE.includes("minmax(0, 1fr)"));
});

test("identity control opens Tailscale status tab in source wiring", () => {
  assert.ok(APP_SOURCE.includes('openStatusDialog("tailscale")'));
  assert.ok(APP_SOURCE.includes("if (identityBadge)"));
  assert.ok(APP_SOURCE.includes('setActiveStatusTab("tailscale")'));
  assert.ok(INDEX_SOURCE.includes('id="status-tab-tailscale"'));
  assert.ok(INDEX_SOURCE.includes('id="status-panel-tailscale"'));
  assert.ok(
    INDEX_SOURCE.indexOf('id="status-tab-cloud"') < INDEX_SOURCE.indexOf('id="status-tab-tailscale"'),
  );
});

test("privileged controls are gated by capabilities in source", () => {
  assert.ok(APP_SOURCE.includes('identityHasCapability("upload.submit")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("upload.manage")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("metadata.canonical.write")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("analysis.run")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("media.workspace.read")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("analysis.propose")'));
  assert.ok(APP_SOURCE.includes('identityHasCapability("metadata.alias.team.read")'));
  assert.ok(APP_SOURCE.includes("awaits administrator review"));
  assert.ok(APP_SOURCE.includes("clearStaleUploadRecoveryState"));
  assert.ok(APP_SOURCE.includes("submission expired or is unavailable"));
  assert.ok(APP_SOURCE.includes('identityHasCapability("metadata.alias.write")'));
  assert.ok(APP_SOURCE.includes("function identityAllowsMetadataEdit()"));
  const cardBody = extractFunction(APP_SOURCE, "renderCatalogCard");
  assert.ok(cardBody.includes("cardAiQuickActionEligible(item)"));
  assert.equal(cardBody.includes("cardNeedsMetadata(item)"), false);
  assert.ok(cardBody.includes("if (identityAllowsMetadataEdit()) {"));
  const eligibleBody = extractFunction(APP_SOURCE, "cardAiQuickActionEligible");
  const identityGateBody = extractFunction(APP_SOURCE, "identityAllowsCardAiQuickAction");
  assert.ok(eligibleBody.includes("identityAllowsCardAiQuickAction()"));
  assert.equal(eligibleBody.includes("identityHasCapability("), false);
  assert.ok(identityGateBody.includes("identityState.resolved"));
  assert.ok(identityGateBody.includes("identityState.available"));
  assert.ok(identityGateBody.includes('capabilities.has("analysis.run")'));
  assert.ok(identityGateBody.includes('capabilities.has("metadata.canonical.write")'));
  assert.ok(identityGateBody.includes("companionWebHosted()"));
});

test("hosted Details hide Analyze by AI and keep suggestion chrome", () => {
  const controls = extractFunction(APP_SOURCE, "updateMetadataControls");
  assert.ok(controls.includes("identityAllowsAiAnalyze()"));
  assert.ok(controls.includes("identityAllowsAiSuggestionChrome()"));
  assert.equal(controls.includes("identityAllowsAiSuggestionLoadChrome()"), false);
  assert.equal(controls.includes("identityAllowsAiSuggestionsChrome()"), false);
  assert.equal(controls.includes("URLSearchParams"), false);
  const analyze = extractFunction(APP_SOURCE, "identityAllowsAiAnalyze");
  assert.ok(analyze.includes("companionWebHosted()"));
  assert.ok(analyze.includes("analysis.run"));
  const suggestionChrome = extractFunction(APP_SOURCE, "identityAllowsAiSuggestionChrome");
  assert.equal(suggestionChrome.includes("companionWebHosted()"), false);
  assert.ok(suggestionChrome.includes("metadata.alias.write"));
});

test("gallery AI quick action fails closed for missing unresolved and ordinary identity", () => {
  const identityGateBody = extractFunction(APP_SOURCE, "identityAllowsCardAiQuickAction");
  assert.ok(identityGateBody.includes("identityState.resolved"));
  assert.ok(identityGateBody.includes("identityState.available"));
  assert.ok(identityGateBody.includes('capabilities.has("analysis.run")'));
  assert.ok(identityGateBody.includes('capabilities.has("metadata.canonical.write")'));
  assert.ok(identityGateBody.includes("companionWebHosted()"));
  assert.equal(identityGateBody.includes("identityHasCapability("), false);
  assert.ok(APP_SOURCE.includes("function identityAllowsCardAiQuickAction()"));
});

test("admin identity populates name-only badge and unlocks privileged controls", async () => {
  const context = createIdentityHarness(async (url) => {
    assert.equal(url, "/api/audience/me");
    return response({
      audience: "tailscale_workspace",
      identity: {
        login: "admin@example.com",
        display_name: "Admin User",
        role: "admin",
        provenance: "tailscale-serve",
      },
      capabilities: [
        "analysis.run",
        "gallery.read",
        "metadata.canonical.write",
        "media.workflow.read",
        "media.workspace.read",
        "upload.manage",
        "upload.submit",
      ],
    });
  });
  await vm.runInContext("loadIdentity()", context);
  const state = vm.runInContext("identityState", context);
  assert.equal(state.available, true);
  assert.equal(state.login, "admin@example.com");
  assert.equal(state.provenance, "tailscale-serve");
  assert.equal(context.identityBadge.hidden, false);
  assert.equal(context.identityStatusName.textContent, "Admin User");
  assert.match(context.identityBadge.getAttribute("aria-label"), /Signed in as Admin User/);
  assert.ok(!/Admin\./.test(context.identityBadge.getAttribute("aria-label").replace("Admin User", "")));
  assert.match(context.identityBadge.title, /Signed in as admin@example.com/);
  assert.equal(context.identityBadge.classList.contains("status-button--healthy"), false);
  assert.equal(context.uploadOpenButton.hidden, false);
  assert.equal(context.detailsEditButton.hidden, false);
  assert.equal(context.adminMediaOpenButton.hidden, false);
  assert.equal(context.workspaceMediaOpenButton.hidden, false);
  assert.equal(context.metadataControlCalls, 1);
  assert.equal(context.statusTailscaleAdminOnlyRows.every((row) => row.hidden === false), true);
});

test("ordinary user identity hides privileged controls and keeps admin Tailscale rows hidden", async () => {
  const context = createIdentityHarness(async () =>
    response({
      audience: "tailscale_workspace",
      identity: {
        login: "user@example.com",
        display_name: "Reader",
        role: "user",
        provenance: "tailscale-serve",
      },
      capabilities: [
        "gallery.read",
        "media.download",
        "media.original.read",
        "media.workspace.read",
        "upload.submit",
        "metadata.alias.write",
      ],
    }),
  );
  await vm.runInContext("loadIdentity()", context);
  assert.equal(context.identityBadge.hidden, false);
  assert.equal(context.identityStatusName.textContent, "Reader");
  assert.match(context.identityBadge.getAttribute("aria-label"), /Signed in as Reader/);
  assert.ok(!context.identityBadge.getAttribute("aria-label").includes("Admin"));
  assert.equal(context.uploadOpenButton.hidden, false);
  assert.equal(context.detailsEditButton.hidden, false);
  assert.equal(context.adminMediaOpenButton.hidden, true);
  assert.equal(context.workspaceMediaOpenButton.hidden, false);
  assert.equal(context.statusTailscaleAdminOnlyRows.every((row) => row.hidden === true), true);
  assert.equal(vm.runInContext('identityHasCapability("upload.submit")', context), true);
  assert.equal(vm.runInContext('identityHasCapability("upload.manage")', context), false);
  assert.equal(vm.runInContext('identityHasCapability("metadata.canonical.write")', context), false);
  assert.equal(vm.runInContext('identityHasCapability("metadata.alias.write")', context), true);
  assert.equal(vm.runInContext('identityHasCapability("gallery.read")', context), true);
});

test("ordinary user without upload.submit hides upload control", async () => {
  const context = createIdentityHarness(async () =>
    response({
      audience: "tailscale_workspace",
      identity: {
        login: "user@example.com",
        display_name: "Reader",
        role: "user",
        provenance: "tailscale-serve",
      },
      capabilities: ["gallery.read", "media.download", "media.original.read"],
    }),
  );
  await vm.runInContext("loadIdentity()", context);
  assert.equal(context.uploadOpenButton.hidden, true);
  assert.equal(vm.runInContext('identityHasCapability("upload.submit")', context), false);
});

test("denied identity fails closed and hides the badge", async () => {
  const context = createIdentityHarness(async () => response({}, 403));
  await vm.runInContext("loadIdentity()", context);
  const state = vm.runInContext("identityState", context);
  assert.equal(state.available, false);
  assert.equal(state.capabilities.size, 0);
  assert.equal(context.identityBadge.hidden, true);
  assert.equal(context.uploadOpenButton.hidden, true);
  assert.equal(context.detailsEditButton.hidden, true);
  assert.equal(context.adminMediaOpenButton.hidden, true);
  assert.equal(context.workspaceMediaOpenButton.hidden, true);
  assert.equal(context.statusTailscaleAdminOnlyRows.every((row) => row.hidden === true), true);
});

test("missing audience bootstrap exposes no capabilities", async () => {
  const context = createIdentityHarness(async () => response({}, 404));
  await vm.runInContext("loadIdentity()", context);
  const state = vm.runInContext("identityState", context);
  assert.equal(state.available, false);
  assert.equal(state.audience, "");
  assert.equal(vm.runInContext('identityHasCapability("upload.manage")', context), false);
  assert.equal(vm.runInContext('identityHasCapability("gallery.read")', context), false);
  assert.equal(context.uploadOpenButton.hidden, true);
  assert.equal(context.adminMediaOpenButton.hidden, true);
  assert.equal(context.workspaceMediaOpenButton.hidden, true);
  assert.equal(context.identityBadge.hidden, true);
});

test("identity network failure exposes no capabilities", async () => {
  const context = createIdentityHarness(async () => {
    throw new Error("network down");
  });
  await vm.runInContext("loadIdentity()", context);
  const state = vm.runInContext("identityState", context);
  assert.equal(state.resolved, true);
  assert.equal(state.available, false);
  assert.equal(vm.runInContext('identityHasCapability("analysis.run")', context), false);
  assert.equal(context.adminMediaOpenButton.hidden, true);
  assert.equal(context.workspaceMediaOpenButton.hidden, true);
});

test("public published audience exposes only public read capabilities", async () => {
  const context = createIdentityHarness(async () =>
    response({
      audience: "public_published",
      identity: null,
      capabilities: ["gallery.read", "media.original.read"],
    }),
  );
  await vm.runInContext("loadIdentity()", context);
  const state = vm.runInContext("identityState", context);
  assert.equal(state.audience, "public_published");
  assert.equal(state.available, false);
  assert.equal(state.login, "");
  assert.equal(vm.runInContext('identityHasCapability("gallery.read")', context), true);
  assert.equal(vm.runInContext('identityHasCapability("media.original.read")', context), true);
  assert.equal(vm.runInContext('identityHasCapability("upload.submit")', context), false);
  assert.equal(vm.runInContext('identityHasCapability("metadata.canonical.write")', context), false);
  assert.equal(context.uploadOpenButton.hidden, true);
  assert.equal(context.detailsEditButton.hidden, true);
  assert.equal(context.adminMediaOpenButton.hidden, true);
  assert.equal(context.workspaceMediaOpenButton.hidden, true);
  assert.equal(context.identityBadge.hidden, true);
});

test("admin Tailscale panel shows diagnostic rows", async () => {
  const context = createStatusTabHarness(
    async (url) => {
      assert.equal(url, "/api/status/cloud");
      return response({
        server: "connected",
        connection: "tailscale",
        remote_access: "https://example.ts.net",
      });
    },
    {
      hostname: "example.ts.net",
      origin: "https://example.ts.net",
      protocol: "https:",
    },
    "admin",
  );
  await vm.runInContext('openStatusDialog("tailscale")', context);
  await vm.runInContext("loadTailscaleStatus()", context);
  assert.equal(context.statusDialog.open, true);
  assert.equal(context.statusPanelTailscale.hidden, false);
  assert.equal(context.statusTabTailscale.getAttribute("aria-selected"), "true");
  assert.equal(context.statusTailscaleLogin.textContent, "aecrypto@gmail.com");
  assert.equal(context.statusTailscaleDisplayName.textContent, "ae crypto");
  assert.equal(context.statusTailscaleRole.textContent, "Admin");
  assert.equal(context.statusTailscaleProvenance.textContent, "tailscale-serve");
  assert.equal(context.statusTailscaleHostname.textContent, "example.ts.net");
  assert.equal(context.statusTailscaleAccessMethod.textContent, "Tailscale");
  assert.equal(context.statusTailscaleAdminOnlyRows.every((row) => row.hidden === false), true);
  assert.equal(APP_SOURCE.includes("nuc-1.tail247768.ts.net"), false);
});

test("ordinary user Tailscale panel keeps admin diagnostics hidden", async () => {
  const context = createStatusTabHarness(
    async () =>
      response({
        server: "connected",
        connection: "tailscale",
      }),
    {
      hostname: "example.ts.net",
      origin: "https://example.ts.net",
      protocol: "https:",
    },
    "user",
  );
  await vm.runInContext('openStatusDialog("tailscale")', context);
  await vm.runInContext("loadTailscaleStatus()", context);
  assert.equal(context.statusTailscaleDisplayName.textContent, "Reader");
  assert.equal(context.statusTailscaleLogin.textContent, "user@example.com");
  assert.equal(context.statusTailscaleRole.textContent, "User");
  assert.equal(context.statusTailscaleAccessMethod.textContent, "Tailscale");
  assert.equal(context.statusTailscaleHttps.textContent, "Yes");
  assert.equal(context.statusTailscaleAdminOnlyRows.every((row) => row.hidden === true), true);
});

test("Cloud and AI status controls still select their tabs", async () => {
  const context = createStatusTabHarness(
    async () =>
      response({
        server: "connected",
        connection: "tailscale",
      }),
    {
      hostname: "example.ts.net",
      origin: "https://example.ts.net",
      protocol: "https:",
    },
  );
  await vm.runInContext('openStatusDialog("cloud")', context);
  assert.equal(context.statusPanelCloud.hidden, false);
  assert.equal(context.statusPanelAi.hidden, true);
  assert.equal(context.statusPanelTailscale.hidden, true);
  assert.equal(context.statusTabCloud.getAttribute("aria-selected"), "true");

  await vm.runInContext('openStatusDialog("ai", { refreshAiStatus: true })', context);
  assert.equal(context.statusPanelAi.hidden, false);
  assert.equal(context.statusPanelCloud.hidden, true);
  assert.equal(context.statusPanelTailscale.hidden, true);
  assert.equal(context.statusTabAi.getAttribute("aria-selected"), "true");
  assert.equal(vm.runInContext("contextAiRefreshCount", context), 1);
});

test("Tailscale admin-only rows are hidden by default in markup", () => {
  const matches = [...INDEX_SOURCE.matchAll(/class="status-tailscale-admin-only" hidden/g)];
  assert.equal(matches.length, 4);
  assert.ok(INDEX_SOURCE.includes("status-tailscale-login"));
  assert.ok(INDEX_SOURCE.includes("status-tailscale-provenance"));
  assert.ok(!INDEX_SOURCE.toLowerCase().includes("cookie"));
  assert.ok(!INDEX_SOURCE.includes("nuc-1.tail247768.ts.net"));
  assert.ok(INDEX_SOURCE.includes("tailnet"));
  assert.ok(APP_SOURCE.includes("applyTailscalePanelDensity"));
});

test("Cloud and AI accessible labels communicate status without emoji reliance", () => {
  const cloudBody = extractFunction(APP_SOURCE, "setServerHealthButtonState");
  const aiBody = extractFunction(APP_SOURCE, "setAiStatusButtonState");
  assert.ok(cloudBody.includes("Cloud status: connected"));
  assert.ok(cloudBody.includes("Cloud status: unavailable"));
  assert.ok(cloudBody.includes("Cloud status: checking"));
  assert.ok(aiBody.includes("AI status: available"));
  assert.ok(aiBody.includes("AI status: unavailable"));
  assert.ok(aiBody.includes("AI status: checking"));
});
