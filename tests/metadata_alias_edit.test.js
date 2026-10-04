const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const INDEX_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/index.html");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const INDEX_SOURCE = fs.readFileSync(INDEX_PATH, "utf8");

function extractFunction(name) {
  const markers = [`async function ${name}(`, `function ${name}(`];
  let start = -1;
  for (const marker of markers) {
    start = APP_SOURCE.indexOf(marker);
    if (start !== -1) break;
  }
  assert.notEqual(start, -1, `missing ${name}`);
  const headerOpen = APP_SOURCE.indexOf("(", start);
  assert.notEqual(headerOpen, -1, `missing parameter list for ${name}`);
  let depth = 0;
  let headerClose = -1;
  for (let index = headerOpen; index < APP_SOURCE.length; index += 1) {
    const character = APP_SOURCE[index];
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
  const bodyOpen = APP_SOURCE.indexOf("{", headerClose);
  assert.notEqual(bodyOpen, -1, `missing body for ${name}`);
  depth = 0;
  for (let index = bodyOpen; index < APP_SOURCE.length; index += 1) {
    const character = APP_SOURCE[index];
    if (character === "{") depth += 1;
    else if (character === "}") {
      depth -= 1;
      if (depth === 0) return APP_SOURCE.slice(start, index + 1);
    }
  }
  assert.fail(`unterminated ${name}`);
}

test("ordinary Edit is gated by alias or canonical write in a workspace audience", () => {
  const editBody = extractFunction("identityAllowsMetadataEdit");
  assert.match(editBody, /isWorkspaceAudience\(\)/);
  assert.match(editBody, /metadata\.alias\.write/);
  assert.match(editBody, /metadata\.canonical\.write/);
  const detailsBody = extractFunction("applyIdentityCapabilities");
  assert.match(detailsBody, /identityAllowsMetadataEdit\(\)/);
  const cardBody = extractFunction("renderCatalogCard");
  assert.match(cardBody, /identityAllowsMetadataEdit\(\)/);
});

test("ordinary load seeds Current from a non-empty alias overlay", () => {
  const openBody = extractFunction("handleOpenMetadataWorkspace");
  assert.match(openBody, /mediaAliasEndpoint\(mediaId\)/);
  assert.match(openBody, /aliasOverlayIsNonEmpty\(aliasPayload\)/);
  assert.match(openBody, /applyAliasOverlayToWorkspace\(aliasPayload\)/);
  assert.match(openBody, /editMode: identityUsesCanonicalMetadataWrite\(\) \? "canonical" : "alias"/);
  assert.match(openBody, /previewSuggestion/);
  assert.match(openBody, /presentPreviewSuggestionInMetadataWorkspace/);
  assert.equal(openBody.includes("applyResolvedAiSuggestionToMetadataWorkspace"), false);
  const overlayBody = extractFunction("aliasOverlayIsNonEmpty");
  assert.match(overlayBody, /display_title/);
  assert.match(overlayBody, /description/);
  assert.match(overlayBody, /tag_keys/);
});

test("ordinary Save uses alias PUT only and never companion apply", () => {
  const saveBody = extractFunction("handleSaveMetadata");
  assert.match(saveBody, /mediaAliasEndpoint\(saveOwner\.mediaId\)/);
  assert.match(saveBody, /saveOwner\.editMode === "alias"/);
  assert.equal(saveBody.includes("/apply"), false);
  const claimBody = extractFunction("claimMetadataSaveOwner");
  assert.match(claimBody, /display_title: normalized.displayTitle/);
  assert.match(claimBody, /tag_keys: normalized.tagKeys/);
  assert.match(claimBody, /editMode: aliasMode \? "alias" : "canonical"/);
  assert.equal(APP_SOURCE.includes("/apply"), false);
});

test("ordinary alias Edit hides classification chrome and canonical tag create", () => {
  const createBody = extractFunction("createAndSelectMetadataTag");
  assert.match(createBody, /identityUsesCanonicalMetadataWrite\(\)/);
  const classification = extractFunction("syncClassificationControlsFromWorkspace");
  assert.match(classification, /classificationRow\.hidden = true/);
  assert.doesNotMatch(classification, /classificationRow\.hidden = aliasMode/);
  const suggestionChrome = extractFunction("identityAllowsAiSuggestionChrome");
  assert.match(suggestionChrome, /metadata\.alias\.write/);
  assert.match(suggestionChrome, /metadata\.canonical\.write/);
  assert.doesNotMatch(suggestionChrome, /companionWebHosted\(\)/);
  assert.doesNotMatch(suggestionChrome, /media\.workflow\.read/);
  const analyze = extractFunction("identityAllowsAiAnalyze");
  assert.match(analyze, /analysis\.run/);
  assert.match(analyze, /companionWebHosted\(\)/);
  assert.match(analyze, /metadataWorkspaceIsAliasMode\(\)/);
});

test("per-field copy does not persist and obsolete Load semantics are absent", () => {
  const copyBody = extractFunction("copySuggestionFieldToCurrent");
  assert.equal(copyBody.includes("fetch("), false);
  assert.equal(copyBody.includes("handleSaveMetadata"), false);
  assert.equal(copyBody.includes("method:"), false);
  assert.equal(APP_SOURCE.includes("handleLoadDurableAiSuggestion"), false);
  assert.equal(APP_SOURCE.includes("metadataLoadAiSuggestionButton"), false);
  assert.equal(APP_SOURCE.includes("metadataSuggestionList.revealed"), false);
  assert.match(INDEX_SOURCE, /id="metadata-ai-title-strip"/);
  assert.equal(INDEX_SOURCE.includes('id="metadata-load-ai-suggestion-button"'), false);
});

test("suggestion chrome is available in alias mode and hosted companion web", () => {
  const suggestionChrome = extractFunction("identityAllowsAiSuggestionChrome");
  assert.match(suggestionChrome, /isWorkspaceAudience\(\)/);
  assert.doesNotMatch(suggestionChrome, /metadataWorkspaceIsAliasMode\(\)/);
  assert.doesNotMatch(suggestionChrome, /companionWebHosted\(\)/);
  const panel = extractFunction("renderMetadataAiPanel");
  assert.match(panel, /identityAllowsAiSuggestionChrome\(\)/);
  const controls = extractFunction("updateMetadataControls");
  assert.match(controls, /identityAllowsAiSuggestionChrome\(\)/);
  assert.match(controls, /identityAllowsAiAnalyze\(\)/);
});

test("mapped tag state is accessible and unmapped authority follows alias/canonical mode", () => {
  const strips = extractFunction("renderMetadataSuggestionStrips");
  assert.match(strips, /metadata-suggestion-tag--mapped/);
  assert.match(strips, /copySuggestionFieldToCurrent\("tag", tag\.key\)/);
  assert.match(strips, /metadata-suggestion-tag--unmapped/);
  assert.match(strips, /document\.createElement\("button"\)/);
  assert.match(strips, /metadata-suggestion-tag--already-added/);
  assert.match(strips, /aria-pressed/);
  assert.match(strips, /if \(!metadataWorkspaceIsAliasMode\(\)\)/);
  const create = extractFunction("createAndSelectMetadataTag");
  assert.match(create, /identityUsesCanonicalMetadataWrite\(\)/);
});
