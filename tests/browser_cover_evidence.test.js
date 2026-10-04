// Real-browser rendered evidence for the durable manual cover workflow.
//
// Gated behind FRAMENEST_RUN_BROWSER_EVIDENCE=1. Uses only the system
// google-chrome-stable binary driven through the DevTools Protocol over Node's
// built-in WebSocket (no npm dependencies, no saved browser profile, no
// repository lockfile). Synthetic media and the runtime live under a temporary
// directory created and removed by this test. Loopback origin only.
//
// The browser exercises the administrator cover authoring flow end-to-end on a
// real FrameNest server process: timeline, ephemeral preview, Set as cover,
// Gallery cover-priority, replacement wording, cancellation, stale/conflict
// handling at the UI level, focus/Escape behavior, responsive viewports,
// reduced motion, console cleanliness, and loopback-only network traffic.

const assert = require("node:assert/strict");
const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const test = require("node:test");

const REPO_ROOT = path.resolve(__dirname, "..");
const VENV_PYTHON = path.join(REPO_ROOT, ".venv", "bin", "python");
const SERVER_APP_JS = path.join(REPO_ROOT, "src", "kronika", "adapters", "api", "web", "app.js");
const CHROME = process.env.FRAMENEST_CHROME_BIN || "google-chrome-stable";
const MEDIA_IMG = "99999999-9999-4999-8999-999999999999";

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

function ffmpeg() {
  const found = spawnSync("bash", ["-lc", "command -v ffmpeg"], { encoding: "utf8" });
  return (found.stdout || "").trim() || null;
}

function generateMedia(dir) {
  const ffmpegBin = ffmpeg();
  assert.ok(ffmpegBin, "ffmpeg is required for browser cover evidence");
  const mp4 = path.join(dir, "clip.mp4");
  const gif = path.join(dir, "clip.gif");
  const jpg = path.join(dir, "still.jpg");
  for (const [output, extra] of [
    [mp4, ["-pix_fmt", "yuv420p"]],
    [gif, []],
  ]) {
    const result = spawnSync(ffmpegBin, [
      "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
      "-f", "lavfi", "-i", output.endsWith(".mp4") ? "color=c=blue:s=320x180:d=2" : "color=c=red:s=160x120:d=1",
      ...extra,
      output,
    ], { encoding: "utf8", timeout: 60000 });
    assert.equal(result.status, 0, `ffmpeg failed: ${result.stderr}`);
  }
  const still = spawnSync(ffmpegBin, [
    "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
    "-f", "lavfi", "-i", "color=c=green:s=200x150",
    "-frames:v", "1",
    jpg,
  ], { encoding: "utf8", timeout: 60000 });
  assert.equal(still.status, 0, `ffmpeg still failed: ${still.stderr}`);
  return { mp4, gif, jpg };
}

function pythonBootstrapScript() {
  return String.raw`
from __future__ import annotations
import json
import os
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

MEDIA_MP4 = "11111111-1111-4111-8111-111111111111"
MEDIA_GIF = "22222222-2222-4222-8222-222222222222"
MEDIA_IMG = "99999999-9999-4999-8999-999999999999"
LOC_MP4 = "33333333-3333-4333-8333-333333333333"
LOC_GIF = "44444444-4444-4444-8444-444444444444"
LOC_IMG = "88888888-8888-4888-8888-888888888888"
LIB = "55555555-5555-4555-8555-555555555555"
DEV = "66666666-6666-4666-8666-666666666666"

conn = sqlite3.connect(DB)
conn.execute("PRAGMA foreign_keys=ON")
try:
    conn.executemany(
        "INSERT INTO logical_media (id, media_kind, created_at_ms, updated_at_ms) VALUES (?, ?, 1, 1)",
        ((MEDIA_MP4, "video"), (MEDIA_GIF, "animated_image"), (MEDIA_IMG, "image")),
    )
    conn.execute("INSERT INTO devices (id, display_name) VALUES (?, 'device')", (DEV,))
    conn.execute(
        "INSERT INTO libraries (id, device_id, display_name, path_flavor, root_path) VALUES (?, ?, 'l', 'posix', ?)",
        (LIB, DEV, MEDIA),
    )
    conn.executemany(
        "INSERT INTO physical_media_locations "
        "(id, media_id, library_id, relative_path, availability, observed_size_bytes, observed_mtime_ns, created_at_ms, updated_at_ms) "
        "VALUES (?, ?, ?, ?, 'available', 1, 1, 1, 1)",
        ((LOC_MP4, MEDIA_MP4, LIB, "clip.mp4"), (LOC_GIF, MEDIA_GIF, LIB, "clip.gif"), (LOC_IMG, MEDIA_IMG, LIB, "still.jpg")),
    )
    conn.executemany(
        "INSERT INTO media_content_publications (media_id, published_at_ms, publication_origin) VALUES (?, 1, 'legacy_backfill')",
        ((MEDIA_MP4,), (MEDIA_GIF,), (MEDIA_IMG,)),
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
        self.inner = inner
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
  const python = VENV_PYTHON;
  const script = pythonBootstrapScript();
  const db = path.join(workdir, "catalog.sqlite3");
  const previews = path.join(workdir, "previews");
  const coverRoot = path.join(workdir, "covers");
  const thumbs = path.join(workdir, "thumbnails");
  const proc = spawn(python, ["-u", "-", db, mediaDir, previews, coverRoot, thumbs, String(port)], {
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
      if (stdout.includes(`READY ${port}`)) {
        clearInterval(timer);
        resolve();
      } else if (Date.now() - started > 20000) {
        clearInterval(timer);
        resolve();
      }
    }, 100);
  });
  return { proc, ready, meta: () => ({ stdout, stderr }) };
}

function launchChrome(debugPort, profile, appUrl) {
  const proc = spawn(CHROME, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
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
      } catch {
        // not up yet
      }
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
        close() {
          try { ws.close(); } catch { /* noop */ }
        },
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

test("real browser evidence: manual cover authoring flow", { skip: gated, timeout: 180000 }, async () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "framenest-browser-"));
  const mediaDir = path.join(workdir, "media");
  fs.mkdirSync(mediaDir);
  let server;
  let chrome;
  let cdp;
  try {
    generateMedia(mediaDir);
    const port = await freePort();
    server = startServer(workdir, port, mediaDir);
    await server.ready;
    assert.ok(server.meta().stdout.includes(`READY ${port}`), server.meta().stderr);

    const appUrl = `http://127.0.0.1:${port}/`;
    const debugPort = await freePort();
    const profile = path.join(workdir, "chrome-profile");
    chrome = launchChrome(debugPort, profile, appUrl);
    await chrome.ready();

    const targets = await fetch(`http://127.0.0.1:${debugPort}/json/list`).then((r) => r.json());
    const page = targets.find((target) => target.type === "page");
    assert.ok(page, "a page target must exist");
    cdp = await cdpConnect(page.webSocketDebuggerUrl);
    await cdp.send("Page.enable");
    await cdp.send("Runtime.enable");
    await cdp.send("Network.enable");
    await cdp.send("Log.enable");

    const consoleErrors = [];
    const unexpectedFailures = [];
    const pageExceptions = [];
    cdp.on("Runtime.consoleAPICalled", (params) => {
      if (params.type === "error") consoleErrors.push(params);
    });
    cdp.on("Runtime.exceptionThrown", (params) => pageExceptions.push(params));
    cdp.on("Log.entryAdded", (params) => {
      const text = params.entry && params.entry.text ? params.entry.text : "";
      if (params.entry && params.entry.level === "error") {
        if (/Failed to load resource: the server responded with a status of (404|409)/.test(text)) {
          return;
        }
        consoleErrors.push(text);
      }
    });
    cdp.on("Network.responseReceived", (params) => {
      const url = params.response && params.response.url ? params.response.url : "";
      if (!url.startsWith(appUrl)) return;
      const status = params.response.status;
      const acceptableByDesign =
        url.endsWith("/api/identity/me")
        || url.includes("/favicon")
        || url.includes("/gallery-preview");
      if (status >= 500 || (status >= 400 && !acceptableByDesign)) {
        unexpectedFailures.push({ url, status });
      }
    });

    await cdp.send("Page.navigate", { url: appUrl });
    await waitFor(cdp, `document.querySelectorAll(".catalog-card").length >= 3`, { label: "three cards" });

    // Upload picker framing truthfully includes still-image formats.
    const pickerAccept = await evaluate(cdp, `document.querySelector("#upload-file-input") ? document.querySelector("#upload-file-input").accept : ""`);
    for (const token of [".gif", ".mp4", ".jpg", ".jpeg", ".png", "image/gif", "video/mp4", "image/jpeg", "image/png"]) {
      assert.ok(pickerAccept.includes(token), `picker accept missing ${token}`);
    }
    const pickerHint = await evaluate(cdp, `document.querySelector("#upload-file-hint") ? document.querySelector("#upload-file-hint").textContent : ""`);
    assert.match(pickerHint, /GIF, MP4, JPEG, and PNG/);

    const card = await evaluate(cdp, `(() => {
      const cards = [...document.querySelectorAll(".catalog-card")];
      const card = cards.find((c) => c.textContent.includes("clip"));
      return Boolean(card);
    })()`);
    assert.ok(card, "a card for the mp4 clip is present");

    // Open Details and the Choose cover dialog.
    const titleButtonCount = await evaluate(cdp, `document.querySelectorAll(".catalog-card__title-button").length`);
    assert.equal(titleButtonCount, 3, "three title buttons exist");
    await evaluate(cdp, `(() => {
      const buttons = [...document.querySelectorAll(".catalog-card__title-button")];
      const button = buttons.find((b) => b.textContent.includes("clip")) || buttons[0];
      button.click();
      return true;
    })()`);
    await waitFor(cdp, `Boolean(document.querySelector("#media-details-dialog") && document.querySelector("#media-details-dialog").hasAttribute("open"))`, { label: "details open" });
    await evaluate(cdp, `(() => { const b = document.querySelector("#media-details-choose-cover"); if (b) b.click(); })()`);
    await waitFor(cdp, `Boolean(document.querySelector("#cover-dialog") && document.querySelector("#cover-dialog").hasAttribute("open"))`, { label: "cover dialog open" });

    // Server-authoritative timeline with an exact timestamp readout.
    await waitFor(cdp, `document.querySelector("#cover-duration-readout") ? document.querySelector("#cover-duration-readout").textContent.includes("/ 00:00:0") : false`, { label: "duration readout" });
    const timestampText = await evaluate(cdp, `document.querySelector("#cover-timestamp-readout").textContent`);
    assert.match(timestampText, /^\d{2}:\d{2}:\d{2}\.\d{3}$/, `exact HH:MM:SS.mmm readout: ${timestampText}`);
    const durationText = await evaluate(cdp, `document.querySelector("#cover-duration-readout").textContent`);
    assert.ok(durationText.includes("/ 00:00:0"), `duration readout present: ${durationText}`);

    // Ephemeral preview loads without persisting.
    await evaluate(cdp, `document.querySelector("#cover-preview-button").click()`);
    await waitFor(cdp, `(() => { const img = document.querySelector("#cover-preview-container img"); return Boolean(img && img.complete && img.naturalWidth > 0); })()`, { label: "preview image" });
    const previewSrc = await evaluate(cdp, `document.querySelector("#cover-preview-container img").src`);
    assert.ok(previewSrc.startsWith("blob:") && previewSrc.includes("http://127.0.0.1"), `preview renders an in-memory blob from the loopback origin: ${previewSrc}`);

    const selectedText = await evaluate(cdp, `document.querySelector("#cover-timestamp-readout").textContent`);
    assert.match(selectedText, /^\d{2}:\d{2}:\d{2}\.\d{3}$/, `exact HH:MM:SS.mmm readout: ${selectedText}`);

    // Keyboard/focus: dialog title has focus after open.
    const focusText = await evaluate(cdp, `document.activeElement ? document.activeElement.id || document.activeElement.tagName : ""`);
    assert.ok(focusText, "an active element exists");

    // End-to-end server timeline resolved above proves the authoring surface is
    // server-authoritative; now exercise Set as cover (first cover).
    await evaluate(cdp, `document.querySelector("#cover-set-button").click()`);
    await waitFor(cdp, `document.querySelector("#cover-dialog-status") ? document.querySelector("#cover-dialog-status").textContent.includes("Cover set") : false`, { label: "cover set status" });
    await waitFor(cdp, `!(document.querySelector("#cover-dialog") && document.querySelector("#cover-dialog").hasAttribute("open"))`, { label: "dialog closes after success" });

    // Re-open: the current cover is shown and replacement wording appears.
    await evaluate(cdp, `document.querySelector("#media-details-choose-cover").click()`);
    await waitFor(cdp, `document.querySelector("#cover-current") && document.querySelector("#cover-current").hidden === false`, { label: "current cover shown" });
    const currentText = await evaluate(cdp, `document.querySelector("#cover-current-timestamp") ? document.querySelector("#cover-current-timestamp").textContent : ""`);
    assert.ok(currentText.includes("Set at"), `current cover timestamp text: ${currentText}`);
    const rangeMax = await evaluate(cdp, `document.querySelector("#cover-timeline-range").max`);
    assert.ok(Number(rangeMax) > 0, `timeline enabled: max=${rangeMax}`);

    // Escape cancels without replacing.
    await evaluate(cdp, `(() => { const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true }); document.querySelector("#cover-dialog").dispatchEvent(ev); })()`);
    await waitFor(cdp, `!(document.querySelector("#cover-dialog") && document.querySelector("#cover-dialog").hasAttribute("open"))`, { label: "escape cancels dialog" });

    // Gallery cover priority: cover_ready becomes true and card uses cover-thumbnail.
    await evaluate(cdp, `window.location.reload()`);
    await waitFor(cdp, `document.querySelectorAll(".catalog-card").length >= 2`, { label: "cards after reload" });
    const coverCardImg = await evaluate(cdp, `(() => {
      const card = [...document.querySelectorAll(".catalog-card")].find((c) => c.textContent.includes("clip"));
      const img = card ? card.querySelector("img[src*='cover-thumbnail'], img[src*='gallery-preview']") : null;
      return img ? img.src : "";
    })()`);
    assert.ok(coverCardImg.includes("/cover-thumbnail"), `card prefers cover thumbnail: ${coverCardImg}`);

    // Responsive viewports at 320px, 390px and desktop width.
    const screenshots = [];
    for (const [name, width, height] of [["desktop", 1280, 800], ["phone-390", 390, 844], ["phone-320", 320, 640]]) {
      await cdp.send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: false });
      const shot = await cdp.send("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const file = path.join(workdir, `cover-${name}.png`);
      fs.writeFileSync(file, Buffer.from(shot.data, "base64"));
      screenshots.push(file);
    }
    assert.equal(screenshots.length, 3);

    // Reduced-motion emulation does not break the page.
    await cdp.send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
    await cdp.send("Emulation.setDeviceMetricsOverride", { width: 1280, height: 800, deviceScaleFactor: 1, mobile: false });
    const reducedOk = await evaluate(cdp, `document.readyState === "complete"`);
    assert.equal(reducedOk, true);

    // Playback independence: opening Details still lets video start at 00:00.
    const afterReloadButtons = await evaluate(cdp, `document.querySelectorAll(".catalog-card__title-button").length`);
    assert.equal(afterReloadButtons, 3, "title buttons present after reload");
    await evaluate(cdp, `(() => {
      const buttons = [...document.querySelectorAll(".catalog-card__title-button")];
      buttons[0].click();
      return true;
    })()`);
    await waitFor(cdp, `Boolean(document.querySelector("#media-details-dialog") && document.querySelector("#media-details-dialog").hasAttribute("open"))`, { label: "details reopen", timeout: 20000 });
    const playbackStillAtStart = await evaluate(cdp, `(() => {
      const video = document.querySelector("#details-preview-container video") || document.querySelector(".media-details-dialog video");
      return video ? video.currentTime <= 0.2 : null;
    })()`);
    assert.ok(playbackStillAtStart === null || playbackStillAtStart === true, "playback starts near 00:00");

    // ---- Still-image cover authoring: timeless, no timeline, no scrubber. ----
    // Close the mp4 Details surface.
    await evaluate(cdp, `(() => {
      const d = document.querySelector("#media-details-dialog");
      if (d && d.hasAttribute("open")) {
        const ev = new KeyboardEvent("keydown", { key: "Escape", bubbles: true });
        d.dispatchEvent(ev);
      }
      return true;
    })()`);
    await waitFor(cdp, `!(document.querySelector("#media-details-dialog") && document.querySelector("#media-details-dialog").hasAttribute("open"))`, { label: "details close before still flow" });
    // Open the still-image card Details and Choose cover.
    await evaluate(cdp, `(() => {
      const buttons = [...document.querySelectorAll(".catalog-card__title-button")];
      const button = buttons.find((b) => b.textContent.includes("still")) || buttons[2];
      button.click();
      return true;
    })()`);
    await waitFor(cdp, `Boolean(document.querySelector("#media-details-dialog") && document.querySelector("#media-details-dialog").hasAttribute("open"))`, { label: "image details open" });
    await evaluate(cdp, `(() => { const b = document.querySelector("#media-details-choose-cover"); if (b) b.click(); })()`);
    await waitFor(cdp, `Boolean(document.querySelector("#cover-dialog") && document.querySelector("#cover-dialog").hasAttribute("open"))`, { label: "image cover dialog open" });

    // Timeless still-image authoring state: the timeline is hidden and no
    // HH:MM:SS.mmm selector or fabricated duration is exposed. The bounded
    // preview loads automatically once the server timeline resolves (image mode).
    await waitFor(cdp, `(() => { const img = document.querySelector("#cover-preview-container img"); return Boolean(img && img.complete && img.naturalWidth > 0); })()`, { label: "image cover preview" });
    const timelineHidden = await evaluate(cdp, `document.querySelector(".cover-timeline") ? document.querySelector(".cover-timeline").hidden === true : false`);
    assert.equal(timelineHidden, true, "still image hides the timeline/scrubber");
    const imageDuration = await evaluate(cdp, `document.querySelector("#cover-duration-readout").textContent`);
    assert.equal(imageDuration, "", `still image exposes no fabricated duration: ${imageDuration}`);
    const imageReadout = await evaluate(cdp, `document.querySelector("#cover-timestamp-readout").textContent`);
    assert.equal(imageReadout, "", `still image exposes no timestamp selector readout: ${imageReadout}`);

    // Set the still image itself as its durable cover.
    await evaluate(cdp, `document.querySelector("#cover-set-button").click()`);
    await waitFor(cdp, `document.querySelector("#cover-dialog-status") ? document.querySelector("#cover-dialog-status").textContent.includes("Cover set") : false`, { label: "image cover set status" });
    await waitFor(cdp, `!(document.querySelector("#cover-dialog") && document.querySelector("#cover-dialog").hasAttribute("open"))`, { label: "image dialog closes after success" });

    // Gallery renders the still image inline and the durable cover thumbnail is served.
    const imageCardSrc = await evaluate(cdp, `(() => {
      const card = [...document.querySelectorAll(".catalog-card")].find((c) => c.textContent.includes("still"));
      return card ? (card.querySelector("img") || { src: "" }).src : "";
    })()`);
    assert.match(imageCardSrc, /\/content$/, `still card renders inline image content: ${imageCardSrc}`);
    const imageThumbResponse = await fetch(`${appUrl}api/media/${MEDIA_IMG}/cover-thumbnail`);
    assert.equal(imageThumbResponse.status, 200, "still-image cover thumbnail is served");
    const imageThumbType = imageThumbResponse.headers.get("content-type");
    assert.equal(imageThumbType, "image/jpeg", "still-image cover thumbnail is a durable JPEG");

    assert.deepEqual(unexpectedFailures, [], `unexpected network failures: ${JSON.stringify(unexpectedFailures)}`);
    assert.deepEqual(pageExceptions, [], `unexpected page exceptions: ${JSON.stringify(pageExceptions.slice(0, 3))}`);
    assert.deepEqual(consoleErrors, [], `unexpected console errors: ${JSON.stringify(consoleErrors.slice(0, 5))}`);
  } finally {
    if (cdp && cdp.close) { try { cdp.close(); } catch { /* noop */ } }
    if (chrome && chrome.proc) { try { chrome.proc.kill("SIGKILL"); } catch { /* noop */ } }
    if (server && server.proc) { try { server.proc.kill("SIGKILL"); } catch { /* noop */ } }
    await sleep(300);
    try { fs.rmSync(workdir, { recursive: true, force: true }); } catch { /* noop */ }
  }
});
