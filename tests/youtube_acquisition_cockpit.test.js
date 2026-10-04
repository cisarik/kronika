const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_SOURCE = fs.readFileSync(
  path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js"),
  "utf8",
);
const INDEX_SOURCE = fs.readFileSync(
  path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html"),
  "utf8",
);
const STYLES_SOURCE = fs.readFileSync(
  path.resolve(__dirname, "../src/kronika/adapters/api/web/styles.css"),
  "utf8",
);

const RECOVERY_KEY = "framenest.youtube.currentClaim.v1";
const RECOVERY_KEY_CURRENT = "kronika.youtube.currentClaim.v1";
const VIDEO_ID = "AbCdEf123_-";
// The confirmation prose the served shell shows is owned by `app.js` and must
// carry the same brand as the extension manifest, so the pin is derived from
// that manifest rather than spelled out here. A one-sided rename on either side
// breaks the equality assertions below instead of passing silently.
const BRAND = JSON.parse(
  fs.readFileSync(path.resolve(__dirname, "../extension/manifest.json"), "utf8"),
).name.split(/\s+/)[0];
const CREATE_CONFIRMATION_MESSAGE = `${BRAND} will start the acquisition in the background. Closing the cockpit will not cancel it. Acquired media remains unpublished until it is reviewed and published in Manage media.`;

function extractFunction(source, name) {
  const markers = [`async function ${name}(`, `function ${name}(`];
  let start = -1;
  for (const marker of markers) {
    start = source.indexOf(marker);
    if (start !== -1) break;
  }
  assert.notEqual(start, -1, `missing production function ${name}`);
  const bodyOpen = source.indexOf("{", source.indexOf(")", start));
  let depth = 0;
  for (let index = bodyOpen; index < source.length; index += 1) {
    if (source[index] === "{") depth += 1;
    if (source[index] === "}") {
      depth -= 1;
      if (depth === 0) return source.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production function ${name}`);
}

function extractYouTubeClaimValidationSource() {
  const constantsStart = APP_SOURCE.indexOf("const YOUTUBE_CLAIM_VIDEO_ID_PATTERN");
  const functionStart = APP_SOURCE.indexOf("function validateYouTubeClaimUrl(");
  assert.ok(constantsStart >= 0);
  assert.ok(functionStart > constantsStart);
  return `${APP_SOURCE.slice(constantsStart, functionStart)}\n${extractFunction(
    APP_SOURCE,
    "validateYouTubeClaimUrl",
  )}`;
}

function evaluate(source, prelude, expression) {
  const context = {
    console,
    Set,
    Map,
    Array,
    String,
    Boolean,
    Object,
    Number,
    URL,
    URLSearchParams,
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(`${prelude}\n${source}\n`, context);
  return expression ? vm.runInContext(expression, context) : context;
}

function extractMarkupElementById(source, id) {
  const escapedId = id.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const pattern = new RegExp(
    `<([a-z][a-z0-9-]*)\\b([^>]*\\bid="${escapedId}"[^>]*)>([\\s\\S]*?)<\\/\\1>`,
    "i",
  );
  const match = source.match(pattern);
  assert.ok(match, `missing markup element ${id}`);
  return {
    tagName: match[1].toLowerCase(),
    attributes: match[2],
    content: match[3],
    markup: match[0],
  };
}

function markupAttribute(attributes, name) {
  const escapedName = name.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const match = attributes.match(new RegExp(`(?:^|\\s)${escapedName}="([^"]*)"`, "i"));
  return match ? match[1] : null;
}

function createClaimElement(tagName = "div") {
  const attributes = new Map();
  const listeners = new Map();
  return {
    tagName: String(tagName).toUpperCase(),
    attributes,
    listeners,
    dataset: {},
    hidden: false,
    disabled: false,
    textContent: "",
    value: "",
    valid: true,
    focusCount: 0,
    setAttribute(name, value) {
      attributes.set(String(name).toLowerCase(), String(value));
    },
    getAttribute(name) {
      const key = String(name).toLowerCase();
      return attributes.has(key) ? attributes.get(key) : null;
    },
    removeAttribute(name) {
      attributes.delete(String(name).toLowerCase());
    },
    addEventListener(type, listener) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(listener);
    },
    dispatchEvent(event) {
      event.target = event.target || this;
      event.currentTarget = this;
      event.defaultPrevented = false;
      event.preventDefault = () => {
        event.defaultPrevented = true;
      };
      for (const listener of [...(listeners.get(event.type) || [])]) listener(event);
      return !event.defaultPrevented;
    },
    checkValidity() {
      return this.valid;
    },
    focus() {
      this.focusCount += 1;
    },
  };
}

function createYouTubeAccessibilityHarness({ confirmationAccepted = true } = {}) {
  const youtubeClaimUrlInput = createClaimElement("input");
  const youtubeClaimUrlError = createClaimElement("p");
  youtubeClaimUrlError.hidden = true;
  const elements = {
    youtubeClaimForm: createClaimElement("form"),
    youtubeClaimUrlInput,
    youtubeClaimUrlError,
    youtubeClaimRow: createClaimElement("section"),
    youtubeClaimStateLabel: createClaimElement("h3"),
    youtubeClaimMessage: createClaimElement("p"),
    youtubeClaimDetails: createClaimElement("div"),
    youtubeClaimMetadata: createClaimElement("p"),
    youtubeClaimRequester: createClaimElement("p"),
    youtubeClaimPublication: createClaimElement("p"),
    youtubeClaimFailure: createClaimElement("p"),
    youtubeClaimSubmitButton: createClaimElement("button"),
    youtubeClaimRetryButton: createClaimElement("button"),
    youtubeClaimResetButton: createClaimElement("button"),
    youtubeClaimManageMediaButton: createClaimElement("button"),
    youtubeClaimDialogTitle: createClaimElement("h2"),
  };
  const state = {
    generation: 0,
    claimId: null,
    snapshot: null,
    requestOwner: null,
    pollOwner: null,
    pollTimer: null,
    pollRetryDelayMs: 1000,
    submitting: false,
    retrying: false,
    recoveryAttempted: false,
    submissionResult: null,
    message: "",
    errorMessage: "",
    urlError: "",
  };
  const fetchCalls = [];
  const confirmationRequests = [];
  const storageWrites = [];
  const context = {
    console,
    Set,
    Map,
    Array,
    String,
    Boolean,
    Object,
    Number,
    Math,
    Promise,
    encodeURIComponent,
    clearTimeout,
    setTimeout,
    URL,
    URLSearchParams,
    youtubeClaimState: state,
    YOUTUBE_CLAIMS_ENDPOINT: "/api/admin/youtube/claims",
    identityAllowsAdminWorkflow: () => true,
    identityAllowsYouTubeClaim: () => true,
    framenestMutationHeaders: (headers) => Object.assign({ "X-FrameNest-Request": "1" }, headers),
    saveYouTubeClaimRecovery(claimId) {
      storageWrites.push(String(claimId));
    },
    stopYouTubeClaimPolling() {
      state.pollOwner = null;
      state.pollTimer = null;
    },
    scheduleYouTubeClaimPolling() {},
    requestConfirmation: async (options) => {
      context.confirmationCalls += 1;
      confirmationRequests.push(options);
      return confirmationAccepted;
    },
    confirmationCalls: 0,
    fetch: async (url, options) => {
      fetchCalls.push({ url: String(url), options });
      return {
        ok: true,
        status: 201,
        json: async () => ({
          claim_id: "claim-accepted-1",
          state: "cataloged",
          phase: "cataloged",
          catalog_state: "cataloged",
          metadata_state: "unknown",
          publication_state: "unpublished",
        }),
      };
    },
    ...elements,
  };
  context.claimYouTubeRequest = (kind) => {
    if (state.requestOwner) return null;
    state.generation += 1;
    const owner = { generation: state.generation, claimId: state.claimId, kind };
    state.requestOwner = owner;
    return owner;
  };
  context.releaseYouTubeRequest = (owner) => {
    if (state.requestOwner !== owner) return false;
    state.requestOwner = null;
    return true;
  };
  context.youtubeClaimContextStillCurrent = (owner) => Boolean(
    owner
      && state.generation === owner.generation
      && (!owner.claimId || state.claimId === owner.claimId),
  );
  context.youtubeClaimContext = (claimId = state.claimId) => ({
    generation: state.generation,
    claimId: claimId || null,
  });

  const functions = [
    extractYouTubeClaimValidationSource(),
    extractFunction(APP_SOURCE, "youtubeClaimStateIsTerminal"),
    extractFunction(APP_SOURCE, "youtubeClaimShouldPoll"),
    extractFunction(APP_SOURCE, "normalizeYouTubeClaimSnapshot"),
    extractFunction(APP_SOURCE, "youtubeClaimPhaseLabel"),
    extractFunction(APP_SOURCE, "youtubeClaimStatusMessage"),
    extractFunction(APP_SOURCE, "youtubeClaimMetadataLabel"),
    extractFunction(APP_SOURCE, "youtubeClaimPublicationLabel"),
    extractFunction(APP_SOURCE, "renderYouTubeClaimCockpit"),
    extractFunction(APP_SOURCE, "applyYouTubeClaimSnapshot"),
    extractFunction(APP_SOURCE, "submitYouTubeClaim"),
  ].join("\n");
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(functions, context);
  const eventWiringStart = APP_SOURCE.lastIndexOf("if (youtubeClaimForm) {");
  const eventWiringEnd = APP_SOURCE.indexOf("if (youtubeClaimRetryButton) {", eventWiringStart);
  assert.ok(eventWiringStart >= 0);
  assert.ok(eventWiringEnd > eventWiringStart);
  vm.runInContext(APP_SOURCE.slice(eventWiringStart, eventWiringEnd), context);
  context.renderYouTubeClaimCockpit = vm.runInContext("renderYouTubeClaimCockpit", context);
  context.submitYouTubeClaim = vm.runInContext("submitYouTubeClaim", context);
  context.renderYouTubeClaimCockpit();
  return { context, elements, state, fetchCalls, confirmationRequests, storageWrites };
}

test("YouTube cockpit opener has exact visible and accessible identity", () => {
  const opener = extractMarkupElementById(INDEX_SOURCE, "youtube-claim-open-button");
  assert.equal(opener.tagName, "button");
  assert.equal(markupAttribute(opener.attributes, "type"), "button");
  assert.equal(markupAttribute(opener.attributes, "aria-label"), "Acquire YouTube media");
  assert.match(opener.attributes, /(?:^|\s)hidden(?:\s|$)/);
  assert.doesNotMatch(opener.attributes, /(?:^|\s)disabled(?:\s|$|=)/);
  assert.doesNotMatch(opener.attributes, /(?:^|\s)tabindex="-1"/);
  assert.doesNotMatch(opener.content, /<[^>]+>/);
  assert.equal(opener.content.trim().replace(/\s+/g, " "), "YouTube");
  assert.doesNotMatch(opener.markup, /Claim YouTube/);
  assert.match(
    APP_SOURCE,
    /youtubeClaimOpenButton\.addEventListener\("click", openYouTubeClaimDialog\)/,
  );
});

test("YouTube cockpit markup is administrator-gated and accessible", () => {
  assert.match(INDEX_SOURCE, /id="youtube-claim-open-button"[^>]*hidden/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-dialog"[^>]*aria-labelledby="youtube-claim-dialog-title"/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-form"/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-url"[^>]*type="url"/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-url-error"[^>]*role="alert"[^>]*hidden/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-message"[^>]*role="status"/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-failure"[^>]*role="alert"/);
  assert.match(INDEX_SOURCE, /id="youtube-claim-manage-media-button"/);
  assert.match(APP_SOURCE, /capabilities\.has\("youtube\.acquire"\)/);
  assert.match(APP_SOURCE, /function identityAllowsYouTubeClaim\(\)/);
  assert.match(APP_SOURCE, /aria-invalid/);
  assert.match(APP_SOURCE, /youtube-claim-url-note youtube-claim-url-error/);

  const harness = createYouTubeAccessibilityHarness();
  const { context, elements, state, fetchCalls, confirmationRequests } = harness;
  const input = elements.youtubeClaimUrlInput;
  const error = elements.youtubeClaimUrlError;
  assert.equal(input.getAttribute("aria-invalid"), "false");
  assert.equal(input.getAttribute("aria-describedby"), "youtube-claim-url-note");
  assert.equal(error.hidden, true);

  input.value = "https://www.youtube.com/watch?v=private-test-value";
  input.valid = false;
  const invalidEvent = { type: "invalid" };
  input.dispatchEvent(invalidEvent);
  assert.equal(invalidEvent.defaultPrevented, true);
  assert.equal(input.getAttribute("aria-invalid"), "true");
  assert.equal(
    input.getAttribute("aria-describedby"),
    "youtube-claim-url-note youtube-claim-url-error",
  );
  assert.equal(error.hidden, false);
  assert.equal(
    error.textContent,
    "Enter a supported single-video YouTube URL before submitting the claim.",
  );
  assert.equal(error.textContent.includes(input.value), false);

  input.valid = true;
  input.dispatchEvent({ type: "input" });
  assert.equal(input.getAttribute("aria-invalid"), "false");
  assert.equal(input.getAttribute("aria-describedby"), "youtube-claim-url-note");
  assert.equal(error.hidden, true);

  input.value = "not a URL";
  input.valid = false;
  const pointerSubmit = { type: "submit" };
  elements.youtubeClaimForm.dispatchEvent(pointerSubmit);
  assert.equal(pointerSubmit.defaultPrevented, true);
  assert.equal(context.confirmationCalls, 0);
  assert.equal(fetchCalls.length, 0);
  assert.equal(input.getAttribute("aria-invalid"), "true");

  const keyboardSubmit = { type: "submit", submitter: elements.youtubeClaimSubmitButton };
  elements.youtubeClaimForm.dispatchEvent(keyboardSubmit);
  assert.equal(keyboardSubmit.defaultPrevented, true);
  assert.equal(context.confirmationCalls, 0);
  assert.equal(fetchCalls.length, 0);

  const submittedUrl = `https://www.youtube.com/watch?v=${VIDEO_ID}`;
  input.value = submittedUrl;
  input.valid = true;
  return context.submitYouTubeClaim().then(() => {
    assert.equal(confirmationRequests.length, 1);
    assert.equal(confirmationRequests[0].message, CREATE_CONFIRMATION_MESSAGE);
    assert.match(confirmationRequests[0].message, /acquisition in the background/);
    assert.match(confirmationRequests[0].message, /Closing the cockpit will not cancel it/);
    assert.match(confirmationRequests[0].message, /media remains unpublished/);
    assert.match(confirmationRequests[0].message, /reviewed and published in Manage media/);
    assert.equal(confirmationRequests[0].message.includes(submittedUrl), false);
    assert.equal(confirmationRequests[0].focusReturn, elements.youtubeClaimSubmitButton);
    assert.equal(fetchCalls.length, 1);
    assert.deepEqual(
      JSON.parse(fetchCalls[0].options.body),
      { url: submittedUrl, confirmation_method: "interactive" },
    );
    assert.equal(input.value, "");
    assert.equal(state.urlError, "");
    assert.equal(input.getAttribute("aria-invalid"), "false");
    assert.equal(input.getAttribute("aria-describedby"), "youtube-claim-url-note");
    assert.equal(error.hidden, true);
    context.renderYouTubeClaimCockpit();
    assert.equal(input.value, "");
  });
});

test("YouTube URL validator rejects the complete unsupported-input contract", () => {
  const context = evaluate(extractYouTubeClaimValidationSource(), "");
  const invalidInputs = [
    ["empty", ""],
    ["whitespace-only", "   "],
    ["plain text", "not a URL"],
    ["relative URL", `/watch?v=${VIDEO_ID}`],
    ["non-HTTP scheme", `ftp://www.youtube.com/watch?v=${VIDEO_ID}`],
    ["HTTP YouTube URL", `http://www.youtube.com/watch?v=${VIDEO_ID}`],
    ["HTTP non-YouTube host", `http://media.example.invalid/watch?v=${VIDEO_ID}`],
    ["production non-YouTube HTTPS failure", `https://media.example.invalid/watch?v=${VIDEO_ID}`],
    ["lookalike host", `https://youtube.com.example.invalid/watch?v=${VIDEO_ID}`],
    ["hostname containing youtube text", `https://notyoutube.example.invalid/watch?v=${VIDEO_ID}`],
    ["playlist-only production failure", "https://www.youtube.com/playlist?list=PLSynthetic"],
    ["channel URL", `https://www.youtube.com/channel/${VIDEO_ID}`],
    ["search URL", "https://www.youtube.com/results?search_query=synthetic"],
    ["missing video ID", "https://www.youtube.com/watch?t=1"],
    ["empty video ID", "https://www.youtube.com/watch?v="],
    ["malformed production video ID", "https://www.youtube.com/watch?v=too-short"],
    ["invalid video ID length", "https://youtu.be/AbCdEf123_"],
    ["invalid video ID characters", "https://youtu.be/AbCdEf123!*"],
    ["duplicate video ID", `https://www.youtube.com/watch?v=${VIDEO_ID}&v=${VIDEO_ID}`],
    ["conflicting video ID", `https://www.youtube.com/watch?v=${VIDEO_ID}&v=ZyXwVu987_-`],
    ["playlist query on video", `https://www.youtube.com/watch?v=${VIDEO_ID}&list=PLSynthetic`],
    ["unsupported embedded URL", `https://www.youtube.com/embed/${VIDEO_ID}`],
    ["unsupported live URL", `https://www.youtube.com/live/${VIDEO_ID}`],
    ["surrounding whitespace", ` https://www.youtube.com/watch?v=${VIDEO_ID} `],
    ["fragment", `https://youtu.be/${VIDEO_ID}#details`],
    ["userinfo", `https://user@www.youtube.com/watch?v=${VIDEO_ID}`],
    ["non-standard port", `https://www.youtube.com:444/watch?v=${VIDEO_ID}`],
    ["query field without value separator", `https://www.youtube.com/watch?v=${VIDEO_ID}&t`],
    [
      "too many query fields",
      `https://www.youtube.com/watch?v=${VIDEO_ID}&t=1&si=a&feature=b&x1=1&x2=2&x3=3&x4=4&x5=5`,
    ],
  ];

  for (const [name, submitted] of invalidInputs) {
    const result = vm.runInContext(`validateYouTubeClaimUrl(${JSON.stringify(submitted)})`, context);
    assert.equal(result.supported, false, name);
    assert.match(result.message, /YouTube URL|single-video YouTube URL/, name);
    if (submitted) assert.equal(result.message.includes(submitted), false, name);
  }
});

test("unsupported URL-shaped inputs cannot reach final confirmation or mutation", async () => {
  const invalidInputs = [
    `https://media.example.invalid/watch?v=${VIDEO_ID}`,
    "https://www.youtube.com/playlist?list=PLSynthetic",
    "https://www.youtube.com/watch?v=too-short",
    `https://youtube.com.example.invalid/watch?v=${VIDEO_ID}`,
    `https://www.youtube.com/watch?v=${VIDEO_ID}&v=ZyXwVu987_-`,
  ];
  for (const submitted of invalidInputs) {
    const {
      context,
      elements,
      fetchCalls,
      confirmationRequests,
      storageWrites,
    } = createYouTubeAccessibilityHarness();
    elements.youtubeClaimUrlInput.value = submitted;
    elements.youtubeClaimUrlInput.valid = true;

    await context.submitYouTubeClaim();

    assert.equal(confirmationRequests.length, 0, submitted);
    assert.equal(fetchCalls.length, 0, submitted);
    assert.equal(storageWrites.length, 0, submitted);
    assert.equal(elements.youtubeClaimUrlInput.getAttribute("aria-invalid"), "true", submitted);
    assert.equal(
      elements.youtubeClaimUrlInput.getAttribute("aria-describedby"),
      "youtube-claim-url-note youtube-claim-url-error",
      submitted,
    );
    assert.equal(elements.youtubeClaimUrlError.hidden, false, submitted);
    assert.ok(elements.youtubeClaimUrlInput.focusCount > 0, submitted);
  }
});

test("every canonical supported URL form reaches only cancellable confirmation", async () => {
  const supportedInputs = [
    `https://www.youtube.com/watch?v=${VIDEO_ID}`,
    `https://youtube.com/watch?v=${VIDEO_ID}&t=12`,
    `https://m.youtube.com/watch?v=${VIDEO_ID}&feature=synthetic`,
    `https://www.youtube.com/shorts/${VIDEO_ID}`,
    `https://youtube.com/shorts/${VIDEO_ID}/?t=1`,
    `https://m.youtube.com/shorts/${VIDEO_ID}?si=synthetic`,
    `https://youtu.be/${VIDEO_ID}`,
    `https://youtu.be/${VIDEO_ID}/?t=1`,
    `https://www.youtube.com:443/watch?v=${VIDEO_ID}&si=synthetic`,
  ];

  for (const submitted of supportedInputs) {
    const {
      context,
      elements,
      fetchCalls,
      confirmationRequests,
      storageWrites,
    } = createYouTubeAccessibilityHarness({ confirmationAccepted: false });
    elements.youtubeClaimUrlInput.value = submitted;
    elements.youtubeClaimUrlInput.valid = true;

    await context.submitYouTubeClaim();

    assert.equal(confirmationRequests.length, 1, submitted);
    assert.equal(confirmationRequests[0].confirmLabel, "Claim media", submitted);
    assert.equal(confirmationRequests[0].message.includes(submitted), false, submitted);
    assert.equal(confirmationRequests[0].focusReturn, elements.youtubeClaimSubmitButton, submitted);
    assert.equal(fetchCalls.length, 0, submitted);
    assert.equal(storageWrites.length, 0, submitted);
    assert.equal(elements.youtubeClaimUrlInput.value, submitted);
  }
});

test("cancelling create confirmation preserves URL privacy and sends no request", async () => {
  const { context, elements, fetchCalls, confirmationRequests } = createYouTubeAccessibilityHarness({
    confirmationAccepted: false,
  });
  const submittedUrl = `https://www.youtube.com/watch?v=${VIDEO_ID}`;
  elements.youtubeClaimUrlInput.value = submittedUrl;
  elements.youtubeClaimUrlInput.valid = true;

  await context.submitYouTubeClaim();

  assert.equal(confirmationRequests.length, 1);
  assert.equal(confirmationRequests[0].message, CREATE_CONFIRMATION_MESSAGE);
  assert.equal(confirmationRequests[0].message.includes(submittedUrl), false);
  assert.doesNotMatch(confirmationRequests[0].message, /https?:\/\//);
  assert.doesNotMatch(confirmationRequests[0].message, /video ID|claim ID|media ID|metadata|AI analysis/i);
  assert.equal(fetchCalls.length, 0);
});

test("terminal claim copy distinguishes duplicate reuse and unpublished catalog media", () => {
  const statusMessage = extractFunction(APP_SOURCE, "youtubeClaimStatusMessage");
  const context = evaluate(
    statusMessage,
    "let youtubeClaimState = { message: \"\" };",
  );
  assert.match(
    vm.runInContext(
      'youtubeClaimStatusMessage({ state: "duplicate_resolved" })',
      context,
    ),
    /No new download was required.*no second catalog item was created/,
  );
  assert.match(
    vm.runInContext(
      'youtubeClaimStatusMessage({ state: "duplicate_resolved", submission_result: "terminal_duplicate_reuse" })',
      context,
    ),
    /No new download was required.*no second catalog item was created/,
  );
  assert.match(
    vm.runInContext(
      'youtubeClaimStatusMessage({ state: "cataloged", publication_state: "unpublished" })',
      context,
    ),
    /not visible to ordinary Gallery users.*published in Manage media/,
  );
  assert.match(
    vm.runInContext(
      'youtubeClaimStatusMessage({ state: "cataloged", publication_state: "published" })',
      context,
    ),
    /available in Gallery/,
  );
});

test("YouTube capability fails closed until exact identity resolution", () => {
  const functions = [
    extractFunction(APP_SOURCE, "identityAllowsYouTubeClaim"),
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
  ].join("\n");
  const context = evaluate(
    functions,
    `let identityState = {
      resolved: false,
      available: false,
      capabilities: new Set(),
    };
    const uploadOpenButton = null;
    const detailsEditButton = null;
    const adminMediaOpenButton = null;
    const youtubeClaimOpenButton = { hidden: false };
    const youtubeClaimDialog = null;
    const adminMediaBrowser = null;
    function identityHasCapability() { return false; }
    function identityAllowsAdminWorkflow() { return false; }
    function closeYouTubeClaimDialog() {}
    function closeAdminMediaBrowser() {}
    function updateMetadataControls() {}`,
  );
  assert.equal(vm.runInContext("identityAllowsYouTubeClaim()", context), false);
  vm.runInContext("applyIdentityCapabilities()", context);
  assert.equal(vm.runInContext("youtubeClaimOpenButton.hidden", context), true);
  vm.runInContext("identityState = { resolved: true, available: true, capabilities: new Set() }", context);
  assert.equal(vm.runInContext("identityAllowsYouTubeClaim()", context), false);
  vm.runInContext("applyIdentityCapabilities()", context);
  assert.equal(vm.runInContext("youtubeClaimOpenButton.hidden", context), true);
  vm.runInContext('identityState.capabilities.add("youtube.acquire")', context);
  assert.equal(vm.runInContext("identityAllowsYouTubeClaim()", context), true);
  vm.runInContext("applyIdentityCapabilities()", context);
  assert.equal(vm.runInContext("youtubeClaimOpenButton.hidden", context), false);
});

test("retry confirmation keeps its existing copy and focus return", async () => {
  const context = evaluate(
    extractFunction(APP_SOURCE, "retryYouTubeClaim"),
    `let youtubeClaimState = {
      snapshot: { claim_id: "claim-retry-test", state: "failed" },
      requestOwner: null,
    };
    const youtubeClaimRetryButton = { marker: "retry-button" };
    let retryConfirmation = null;
    async function requestConfirmation(options) {
      retryConfirmation = options;
      return false;
    }
    function identityAllowsYouTubeClaim() { return true; }`,
  );

  await vm.runInContext("retryYouTubeClaim()", context);

  assert.equal(vm.runInContext("retryConfirmation.title", context), "Retry YouTube claim");
  assert.equal(
    vm.runInContext("retryConfirmation.message", context),
    "Retry the failed claim using the same server-owned claim context?",
  );
  assert.equal(vm.runInContext("retryConfirmation.dismissLabel", context), "Cancel");
  assert.equal(vm.runInContext("retryConfirmation.confirmLabel", context), "Retry claim");
  assert.equal(
    vm.runInContext("retryConfirmation.focusReturn === youtubeClaimRetryButton", context),
    true,
  );
});

test("claim recovery stores only the opaque claim ID in session storage", () => {
  const storage = new Map();
  const context = evaluate(
    [
      `const YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "${RECOVERY_KEY}";`,
      `const KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "${RECOVERY_KEY_CURRENT}";`,
      extractFunction(APP_SOURCE, "readMigratedStorageItem"),
      extractFunction(APP_SOURCE, "clearMigratedStorageItem"),
      "let youtubeClaimState = { claimId: \"claim-opaque-1\", message: \"\" };",
      extractFunction(APP_SOURCE, "youtubeClaimStorage"),
      extractFunction(APP_SOURCE, "saveYouTubeClaimRecovery"),
      extractFunction(APP_SOURCE, "clearYouTubeClaimRecovery"),
      extractFunction(APP_SOURCE, "loadYouTubeClaimRecovery"),
    ].join("\n"),
    "",
  );
  context.window = {
    sessionStorage: {
      setItem(key, value) {
        storage.set(key, String(value));
      },
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      removeItem(key) {
        storage.delete(key);
      },
    },
  };
  vm.runInContext("saveYouTubeClaimRecovery()", context);
  assert.equal(storage.get(RECOVERY_KEY_CURRENT), "claim-opaque-1");
  assert.equal(storage.get(RECOVERY_KEY_CURRENT).includes("youtube.com"), false);
  assert.equal(vm.runInContext("loadYouTubeClaimRecovery()", context), "claim-opaque-1");
  vm.runInContext("clearYouTubeClaimRecovery()", context);
  assert.equal(storage.has(RECOVERY_KEY_CURRENT), false);
});

test("claim recovery reads the retired session key only when the current key is absent", () => {
  const storage = new Map();
  const context = claimRecoveryContext(storage);

  storage.set(RECOVERY_KEY, "claim-retired-only");
  assert.equal(context.run("loadYouTubeClaimRecovery()"), "claim-retired-only");

  storage.set(RECOVERY_KEY_CURRENT, "claim-current-only");
  assert.equal(context.run("loadYouTubeClaimRecovery()"), "claim-current-only");

  storage.set(RECOVERY_KEY, "claim-retired-only");
  assert.equal(context.run("loadYouTubeClaimRecovery()"), "claim-current-only");

  context.run('saveYouTubeClaimRecovery("claim-written")');
  assert.equal(storage.get(RECOVERY_KEY_CURRENT), "claim-written");
  assert.equal(storage.get(RECOVERY_KEY), "claim-retired-only");
});

test("claim recovery write creates the current key and leaves a retired-only key untouched", () => {
  const storage = new Map();
  const context = claimRecoveryContext(storage);

  storage.set(RECOVERY_KEY, "claim-retired-only");
  context.run('saveYouTubeClaimRecovery("claim-written")');
  assert.equal(storage.get(RECOVERY_KEY_CURRENT), "claim-written");
  assert.equal(storage.get(RECOVERY_KEY), "claim-retired-only");
});

test("claim recovery consume removes both spellings so the fallback cannot resurrect the claim", () => {
  const storage = new Map();
  const context = claimRecoveryContext(storage);

  storage.set(RECOVERY_KEY, "claim-retired-only");
  context.run("clearYouTubeClaimRecovery()");
  assert.equal(storage.has(RECOVERY_KEY), false);
  assert.equal(storage.has(RECOVERY_KEY_CURRENT), false);
  assert.equal(context.run("loadYouTubeClaimRecovery()"), null);
});

function claimRecoveryContext(storage) {
  const context = evaluate(
    [
      `const YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "${RECOVERY_KEY}";`,
      `const KRONIKA_YOUTUBE_CLAIM_RECOVERY_STORAGE_KEY = "${RECOVERY_KEY_CURRENT}";`,
      extractFunction(APP_SOURCE, "readMigratedStorageItem"),
      extractFunction(APP_SOURCE, "clearMigratedStorageItem"),
      "let youtubeClaimState = { claimId: \"claim-opaque-1\", message: \"\" };",
      extractFunction(APP_SOURCE, "youtubeClaimStorage"),
      extractFunction(APP_SOURCE, "saveYouTubeClaimRecovery"),
      extractFunction(APP_SOURCE, "clearYouTubeClaimRecovery"),
      extractFunction(APP_SOURCE, "loadYouTubeClaimRecovery"),
    ].join("\n"),
    "",
  );
  context.window = {
    sessionStorage: {
      setItem(key, value) {
        storage.set(key, String(value));
      },
      getItem(key) {
        return storage.has(key) ? storage.get(key) : null;
      },
      removeItem(key) {
        storage.delete(key);
      },
    },
  };
  return {
    run(code) {
      return vm.runInContext(code, context);
    },
  };
}

test("claim lifecycle uses the Phase A endpoints and interactive confirmation", () => {
  assert.match(APP_SOURCE, /const YOUTUBE_CLAIMS_ENDPOINT = "\/api\/admin\/youtube\/claims";/);
  assert.match(APP_SOURCE, /method: "POST"[\s\S]*body: JSON\.stringify\(\{ url, confirmation_method: "interactive" \}\)/);
  assert.match(APP_SOURCE, /\/retry[\s\S]*body: JSON\.stringify\(\{ confirmation_method: "interactive" \}\)/);
  assert.match(APP_SOURCE, /headers: framenestMutationHeaders\([\s\S]*"Content-Type": "application\/json"/);
  assert.match(APP_SOURCE, /function scheduleYouTubeClaimPolling\(\s*owner = youtubeClaimContext\(\),/);
  assert.match(APP_SOURCE, /const YOUTUBE_CLAIM_POLL_INTERVAL_MS = 1000;/);
  assert.match(APP_SOURCE, /const YOUTUBE_CLAIM_POLL_RETRY_MAX_MS = 10000;/);
});

test("polling stops for every terminal claim state and keeps bounded backoff", () => {
  const functions = [
    extractFunction(APP_SOURCE, "youtubeClaimStateIsTerminal"),
    extractFunction(APP_SOURCE, "youtubeClaimShouldPoll"),
  ].join("\n");
  const context = evaluate(functions, "");
  for (const state of ["failed", "cataloged", "duplicate_resolved"]) {
    assert.equal(vm.runInContext(`youtubeClaimShouldPoll({ state: "${state}" })`, context), false);
  }
  assert.equal(vm.runInContext('youtubeClaimShouldPoll({ state: "downloading" })', context), true);
  assert.match(APP_SOURCE, /Math\.min\([\s\S]*YOUTUBE_CLAIM_POLL_RETRY_MAX_MS/);
});

test("claim cockpit styles wrap on narrow screens and honor reduced motion", () => {
  assert.match(STYLES_SOURCE, /\.youtube-claim-dialog\s*\{[\s\S]*width: min\(680px/);
  assert.match(STYLES_SOURCE, /@media \(max-width: 620px\)[\s\S]*\.youtube-claim-dialog\s*\{[\s\S]*width: calc\(100% - 20px\)/);
  assert.match(STYLES_SOURCE, /@media \(prefers-reduced-motion: reduce\)/);
  assert.match(STYLES_SOURCE, /\.youtube-claim-message[\s\S]*overflow-wrap: anywhere/);
});
