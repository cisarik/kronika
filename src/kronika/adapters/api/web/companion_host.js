(function (root, factory) {
  const api = factory(root);
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  }
  root.KronikaCompanionWeb = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function (root) {
  // This cross-boundary protocol connects the served NUC host and extension.
  // Both sides emit and accept only the canonical spelling.
  const PROTOCOL = "kronika.companion.web.v1";
  const ACCEPTED_WEB_PROTOCOLS = Object.freeze([PROTOCOL]);
  const PINNED_EXTENSION_ORIGIN = "chrome-extension://omiihmnlkmieaafaphohakcgmbggppap";
  const UUID_PATTERN =
    /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;
  const TYPES = Object.freeze({
    WEB_READY: "web_ready",
    HOST_HELLO: "host_hello",
    HOST_ACK: "host_ack",
    ATTACH_REQUEST: "attach_request",
    ATTACH_RESULT: "attach_result",
    OPEN_DETAILS: "open_details",
  });

  function isUuid(value) {
    return typeof value === "string" && UUID_PATTERN.test(value);
  }

  function isPinnedExtensionOrigin(origin) {
    return origin === PINNED_EXTENSION_ORIGIN;
  }

  function createHost(env) {
    const win = env && env.window;
    const parentWindow = (env && env.parent) || (win && win.parent);
    const framed = Boolean(win && parentWindow && parentWindow !== win);
    const listeners = [];
    const openDetailsListeners = [];
    const pending = [];
    let hosted = false;
    let attachSeq = 0;

    function notifyHostedChange() {
      listeners.slice().forEach((listener) => {
        try {
          listener(hosted);
        } catch {
          // listener failure must not break the host
        }
      });
    }

    function setHosted(value) {
      if (hosted === value) {
        return;
      }
      hosted = value;
      notifyHostedChange();
    }

    function postToParent(message) {
      if (!parentWindow || parentWindow === win) {
        return;
      }
      parentWindow.postMessage(message, PINNED_EXTENSION_ORIGIN);
    }

    function onMessage(event) {
      if (!framed) {
        return;
      }
      if (!isPinnedExtensionOrigin(event.origin)) {
        return;
      }
      if (parentWindow && event.source !== parentWindow) {
        return;
      }
      const data = event.data;
      if (!data || typeof data !== "object" || !ACCEPTED_WEB_PROTOCOLS.includes(data.v) || typeof data.type !== "string") {
        return;
      }
      if (data.type === TYPES.HOST_HELLO) {
        setHosted(true);
        postToParent({ v: PROTOCOL, type: TYPES.HOST_ACK });
        return;
      }
      if (data.type === TYPES.OPEN_DETAILS) {
        const payload = data.payload && typeof data.payload === "object" ? data.payload : {};
        if (!isUuid(payload.mediaId)) {
          return;
        }
        openDetailsListeners.slice().forEach((listener) => {
          try {
            listener(payload.mediaId);
          } catch {
            // listener failure must not break the host
          }
        });
        return;
      }
      if (data.type === TYPES.ATTACH_RESULT) {
        const payload = data.payload && typeof data.payload === "object" ? data.payload : {};
        const waiter = pending.shift();
        if (waiter) {
          waiter.resolve({
            ok: payload.ok === true,
            error: typeof payload.error === "string" ? payload.error : undefined,
          });
        }
      }
    }

    function attach(mediaId, locationId) {
      if (!hosted) {
        return Promise.resolve({ ok: false, error: "not_hosted" });
      }
      if (!isUuid(mediaId) || !isUuid(locationId)) {
        return Promise.resolve({ ok: false, error: "invalid_attach" });
      }
      attachSeq += 1;
      void attachSeq;
      return new Promise((resolve) => {
        pending.push({ resolve });
        postToParent({
          v: PROTOCOL,
          type: TYPES.ATTACH_REQUEST,
          payload: { mediaId: mediaId, locationId: locationId },
        });
      });
    }

    function start() {
      if (!framed) {
        return;
      }
      if (typeof win.addEventListener === "function") {
        win.addEventListener("message", onMessage);
      }
      postToParent({ v: PROTOCOL, type: TYPES.WEB_READY });
    }

    if (env && typeof env.onOpenDetails === "function") {
      openDetailsListeners.push(env.onOpenDetails);
    }

    start();
    return {
      isHosted: function isHosted() {
        return hosted;
      },
      attach: attach,
      onHostedChange: function onHostedChange(listener) {
        if (typeof listener === "function") {
          listeners.push(listener);
        }
      },
      onOpenDetails: function onOpenDetails(listener) {
        if (typeof listener === "function") {
          openDetailsListeners.push(listener);
        }
      },
      handleMessage: onMessage,
    };
  }

  let defaultHost = null;
  const win = root.window || (typeof window !== "undefined" ? window : null);
  if (win && win.parent && win.parent !== win) {
    defaultHost = createHost({ window: win, parent: win.parent });
  }

  return {
    PROTOCOL,
    ACCEPTED_WEB_PROTOCOLS,
    TYPES,
    PINNED_EXTENSION_ORIGIN,
    isUuid,
    isPinnedExtensionOrigin,
    createHost,
    isHosted: function isHosted() {
      return Boolean(defaultHost && defaultHost.isHosted());
    },
    attach: function attach(mediaId, locationId) {
      if (!defaultHost) {
        return Promise.resolve({ ok: false, error: "not_hosted" });
      }
      return defaultHost.attach(mediaId, locationId);
    },
    onHostedChange: function onHostedChange(listener) {
      if (defaultHost) {
        defaultHost.onHostedChange(listener);
      }
    },
    onOpenDetails: function onOpenDetails(listener) {
      if (defaultHost) {
        defaultHost.onOpenDetails(listener);
      }
    },
  };
});
