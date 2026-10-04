const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const APP_PATH = path.resolve(__dirname, "../src/kronika/adapters/api/web/app.js");
const APP_SOURCE = fs.readFileSync(APP_PATH, "utf8");

function productionFunction(name) {
  const asyncMarker = `async function ${name}(`;
  const regularMarker = `function ${name}(`;
  const asyncStart = APP_SOURCE.indexOf(asyncMarker);
  const marker = asyncStart === -1 ? regularMarker : asyncMarker;
  const start = asyncStart === -1 ? APP_SOURCE.indexOf(marker) : asyncStart;
  assert.notEqual(start, -1, `missing production function ${name}`);
  let depth = 0;
  let quote = null;
  let escaped = false;
  let lineComment = false;
  let blockComment = false;
  const bodyStart = APP_SOURCE.indexOf("{", start);
  for (let index = bodyStart; index < APP_SOURCE.length; index += 1) {
    const character = APP_SOURCE[index];
    const next = APP_SOURCE[index + 1];
    if (lineComment) {
      if (character === "\n") lineComment = false;
      continue;
    }
    if (blockComment) {
      if (character === "*" && next === "/") {
        blockComment = false;
        index += 1;
      }
      continue;
    }
    if (quote) {
      if (escaped) escaped = false;
      else if (character === "\\") escaped = true;
      else if (character === quote) quote = null;
      continue;
    }
    if (character === "/" && next === "/") {
      lineComment = true;
      index += 1;
      continue;
    }
    if (character === "/" && next === "*") {
      blockComment = true;
      index += 1;
      continue;
    }
    if (character === '"' || character === "'" || character === "`") {
      quote = character;
      continue;
    }
    if (character === "{") depth += 1;
    if (character === "}") {
      depth -= 1;
      if (depth === 0) return APP_SOURCE.slice(start, index + 1);
    }
  }
  throw new Error(`unterminated production function ${name}`);
}

class FakeElement {
  constructor(document, tagName) {
    this.ownerDocument = document;
    this.tagName = String(tagName).toUpperCase();
    this.children = [];
    this.parentNode = null;
    this.attributes = new Map();
    this.className = "";
    this.src = "";
    this.alt = "";
    this.listeners = new Map();
  }

  setAttribute(name, value) {
    this.attributes.set(name, String(value));
  }

  getAttribute(name) {
    return this.attributes.has(name) ? this.attributes.get(name) : null;
  }

  removeAttribute(name) {
    this.attributes.delete(name);
  }

  appendChild(child) {
    child.parentNode = this;
    this.children.push(child);
    return child;
  }

  replaceChildren(...nodes) {
    this.children = [];
    nodes.forEach((node) => this.appendChild(node));
  }

  addEventListener(type, handler) {
    const list = this.listeners.get(type) || [];
    list.push(handler);
    this.listeners.set(type, list);
  }
}

class FakeDocument {
  createElement(tagName) {
    return new FakeElement(this, tagName);
  }
}

function loadHelpers() {
  const sandbox = {
    document: new FakeDocument(),
    MEDIA_CATALOG_ENDPOINT: "/api/media",
  };
  vm.createContext(sandbox);
  vm.runInContext(
    [
      productionFunction("mediaContentUrl"),
      productionFunction("mediaGalleryPreviewUrl"),
      productionFunction("selectSupportedAvailableLocation"),
      productionFunction("renderPreviewFallback"),
      productionFunction("syncCardMediaSurfaceToggleState"),
      productionFunction("renderPersistentPreview"),
      productionFunction("renderCatalogCardMediaSurface"),
      productionFunction("formatCatalogKind"),
    ].join("\n"),
    sandbox,
  );
  return sandbox;
}

test("still image gallery card uses identity content and skips play toggle", () => {
  const helpers = loadHelpers();
  const item = {
    media_id: "11111111-1111-4111-8111-111111111111",
    media_kind: "image",
    display_title: "Still",
    locations: [{ location_id: "22222222-2222-4222-8222-222222222222", availability: "available" }],
  };

  assert.equal(
    helpers.selectSupportedAvailableLocation(item).location_id,
    item.locations[0].location_id,
  );
  assert.equal(helpers.formatCatalogKind("image"), "image");

  const surface = helpers.renderCatalogCardMediaSurface(item);
  assert.equal(surface.getAttribute("role"), null);
  assert.equal(surface.getAttribute("data-media-state"), "preview");
  assert.match(surface.className, /media-placeholder--image/);
  const image = surface.children[0];
  assert.equal(image.tagName, "IMG");
  assert.match(image.src, /\/content$/);
  assert.doesNotMatch(image.src, /gallery-preview/);
  assert.equal(surface.listeners.has("click"), false);
});

test("animated image support remains intact beside still images", () => {
  const helpers = loadHelpers();
  const item = {
    media_id: "11111111-1111-4111-8111-111111111111",
    media_kind: "animated_image",
    display_title: "Gif",
    locations: [{ location_id: "22222222-2222-4222-8222-222222222222", availability: "available" }],
  };
  const surface = helpers.renderCatalogCardMediaSurface(item);
  assert.equal(surface.getAttribute("role"), "button");
  const image = surface.children[0];
  assert.match(image.src, /gallery-preview/);
});
