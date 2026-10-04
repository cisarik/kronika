const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const INDEX_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const INDEX_SOURCE = fs.readFileSync(INDEX_PATH, "utf8");

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

test("source exposes capability-gated Remove from catalog and cleanup retry", () => {
  assert.match(APP_SOURCE, /Remove from catalog/);
  assert.match(APP_SOURCE, /identityHasCapability\("media\.catalog\.remove"\)/);
  assert.match(APP_SOURCE, /acknowledge_consequences:\s*true/);
  assert.match(APP_SOURCE, /consequence_fingerprint:\s*preview\.consequence_fingerprint/);
  assert.match(APP_SOURCE, /original media file remains on disk/i);
  assert.match(APP_SOURCE, /Catalog state changed\. Review the refreshed consequences/);
  assert.match(APP_SOURCE, /adminBatchState\.selectedMediaIds\.delete/);
  assert.match(APP_SOURCE, /await loadAdminCatalog\(/);
  assert.match(APP_SOURCE, /typeof loadCatalog === "function"/);
  assert.match(INDEX_SOURCE, /id="admin-catalog-cleanup-retry-button"/);
  assert.match(APP_SOURCE, /setAdminPendingCleanupReceipt/);
});

test("confirmation message discloses retained originals and fingerprint path", () => {
  const context = {
    preview: {
      display_title: "Synthetic",
      publication_state: "published",
      storage_class: "operator_managed",
      analysis_run_count: 1,
      provider_submission_count: 0,
      provenance_effects: ["youtube_claims_become_catalog_removed"],
      derived_artifact_cleanup_intent: ["cover_artifact"],
    },
  };
  vm.createContext(context);
  vm.runInContext(extractFunction(APP_SOURCE, "buildCatalogRemovalConfirmationMessage"), context);
  const message = vm.runInContext(
    "buildCatalogRemovalConfirmationMessage(preview)",
    context,
  );
  assert.match(message, /original media file remains on disk/i);
  assert.match(message, /does not purge originals/i);
  assert.match(message, /Publication state: published/);
});
