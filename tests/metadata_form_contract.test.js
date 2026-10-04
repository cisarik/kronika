const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const FIXTURE_PATH = path.resolve(__dirname, "support/metadata_field_contract_cases.json");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");
const FIXTURE = JSON.parse(fs.readFileSync(FIXTURE_PATH, "utf8"));

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

function extractConstant(name) {
  const marker = `const ${name} = `;
  const start = APP_SOURCE.indexOf(marker);
  if (start === -1) return null;
  const end = APP_SOURCE.indexOf(";", start);
  assert.notEqual(end, -1, `unterminated constant ${name}`);
  return APP_SOURCE.slice(start, end + 1);
}

function buildClientExcerpt() {
  const pieces = [
    extractConstant("MAX_METADATA_TITLE_CODE_POINTS"),
    extractConstant("MAX_METADATA_DESCRIPTION_CODE_POINTS"),
    extractConstant("MAX_METADATA_GENRES"),
    extractConstant("TAG_KEY_PATTERN"),
    extractFunction("unicodeCodePointLength"),
    extractFunction("hasControlCharacter"),
    extractFunction("hasForbiddenDescriptionControlChar"),
    extractFunction("normalizedDescriptionState"),
    extractFunction("normalizedMetadataFormState"),
    extractFunction("normalizedTagDisplayName"),
    extractFunction("tagDisplayNameError"),
    extractFunction("tagSlugFromDisplayName"),
    extractFunction("uniqueTagKeyForDisplayName"),
  ];
  return pieces.filter((piece) => piece !== null && piece !== undefined).join("\n");
}

function buildClientContext() {
  const context = {
    metadataTitleInput: { value: "" },
    metadataDescriptionInput: { value: "" },
    metadataWorkspace: {
      current: {
        displayTitle: "",
        description: "",
        tagKeys: [],
        contentCategory: "movie",
        acquisitionSource: "unknown",
        genres: [],
        creatorAttributionKind: null,
        creatorStableId: null,
        creatorHandle: null,
        creatorDisplayName: null,
      },
    },
    canonicalTagDefinitions: [],
  };
  vm.createContext(context);
  vm.runInContext(buildClientExcerpt(), context, {
    filename: "app.js#metadata-field-contract",
  });
  return context;
}

function expandValue(value) {
  if (
    value !== null
    && typeof value === "object"
    && !Array.isArray(value)
    && Array.isArray(value.repeat)
  ) {
    const [unit, count] = value.repeat;
    return unit.repeat(count);
  }
  return value;
}

function clientAccepts(context, contractCase) {
  const value = expandValue(contractCase.value);
  if (contractCase.field === "title") {
    context.metadataTitleInput.value = value;
    context.metadataDescriptionInput.value = "";
    context.metadataWorkspace.current.genres = [];
    return !context.normalizedMetadataFormState().error;
  }
  if (contractCase.field === "description") {
    context.metadataTitleInput.value = "Sample title";
    context.metadataDescriptionInput.value = value;
    context.metadataWorkspace.current.genres = [];
    return !context.normalizedMetadataFormState().error;
  }
  if (contractCase.field === "tag_display_name") {
    const normalized = context.normalizedTagDisplayName(value);
    if (context.tagDisplayNameError(normalized)) return false;
    return Boolean(context.uniqueTagKeyForDisplayName(normalized));
  }
  if (contractCase.field === "genre_set") {
    context.metadataTitleInput.value = "Sample title";
    context.metadataDescriptionInput.value = "";
    context.metadataWorkspace.current.genres = value;
    return !context.normalizedMetadataFormState().error;
  }
  assert.fail(`unknown fixture field ${contractCase.field}`);
}

const clientContext = buildClientContext();

for (const contractCase of FIXTURE.cases) {
  test(`client metadata field contract: ${contractCase.id}`, () => {
    const accepted = clientAccepts(clientContext, contractCase);
    assert.equal(
      accepted,
      contractCase.expected === "accepted",
      `${contractCase.field} case ${contractCase.id} expected ${contractCase.expected}`,
    );
  });
}
