(function () {
  const companion = globalThis.FrameNestCompanion;
  const SUGGESTION_LIMIT = 8;
  const TAG_LIMIT = 32;
  const COMPANION_X_TAG_KEY = "x";
  const COMPANION_X_TAG_DISPLAY_NAME = "\u{1D54F}";
  const UPGRADE_MESSAGE = "Kronika needs an update before this Save can complete.";
  const form = document.getElementById("save-form");
  const title = document.getElementById("title");
  const description = document.getElementById("description");
  const tagSearch = document.getElementById("tag-search");
  const suggestions = document.getElementById("tag-suggestions");
  const selectedTags = document.getElementById("selected-tags");
  const tagsStatus = document.getElementById("tags-status");
  const formStatus = document.getElementById("form-status");
  const closeButton = document.getElementById("close");
  const saveButton = document.getElementById("save");
  const catalog = [];
  const chosen = [];
  let activeSuggestion = -1;
  let formBusy = false;
  let runtimeStale = false;
  const RELOAD_RECOVERY = companion.EXTENSION_CONTEXT_RECOVERY_COPY;

  function setStatus(node, value, kind) {
    node.textContent = value;
    if (kind) {
      node.setAttribute("data-kind", kind);
    } else {
      node.removeAttribute("data-kind");
    }
    if (node === formStatus) {
      if (kind === "error") {
        formStatus.setAttribute("role", "alert");
        formStatus.setAttribute("aria-live", "assertive");
      } else {
        formStatus.setAttribute("role", "status");
        formStatus.setAttribute("aria-live", "polite");
      }
    }
  }

  function markRuntimeStale(error) {
    if (runtimeStale) {
      return;
    }
    runtimeStale = true;
    setFormBusy(true);
    setStatus(formStatus, RELOAD_RECOVERY, "error");
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

  function hashParams() {
    const raw = window.location.hash.startsWith("#")
      ? window.location.hash.slice(1)
      : "";
    return new URLSearchParams(raw);
  }

  function submittedUrl() {
    const accepted = companion.acceptXPostUrl(hashParams().get("url") || "");
    return accepted ? accepted.submittedUrl : null;
  }

  function overlayContentHeight() {
    let height = 0;
    if (form && typeof form.getBoundingClientRect === "function") {
      const rect = form.getBoundingClientRect();
      if (rect && typeof rect.height === "number") {
        height = Math.ceil(rect.height);
      }
    }
    if (height < 1 && form && typeof form.offsetHeight === "number") {
      height = Math.ceil(form.offsetHeight);
    }
    return height;
  }

  function notifySize() {
    const height = overlayContentHeight();
    if (height < 1) {
      return;
    }
    window.parent.postMessage(
      {
        v: companion.PROTOCOL,
        source: "framenest-save-popup",
        action: "size",
        height: height,
        result: null,
      },
      "*"
    );
  }

  function scheduleSize() {
    if (typeof requestAnimationFrame === "function") {
      requestAnimationFrame(notifySize);
      return;
    }
    notifySize();
  }

  function applyPrefill(data) {
    if (!data || formBusy) {
      return;
    }
    if (typeof data.title === "string" && !title.value) {
      title.value = data.title.slice(0, title.maxLength || 240);
    }
    if (typeof data.description === "string" && !description.value) {
      description.value = data.description.slice(0, 10000);
    }
    scheduleSize();
  }

  function notifyParent(action, result) {
    window.parent.postMessage(
      {
        v: companion.PROTOCOL,
        source: "framenest-save-popup",
        action: action,
        result: result || null,
      },
      "*"
    );
  }

  function displayNameOf(item) {
    if (item && typeof item.display_name === "string" && item.display_name) {
      return item.display_name;
    }
    return item && typeof item.key === "string" ? item.key : "";
  }

  function selectedKeys() {
    return chosen.map((item) => item.key);
  }

  function matchingTags(query) {
    const needle = query.toLocaleLowerCase();
    if (!needle) {
      return [];
    }
    const selected = selectedKeys();
    return catalog
      .filter((item) => {
        if (selected.indexOf(item.key) !== -1) {
          return false;
        }
        const name = displayNameOf(item).toLocaleLowerCase();
        const key = item.key.toLocaleLowerCase();
        return name.indexOf(needle) !== -1 || key.indexOf(needle) !== -1;
      })
      .slice(0, SUGGESTION_LIMIT);
  }

  function tagListOpen() {
    return tagSearch.getAttribute("aria-expanded") === "true";
  }

  function closeTagList() {
    suggestions.replaceChildren();
    activeSuggestion = -1;
    tagSearch.setAttribute("aria-expanded", "false");
  }

  function setFormBusy(busy) {
    formBusy = busy;
    if (busy) {
      form.setAttribute("aria-busy", "true");
    } else {
      form.removeAttribute("aria-busy");
    }
    title.disabled = busy;
    description.disabled = busy;
    tagSearch.disabled = busy;
    saveButton.disabled = busy;
    renderSelected();
  }

  function renderSelected() {
    selectedTags.replaceChildren();
    chosen.forEach((item) => {
      const chip = document.createElement("span");
      chip.className = "tag-chip";
      const label = document.createElement("span");
      label.textContent = displayNameOf(item);
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "tag-chip__remove";
      remove.textContent = "×";
      remove.setAttribute("aria-label", "Remove " + displayNameOf(item));
      remove.disabled = formBusy;
      remove.addEventListener("click", () => {
        if (formBusy) {
          return;
        }
        const index = chosen.findIndex((entry) => entry.key === item.key);
        if (index >= 0) {
          chosen.splice(index, 1);
          renderSelected();
          renderSuggestions();
        }
      });
      chip.appendChild(label);
      chip.appendChild(remove);
      selectedTags.appendChild(chip);
    });
    scheduleSize();
  }

  function renderSuggestions() {
    suggestions.replaceChildren();
    const matches = matchingTags(tagSearch.value.trim());
    if (!matches.length) {
      activeSuggestion = -1;
      tagSearch.setAttribute("aria-expanded", "false");
      return;
    }
    if (activeSuggestion < 0 || activeSuggestion >= matches.length) {
      activeSuggestion = 0;
    }
    tagSearch.setAttribute("aria-expanded", "true");
    matches.forEach((item, index) => {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "tag-suggestion";
      button.id = "tag-suggestion-" + String(index);
      button.setAttribute("role", "option");
      button.setAttribute("aria-selected", String(index === activeSuggestion));
      button.textContent = displayNameOf(item);
      button.disabled = formBusy;
      button.addEventListener("mousedown", (event) => {
        event.preventDefault();
      });
      button.addEventListener("click", () => {
        addTag(item);
      });
      suggestions.appendChild(button);
    });
  }

  function addTag(item) {
    if (formBusy || !item || typeof item.key !== "string") {
      return;
    }
    if (chosen.some((entry) => entry.key === item.key)) {
      return;
    }
    if (chosen.length >= TAG_LIMIT) {
      setStatus(tagsStatus, "Tag limit reached.", "error");
      return;
    }
    chosen.push({ key: item.key, display_name: displayNameOf(item) });
    tagSearch.value = "";
    activeSuggestion = -1;
    setStatus(tagsStatus, "");
    renderSelected();
    renderSuggestions();
  }

  function aliasPayload() {
    const payload = {};
    const titleValue = companion.canonicalizeCompanionAliasTitle(title.value);
    const descriptionValue = description.value.trim();
    const tagKeys = selectedKeys();
    if (titleValue) {
      payload.display_title = titleValue;
    }
    if (descriptionValue) {
      payload.description = descriptionValue;
    }
    if (tagKeys.length) {
      payload.tag_keys = tagKeys;
    }
    return payload;
  }

  function failMessage(result) {
    if (result && result.error === "X_REQUEST_INVALID_CATEGORY") {
      return UPGRADE_MESSAGE;
    }
    if (result && result.error === "X_REQUEST_CATEGORY_CONFLICT") {
      return "Save failed. A different category is already stored for this post.";
    }
    if (result && result.error === "invalid_category") {
      return UPGRADE_MESSAGE;
    }
    return "Save failed.";
  }

  function submitSave() {
    if (formBusy || runtimeStale) {
      return;
    }
    const url = submittedUrl();
    if (!url) {
      setStatus(formStatus, "Invalid post URL.", "error");
      return;
    }
    setFormBusy(true);
    setStatus(formStatus, "Saving…");
    request(companion.TYPES.SAVE_POST, {
      url: url,
      alias: aliasPayload(),
    }).then((result) => {
      if (runtimeStale || (result && result.stale === true)) {
        return;
      }
      if (!result.ok) {
        setFormBusy(false);
        setStatus(formStatus, failMessage(result), "error");
        notifyParent("result", result);
        return;
      }
      notifyParent("result", result);
    });
  }

  async function loadTags() {
    const response = await request(companion.TYPES.CANONICAL_TAGS, {});
    if (runtimeStale || (response && response.stale === true)) {
      return;
    }
    if (!response.ok) {
      setStatus(tagsStatus, "Tags unavailable.", "error");
      return;
    }
    const list = (response.body && response.body.tags) || [];
    catalog.length = 0;
    (Array.isArray(list) ? list : []).forEach((item) => {
      if (item && typeof item.key === "string") {
        catalog.push({ key: item.key, display_name: displayNameOf(item) });
      }
    });
    preselectCompanionXTag();
    setStatus(tagsStatus, catalog.length ? "" : "No canonical tags yet.");
    renderSelected();
    renderSuggestions();
    scheduleSize();
  }

  function preselectCompanionXTag() {
    if (chosen.some((entry) => entry.key === COMPANION_X_TAG_KEY)) {
      return;
    }
    const match = catalog.find(
      (item) =>
        item.key === COMPANION_X_TAG_KEY &&
        item.display_name === COMPANION_X_TAG_DISPLAY_NAME
    );
    if (!match) {
      return;
    }
    chosen.unshift({
      key: match.key,
      display_name: match.display_name,
    });
  }

  form.addEventListener("submit", (event) => {
    event.preventDefault();
    submitSave();
  });

  form.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submitSave();
    }
  });

  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.ctrlKey && !event.metaKey) {
      event.preventDefault();
      submitSave();
    }
  });

  closeButton.addEventListener("click", () => {
    notifyParent("cancel");
  });

  tagSearch.addEventListener("input", () => {
    activeSuggestion = -1;
    renderSuggestions();
  });

  tagSearch.addEventListener("keydown", (event) => {
    const matches = matchingTags(tagSearch.value.trim());
    if (event.key === "ArrowDown" && matches.length) {
      event.preventDefault();
      activeSuggestion = (activeSuggestion + 1) % matches.length;
      renderSuggestions();
      return;
    }
    if (event.key === "ArrowUp" && matches.length) {
      event.preventDefault();
      activeSuggestion = activeSuggestion <= 0 ? matches.length - 1 : activeSuggestion - 1;
      renderSuggestions();
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      if (tagListOpen() && activeSuggestion >= 0) {
        const item = matches[activeSuggestion];
        if (item) {
          addTag(item);
          return;
        }
      }
      submitSave();
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") {
      return;
    }
    event.preventDefault();
    if (tagListOpen()) {
      closeTagList();
      return;
    }
    notifyParent("cancel");
  });

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Enter") {
        return;
      }
      if (event.isComposing) {
        return;
      }
      if (formBusy) {
        event.preventDefault();
        return;
      }
      const target = event.target;
      if (target === description && !event.ctrlKey && !event.metaKey) {
        return;
      }
      if (target === tagSearch && tagListOpen() && activeSuggestion >= 0) {
        return;
      }
      event.preventDefault();
      submitSave();
    },
    true
  );

  window.addEventListener("message", (event) => {
    const data = event.data;
    if (!data || data.v !== companion.PROTOCOL || data.source !== "framenest-save-host") {
      return;
    }
    if (data.action === "prefill") {
      applyPrefill(data);
      return;
    }
    if (data.action === "submit") {
      submitSave();
    }
  });

  if (typeof ResizeObserver === "function" && form) {
    const observer = new ResizeObserver(() => {
      notifySize();
    });
    observer.observe(form);
    if (selectedTags) {
      observer.observe(selectedTags);
    }
  }

  void loadTags();
  notifyParent("ready");
  scheduleSize();
})();
