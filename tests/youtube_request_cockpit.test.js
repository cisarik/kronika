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

test("ordinary My YouTube downloads is capability-gated and sanitized", () => {
  assert.match(APP_SOURCE, /identityAllowsYouTubeRequest/);
  assert.match(APP_SOURCE, /capabilities\.has\("youtube\.request"\)/);
  assert.match(APP_SOURCE, /YOUTUBE_REQUESTS_ENDPOINT\s*=\s*"\/api\/youtube\/requests"/);
  assert.match(INDEX_SOURCE, /id="youtube-request-open-button"/);
  assert.match(INDEX_SOURCE, /My YouTube downloads/);
  assert.match(INDEX_SOURCE, /id="youtube-request-dialog"/);
  assert.match(INDEX_SOURCE, /aria-live="polite"/);
  assert.match(APP_SOURCE, /Private until an administrator publishes it/);
  assert.match(APP_SOURCE, /phase === "failed"/);
  assert.doesNotMatch(APP_SOURCE, /youtube\.request[\s\S]{0,200}analysis\.run/);
  assert.doesNotMatch(INDEX_SOURCE, /id="youtube-request-publish"/);
  assert.doesNotMatch(INDEX_SOURCE, /id="youtube-request-ai"/);
  assert.match(STYLES_SOURCE, /\.youtube-request-list/);
  assert.match(STYLES_SOURCE, /min-height:\s*2\.75rem/);
});

test("administrator claim cockpit exposes requester login key only", () => {
  assert.match(INDEX_SOURCE, /id="youtube-claim-requester"/);
  assert.match(APP_SOURCE, /Requested by \$\{key\}/);
  assert.match(APP_SOURCE, /Administrator claim/);
  assert.doesNotMatch(APP_SOURCE, /requester_display_name/);
});

const REQUEST_VIDEO_ID = "AbCdEf123_-";
const UNSUPPORTED_MESSAGE =
  "Enter a supported single-video YouTube URL before submitting the claim.";
const REQUIRED_MESSAGE = "Enter a YouTube URL before submitting the claim.";

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

function createRequestElement(tagName = "div") {
  return {
    tagName: String(tagName).toUpperCase(),
    value: "",
    focusCount: 0,
    focus() {
      this.focusCount += 1;
    },
  };
}

function createYouTubeRequestSubmitHarness({ fetchResponder } = {}) {
  const youtubeRequestUrlInput = createRequestElement("input");
  const youtubeRequestSubmitButton = createRequestElement("button");
  const state = {
    submitting: false,
    urlError: "",
    statusMessage: "",
    items: [],
    pollTimer: null,
    focusedRequestId: "",
  };
  const fetchCalls = [];
  const defaultResponder = async () => ({
    ok: true,
    status: 201,
    json: async () => ({ request_id: "request-accepted-1" }),
  });
  const context = {
    console,
    Set,
    Map,
    Array,
    String,
    Boolean,
    Object,
    Number,
    Promise,
    JSON,
    URL,
    URLSearchParams,
    youtubeRequestState: state,
    YOUTUBE_REQUESTS_ENDPOINT: "/api/youtube/requests",
    identityAllowsYouTubeRequest: () => true,
    framenestMutationHeaders: (headers) => Object.assign({}, headers),
    youtubeRequestUrlInput,
    youtubeRequestSubmitButton,
    renderCount: 0,
    renderYouTubeRequestCockpit() {
      context.renderCount += 1;
    },
    refreshCalls: 0,
    refreshYouTubeRequests: async () => {
      context.refreshCalls += 1;
    },
    fetch: async (url, options) => {
      fetchCalls.push({ url: String(url), options });
      return (fetchResponder || defaultResponder)(url, options);
    },
  };
  context.globalThis = context;
  vm.createContext(context);
  vm.runInContext(
    `${extractYouTubeClaimValidationSource()}\n${extractFunction(
      APP_SOURCE,
      "submitYouTubeRequest",
    )}`,
    context,
  );
  context.submitYouTubeRequest = vm.runInContext("submitYouTubeRequest", context);
  return {
    context,
    state,
    fetchCalls,
    youtubeRequestUrlInput,
    youtubeRequestSubmitButton,
  };
}

test("ordinary validator exposes the supported contract without an ok field", () => {
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
  vm.runInContext(extractYouTubeClaimValidationSource(), context);
  const validate = vm.runInContext("validateYouTubeClaimUrl", context);
  const supported = validate(`https://youtu.be/${REQUEST_VIDEO_ID}`);
  assert.equal(supported.supported, true);
  assert.equal("ok" in supported, false);
  const rejected = validate("not a URL");
  assert.equal(rejected.supported, false);
  assert.equal(rejected.message, UNSUPPORTED_MESSAGE);
});

test("ordinary request submit reaches the API for a supported URL", async () => {
  const harness = createYouTubeRequestSubmitHarness();
  const submittedUrl = `https://youtu.be/${REQUEST_VIDEO_ID}`;
  harness.youtubeRequestUrlInput.value = submittedUrl;
  await harness.context.submitYouTubeRequest();
  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.fetchCalls[0].url, "/api/youtube/requests");
  assert.deepEqual(JSON.parse(harness.fetchCalls[0].options.body), {
    url: submittedUrl,
    confirmation_method: "interactive",
  });
  assert.equal(harness.state.urlError, "");
  assert.equal(harness.state.statusMessage, "YouTube request accepted.");
  assert.equal(harness.state.focusedRequestId, "request-accepted-1");
  assert.equal(harness.context.refreshCalls, 1);
});

test("ordinary request submit reuses an existing request without url error", async () => {
  const harness = createYouTubeRequestSubmitHarness({
    fetchResponder: async () => ({
      ok: true,
      status: 200,
      json: async () => ({ request_id: "request-reused-1" }),
    }),
  });
  harness.youtubeRequestUrlInput.value = `https://www.youtube.com/watch?v=${REQUEST_VIDEO_ID}`;
  await harness.context.submitYouTubeRequest();
  assert.equal(harness.fetchCalls.length, 1);
  assert.equal(harness.state.statusMessage, "Existing YouTube request reused.");
  assert.equal(harness.state.focusedRequestId, "request-reused-1");
});

test("ordinary request submit blocks unsupported input before the API", async () => {
  const harness = createYouTubeRequestSubmitHarness();
  harness.youtubeRequestUrlInput.value =
    `https://media.example.invalid/watch?v=${REQUEST_VIDEO_ID}`;
  await harness.context.submitYouTubeRequest();
  assert.equal(harness.fetchCalls.length, 0);
  assert.equal(harness.state.urlError, UNSUPPORTED_MESSAGE);
  assert.equal(harness.youtubeRequestUrlInput.focusCount, 1);
  assert.equal(harness.state.submitting, false);
});

test("ordinary request submit blocks empty input with the required message", async () => {
  const harness = createYouTubeRequestSubmitHarness();
  harness.youtubeRequestUrlInput.value = "   ";
  await harness.context.submitYouTubeRequest();
  assert.equal(harness.fetchCalls.length, 0);
  assert.equal(harness.state.urlError, REQUIRED_MESSAGE);
  assert.equal(harness.youtubeRequestUrlInput.focusCount, 1);
});

test("completed_private Open Details hydrates through audience-safe media detail", () => {
  assert.match(APP_SOURCE, /function resolveDetailsMediaItem\(/);
  assert.match(APP_SOURCE, /function detailsMediaItemLooksComplete\(/);
  assert.match(APP_SOURCE, /mediaDetailEndpoint\(/);
  assert.match(APP_SOURCE, /await resolveDetailsMediaItem\(item\)/);
  assert.match(
    APP_SOURCE,
    /media_id:\s*item\.media_id,\s*id:\s*item\.media_id/,
  );
  assert.match(APP_SOURCE, /phase === "completed_private"/);
  assert.match(APP_SOURCE, /Private until an administrator publishes it/);
  assert.doesNotMatch(
    APP_SOURCE,
    /openDetailsDialog\(\{\s*media_id:\s*item\.media_id[\s\S]{0,80}media_kind/,
  );
});

test("Details population tolerates incomplete items after media detail hydration", () => {
  const populate = extractFunction(APP_SOURCE, "populateDetailsDialog");
  assert.match(populate, /payload\.display_title/);
  assert.match(populate, /Array\.isArray\(item\.locations\)/);
  assert.match(populate, /const locations = Array\.isArray\(item\.locations\)/);
  assert.doesNotMatch(populate, /item\.locations\.forEach\(/);
});
