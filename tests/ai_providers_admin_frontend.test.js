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

function revisionResponse(payload, revision, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    headers: { get: (name) => (name === "etag" ? `"${revision}"` : null) },
    json: async () => payload,
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

function createDialogStub() {
  const attrs = {};
  return {
    open: false,
    attrs,
    showModal() {
      this.open = true;
      attrs.open = "";
    },
    close() {
      this.open = false;
      delete attrs.open;
    },
    setAttribute(name, value) {
      attrs[name] = String(value);
    },
    removeAttribute(name) {
      delete attrs[name];
    },
    hasAttribute(name) {
      return Object.prototype.hasOwnProperty.call(attrs, name);
    },
    querySelector() {
      return null;
    },
  };
}

function createAiProvidersHarness(fetchImpl, { identity } = {}) {
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
    document: { activeElement: null },
    aiProvidersButton: { hidden: true, focus() {} },
    aiProvidersDialog: createDialogStub(),
    aiProvidersActiveSummary: { textContent: "" },
    aiProvidersStatus: { textContent: "", hidden: false },
    aiProvidersList: null,
    aiProvidersJsonPreview: { textContent: "" },
    aiProviderIdInput: { value: "" },
    aiProviderNameInput: { value: "" },
    aiProviderBaseUrlInput: { value: "" },
    aiProviderCredentialEnvInput: { value: "" },
    aiProviderProtocolInput: { value: "openai-chat-completions" },
    aiProviderModelsList: null,
    aiProviderPingButton: { disabled: false },
    aiProviderPongButton: { disabled: false },
    aiProviderActivateButton: { disabled: false },
    aiProviderDeleteButton: { disabled: false },
    aiProviderPongConfirm: { hidden: true },
    aiProviderPongConfirmNote: { textContent: "" },
    uploadOpenButton: { hidden: true },
    detailsEditButton: { hidden: true },
    adminMediaOpenButton: { hidden: true },
    adminMediaBrowser: { hidden: true },
    workspaceMediaOpenButton: { hidden: true },
    metadataControlCalls: 0,
    identityState: identity || identityStateFor({
      resolved: true,
      available: false,
      audience: "trusted_loopback",
      capabilities: ["provider.operate"],
    }),
  };
  context.updateMetadataControls = () => {
    context.metadataControlCalls += 1;
  };
  context.globalThis = context;
  vm.createContext(context);
  const prelude = [
    'const AI_ADMIN_PROVIDERS_ENDPOINT = "/api/admin/ai/providers";',
    'const AI_ADMIN_SELECTION_ENDPOINT = "/api/admin/ai/active-selection";',
    'const AI_ADMIN_PING_ENDPOINT = "/api/admin/ai/ping";',
    'const AI_ADMIN_PONG_ENDPOINT = "/api/admin/ai/pong";',
    'const AI_PROVIDER_DECLARED_PROTOCOL = "openai-chat-completions";',
    'const AI_PROVIDER_BUILTIN_IDS = new Set(["nvidia-nim", "vercel-ai-gateway"]);',
    "let lastFocusedElementBeforeAiProviders = null;",
    "let aiProviderPongArmed = false;",
    'let aiProvidersState = { loaded: false, providers: [], activeProviderId: null, activeModelId: null, configurationSource: "", selectedProviderId: "", message: "", errorMessage: "", busy: false, revision: "" };',
    "let researchSettingsState = { loaded: true, revision: \"research-rev-1\", stale: false, dirty: false, draft: {}, saving: false, message: \"\", errorMessage: \"\", responseGeneration: 0 };",
    extractFunction(APP_SOURCE, "identityHasCapability"),
    extractFunction(APP_SOURCE, "isWorkspaceAudience"),
    extractFunction(APP_SOURCE, "framenestMutationHeaders"),
    extractFunction(APP_SOURCE, "framenestResponseRevision"),
    extractFunction(APP_SOURCE, "framenestRevisionHeader"),
    extractFunction(APP_SOURCE, "invalidateAiProvidersRevision"),
    extractFunction(APP_SOURCE, "applyAiProvidersRevision"),
    extractFunction(APP_SOURCE, "invalidateResearchSettingsRevision"),
    "const RESEARCH_SETTINGS_ENDPOINT = \"/api/admin/ai/research-settings\";",
    "function researchSettingsPayloadFromDraft(draft) { return { enabled: true, model_id: draft.model_id }; }",
    "function researchSettingsResponseMessage(payload, fallback) { return (payload && payload.error && payload.error.message) || fallback; }",
    "function applyResearchSettingsPayload(payload) { researchSettingsState.revision = payload.revision; }",
    "function renderResearchSettings() {}",
    extractFunction(APP_SOURCE, "saveResearchSettings"),
    extractFunction(APP_SOURCE, "identityHasCapability"),
    extractFunction(APP_SOURCE, "isWorkspaceAudience"),
    extractFunction(APP_SOURCE, "framenestMutationHeaders"),
    extractFunction(APP_SOURCE, "identityAllowsMetadataEdit"),
    extractFunction(APP_SOURCE, "identityAllowsAdminWorkflow"),
    extractFunction(APP_SOURCE, "identityAllowsProviderAdministration"),
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
    extractFunction(APP_SOURCE, "aiProviderSourceLabel"),
    extractFunction(APP_SOURCE, "aiProviderCredentialHint"),
    extractFunction(APP_SOURCE, "aiProviderLastTestLabel"),
    extractFunction(APP_SOURCE, "aiProviderLastProbeLabel"),
    extractFunction(APP_SOURCE, "aiProviderRowActions"),
    extractFunction(APP_SOURCE, "aiProviderStatusMessage"),
    extractFunction(APP_SOURCE, "aiProviderResponseMessage"),
    extractFunction(APP_SOURCE, "sortedJsonValue"),
    extractFunction(APP_SOURCE, "buildAiProviderRecordPayload"),
    extractFunction(APP_SOURCE, "formatAiProviderJsonPreview"),
    extractFunction(APP_SOURCE, "aiProviderModelRows"),
    extractFunction(APP_SOURCE, "collectAiProviderFormRecord"),
    extractFunction(APP_SOURCE, "renderAiProviderJsonPreview"),
    extractFunction(APP_SOURCE, "handleAiProviderFormInput"),
    extractFunction(APP_SOURCE, "providerById"),
    extractFunction(APP_SOURCE, "activeProviderEntry"),
    extractFunction(APP_SOURCE, "applyAiProvidersPayload"),
    extractFunction(APP_SOURCE, "fetchAiProvidersList"),
    extractFunction(APP_SOURCE, "loadAiProviders"),
    extractFunction(APP_SOURCE, "setAiProviderButtonDisabled"),
    extractFunction(APP_SOURCE, "setAiProvidersBusy"),
    extractFunction(APP_SOURCE, "renderAiProvidersSummary"),
    extractFunction(APP_SOURCE, "renderAiProvidersStatusLine"),
    extractFunction(APP_SOURCE, "renderAiProvidersList"),
    extractFunction(APP_SOURCE, "renderAiProviderActionControls"),
    extractFunction(APP_SOURCE, "renderAiProvidersState"),
    extractFunction(APP_SOURCE, "openAiProvidersDialog"),
    extractFunction(APP_SOURCE, "closeAiProvidersDialog"),
    extractFunction(APP_SOURCE, "renderAiProviderModelRows"),
    extractFunction(APP_SOURCE, "resetAiProviderForm"),
    extractFunction(APP_SOURCE, "requestAiProviderPong"),
    extractFunction(APP_SOURCE, "cancelAiProviderPong"),
    extractFunction(APP_SOURCE, "activateAiProvider"),
    extractFunction(APP_SOURCE, "saveAiProviderRecord"),
    extractFunction(APP_SOURCE, "deleteAiProvider"),
    extractFunction(APP_SOURCE, "runAiProviderPing"),
    extractFunction(APP_SOURCE, "confirmAiProviderPong"),
  ].join("\n");
  vm.runInContext(prelude, context);
  return context;
}

function declaredProviderPayload() {
  return {
    provider_id: "opencode-go",
    display_name: "OpenCode Go",
    source: "declared",
    protocol: "openai-chat-completions",
    base_url: "https://opencode.ai/zen/go/v1",
    credential_env: "OPENCODE_API_KEY",
    credential_available: true,
    selected_model_id: "deepseek-v4-flash-vision-exp",
    supports_vision: true,
    models: [
      {
        model_id: "deepseek-v4-flash-vision-exp",
        display_name: "DeepSeek V4 Flash Vision Exp",
        capabilities: ["vision_input"],
      },
    ],
    last_test: null,
    last_vision_probe: null,
  };
}

test("AI providers header control exists hidden by default and the dialog reuses settings-dialog", () => {
  const headerStart = INDEX_SOURCE.indexOf('class="app-header"');
  const headerEnd = INDEX_SOURCE.indexOf("</header>", headerStart);
  const header = INDEX_SOURCE.slice(headerStart, headerEnd);
  assert.match(header, /id="ai-providers-button"/);
  assert.match(header, /aria-label="AI provider administration"/);
  assert.match(header, /🛠️/);
  assert.match(header, /id="ai-providers-button-text"/);
  const buttonStart = INDEX_SOURCE.indexOf('id="ai-providers-button"');
  const buttonTag = INDEX_SOURCE.slice(buttonStart, INDEX_SOURCE.indexOf(">", buttonStart) + 1);
  assert.match(buttonTag, /hidden/);
  assert.match(buttonTag, /status-button--icon/);

  const dialogStart = INDEX_SOURCE.indexOf('id="ai-providers-dialog"');
  assert.notEqual(dialogStart, -1);
  const dialogEnd = INDEX_SOURCE.indexOf("</dialog>", dialogStart);
  const dialog = INDEX_SOURCE.slice(dialogStart, dialogEnd);
  assert.match(dialog, /class="settings-dialog"/);
  for (const id of [
    "ai-providers-active-summary",
    "ai-providers-status",
    "ai-providers-list",
    "ai-providers-form",
    "ai-providers-json-preview",
    "ai-provider-id",
    "ai-provider-name",
    "ai-provider-base-url",
    "ai-provider-credential-env",
    "ai-provider-protocol",
    "ai-provider-models",
    "ai-provider-add-model",
    "ai-provider-save",
    "ai-provider-activate",
    "ai-provider-ping",
    "ai-provider-pong",
    "ai-provider-delete",
    "ai-provider-pong-confirm",
    "ai-provider-pong-confirm-note",
    "ai-provider-pong-cancel",
    "ai-provider-pong-confirm-button",
  ]) {
    assert.ok(dialog.includes(`id="${id}"`), `missing dialog element ${id}`);
  }
  assert.match(dialog, /Other protocols are not supported yet\./);
  assert.equal(APP_SOURCE.includes("window.confirm("), false);
});

test("provider administration gating admits only loopback and workspace administrators", () => {
  const cases = [
    [
      "trusted loopback administrator",
      identityStateFor({
        resolved: true,
        available: false,
        audience: "trusted_loopback",
        capabilities: ["provider.operate"],
      }),
      true,
    ],
    [
      "tailscale workspace administrator",
      identityStateFor({
        resolved: true,
        available: true,
        audience: "tailscale_workspace",
        capabilities: ["provider.operate", "gallery.read"],
      }),
      true,
    ],
    [
      "ordinary workspace user",
      identityStateFor({
        resolved: true,
        available: true,
        audience: "tailscale_workspace",
        capabilities: ["gallery.read", "upload.submit"],
      }),
      false,
    ],
    [
      "public published audience",
      identityStateFor({
        resolved: true,
        available: false,
        audience: "public_published",
        capabilities: ["gallery.read"],
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
    [
      "failed identity bootstrap",
      identityStateFor({
        resolved: true,
        available: false,
        audience: "",
        capabilities: [],
      }),
      false,
    ],
  ];
  for (const [label, identity, expected] of cases) {
    const context = createAiProvidersHarness(async () => response({}, 404), { identity });
    assert.equal(
      vm.runInContext("identityAllowsProviderAdministration()", context),
      expected,
      label,
    );
    vm.runInContext("applyIdentityCapabilities()", context);
    assert.equal(context.aiProvidersButton.hidden, !expected, label);
  }
});

test("dialog open performs exactly one list GET and typing stays local", async () => {
  const calls = [];
  const context = createAiProvidersHarness(async (url, options) => {
    calls.push({
      url,
      method: (options && options.method) || "GET",
    });
    return response({
      active_provider_id: null,
      active_model_id: null,
      configuration_source: "unconfigured",
      providers: [],
      supported_protocols: ["openai-chat-completions"],
      limits: {},
    });
  });

  await vm.runInContext("openAiProvidersDialog()", context);

  assert.deepEqual(calls, [{ url: "/api/admin/ai/providers", method: "GET" }]);
  assert.equal(context.aiProvidersDialog.open, true);

  context.aiProviderNameInput.value = "OpenCode Go";
  context.aiProviderBaseUrlInput.value = "https://opencode.ai/zen/go/v1";
  context.aiProviderCredentialEnvInput.value = "OPENCODE_API_KEY";
  vm.runInContext("handleAiProviderFormInput()", context);

  assert.equal(calls.length, 1, "typing must not fetch");
  const preview = context.aiProvidersJsonPreview.textContent;
  assert.match(preview, /"base_url": "https:\/\/opencode\.ai\/zen\/go\/v1"/);
  assert.match(preview, /"credential_env": "OPENCODE_API_KEY"/);
  assert.match(preview, /"protocol": "openai-chat-completions"/);
  assert.match(preview, /"name": "OpenCode Go"/);
  assert.ok(!/"api_key"|"apikey"|"secret"|"authorization"|"bearer"|"token"/i.test(preview));
});

test("ping and pong run only on explicit actions and pong requires the in-dialog confirm", async () => {
  const calls = [];
  const context = createAiProvidersHarness(async (url, options) => {
    const method = (options && options.method) || "GET";
    calls.push({
      url,
      method,
      body: options && options.body ? JSON.parse(options.body) : null,
    });
    if (method === "GET") {
      return response({
        active_provider_id: "opencode-go",
        active_model_id: "deepseek-v4-flash-vision-exp",
        configuration_source: "server config",
        providers: [declaredProviderPayload()],
        supported_protocols: ["openai-chat-completions"],
        limits: {},
      });
    }
    if (url === "/api/admin/ai/ping") {
      return response({
        status: "success",
        tested_at_ms: 1,
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash-vision-exp",
        credential_available: true,
      });
    }
    if (url === "/api/admin/ai/pong") {
      return response({
        status: "success",
        matched: true,
        expected_color: "red",
        observed_color: "red",
        probed_at_ms: 2,
        provider_id: "opencode-go",
        model_id: "deepseek-v4-flash-vision-exp",
      });
    }
    return response({});
  });

  await vm.runInContext("openAiProvidersDialog()", context);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].method, "GET");

  vm.runInContext('requestAiProviderPong("opencode-go")', context);
  assert.equal(context.aiProviderPongConfirm.hidden, false);
  assert.equal(calls.length, 1, "arming the confirmation must not fetch");
  assert.ok(context.aiProviderPongConfirmNote.textContent.includes("OpenCode Go"));
  assert.ok(context.aiProviderPongConfirmNote.textContent.includes("solid-red test square"));

  await vm.runInContext("confirmAiProviderPong()", context);
  const pongCall = calls.find((call) => call.url === "/api/admin/ai/pong");
  assert.ok(pongCall);
  assert.equal(pongCall.method, "POST");
  assert.deepEqual(pongCall.body, { confirm_cloud_upload: true });
  assert.equal(context.aiProviderPongConfirm.hidden, true);

  await vm.runInContext('runAiProviderPing("opencode-go")', context);
  const pingCall = calls.find((call) => call.url === "/api/admin/ai/ping");
  assert.ok(pingCall);
  assert.equal(pingCall.method, "POST");
  assert.deepEqual(pingCall.body, {});
});

test("every unsafe fetch call site uses framenestMutationHeaders and the literal stays single", () => {
  const mutationSites = APP_SOURCE.match(/method: "(?:POST|PUT|PATCH|DELETE)"/g) || [];
  const wrappedSites = APP_SOURCE.match(/headers: framenestMutationHeaders\(/g) || [];
  assert.ok(mutationSites.length > 0);
  assert.equal(mutationSites.length, wrappedSites.length);
  assert.equal((APP_SOURCE.match(/"X-FrameNest-Request"/g) || []).length, 1);
});

test("failure copy is sanitized and credential-missing providers disable actions", () => {
  const context = createAiProvidersHarness(async () => response({}, 500));

  const authenticationCopy = vm.runInContext(
    'aiProviderStatusMessage("AI_PROVIDER_AUTHENTICATION_FAILED", "")',
    context,
  );
  assert.match(authenticationCopy, /rejected the credential/);
  assert.equal(
    vm.runInContext('aiProviderStatusMessage("AI_MODEL_CAPABILITY_MISSING", "")', context),
    "The selected model does not declare vision support.",
  );
  assert.equal(
    vm.runInContext('aiProviderStatusMessage("UNKNOWN_CODE", "Fallback copy.")', context),
    "Fallback copy.",
  );
  assert.equal(
    vm.runInContext('aiProviderStatusMessage("AI_PROVIDER_BUSY", "")', context),
    "Another AI provider operation is already running.",
  );

  const missingHint = vm.runInContext(
    'aiProviderCredentialHint({ credential_env: "OPENCODE_API_KEY" })',
    context,
  );
  assert.ok(missingHint.includes("OPENCODE_API_KEY"));

  const builtinActions = vm.runInContext(
    'aiProviderRowActions({ provider_id: "nvidia-nim", source: "builtin", credential_available: true, supports_vision: true }, "nvidia-nim")',
    context,
  );
  assert.equal(builtinActions.canEdit, false);
  assert.equal(builtinActions.canDelete, false);

  const activeDeclared = vm.runInContext(
    'aiProviderRowActions({ provider_id: "opencode-go", source: "declared", credential_available: true, supports_vision: true }, "opencode-go")',
    context,
  );
  assert.equal(activeDeclared.canEdit, true);
  assert.equal(activeDeclared.canDelete, false);

  const inactiveUncredentialed = vm.runInContext(
    'aiProviderRowActions({ provider_id: "second-provider", source: "declared", credential_available: false, supports_vision: false }, "opencode-go")',
    context,
  );
  assert.equal(inactiveUncredentialed.canDelete, true);
  assert.equal(inactiveUncredentialed.canPing, false);
  assert.equal(inactiveUncredentialed.canTestVision, false);

  const copy = [authenticationCopy, missingHint].join(" ");
  assert.ok(!/Bearer |apiKey|api_key|Authorization|\/srv\/|\/etc\//.test(copy));

  const rowBody = extractFunction(APP_SOURCE, "buildAiProviderRow");
  assert.ok(rowBody.includes("if (availability.canEdit)"));
  assert.ok(rowBody.includes("if (availability.canDelete)"));
  assert.ok(rowBody.includes("!availability.canPing"));
  assert.ok(rowBody.includes("!availability.canTestVision"));
});

test("record payload builder mirrors the on-disk field names without secrets", () => {
  const context = createAiProvidersHarness(async () => response({}, 404));
  const record = vm.runInContext(
    'JSON.parse(JSON.stringify(buildAiProviderRecordPayload({'
      + ' name: "OpenCode Go",'
      + ' baseUrl: "https://opencode.ai/zen/go/v1",'
      + ' credentialEnv: "OPENCODE_API_KEY",'
      + ' models: [{ modelId: "deepseek-v4-flash-vision-exp", displayName: "", visionInput: true }]'
      + " })))",
    context,
  );
  assert.deepEqual(Object.keys(record).sort(), [
    "base_url",
    "credential_env",
    "models",
    "name",
    "protocol",
  ]);
  assert.equal(record.protocol, "openai-chat-completions");
  assert.equal(record.credential_env, "OPENCODE_API_KEY");
  assert.deepEqual(record.models["deepseek-v4-flash-vision-exp"], {
    name: "deepseek-v4-flash-vision-exp",
    capabilities: ["vision_input"],
  });

  const preview = vm.runInContext(
    'formatAiProviderJsonPreview(buildAiProviderRecordPayload({ name: "B", baseUrl: "https://b.example/v1", credentialEnv: "B_KEY", models: [] }))',
    context,
  );
  const parsed = JSON.parse(preview);
  assert.deepEqual(Object.keys(parsed), [
    "base_url",
    "credential_env",
    "models",
    "name",
    "protocol",
  ]);
  assert.ok(preview.includes('\n  "base_url"'));
  assert.ok(!/"api_key"|"apikey"|"secret"|"authorization"|"bearer"|"token"/i.test(preview));
});

test("styles reuse the settings dialog language and scope narrow-width rules", () => {
  assert.ok(STYLES_SOURCE.includes("#ai-providers-dialog"));
  assert.ok(STYLES_SOURCE.includes(".ai-provider-row"));
  assert.ok(STYLES_SOURCE.includes(".ai-provider-chip"));
  assert.ok(STYLES_SOURCE.includes(".ai-provider-preview"));
  assert.ok(STYLES_SOURCE.includes(".ai-provider-confirm"));
  assert.ok(STYLES_SOURCE.includes(".ai-provider-model-row__check"));
  const narrowStart = STYLES_SOURCE.lastIndexOf("@media (max-width: 640px)");
  assert.notEqual(narrowStart, -1);
  assert.ok(STYLES_SOURCE.slice(narrowStart, narrowStart + 400).includes("#ai-providers-dialog"));
  assert.ok(STYLES_SOURCE.slice(narrowStart).includes("@media (max-width: 360px)"));
});

test("media list captures the revision and media writes send If-Match", async () => {
  const calls = [];
  const context = createAiProvidersHarness(async (url, options) => {
    const method = (options && options.method) || "GET";
    calls.push({ url, method, headers: (options && options.headers) || {} });
    if (method === "GET") {
      return revisionResponse(
        {
          active_provider_id: null,
          active_model_id: null,
          configuration_source: "unconfigured",
          providers: [],
          supported_protocols: ["openai-chat-completions"],
          limits: {},
        },
        "media-rev-1",
      );
    }
    return revisionResponse({ provider_id: "opencode-go" }, "media-rev-2");
  });

  await vm.runInContext("openAiProvidersDialog()", context);
  assert.equal(
    vm.runInContext("aiProvidersState.revision", context),
    "media-rev-1",
  );

  // A mutation response ETag is consumed directly.
  vm.runInContext(
    'applyAiProvidersRevision({ headers: { get: (name) => (name === "etag" ? "\\"media-rev-9\\"" : null) } })',
    context,
  );
  assert.equal(
    vm.runInContext("aiProvidersState.revision", context),
    "media-rev-9",
  );
  vm.runInContext('aiProvidersState.revision = "media-rev-1"', context);

  context.aiProviderIdInput.value = "opencode-go";
  context.aiProviderNameInput.value = "OpenCode Go";
  context.aiProviderBaseUrlInput.value = "https://opencode.ai/zen/go/v1";
  context.aiProviderCredentialEnvInput.value = "OPENCODE_API_KEY";
  await vm.runInContext(
    'saveAiProviderRecord({ providerId: "opencode-go", baseUrl: "https://opencode.ai/zen/go/v1", credentialEnv: "OPENCODE_API_KEY", models: [{ modelId: "deepseek-v4-flash-vision-exp", displayName: "", visionInput: true }] })',
    context,
  );

  const put = calls.find((call) => call.method === "PUT");
  assert.ok(put, "the media record write must issue a PUT");
  assert.equal(put.headers["If-Match"], '"media-rev-1"');
  // The post-save list read re-syncs the captured revision.
  assert.equal(
    vm.runInContext("aiProvidersState.revision", context),
    "media-rev-1",
  );
});

test("a media write without a captured revision stays header-free and still valid", async () => {
  const calls = [];
  const context = createAiProvidersHarness(async (url, options) => {
    const method = (options && options.method) || "GET";
    calls.push({ url, method, headers: (options && options.headers) || {} });
    if (method === "GET") return response({ providers: [], limits: {} });
    return response({ provider_id: "opencode-go" });
  });

  await vm.runInContext("openAiProvidersDialog()", context);
  assert.equal(vm.runInContext("aiProvidersState.revision", context), "");

  context.aiProviderIdInput.value = "opencode-go";
  await vm.runInContext(
    'saveAiProviderRecord({ providerId: "opencode-go", baseUrl: "https://opencode.ai/zen/go/v1", credentialEnv: "OPENCODE_API_KEY", models: [] })',
    context,
  );
  const put = calls.find((call) => call.method === "PUT");
  assert.ok(put);
  assert.equal(put.headers["If-Match"], undefined);
});

test("a successful media save invalidates the research settings revision", async () => {
  const context = createAiProvidersHarness(async (url, options) => {
    const method = (options && options.method) || "GET";
    if (method === "GET") {
      return revisionResponse({ providers: [], limits: {} }, "media-rev-1");
    }
    return revisionResponse({ provider_id: "opencode-go" }, "media-rev-2");
  });

  await vm.runInContext("openAiProvidersDialog()", context);
  assert.equal(
    vm.runInContext("researchSettingsState.revision", context),
    "research-rev-1",
  );

  context.aiProviderIdInput.value = "opencode-go";
  await vm.runInContext(
    'saveAiProviderRecord({ providerId: "opencode-go", baseUrl: "https://opencode.ai/zen/go/v1", credentialEnv: "OPENCODE_API_KEY", models: [] })',
    context,
  );

  const research = vm.runInContext("researchSettingsState", context);
  assert.equal(research.revision, "", "the sibling revision must be dropped");
  assert.equal(research.stale, true);
});

test("a successful research save invalidates the media revision", async () => {
  const context = createAiProvidersHarness(async (url, options) => {
    const method = (options && options.method) || "GET";
    if (method === "GET") {
      return revisionResponse({ providers: [], limits: {} }, "media-rev-1");
    }
    return revisionResponse(
      {
        revision: "research-rev-2",
        configuration_present: true,
        provider_id: "openai-responses",
        settings: {},
        credential_available: true,
        models: [],
        limits: {},
        changed: true,
      },
      "research-rev-2",
    );
  });

  await vm.runInContext("openAiProvidersDialog()", context);
  assert.equal(vm.runInContext("aiProvidersState.revision", context), "media-rev-1");

  await vm.runInContext(
    'saveResearchSettings({ enabled: true, model_id: "gpt-5.6-luna", daily_budget_usd: "10", monthly_budget_usd: "30", search_reservation_usd: "0.5", research_reservation_usd: "5" })',
    context,
  );

  assert.equal(
    vm.runInContext("aiProvidersState.revision", context),
    "",
    "the media revision must be dropped after a research write",
  );
});
