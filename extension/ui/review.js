(function () {
  const companion = globalThis.KronikaCompanion;
  const FIELD_TITLE = "display_title";
  const FIELD_TAGS = "tags";
  const FIELD_DESCRIPTION = "description";
  const NO_SUCCESSFUL_ANALYSIS = "No successful analysis yet.";
  const RELOAD_RECOVERY = companion.EXTENSION_CONTEXT_RECOVERY_COPY;
  let runtimeStale = false;
  let runtimeStaleHandler = function handleEarlyRuntimeStale() {};

  function emptyFields() {
    return { display_title: false, tags: false, description: false };
  }

  function remainingMappedKeys(tags, removed) {
    const keys = [];
    const dropped = removed || {};
    (Array.isArray(tags) ? tags : []).forEach((tag) => {
      if (!tag || tag.status !== "mapped" || typeof tag.key !== "string") {
        return;
      }
      if (dropped[tag.key]) {
        return;
      }
      keys.push(tag.key);
    });
    return keys;
  }

  function tagsCanBeChecked(mappedCount) {
    return mappedCount > 0;
  }

  function saveEnabled(fields, mappedCount) {
    if (!fields) {
      return false;
    }
    if (fields.display_title || fields.description) {
      return true;
    }
    return fields.tags === true && mappedCount > 0;
  }

  function createReviewController(options) {
    const request = options.request;
    const notifyParent = options.notifyParent;
    const render = options.render || function renderReview() {};
    let mediaId = null;
    let detail = null;
    let runId = "";
    let fields = emptyFields();
    let removed = {};
    let lastError = "";
    let overlayOpen = true;
    let contextStale = false;
    const openedRunIds = {};

    function suggestions() {
      return detail && Array.isArray(detail.suggestions) ? detail.suggestions : [];
    }

    function currentSuggestion() {
      const rows = suggestions();
      const match = rows.filter(function findRun(row) {
        return row && row.analysis_run_id === runId;
      })[0];
      return match || rows[0] || null;
    }

    function mappedCount() {
      const suggestion = currentSuggestion();
      return remainingMappedKeys(suggestion && suggestion.tags, removed).length;
    }

    function resetSelections() {
      fields = emptyFields();
      removed = {};
      lastError = "";
    }

    function snapshot() {
      const count = mappedCount();
      const pending = currentSuggestion() === null;
      if (fields.tags && count < 1) {
        fields.tags = false;
      }
      return {
        mediaId: mediaId,
        runId: runId,
        fields: {
          display_title: fields.display_title,
          tags: fields.tags,
          description: fields.description,
        },
        removed: Object.assign({}, removed),
        overlayOpen: overlayOpen,
        lastError: lastError,
        runtimeStale: contextStale,
        pending: pending,
        mappedCount: count,
        saveEnabled: !contextStale && !pending && saveEnabled(fields, count),
        tagsDisabled: contextStale || pending || !tagsCanBeChecked(count),
        suggestion: currentSuggestion(),
        suggestions: suggestions(),
        canonical: detail && detail.canonical,
        publication: detail && detail.publication,
      };
    }

    function paint() {
      render(snapshot());
    }

    function handleStaleResponse(response) {
      if (!response || response.stale !== true) {
        return false;
      }
      contextStale = true;
      lastError = RELOAD_RECOVERY;
      overlayOpen = true;
      paint();
      return true;
    }

    function handleForbidden() {
      overlayOpen = false;
      lastError = "";
      detail = null;
      runId = "";
      resetSelections();
      notifyParent(companion.REVIEW_OVERLAY.types.FORBIDDEN);
      paint();
    }

    async function postOpened() {
      if (!companion.isUuid(mediaId) || !companion.isUuid(runId)) {
        return { ok: false, error: "invalid_opened" };
      }
      const openedMediaId = mediaId;
      const openedRunId = runId;
      const response = await request(companion.TYPES.REVIEW_INBOX_OPENED, {
        mediaId: openedMediaId,
        analysis_run_id: openedRunId,
      });
      if (handleStaleResponse(response)) {
        return response;
      }
      if (response && (response.forbidden || response.status === 403)) {
        handleForbidden();
        return response;
      }
      if (response && response.ok) {
        openedRunIds[openedRunId] = true;
        notifyParent(companion.REVIEW_OVERLAY.types.INBOX_REFRESH);
      }
      return response;
    }

    async function ensureOpened() {
      if (openedRunIds[runId] === true) {
        return { ok: true, already_opened: true };
      }
      const response = await postOpened();
      if (response && response.stale === true) {
        return response;
      }
      if (response && (response.forbidden || response.status === 403)) {
        return response;
      }
      if (!response || !response.ok) {
        lastError = "This review could not be marked opened.";
        overlayOpen = true;
        paint();
        return response || { ok: false, error: "opened_failed" };
      }
      return response;
    }

    async function loadDetail() {
      const response = await request(companion.TYPES.REVIEW_INBOX_DETAIL, { mediaId: mediaId });
      if (handleStaleResponse(response)) {
        return response;
      }
      if (response && (response.forbidden || response.status === 403)) {
        handleForbidden();
        return response;
      }
      if (!response || !response.ok) {
        lastError = "Could not load review.";
        paint();
        return response;
      }
      detail = response.body || {};
      const rows = suggestions();
      if (!companion.isUuid(runId) && rows[0] && companion.isUuid(rows[0].analysis_run_id)) {
        runId = rows[0].analysis_run_id;
      }
      paint();
      if (!rows.length) {
        return { ok: true, pending: true };
      }
      return postOpened();
    }

    async function open(nextMediaId) {
      if (!companion.isUuid(nextMediaId)) {
        lastError = "Invalid media.";
        paint();
        return { ok: false, error: "invalid_media" };
      }
      mediaId = nextMediaId;
      runId = "";
      overlayOpen = true;
      resetSelections();
      return loadDetail();
    }

    async function selectRun(nextRunId) {
      if (contextStale) {
        return { ok: false, error: "extension_context_invalidated", stale: true };
      }
      if (currentSuggestion() === null) {
        return { ok: false, error: "analysis_pending" };
      }
      if (!companion.isUuid(nextRunId) || nextRunId === runId) {
        return { ok: true };
      }
      runId = nextRunId;
      resetSelections();
      paint();
      return postOpened();
    }

    function setField(name, checked) {
      if (contextStale || currentSuggestion() === null) {
        return;
      }
      if (name === FIELD_TAGS && checked && mappedCount() < 1) {
        fields.tags = false;
      } else if (Object.prototype.hasOwnProperty.call(fields, name)) {
        fields[name] = Boolean(checked);
      }
      paint();
    }

    function removeChip(key) {
      if (contextStale || currentSuggestion() === null) {
        return;
      }
      if (typeof key === "string" && key) {
        removed[key] = true;
      }
      if (mappedCount() < 1) {
        fields.tags = false;
      }
      paint();
    }

    async function save() {
      if (contextStale) {
        return { ok: false, error: "extension_context_invalidated", stale: true };
      }
      if (currentSuggestion() === null) {
        return { ok: false, error: "analysis_pending" };
      }
      const selected = [];
      if (fields.display_title) {
        selected.push(FIELD_TITLE);
      }
      if (fields.tags) {
        selected.push(FIELD_TAGS);
      }
      if (fields.description) {
        selected.push(FIELD_DESCRIPTION);
      }
      const suggestion = currentSuggestion();
      const body = companion.sanitizeReviewApplyBody({
        analysis_run_id: runId,
        fields: selected,
        tag_keys: fields.tags ? remainingMappedKeys(suggestion && suggestion.tags, removed) : [],
      });
      if (!body) {
        lastError = "Select at least one field.";
        paint();
        return { ok: false, error: "invalid_apply" };
      }
      const openedResponse = await ensureOpened();
      if (!openedResponse || !openedResponse.ok) {
        return openedResponse;
      }
      const previousFields = {
        display_title: fields.display_title,
        tags: fields.tags,
        description: fields.description,
      };
      const previousRemoved = Object.assign({}, removed);
      const response = await request(
        companion.TYPES.REVIEW_INBOX_APPLY,
        Object.assign({ mediaId: mediaId }, body)
      );
      if (handleStaleResponse(response)) {
        return response;
      }
      if (response && (response.forbidden || response.status === 403)) {
        handleForbidden();
        return response;
      }
      if (!response || !response.ok) {
        fields = previousFields;
        removed = previousRemoved;
        lastError = "This review could not be applied.";
        overlayOpen = true;
        paint();
        return response;
      }
      const next = response.body || {};
      if (!detail || typeof detail !== "object") {
        detail = {};
      }
      if (next.canonical) {
        detail.canonical = next.canonical;
      }
      if (next.publication) {
        detail.publication = next.publication;
      }
      overlayOpen = true;
      resetSelections();
      notifyParent(companion.REVIEW_OVERLAY.types.INBOX_REFRESH);
      paint();
      return response;
    }

    function close() {
      overlayOpen = false;
      notifyParent(companion.REVIEW_OVERLAY.types.CLOSE);
      paint();
    }

    return {
      open: open,
      selectRun: selectRun,
      setField: setField,
      removeChip: removeChip,
      save: save,
      close: close,
      snapshot: snapshot,
    };
  }

  function markRuntimeStale(error) {
    if (runtimeStale) {
      return;
    }
    runtimeStale = true;
    runtimeStaleHandler();
    void error;
  }

  function runtimeObject() {
    return globalThis.chrome && globalThis.chrome.runtime;
  }

  function staleRuntimeResult() {
    return {
      ok: false,
      error: "extension_context_invalidated",
      status: 0,
      body: {},
      forbidden: false,
      stale: true,
    };
  }

  function guardInvalidatedRuntime(runtime, error) {
    if (!companion.isExtensionContextInvalidated(runtime, error)) {
      return false;
    }
    markRuntimeStale(error);
    return true;
  }

  function runtimeUrl(resource) {
    if (runtimeStale) {
      return "";
    }
    const runtime = runtimeObject();
    if (guardInvalidatedRuntime(runtime)) {
      return "";
    }
    try {
      return runtime.getURL(resource);
    } catch (error) {
      if (guardInvalidatedRuntime(runtime, error)) {
        return "";
      }
      throw error;
    }
  }

  function extensionOrigin() {
    try {
      if (typeof location !== "undefined" && location.protocol === "chrome-extension:") {
        return location.origin;
      }
    } catch {
      /* ignore */
    }
    const raw = runtimeUrl("ui/review.html");
    if (raw) {
      try {
        return new URL(raw).origin;
      } catch {
        if (raw.indexOf("chrome-extension://") === 0) {
          return raw.split("/").slice(0, 3).join("/");
        }
      }
    }
    return "";
  }

  function request(type, payload) {
    return new Promise((resolve, reject) => {
      if (runtimeStale) {
        resolve(staleRuntimeResult());
        return;
      }
      const runtime = runtimeObject();
      if (guardInvalidatedRuntime(runtime)) {
        resolve(staleRuntimeResult());
        return;
      }
      try {
        runtime.sendMessage(
          { v: companion.PROTOCOL, type: type, payload: payload || {} },
          (response) => {
            let lastError;
            try {
              lastError = runtime.lastError;
            } catch (error) {
              if (guardInvalidatedRuntime(runtime, error)) {
                resolve(staleRuntimeResult());
                return;
              }
              reject(error);
              return;
            }
            if (lastError) {
              if (guardInvalidatedRuntime(runtime, lastError)) {
                resolve(staleRuntimeResult());
                return;
              }
              resolve({
                ok: false,
                error: "extension_unavailable",
                status: 0,
                body: {},
                forbidden: false,
              });
              return;
            }
            resolve(
              response || {
                ok: false,
                error: "empty_response",
                status: 0,
                body: {},
                forbidden: false,
              }
            );
          }
        );
      } catch (error) {
        if (guardInvalidatedRuntime(runtime, error)) {
          resolve(staleRuntimeResult());
          return;
        }
        reject(error);
      }
    });
  }

  function postToParent(type) {
    const origin = extensionOrigin();
    if (!origin || !window.parent || window.parent === window) {
      return;
    }
    window.parent.postMessage({ v: companion.REVIEW_OVERLAY.protocol, type: type }, origin);
  }

  function clearNode(node) {
    if (!node) {
      return;
    }
    while (node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function addLine(parent, label, value) {
    const p = parent.ownerDocument.createElement("p");
    const strong = parent.ownerDocument.createElement("strong");
    strong.textContent = label + ": ";
    p.appendChild(strong);
    p.appendChild(parent.ownerDocument.createTextNode(value == null ? "" : String(value)));
    parent.appendChild(p);
  }

  function receiptLine(receipt) {
    return [receipt.analysis_run_id, receipt.model_id, receipt.applied_at_ms]
      .filter(function keep(value) {
        return value != null && value !== "";
      })
      .join(" · ");
  }

  function renderReceiptPanel(receipts, canonical) {
    clearNode(receipts);
    if (!canonical || typeof canonical !== "object") {
      return;
    }
    const sources = canonical.field_sources || {};
    Object.keys(sources).forEach(function renderReceipt(fieldName) {
      const receipt = sources[fieldName];
      if (!receipt || typeof receipt !== "object") {
        return;
      }
      addLine(receipts, fieldName, receiptLine(receipt));
    });
    const tagSources = canonical.tag_sources || {};
    Object.keys(tagSources).forEach(function renderTagSource(tagKey) {
      const receipt = tagSources[tagKey];
      if (!receipt || typeof receipt !== "object") {
        return;
      }
      addLine(receipts, "tag " + tagKey, receiptLine(receipt));
    });
  }

  function bindReviewUi(doc) {
    const history = doc.getElementById("run-history");
    const canonical = doc.getElementById("canonical");
    const publication = doc.getElementById("publication");
    const receipts = doc.getElementById("receipts");
    const suggestionTitle = doc.getElementById("suggestion-title");
    const suggestionDescription = doc.getElementById("suggestion-description");
    const mappedChips = doc.getElementById("mapped-chips");
    const droppedTags = doc.getElementById("dropped-tags");
    const fieldTitle = doc.getElementById("field-title");
    const fieldTags = doc.getElementById("field-tags");
    const fieldDescription = doc.getElementById("field-description");
    const status = doc.getElementById("status");
    const saveButton = doc.getElementById("save");
    const closeButton = doc.getElementById("close");
    if (
      !history ||
      !canonical ||
      !publication ||
      !receipts ||
      !suggestionTitle ||
      !suggestionDescription ||
      !mappedChips ||
      !droppedTags ||
      !fieldTitle ||
      !fieldTags ||
      !fieldDescription ||
      !status ||
      !saveButton ||
      !closeButton
    ) {
      return null;
    }

    runtimeStaleHandler = function handleRuntimeStale() {
      status.textContent = RELOAD_RECOVERY;
      status.setAttribute("data-kind", "error");
      history.disabled = true;
      fieldTitle.disabled = true;
      fieldTags.disabled = true;
      fieldDescription.disabled = true;
      saveButton.disabled = true;
    };

    function render(state) {
      if (!state.overlayOpen) {
        suggestionTitle.textContent = "";
        suggestionDescription.textContent = "";
        clearNode(history);
        clearNode(canonical);
        clearNode(publication);
        clearNode(receipts);
        clearNode(mappedChips);
        clearNode(droppedTags);
        status.textContent = "";
        status.removeAttribute("data-kind");
        fieldTitle.checked = false;
        fieldTags.checked = false;
        fieldDescription.checked = false;
        saveButton.disabled = true;
        return;
      }
      const suggestion = state.suggestion || {};
      const rows = Array.isArray(state.suggestions) ? state.suggestions : [];
      if (history.options.length !== rows.length) {
        clearNode(history);
        rows.forEach((row) => {
          if (!row || !companion.isUuid(row.analysis_run_id)) {
            return;
          }
          const option = doc.createElement("option");
          option.value = row.analysis_run_id;
          option.textContent = companion.formatReviewRunLabel(row);
          history.appendChild(option);
        });
      }
      if (companion.isUuid(state.runId)) {
        history.value = state.runId;
      }
      suggestionTitle.textContent = typeof suggestion.title === "string" ? suggestion.title : "";
      suggestionDescription.textContent =
        typeof suggestion.description === "string" ? suggestion.description : "";
      clearNode(canonical);
      const current = state.canonical || {};
      addLine(canonical, "Current title", current.display_title || "");
      addLine(canonical, "Current description", current.description || "");
      const currentTags = Array.isArray(current.tags) ? current.tags : [];
      addLine(
        canonical,
        "Current tags",
        currentTags
          .map(function tagLabel(tag) {
            return (tag && (tag.display_name || tag.key)) || "";
          })
          .filter(Boolean)
          .join(", ")
      );
      clearNode(publication);
      const pub = state.publication || {};
      addLine(publication, "State", pub.state || "");
      addLine(publication, "Ready", pub.ready === true ? "yes" : "no");
      addLine(publication, "Status", pub.status || "");
      const missing = Array.isArray(pub.missing_fields) ? pub.missing_fields.join(", ") : "";
      addLine(publication, "Missing fields", missing);
      clearNode(receipts);
      renderReceiptPanel(receipts, current);
      clearNode(mappedChips);
      clearNode(droppedTags);
      (Array.isArray(suggestion.tags) ? suggestion.tags : []).forEach(function renderTag(tag) {
        if (!tag) {
          return;
        }
        if (tag.status === "mapped" && typeof tag.key === "string" && !state.removed[tag.key]) {
          const chip = doc.createElement("button");
          chip.type = "button";
          chip.setAttribute("data-tag-key", tag.key);
          chip.textContent = tag.display_name || tag.value || tag.key;
          chip.disabled = runtimeStale || state.runtimeStale === true;
          mappedChips.appendChild(chip);
          return;
        }
        if (tag.status && tag.status !== "mapped") {
          const li = doc.createElement("li");
          li.textContent = (tag.value || tag.key || "") + " (" + tag.status + ")";
          droppedTags.appendChild(li);
        }
      });
      fieldTitle.checked = state.fields.display_title === true;
      fieldTags.checked = state.fields.tags === true;
      fieldDescription.checked = state.fields.description === true;
      history.disabled =
        runtimeStale || state.runtimeStale === true || state.pending === true;
      fieldTitle.disabled =
        runtimeStale || state.runtimeStale === true || state.pending === true;
      fieldTags.disabled =
        runtimeStale ||
        state.runtimeStale === true ||
        state.pending === true ||
        state.tagsDisabled === true;
      fieldDescription.disabled =
        runtimeStale || state.runtimeStale === true || state.pending === true;
      saveButton.disabled =
        runtimeStale || state.runtimeStale === true || state.saveEnabled !== true;
      status.textContent =
        state.lastError || (state.pending === true ? NO_SUCCESSFUL_ANALYSIS : "");
      if (state.lastError) {
        status.setAttribute("data-kind", "error");
      } else {
        status.removeAttribute("data-kind");
      }
    }

    const session = createReviewController({
      request: request,
      notifyParent: postToParent,
      render: render,
    });

    history.addEventListener("change", function onHistoryChange() {
      void session.selectRun(history.value);
    });
    fieldTitle.addEventListener("change", function onTitleChange() {
      session.setField(FIELD_TITLE, fieldTitle.checked);
    });
    fieldTags.addEventListener("change", function onTagsChange() {
      session.setField(FIELD_TAGS, fieldTags.checked);
    });
    fieldDescription.addEventListener("change", function onDescriptionChange() {
      session.setField(FIELD_DESCRIPTION, fieldDescription.checked);
    });
    mappedChips.addEventListener("click", function onChipClick(event) {
      let node = event.target;
      while (node && node !== mappedChips) {
        const key =
          (node.dataset && node.dataset.tagKey) ||
          (typeof node.getAttribute === "function" ? node.getAttribute("data-tag-key") : "");
        if (key) {
          session.removeChip(key);
          return;
        }
        node = node.parentNode;
      }
    });
    saveButton.addEventListener("click", function onSaveClick() {
      void session.save();
    });
    closeButton.addEventListener("click", function onCloseClick() {
      session.close();
    });
    window.addEventListener("keydown", function onKeydown(event) {
      if (event.key === "Escape") {
        session.close();
      }
    });

    const mediaId = companion.parseReviewMediaHash(window.location.hash);
    if (mediaId) {
      void session.open(mediaId);
    }
    return session;
  }

  globalThis.KronikaReviewOverlay = {
    remainingMappedKeys: remainingMappedKeys,
    tagsCanBeChecked: tagsCanBeChecked,
    saveEnabled: saveEnabled,
    formatRunLabel: function formatRunLabel(run) {
      return companion.formatReviewRunLabel(run);
    },
    parseMediaHash: function parseMediaHash(hash) {
      return companion.parseReviewMediaHash(hash);
    },
    createController: createReviewController,
    request: request,
    runtimeUrl: runtimeUrl,
    renderReceiptPanel: renderReceiptPanel,
  };

  if (globalThis.document && globalThis.document.getElementById) {
    bindReviewUi(globalThis.document);
  }
})();
