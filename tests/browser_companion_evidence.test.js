// Gated synthetic browser evidence for the X companion origin and attach path.
//
// FRAMENEST_RUN_BROWSER_EVIDENCE=1. System Chrome / FRAMENEST_CHROME_BIN, loopback
// only, disposable profile, no Playwright, no saved user profile, no signed-in X.
//
// Chrome 137+ branded builds ignore --load-extension. Origin evidence uses
// --remote-debugging-pipe plus CDP Extensions.loadUnpacked.

const assert = require("node:assert/strict");
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const net = require("node:net");
const test = require("node:test");

const REPO_ROOT = path.resolve(__dirname, "..");
const CHROME = process.env.FRAMENEST_CHROME_BIN || "google-chrome-stable";
const gated = process.env.FRAMENEST_RUN_BROWSER_EVIDENCE !== "1";
const EXPECTED_EXTENSION_ID = "omiihmnlkmieaafaphohakcgmbggppap";

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

function startProbe(port, fixtureHtml) {
  const origins = [];
  const probes = [];
  const server = http.createServer((request, response) => {
    origins.push(request.headers.origin || "");
    if (request.url === "/fixture") {
      response.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      response.end(fixtureHtml);
      return;
    }
    probes.push({
      method: request.method,
      origin: request.headers.origin || "",
      kronika: request.headers["x-kronika-request"] || "",
    });
    response.writeHead(200, { "content-type": "application/json", "cache-control": "no-store" });
    response.end(JSON.stringify({ ok: true }));
  });
  return new Promise((resolve) => {
    server.listen(port, "127.0.0.1", () => resolve({ server, origins, probes }));
  });
}

function copyEvidenceExtension(workdir, probePort) {
  const source = path.join(REPO_ROOT, "extension");
  const dest = path.join(workdir, "extension");
  fs.cpSync(source, dest, { recursive: true });
  const manifestPath = path.join(dest, "manifest.json");
  const manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
  // Match patterns cannot wildcard the port; grant only this disposable probe.
  manifest.host_permissions = [`http://127.0.0.1:${probePort}/*`];
  fs.writeFileSync(manifestPath, JSON.stringify(manifest, null, 2));
  return dest;
}

function createPipeCdp(writeStream, readStream) {
  const pending = new Map();
  let nextId = 1;
  let buffer = Buffer.alloc(0);
  readStream.on("data", (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    let idx = buffer.indexOf(0);
    while (idx !== -1) {
      const raw = buffer.subarray(0, idx).toString("utf8");
      buffer = buffer.subarray(idx + 1);
      idx = buffer.indexOf(0);
      if (!raw) {
        continue;
      }
      let message;
      try {
        message = JSON.parse(raw);
      } catch {
        continue;
      }
      if (message.id && pending.has(message.id)) {
        const { resolve, reject } = pending.get(message.id);
        pending.delete(message.id);
        if (message.error) {
          reject(new Error(message.error.message || JSON.stringify(message.error)));
        } else {
          resolve(message.result || {});
        }
      }
    }
  });
  return {
    send(method, params = {}, sessionId) {
      return new Promise((resolve, reject) => {
        const id = nextId++;
        pending.set(id, { resolve, reject });
        const payload = { id, method, params };
        if (sessionId) {
          payload.sessionId = sessionId;
        }
        writeStream.write(`${JSON.stringify(payload)}\0`);
      });
    },
  };
}

function launchChrome(profile) {
  const proc = spawn(CHROME, [
    "--headless=new",
    "--no-sandbox",
    "--disable-gpu",
    "--disable-dev-shm-usage",
    "--no-first-run",
    "--no-default-browser-check",
    "--disable-component-extensions-with-background-pages",
    "--enable-unsafe-extension-debugging",
    "--remote-debugging-pipe",
    `--user-data-dir=${profile}`,
    "about:blank",
  ], { stdio: ["ignore", "ignore", "pipe", "pipe", "pipe"] });
  let stderr = "";
  proc.stderr.on("data", (chunk) => {
    stderr += chunk;
  });
  const writeStream = proc.stdio[3];
  const readStream = proc.stdio[4];
  if (!writeStream || !readStream) {
    throw new Error("chrome did not expose remote-debugging-pipe fds 3/4");
  }
  const cdp = createPipeCdp(writeStream, readStream);
  return { proc, cdp, stderr: () => stderr };
}

function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      setTimeout(() => reject(new Error(label)), ms);
    }),
  ]);
}

async function waitForCdp(cdp, stderr) {
  const deadline = Date.now() + 20000;
  let lastError = "not attempted";
  while (Date.now() < deadline) {
    try {
      await withTimeout(cdp.send("Browser.getVersion"), 500, "cdp handshake timeout");
      return;
    } catch (error) {
      lastError = error.message;
      await new Promise((resolve) => setTimeout(resolve, 100));
    }
  }
  throw new Error(`chrome pipe CDP not ready: ${lastError}; ${stderr()}`);
}

async function attachTarget(cdp, targetId) {
  const attached = await cdp.send("Target.attachToTarget", { targetId, flatten: true });
  const sessionId = attached.sessionId;
  await cdp.send("Page.enable", {}, sessionId);
  await cdp.send("Runtime.enable", {}, sessionId);
  return sessionId;
}

async function evaluate(cdp, sessionId, expression) {
  const result = await cdp.send("Runtime.evaluate", {
    expression,
    returnByValue: true,
    awaitPromise: true,
  }, sessionId);
  if (result.exceptionDetails) {
    throw new Error(`evaluate exception: ${JSON.stringify(result.exceptionDetails)}`);
  }
  return result.result ? result.result.value : undefined;
}

test("synthetic browser evidence: extension Origin and composer DataTransfer", {
  skip: gated,
  timeout: 120000,
}, async () => {
  const workdir = fs.mkdtempSync(path.join(os.tmpdir(), "framenest-companion-"));
  let probe;
  let chrome;
  try {
    const probePort = await freePort();
    const fixture = fs.readFileSync(
      path.join(REPO_ROOT, "tests/support/x_fixtures/composer.html"),
      "utf8"
    );
    probe = await startProbe(probePort, fixture);
    const extensionDir = copyEvidenceExtension(workdir, probePort);
    const profile = path.join(workdir, "chrome-profile");
    chrome = launchChrome(profile);
    await waitForCdp(chrome.cdp, chrome.stderr);

    const loaded = await chrome.cdp.send("Extensions.loadUnpacked", { path: extensionDir });
    assert.equal(loaded.id, EXPECTED_EXTENSION_ID);

    const fixtureTarget = await chrome.cdp.send("Target.createTarget", {
      url: `http://127.0.0.1:${probePort}/fixture`,
    });
    const fixtureSession = await attachTarget(chrome.cdp, fixtureTarget.targetId);
    await new Promise((resolve) => setTimeout(resolve, 400));

    const attach = await evaluate(chrome.cdp, fixtureSession, `(() => {
      const input = document.querySelector("[data-kronika-composer-file]");
      const post = document.querySelector("#must-not-click");
      let submitted = false;
      post.addEventListener("click", () => { submitted = true; });
      document.querySelector("form").addEventListener("submit", (event) => {
        event.preventDefault();
        submitted = true;
      });
      const file = new File([new Uint8Array([1, 2, 3, 4])], "meme.jpg", { type: "image/jpeg" });
      const transfer = new DataTransfer();
      transfer.items.add(file);
      input.files = transfer.files;
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return {
        files: input.files.length,
        name: input.files[0] && input.files[0].name,
        submitted,
      };
    })()`);
    assert.equal(attach.files, 1);
    assert.equal(attach.name, "meme.jpg");
    assert.equal(attach.submitted, false);

    const expectedOrigin = `chrome-extension://${EXPECTED_EXTENSION_ID}`;
    const pickerTarget = await chrome.cdp.send("Target.createTarget", {
      url: `${expectedOrigin}/ui/picker.html`,
    });
    const pickerSession = await attachTarget(chrome.cdp, pickerTarget.targetId);
    await new Promise((resolve) => setTimeout(resolve, 400));
    const href = await evaluate(chrome.cdp, pickerSession, "location.href");
    assert.ok(
      String(href).startsWith(expectedOrigin),
      `picker page not loaded: ${href}`
    );
    const fetched = await evaluate(
      chrome.cdp,
      pickerSession,
      `fetch("http://127.0.0.1:${probePort}/probe", {
        method: "POST",
        headers: {
          "content-type": "application/json",
          "x-kronika-request": "1"
        },
        body: "{}"
      }).then((r) => r.ok).catch((e) => String(e))`
    );
    assert.equal(fetched, true, `extension page fetch failed: ${fetched}`);
    const originProbe = probe.probes.find((row) => row.method === "POST" && row.kronika === "1");
    assert.ok(originProbe, `companion POST probe missing: ${JSON.stringify(probe.probes)}`);
    assert.equal(originProbe.origin, expectedOrigin);
  } finally {
    if (chrome && chrome.proc) {
      chrome.proc.kill("SIGKILL");
    }
    if (probe && probe.server) {
      probe.server.close();
    }
    fs.rmSync(workdir, { recursive: true, force: true });
  }
});
