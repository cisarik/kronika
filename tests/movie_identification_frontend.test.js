// Movie identification status/draft boundary checks for the packaged web shell.

const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "src", "kronika", "adapters", "api", "web", "app.js"),
  "utf8",
);
const INDEX_SOURCE = fs.readFileSync(
  path.join(__dirname, "..", "src", "kronika", "adapters", "api", "web", "index.html"),
  "utf8",
);

function extractFunction(name) {
  const start = APP_SOURCE.indexOf(`function ${name}(`);
  assert.ok(start >= 0, `missing function ${name}`);
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
  throw new Error(`failed to extract ${name}`);
}

function createStripElement() {
  return {
    hidden: true,
    className: "",
    textContent: "",
    attrs: {},
    children: [],
    classList: {
      toggle() {},
    },
    setAttribute(name, value) {
      this.attrs[name] = String(value);
    },
    getAttribute(name) {
      return Object.prototype.hasOwnProperty.call(this.attrs, name) ? this.attrs[name] : null;
    },
    addEventListener() {},
    replaceChildren(...nodes) {
      this.children = [...nodes];
    },
    appendChild(node) {
      this.children.push(node);
      return node;
    },
  };
}

function stripValueText(strip) {
  return strip.children
    .filter((child) => child.className !== "metadata-suggestion-strip__apply")
    .map((child) => child.textContent)
    .join("");
}

function stripChipValues(strip) {
  return strip.children.map((child) => child.textContent);
}

function collectStripRenderText(context) {
  return [
    context.metadataAiTitleStrip,
    context.metadataAiDescriptionStrip,
    context.metadataAiTagsStrip,
  ]
    .flatMap((strip) => [strip.textContent, ...strip.children.map((child) => child.textContent)])
    .join("\n");
}

function createStripRenderContext() {
  const context = {
    metadataSuggestionList: {
      mediaId: "media-1",
      fetching: false,
      items: [],
      selectedRunId: null,
      errorMessage: "",
      movieExcluded: false,
    },
    metadataAiTitleStrip: createStripElement(),
    metadataAiDescriptionStrip: createStripElement(),
    metadataAiTagsStrip: createStripElement(),
    identityAllowsAiSuggestionChrome() {
      return true;
    },
    metadataWorkspace: { current: { tagKeys: [] } },
    metadataWorkspaceIsAliasMode() {
      return false;
    },
    copySuggestionFieldToCurrent() {},
    document: {
      createElement() {
        return createStripElement();
      },
    },
  };
  vm.createContext(context);
  vm.runInContext(extractFunction("movieIdentificationIsPureUnknown"), context);
  vm.runInContext(extractFunction("selectedMetadataSuggestion"), context);
  vm.runInContext(extractFunction("clearMetadataSuggestionStrip"), context);
  vm.runInContext(extractFunction("appendSuggestionApplyButton"), context);
  vm.runInContext(extractFunction("renderMetadataSuggestionStrips"), context);
  return context;
}

test("movie identification status distinguishes pure unknown results", () => {
  assert.match(APP_SOURCE, /function movieIdentificationEndpoint/);
  assert.match(APP_SOURCE, /function movieIdentificationIsPureUnknown/);
  assert.match(APP_SOURCE, /movieIdentificationEndpoint\(mediaId\)/);

  const context = {};
  vm.createContext(context);
  vm.runInContext(extractFunction("movieIdentificationIsPureUnknown"), context);

  const unknown = {
    identified_title: null,
    identification_status: "unknown",
    confidence: "unknown",
    genres: [],
    tags: [],
    description: "Movie could not be identified from the available frames.",
  };
  assert.equal(context.movieIdentificationIsPureUnknown(unknown), true);
  assert.equal(
    context.movieIdentificationIsPureUnknown({
      identified_title: "Synthetic Adventure",
      identification_status: "unknown",
      genres: [],
      tags: [],
    }),
    false,
  );
  assert.equal(
    context.movieIdentificationIsPureUnknown({
      identified_title: null,
      identification_status: "identified",
      genres: [],
      tags: [],
    }),
    false,
  );
  assert.equal(
    context.movieIdentificationIsPureUnknown({
      identified_title: null,
      identification_status: "unknown",
      genres: ["Adventure"],
      tags: [],
    }),
    false,
  );
  assert.equal(context.movieIdentificationIsPureUnknown(null), false);
});

test("movie Identify and suggestion review stay non-canonical in source", () => {
  assert.match(APP_SOURCE, /Running movie identification/);
  assert.match(APP_SOURCE, /Movie identification in progress\./);
  const start = APP_SOURCE.indexOf('document.querySelector("#metadata-movie-identify-button")');
  assert.ok(start >= 0);
  const identifyBlock = APP_SOURCE.slice(start, APP_SOURCE.indexOf("let commandSearchDebounceTimer"));
  assert.match(identifyBlock, /movie-identification/);
  assert.doesNotMatch(identifyBlock, /handleSaveMetadata/);
  assert.doesNotMatch(identifyBlock, /method:\s*"PUT"/);
  assert.equal(APP_SOURCE.includes("handleLoadDurableAiSuggestion"), false);
  // Movie identification status fetch stays on the read-only status surface.
  const statusStart = APP_SOURCE.indexOf("async function refreshMetadataDurableAnalysis");
  const statusEnd = APP_SOURCE.indexOf("function applyAnalysisStatusPayload");
  assert.ok(statusStart >= 0 && statusEnd > statusStart);
  const statusBlock = APP_SOURCE.slice(statusStart, statusEnd);
  assert.match(statusBlock, /movieIdentificationEndpoint/);
  assert.doesNotMatch(statusBlock, /handleSaveMetadata/);
  assert.doesNotMatch(statusBlock, /method:\s*"PUT"/);
});

test("durable movie suggestion markup separates Suggested genres and Suggested tags", () => {
  const workspaceBlock = INDEX_SOURCE.slice(
    INDEX_SOURCE.indexOf('id="metadata-ai-title-strip"'),
    INDEX_SOURCE.indexOf('id="metadata-save-button"'),
  );
  assert.match(workspaceBlock, /id="metadata-ai-title-strip"/);
  assert.match(workspaceBlock, /id="metadata-ai-description-strip"/);
  assert.match(workspaceBlock, /id="metadata-ai-tags-strip"/);
  assert.equal((workspaceBlock.match(/id="metadata-ai-title-strip"/g) || []).length, 1);
  assert.equal((workspaceBlock.match(/id="metadata-ai-description-strip"/g) || []).length, 1);
  assert.equal((workspaceBlock.match(/id="metadata-ai-tags-strip"/g) || []).length, 1);
  // Genres stay a separate draft-side facet surface (the movie genre fieldset),
  // never merged into the suggestion tag strip.
  assert.match(workspaceBlock, /id="metadata-genres-fieldset"/);
  assert.equal((workspaceBlock.match(/id="metadata-genres-fieldset"/g) || []).length, 1);
  assert.equal((workspaceBlock.match(/metadata-suggestion-strip--tags/g) || []).length, 1);
  assert.doesNotMatch(workspaceBlock, /Confidence/i);
  const genresAt = workspaceBlock.indexOf('id="metadata-genres-fieldset"');
  const tagsAt = workspaceBlock.indexOf('id="metadata-ai-tags-strip"');
  assert.ok(genresAt >= 0 && tagsAt > genresAt);
});

test("durable renderer does not concatenate genres into tags", () => {
  const stripsBody = extractFunction("renderMetadataSuggestionStrips");
  assert.match(stripsBody, /metadataAiTagsStrip/);
  assert.match(stripsBody, /item\.tags/);
  assert.doesNotMatch(stripsBody, /\.\.\.suggestion\.genres,\s*\.\.\.suggestion\.tags/);
  assert.doesNotMatch(stripsBody, /\[\.\.\.suggestion\.genres/);
  // The suggestion tag renderer never touches the genre facet at all.
  assert.doesNotMatch(stripsBody, /genres/);
  assert.doesNotMatch(stripsBody, /handleSaveMetadata/);
  assert.doesNotMatch(stripsBody, /method:\s*"PUT"/);
  assert.match(APP_SOURCE, /#metadata-ai-tags-strip/);
});

test("durable movie suggestion renders overlapping genres and tags as distinct facets", () => {
  const context = createStripRenderContext();
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "The Tinder Swindler",
    description: "The Tinder Swindler",
    tags: [
      { value: "Documentary", status: "mapped", key: "documentary", displayName: "Documentary" },
      { value: "Crime", status: "mapped", key: "crime", displayName: "Crime" },
    ],
  }];
  context.metadataSuggestionList.selectedRunId = "run-1";
  context.renderMetadataSuggestionStrips();

  assert.equal(context.metadataAiTitleStrip.hidden, false);
  assert.equal(context.metadataAiDescriptionStrip.hidden, false);
  assert.equal(context.metadataAiTagsStrip.hidden, false);
  assert.equal(stripValueText(context.metadataAiTitleStrip), "The Tinder Swindler");
  assert.equal(stripValueText(context.metadataAiDescriptionStrip), "The Tinder Swindler");
  assert.deepEqual(stripChipValues(context.metadataAiTagsStrip), ["Documentary", "Crime"]);
  assert.notEqual(
    stripChipValues(context.metadataAiTagsStrip).join(", "),
    "Documentary, Crime, Documentary, Crime",
  );
  assert.doesNotMatch(collectStripRenderText(context), /Documentary, Crime, Documentary/);
  assert.doesNotMatch(collectStripRenderText(context), /confidence/i);
});

test("durable movie suggestion keeps distinct genre and tag facet values", () => {
  const context = createStripRenderContext();

  // The tag facet renders each suggested tag value once, as its own chip.
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "Distinct Facets",
    description: "Distinct description",
    tags: [
      { value: "Romance scam", status: "mapped", key: "romance-scam", displayName: "Romance scam" },
      { value: "True crime", status: "mapped", key: "true-crime", displayName: "True crime" },
    ],
  }];
  context.metadataSuggestionList.selectedRunId = "run-1";
  context.renderMetadataSuggestionStrips();
  assert.deepEqual(stripChipValues(context.metadataAiTagsStrip), ["Romance scam", "True crime"]);
  assert.equal(stripValueText(context.metadataAiTitleStrip), "Distinct Facets");
  assert.equal(stripValueText(context.metadataAiDescriptionStrip), "Distinct description");
});

test("durable movie suggestion empty and reset semantics clear stale facet values", () => {
  const context = createStripRenderContext();
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "First",
    description: "First description",
    tags: [
      { value: "Documentary", status: "mapped", key: "documentary", displayName: "Documentary" },
      { value: "Crime", status: "mapped", key: "crime", displayName: "Crime" },
    ],
  }];
  context.metadataSuggestionList.selectedRunId = "run-1";
  context.renderMetadataSuggestionStrips();
  assert.equal(stripValueText(context.metadataAiTitleStrip), "First");
  assert.deepEqual(stripChipValues(context.metadataAiTagsStrip), ["Documentary", "Crime"]);

  // A replacement suggestion with fewer tag values clears the stale chips.
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "Has tags",
    description: "Has tags",
    tags: [
      { value: "Romance scam", status: "mapped", key: "romance-scam", displayName: "Romance scam" },
    ],
  }];
  context.renderMetadataSuggestionStrips();
  assert.deepEqual(stripChipValues(context.metadataAiTagsStrip), ["Romance scam"]);

  // An empty tag facet hides the strip and leaves no stale chip values.
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "Empty tags",
    description: "Has genres",
    tags: [],
  }];
  context.renderMetadataSuggestionStrips();
  assert.equal(context.metadataAiTagsStrip.hidden, true);
  assert.equal(context.metadataAiTagsStrip.children.length, 0);

  // Empty text values render deterministic placeholders instead of stale text.
  context.metadataSuggestionList.items = [{
    analysisRunId: "run-1",
    title: "",
    description: "",
    tags: [],
  }];
  context.renderMetadataSuggestionStrips();
  assert.equal(stripValueText(context.metadataAiTitleStrip), "(No title)");
  assert.equal(stripValueText(context.metadataAiDescriptionStrip), "(No description)");
  assert.equal(context.metadataAiTagsStrip.hidden, true);
  assert.equal(context.metadataAiTagsStrip.children.length, 0);

  // Reset (no selected suggestion) hides every strip and clears stale values.
  context.metadataSuggestionList.selectedRunId = null;
  context.renderMetadataSuggestionStrips();
  for (const strip of [
    context.metadataAiTitleStrip,
    context.metadataAiDescriptionStrip,
    context.metadataAiTagsStrip,
  ]) {
    assert.equal(strip.hidden, true);
    assert.equal(strip.children.length, 0);
  }
});
