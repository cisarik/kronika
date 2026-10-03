(function (root, factory) {
  const api = factory();
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.FrameNestCompanion = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  const PROTOCOL = "kronika.companion.v1";
  // The server keeps emitting the retired spelling until a later cut moves
  // COMPANION_API_VERSION, so both spellings are accepted and only a value that
  // is neither one still counts as version skew.
  const API_VERSION = "kronika-companion.v1";
  const RETIRED_API_VERSION = "framenest-companion.v1";
  const ACCEPTED_API_VERSIONS = Object.freeze([API_VERSION, RETIRED_API_VERSION]);
  const MAX_ATTACH_BYTES = 32 * 1024 * 1024;
  const FETCH_TIMEOUT_MS = 60 * 1000;
  const CHUNK_BYTES = 24 * 1024;
  const EXTENSION_CONTEXT_INVALIDATED_SIGNATURE = "Extension context invalidated";
  const EXTENSION_CONTEXT_RECOVERY_COPY =
    "Kronika was reloaded. Refresh X and reopen the side panel.";
  const UUID_PATTERN =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const POST_ID_PATTERN = /^[0-9]{1,19}$/;
  const HANDLE_PATTERN = /^[A-Za-z0-9_]{1,64}$/;
  const X_HOSTS = new Set(["x.com", "www.x.com", "twitter.com", "www.twitter.com"]);
  const CONTENT_CATEGORIES = Object.freeze(["general", "meme", "movie", "youtube"]);
  const POST_ID_VALUE_PATTERN = /^[0-9]{1,19}$/;
  const TS_ORIGIN_PATTERN = /^https:\/\/[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+\.ts\.net$/;
  const TYPES = Object.freeze({
    SAVE_POST: "save_post",
    POLL_CLAIM: "poll_claim",
    RECOVER_INFLIGHT: "recover_inflight",
    PICKER_QUERY: "picker_query",
    ATTACH_BEGIN: "attach_begin",
    PREVIEW_FETCH: "preview_fetch",
    CONFIGURE_ORIGIN: "configure_origin",
    RESET: "reset",
    IDENTITY: "identity",
    ACK: "ack",
    ERROR: "error",
    CANONICAL_TAGS: "canonical_tags",
    DISMISS_PICKER: "dismiss_picker",
    PICKER_LAYOUT: "picker_layout",
    REVIEW_INBOX: "review_inbox",
    REVIEW_INBOX_DETAIL: "review_inbox_detail",
    REVIEW_INBOX_OPENED: "review_inbox_opened",
    REVIEW_INBOX_APPLY: "review_inbox_apply",
    AUTOMATIC_ANALYSIS_CAPABILITY: "automatic_analysis_capability",
    AUTOMATIC_ANALYSIS_SETTINGS: "automatic_analysis_settings",
  });
  const REVIEW_APPLY_FIELDS = Object.freeze(["display_title", "tags", "description"]);
  const TAG_KEY_PATTERN = /^[a-z][a-z0-9-]{0,63}$/;
  const REVIEW_OVERLAY = Object.freeze({
    protocol: "kronika.companion.review.v1",
    types: Object.freeze({
      CLOSE: "close",
      FORBIDDEN: "forbidden",
      INBOX_REFRESH: "inbox_refresh",
    }),
  });
  const REVIEW_INBOX = Object.freeze({
    alarmName: "kronika.review-inbox",
    retiredAlarmName: "framenest.review-inbox",
    explicitCollapsedKey: "reviewInboxExplicitCollapsed",
    seenRunIdKey: "reviewInboxSeenRunId",
    awaitingKey: "reviewInboxAwaitingAnalysis",
    emptyCopy: "No analyzed items.",
    hintCopy: "Awaiting analysis",
    pollMs: 15000,
    awaitingMs: 30 * 60 * 1000,
    awaitingCap: 16,
    badgeLimit: 1,
    maxLimit: 100,
  });

  // Persisted browser identifiers keep the retired spelling readable until the
  // removal cut. A reader takes the current name when it is present and the
  // retired name only when the current one is absent; a writer writes the
  // current name and never touches the retired one. Nothing here ever deletes a
  // retired entry, so an upgrade cannot lose state that predates it.
  const STORAGE = Object.freeze({
    origin: Object.freeze({
      name: "kronikaOrigin",
      retiredName: "frameNestOrigin",
    }),
  });

  function storageKeyRequest(entry) {
    return entry.retiredName ? [entry.name, entry.retiredName] : [entry.name];
  }

  // `chrome.storage.local.get` omits absent keys, so an absent current name
  // reads as `undefined` here whether the backing store omits the key or returns
  // it with an undefined value.
  function storageValue(stored, entry) {
    if (!stored || typeof stored !== "object") {
      return undefined;
    }
    if (stored[entry.name] !== undefined) {
      return stored[entry.name];
    }
    if (entry.retiredName && stored[entry.retiredName] !== undefined) {
      return stored[entry.retiredName];
    }
    return undefined;
  }

  function storageChangeFor(changes, entry) {
    if (!changes || typeof changes !== "object") {
      return null;
    }
    if (Object.prototype.hasOwnProperty.call(changes, entry.name)) {
      return changes[entry.name];
    }
    if (entry.retiredName && Object.prototype.hasOwnProperty.call(changes, entry.retiredName)) {
      return changes[entry.retiredName];
    }
    return null;
  }

  function acceptCompanionApiVersion(value) {
    return ACCEPTED_API_VERSIONS.indexOf(value) !== -1;
  }

  function isProtocolMessage(value) {
    return Boolean(
      value &&
        typeof value === "object" &&
        value.v === PROTOCOL &&
        typeof value.type === "string" &&
        Object.values(TYPES).includes(value.type)
    );
  }

  function dropUnknown(value) {
    if (!isProtocolMessage(value)) {
      return null;
    }
    return value;
  }

  function isUuid(value) {
    return typeof value === "string" && UUID_PATTERN.test(value);
  }

  function isExtensionContextInvalidated(runtime, error) {
    if (!runtime || !runtime.id) {
      return true;
    }
    const message = error && typeof error.message === "string" ? error.message : "";
    return message.indexOf(EXTENSION_CONTEXT_INVALIDATED_SIGNATURE) !== -1;
  }

  function acceptXPostUrl(url) {
    if (typeof url !== "string" || url.trim() !== url || url.length === 0 || url.length > 2048) {
      return null;
    }
    let parsed;
    try {
      parsed = new URL(url);
    } catch {
      return null;
    }
    if (parsed.protocol !== "https:") {
      return null;
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      return null;
    }
    const host = parsed.hostname.toLowerCase();
    if (!X_HOSTS.has(host)) {
      return null;
    }
    if (parsed.port && parsed.port !== "443") {
      return null;
    }
    const parts = parsed.pathname.split("/").filter(Boolean);
    if (parts.length !== 3 || parts[1] !== "status") {
      return null;
    }
    const handle = parts[0];
    const postId = parts[2];
    if (!HANDLE_PATTERN.test(handle) || handle.startsWith("i") || !POST_ID_PATTERN.test(postId)) {
      return null;
    }
    return {
      postId,
      submittedUrl: url,
      canonicalUrl: "https://x.com/" + handle.replace(/_+$/, "") + "/status/" + postId,
    };
  }

  function canonicalizeFrameNestOrigin(value) {
    if (typeof value !== "string") {
      return null;
    }
    const trimmed = value.trim();
    if (!trimmed) {
      return null;
    }
    const candidate = /^[A-Za-z][A-Za-z0-9+.-]*:/.test(trimmed) ? trimmed : "https://" + trimmed;
    let parsed;
    try {
      parsed = new URL(candidate);
    } catch {
      return null;
    }
    if (parsed.username || parsed.password || parsed.search || parsed.hash) {
      return null;
    }
    if (parsed.pathname !== "" && parsed.pathname !== "/") {
      return null;
    }
    let protocol = parsed.protocol;
    if (protocol === "http:") {
      protocol = "https:";
    }
    if (protocol !== "https:") {
      return null;
    }
    if (parsed.port && parsed.port !== "443") {
      return null;
    }
    const hostname = parsed.hostname;
    if (!hostname) {
      return null;
    }
    const origin = "https://" + hostname;
    if (!TS_ORIGIN_PATTERN.test(origin)) {
      return null;
    }
    return origin;
  }

  function acceptFrameNestOrigin(value) {
    return canonicalizeFrameNestOrigin(value) !== null;
  }

  function canonicalizeCompanionAliasTitle(value) {
    if (typeof value !== "string") {
      return null;
    }
    const chars = Array.from(value.normalize("NFC"));
    let mapped = "";
    for (let index = 0; index < chars.length; index += 1) {
      mapped += /\p{Cc}/u.test(chars[index]) ? " " : chars[index];
    }
    const collapsed = mapped.replace(/\s+/gu, " ").trim();
    if (!collapsed) {
      return null;
    }
    const points = Array.from(collapsed);
    if (points.length <= 240) {
      return collapsed;
    }
    const clipped = points.slice(0, 240).join("").trim();
    return clipped || null;
  }

  function pathFor(name, ids) {
    const safe = ids || {};
    switch (name) {
      case "identity":
        return "/api/identity/me";
      case "companionMedia":
        return "/api/x/companion/media";
      case "xRequests":
        return "/api/x/requests";
      case "xRequest":
        if (!isUuid(safe.claimId)) {
          return null;
        }
        return "/api/x/requests/" + safe.claimId;
      case "xRetry":
        if (!isUuid(safe.claimId)) {
          return null;
        }
        return "/api/x/requests/" + safe.claimId + "/retry";
      case "canonicalTags":
        return "/api/canonical-tags?surface=x-companion-save";
      case "reviewInbox":
        return "/api/companion/review-inbox";
      case "reviewInboxDetail":
        if (!isUuid(safe.mediaId)) {
          return null;
        }
        return "/api/companion/review-inbox/" + safe.mediaId;
      case "reviewInboxOpened":
        if (!isUuid(safe.mediaId)) {
          return null;
        }
        return "/api/companion/review-inbox/" + safe.mediaId + "/opened";
      case "reviewInboxApply":
        if (!isUuid(safe.mediaId)) {
          return null;
        }
        return "/api/companion/review-inbox/" + safe.mediaId + "/apply";
      case "content":
        if (!isUuid(safe.mediaId) || !isUuid(safe.locationId)) {
          return null;
        }
        return (
          "/api/media/" +
          safe.mediaId +
          "/locations/" +
          safe.locationId +
          "/content"
        );
      case "preview":
        if (!isUuid(safe.mediaId) || !isUuid(safe.locationId)) {
          return null;
        }
        return (
          "/api/media/" +
          safe.mediaId +
          "/locations/" +
          safe.locationId +
          "/gallery-preview"
        );
      case "automaticAnalysisCapability":
        return "/api/ai/automatic-analysis-capability";
      case "automaticAnalysisSettings":
        return "/api/admin/settings/automatic-analysis";
      default:
        return null;
    }
  }

  function bytesFromBase64(chunk) {
    const binary = atob(chunk);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i += 1) {
      bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
  }

  function reviewInboxQuerySuffix(limit) {
    if (typeof limit !== "number" || !Number.isInteger(limit) || limit < 1 || limit > REVIEW_INBOX.maxLimit) {
      return "";
    }
    return "?limit=" + String(limit);
  }

  function badgeTextForUnopenedCount(count) {
    if (typeof count !== "number" || !Number.isFinite(count) || count <= 0) {
      return "";
    }
    const n = Math.floor(count);
    if (n > 99) {
      return "99+";
    }
    return String(n);
  }

  function unopenedCountFromBody(body) {
    const value = body && body.unopened_count;
    if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
      return 0;
    }
    return Math.floor(value);
  }

  function catalogedMediaIdsFromAssets(assets) {
    if (!Array.isArray(assets)) {
      return [];
    }
    const ids = [];
    const seen = {};
    assets.forEach((asset) => {
      if (!asset || typeof asset !== "object") {
        return;
      }
      const mediaId = asset.media_id;
      if (!isUuid(mediaId) || seen[mediaId]) {
        return;
      }
      seen[mediaId] = true;
      ids.push(mediaId);
    });
    return ids;
  }

  function normalizeAwaitingRecords(raw, nowMs) {
    const now = typeof nowMs === "number" && Number.isFinite(nowMs) ? nowMs : Date.now();
    const records = [];
    const seen = {};
    (Array.isArray(raw) ? raw : []).forEach((item) => {
      if (!item || typeof item !== "object") {
        return;
      }
      const mediaId = item.media_id;
      const expires = item.expires_at_ms;
      if (!isUuid(mediaId) || seen[mediaId]) {
        return;
      }
      if (typeof expires !== "number" || !Number.isFinite(expires) || expires <= now) {
        return;
      }
      seen[mediaId] = true;
      records.push({ media_id: mediaId, expires_at_ms: expires });
    });
    return records.slice(0, REVIEW_INBOX.awaitingCap);
  }

  function pruneAwaitingRecords(records, inboxMediaIds, nowMs) {
    const present = {};
    (Array.isArray(inboxMediaIds) ? inboxMediaIds : []).forEach((id) => {
      if (isUuid(id)) {
        present[id] = true;
      }
    });
    return normalizeAwaitingRecords(records, nowMs).filter((record) => !present[record.media_id]);
  }

  function sanitizeReviewInboxItems(raw) {
    if (!Array.isArray(raw)) {
      return [];
    }
    const items = [];
    raw.forEach((item) => {
      if (!item || typeof item !== "object") {
        return;
      }
      if (!isUuid(item.media_id)) {
        return;
      }
      if (typeof item.title !== "string" || !item.title.length) {
        return;
      }
      const created = item.created_at_ms;
      const completed = item.completed_at_ms;
      if (
        typeof created !== "number" ||
        !Number.isInteger(created) ||
        created < 0 ||
        typeof item.analyzed !== "boolean" ||
        typeof item.unopened !== "boolean"
      ) {
        return;
      }
      if (item.analyzed) {
        if (
          !isUuid(item.analysis_run_id) ||
          typeof completed !== "number" ||
          !Number.isInteger(completed) ||
          completed < 0
        ) {
          return;
        }
      } else if (
        item.analysis_run_id !== null ||
        completed !== null ||
        item.unopened !== false
      ) {
        return;
      }
      items.push({
        media_id: item.media_id,
        title: item.title,
        created_at_ms: created,
        analyzed: item.analyzed,
        analysis_run_id: item.analysis_run_id,
        completed_at_ms: completed,
        unopened: item.unopened,
      });
    });
    return items;
  }

  function sanitizeReviewApplyFields(raw) {
    if (!Array.isArray(raw)) {
      return [];
    }
    const fields = [];
    const seen = {};
    raw.forEach((field) => {
      if (typeof field !== "string" || REVIEW_APPLY_FIELDS.indexOf(field) === -1 || seen[field]) {
        return;
      }
      seen[field] = true;
      fields.push(field);
    });
    return fields;
  }

  function sanitizeReviewTagKeys(raw) {
    if (!Array.isArray(raw)) {
      return [];
    }
    const keys = [];
    const seen = {};
    raw.forEach((key) => {
      if (typeof key !== "string" || !TAG_KEY_PATTERN.test(key) || seen[key]) {
        return;
      }
      seen[key] = true;
      keys.push(key);
    });
    return keys.slice(0, 5);
  }

  function sanitizeReviewApplyBody(payload) {
    if (!payload || typeof payload !== "object") {
      return null;
    }
    if (!isUuid(payload.analysis_run_id)) {
      return null;
    }
    const fields = sanitizeReviewApplyFields(payload.fields);
    if (!fields.length) {
      return null;
    }
    const tagsSelected = fields.indexOf("tags") !== -1;
    const tagKeys = tagsSelected ? sanitizeReviewTagKeys(payload.tag_keys) : [];
    if (tagsSelected && tagKeys.length < 1) {
      return null;
    }
    return {
      analysis_run_id: payload.analysis_run_id,
      fields: fields,
      tag_keys: tagsSelected ? tagKeys : [],
    };
  }

  function parseReviewMediaHash(hash) {
    const raw = typeof hash === "string" ? hash : "";
    const trimmed = raw.charAt(0) === "#" ? raw.slice(1) : raw;
    let mediaId = "";
    trimmed.split("&").forEach((part) => {
      const eq = part.indexOf("=");
      const key = eq === -1 ? part : part.slice(0, eq);
      const value = eq === -1 ? "" : part.slice(eq + 1);
      if (key === "media") {
        try {
          mediaId = decodeURIComponent(value);
        } catch {
          mediaId = value;
        }
      }
    });
    return isUuid(mediaId) ? mediaId : null;
  }

  function formatReviewRunLabel(run) {
    const completed = run && typeof run.completed_at_ms === "number" ? run.completed_at_ms : 0;
    const model = run && typeof run.model_id === "string" ? run.model_id : "";
    const title = run && typeof run.title === "string" ? run.title : "";
    let when = "";
    try {
      when = new Date(completed).toLocaleString();
    } catch {
      when = String(completed);
    }
    return when + " · " + model + " · " + title;
  }

  function acceptReviewOverlayMessage(event, expectedSource, expectedOrigin) {
    if (!event || event.source !== expectedSource || event.origin !== expectedOrigin) {
      return null;
    }
    const data = event.data;
    if (!data || typeof data !== "object" || data.v !== REVIEW_OVERLAY.protocol) {
      return null;
    }
    const types = REVIEW_OVERLAY.types;
    if (
      data.type !== types.CLOSE &&
      data.type !== types.FORBIDDEN &&
      data.type !== types.INBOX_REFRESH
    ) {
      return null;
    }
    return { type: data.type };
  }

  function hasProviderOperateCapability(body) {
    const caps = body && Array.isArray(body.capabilities) ? body.capabilities : [];
    return caps.indexOf("provider.operate") !== -1;
  }

  function concatChunks(chunks, total) {
    const out = new Uint8Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      out.set(chunk, offset);
      offset += chunk.byteLength;
    }
    return out;
  }

  function acceptContentCategory(value) {
    if (typeof value !== "string") {
      return null;
    }
    return CONTENT_CATEGORIES.indexOf(value) === -1 ? null : value;
  }

  function savePopupDefaultContentCategory() {
    return "general";
  }

  function defaultContentCategoryForMediaKind(kind) {
    void kind;
    return savePopupDefaultContentCategory();
  }

  function acceptXPostId(value) {
    return typeof value === "string" && POST_ID_VALUE_PATTERN.test(value) ? value : null;
  }

  function reduceXSaveOutcome(result) {
    if (!result || result.ok !== true) {
      const error = result && result.error;
      if (
        result &&
        (result.ambiguous === true ||
          error === "network_failed" ||
          error === "extension_unavailable" ||
          error === "empty_response")
      ) {
        return {
          kind: "unknown",
          name: "Save status unknown—check FrameNest",
          busy: false,
          retainInflight: true,
        };
      }
      let name = "Save to FrameNest failed";
      if (error === "X_REQUEST_INVALID_CATEGORY") {
        name = "Save to FrameNest failed—FrameNest needs an update";
      } else if (error === "X_REQUEST_CATEGORY_CONFLICT") {
        name = "Save to FrameNest failed—category already differs";
      }
      return { kind: "failed", name: name, busy: false, retainInflight: false };
    }
    const state = result.state;
    const disposition = result.submissionResult;
    if (disposition === "reuse" || disposition === "duplicate_resolved" || state === "duplicate_resolved") {
      return {
        kind: "done",
        name: "Already saved to FrameNest",
        busy: false,
        retainInflight: false,
      };
    }
    if (state === "completed") {
      return {
        kind: "done",
        name: "Saved to FrameNest",
        busy: false,
        retainInflight: false,
      };
    }
    if (state === "completed_partial") {
      const successCount = result.successCount;
      const discovered = result.discoveredAssetCount;
      let name = "Partially saved to FrameNest";
      if (successCount != null && discovered != null) {
        name = "Partially saved to FrameNest (" + successCount + " of " + discovered + ")";
      }
      return { kind: "partial", name: name, busy: false, retainInflight: false };
    }
    if (state === "failed") {
      return {
        kind: "failed",
        name: "Save to FrameNest failed",
        busy: false,
        retainInflight: false,
      };
    }
    if (state === "catalog_removed") {
      return {
        kind: "failed",
        name: "Saved item is no longer available in FrameNest",
        busy: false,
        retainInflight: false,
      };
    }
    if (result.terminal) {
      return {
        kind: "unknown",
        name: "Save status unknown—check FrameNest",
        busy: false,
        retainInflight: true,
      };
    }
    return {
      kind: "busy",
      name: "Saving to FrameNest…",
      busy: true,
      retainInflight: true,
    };
  }

  return {
    PROTOCOL,
    API_VERSION,
    RETIRED_API_VERSION,
    ACCEPTED_API_VERSIONS,
    acceptCompanionApiVersion,
    MAX_ATTACH_BYTES,
    FETCH_TIMEOUT_MS,
    CHUNK_BYTES,
    EXTENSION_CONTEXT_INVALIDATED_SIGNATURE,
    EXTENSION_CONTEXT_RECOVERY_COPY,
    TYPES,
    CONTENT_CATEGORIES,
    REVIEW_INBOX,
    REVIEW_OVERLAY,
    REVIEW_APPLY_FIELDS,
    STORAGE,
    storageKeyRequest,
    storageValue,
    storageChangeFor,
    isProtocolMessage,
    dropUnknown,
    isUuid,
    isExtensionContextInvalidated,
    acceptXPostUrl,
    acceptXPostId,
    acceptContentCategory,
    savePopupDefaultContentCategory,
    defaultContentCategoryForMediaKind,
    reduceXSaveOutcome,
    acceptFrameNestOrigin,
    canonicalizeFrameNestOrigin,
    canonicalizeCompanionAliasTitle,
    pathFor,
    reviewInboxQuerySuffix,
    badgeTextForUnopenedCount,
    unopenedCountFromBody,
    catalogedMediaIdsFromAssets,
    normalizeAwaitingRecords,
    pruneAwaitingRecords,
    sanitizeReviewInboxItems,
    sanitizeReviewApplyBody,
    parseReviewMediaHash,
    formatReviewRunLabel,
    acceptReviewOverlayMessage,
    hasProviderOperateCapability,
    bytesFromBase64,
    concatChunks,
  };
});
