// Frontend contracts for contributor-scoped workspace media.

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

test("workspace navigation is hidden by default and requires media.workspace.read plus login", () => {
  assert.match(
    INDEX_SOURCE,
    /id="workspace-media-open-button"[\s\S]*?hidden[\s\S]*?>[\s\S]*?My contributions/,
  );
  assert.match(INDEX_SOURCE, /id="workspace-media-browser"/);
  const gate = extractFunction(APP_SOURCE, "identityAllowsWorkspaceMedia");
  assert.match(gate, /identityState\.resolved/);
  assert.match(gate, /identityState\.available/);
  assert.match(gate, /identityState\.login/);
  assert.match(gate, /identityHasCapability\("media\.workspace\.read"\)/);
  assert.match(
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
    /identityAllowsWorkspaceMedia/,
  );
});

test("public audience CSS never reveals workspace chrome", () => {
  assert.match(
    STYLES_SOURCE,
    /body\[data-audience="public_published"\] #workspace-media-open-button/,
  );
  assert.match(
    STYLES_SOURCE,
    /body\[data-audience="public_published"\] #workspace-media-browser/,
  );
});

test("admin contributor filter is omitted from the default query string", () => {
  assert.match(INDEX_SOURCE, /id="admin-media-contributor-filter"/);
  const params = extractFunction(APP_SOURCE, "buildAdminCatalogQueryParams");
  assert.match(params, /params\.set\("contributor", contributor\)/);
  assert.match(params, /if \(contributor\) params\.set\("contributor"/);
  assert.match(APP_SOURCE, /contributor: ""/);
});

test("workspace list uses the dedicated endpoint rather than gallery", () => {
  assert.match(APP_SOURCE, /const WORKSPACE_MEDIA_ENDPOINT = "\/api\/workspace\/media"/);
  assert.match(extractFunction(APP_SOURCE, "loadWorkspaceMedia"), /WORKSPACE_MEDIA_ENDPOINT/);
  assert.doesNotMatch(
    extractFunction(APP_SOURCE, "loadWorkspaceMedia"),
    /MEDIA_CATALOG_ENDPOINT\?/,
  );
});

test("propose-analysis control is capability-gated and does not call analysis run routes", () => {
  assert.match(
    extractFunction(APP_SOURCE, "identityAllowsAnalysisPropose"),
    /identityHasCapability\("analysis\.propose"\)/,
  );
  const render = extractFunction(APP_SOURCE, "renderWorkspaceMediaItem");
  assert.match(render, /identityAllowsAnalysisPropose/);
  assert.match(render, /Propose analysis/);
  const propose = extractFunction(APP_SOURCE, "proposeWorkspaceAnalysis");
  assert.match(propose, /WORKSPACE_MEDIA_ENDPOINT/);
  assert.match(propose, /analysis-proposals/);
  assert.match(propose, /framenestMutationHeaders/);
  assert.doesNotMatch(propose, /durable-analysis/);
  assert.doesNotMatch(propose, /automatic-analysis/);
  assert.doesNotMatch(propose, /media-analysis-preview/);
});

test("administrator analysis-proposals view stays hidden from the public audience", () => {
  assert.match(
    INDEX_SOURCE,
    /id="analysis-proposals-open-button"[\s\S]*?hidden[\s\S]*?>[\s\S]*?Analysis proposals/,
  );
  assert.match(INDEX_SOURCE, /id="analysis-proposals-browser"/);
  assert.match(
    extractFunction(APP_SOURCE, "applyIdentityCapabilities"),
    /analysisProposalsOpenButton/,
  );
  assert.match(
    STYLES_SOURCE,
    /body\[data-audience="public_published"\] #analysis-proposals-open-button/,
  );
  assert.match(
    STYLES_SOURCE,
    /body\[data-audience="public_published"\] #analysis-proposals-browser/,
  );
  assert.match(APP_SOURCE, /const ANALYSIS_PROPOSALS_ENDPOINT = "\/api\/admin\/analysis-proposals"/);
  assert.match(extractFunction(APP_SOURCE, "loadAnalysisProposals"), /ANALYSIS_PROPOSALS_ENDPOINT/);
  assert.match(extractFunction(APP_SOURCE, "openAnalysisProposalsBrowser"), /identityAllowsAdminWorkflow/);
});
