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
  const bodyOpen = source.indexOf("{", headerClose);
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

function stubElement() {
  return {
    hidden: false,
    checked: false,
    value: "",
    textContent: "",
    className: "",
    appendChild() {},
    setAttribute() {},
    removeAttribute() {},
    addEventListener() {},
  };
}

function identityStateFor({ resolved, available, audience, capabilities }) {
  return {
    resolved,
    available,
    audience,
    login: available ? "admin@example.com" : "",
    displayName: available ? "Admin User" : "",
    role: "admin",
    provenance: available ? "tailscale-serve" : "",
    capabilities: new Set(capabilities),
  };
}

function createResearchHarness(fetchImpl, { identity } = {}) {
  const context = {
    console,
    fetch: fetchImpl,
    Set,
    Object,
    Array,
    Boolean,
    String,
    Math,
    JSON,
    Error,
    BigInt,
    Number,
    document: {
      activeElement: null,
      createElement: () => stubElement(),
    },
    researchSettingsSection: stubElement(),
    researchSettingsStatus: stubElement(),
    researchSettingsLoading: stubElement(),
    researchSettingsForm: stubElement(),
    researchSettingsEnabled: stubElement(),
    researchSettingsModel: stubElement(),
    researchSettingsDailyBudget: stubElement(),
    researchSettingsMonthlyBudget: stubElement(),
    researchSettingsSearchReservation: stubElement(),
    researchSettingsResearchReservation: stubElement(),
    researchSettingsCatalog: stubElement(),
    researchSettingsCredential: stubElement(),
    researchSettingsSaveButton: stubElement(),
    researchSettingsReloadButton: stubElement(),
    researchSettingsConfirm: stubElement(),
    researchSettingsConfirmNote: stubElement(),
    researchSettingsConfirmCancel: stubElement(),
    researchSettingsConfirmButton: stubElement(),
    identityState: identity || identityStateFor({
      resolved: true,
      available: true,
      audience: "tailscale_workspace",
      capabilities: ["provider.operate"],
    }),
  };
  const prelude = [
    "let lastFocusedElementBeforeResearch = null;",
    'const RESEARCH_SETTINGS_ENDPOINT = "/api/admin/ai/research-settings";',
    "let researchSettingsState = { loaded: false, server: null, revision: \"\", configurationPresent: false, credentialAvailable: false, models: [], limits: null, draft: null, dirty: false, loading: false, saving: false, confirmArmed: false, pendingDraft: null, message: \"\", errorMessage: \"\", responseGeneration: 0 };",
    extractFunction(APP_SOURCE, "identityHasCapability"),
    extractFunction(APP_SOURCE, "isWorkspaceAudience"),
    extractFunction(APP_SOURCE, "framenestMutationHeaders"),
    extractFunction(APP_SOURCE, "identityAllowsResearchSettings"),
    extractFunction(APP_SOURCE, "researchSettingsModelById"),
    extractFunction(APP_SOURCE, "researchSettingsFormatUsd"),
    extractFunction(APP_SOURCE, "researchSettingsParseUsd"),
    extractFunction(APP_SOURCE, "researchSettingsDraftFromServer"),
    extractFunction(APP_SOURCE, "researchSettingsPayloadFromDraft"),
    extractFunction(APP_SOURCE, "researchSettingsRequiresConfirmation"),
    extractFunction(APP_SOURCE, "researchSettingsStatusMessage"),
    extractFunction(APP_SOURCE, "researchSettingsResponseMessage"),
    extractFunction(APP_SOURCE, "applyResearchSettingsPayload"),
    extractFunction(APP_SOURCE, "clearResearchSettingsProtectedState"),
    extractFunction(APP_SOURCE, "renderResearchSettings"),
    extractFunction(APP_SOURCE, "collectResearchSettingsDraft"),
    extractFunction(APP_SOURCE, "researchSettingsConfirmNoteText"),
    extractFunction(APP_SOURCE, "requestSaveResearchSettings"),
    extractFunction(APP_SOURCE, "cancelResearchSettingsConfirmation"),
    extractFunction(APP_SOURCE, "confirmResearchSettingsChange"),
    extractFunction(APP_SOURCE, "saveResearchSettings"),
  ].join("\n");
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(prelude, context);
  return context;
}

function setResearchState(context, patch) {
  vm.runInContext(
    `Object.assign(researchSettingsState, ${JSON.stringify(patch)})`,
    context,
  );
}

function getResearchState(context) {
  return vm.runInContext(
    "JSON.parse(JSON.stringify(researchSettingsState))",
    context,
  );
}

function serverPayload(overrides = {}) {
  return {
    revision: "rev-1",
    configuration_present: true,
    provider_id: "openai-responses",
    settings: {
      enabled: false,
      model_id: "gpt-5.5-2026-04-23",
      daily_budget_usd_micros: 10000000,
      monthly_budget_usd_micros: 30000000,
      search_budget_reservation_usd_micros: 500000,
      research_budget_reservation_usd_micros: 5000000,
      ...(overrides.settings || {}),
    },
    credential_available: true,
    models: [
      {
        model_id: "gpt-5.5-2026-04-23",
        display_name: "GPT-5.5 (2026-04-23)",
        pinning: "dated_snapshot",
        selectable: true,
        price_schedule_version: "openai-standard-2026-09-30",
        valid_until: null,
        pricing: {},
      },
    ],
    limits: {},
    ...overrides,
  };
}

test("research settings copy and controls exist inside the administrator AI dialog", () => {
  const dialogStart = INDEX_SOURCE.indexOf('id="ai-providers-dialog"');
  const dialogEnd = INDEX_SOURCE.indexOf("</dialog>", dialogStart);
  const dialog = INDEX_SOURCE.slice(dialogStart, dialogEnd);
  assert.ok(dialog.includes("Research settings"));
  for (const id of [
    "research-settings-section",
    "research-settings-status",
    "research-settings-enabled",
    "research-settings-model",
    "research-settings-daily-budget",
    "research-settings-monthly-budget",
    "research-settings-search-reservation",
    "research-settings-research-reservation",
    "research-settings-catalog",
    "research-settings-credential",
    "research-settings-save",
    "research-settings-reload",
    "research-settings-confirm",
    "research-settings-confirm-note",
    "research-settings-confirm-cancel",
    "research-settings-confirm-button",
  ]) {
    assert.ok(dialog.includes(`id="${id}"`), `missing dialog element ${id}`);
  }
  assert.ok(
    dialog.includes(
      "These server settings apply to new Search and Research requests. "
        + "People submitting questions cannot choose a model, endpoint or tools.",
    ),
  );
  assert.ok(
    dialog.includes(
      "Changing the model does not change requests already admitted. "
        + "Disabling pauses new generation; existing requests can still be checked or cancelled.",
    ),
  );
  assert.ok(dialog.includes("Credentials are managed on the server."));
  assert.equal(APP_SOURCE.includes("window.confirm("), false);
});

test("research settings identity predicate rejects non-administrator contexts", () => {
  const cases = [
    [
      "workspace administrator",
      identityStateFor({
        resolved: true,
        available: true,
        audience: "tailscale_workspace",
        capabilities: ["provider.operate"],
      }),
      true,
    ],
    [
      "ordinary workspace user",
      identityStateFor({
        resolved: true,
        available: true,
        audience: "tailscale_workspace",
        capabilities: ["gallery.read"],
      }),
      false,
    ],
    [
      "capability-only loopback context",
      identityStateFor({
        resolved: true,
        available: false,
        audience: "trusted_loopback",
        capabilities: ["provider.operate"],
      }),
      false,
    ],
    [
      "anonymous public context",
      identityStateFor({
        resolved: true,
        available: false,
        audience: "public_published",
        capabilities: [],
      }),
      false,
    ],
    [
      "unresolved bootstrap",
      identityStateFor({
        resolved: false,
        available: false,
        audience: "",
        capabilities: [],
      }),
      false,
    ],
  ];
  for (const [label, identity, expected] of cases) {
    const context = createResearchHarness(async () => response({}, 404), { identity });
    assert.equal(
      vm.runInContext("identityAllowsResearchSettings()", context),
      expected,
      label,
    );
  }
});

test("USD conversion uses integer micro-USD without floating point rounding", () => {
  const context = createResearchHarness(async () => response({}, 404));
  const cases = [
    ["10", 10000000],
    ["0.5", 500000],
    ["0.000001", 1],
    ["1.234567", 1234567],
    ["30", 30000000],
  ];
  for (const [text, micros] of cases) {
    assert.equal(
      vm.runInContext(`researchSettingsParseUsd(${JSON.stringify(text)})`, context),
      micros,
      text,
    );
  }
  assert.equal(vm.runInContext('researchSettingsParseUsd("1.2345678")', context), null);
  assert.equal(vm.runInContext('researchSettingsParseUsd("abc")', context), null);
  assert.equal(vm.runInContext('researchSettingsParseUsd("-1")', context), null);
  assert.equal(vm.runInContext("researchSettingsFormatUsd(10000000)", context), "10");
  assert.equal(vm.runInContext("researchSettingsFormatUsd(500000)", context), "0.5");
  assert.equal(vm.runInContext("researchSettingsFormatUsd(1)", context), "0.000001");
  assert.equal(vm.runInContext("researchSettingsFormatUsd(1234567)", context), "1.234567");
});

test("model and budget changes require explicit confirmation, equal values do not", () => {
  const context = createResearchHarness(async () => response({}, 404));
  const previous = {
    enabled: false,
    model_id: "gpt-5.5-2026-04-23",
    daily_budget_usd: "10",
    monthly_budget_usd: "30",
    search_reservation_usd: "0.5",
    research_reservation_usd: "5",
  };
  const changed = { ...previous, model_id: "gpt-5.6-luna" };
  const budget = { ...previous, daily_budget_usd: "20" };
  context.previousDraft = previous;
  const evaluate = (draft) => vm.runInContext(
    `researchSettingsRequiresConfirmation(previousDraft, ${JSON.stringify(draft)})`,
    context,
  );
  assert.equal(evaluate(previous), false);
  assert.equal(evaluate(changed), true);
  assert.equal(evaluate(budget), true);

  const enabling = { ...previous, enabled: true };
  assert.equal(evaluate(enabling), false);
});

test("a money edit opens the confirmation instead of saving without confirmation", async () => {
  const calls = [];
  const context = createResearchHarness(async (url, options) => {
    calls.push({ url, options });
    return response(serverPayload());
  });
  setResearchState(context, {
    draft: {
      enabled: false,
      model_id: "gpt-5.5-2026-04-23",
      daily_budget_usd: "10",
      monthly_budget_usd: "30",
      search_reservation_usd: "0.5",
      research_reservation_usd: "5",
    },
    revision: "rev-1",
  });
  context.researchSettingsDailyBudget.value = "20";
  context.researchSettingsMonthlyBudget.value = "30";
  context.researchSettingsSearchReservation.value = "0.5";
  context.researchSettingsResearchReservation.value = "5";
  context.researchSettingsModel.value = "gpt-5.5-2026-04-23";
  context.researchSettingsEnabled.checked = false;

  await vm.runInContext("requestSaveResearchSettings()", context);
  assert.equal(getResearchState(context).confirmArmed, true);
  assert.equal(calls.length, 0, "confirming must not save early");
  assert.ok(
    context.researchSettingsConfirmNote.textContent.includes("Daily budget 20 USD"),
  );
});

test("a stale-revision save returns the conflict copy without rebasing", async () => {
  const calls = [];
  const context = createResearchHarness(async (url, options) => {
    calls.push({ url, options });
    return response(
      {
        error: {
          code: "AI_CONFIG_CONFLICT",
          message: "AI settings changed. Reload and review before saving again.",
        },
      },
      409,
    );
  });
  setResearchState(context, {
    revision: "stale-rev",
    draft: {
      enabled: false,
      model_id: "gpt-5.5-2026-04-23",
      daily_budget_usd: "10",
      monthly_budget_usd: "30",
      search_reservation_usd: "0.5",
      research_reservation_usd: "5",
    },
  });
  await vm.runInContext(
    'saveResearchSettings({ enabled: false, model_id: "gpt-5.5-2026-04-23", daily_budget_usd: "10", monthly_budget_usd: "30", search_reservation_usd: "0.5", research_reservation_usd: "5" })',
    context,
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "/api/admin/ai/research-settings");
  assert.equal(calls[0].options.method, "PUT");
  assert.equal(calls[0].options.headers["If-Match"], '"stale-rev"');
  assert.match(
    getResearchState(context).errorMessage,
    /Reload and review before saving again/,
  );
});

test("a successful save applies the new revision and reports changed", async () => {
  const context = createResearchHarness(async () =>
    response(serverPayload({ revision: "rev-2", changed: true })),
  );
  setResearchState(context, { revision: "rev-1" });
  await vm.runInContext(
    'saveResearchSettings({ enabled: false, model_id: "gpt-5.6-luna", daily_budget_usd: "10", monthly_budget_usd: "30", search_reservation_usd: "0.5", research_reservation_usd: "5" })',
    context,
  );
  const state = getResearchState(context);
  assert.equal(state.revision, "rev-2");
  assert.match(state.message, /saved/);
  assert.equal(state.errorMessage, "");
});

test("identity loss clears protected research state", () => {
  const context = createResearchHarness(async () => response({}, 404), {
    identity: identityStateFor({
      resolved: false,
      available: false,
      audience: "",
      capabilities: [],
    }),
  });
  vm.runInContext(
    'applyResearchSettingsPayload({ revision: "rev-1", configuration_present: true, provider_id: "openai-responses", settings: { enabled: true, model_id: "gpt-5.6-luna", daily_budget_usd_micros: 1, monthly_budget_usd_micros: 1, search_budget_reservation_usd_micros: 1, research_budget_reservation_usd_micros: 1 }, credential_available: true, models: [], limits: {} })',
    context,
  );
  assert.equal(getResearchState(context).loaded, true);
  vm.runInContext("clearResearchSettingsProtectedState()", context);
  const state = getResearchState(context);
  assert.equal(state.loaded, false);
  assert.equal(state.draft, null);
  assert.equal(state.revision, "");
  assert.equal(
    vm.runInContext("identityAllowsResearchSettings()", context),
    false,
  );
});

test("research settings styles stay scoped and reuse the settings dialog language", () => {
  assert.ok(STYLES_SOURCE.includes(".research-settings__form"));
  assert.ok(STYLES_SOURCE.includes(".research-settings__catalog"));
  assert.ok(STYLES_SOURCE.includes("#ai-providers-dialog"));
  const narrowStart = STYLES_SOURCE.lastIndexOf("@media (max-width: 640px)");
  assert.notEqual(narrowStart, -1);
  assert.ok(STYLES_SOURCE.slice(narrowStart, narrowStart + 400).includes("#ai-providers-dialog"));
  assert.ok(STYLES_SOURCE.slice(narrowStart).includes("@media (max-width: 360px)"));
});
