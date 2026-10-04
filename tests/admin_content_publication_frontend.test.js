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

class TestElement {
  constructor(document, tagName) {
    this.ownerDocument = document;
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
    this.title = "";
  }

  appendChild(node) {
    node.parentNode = this;
    this.children.push(node);
    return node;
  }

  append(...nodes) {
    nodes.forEach((node) => this.appendChild(node));
  }

  setAttribute(name, value) {
    this.attributes.set(String(name).toLowerCase(), String(value));
  }

  addEventListener(type, listener) {
    if (!this.listeners.has(type)) this.listeners.set(type, []);
    this.listeners.get(type).push(listener);
  }
}

class TestDocument {
  createElement(tagName) {
    return new TestElement(this, tagName);
  }
}

function renderAdminPublicationState(item) {
  const document = new TestDocument();
  const detailsCalls = [];
  const context = {
    document,
    adminCatalogState: {
      actionStatusByMediaId: new Map(),
      publishOwners: new Map(),
      removalOwners: new Map(),
    },
    adminBatchState: {
      selectedMediaIds: new Set(),
      driver: null,
    },
    formatCatalogKind: () => "Video",
    summarizeAvailability: () => "Available",
    renderAdminThumbnail: () => document.createElement("div"),
    safeAdminDetailsItem: (candidate) => candidate,
    openDetailsDialog: (candidate, opener) => detailsCalls.push({ candidate, opener }),
    publishAdminMediaItem: () => {},
    unpublishAdminMediaItem: () => {},
    removeAdminMediaFromCatalog: () => {},
    identityHasCapability: () => true,
    loadAdminMediaTeamAliases: () => {},
    identityAllowsTeamAliasRead: () => true,
    adminBatchDriverActive: () => false,
    item,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(
    [
      extractFunction(APP_SOURCE, "adminMediaTitle"),
      extractFunction(APP_SOURCE, "adminReadinessLabel"),
      extractFunction(APP_SOURCE, "adminAnalysisPresentation"),
      extractFunction(APP_SOURCE, "adminPublicationLabel"),
      extractFunction(APP_SOURCE, "adminMissingFieldsLabel"),
      extractFunction(APP_SOURCE, "createAdminStateBadge"),
      extractFunction(APP_SOURCE, "adminBatchDriverActive"),
      extractFunction(APP_SOURCE, "renderAdminSelectControl"),
      extractFunction(APP_SOURCE, "renderAdminMediaItem"),
    ].join("\n"),
    context,
  );
  const row = vm.runInContext("renderAdminMediaItem(item)", context);
  row.detailsCalls = detailsCalls;
  return row;
}

function adminPublicationFixture(overrides) {
  return {
    media_id: "media-1",
    media_kind: "video",
    display_title: "Catalog item",
    processed: false,
    locations: [],
    publication_ready: true,
    missing_fields: [],
    analysis_state: "not_requested",
    content_publication_state: "unpublished",
    ...overrides,
  };
}

function badgePresentation(cell) {
  const badge = cell.children[0];
  return {
    label: badge.children[1].textContent,
    className: badge.className,
    icon: badge.children[0].textContent,
  };
}

function actionFor(cell, action) {
  return cell.children.find((child) => child.dataset.adminAction === action);
}

test("admin navigation is hidden by default and requires the explicit workflow capability", () => {
  assert.match(
    INDEX_SOURCE,
    /id="admin-media-open-button"[\s\S]*?hidden[\s\S]*?>[\s\S]*?Manage media/,
  );
  const gate = extractFunction(APP_SOURCE, "identityAllowsAdminWorkflow");
  assert.match(gate, /identityState\.resolved/);
  assert.match(gate, /identityState\.available/);
  assert.match(gate, /capabilities\.has\("media\.workflow\.read"\)/);
  assert.doesNotMatch(gate, /identityHasCapability/);
  assert.match(extractFunction(APP_SOURCE, "applyIdentityCapabilities"), /identityAllowsAdminWorkflow/);
});

test("admin surface owns independent filters, requests, items, and per-item publication state", () => {
  const stateSource = APP_SOURCE.slice(
    APP_SOURCE.indexOf("let adminCatalogState = {"),
    APP_SOURCE.indexOf("let uploadCapability = {"),
  );
  for (const field of [
    "publication",
    "readiness",
    "analysis",
    "contributor",
    "limit",
    "offset",
    "requestOwner",
    "items",
    "publishOwners",
    "removalOwners",
    "pendingCleanupReceiptId",
    "actionStatusByMediaId",
    "loading",
    "error",
  ]) {
    assert.match(stateSource, new RegExp(`${field}:`));
  }
  assert.match(stateSource, /publication: "unpublished"/);
});

test("admin list request ownership suppresses an older response", async () => {
  const pending = [];
  const rendered = [];
  const context = {
    URLSearchParams,
    Object,
    Boolean,
    String,
    Number,
    Math,
    adminCatalogRequestToken: 0,
    adminCatalogState: {
      q: "older",
      publication: "unpublished",
      readiness: "all",
      analysis: "all",
      limit: 24,
      offset: 0,
      requestOwner: null,
    },
    ADMIN_MEDIA_ENDPOINT: "/api/admin/media",
    ADMIN_MEDIA_PAGE_SIZE: 24,
    adminMediaPrevButton: { disabled: false },
    adminMediaNextButton: { disabled: false },
    adminMediaPageSummary: { textContent: "" },
    identityAllowsAdminWorkflow: () => true,
    setAdminCatalogViewState: () => {},
    renderAdminCatalogPage: (page) => rendered.push(page.marker),
    fetch: () => {
      const request = deferred();
      pending.push(request);
      return request.promise;
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(
    [
      extractFunction(APP_SOURCE, "snapshotAdminCatalogQueryState"),
      extractFunction(APP_SOURCE, "buildAdminCatalogQueryParams"),
      extractFunction(APP_SOURCE, "claimAdminCatalogRequest"),
      extractFunction(APP_SOURCE, "adminCatalogRequestOwnerIsCurrent"),
      extractFunction(APP_SOURCE, "releaseAdminCatalogRequest"),
      extractFunction(APP_SOURCE, "loadAdminCatalog"),
    ].join("\n"),
    context,
  );

  const older = vm.runInContext("loadAdminCatalog()", context);
  context.adminCatalogState.q = "newer";
  const newer = vm.runInContext("loadAdminCatalog()", context);
  pending[1].resolve(response({ marker: "newer", items: [] }));
  await newer;
  pending[0].resolve(response({ marker: "older", items: [] }));
  await older;
  assert.deepEqual(rendered, ["newer"]);
});

test("single-item publication ownership prevents duplicate activation", () => {
  const context = {
    Object,
    Boolean,
    adminPublicationRequestToken: 0,
    adminCatalogState: { publishOwners: new Map() },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(
    [
      extractFunction(APP_SOURCE, "publicationOwnerIsCurrent"),
      extractFunction(APP_SOURCE, "claimPublicationRequest"),
      extractFunction(APP_SOURCE, "releasePublicationRequest"),
    ].join("\n"),
    context,
  );
  const first = vm.runInContext('claimPublicationRequest("media-1", null)', context);
  const duplicate = vm.runInContext('claimPublicationRequest("media-1", null)', context);
  assert.ok(first);
  assert.equal(duplicate, null);
  assert.equal(vm.runInContext("publicationOwnerIsCurrent(adminCatalogState.publishOwners.get('media-1'))", context), true);
  assert.equal(vm.runInContext("releasePublicationRequest(adminCatalogState.publishOwners.get('media-1'))", context), true);
});

test("publication UI waits for server truth and supports bounded failure recovery", () => {
  const mutate = extractFunction(APP_SOURCE, "mutateAdminContentPublication");
  assert.match(extractFunction(APP_SOURCE, "publishAdminMediaItem"), /mutateAdminContentPublication\(item, opener, true\)/);
  assert.match(extractFunction(APP_SOURCE, "unpublishAdminMediaItem"), /mutateAdminContentPublication\(item, opener, false\)/);
  assert.match(mutate, /method: "PUT"/);
  assert.match(mutate, /framenestMutationHeaders/);
  assert.match(mutate, /claimPublicationRequest/);
  assert.match(mutate, /aria-busy/);
  assert.match(mutate, /if \(response\.ok\)/);
  assert.match(mutate, /await loadAdminCatalog/);
  assert.match(mutate, /published && response\.status === 409/);
  assert.match(mutate, /item\.publication_ready = false/);
  assert.match(mutate, /kind: "readiness"/);
  assert.match(mutate, /response\.status === 401 \|\| response\.status === 403/);
  assert.match(mutate, /kind: "authorization"/);
  assert.match(mutate, /retryable: response\.status >= 500/);
  assert.match(mutate, /retryable: true/);
  assert.match(mutate, /JSON\.stringify\(\{ published: false \}\)/);
  assert.doesNotMatch(mutate.slice(0, mutate.indexOf("if (response.ok)")), /published\./);
});

test("admin states use literal text and non-color icons without claiming AI application", () => {
  for (const label of [
    "Ready to publish",
    "Incomplete metadata",
    "AI suggestion ready",
    "Analysis queued",
    "Analysis in progress",
    "Analysis failed",
    "Published",
    "Processed",
  ]) {
    assert.ok(APP_SOURCE.includes(label), `missing ${label}`);
  }
  assert.equal(APP_SOURCE.includes("AI applied"), false);
  assert.match(extractFunction(APP_SOURCE, "createAdminStateBadge"), /aria-hidden/);
});

test("admin header uses a left icon-only Back action with exact filter and keyboard order", () => {
  const headerStart = INDEX_SOURCE.indexOf('<div class="admin-media-header">');
  const headerEnd = INDEX_SOURCE.indexOf("</div>", INDEX_SOURCE.indexOf("</div>", headerStart) + 1);
  const header = INDEX_SOURCE.slice(headerStart, headerEnd);
  assert.ok(header.indexOf('id="admin-media-close-button"') < header.indexOf('id="admin-media-heading"'));
  assert.match(header, /id="admin-media-close-button"[\s\S]*?aria-label="Back to Gallery"/);
  assert.doesNotMatch(header.replace(/aria-label="Back to Gallery"|title="Back to Gallery"/g, ""), />\s*Back to Gallery\s*</);
  assert.match(header, /<span aria-hidden="true">&larr;<\/span>/);
  assert.match(extractFunction(APP_SOURCE, "closeAdminMediaBrowser"), /adminMediaOpenButton\.focus\(\)/);

  const search = INDEX_SOURCE.indexOf('id="admin-media-search"');
  const readiness = INDEX_SOURCE.indexOf('id="admin-media-readiness-filter"');
  const analysis = INDEX_SOURCE.indexOf('id="admin-media-analysis-filter"');
  const publication = INDEX_SOURCE.indexOf('id="admin-media-publication-filter"');
  assert.ok(search < readiness && readiness < analysis && analysis < publication);
  assert.match(
    INDEX_SOURCE,
    /id="admin-media-analysis-filter"[\s\S]*?value="not_requested"[\s\S]*?value="pending"[\s\S]*?value="analyzing"[\s\S]*?value="analyzed"[\s\S]*?value="failed"/,
  );
});

test("five durable AI states keep exact labels cues filters badges and unpublished row modifiers", () => {
  const expected = [
    ["not_requested", "Analysis not requested", "·", "analysis-not-requested"],
    ["pending", "Analysis queued", "◷", "analysis-pending"],
    ["analyzing", "Analysis in progress", "↻", "analysis-analyzing"],
    ["analyzed", "AI suggestion ready", "✦", "analysis-ready"],
    ["failed", "Analysis failed", "!", "analysis-failed"],
  ];
  for (const [state, label, icon, badgeModifier] of expected) {
    const row = renderAdminPublicationState(adminPublicationFixture({ analysis_state: state }));
    const presentation = badgePresentation(row.children[3]);
    assert.equal(row.dataset.analysisState, state);
    assert.equal(row.dataset.publicationState, "unpublished");
    assert.equal(presentation.label, label);
    assert.equal(presentation.icon, icon);
    assert.equal(
      presentation.className,
      `admin-media-badge admin-media-badge--${badgeModifier}`,
    );
    const rowModifier = state === "analyzed" ? "analysis-analyzed" : badgeModifier;
    assert.match(row.className, new RegExp(`admin-media-row--${rowModifier}(?:\\s|$)`));
    assert.ok(INDEX_SOURCE.includes(`value="${state}"`));
    assert.ok(STYLES_SOURCE.includes(`--admin-row-ai-${state === "not_requested" ? "neutral" : state}`));
  }
  assert.match(STYLES_SOURCE, /\.admin-media-row--analysis-analyzed\s*\{/);
  assert.match(STYLES_SOURCE, /--admin-row-ai-analyzed-bg:\s*rgba\(78, 142, 255,/);
});

test("ready unpublished DOM presents ready metadata and an active publish action", () => {
  const row = renderAdminPublicationState(adminPublicationFixture());
  const readiness = row.children[2];
  const publication = row.children[4];
  const actions = row.children[5];

  assert.deepEqual(badgePresentation(readiness), {
    label: "Ready to publish",
    className: "admin-media-badge admin-media-badge--ready",
    icon: "✓",
  });
  assert.equal(readiness.children.length, 1);
  assert.deepEqual(badgePresentation(publication), {
    label: "Unpublished",
    className: "admin-media-badge admin-media-badge--neutral",
    icon: "○",
  });
  assert.equal(actionFor(actions, "publish").disabled, false);
  assert.equal(actionFor(actions, "retry"), undefined);
  assert.match(row.className, /admin-media-row--unpublished/);
});

test("ready published DOM keeps ready metadata independent from durable publication", () => {
  const row = renderAdminPublicationState(adminPublicationFixture({
    content_publication_state: "published",
  }));
  const readiness = row.children[2];
  const publication = row.children[4];
  const actions = row.children[5];

  assert.deepEqual(badgePresentation(readiness), {
    label: "Ready to publish",
    className: "admin-media-badge admin-media-badge--ready",
    icon: "✓",
  });
  assert.equal(readiness.children.length, 1);
  assert.deepEqual(badgePresentation(publication), {
    label: "Published",
    className: "admin-media-badge admin-media-badge--published",
    icon: "✓",
  });
  assert.equal(actionFor(actions, "publish"), undefined);
  assert.equal(actionFor(actions, "unpublish").disabled, false);
  assert.equal(actionFor(actions, "unpublish").textContent, "Unpublish");
  assert.equal(
    actionFor(actions, "unpublish").className,
    "admin-media-action admin-media-action--unpublish",
  );
  assert.equal(actionFor(actions, "retry"), undefined);
  assert.equal(row.className, "admin-media-row admin-media-row--published");
});

test("incomplete unpublished DOM presents missing metadata and disables publication", () => {
  const row = renderAdminPublicationState(adminPublicationFixture({
    publication_ready: false,
    missing_fields: ["description"],
  }));
  const readiness = row.children[2];
  const publication = row.children[4];
  const actions = row.children[5];

  assert.deepEqual(badgePresentation(readiness), {
    label: "Incomplete metadata",
    className: "admin-media-badge admin-media-badge--incomplete",
    icon: "!",
  });
  assert.equal(readiness.children[1].textContent, "Missing: description");
  assert.deepEqual(badgePresentation(publication), {
    label: "Unpublished",
    className: "admin-media-badge admin-media-badge--neutral",
    icon: "○",
  });
  assert.equal(actionFor(actions, "publish").disabled, true);
  assert.equal(actionFor(actions, "retry"), undefined);
});

test("published metadata regression DOM preserves publication and offers unpublish", () => {
  const row = renderAdminPublicationState(adminPublicationFixture({
    publication_ready: false,
    missing_fields: ["description"],
    content_publication_state: "published",
  }));
  const readiness = row.children[2];
  const publication = row.children[4];
  const actions = row.children[5];

  assert.deepEqual(badgePresentation(readiness), {
    label: "Incomplete metadata",
    className: "admin-media-badge admin-media-badge--incomplete",
    icon: "!",
  });
  assert.equal(readiness.children[1].textContent, "Missing: description");
  assert.deepEqual(badgePresentation(publication), {
    label: "Published",
    className: "admin-media-badge admin-media-badge--published",
    icon: "✓",
  });
  assert.equal(actionFor(actions, "publish"), undefined);
  assert.equal(actionFor(actions, "unpublish").disabled, false);
  assert.equal(actionFor(actions, "retry"), undefined);
  assert.equal(row.className, "admin-media-row admin-media-row--published");
});

test("clickable title replaces Inspect and reuses Details through a path-redacted adapter", () => {
  const renderItem = extractFunction(APP_SOURCE, "renderAdminMediaItem");
  const adapter = extractFunction(APP_SOURCE, "safeAdminDetailsItem");
  assert.doesNotMatch(renderItem, /Inspect|inspectButton|adminAction = "inspect"/);
  assert.match(renderItem, /openDetailsDialog\(safeAdminDetailsItem\(item\), titleButton\)/);
  assert.match(adapter, /relative_path: "Local catalog location"/);
  assert.doesNotMatch(adapter, /location\.relative_path/);

  const row = renderAdminPublicationState(adminPublicationFixture());
  const heading = row.children[1].children[0];
  const titleButton = heading.children[0];
  assert.equal(titleButton.tagName, "BUTTON");
  assert.equal(titleButton.type, "button");
  assert.equal(titleButton.className, "admin-media-row__title-button");
  assert.equal(titleButton.attributes.get("aria-label"), "Open details for Catalog item");
  assert.equal(row.children[5].children.some((child) => child.textContent === "Inspect"), false);
  let propagationStopped = false;
  titleButton.listeners.get("click")[0]({
    stopPropagation() {
      propagationStopped = true;
    },
  });
  assert.equal(propagationStopped, true);
  assert.equal(row.detailsCalls.length, 1);
  assert.equal(row.detailsCalls[0].opener, titleButton);
  assert.equal(actionFor(row.children[5], "publish").disabled, false);
});

test("admin controls expose loading empty error retry search filters and deterministic pagination", () => {
  for (const id of [
    "admin-media-search",
    "admin-media-publication-filter",
    "admin-media-readiness-filter",
    "admin-media-analysis-filter",
    "admin-media-contributor-filter",
    "admin-media-refresh-button",
    "admin-media-loading",
    "admin-media-empty",
    "admin-media-error",
    "admin-media-retry-button",
    "admin-media-prev-button",
    "admin-media-next-button",
    "admin-media-aliases-panel",
    "admin-media-aliases-heading",
    "admin-media-aliases-status",
    "admin-media-aliases-results",
  ]) {
    assert.ok(INDEX_SOURCE.includes(`id="${id}"`), `missing ${id}`);
  }
  const params = extractFunction(APP_SOURCE, "buildAdminCatalogQueryParams");
  for (const key of ["q", "publication", "readiness", "analysis", "contributor", "limit", "offset"]) {
    assert.match(params, new RegExp(`"${key}"`));
  }
});

test("responsive source contracts cover exact stacked wrapping and full-grid ranges", () => {
  assert.match(STYLES_SOURCE, /@media \(max-width: 720px\)/);
  assert.match(STYLES_SOURCE, /@media \(min-width: 721px\) and \(max-width: 1023px\)/);
  assert.match(STYLES_SOURCE, /@media \(min-width: 1024px\)/);
  assert.match(STYLES_SOURCE, /\.admin-media-row \{[\s\S]*?min-width: 0/);
  assert.match(STYLES_SOURCE, /\.admin-media-browser \{[\s\S]*?overflow: hidden/);
  assert.match(STYLES_SOURCE, /min-height: 44px/);
  assert.match(STYLES_SOURCE, /@media \(max-width: 390px\)\s*\{[\s\S]*?html\s*\{[\s\S]*?min-width: 0/);
});

test("action and badge classes preserve hierarchy touch targets and publication precedence", () => {
  assert.match(STYLES_SOURCE, /\.admin-media-filters \.admin-media-action-control,\n\.admin-media-action\s*\{[\s\S]*?font-size: 0\.82rem/);
  assert.match(STYLES_SOURCE, /\.admin-media-badge\s*\{[\s\S]*?font-size: 0\.7rem/);
  assert.match(
    STYLES_SOURCE,
    /\.admin-media-header button,[\s\S]*?\.admin-media-action,[\s\S]*?min-height: 44px/,
  );
  assert.match(STYLES_SOURCE, /\.admin-media-back-button\s*\{[\s\S]*?width: 44px;[\s\S]*?height: 44px/);
  assert.match(STYLES_SOURCE, /\.admin-media-row--published\s*\{[\s\S]*?--admin-row-published/);
  assert.match(STYLES_SOURCE, /\.admin-media-action--unpublish\s*\{/);
  assert.doesNotMatch(
    extractFunction(APP_SOURCE, "renderAdminMediaItem"),
    /published[\s\S]*?admin-media-row--analysis-\$\{analysisPresentation\.rowModifier\}/,
  );
});

test("accessibility contracts include semantic rows, live status, focus recovery, and reduced motion", () => {
  assert.match(INDEX_SOURCE, /id="admin-media-results"[\s\S]*?role="table"/);
  assert.match(INDEX_SOURCE, /id="admin-media-action-status"[\s\S]*?aria-live="polite"/);
  assert.match(extractFunction(APP_SOURCE, "renderAdminMediaItem"), /setAttribute\("role", "row"\)/);
  assert.match(extractFunction(APP_SOURCE, "loadAdminCatalog"), /adminMediaHeading\.focus\(\)/);
  assert.match(STYLES_SOURCE, /\.admin-media-row:hover,\n\.admin-media-row:focus-within/);
  assert.match(STYLES_SOURCE, /@media \(prefers-reduced-motion: reduce\)/);
});

test("team aliases view is capability-gated, on-demand GET, and hidden from the public audience", () => {
  assert.match(
    extractFunction(APP_SOURCE, "identityAllowsTeamAliasRead"),
    /identityHasCapability\("metadata\.alias\.team\.read"\)/,
  );
  assert.match(
    extractFunction(APP_SOURCE, "identityAllowsTeamAliasRead"),
    /identityHasCapability\("media\.workflow\.read"\)/,
  );
  const render = extractFunction(APP_SOURCE, "renderAdminMediaItem");
  assert.match(render, /identityHasCapability\("metadata\.alias\.team\.read"\)/);
  assert.match(render, /Team aliases/);
  assert.match(render, /loadAdminMediaTeamAliases/);
  const loader = extractFunction(APP_SOURCE, "loadAdminMediaTeamAliases");
  assert.match(loader, /identityAllowsTeamAliasRead/);
  assert.match(loader, /adminMediaTeamAliasesUrl/);
  assert.match(loader, /method: "GET"/);
  assert.doesNotMatch(loader, /framenestMutationHeaders/);
  assert.match(INDEX_SOURCE, /id="admin-media-aliases-panel"/);
  assert.match(
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
    /identityAllowsTeamAliasRead/,
  );
  assert.match(
    STYLES_SOURCE,
    /body\[data-audience="public_published"\] #admin-media-aliases-panel/,
  );
  const row = renderAdminPublicationState(adminPublicationFixture());
  assert.equal(actionFor(row.children[5], "team-aliases").textContent, "Team aliases");
});
