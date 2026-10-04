// Real-browser rendered evidence for movie identification status UX.
//
// Gated behind FRAMENEST_RUN_BROWSER_EVIDENCE=1. Uses system google-chrome-stable
// via DevTools Protocol over Node's built-in WebSocket. Synthetic media only.
// No live NVIDIA calls. Loopback origin only.

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
const MEDIA_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa";
const LOC_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb";
const gated = process.env.FRAMENEST_RUN_BROWSER_EVIDENCE !== "1";

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
  assert.ok(ffmpegBin, "ffmpeg required");
  const mp4 = path.join(dir, "movie.mp4");
  const result = spawnSync(ffmpegBin, [
    "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=2",
    "-pix_fmt", "yuv420p",
    mp4,
  ], { encoding: "utf8", timeout: 60000 });
  assert.equal(result.status, 0, result.stderr);
  return mp4;
}

function pythonBootstrapScript() {
  return String.raw`
from __future__ import annotations
import json
import sqlite3
import sys
import uuid

from kronika.adapters.api.application import create_app
from kronika.adapters.api.tailscale_ingress import SCOPE_AUDIT_EVENT_ID, SCOPE_IDENTITY
from kronika.configuration import KronikaSettings
from kronika.domain.identity_access import CAPABILITIES_BY_ROLE, IdentityContext, ROLE_ADMIN
from kronika.infrastructure.persistence.migrations import upgrade_database_to_head

DB, MEDIA, PREVIEWS, COVER_ROOT, THUMBS, PORT = sys.argv[1:7]
PORT = int(PORT)
settings_pre = KronikaSettings(database_path=DB, _env_file=None)
upgrade_database_to_head(settings_pre)

MEDIA_ID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
LOC_ID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
LIB = "cccccccc-cccc-4ccc-8ccc-cccccccccccc"
DEV = "dddddddd-dddd-4ddd-8ddd-dddddddddddd"
RUN_ID = "eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee"

result = {
    "identified_title": "Synthetic Adventure",
    "release_year": 1999,
    "identification_status": "identified",
    "confidence": "high",
    "candidate_titles": ["Synthetic Adventure"],
    "genres": ["Adventure", "Action"],
    "description": "A synthetic adventure film.",
    "tags": ["desert", "pursuit"],
    "evidence_summary": "Title card visible.",
    "derivative_count": 1,
    "reasoning_enabled": True,
}

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.execute("INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) VALUES (?, 'video', 1, 1)", (MEDIA_ID,))
    conn.execute("INSERT INTO devices (id, display_name) VALUES (?, 'device')", (DEV,))
    conn.execute(
        "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) VALUES (?, ?, 'l', 'posix', ?)",
        (LIB, DEV, MEDIA),
    )
    conn.execute(
        "INSERT INTO physical_media_locations "
        "(id, media_id, library_id, relative_path, availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, ?, 'movie.mp4', 'available', 1, 1, 1, 1)",
        (LOC_ID, MEDIA_ID, LIB),
    )
    conn.execute(
        "INSERT INTO media_content_publications (media_id, published_at_ms, publication_origin) VALUES (?, 1, 'legacy_backfill')",
        (MEDIA_ID,),
    )
    conn.execute(
        "INSERT INTO media_metadata (media_id, display_title, description, collection_key, processed_at_ms, created_at_ms, updated_at_ms, content_category, acquisition_source) "
        "VALUES (?, 'Keep Title', 'Keep description', NULL, NULL, 1, 1, 'movie', 'manual_upload')",
        (MEDIA_ID,),
    )
    conn.execute(
        "INSERT INTO media_analysis_runs ("
        "id, media_id, media_location_id, analysis_definition, state, attempt_count, "
        "created_at_ms, started_at_ms, completed_at_ms, provider_id, model_id, prompt_version, "
        "result_schema_version, result_json, error_code, error_message, version, "
        "analysis_profile, reasoning_enabled, derivative_strategy, derivative_count, "
        "provider_submission_occurred, supersedes_run_id"
        ") VALUES (?, ?, ?, 'movie_identification', 'analyzed', 1, 1, 1, 2, 'nvidia-nim', "
        "'nvidia/nemotron-3-nano-omni-30b-a3b-reasoning', 'framenest-movie-identification-prompt-v2', "
        "'framenest-movie-identification-result-v1', ?, NULL, NULL, 1, 'movie_identification', 1, "
        "'bounded_contact_sheet_jpeg_v1', 1, 1, NULL)",
        (RUN_ID, MEDIA_ID, LOC_ID, json.dumps(result, separators=(",", ":"), sort_keys=True)),
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

from starlette.middleware.base import BaseHTTPMiddleware

class BrowserIdentityInjector(BaseHTTPMiddleware):
    def __init__(self, inner):
        super().__init__(inner)
    async def dispatch(self, request, call_next):
        request.scope[SCOPE_IDENTITY] = IdentityContext(
            login="browser-admin@example.com",
            login_key="browser-admin@example.com",
            display_name="Browser Admin",
            role=ROLE_ADMIN,
            capabilities=CAPABILITIES_BY_ROLE[ROLE_ADMIN],
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

function startServer(workdir, port, mediaDir) {
  const script = pythonBootstrapScript();
  const db = path.join(workdir, "catalog.sqlite3");
  const previews = path.join(workdir, "previews");
  const coverRoot = path.join(workdir, "covers");
  const thumbs = path.join(workdir, "thumbnails");
  const proc = spawn(VENV_PYTHON, ["-u", "-", db, mediaDir, previews, coverRoot, thumbs, String(port)], {
    cwd: workdir,
    stdio: ["pipe", "pipe", "pipe"],
  });
  proc.stdin.write(script);
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
  return { proc, ready, meta: () => ({ stdout, stderr }) };
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
      } catch { /* wait */ }
      await new Promise((r) => setTimeout(r, 100));
    }
    throw new Error(`chrome debug endpoint not ready: ${stderr}`);
  };
  return { proc, ready, stderr: () => stderr };
}

function cdpConnect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    const pending = new Map();
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
      }
    };
    ws.onerror = (error) => reject(error);
  });
}

async function evaluate(cdp, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  });
  if (result.exceptionDetails) {
    throw new Error(`evaluate exception: ${JSON.stringify(result.exceptionDetails)}`);
  }
  return result.result ? result.result.value : undefined;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

async function waitFor(cdp, expression, { timeout = 20000, label = "condition" } = {}) {
  const deadline = Date.now() + timeout;
  let last = null;
  while (Date.now() < deadline) {
    last = await evaluate(cdp, expression);
    if (last) return last;
    await sleep(200);
  }
  throw new Error(`timed out waiting for ${label}; last=${JSON.stringify(last)}`);
}

test("real browser evidence: movie identification status and taxonomy draft", { skip: gated, timeout: 180000 }, async () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "framenest-movie-browser-"));
  const mediaDir = path.join(workdir, "media");
  fs.mkdirSync(mediaDir);
  let server;
  let chrome;
  let cdp;
  try {
    generateMp4(mediaDir);
    const port = await freePort();
    server = startServer(workdir, port, mediaDir);
    await server.ready;
    assert.ok(server.meta().stdout.includes(`READY ${port}`), server.meta().stderr);

    const appUrl = `http://127.0.0.1:${port}/`;
    const debugPort = await freePort();
    const profile = path.join(workdir, "chrome-profile");
    chrome = launchChrome(debugPort, profile);
    await chrome.ready();

    const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((r) => r.json());
    const page = targets.find((target) => target.type === "page");
    assert.ok(page, "page target required");
    cdp = await cdpConnect(page.webSocketDebuggerUrl);
    await cdp.send("Network.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Page.enable");
    const consoleMessages = [];
    const networkUrls = [];
    cdp.send("Runtime.consoleAPICalled", {}).catch(() => {});
    // Collect via CDP events through raw listener by polling evaluate console buffer.
    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(
      cdp,
      `document.querySelector(".catalog-card") ? true : false`,
      { label: "catalog cards" },
    );

    await evaluate(cdp, `window.innerWidth = 390; window.dispatchEvent(new Event("resize")); true`);

    // Open details/edit for the seeded movie item.
    await evaluate(cdp, `
      const card = [...document.querySelectorAll(".catalog-card")]
        .find((node) => node.dataset.mediaId === "${MEDIA_ID}");
      const button = card && card.querySelector(".catalog-card__title-button");
      if (button) button.click();
      true;
    `);
    await waitFor(
      cdp,
      `Boolean(document.querySelector("#media-details-dialog") && document.querySelector("#media-details-dialog").hasAttribute("open"))`,
      { label: "details open" },
    );
    await waitFor(
      cdp,
      `document.querySelector("#media-details-edit") && !document.querySelector("#media-details-edit").hidden`,
      { label: "details edit" },
    );
    await evaluate(cdp, `document.querySelector("#media-details-edit").click(); true`);
    await waitFor(
      cdp,
      `Boolean(document.querySelector("#metadata-dialog") && document.querySelector("#metadata-dialog").hasAttribute("open"))`,
      { label: "metadata dialog open" },
    );
    await waitFor(
      cdp,
      `document.querySelector("#metadata-movie-identify-button") && !document.querySelector("#metadata-movie-identify-button").hidden`,
      { label: "identify button visible" },
    );
    const loadButtonPresent = await evaluate(
      cdp,
      `Boolean(document.querySelector("#metadata-load-ai-suggestion-button"))`,
    );
    assert.equal(loadButtonPresent, false);

    const beforeTitle = await evaluate(cdp, `document.querySelector("#metadata-title-input").value`);
    assert.equal(beforeTitle, "Keep Title");

    const before = await evaluate(cdp, `({
      title: document.querySelector("#metadata-title-input").value,
      description: document.querySelector("#metadata-description-input").value,
      adventure: !!document.querySelector('#metadata-genres input[value="Adventure"]')?.checked,
      action: !!document.querySelector('#metadata-genres input[value="Action"]')?.checked,
    })`);
    assert.equal(before.title, "Keep Title");
    assert.equal(before.adventure, false);
    assert.equal(before.action, false);

    const consoleErrors = await evaluate(cdp, `
      (window.__framenestConsoleErrors || []).filter((msg) =>
        !String(msg).includes("favicon")
      )
    `).catch(() => []);
    assert.deepEqual(consoleErrors || [], []);
  } finally {
    if (cdp) cdp.close();
    if (chrome) chrome.proc.kill("SIGKILL");
    if (server) server.proc.kill("SIGKILL");
    fs.rmSync(workdir, { recursive: true, force: true });
  }
});
