const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

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

test("ordinary My X downloads endpoint and capability gating", () => {
  assert.match(APP_SOURCE, /X_REQUESTS_ENDPOINT\s*=\s*"\/api\/x\/requests"/);
  assert.match(APP_SOURCE, /function identityAllowsXRequest\(\)/);
  assert.match(APP_SOURCE, /CAPABILITY_X_REQUEST|"x\.request"/);
  assert.match(APP_SOURCE, /identityHasCapability\("x\.request"\)/);
  assert.match(INDEX_SOURCE, /id="x-request-open-button"/);
  assert.match(INDEX_SOURCE, /My X downloads/);
  assert.match(INDEX_SOURCE, /id="x-request-dialog"/);
  assert.match(INDEX_SOURCE, /aria-live="polite"/);
});

test("requester guidance advertises video and animated media", () => {
  const guidance = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf('id="x-request-guidance"'),
    INDEX_SOURCE.indexOf("</p>", INDEX_SOURCE.indexOf('id="x-request-guidance"')),
  );
  assert.match(guidance, /video or animated media/i);
});

test("requester cockpit has no ordinary-user publication or AI control", () => {
  assert.doesNotMatch(INDEX_SOURCE, /id="x-request-publish"/);
  assert.doesNotMatch(INDEX_SOURCE, /id="x-request-ai"/);
  assert.doesNotMatch(
    APP_SOURCE,
    /function submitXRequest[\s\S]{0,400}metadata\.canonical\.write/,
  );
});

test("administrator review surfaces provenance read-only and creator chips first", () => {
  assert.match(APP_SOURCE, /X_ADMIN_ENDPOINT\s*=\s*"\/api\/admin\/x\/requests"/);
  assert.match(APP_SOURCE, /function identityAllowsXAdmin\(\)/);
  assert.match(APP_SOURCE, /identityHasCapability\("x\.acquire"\)/);
  assert.match(INDEX_SOURCE, /id="x-admin-dialog"/);
  assert.match(INDEX_SOURCE, /id="x-admin-review"/);
  assert.match(APP_SOURCE, /add\("Acquisition source", "x_manual_claim"\)/);
  assert.match(APP_SOURCE, /Creator: /);
});

test("phase and asset status projection present", () => {
  assert.match(APP_SOURCE, /case "completed_private": return/);
  assert.match(APP_SOURCE, /case "failed": return "Failed"/);
  assert.match(APP_SOURCE, /item\.assets/);
  assert.match(APP_SOURCE, /asset\.failure_code/);
  assert.match(APP_SOURCE, /asset\.media_id/);
  assert.match(APP_SOURCE, /openPrivateDetails\(asset\.media_id\)/);
});

test("retry is gated and partial-success is presentable", () => {
  assert.match(APP_SOURCE, /retryXRequest\(/);
  assert.match(APP_SOURCE, /x-asset-list/);
  assert.match(APP_SOURCE, /encodeURIComponent\(requestId\)\}\/retry/);
  assert.match(APP_SOURCE, /if \(item\.can_retry\)/);
});

test("requester retry is gated on explicit backend can_retry projection", () => {
  assert.match(APP_SOURCE, /if \(item\.can_retry\)/);
  assert.match(APP_SOURCE, /retryXRequest\(item\.request_id\)/);
  assert.doesNotMatch(APP_SOURCE, /completed_partial && item\.failure_code/);
});

test("styles cover requester and admin X surfaces", () => {
  assert.match(STYLES_SOURCE, /\.x-asset-list/);
  assert.match(STYLES_SOURCE, /\.x-admin-review/);
  assert.match(STYLES_SOURCE, /\.x-admin-meta/);
});
