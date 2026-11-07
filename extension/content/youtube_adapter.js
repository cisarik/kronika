(function () {
  // Adds one "Save to Kronika" button to the YouTube player. The button sits
  // in the top-left corner of the player, away from the bottom control bar
  // and the top-right info cards, and follows the player's own auto-hide.
  const companion = globalThis.KronikaCompanion;
  if (!companion || typeof companion.acceptYouTubeVideoUrl !== "function") {
    return;
  }

  const SAVE_NAME = "Save to Kronika";
  const ACCENT = "#00ff41";
  const DANGER = "#ff4d4d";
  const POLL_DELAY_MS = 4000;
  const POLL_BUDGET_MS = 30 * 60 * 1000;
  const STYLE_ID = "kronika-youtube-style";
  const HOST_ATTR = "data-kronika-youtube";
  const STYLE = [
    "[" + HOST_ATTR + "] {",
    "  position: absolute;",
    "  top: 14px;",
    "  left: 14px;",
    "  z-index: 70;",
    "  display: inline-flex;",
    "  align-items: center;",
    "  gap: 8px;",
    "  pointer-events: none;",
    "  font: 600 13px/1.2 ui-monospace, 'SF Mono', Menlo, Consolas, monospace;",
    "  opacity: 0;",
    "  transform: translateY(-4px);",
    "  transition: opacity 160ms ease, transform 160ms ease;",
    "}",
    ".html5-video-player:hover [" + HOST_ATTR + "],",
    ".html5-video-player:not(.ytp-autohide) [" + HOST_ATTR + "],",
    "[" + HOST_ATTR + "][data-state='busy'],",
    "[" + HOST_ATTR + "][data-state='done'],",
    "[" + HOST_ATTR + "][data-state='failed'],",
    "[" + HOST_ATTR + "]:focus-within {",
    "  opacity: 1;",
    "  transform: none;",
    "}",
    ".html5-video-player.ytp-autohide:not(:hover) [" + HOST_ATTR + "][data-state='done'] {",
    "  opacity: 0;",
    "}",
    "[" + HOST_ATTR + "] button {",
    "  pointer-events: auto;",
    "  display: inline-flex;",
    "  align-items: center;",
    "  justify-content: center;",
    "  width: 34px;",
    "  height: 34px;",
    "  margin: 0;",
    "  padding: 0;",
    "  border: 2px solid rgba(0, 255, 65, 0.75);",
    "  border-radius: 999px;",
    "  background: rgba(6, 12, 8, 0.72);",
    "  color: " + ACCENT + ";",
    "  cursor: pointer;",
    "  box-shadow: 0 3px 14px rgba(0, 0, 0, 0.55);",
    "  backdrop-filter: blur(6px);",
    "  transition: background 150ms ease, color 150ms ease, transform 150ms ease;",
    "}",
    "[" + HOST_ATTR + "] button:hover,",
    "[" + HOST_ATTR + "] button:focus-visible {",
    "  background: " + ACCENT + ";",
    "  color: #04130a;",
    "  outline: none;",
    "  transform: scale(1.06);",
    "}",
    "[" + HOST_ATTR + "][data-state='failed'] button {",
    "  border-color: " + DANGER + ";",
    "  color: " + DANGER + ";",
    "}",
    "[" + HOST_ATTR + "] svg {",
    "  width: 18px;",
    "  height: 18px;",
    "  pointer-events: none;",
    "}",
    "[" + HOST_ATTR + "][data-state='busy'] svg {",
    "  animation: kronika-yt-spin 1s linear infinite;",
    "}",
    "@keyframes kronika-yt-spin { to { transform: rotate(360deg); } }",
    "[" + HOST_ATTR + "] .kronika-yt-label {",
    "  max-width: 260px;",
    "  padding: 6px 10px;",
    "  border: 1px solid rgba(0, 255, 65, 0.45);",
    "  border-radius: 999px;",
    "  background: rgba(6, 12, 8, 0.86);",
    "  color: #e8f0e8;",
    "  white-space: nowrap;",
    "  overflow: hidden;",
    "  text-overflow: ellipsis;",
    "}",
    "[" + HOST_ATTR + "] .kronika-yt-label:empty { display: none; }",
    "[" + HOST_ATTR + "][data-state='failed'] .kronika-yt-label {",
    "  border-color: rgba(255, 77, 77, 0.6);",
    "}",
  ].join("\n");

  const ICONS = {
    idle: "M12 4.5v10m0 0l-4-4m4 4l4-4M5 19.5h14",
    busy: "M12 4.5a7.5 7.5 0 1 1-5.3 2.2",
    done: "M5.5 12.5l4.2 4.2L18.5 8",
    failed: "M12 7v6m0 3.5v.5",
  };

  const stateByVideo = new Map();
  let stale = false;
  let current = null;

  function svgIcon(kind) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICONS[kind] || ICONS.idle);
    path.setAttribute("fill", "none");
    path.setAttribute("stroke", "currentColor");
    path.setAttribute("stroke-width", "2.2");
    path.setAttribute("stroke-linecap", "round");
    path.setAttribute("stroke-linejoin", "round");
    svg.appendChild(path);
    return svg;
  }

  function ensureStyle() {
    if (document.getElementById(STYLE_ID)) {
      return;
    }
    const style = document.createElement("style");
    style.id = STYLE_ID;
    style.textContent = STYLE;
    (document.head || document.documentElement).appendChild(style);
  }

  function send(type, payload) {
    return new Promise((resolve) => {
      if (stale) {
        resolve({ ok: false, error: "extension_reloaded" });
        return;
      }
      try {
        chrome.runtime.sendMessage({ v: companion.PROTOCOL, type, payload }, (response) => {
          if (chrome.runtime.lastError) {
            resolve({ ok: false, error: "extension_unavailable" });
            return;
          }
          resolve(response || { ok: false, error: "empty_response" });
        });
      } catch (error) {
        if (companion.isExtensionContextInvalidated(chrome.runtime, error)) {
          stale = true;
        }
        resolve({ ok: false, error: stale ? "extension_reloaded" : "extension_unavailable" });
      }
    });
  }

  function errorCopy(result) {
    const error = result && result.error;
    if (error === "not_configured") return "Connect Kronika in the side panel first";
    if (error === "network_failed") return "Kronika is not reachable";
    if (error === "extension_reloaded") return companion.EXTENSION_CONTEXT_RECOVERY_COPY;
    if (error === "invalid_url") return "This page is not a single YouTube video";
    if (error === "MUTATION_ORIGIN_FORBIDDEN") return "Update the Kronika server to enable YouTube saving";
    if (result && (result.status === 401 || result.status === 403)) return "Your identity cannot request downloads";
    if (result && result.status === 429) return "Too many requests — try again later";
    if (result && result.status === 507) return "Kronika storage is full";
    if (result && result.status === 503) return "YouTube downloads are turned off on the server";
    return "Saving failed";
  }

  function phaseCopy(snapshot) {
    if (snapshot.outcome === "done") return "Saved to Kronika ✓";
    if (snapshot.outcome === "failed") return "Kronika could not download this video";
    if (snapshot.phase === "downloading") return "Downloading…";
    if (snapshot.phase === "processing") return "Processing…";
    return "Queued…";
  }

  function render(entry) {
    if (!current || current.videoId !== entry.videoId) {
      return;
    }
    const { host, button, label } = current;
    host.setAttribute("data-state", entry.state);
    while (button.firstChild) {
      button.removeChild(button.firstChild);
    }
    button.appendChild(svgIcon(entry.state));
    const name = entry.state === "idle" ? SAVE_NAME : entry.message || SAVE_NAME;
    button.setAttribute("aria-label", name);
    button.title = name;
    button.disabled = entry.state === "busy";
    label.textContent = entry.state === "idle" ? "" : entry.message || "";
  }

  function update(videoId, patch) {
    const entry = Object.assign(stateByVideo.get(videoId) || { videoId, state: "idle" }, patch);
    stateByVideo.set(videoId, entry);
    render(entry);
    return entry;
  }

  async function poll(videoId, requestId, startedAt) {
    if (Date.now() - startedAt > POLL_BUDGET_MS) {
      update(videoId, { state: "idle", message: "" });
      return;
    }
    const snapshot = await send(companion.TYPES.POLL_YOUTUBE, { requestId });
    if (!snapshot || snapshot.ok !== true) {
      setTimeout(() => void poll(videoId, requestId, startedAt), POLL_DELAY_MS * 2);
      return;
    }
    applySnapshot(videoId, snapshot, startedAt);
  }

  function applySnapshot(videoId, snapshot, startedAt) {
    if (snapshot.outcome === "done") {
      update(videoId, { state: "done", message: phaseCopy(snapshot) });
      return;
    }
    if (snapshot.outcome === "failed") {
      update(videoId, { state: "failed", message: phaseCopy(snapshot) });
      return;
    }
    update(videoId, { state: "busy", message: phaseCopy(snapshot) });
    if (snapshot.requestId) {
      setTimeout(() => void poll(videoId, snapshot.requestId, startedAt), POLL_DELAY_MS);
    }
  }

  async function save(videoId, url) {
    const entry = stateByVideo.get(videoId);
    if (entry && (entry.state === "busy" || entry.state === "done")) {
      return;
    }
    update(videoId, { state: "busy", message: "Sending to Kronika…" });
    const snapshot = await send(companion.TYPES.SAVE_YOUTUBE, { url });
    if (!snapshot || snapshot.ok !== true) {
      update(videoId, { state: "failed", message: errorCopy(snapshot) });
      return;
    }
    applySnapshot(videoId, snapshot, Date.now());
  }

  function playerElement() {
    const players = document.querySelectorAll(".html5-video-player");
    for (const player of players) {
      const rect = player.getBoundingClientRect();
      if (rect.width > 120 && rect.height > 80 && player.offsetParent !== null) {
        return player;
      }
    }
    return null;
  }

  function sync() {
    if (stale) {
      return;
    }
    const accepted = companion.acceptYouTubeVideoUrl(location.href.split("#")[0]);
    const player = accepted ? playerElement() : null;
    if (!accepted || !player) {
      if (current && current.host.isConnected) {
        current.host.remove();
      }
      current = null;
      return;
    }
    if (current && current.player === player && current.host.isConnected) {
      if (current.videoId !== accepted.videoId) {
        current.videoId = accepted.videoId;
        current.url = accepted.canonicalUrl;
        render(stateByVideo.get(accepted.videoId) || { videoId: accepted.videoId, state: "idle" });
      }
      return;
    }
    if (current && current.host.isConnected) {
      current.host.remove();
    }
    ensureStyle();
    const host = document.createElement("div");
    host.setAttribute(HOST_ATTR, "");
    const button = document.createElement("button");
    button.type = "button";
    const label = document.createElement("span");
    label.className = "kronika-yt-label";
    label.setAttribute("role", "status");
    label.setAttribute("aria-live", "polite");
    host.appendChild(button);
    host.appendChild(label);
    for (const type of ["click", "mousedown", "mouseup", "dblclick", "keydown"]) {
      host.addEventListener(type, (event) => {
        // Keep the player from toggling playback or fullscreen underneath.
        event.stopPropagation();
      });
    }
    button.addEventListener("click", (event) => {
      event.preventDefault();
      if (current) {
        void save(current.videoId, current.url);
      }
    });
    player.appendChild(host);
    current = { player, host, button, label, videoId: accepted.videoId, url: accepted.canonicalUrl };
    render(stateByVideo.get(accepted.videoId) || { videoId: accepted.videoId, state: "idle" });
  }

  let scheduled = false;
  function scheduleSync() {
    if (scheduled) {
      return;
    }
    scheduled = true;
    setTimeout(() => {
      scheduled = false;
      sync();
    }, 250);
  }

  document.addEventListener("yt-navigate-finish", scheduleSync);
  window.addEventListener("popstate", scheduleSync);
  new MutationObserver(scheduleSync).observe(document.documentElement, { childList: true, subtree: true });
  scheduleSync();
})();
