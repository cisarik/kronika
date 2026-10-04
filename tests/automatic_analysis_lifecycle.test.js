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

function extractFunction(name) {
  const marker = `function ${name}(`;
  const start = APP_SOURCE.indexOf(marker);
  assert.notEqual(start, -1, `missing ${name}`);
  let depth = 0;
  let started = false;
  for (let index = start; index < APP_SOURCE.length; index += 1) {
    const char = APP_SOURCE[index];
    if (char === "{") {
      depth += 1;
      started = true;
    } else if (char === "}") {
      depth -= 1;
      if (started && depth === 0) {
        return APP_SOURCE.slice(start, index + 1);
      }
    }
  }
  assert.fail(`unterminated ${name}`);
}

function extractAsyncFunction(name) {
  const marker = `async function ${name}(`;
  const start = APP_SOURCE.indexOf(marker);
  assert.notEqual(start, -1, `missing ${name}`);
  let depth = 0;
  let started = false;
  for (let index = start; index < APP_SOURCE.length; index += 1) {
    const char = APP_SOURCE[index];
    if (char === "{") {
      depth += 1;
      started = true;
    } else if (char === "}") {
      depth -= 1;
      if (started && depth === 0) {
        return APP_SOURCE.slice(start, index + 1);
      }
    }
  }
  assert.fail(`unterminated ${name}`);
}

test("pending and analyzing never appear as success messages", () => {
  const context = {};
  vm.runInNewContext(extractFunction("automaticAnalysisStatusMessage"), context);
  const pending = context.automaticAnalysisStatusMessage({ state: "pending" });
  const analyzing = context.automaticAnalysisStatusMessage({ state: "analyzing" });
  const analyzed = context.automaticAnalysisStatusMessage({ state: "analyzed" });
  const failed = context.automaticAnalysisStatusMessage({
    state: "failed",
    error_message: "AI provider is temporarily unavailable.",
  });
  assert.match(pending, /queued/i);
  assert.match(analyzing, /progress/i);
  assert.match(analyzed, /AI suggestion ready for review/i);
  assert.match(failed, /unavailable|failed/i);
  assert.doesNotMatch(pending, /ready for review/i);
  assert.doesNotMatch(analyzing, /ready for review/i);
  assert.doesNotMatch(failed, /ready for review/i);
  assert.doesNotMatch(analyzed, /Saved AI suggestion/i);
  assert.doesNotMatch(analyzed, /AI analysis ready for review/i);
});

test("automatic analysis polling stops at terminal states and avoids interval loops", () => {
  assert.ok(APP_SOURCE.includes("AUTOMATIC_ANALYSIS_TERMINAL_STATES"));
  assert.ok(APP_SOURCE.includes('new Set(["analyzed", "failed", "not_requested"])'));
  assert.ok(APP_SOURCE.includes("stopAutomaticAnalysisPolling(mediaId)"));
  assert.ok(APP_SOURCE.includes("if (AUTOMATIC_ANALYSIS_TERMINAL_STATES.has(payload.state))"));
  assert.ok(APP_SOURCE.includes("maybeTrackAutomaticAnalysisAfterCatalog(snapshot)"));
  assert.equal(APP_SOURCE.includes("setInterval(pollAutomaticAnalysis"), false);
});

test("cataloged upload copy mentions automatic analysis only when enabled", () => {
  const catalogedBranch = APP_SOURCE.slice(
    APP_SOURCE.indexOf('if (snapshot.state === "cataloged") {'),
    APP_SOURCE.indexOf('if (snapshot.state === "received") {'),
  );
  assert.match(catalogedBranch, /Automatic AI analysis may follow/);
  assert.match(catalogedBranch, /automaticAnalysisCapability\.automatic_analysis_enabled/);
});

test("metadata editor immediately exposes suggestion strips without Load or Apply", () => {
  assert.equal(INDEX_SOURCE.includes('id="metadata-load-ai-suggestion-button"'), false);
  assert.match(INDEX_SOURCE, /id="metadata-ai-suggestion-dropdown"/);
  assert.match(INDEX_SOURCE, /id="metadata-ai-suggestion-toggle"/);
  assert.equal(INDEX_SOURCE.includes('id="metadata-ai-suggestion-select"'), false);
  assert.match(INDEX_SOURCE, />AI suggestions</);
  assert.equal(INDEX_SOURCE.includes("View details"), false);
  assert.equal(INDEX_SOURCE.includes("Saved AI suggestion"), false);
  assert.equal(APP_SOURCE.includes("handleLoadDurableAiSuggestion"), false);
  assert.equal(APP_SOURCE.includes("metadataSuggestionList.revealed"), false);
  assert.match(APP_SOURCE, /function mediaAiSuggestionsEndpoint/);
  assert.match(APP_SOURCE, /\/ai-suggestions\?limit=100/);
  assert.equal(APP_SOURCE.includes("companionReviewInboxDetailEndpoint"), false);
  assert.equal(APP_SOURCE.includes("/apply"), false);
  assert.equal(APP_SOURCE.includes("handleApplyDurable"), false);
});

test("durable suggestion mapping keeps only title description tags and display filename", () => {
  const context = {};
  vm.runInNewContext(extractFunction("aiSuggestionFromAutomaticAnalysisResult"), context);
  const mapped = context.aiSuggestionFromAutomaticAnalysisResult({
    title: "Durable title",
    description: "Durable description",
    collection: "Must not map",
    tags: ["alpha", "beta"],
    suggested_filename: "durable.mp4",
    confidence: 0.9,
    evidence: ["frame"],
    uncertainties: ["maybe"],
  });
  assert.equal(mapped.title, "Durable title");
  assert.equal(mapped.description, "Durable description");
  assert.deepEqual([...mapped.tags], ["alpha", "beta"]);
  assert.equal(mapped.suggestedFilename, "durable.mp4");
  assert.equal("collection" in mapped, false);
  assert.equal(context.aiSuggestionFromAutomaticAnalysisResult(null), null);
  assert.equal(context.aiSuggestionFromAutomaticAnalysisResult({ title: "x" }), null);
});

test("suggestion selection is local-only and generic bulk apply is removed", () => {
  const selectBody = extractFunction("selectMetadataSuggestion");
  assert.equal(selectBody.includes("fetch("), false);
  assert.equal(selectBody.includes("handleSaveMetadata"), false);
  assert.match(selectBody, /renderMetadataSuggestionStrips/);
  assert.equal(APP_SOURCE.includes("applyResolvedAiSuggestionToMetadataWorkspace"), false);
});

test("generated-automatically essay and View details chrome are gone", () => {
  assert.equal(APP_SOURCE.includes("function aiSuggestionOriginExplanation"), false);
  assert.equal(INDEX_SOURCE.includes("Generated automatically after upload."), false);
  assert.equal(INDEX_SOURCE.includes("View details"), false);
  assert.equal(INDEX_SOURCE.includes("New AI analysis is available after confirmation."), false);
  assert.equal(APP_SOURCE.includes("function aiSuggestionOriginExplanation"), false);
  assert.equal(INDEX_SOURCE.includes("automatic_post_catalog"), false);
});

test("AI suggestions chrome sits above Title with immediate per-field strips", () => {
  assert.match(INDEX_SOURCE, /id="metadata-ai-heading"/);
  assert.match(INDEX_SOURCE, />AI suggestions</);
  assert.equal(INDEX_SOURCE.includes("Saved AI suggestion"), false);
  assert.match(INDEX_SOURCE, /id="metadata-ai-suggestion-dropdown"/);
  assert.match(INDEX_SOURCE, /id="metadata-ai-title-strip"/);
  assert.match(INDEX_SOURCE, /id="metadata-ai-description-strip"/);
  assert.match(INDEX_SOURCE, /id="metadata-ai-tags-strip"/);
  assert.equal(INDEX_SOURCE.includes("metadata-ai-filename-input"), false);
  const headingAt = INDEX_SOURCE.indexOf('id="metadata-ai-heading"');
  const titleAt = INDEX_SOURCE.indexOf('id="metadata-title-input"');
  const saveAt = INDEX_SOURCE.indexOf('id="metadata-save-button"');
  const analyzeAt = INDEX_SOURCE.indexOf('id="metadata-ai-analyze-button"');
  assert.ok(headingAt < titleAt);
  assert.ok(saveAt < analyzeAt);
  const panelBody = extractFunction("renderMetadataAiPanel");
  assert.match(panelBody, /AI suggestions/);
  assert.match(panelBody, /identityAllowsAiSuggestionChrome\(\)/);
  assert.equal(panelBody.includes("New AI analysis is currently unavailable."), false);
});

test("suggested filename is an immediately revealed informational note without an input", () => {
  assert.match(INDEX_SOURCE, /id="metadata-ai-filename-note"/);
  assert.equal(INDEX_SOURCE.includes("metadata-ai-filename-input"), false);
  const panelBody = extractFunction("renderMetadataAiPanel");
  assert.match(panelBody, /metadataAiFilenameNote/);
  assert.match(panelBody, /Suggested filename/);
  assert.doesNotMatch(panelBody, /identityUsesCanonicalMetadataWrite\(\)/);
  assert.equal(panelBody.includes("metadataAiFilenameInput"), false);
  assert.equal(APP_SOURCE.includes("metadataAiFilenameInput.addEventListener"), false);
  const refreshBody = extractAsyncFunction("refreshMetadataSuggestionList");
  assert.match(refreshBody, /mediaAiSuggestionsEndpoint\(mediaId\)/);
  assert.doesNotMatch(refreshBody, /selectedItem, \.\.\.items/);
  assert.match(refreshBody, /analysisRunId/);
});

test("suggestion strips enable pointer events and mapped tags are clickable", () => {
  assert.match(STYLES_SOURCE, /\.metadata-suggestion-strip[^{]*\{[^}]*pointer-events:\s*auto/);
  assert.match(STYLES_SOURCE, /\.metadata-suggestion-tag--mapped[^{]*\{[^}]*pointer-events:\s*auto/);
});

test("modal backdrop hierarchy keeps blur and lightens the parent overlay", () => {
  const metadataBackdrop = STYLES_SOURCE.slice(
    STYLES_SOURCE.indexOf(".metadata-dialog::backdrop"),
    STYLES_SOURCE.indexOf(".metadata-dialog__header"),
  );
  const confirmationBackdrop = STYLES_SOURCE.slice(
    STYLES_SOURCE.indexOf(".confirmation-dialog::backdrop"),
    STYLES_SOURCE.indexOf(".confirmation-dialog .upload-dialog__title"),
  );
  assert.match(metadataBackdrop, /backdrop-filter:\s*blur\(/);
  assert.match(metadataBackdrop, /rgba\(0,\s*0,\s*0,\s*0\.34\)/);
  assert.doesNotMatch(metadataBackdrop, /rgba\(0,\s*0,\s*0,\s*0\.6\)/);
  assert.match(confirmationBackdrop, /backdrop-filter:\s*blur\(/);
  assert.match(confirmationBackdrop, /rgba\(0,\s*0,\s*0,\s*0\.52\)/);
});
