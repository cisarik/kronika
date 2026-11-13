(function () {
  const companion = globalThis.KronikaCompanion;
  // The extension emits and accepts only the canonical companion web protocol.
  // Handshake, attach, hosted details and recovery are otherwise unchanged.
  const WEB_PROTOCOL = "kronika.companion.web.v1";
  const ACCEPTED_WEB_PROTOCOLS = Object.freeze([WEB_PROTOCOL]);
  const WEB_TYPES = Object.freeze({
    WEB_READY: "web_ready",
    HOST_HELLO: "host_hello",
    HOST_ACK: "host_ack",
    ATTACH_REQUEST: "attach_request",
    ATTACH_RESULT: "attach_result",
    OPEN_DETAILS: "open_details",
    OPEN_UPLOAD: "open_upload",
  });
  const HANDSHAKE_WAIT_MS = 10000;
  const RELOAD_RECOVERY = companion.EXTENSION_CONTEXT_RECOVERY_COPY;
  let runtimeStale = false;
  let runtimeStaleHandler = function handleEarlyRuntimeStale() {};

  function acceptCompanionWebProtocol(value) {
    return ACCEPTED_WEB_PROTOCOLS.indexOf(value) !== -1;
  }

  function acceptIncomingWebMessage(event, iframeWindow, storedOrigin) {
    if (!iframeWindow || !event || event.source !== iframeWindow) {
      return null;
    }
    if (typeof storedOrigin !== "string" || event.origin !== storedOrigin) {
      return null;
    }
    if (!companion.acceptKronikaOrigin(event.origin)) {
      return null;
    }
    const data = event.data;
    if (
      !data ||
      typeof data !== "object" ||
      !acceptCompanionWebProtocol(data.v) ||
      typeof data.type !== "string"
    ) {
      return null;
    }
    return data;
  }

  function attachIdsFromWebRequest(data) {
    const payload = data && data.payload;
    if (!payload || typeof payload !== "object") {
      return null;
    }
    if (!companion.isUuid(payload.mediaId) || !companion.isUuid(payload.locationId)) {
      return null;
    }
    return { mediaId: payload.mediaId, locationId: payload.locationId };
  }

  function framingFailureCopy() {
    return "Kronika did not load in this panel.";
  }

  function companionHostMissingCopy() {
    return "This Kronika server cannot host companion Attach yet. The library below is an older web without the companion host.";
  }

  function handshakeTimeoutCopy(frameLoaded) {
    return frameLoaded ? companionHostMissingCopy() : framingFailureCopy();
  }

  globalThis.KronikaSidebarBridge = {
    WEB_PROTOCOL,
    CURRENT_WEB_PROTOCOL: WEB_PROTOCOL,
    ACCEPTED_WEB_PROTOCOLS,
    acceptCompanionWebProtocol,
    WEB_TYPES,
    acceptIncomingWebMessage,
    attachIdsFromWebRequest,
    framingFailureCopy,
    companionHostMissingCopy,
    handshakeTimeoutCopy,
  };

  const REVIEW = companion.REVIEW_INBOX;
  const COMPACT_ANALYZED_LIMIT = 5;
  let historyUserExpanded = false;
  let downloadHistoryExpanded = false;
  let reviewAllAvailable = false;

  function historyStampMs(item) {
    if (!item) {
      return 0;
    }
    if (item.analyzed === true) {
      return item.completed_at_ms;
    }
    return item.created_at_ms;
  }

  function compareHistoryNewestFirst(left, right) {
    const rightStamp = historyStampMs(right);
    const leftStamp = historyStampMs(left);
    if (rightStamp !== leftStamp) {
      return rightStamp - leftStamp;
    }
    const leftId = left && typeof left.media_id === "string" ? left.media_id : "";
    const rightId = right && typeof right.media_id === "string" ? right.media_id : "";
    if (leftId < rightId) {
      return -1;
    }
    if (leftId > rightId) {
      return 1;
    }
    return 0;
  }

  function partitionHistoryItems(rawItems, compactMode) {
    const items = companion.sanitizeReviewInboxItems(rawItems);
    const analyzed = items.filter((item) => item.analyzed === true).slice().sort(compareHistoryNewestFirst);
    const pending = items.filter((item) => item.analyzed !== true).slice().sort(compareHistoryNewestFirst);
    if (compactMode === "own_activity") {
      const ordered = items.slice().sort(compareHistoryNewestFirst);
      return {
        items: ordered,
        analyzed: analyzed,
        pending: pending,
        compact: ordered.slice(0, COMPACT_ANALYZED_LIMIT),
        expanded: ordered.slice(COMPACT_ANALYZED_LIMIT),
      };
    }
    const compact = analyzed.slice(0, COMPACT_ANALYZED_LIMIT);
    const remainderAnalyzed = analyzed.slice(COMPACT_ANALYZED_LIMIT);
    const expanded = pending.concat(remainderAnalyzed).sort(compareHistoryNewestFirst);
    return { items, analyzed, pending, compact, expanded };
  }

  function historyClickKind(_item) {
    return "open_details";
  }

  function openDetailsMessage(mediaId) {
    if (!companion.isUuid(mediaId)) {
      return null;
    }
    return {
      v: WEB_PROTOCOL,
      type: WEB_TYPES.OPEN_DETAILS,
      payload: { mediaId: mediaId },
    };
  }

  function activateHistoryItem(item, openDetails, openPending) {
    void openPending;
    if (typeof openDetails === "function") {
      openDetails(item && item.media_id);
    }
    return "open_details";
  }

  function renderReviewInboxList(listNode, items) {
    if (!listNode) {
      return;
    }
    while (listNode.firstChild) {
      listNode.removeChild(listNode.firstChild);
    }
    const doc = listNode.ownerDocument || globalThis.document;
    (Array.isArray(items) ? items : []).forEach((item) => {
      if (!item || typeof item.title !== "string" || !companion.isUuid(item.media_id)) {
        return;
      }
      const li = doc.createElement("li");
      const button = doc.createElement("button");
      button.type = "button";
      const classes = ["review-history-button"];
      if (item.analyzed === true) {
        classes.push("review-history-button--analyzed");
        if (item.unopened === true) {
          classes.push("review-history-button--unopened");
        }
      } else {
        classes.push("review-history-button--pending");
      }
      button.className = classes.join(" ");
      if (typeof button.setAttribute === "function") {
        button.setAttribute("data-media-id", item.media_id);
      }
      button.textContent = item.title;
      if (typeof li.appendChild === "function") {
        li.appendChild(button);
      } else {
        li.textContent = item.title;
        if (typeof li.setAttribute === "function") {
          li.setAttribute("data-media-id", item.media_id);
        }
      }
      listNode.appendChild(li);
    });
  }

  function setReviewHistoryExpanded(toggleNode, historyNode, expanded) {
    const next = Boolean(expanded && toggleNode && toggleNode.disabled !== true);
    if (toggleNode) {
      toggleNode.setAttribute("aria-expanded", next ? "true" : "false");
    }
    if (historyNode) {
      historyNode.hidden = !next;
    }
    return next;
  }

  function setAllExpanded(nodes, expanded) {
    const allButton = nodes && nodes.allButton;
    const expandedList = nodes && nodes.expandedList;
    const downloadsOwnAll = Boolean(
      nodes &&
        nodes.downloadAll &&
        nodes.downloadSection &&
        nodes.downloadSection.hidden !== true
    );
    const next = Boolean(expanded && allButton && allButton.hidden !== true && allButton.disabled !== true);
    if (allButton && typeof allButton.setAttribute === "function") {
      allButton.setAttribute("aria-expanded", next ? "true" : "false");
    }
    if (expandedList && !downloadsOwnAll) {
      expandedList.hidden = !next;
    }
    return next;
  }

  function reviewItemsOmittingDownloadMedia(rawItems, downloadItems) {
    const covered = {};
    (Array.isArray(downloadItems) ? downloadItems : []).forEach((item) => {
      const mediaId = item && item.media_id;
      if (companion.isUuid(mediaId)) {
        covered[mediaId.toLowerCase()] = true;
      }
    });
    if (!Array.isArray(rawItems)) {
      return rawItems;
    }
    return rawItems.filter((item) => {
      if (!item || typeof item !== "object" || typeof item.media_id !== "string") {
        return true;
      }
      return covered[item.media_id.toLowerCase()] !== true;
    });
  }

  function unreadRunByMediaId(rawItems) {
    const matched = {};
    companion.sanitizeReviewInboxItems(rawItems).forEach((item) => {
      if (item.analyzed !== true || item.unopened !== true || !companion.isUuid(item.analysis_run_id)) {
        return;
      }
      const key = item.media_id.toLowerCase();
      if (!matched[key]) {
        matched[key] = item.analysis_run_id;
      }
    });
    return matched;
  }

  function applyHistoryDisclosure(nodes) {
    const sectionOpen = Boolean(nodes && nodes.downloadSection && nodes.downloadSection.hidden !== true);
    const button = nodes && nodes.downloadAll;
    if (!sectionOpen) {
      downloadHistoryExpanded = false;
    }
    const expanded = Boolean(downloadHistoryExpanded && sectionOpen);
    if (button) {
      button.hidden = !sectionOpen;
      button.disabled = !sectionOpen;
      if (typeof button.setAttribute === "function") {
        button.setAttribute("aria-expanded", expanded ? "true" : "false");
      }
    }
    if (nodes && nodes.downloadExpandedList) {
      const laterCount = nodes.downloadExpandedList.childNodes
        ? nodes.downloadExpandedList.childNodes.length
        : 0;
      nodes.downloadExpandedList.hidden = !(expanded && laterCount > 0);
    }
    const gateReviews = Boolean(button && sectionOpen && !expanded);
    if (nodes && nodes.reviewRest) {
      nodes.reviewRest.hidden = gateReviews;
    }
    const downloadsOwnAll = Boolean(button && sectionOpen);
    if (nodes && nodes.allButton) {
      if (downloadsOwnAll) {
        nodes.allButton.hidden = true;
        if (typeof nodes.allButton.setAttribute === "function") {
          nodes.allButton.setAttribute("aria-expanded", "false");
        }
      } else {
        nodes.allButton.hidden = !(reviewAllAvailable && !gateReviews);
      }
    }
    if (downloadsOwnAll && nodes && nodes.expandedList) {
      const reviewLater = nodes.expandedList.childNodes ? nodes.expandedList.childNodes.length : 0;
      nodes.expandedList.hidden = !(expanded && reviewLater > 0);
    }
    if (gateReviews) {
      setAllExpanded(nodes, false);
    }
  }

  function setDownloadHistoryExpanded(nodes, expanded) {
    downloadHistoryExpanded = Boolean(expanded);
    applyHistoryDisclosure(nodes);
    const button = nodes && nodes.downloadAll;
    return Boolean(
      button && typeof button.getAttribute === "function" && button.getAttribute("aria-expanded") === "true"
    );
  }

  function markMatchedDownloadOpened(mediaId, analysisRunId) {
    if (!companion.isUuid(mediaId) || !companion.isUuid(analysisRunId)) {
      return;
    }
    void request(companion.TYPES.REVIEW_INBOX_OPENED, {
      mediaId: mediaId,
      analysis_run_id: analysisRunId,
    }).then(
      function refreshAfterOpened() {
        void refreshInbox();
      },
      function refreshAfterOpenedFailure() {
        void refreshInbox();
      }
    );
  }

  function renderDownloadHistory(listNode, items, unreadRuns) {
    if (!listNode || typeof listNode.appendChild !== "function" || typeof listNode.removeChild !== "function") {
      return 0;
    }
    while (listNode.firstChild) {
      listNode.removeChild(listNode.firstChild);
    }
    const doc = listNode.ownerDocument || globalThis.document;
    const rows = Array.isArray(items) ? items : [];
    const runs = unreadRuns && typeof unreadRuns === "object" ? unreadRuns : {};
    if (!doc || typeof doc.createElement !== "function") {
      return 0;
    }
    rows.forEach((item) => {
      const li = doc.createElement("li");
      const source = doc.createElement("span");
      const label = doc.createElement("span");
      const status = doc.createElement("span");
      source.className = "download-history-source";
      label.className = "download-history-label";
      status.className = "download-history-status";
      source.textContent = item.source === "youtube" ? "YouTube" : "X";
      label.textContent = item.label;
      status.textContent = companion.downloadStatusText(item.status);
      const mediaId = item && item.media_id;
      const actionable = companion.isUuid(mediaId);
      const matchedRun = actionable ? runs[String(mediaId).toLowerCase()] : "";
      const unread = companion.isUuid(matchedRun);
      const row = doc.createElement(actionable ? "button" : "div");
      if (actionable) {
        row.type = "button";
        row.className = unread
          ? "download-history-row download-history-row--action download-history-row--unread"
          : "download-history-row download-history-row--action";
        if (typeof row.setAttribute === "function") {
          row.setAttribute("type", "button");
          row.setAttribute(
            "aria-label",
            (unread ? "Unread. " : "") + "Open details for " + item.label
          );
        }
        if (typeof row.addEventListener === "function") {
          let openedPosted = false;
          row.addEventListener("click", function openOwnDownloadDetails() {
            openHostedDetails(mediaId);
            if (!unread || openedPosted) {
              return;
            }
            openedPosted = true;
            markMatchedDownloadOpened(mediaId, matchedRun);
          });
        }
      } else {
        row.className = "download-history-row";
      }
      row.appendChild(source);
      row.appendChild(label);
      row.appendChild(status);
      if (unread) {
        const dot = doc.createElement("span");
        dot.className = "download-history-unread-dot";
        if (typeof dot.setAttribute === "function") {
          dot.setAttribute("aria-hidden", "true");
        }
        const hidden = doc.createElement("span");
        hidden.className = "download-history-unread";
        hidden.textContent = "Unread";
        row.appendChild(dot);
        row.appendChild(hidden);
      }
      li.appendChild(row);
      listNode.appendChild(li);
    });
    return rows.length;
  }

  function renderReviewCollections(nodes, rawItems, compactMode, downloadCount) {
    const partitioned = partitionHistoryItems(rawItems, compactMode);
    renderReviewInboxList(nodes.historyList, partitioned.compact);
    if (nodes.expandedList) {
      renderReviewInboxList(nodes.expandedList, partitioned.expanded);
    }
    const hasHistory = partitioned.items.length > 0;
    const hasDownloads = typeof downloadCount === "number" && downloadCount > 0;
    const hasRows = hasHistory || hasDownloads;
    reviewAllAvailable = hasHistory;
    nodes.toggle.disabled = !hasRows;
    if (nodes.allButton) {
      nodes.allButton.hidden = !hasHistory;
      nodes.allButton.disabled = !hasHistory;
    }
    if (!hasRows) {
      historyUserExpanded = false;
      downloadHistoryExpanded = false;
      setReviewHistoryExpanded(nodes.toggle, nodes.history, false);
      setAllExpanded(nodes, false);
    } else if (!hasHistory) {
      setReviewHistoryExpanded(nodes.toggle, nodes.history, historyUserExpanded);
      setAllExpanded(nodes, false);
    } else {
      setReviewHistoryExpanded(nodes.toggle, nodes.history, historyUserExpanded);
    }
    applyHistoryDisclosure(nodes);
    return {
      historyItems: partitioned.items,
      compact: partitioned.compact,
      expanded: partitioned.expanded,
    };
  }

  function hideReviewCollections(nodes, disableToggle) {
    renderReviewInboxList(nodes.historyList, []);
    if (nodes.expandedList) {
      renderReviewInboxList(nodes.expandedList, []);
    }
    renderDownloadHistory(nodes.downloadList, []);
    if (nodes.downloadExpandedList) {
      renderDownloadHistory(nodes.downloadExpandedList, []);
    }
    if (nodes.downloadSection) {
      nodes.downloadSection.hidden = true;
    }
    nodes.toggle.disabled = disableToggle === true;
    historyUserExpanded = false;
    downloadHistoryExpanded = false;
    reviewAllAvailable = false;
    setReviewHistoryExpanded(nodes.toggle, nodes.history, false);
    if (nodes.allButton) {
      nodes.allButton.hidden = true;
      nodes.allButton.disabled = true;
    }
    setAllExpanded(nodes, false);
    applyHistoryDisclosure(nodes);
  }

  function presentHistory(nodes, result) {
    if (
      !result ||
      result.ok !== true ||
      result.forbidden === true ||
      result.status === 403 ||
      result.stale === true
    ) {
      hideReviewCollections(nodes, true);
      return { ok: false, historyItems: [], downloads: [], rendered: null };
    }
    const projection = companion.sanitizeDownloadProjection(result.downloads);
    const downloadItems = projection.ok ? projection.items : [];
    const canSplit = Boolean(nodes.downloadExpandedList);
    const compactDownloads = canSplit
      ? downloadItems.slice(0, companion.DOWNLOAD_HISTORY.limit)
      : downloadItems;
    const laterDownloads = canSplit ? downloadItems.slice(companion.DOWNLOAD_HISTORY.limit) : [];
    const unreadRuns = unreadRunByMediaId(result.items);
    renderDownloadHistory(nodes.downloadList, compactDownloads, unreadRuns);
    if (nodes.downloadExpandedList) {
      renderDownloadHistory(nodes.downloadExpandedList, laterDownloads, unreadRuns);
    }
    const downloadCount = projection.ok ? downloadItems.length : 0;
    if (downloadCount === 0) {
      downloadHistoryExpanded = false;
    }
    if (nodes.downloadSection) {
      nodes.downloadSection.hidden = downloadCount === 0;
    }
    const compactMode = result.history_source === "own-history" ? "own_activity" : "analyzed";
    const rendered = renderReviewCollections(
      nodes,
      reviewItemsOmittingDownloadMedia(result.items, projection.ok ? downloadItems : []),
      compactMode,
      downloadCount
    );
    return {
      ok: true,
      historyItems: rendered.historyItems,
      downloads: downloadItems,
      rendered: rendered,
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
    return { ok: false, error: "extension_context_invalidated", stale: true };
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

  function reviewOverlayUrl(mediaId) {
    if (!companion.isUuid(mediaId)) {
      return "";
    }
    const base = runtimeUrl("ui/review.html");
    return base ? base + "#media=" + mediaId : "";
  }

  function openReviewOverlay(mediaId, dialogNode, frameNode) {
    if (!companion.isUuid(mediaId) || !dialogNode || !frameNode) {
      return false;
    }
    const url = reviewOverlayUrl(mediaId);
    if (!url) {
      return false;
    }
    frameNode.setAttribute("src", url);
    if (typeof dialogNode.show === "function") {
      dialogNode.show();
    } else {
      dialogNode.setAttribute("open", "");
    }
    return true;
  }

  function closeReviewOverlay(dialogNode, frameNode) {
    if (frameNode) {
      frameNode.removeAttribute("src");
    }
    if (!dialogNode) {
      return;
    }
    if (typeof dialogNode.close === "function" && dialogNode.open) {
      dialogNode.close();
      return;
    }
    dialogNode.removeAttribute("open");
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

  globalThis.KronikaReviewInbox = {
    POLL_MS: REVIEW.pollMs,
    COMPACT_ANALYZED_LIMIT: COMPACT_ANALYZED_LIMIT,
    renderList: renderReviewInboxList,
    renderCollections: renderReviewCollections,
    renderDownloadHistory: renderDownloadHistory,
    presentHistory: presentHistory,
    hideCollections: hideReviewCollections,
    setHistoryExpanded: setReviewHistoryExpanded,
    setAllExpanded: setAllExpanded,
    setDownloadHistoryExpanded: setDownloadHistoryExpanded,
    partitionHistoryItems: partitionHistoryItems,
    historyClickKind: historyClickKind,
    activateHistoryItem: activateHistoryItem,
    openDetailsMessage: openDetailsMessage,
    request: request,
    runtimeUrl: runtimeUrl,
    overlayUrl: reviewOverlayUrl,
    openOverlay: openReviewOverlay,
    closeOverlay: closeReviewOverlay,
  };

  const originInput = document.getElementById("origin");
  const shellStatus = document.getElementById("shell-status");
  const frame = document.getElementById("frame");
  const chromeAction = document.getElementById("chrome-action");
  const settingsDialog = document.getElementById("settings-dialog");
  const settingsOpen = document.getElementById("settings-open");
  const settingsClose = document.getElementById("settings-close");
  const settingsSave = document.getElementById("settings-save");
  const adminSettings = document.getElementById("admin-settings");
  const automaticAnalysisEnabled = document.getElementById("automatic-analysis-enabled");
  const automaticAnalysisError = document.getElementById("automatic-analysis-error");
  const automaticAnalysisConfirm = document.getElementById("automatic-analysis-confirm");
  const automaticAnalysisConfirmOk = document.getElementById("automatic-analysis-confirm-ok");
  const automaticAnalysisConfirmCancel = document.getElementById("automatic-analysis-confirm-cancel");
  const reviewHistoryToggle = document.getElementById("review-history-toggle");
  const reviewHistory = document.getElementById("review-history");
  const reviewHistoryList = document.getElementById("review-history-list");
  const reviewHistoryAll = document.getElementById("review-history-all");
  const reviewHistoryExpanded = document.getElementById("review-history-expanded");
  const downloadHistory = document.getElementById("download-history");
  const downloadHistoryList = document.getElementById("download-history-list");
  const downloadHistoryAll = document.getElementById("download-history-all");
  const downloadHistoryExpandedList = document.getElementById("download-history-expanded");
  const reviewHistoryRest = document.getElementById("review-history-rest");
  const reviewDialog = document.getElementById("review-dialog");
  const reviewFrame = document.getElementById("review-frame");
  const welcome = document.getElementById("welcome");
  const welcomeForm = document.getElementById("welcome-form");
  const welcomeOrigin = document.getElementById("welcome-origin");
  const uploadOpen = document.getElementById("upload-open");
  const connectionState = document.getElementById("connection-state");
  if (
    !originInput ||
    !shellStatus ||
    !frame ||
    !chromeAction ||
    !settingsDialog ||
    !settingsOpen ||
    !settingsClose ||
    !settingsSave ||
    !adminSettings ||
    !automaticAnalysisEnabled ||
    !automaticAnalysisError ||
    !automaticAnalysisConfirm ||
    !automaticAnalysisConfirmOk ||
    !automaticAnalysisConfirmCancel ||
    !reviewHistoryToggle ||
    !reviewHistory ||
    !reviewHistoryList ||
    !reviewHistoryAll ||
    !reviewHistoryExpanded ||
    !reviewDialog ||
    !reviewFrame
  ) {
    return;
  }

  let storedOrigin = "";
  let handshakeTimer = 0;
  let handshakeSeen = false;
  let frameLoaded = false;
  let inboxPollTimer = 0;
  let inboxGeneration = 0;
  let inboxRefreshActive = false;
  let lastHistoryItems = [];
  let serverAutomaticAnalysisEnabled = false;
  let automaticAnalysisBusy = false;
  const reviewChromeNodes = {
    toggle: reviewHistoryToggle,
    history: reviewHistory,
    historyList: reviewHistoryList,
    allButton: reviewHistoryAll,
    expandedList: reviewHistoryExpanded,
    downloadSection: downloadHistory,
    downloadList: downloadHistoryList,
    downloadAll: downloadHistoryAll,
    downloadExpandedList: downloadHistoryExpandedList,
    reviewRest: reviewHistoryRest,
  };

  function setText(node, value, kind) {
    node.textContent = value;
    if (kind) {
      node.setAttribute("data-kind", kind);
    } else {
      node.removeAttribute("data-kind");
    }
  }

  function retireInboxGeneration() {
    inboxGeneration += 1;
    inboxRefreshActive = false;
    downloadHistoryExpanded = false;
    renderDownloadHistory(downloadHistoryList, []);
    if (downloadHistoryExpandedList) {
      renderDownloadHistory(downloadHistoryExpandedList, []);
      downloadHistoryExpandedList.hidden = true;
    }
    if (downloadHistory) {
      downloadHistory.hidden = true;
    }
    if (downloadHistoryAll && typeof downloadHistoryAll.setAttribute === "function") {
      downloadHistoryAll.hidden = true;
      downloadHistoryAll.setAttribute("aria-expanded", "false");
    }
  }

  runtimeStaleHandler = function handleRuntimeStale() {
    retireInboxGeneration();
    setText(shellStatus, RELOAD_RECOVERY, "error");
    chromeAction.disabled = true;
    settingsSave.disabled = true;
    automaticAnalysisEnabled.disabled = true;
    hideAdminSettings();
    reviewHistoryToggle.disabled = true;
    reviewHistoryAll.disabled = true;
    if (downloadHistoryAll) {
      downloadHistoryAll.disabled = true;
    }
    historyUserExpanded = false;
    downloadHistoryExpanded = false;
    setReviewHistoryExpanded(reviewHistoryToggle, reviewHistory, false);
    setAllExpanded(reviewChromeNodes, false);
    reviewHistoryList.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
    reviewHistoryExpanded.querySelectorAll("button").forEach((button) => {
      button.disabled = true;
    });
  };

  function syncChromeAction() {
    const connected = Boolean(storedOrigin);
    chromeAction.textContent = connected ? "Disconnect" : "Connect";
    chromeAction.setAttribute("aria-label", connected ? "Disconnect Kronika" : "Connect Kronika");
    if (welcome) {
      welcome.hidden = connected;
    }
    if (uploadOpen) {
      uploadOpen.hidden = !connected;
    }
    if (connectionState) {
      connectionState.textContent = connected ? "Connected to " + storedOrigin : "Not connected";
    }
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
          { v: companion.PROTOCOL, type, payload: payload || {} },
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
              resolve({ ok: false, error: "extension_unavailable" });
              return;
            }
            resolve(response || { ok: false, error: "empty_response" });
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

  function stopInboxPoll() {
    if (inboxPollTimer) {
      clearInterval(inboxPollTimer);
      inboxPollTimer = 0;
    }
  }

  function startInboxPoll() {
    stopInboxPoll();
    void refreshInbox();
    inboxPollTimer = setInterval(() => {
      if (globalThis.document && document.hidden) {
        return;
      }
      void refreshInbox();
    }, REVIEW.pollMs);
  }

  function hideInboxSection() {
    lastHistoryItems = [];
    hideReviewCollections(reviewChromeNodes, true);
  }

  function applyInboxResult(result) {
    const allWasExpanded = reviewHistoryAll.getAttribute("aria-expanded") === "true";
    const presented = presentHistory(reviewChromeNodes, result);
    lastHistoryItems = presented.ok ? presented.historyItems : [];
    if (!presented.ok || !presented.rendered) {
      return;
    }
    const downloadsOpen = Boolean(
      reviewChromeNodes.downloadSection && reviewChromeNodes.downloadSection.hidden !== true
    );
    if (downloadsOpen) {
      return;
    }
    setAllExpanded(
      reviewChromeNodes,
      allWasExpanded && presented.rendered.expanded.length > 0
    );
  }

  async function refreshInbox() {
    if (!storedOrigin) {
      hideInboxSection();
      return;
    }
    if (inboxRefreshActive) {
      return;
    }
    const generation = inboxGeneration;
    inboxRefreshActive = true;
    try {
      const result = await request(companion.TYPES.REVIEW_INBOX, {});
      if (generation !== inboxGeneration) {
        return;
      }
      applyInboxResult(result);
    } finally {
      if (generation === inboxGeneration) {
        inboxRefreshActive = false;
      }
    }
  }

  function onHistoryToggle() {
    const opening = reviewHistoryToggle.getAttribute("aria-expanded") !== "true";
    if (!opening) {
      downloadHistoryExpanded = false;
    }
    historyUserExpanded = setReviewHistoryExpanded(reviewHistoryToggle, reviewHistory, opening);
    applyHistoryDisclosure(reviewChromeNodes);
  }

  function onAllClick() {
    setAllExpanded(reviewChromeNodes, reviewHistoryAll.getAttribute("aria-expanded") !== "true");
  }

  function onDownloadAllClick() {
    setDownloadHistoryExpanded(
      reviewChromeNodes,
      !(downloadHistoryAll && downloadHistoryAll.getAttribute("aria-expanded") === "true")
    );
  }

  function openHostedDetails(mediaId) {
    const message = openDetailsMessage(mediaId);
    if (!message) {
      return false;
    }
    if (!handshakeSeen || !storedOrigin || !companion.acceptKronikaOrigin(storedOrigin)) {
      const loaded = frameLoaded;
      setText(shellStatus, handshakeTimeoutCopy(loaded), loaded ? "notice" : "error");
      return false;
    }
    postToFrame(message, storedOrigin);
    return true;
  }

  function historyItemForMediaId(mediaId) {
    let index = 0;
    while (index < lastHistoryItems.length) {
      if (lastHistoryItems[index].media_id === mediaId) {
        return lastHistoryItems[index];
      }
      index += 1;
    }
    return null;
  }

  function clearHandshakeWait() {
    if (handshakeTimer) {
      clearTimeout(handshakeTimer);
      handshakeTimer = 0;
    }
  }

  function clearFrame() {
    clearHandshakeWait();
    handshakeSeen = false;
    frameLoaded = false;
    frame.removeAttribute("src");
    frame.hidden = true;
  }

  function showFramingError() {
    setText(shellStatus, framingFailureCopy(), "error");
  }

  function showHandshakeTimeout() {
    const loaded = frameLoaded;
    setText(shellStatus, handshakeTimeoutCopy(loaded), loaded ? "notice" : "error");
  }

  function syncSettingsSave() {
    const origin = originInput.value.trim();
    const dirty = origin !== storedOrigin;
    settingsSave.disabled = runtimeStale || !origin || !dirty;
  }

  function hideAutomaticAnalysisConfirm() {
    automaticAnalysisConfirm.hidden = true;
  }

  function hideAdminSettings() {
    adminSettings.hidden = true;
    automaticAnalysisEnabled.checked = false;
    automaticAnalysisEnabled.disabled = true;
    automaticAnalysisError.hidden = true;
    automaticAnalysisError.textContent = "";
    hideAutomaticAnalysisConfirm();
    serverAutomaticAnalysisEnabled = false;
    automaticAnalysisBusy = false;
  }

  function showAutomaticAnalysisError(message) {
    automaticAnalysisError.textContent = message;
    automaticAnalysisError.hidden = !message;
  }

  function automaticAnalysisErrorCopy(result) {
    const error = result && result.error;
    const status = result && result.status;
    if (error === "network_failed") {
      return "Could not reach Kronika.";
    }
    if (error === "CAPABILITY_DENIED" || error === "http_403" || status === 403) {
      return "Automatic media analysis is administrator-only.";
    }
    if (error === "CLOUD_CONFIRMATION_REQUIRED" || error === "confirm_required" || status === 422) {
      return "Confirmation is required to turn on automatic media analysis.";
    }
    return "Could not update automatic media analysis.";
  }

  function applyServerAutomaticAnalysis(enabled) {
    serverAutomaticAnalysisEnabled = enabled === true;
    automaticAnalysisEnabled.checked = serverAutomaticAnalysisEnabled;
    automaticAnalysisEnabled.disabled = runtimeStale || automaticAnalysisBusy;
  }

  async function refreshAdministration() {
    hideAutomaticAnalysisConfirm();
    if (!storedOrigin || runtimeStale) {
      hideAdminSettings();
      return;
    }
    const identity = await request(companion.TYPES.IDENTITY, {});
    if (runtimeStale || (identity && identity.stale === true)) {
      return;
    }
    if (!identity || identity.ok !== true || !companion.hasProviderOperateCapability(identity.body)) {
      hideAdminSettings();
      return;
    }
    adminSettings.hidden = false;
    automaticAnalysisEnabled.disabled = true;
    showAutomaticAnalysisError("");
    const capability = await request(companion.TYPES.AUTOMATIC_ANALYSIS_CAPABILITY, {});
    if (runtimeStale || (capability && capability.stale === true)) {
      return;
    }
    if (!capability || capability.ok !== true) {
      applyServerAutomaticAnalysis(false);
      automaticAnalysisEnabled.disabled = true;
      showAutomaticAnalysisError(automaticAnalysisErrorCopy(capability));
      return;
    }
    applyServerAutomaticAnalysis(
      capability.body && capability.body.automatic_analysis_enabled === true
    );
  }

  async function putAutomaticAnalysis(enabled) {
    automaticAnalysisBusy = true;
    automaticAnalysisEnabled.disabled = true;
    const payload = enabled
      ? {
          automatic_media_analysis_enabled: true,
          confirm_cloud_upload: true,
        }
      : { automatic_media_analysis_enabled: false };
    const result = await request(companion.TYPES.AUTOMATIC_ANALYSIS_SETTINGS, payload);
    automaticAnalysisBusy = false;
    if (runtimeStale || (result && result.stale === true)) {
      return;
    }
    if (!result || result.ok !== true) {
      applyServerAutomaticAnalysis(serverAutomaticAnalysisEnabled);
      showAutomaticAnalysisError(automaticAnalysisErrorCopy(result));
      return;
    }
    const next =
      result.body && result.body.automatic_media_analysis_enabled === true;
    showAutomaticAnalysisError("");
    applyServerAutomaticAnalysis(next);
  }

  function onAutomaticAnalysisChange() {
    if (automaticAnalysisBusy || automaticAnalysisEnabled.disabled) {
      automaticAnalysisEnabled.checked = serverAutomaticAnalysisEnabled;
      return;
    }
    showAutomaticAnalysisError("");
    if (automaticAnalysisEnabled.checked) {
      automaticAnalysisConfirm.hidden = false;
      return;
    }
    hideAutomaticAnalysisConfirm();
    void putAutomaticAnalysis(false);
  }

  function onAutomaticAnalysisConfirmOk() {
    hideAutomaticAnalysisConfirm();
    void putAutomaticAnalysis(true);
  }

  function onAutomaticAnalysisConfirmCancel() {
    hideAutomaticAnalysisConfirm();
    automaticAnalysisEnabled.checked = false;
  }

  function openSettings() {
    if (!settingsDialog.open) {
      if (typeof settingsDialog.show === "function") {
        settingsDialog.show();
      } else {
        settingsDialog.setAttribute("open", "");
      }
      settingsOpen.setAttribute("aria-expanded", "true");
    }
    syncSettingsSave();
    originInput.focus();
    void refreshAdministration();
  }

  function promptConnectInSettings() {
    setText(shellStatus, "Connect Kronika in Settings");
    openSettings();
  }

  function closeSettings() {
    hideAutomaticAnalysisConfirm();
    settingsOpen.setAttribute("aria-expanded", "false");
    if (typeof settingsDialog.close === "function" && settingsDialog.open) {
      settingsDialog.close();
      return;
    }
    settingsDialog.removeAttribute("open");
  }

  function hostFrame(origin) {
    if (!companion.acceptKronikaOrigin(origin)) {
      clearFrame();
      return;
    }
    handshakeSeen = false;
    frameLoaded = false;
    clearHandshakeWait();
    frame.hidden = false;
    frame.src = origin;
    handshakeTimer = setTimeout(() => {
      if (!handshakeSeen) {
        showHandshakeTimeout();
      }
    }, HANDSHAKE_WAIT_MS);
  }

  function postToFrame(message, origin) {
    if (!frame.contentWindow || !companion.acceptKronikaOrigin(origin)) {
      return;
    }
    frame.contentWindow.postMessage(message, origin);
  }

  async function connect() {
    retireInboxGeneration();
    const origin = originInput.value.trim();
    if (!origin) {
      setText(shellStatus, "Enter a Kronika origin in Settings", "error");
      openSettings();
      return;
    }
    const result = await request(companion.TYPES.CONFIGURE_ORIGIN, { origin });
    if (runtimeStale || (result && result.stale === true)) {
      return;
    }
    if (!result.ok) {
      const error = result.error;
      const copy =
        error === "invalid_origin"
          ? "Use the Kronika HTTPS tailnet origin (https://<node>.<tailnet>.ts.net), with no path."
          : error || "Failed";
      setText(shellStatus, copy, "error");
      return;
    }
    storedOrigin = result.origin || origin;
    originInput.value = storedOrigin;
    syncChromeAction();
    syncSettingsSave();
    closeSettings();
    setText(shellStatus, "");
    hostFrame(storedOrigin);
    startInboxPoll();
  }

  async function reset() {
    retireInboxGeneration();
    stopInboxPoll();
    hideInboxSection();
    const result = await request(companion.TYPES.RESET, {});
    if (runtimeStale || (result && result.stale === true)) {
      return;
    }
    storedOrigin = "";
    originInput.value = "";
    syncChromeAction();
    syncSettingsSave();
    hideAdminSettings();
    clearFrame();
    setText(shellStatus, "Cleared");
    openSettings();
  }

  function onChromeAction() {
    if (storedOrigin) {
      void reset();
      return;
    }
    if (!originInput.value.trim()) {
      promptConnectInSettings();
      return;
    }
    void connect();
  }

  function onFrameLoad() {
    if (!frame.getAttribute("src")) {
      return;
    }
    frameLoaded = true;
  }

  function onFrameError() {
    clearHandshakeWait();
    showFramingError();
  }

  async function handleAttachRequest(event, ids) {
    const result = await request(companion.TYPES.ATTACH_BEGIN, {
      mediaId: ids.mediaId,
      locationId: ids.locationId,
      filename: "kronika-media.bin",
    });
    if (runtimeStale || (result && result.stale === true)) {
      return;
    }
    const ok = Boolean(result && result.ok);
    const error = (result && result.error) || "attach_failed";
    postToFrame(
      {
        v: WEB_PROTOCOL,
        type: WEB_TYPES.ATTACH_RESULT,
        payload: ok ? { ok: true } : { ok: false, error: error },
      },
      event.origin
    );
    if (!ok && error === "composer_unbound") {
      setText(shellStatus, "Composer is not bound", "error");
    } else if (ok) {
      setText(shellStatus, "Attached");
    } else {
      setText(shellStatus, error, "error");
    }
  }

  function mediaIdFromReviewEvent(event, listNode) {
    let node = event && event.target;
    while (node && node !== listNode) {
      const mediaId =
        (node.dataset && node.dataset.mediaId) ||
        (typeof node.getAttribute === "function" ? node.getAttribute("data-media-id") : "");
      if (companion.isUuid(mediaId)) {
        return mediaId;
      }
      node = node.parentNode;
    }
    return "";
  }

  function onReviewListClick(event, listNode) {
    const mediaId = mediaIdFromReviewEvent(event, listNode);
    if (!mediaId) {
      return;
    }
    const item = historyItemForMediaId(mediaId);
    if (!item) {
      return;
    }
    activateHistoryItem(
      item,
      function openAnalyzedDetails(id) {
        openHostedDetails(id);
        if (item.analyzed === true && companion.isUuid(item.analysis_run_id)) {
          void request(companion.TYPES.REVIEW_INBOX_OPENED, {
            mediaId: id,
            analysis_run_id: item.analysis_run_id,
          }).then(
            function refreshAfterOpened() {
              void refreshInbox();
            },
            function refreshAfterOpenedFailure() {
              void refreshInbox();
            }
          );
        }
      },
      function unusedPendingOverlay() {}
    );
    historyUserExpanded = setReviewHistoryExpanded(reviewHistoryToggle, reviewHistory, false);
  }

  function onReviewOverlayMessage(event) {
    const message = companion.acceptReviewOverlayMessage(
      event,
      reviewFrame.contentWindow,
      extensionOrigin()
    );
    if (!message) {
      return false;
    }
    if (message.type === companion.REVIEW_OVERLAY.types.FORBIDDEN) {
      closeReviewOverlay(reviewDialog, reviewFrame);
      hideInboxSection();
      return true;
    }
    if (message.type === companion.REVIEW_OVERLAY.types.CLOSE) {
      closeReviewOverlay(reviewDialog, reviewFrame);
      return true;
    }
    if (message.type === companion.REVIEW_OVERLAY.types.INBOX_REFRESH) {
      void refreshInbox();
      return true;
    }
    return true;
  }

  function onWindowMessage(event) {
    if (onReviewOverlayMessage(event)) {
      return;
    }
    const data = acceptIncomingWebMessage(event, frame.contentWindow, storedOrigin);
    if (!data) {
      return;
    }
    if (data.type === WEB_TYPES.WEB_READY) {
      handshakeSeen = true;
      clearHandshakeWait();
      postToFrame({ v: WEB_PROTOCOL, type: WEB_TYPES.HOST_HELLO }, event.origin);
      setText(shellStatus, "");
      return;
    }
    if (data.type === WEB_TYPES.HOST_ACK) {
      handshakeSeen = true;
      clearHandshakeWait();
      return;
    }
    if (data.type === WEB_TYPES.ATTACH_REQUEST) {
      const ids = attachIdsFromWebRequest(data);
      if (!ids) {
        postToFrame(
          {
            v: WEB_PROTOCOL,
            type: WEB_TYPES.ATTACH_RESULT,
            payload: { ok: false, error: "invalid_attach" },
          },
          event.origin
        );
        return;
      }
      void handleAttachRequest(event, ids);
    }
  }

  chromeAction.addEventListener("click", onChromeAction);
  if (uploadOpen) {
    uploadOpen.addEventListener("click", () => {
      if (!storedOrigin || !handshakeSeen || !companion.acceptKronikaOrigin(storedOrigin)) {
        const loaded = frameLoaded;
        setText(shellStatus, handshakeTimeoutCopy(loaded), loaded ? "notice" : "error");
        return;
      }
      closeSettings();
      postToFrame({ v: WEB_PROTOCOL, type: WEB_TYPES.OPEN_UPLOAD }, storedOrigin);
    });
  }
  if (welcomeForm && welcomeOrigin) {
    welcomeForm.addEventListener("submit", (event) => {
      event.preventDefault();
      const origin = welcomeOrigin.value.trim();
      if (!origin) {
        setText(shellStatus, "Enter your Kronika address to connect", "error");
        welcomeOrigin.focus();
        return;
      }
      originInput.value = origin;
      syncSettingsSave();
      void connect();
    });
  }
  reviewHistoryToggle.addEventListener("click", onHistoryToggle);
  reviewHistoryAll.addEventListener("click", onAllClick);
  if (downloadHistoryAll && typeof downloadHistoryAll.addEventListener === "function") {
    downloadHistoryAll.addEventListener("click", onDownloadAllClick);
  }
  reviewHistoryList.addEventListener("click", (event) => {
    onReviewListClick(event, reviewHistoryList);
  });
  reviewHistoryExpanded.addEventListener("click", (event) => {
    onReviewListClick(event, reviewHistoryExpanded);
  });
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) {
      stopInboxPoll();
      return;
    }
    if (storedOrigin) {
      startInboxPoll();
    }
  });
  settingsSave.addEventListener("click", () => {
    if (settingsSave.disabled) {
      return;
    }
    void connect();
  });
  originInput.addEventListener("input", syncSettingsSave);
  originInput.addEventListener("change", syncSettingsSave);
  settingsOpen.addEventListener("click", openSettings);
  settingsClose.addEventListener("click", closeSettings);
  automaticAnalysisEnabled.addEventListener("change", onAutomaticAnalysisChange);
  automaticAnalysisConfirmOk.addEventListener("click", onAutomaticAnalysisConfirmOk);
  automaticAnalysisConfirmCancel.addEventListener("click", onAutomaticAnalysisConfirmCancel);
  settingsDialog.addEventListener("click", (event) => {
    if (event.target === settingsDialog) {
      closeSettings();
    }
  });
  settingsDialog.addEventListener("close", () => {
    settingsOpen.setAttribute("aria-expanded", "false");
  });
  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && reviewDialog.open) {
      closeReviewOverlay(reviewDialog, reviewFrame);
    }
    if (event.key === "Escape" && settingsDialog.open) {
      closeSettings();
    }
  });
  reviewDialog.addEventListener("close", () => {
    reviewFrame.removeAttribute("src");
  });
  frame.addEventListener("load", onFrameLoad);
  frame.addEventListener("error", onFrameError);
  window.addEventListener("message", onWindowMessage);
  window.addEventListener("blur", () => {
    if (reviewHistoryToggle.getAttribute("aria-expanded") === "true") {
      historyUserExpanded = setReviewHistoryExpanded(reviewHistoryToggle, reviewHistory, false);
    }
  });
  chrome.storage.local.get(
    companion.storageKeyRequest(companion.STORAGE.origin),
    (stored) => {
      const origin = companion.storageValue(stored, companion.STORAGE.origin) || "";
      if (companion.acceptKronikaOrigin(origin)) {
        storedOrigin = origin;
        originInput.value = origin;
        syncChromeAction();
        syncSettingsSave();
        setText(shellStatus, "");
        hostFrame(origin);
        startInboxPoll();
        return;
      }
      storedOrigin = "";
      originInput.value = "";
      syncChromeAction();
      syncSettingsSave();
      hideAdminSettings();
      hideInboxSection();
      clearFrame();
      setText(shellStatus, "Connect Kronika in Settings");
    }
  );
})();
