/* global importScripts, chrome, FrameNestCompanion */
importScripts(chrome.runtime.getURL("shared/messages.js"));

const companion = self.FrameNestCompanion;
let boundTabId = null;
// The mutation gate is the one server contract this extension depends on. Both
// spellings are sent on every request so that the extension keeps working
// against a server that predates the dual-read cut as well as one that has it:
// a server compares only the spelling it knows and ignores the extra header.
const MUTATION_REQUEST_HEADERS = Object.freeze({
  "X-FrameNest-Request": "1",
  "X-Kronika-Request": "1",
});
const STORAGE_KEYS = Object.freeze({
  origin: companion.STORAGE.origin.name,
  retiredOrigin: companion.STORAGE.origin.retiredName,
  inflight: "inflightClaims",
  acknowledged: "adapterAcknowledged",
  explicitCollapsed: companion.REVIEW_INBOX.explicitCollapsedKey,
  seenRunId: companion.REVIEW_INBOX.seenRunIdKey,
  awaiting: companion.REVIEW_INBOX.awaitingKey,
});
const REVIEW_INBOX_ALARM = companion.REVIEW_INBOX.alarmName;
const RETIRED_REVIEW_INBOX_ALARM = companion.REVIEW_INBOX.retiredAlarmName;
const REVIEW_INBOX_ALARMS = Object.freeze([REVIEW_INBOX_ALARM, RETIRED_REVIEW_INBOX_ALARM]);
const SUCCESSFUL_CLAIM_STATES = Object.freeze({
  completed: true,
  completed_partial: true,
  duplicate_resolved: true,
});

function enableSidePanelOnActionClick() {
  if (chrome.sidePanel && chrome.sidePanel.setPanelBehavior) {
    chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch(() => {});
  }
}

function isBindableComposerSender(sender) {
  if (!sender || !sender.tab || typeof sender.tab.id !== "number") {
    return false;
  }
  if (typeof sender.origin !== "string") {
    return false;
  }
  let parsed;
  try {
    parsed = new URL(sender.origin);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") {
    return false;
  }
  const host = parsed.hostname.toLowerCase();
  return host === "x.com" || host === "www.x.com" || host === "twitter.com" || host === "www.twitter.com";
}

enableSidePanelOnActionClick();
void ensureReviewInboxAlarm();
chrome.runtime.onInstalled.addListener(() => {
  enableSidePanelOnActionClick();
  void ensureReviewInboxAlarm();
});
chrome.runtime.onStartup.addListener(() => {
  void ensureReviewInboxAlarm();
});
if (chrome.alarms && chrome.alarms.onAlarm) {
  chrome.alarms.onAlarm.addListener((alarm) => {
    if (!alarm || REVIEW_INBOX_ALARMS.indexOf(alarm.name) === -1) {
      return;
    }
    void refreshReviewInboxBadge();
  });
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const parsed = companion.dropUnknown(message);
  if (!parsed) {
    return false;
  }
  if (isBindableComposerSender(sender)) {
    boundTabId = sender.tab.id;
  }
  handle(parsed, sender)
    .then(sendResponse)
    .catch((error) => {
      sendResponse({ ok: false, error: "internal_error" });
      void error;
    });
  return true;
});

chrome.runtime.onConnect.addListener((port) => {
  if (!port || port.name !== "framenest-attach") {
    return;
  }
  port.onMessage.addListener((message) => {
    const parsed = companion.dropUnknown(message);
    if (!parsed || parsed.type !== companion.TYPES.ATTACH_BEGIN) {
      return;
    }
    transferAttach(port, parsed.payload || {}).catch(() => {
      try {
        port.postMessage({
          v: companion.PROTOCOL,
          type: companion.TYPES.ERROR,
          payload: { error: "attach_failed" },
        });
      } catch {
        /* worker may already be closing */
      }
    });
  });
});

async function handle(message) {
  switch (message.type) {
    case companion.TYPES.CONFIGURE_ORIGIN:
      return configureOrigin(message.payload || {});
    case companion.TYPES.RESET:
      return resetState();
    case companion.TYPES.IDENTITY:
      return fetchJson("identity");
    case companion.TYPES.SAVE_POST:
      return savePost(message.payload || {});
    case companion.TYPES.CANONICAL_TAGS:
      return fetchJson("canonicalTags");
    case companion.TYPES.POLL_CLAIM:
      return pollClaim(message.payload || {});
    case companion.TYPES.RECOVER_INFLIGHT:
      return recoverInflight();
    case companion.TYPES.PICKER_QUERY:
      return pickerQuery(message.payload || {});
    case companion.TYPES.ATTACH_BEGIN:
      return startAttach(message.payload || {});
    case companion.TYPES.PREVIEW_FETCH:
      return previewFetch(message.payload || {});
    case companion.TYPES.ACK:
      if (message.payload && message.payload.openPicker) {
        await openPicker();
      }
      return { ok: true };
    case companion.TYPES.DISMISS_PICKER:
      return dismissPicker();
    case companion.TYPES.PICKER_LAYOUT:
      return forwardPickerLayout(message.payload || {});
    case companion.TYPES.REVIEW_INBOX:
      return reviewInbox();
    case companion.TYPES.REVIEW_INBOX_DETAIL:
      return reviewInboxDetail(message.payload || {});
    case companion.TYPES.REVIEW_INBOX_OPENED:
      return reviewInboxOpened(message.payload || {});
    case companion.TYPES.REVIEW_INBOX_APPLY:
      return reviewInboxApply(message.payload || {});
    case companion.TYPES.AUTOMATIC_ANALYSIS_CAPABILITY:
      return fetchJson("automaticAnalysisCapability");
    case companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS:
      return putAutomaticAnalysisSettings(message.payload || {});
    default:
      return { ok: false, error: "unknown_type" };
  }
}

async function configureOrigin(payload) {
  const origin = companion.canonicalizeFrameNestOrigin(payload.origin);
  if (!origin) {
    return { ok: false, error: "invalid_origin" };
  }
  const granted = await chrome.permissions.request({ origins: [origin + "/*"] });
  if (!granted) {
    return { ok: false, error: "permission_denied" };
  }
  await chrome.storage.local.set({ [STORAGE_KEYS.origin]: origin });
  await ensureReviewInboxAlarm();
  await refreshReviewInboxBadge();
  return { ok: true, origin };
}

async function resetState() {
  const stored = await chrome.storage.local.get(
    companion.storageKeyRequest(companion.STORAGE.origin)
  );
  const origin = companion.storageValue(stored, companion.STORAGE.origin);
  if (origin) {
    try {
      await chrome.permissions.remove({ origins: [origin + "/*"] });
    } catch {
      /* optional */
    }
  }
  // An explicit reset is the one place a retired spelling is removed, and it has
  // to remove both. STORAGE_KEYS carries the retired origin name, so removing its
  // values covers both spellings; leaving the retired origin behind would let the
  // fallback read re-configure a companion the operator just disconnected.
  await chrome.storage.local.remove(Object.values(STORAGE_KEYS));
  await clearReviewInboxAlarmAndBadge();
  return { ok: true };
}

async function savePost(payload) {
  const accepted = companion.acceptXPostUrl(payload.url);
  if (!accepted) {
    return { ok: false, error: "invalid_url" };
  }
  const alias = sanitizeAlias(payload.alias);
  const response = await fetchJson("xRequests", {
    method: "POST",
    body: {
      url: accepted.submittedUrl,
      alias: alias,
    },
  });
  if (!response.ok) {
    if (response.error === "network_failed") {
      return { ok: false, error: "network_failed", ambiguous: true };
    }
    return response;
  }
  const claimId = response.body.request_id || response.body.claim_id;
  const snapshot = claimSnapshot(response.body, claimId);
  if (companion.isUuid(claimId) && !snapshot.terminal) {
    await persistInflight(claimId, snapshot.postId || accepted.postId);
  }
  if (snapshot.terminal) {
    await persistAwaitingAnalysisFromClaim(response.body, snapshot);
  }
  return snapshot;
}

function sanitizeAlias(raw) {
  if (raw == null || typeof raw !== "object" || Array.isArray(raw)) {
    return {};
  }
  const alias = {};
  const displayTitle = companion.canonicalizeCompanionAliasTitle(raw.display_title);
  if (displayTitle) {
    alias.display_title = displayTitle;
  }
  if (typeof raw.description === "string") {
    alias.description = raw.description.slice(0, 10000);
  }
  if (Array.isArray(raw.tag_keys)) {
    alias.tag_keys = raw.tag_keys
      .filter((key) => typeof key === "string" && /^[a-z][a-z0-9-]{0,63}$/.test(key))
      .slice(0, 32);
  }
  return alias;
}

async function pollClaim(payload) {
  if (!companion.isUuid(payload.claimId)) {
    return { ok: false, error: "invalid_claim" };
  }
  const response = await fetchJson("xRequest", {
    ids: { claimId: payload.claimId },
  });
  if (!response.ok) {
    if (response.error === "network_failed") {
      return { ok: false, error: "network_failed", ambiguous: true };
    }
    return response;
  }
  const snapshot = claimSnapshot(response.body, payload.claimId);
  if (snapshot.terminal) {
    await dropInflight(payload.claimId);
    await persistAwaitingAnalysisFromClaim(response.body, snapshot);
  }
  return snapshot;
}

async function recoverInflight() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.inflight);
  const records = normalizeInflightRecords(stored[STORAGE_KEYS.inflight]);
  return {
    ok: true,
    claimIds: records.map((record) => record.claimId),
    claims: records,
  };
}

async function pickerQuery(payload) {
  const params = new URLSearchParams();
  if (payload.q) {
    params.set("q", String(payload.q).slice(0, 240));
  }
  if (payload.kind) {
    params.set("kind", String(payload.kind));
  }
  if (payload.cursor) {
    params.set("cursor", String(payload.cursor));
  }
  if (Array.isArray(payload.tags)) {
    payload.tags.forEach((tag) => params.append("tag", String(tag)));
  }
  const suffix = params.toString() ? "?" + params.toString() : "";
  const response = await fetchJson("companionMedia", { suffix });
  if (!response.ok) {
    return response;
  }
  if (!companion.acceptCompanionApiVersion(response.body.companion_api_version)) {
    return { ok: false, error: "version_skew", disable: true };
  }
  return { ok: true, page: response.body };
}

async function dismissPicker() {
  if (boundTabId == null) {
    return { ok: false, error: "composer_unbound" };
  }
  try {
    await chrome.tabs.sendMessage(boundTabId, {
      v: companion.PROTOCOL,
      type: companion.TYPES.DISMISS_PICKER,
    });
    return { ok: true };
  } catch {
    return { ok: false, error: "dismiss_failed" };
  }
}

async function forwardPickerLayout(payload) {
  if (boundTabId == null) {
    return { ok: false, error: "composer_unbound" };
  }
  const compact = payload.compact === true;
  try {
    await chrome.tabs.sendMessage(boundTabId, {
      v: companion.PROTOCOL,
      type: companion.TYPES.PICKER_LAYOUT,
      payload: { compact: compact },
    });
    return { ok: true };
  } catch {
    return { ok: false, error: "layout_failed" };
  }
}

async function openPicker() {
  if (chrome.sidePanel && chrome.sidePanel.open) {
    try {
      await chrome.sidePanel.open({ windowId: chrome.windows.WINDOW_ID_CURRENT });
      return;
    } catch {
      /* popup fallback */
    }
  }
}

function isTerminal(body) {
  const state = body && body.state;
  return (
    state === "completed" ||
    state === "completed_partial" ||
    state === "failed" ||
    state === "duplicate_resolved" ||
    state === "catalog_removed"
  );
}

function claimSnapshot(body, claimId) {
  const source = body || {};
  return {
    ok: true,
    claimId: claimId,
    postId: companion.acceptXPostId(source.x_post_id),
    state: source.state,
    phase: source.phase || null,
    failureCode: source.failure_code || null,
    submissionResult: source.submission_result || null,
    successCount: source.success_count == null ? null : source.success_count,
    discoveredAssetCount:
      source.discovered_asset_count == null ? null : source.discovered_asset_count,
    canRetry: Boolean(source.can_retry),
    requestedContentCategory: source.requested_content_category || null,
    terminal: isTerminal(source),
  };
}

function normalizeInflightRecords(raw) {
  const records = [];
  const seen = {};
  (Array.isArray(raw) ? raw : []).forEach((item) => {
    let claimId = null;
    let postId = null;
    if (typeof item === "string" && companion.isUuid(item)) {
      claimId = item;
    } else if (item && typeof item === "object" && companion.isUuid(item.claimId)) {
      claimId = item.claimId;
      postId = companion.acceptXPostId(item.postId);
    }
    if (!claimId || seen[claimId]) {
      return;
    }
    seen[claimId] = true;
    records.push({ claimId: claimId, postId: postId });
  });
  return records;
}

async function persistInflight(claimId, postId) {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.inflight);
  const current = normalizeInflightRecords(stored[STORAGE_KEYS.inflight]);
  const existing = current.find((record) => record.claimId === claimId);
  const acceptedPostId = companion.acceptXPostId(postId);
  if (existing) {
    if (!existing.postId && acceptedPostId) {
      existing.postId = acceptedPostId;
    }
  } else {
    current.push({ claimId: claimId, postId: acceptedPostId });
  }
  await chrome.storage.local.set({ [STORAGE_KEYS.inflight]: current.slice(-16) });
}

async function dropInflight(claimId) {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.inflight);
  const current = normalizeInflightRecords(stored[STORAGE_KEYS.inflight]).filter(
    (record) => record.claimId !== claimId
  );
  await chrome.storage.local.set({ [STORAGE_KEYS.inflight]: current });
}

async function configuredOrigin() {
  const stored = await chrome.storage.local.get(
    companion.storageKeyRequest(companion.STORAGE.origin)
  );
  const origin = companion.storageValue(stored, companion.STORAGE.origin);
  if (!companion.acceptFrameNestOrigin(origin)) {
    return null;
  }
  return origin;
}

function isSuccessfulClaim(snapshot) {
  if (!snapshot || snapshot.ok !== true) {
    return false;
  }
  if (SUCCESSFUL_CLAIM_STATES[snapshot.state]) {
    return true;
  }
  return snapshot.submissionResult === "reuse";
}

async function ensureReviewInboxAlarm() {
  const origin = await configuredOrigin();
  if (!origin || !chrome.alarms || typeof chrome.alarms.create !== "function") {
    await clearReviewInboxAlarmAndBadge();
    return false;
  }
  try {
    await Promise.resolve(
      chrome.alarms.create(REVIEW_INBOX_ALARM, { periodInMinutes: 1 })
    );
  } catch {
    return false;
  }
  return true;
}

async function clearBadgeText() {
  if (!chrome.action || typeof chrome.action.setBadgeText !== "function") {
    return;
  }
  try {
    await Promise.resolve(chrome.action.setBadgeText({ text: "" }));
  } catch {
    /* badge API may be unavailable in tests */
  }
}

async function clearReviewInboxAlarmAndBadge() {
  if (chrome.alarms && typeof chrome.alarms.clear === "function") {
    for (const name of REVIEW_INBOX_ALARMS) {
      try {
        await Promise.resolve(chrome.alarms.clear(name));
      } catch {
        /* alarm may already be absent */
      }
    }
  }
  await clearBadgeText();
}

async function persistAwaitingAnalysisFromClaim(body, snapshot) {
  if (!isSuccessfulClaim(snapshot)) {
    return;
  }
  const ids = companion.catalogedMediaIdsFromAssets(body && body.assets);
  if (!ids.length) {
    return;
  }
  const now = Date.now();
  const expires = now + companion.REVIEW_INBOX.awaitingMs;
  const stored = await chrome.storage.local.get(STORAGE_KEYS.awaiting);
  const current = companion.normalizeAwaitingRecords(stored[STORAGE_KEYS.awaiting], now);
  ids.forEach((mediaId) => {
    const existing = current.find((record) => record.media_id === mediaId);
    if (existing) {
      existing.expires_at_ms = expires;
    } else {
      current.push({ media_id: mediaId, expires_at_ms: expires });
    }
  });
  await chrome.storage.local.set({
    [STORAGE_KEYS.awaiting]: current.slice(-companion.REVIEW_INBOX.awaitingCap),
  });
}

async function readAwaitingRecords() {
  const stored = await chrome.storage.local.get(STORAGE_KEYS.awaiting);
  return companion.normalizeAwaitingRecords(stored[STORAGE_KEYS.awaiting], Date.now());
}

async function storeAwaitingRecords(records) {
  await chrome.storage.local.set({ [STORAGE_KEYS.awaiting]: records });
}

function inboxForbidden(response) {
  return Boolean(response && (response.status === 403 || response.error === "http_403"));
}

async function applyBadgeFromResponse(response) {
  if (!response || !response.ok) {
    await clearBadgeText();
    return;
  }
  const text = companion.badgeTextForUnopenedCount(
    companion.unopenedCountFromBody(response.body)
  );
  if (!chrome.action || typeof chrome.action.setBadgeText !== "function") {
    return;
  }
  try {
    await Promise.resolve(chrome.action.setBadgeText({ text: text }));
  } catch {
    /* badge API may be unavailable in tests */
  }
}

async function refreshReviewInboxBadge() {
  const route = await historyListRoute();
  if (!route.ok) {
    await clearBadgeText();
    return { ok: false, error: route.error || "identity_unavailable" };
  }
  const response = await fetchJson(route.pathName, {
    path: route.path,
    suffix: companion.reviewInboxQuerySuffix(companion.REVIEW_INBOX.badgeLimit),
  });
  await applyBadgeFromResponse(response);
  return response;
}

function mediaIdFromPayload(payload) {
  const mediaId = payload && (payload.mediaId || payload.media_id);
  return companion.isUuid(mediaId) ? mediaId : "";
}

function wrapReviewClient(response) {
  const status = (response && typeof response.status === "number" && Number.isFinite(response.status)
    ? response.status
    : 0);
  const forbidden = Boolean(
    response && (response.status === 403 || response.error === "http_403")
  );
  if (!response || !response.ok) {
    return {
      ok: false,
      error: (response && response.error) || "request_failed",
      status: status,
      body: {},
      forbidden: forbidden,
    };
  }
  return {
    ok: true,
    error: null,
    status: status || 200,
    body: response.body || {},
    forbidden: false,
  };
}

async function reviewInboxDetail(payload) {
  const mediaId = mediaIdFromPayload(payload);
  if (!mediaId) {
    return { ok: false, error: "invalid_media", status: 0, body: {}, forbidden: false };
  }
  return wrapReviewClient(await fetchJson("reviewInboxDetail", { ids: { mediaId: mediaId } }));
}

async function reviewInboxOpened(payload) {
  const mediaId = mediaIdFromPayload(payload);
  const analysisRunId = payload && payload.analysis_run_id;
  if (!mediaId || !companion.isUuid(analysisRunId)) {
    return { ok: false, error: "invalid_opened", status: 0, body: {}, forbidden: false };
  }
  return wrapReviewClient(
    await fetchJson("reviewInboxOpened", {
      ids: { mediaId: mediaId },
      method: "POST",
      body: { analysis_run_id: analysisRunId },
    })
  );
}

async function reviewInboxApply(payload) {
  const mediaId = mediaIdFromPayload(payload);
  const body = companion.sanitizeReviewApplyBody(payload);
  if (!mediaId || !body) {
    return { ok: false, error: "invalid_apply", status: 0, body: {}, forbidden: false };
  }
  return wrapReviewClient(
    await fetchJson("reviewInboxApply", {
      ids: { mediaId: mediaId },
      method: "POST",
      body: body,
    })
  );
}

function reviewInboxPageSuffix(cursor) {
  const query = new URLSearchParams();
  query.set("limit", String(companion.REVIEW_INBOX.maxLimit));
  if (typeof cursor === "string" && cursor) {
    query.set("cursor", cursor);
  }
  return "?" + query.toString();
}

async function failedReviewInbox(response, error) {
  await clearBadgeText();
  return {
    ok: false,
    error: error || (response && response.error) || "request_failed",
    status: (response && response.status) || 0,
    forbidden: inboxForbidden(response) || Boolean(response && response.forbidden),
    items: [],
    unopened_count: 0,
    awaiting: [],
    history_source: "",
  };
}

function capabilitiesFromBody(body) {
  const caps = body && Array.isArray(body.capabilities) ? body.capabilities : [];
  return {
    workflowRead: caps.indexOf("media.workflow.read") !== -1,
    xRequest: caps.indexOf("x.request") !== -1,
    providerOperate: caps.indexOf("provider.operate") !== -1,
  };
}

async function putAutomaticAnalysisSettings(payload) {
  if (!payload || typeof payload !== "object") {
    return { ok: false, error: "invalid_settings", status: 0 };
  }
  if (payload.automatic_media_analysis_enabled === true) {
    if (payload.confirm_cloud_upload !== true) {
      return { ok: false, error: "confirm_required", status: 0 };
    }
    return fetchJson("automaticAnalysisSettings", {
      method: "PUT",
      body: {
        automatic_media_analysis_enabled: true,
        confirm_cloud_upload: true,
      },
    });
  }
  if (payload.automatic_media_analysis_enabled === false) {
    return fetchJson("automaticAnalysisSettings", {
      method: "PUT",
      body: { automatic_media_analysis_enabled: false },
    });
  }
  return { ok: false, error: "invalid_settings", status: 0 };
}

async function historyListRoute() {
  const identity = await fetchJson("identity");
  if (!identity || !identity.ok) {
    return {
      ok: false,
      error: (identity && identity.error) || "identity_unavailable",
      forbidden: Boolean(identity && (identity.status === 403 || identity.error === "http_403")),
    };
  }
  const caps = capabilitiesFromBody(identity.body);
  if (caps.workflowRead) {
    return { ok: true, pathName: "reviewInbox", history_source: "review-inbox" };
  }
  if (caps.xRequest) {
    return {
      ok: true,
      pathName: "ownHistory",
      path: "/api/companion/own-history",
      history_source: "own-history",
    };
  }
  return { ok: false, error: "http_403", forbidden: true };
}

async function reviewInbox() {
  const route = await historyListRoute();
  if (!route.ok) {
    return failedReviewInbox(route, route.error);
  }
  const items = [];
  const itemIndexes = {};
  const seenCursors = {};
  let cursor = "";
  let unopenedCount = 0;
  let firstPage = true;
  while (true) {
    const response = await fetchJson(route.pathName, {
      path: route.path,
      suffix: reviewInboxPageSuffix(cursor),
    });
    if (!response.ok) {
      return failedReviewInbox(response);
    }
    if (firstPage) {
      unopenedCount = companion.unopenedCountFromBody(response.body);
      firstPage = false;
    }
    companion
      .sanitizeReviewInboxItems(response.body && response.body.items)
      .forEach((item) => {
        const existingIndex = itemIndexes[item.media_id];
        if (typeof existingIndex === "number") {
          if (items[existingIndex].analyzed !== true && item.analyzed === true) {
            items[existingIndex] = item;
          }
          return;
        }
        itemIndexes[item.media_id] = items.length;
        items.push(item);
      });
    const nextCursor = response.body && response.body.next_cursor;
    if (typeof nextCursor !== "string" || !nextCursor) {
      break;
    }
    if (seenCursors[nextCursor]) {
      return failedReviewInbox(null, "cursor_cycle");
    }
    seenCursors[nextCursor] = true;
    cursor = nextCursor;
  }
  await applyBadgeFromResponse({ ok: true, body: { unopened_count: unopenedCount } });
  const inboxIds = items.map((item) => item.media_id);
  const awaiting = companion.pruneAwaitingRecords(
    await readAwaitingRecords(),
    inboxIds,
    Date.now()
  );
  await storeAwaitingRecords(awaiting);
  return {
    ok: true,
    forbidden: false,
    items: items,
    unopened_count: unopenedCount,
    awaiting: awaiting,
    history_source: route.history_source,
  };
}

const MAX_PREVIEW_BYTES = 2 * 1024 * 1024;

async function previewFetch(payload) {
  const origin = await configuredOrigin();
  const path = companion.pathFor("preview", {
    mediaId: payload.mediaId,
    locationId: payload.locationId,
  });
  if (!origin || !path) {
    return { ok: false, error: "invalid_preview" };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), companion.FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(origin + path, {
      method: "GET",
      headers: Object.assign({}, MUTATION_REQUEST_HEADERS),
      signal: controller.signal,
    });
    if (!response.ok) {
      return { ok: false, error: "http_" + response.status };
    }
    const buffer = await response.arrayBuffer();
    if (buffer.byteLength <= 0 || buffer.byteLength > MAX_PREVIEW_BYTES) {
      return { ok: false, error: "too_large_or_invalid" };
    }
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i += 1) {
      binary += String.fromCharCode(bytes[i]);
    }
    return {
      ok: true,
      mediaType: response.headers.get("content-type") || "image/jpeg",
      base64: btoa(binary),
    };
  } catch {
    return { ok: false, error: "network_failed" };
  } finally {
    clearTimeout(timer);
  }
}

async function fetchJson(pathName, options) {
  const origin = await configuredOrigin();
  if (!origin) {
    return { ok: false, error: "not_configured" };
  }
  const override = options && options.path;
  const path =
    typeof override === "string" && override
      ? override
      : companion.pathFor(pathName, options && options.ids);
  if (!path) {
    return { ok: false, error: "invalid_path" };
  }
  const method = (options && options.method) || "GET";
  const suffix = (options && options.suffix) || "";
  const headers = Object.assign({}, MUTATION_REQUEST_HEADERS);
  const init = { method, headers };
  if (options && options.body) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(options.body);
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), companion.FETCH_TIMEOUT_MS);
  init.signal = controller.signal;
  try {
    const response = await fetch(origin + path + suffix, init);
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      const code = body && body.error && body.error.code;
      return { ok: false, error: code || "http_" + response.status, status: response.status };
    }
    return { ok: true, status: response.status, body };
  } catch {
    return { ok: false, error: "network_failed" };
  } finally {
    clearTimeout(timer);
  }
}

function waitForPortAttachOutcome(port) {
  let settled = false;
  let timer = null;
  let finish = function finishAttachOutcome() {};
  const promise = new Promise((resolve) => {
    finish = function finishAttachOutcome(result) {
      if (settled) {
        return;
      }
      settled = true;
      if (timer) {
        clearTimeout(timer);
        timer = null;
      }
      try {
        port.onMessage.removeListener(onMessage);
      } catch {
        /* port may already be gone */
      }
      try {
        port.disconnect();
      } catch {
        /* already disconnected */
      }
      resolve(result);
    };
  });
  function onMessage(message) {
    const parsed = companion.dropUnknown(message);
    if (!parsed) {
      return;
    }
    if (parsed.type === companion.TYPES.ACK && parsed.payload && parsed.payload.attached === true) {
      finish({ ok: true });
      return;
    }
    if (parsed.type === companion.TYPES.ERROR) {
      finish({
        ok: false,
        error: (parsed.payload && parsed.payload.error) || "attach_failed",
      });
    }
  }
  port.onMessage.addListener(onMessage);
  if (port.onDisconnect && typeof port.onDisconnect.addListener === "function") {
    port.onDisconnect.addListener(() => {
      finish({ ok: false, error: "attach_disconnected" });
    });
  }
  return {
    promise,
    armAckTimeout() {
      if (settled || timer) {
        return;
      }
      timer = setTimeout(() => {
        finish({ ok: false, error: "attach_timeout" });
      }, 15000);
    },
    fail(error) {
      finish({ ok: false, error: error || "attach_failed" });
      return promise;
    },
  };
}

async function startAttach(payload) {
  if (boundTabId == null) {
    return { ok: false, error: "composer_unbound" };
  }
  const port = chrome.tabs.connect(boundTabId, { name: "framenest-attach" });
  const outcome = waitForPortAttachOutcome(port);
  let transferResult;
  try {
    transferResult = await transferAttach(port, payload);
  } catch {
    return outcome.fail("attach_failed");
  }
  if (!transferResult || !transferResult.ok) {
    return outcome.fail((transferResult && transferResult.error) || "attach_failed");
  }
  outcome.armAckTimeout();
  return outcome.promise;
}

async function transferAttach(port, payload) {
  const origin = await configuredOrigin();
  const path = companion.pathFor("content", {
    mediaId: payload.mediaId,
    locationId: payload.locationId,
  });
  if (!origin || !path) {
    port.postMessage({
      v: companion.PROTOCOL,
      type: companion.TYPES.ERROR,
      payload: { error: "invalid_attach" },
    });
    return { ok: false, error: "invalid_attach" };
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), companion.FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(origin + path, {
      method: "GET",
      headers: Object.assign({}, MUTATION_REQUEST_HEADERS),
      signal: controller.signal,
    });
    const lengthHeader = response.headers.get("content-length");
    const length = lengthHeader ? Number(lengthHeader) : NaN;
    if (!response.ok || !Number.isFinite(length) || length <= 0 || length > companion.MAX_ATTACH_BYTES) {
      if (length > companion.MAX_ATTACH_BYTES) {
        await fallbackDownload(origin + path, payload.filename);
      }
      port.postMessage({
        v: companion.PROTOCOL,
        type: companion.TYPES.ERROR,
        payload: { error: "too_large_or_invalid" },
      });
      return { ok: false, error: "too_large_or_invalid" };
    }
    port.postMessage({
      v: companion.PROTOCOL,
      type: companion.TYPES.ATTACH_BEGIN,
      payload: {
        phase: "meta",
        mediaType: response.headers.get("content-type") || payload.mediaType,
        filename: payload.filename || "kronika-media.bin",
      },
    });
    const reader = response.body.getReader();
    let seen = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done) {
        break;
      }
      seen += value.byteLength;
      if (seen > companion.MAX_ATTACH_BYTES) {
        reader.cancel();
        port.postMessage({
          v: companion.PROTOCOL,
          type: companion.TYPES.ERROR,
          payload: { error: "too_large" },
        });
        return { ok: false, error: "too_large" };
      }
      let offset = 0;
      while (offset < value.byteLength) {
        const slice = value.subarray(offset, offset + companion.CHUNK_BYTES);
        offset += slice.byteLength;
        let binary = "";
        for (let i = 0; i < slice.byteLength; i += 1) {
          binary += String.fromCharCode(slice[i]);
        }
        port.postMessage({
          v: companion.PROTOCOL,
          type: companion.TYPES.ATTACH_BEGIN,
          payload: { chunk: btoa(binary) },
        });
      }
    }
    port.postMessage({
      v: companion.PROTOCOL,
      type: companion.TYPES.ATTACH_BEGIN,
      payload: { phase: "end" },
    });
    return { ok: true };
  } catch {
    try {
      port.postMessage({
        v: companion.PROTOCOL,
        type: companion.TYPES.ERROR,
        payload: { error: "attach_failed" },
      });
    } catch {
      /* worker may already be closing */
    }
    return { ok: false, error: "attach_failed" };
  } finally {
    clearTimeout(timer);
  }
}

async function fallbackDownload(url, filename) {
  const optional = await chrome.permissions.contains({ permissions: ["downloads"] });
  if (!optional) {
    const granted = await chrome.permissions.request({ permissions: ["downloads"] });
    if (!granted) {
      return;
    }
  }
  await chrome.downloads.download({ url, filename: filename || "kronika-media.bin", saveAs: true });
}
