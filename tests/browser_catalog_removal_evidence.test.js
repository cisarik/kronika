// Real-browser acceptance for administrator catalog removal.
// Gated behind FRAMENEST_RUN_BROWSER_EVIDENCE=1. Disposable catalog/files only.

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const test = require("node:test");

const REPO_ROOT = path.resolve(__dirname, "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const CHROME = process.env.FRAMENEST_CHROME_BIN || "google-chrome-stable";
const gated = process.env.FRAMENEST_RUN_BROWSER_EVIDENCE !== "1";
const MEDIA_ID = "11111111-1111-4111-8111-111111111111";
const LOCATION_ID = "33333333-3333-4333-8333-333333333333";

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const port = server.address().port;
      server.close(() => resolve(port));
    });
  });
}

function generateMp4(dir) {
  const ffmpegBin = (spawnSync("bash", ["-lc", "command -v ffmpeg"], { encoding: "utf8" }).stdout || "").trim();
  assert.ok(ffmpegBin, "ffmpeg is required");
  const mp4 = path.join(dir, "clip.mp4");
  const result = spawnSync(ffmpegBin, [
    "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
    "-pix_fmt", "yuv420p",
    mp4,
  ], { encoding: "utf8", timeout: 60000 });
  assert.equal(result.status, 0, result.stderr);
  return mp4;
}

function pythonBootstrapScript() {
  return String.raw`
from __future__ import annotations
import json, os, sqlite3, sys, uuid
from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID, SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import CAPABILITIES_BY_ROLE, IdentityContext, ROLE_ADMIN, ROLE_USER
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DB, MEDIA, PREVIEWS, COVER_ROOT, THUMBS, PORT = sys.argv[1:7]
PORT = int(PORT)
ROLE = os.environ.get("FRAMENEST_BROWSER_ROLE", "admin")
upgrade_database_to_head(KronikaSettings(database_path=DB, _env_file=None))
MEDIA_ID = "11111111-1111-4111-8111-111111111111"
LOCATION_ID = "33333333-3333-4333-8333-333333333333"
LIB = "55555555-5555-4555-8555-555555555555"
DEV = "66666666-6666-4666-8666-666666666666"
conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', 1, 1)", (MEDIA_ID,))
    conn.execute("INSERT INTO devices (id, display_name) VALUES (?, 'device')", (DEV,))
    conn.execute("INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) VALUES (?, ?, 'l', 'posix', ?)", (LIB, DEV, MEDIA))
    conn.execute(
        "INSERT INTO physical_media_locations (id, media_id, library_id, relative_path, availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) VALUES (?, ?, ?, 'clip.mp4', 'available', 1, 1, 1, 1)",
        (LOCATION_ID, MEDIA_ID, LIB),
    )
    conn.execute(
        "INSERT INTO media_metadata (media_id, display_title, description, content_category, acquisition_source, created_at_ms, updated_at_ms) VALUES (?, 'Removal Fixture', 'Synthetic description', 'general', 'library_scan', 1, 1)",
        (MEDIA_ID,),
    )
    conn.execute("INSERT INTO canonical_tags (key, display_name, created_at_ms, updated_at_ms) VALUES ('manual', 'Manual', 1, 1)")
    conn.execute("INSERT INTO media_canonical_tags (media_id, tag_key, position) VALUES (?, 'manual', 0)", (MEDIA_ID,))
    conn.execute(
        "INSERT INTO media_content_publications (media_id, published_at_ms, publication_origin) VALUES (?, 1, 'admin_explicit')",
        (MEDIA_ID,),
    )
    conn.commit()
finally:
    conn.close()

settings = KronikaSettings(
    database_path=DB,
    gallery_preview_cache_path=PREVIEWS,
    cover_storage_root=COVER_ROOT,
    cover_thumbnail_cache_path=THUMBS,
    host="127.0.0.1",
    port=PORT,
    _env_file=None,
)
app = create_app(settings=settings)
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

@app.get("/api/identity/me")
def identity_me(request: Request):
    identity = request.scope.get(SCOPE_IDENTITY)
    if not isinstance(identity, IdentityContext):
        return JSONResponse(
            {"error": {"code": "IDENTITY_REQUIRED", "message": "required"}},
            status_code=401,
        )
    return {
        "login": identity.login,
        "display_name": identity.display_name,
        "role": identity.role,
        "capabilities": sorted(identity.capabilities),
        "provenance": identity.provenance,
    }

class BrowserIdentityInjector(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        role = ROLE_ADMIN if ROLE == "admin" else ROLE_USER
        request.scope[SCOPE_IDENTITY] = IdentityContext(
            login=f"browser-{role}@example.com",
            login_key=f"browser-{role}@example.com",
            display_name=f"Browser {role}",
            role=role,
            capabilities=CAPABILITIES_BY_ROLE[role],
            provenance="tailscale-serve",
        )
        request.scope[SCOPE_AUDIT_EVENT_ID] = str(uuid.uuid4())
        return await call_next(request)
app = BrowserIdentityInjector(app)
import uvicorn
print("READY %s" % PORT, flush=True)
uvicorn.run(app, host="127.0.0.1", port=PORT, log_level="error", access_log=False)
`;
}

function startServer(workdir, port, mediaDir, role) {
  const db = path.join(workdir, "catalog.sqlite3");
  const previews = path.join(workdir, "previews");
  const coverRoot = path.join(workdir, "covers");
  const thumbs = path.join(workdir, "thumbnails");
  const proc = spawn(VENV_PYTHON, ["-u", "-", db, mediaDir, previews, coverRoot, thumbs, String(port)], {
    cwd: workdir,
    env: { ...process.env, FRAMENEST_BROWSER_ROLE: role, LD_LIBRARY_PATH: "" },
    stdio: ["pipe", "pipe", "pipe"],
  });
  proc.stdin.write(pythonBootstrapScript());
  proc.stdin.end();
  let stdout = "";
  let stderr = "";
  proc.stdout.on("data", (chunk) => { stdout += chunk; });
  proc.stderr.on("data", (chunk) => { stderr += chunk; });
  const ready = new Promise((resolve) => {
    const started = Date.now();
    const timer = setInterval(() => {
      if (stdout.includes(`READY ${port}`) || Date.now() - started > 25000) {
        clearInterval(timer);
        resolve();
      }
    }, 100);
  });
  return { proc, ready, meta: () => ({ stdout, stderr }), paths: { db, mediaDir, previews, coverRoot } };
}

function launchChrome(debugPort, profile) {
  const proc = spawn(CHROME, [
    "--headless=new", "--no-sandbox", "--disable-gpu", "--disable-dev-shm-usage",
    "--no-first-run", "--no-default-browser-check",
    `--remote-debugging-port=${debugPort}`,
    "--remote-debugging-address=127.0.0.1",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe"] });
  let stderr = "";
  proc.stderr.on("data", (c) => { stderr += c; });
  const ready = async () => {
    const deadline = Date.now() + 20000;
    while (Date.now() < deadline) {
      try {
        const response = await fetch(`http://127.0.0.1:${debugPort}/json/version`);
        if (response.ok) return;
      } catch { /* retry */ }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error(`chrome debug endpoint not ready: ${stderr}`);
  };
  return { proc, ready };
}

function cdpConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
    const listeners = new Map();
    let nextId = 1;
    ws.onopen = () => {
      resolve({
        send(method, params = {}) {
          return new Promise((res, rej) => {
            const id = nextId++;
            pending.set(id, { res, rej });
            ws.send(JSON.stringify({ id, method, params }));
          });
        },
        on(method, handler) {
          if (!listeners.has(method)) listeners.set(method, []);
          listeners.get(method).push(handler);
        },
        close() { try { ws.close(); } catch { /* noop */ } },
      });
    };
    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);
      if (message.id && pending.has(message.id)) {
        const { res, rej } = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) rej(new Error(message.error.message));
        else res(message.result);
        return;
      }
      const handlers = listeners.get(message.method);
      if (handlers) handlers.forEach((h) => h(message.params));
    };
    ws.onerror = (error) => reject(error);
  });
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression, returnByValue: true, awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(`evaluate exception: ${JSON.stringify(result.exceptionDetails)}`);
  }
  return result.result ? result.result.value : undefined;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(cdp, expression, { timeout = 15000, label = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await evaluate(cdp, expression);
    if (last) return last;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${label}; last=${JSON.stringify(last)}`);
}

async function withBrowser(role, fn) {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "framenest-removal-"));
  const mediaDir = path.join(workdir, "media");
  fs.mkdirSync(mediaDir);
  let server;
  let chrome;
  let cdp;
  try {
    const mp4 = generateMp4(mediaDir);
    const port = await freePort();
    server = startServer(workdir, port, mediaDir, role);
    await server.ready;
    assert.ok(server.meta().stdout.includes(`READY ${port}`), server.meta().stderr);
    const appUrl = `http://127.0.0.1:${port}/`;
    const debugPort = await freePort();
    chrome = launchChrome(debugPort, path.join(workdir, "chrome-profile"));
    await chrome.ready();
    const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((r) => r.json());
    const page = targets.find((target) => target.type === "page");
    assert.ok(page);
    cdp = await cdpConnect(page.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");
    const consoleErrors = [];
    const network = [];
    cdp.on("Runtime.consoleAPICalled", (params) => {
      if (params.type === "error") consoleErrors.push(params);
    });
    cdp.on("Network.requestWillBeSent", (params) => {
      network.push({ url: params.request.url, method: params.request.method });
    });
    await fn({
      cdp, appUrl, mp4, consoleErrors, network, mediaDir, db: server.paths.db, port,
    });
  } finally {
    if (cdp) cdp.close();
    if (chrome) chrome.proc.kill("SIGKILL");
    if (server) server.proc.kill("SIGKILL");
    fs.rmSync(workdir, { recursive: true, force: true });
  }
}

test("real browser: admin catalog removal retain-all flow", { skip: gated, timeout: 180000 }, async () => {
  await withBrowser("admin", async ({ cdp, appUrl, mp4, consoleErrors, network }) => {
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 1280, height: 900, deviceScaleFactor: 1, mobile: false,
    });
    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(cdp, `document.querySelectorAll(".catalog-card").length >= 1`, { label: "gallery card" });
    await waitFor(cdp, `(() => {
      const button = document.querySelector("#admin-media-open-button");
      return Boolean(button && !button.hidden);
    })()`, { label: "manage media visible" });
    await evaluate(cdp, `(() => {
      adminCatalogState.publication = "published";
      const select = document.querySelector("#admin-media-publication-filter");
      if (select) select.value = "published";
      return true;
    })()`);
    await evaluate(cdp, `document.querySelector("#admin-media-open-button").click()`);
    await waitFor(cdp, `document.querySelector("#admin-media-browser") && !document.querySelector("#admin-media-browser").hidden`, { label: "admin browser" });
    await waitFor(cdp, `document.querySelector('[data-admin-action="catalog-remove"]')`, { label: "remove action", timeout: 20000 });
    await evaluate(cdp, `(() => {
      const checkbox = document.querySelector('input[data-admin-batch-media-id="${MEDIA_ID}"]');
      if (checkbox && !checkbox.checked) {
        checkbox.checked = true;
        checkbox.dispatchEvent(new Event("change", { bubbles: true }));
      }
      return true;
    })()`);
    const staleConflict = await evaluate(cdp, `fetch("/api/admin/media/${MEDIA_ID}/catalog-removal", {
      headers: { Accept: "application/json" },
      cache: "no-store",
    }).then(async (previewResponse) => {
      const preview = await previewResponse.json();
      const conflict = await fetch("/api/admin/media/${MEDIA_ID}/catalog-removal", {
        method: "POST",
        headers: {
          Accept: "application/json",
          "Content-Type": "application/json",
          "X-FrameNest-Request": "1",
        },
        body: JSON.stringify({
          acknowledge_consequences: true,
          consequence_fingerprint: "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff",
        }),
        cache: "no-store",
      });
      return {
        previewOk: previewResponse.ok,
        fingerprint: preview.consequence_fingerprint || "",
        conflictStatus: conflict.status,
      };
    })`);
    assert.equal(staleConflict.previewOk, true);
    assert.ok(staleConflict.fingerprint.length > 8);
    assert.equal(staleConflict.conflictStatus, 409);
    await evaluate(cdp, `document.querySelector('[data-admin-action="catalog-remove"]').click()`);
    await waitFor(cdp, `document.querySelector("#confirmation-dialog[open]")`, { label: "confirmation" });
    const message = await evaluate(cdp, `document.querySelector("#confirmation-dialog-message")?.textContent || ""`);
    assert.match(message, /original media file remains on disk/i);
    assert.match(message, /does not purge originals/i);
    await evaluate(cdp, `document.querySelector("#confirmation-confirm-button").click()`);
    await waitFor(cdp, `!document.querySelector('[data-admin-action="catalog-remove"]')`, { label: "removed from manage list" });
    const batchCleared = await evaluate(cdp, `(() => {
      if (typeof adminBatchState === "undefined" || !adminBatchState.selectedMediaIds) return true;
      return !adminBatchState.selectedMediaIds.has("${MEDIA_ID}");
    })()`);
    assert.equal(batchCleared, true);
    await evaluate(cdp, `document.querySelector("#admin-media-close-button")?.click()`);
    await waitFor(cdp, `document.querySelector("#admin-media-browser")?.hidden !== false`, { label: "admin closed" });
    await waitFor(cdp, `document.querySelectorAll(".catalog-card").length === 0`, { label: "gallery empty", timeout: 20000 });
    const missing = await evaluate(cdp, `Promise.all([
      fetch("/api/media/${MEDIA_ID}/metadata", { headers: { Accept: "application/json" } }).then(r => r.status),
      fetch("/api/media/${MEDIA_ID}/content").then(r => r.status),
      fetch("/api/media/${MEDIA_ID}/download").then(r => r.status),
    ])`);
    assert.deepEqual(missing, [404, 404, 404]);
    assert.ok(fs.existsSync(mp4), "original fixture remains");
    await cdp.send("Emulation.setDeviceMetricsOverride", {
      width: 390, height: 844, deviceScaleFactor: 2, mobile: true,
    });
    const overflow = await evaluate(cdp, `document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1`);
    assert.equal(overflow, true);
    assert.equal(consoleErrors.length, 0, JSON.stringify(consoleErrors));
    assert.ok(!network.some((entry) => /nvidia|ai-gateway|openai|anthropic/i.test(entry.url)));
  });
});

test("real browser: ordinary user never sees remove action", { skip: gated, timeout: 120000 }, async () => {
  await withBrowser("user", async ({ cdp, appUrl }) => {
    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(cdp, `document.readyState === "complete"`, { label: "ready" });
    const hidden = await evaluate(cdp, `(() => {
      const button = document.querySelector("#admin-media-open-button");
      return !button || button.hidden || button.offsetParent === null;
    })()`);
    assert.equal(hidden, true);
    const denied = await evaluate(cdp, `fetch("/api/admin/media/${MEDIA_ID}/catalog-removal", { headers: { Accept: "application/json" } }).then(r => r.status)`);
    assert.equal(denied, 403);
  });
});
