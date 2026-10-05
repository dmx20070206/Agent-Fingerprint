/*
 * Browser-side UI trace probe.
 *
 * The sandbox server injects this file into its HTML responses.  It has no
 * bundler dependency, keeps an in-page copy, and—when an endpoint is supplied
 * through __AGENT_FINGERPRINT_CONFIG__—batches events back to TraceStore.
 *
 * Example (Playwright):
 *   await page.addInitScript({ path: "probes/inject_monitor.js" });
 *   // ... run the Agent ...
 *   const trace = await page.evaluate(() =>
 *     window.__agentFingerprintMonitor.export()
 *   );
 */
(function installAgentFingerprintMonitor(global) {
  "use strict";

  // Do not install duplicate listeners if a browser context injects the script
  // for every navigation.
  if (global.__agentFingerprintMonitor) {
    return;
  }

  // Zero-configuration probe: collect every supported interaction event.
  var EVENTS = [
    "click", "dblclick", "contextmenu", "mousedown", "mouseup",
    "pointerdown", "pointerup", "pointercancel", "pointerover", "pointerout",
    "input", "change", "submit", "reset", "focus", "blur", "keydown", "keyup", "paste", "wheel",
    "touchstart", "touchend", "touchmove", "selectionchange"
  ];
  var MAX_BUFFER = 5000;
  var FLUSH_DELAY_MS = 250;
  var UPLOAD_TIMEOUT_MS = 3000;
  var CONTROL_POLL_MS = 500;
  // Keep individual keepalive/beacon bodies comfortably below the browser's
  // shared ~64 KiB in-flight budget, even for events with a populated target.
  var MAX_BATCH_EVENTS = 50;
  var PROTOCOL_VERSION = 2;
  var POINTER_MOVE_THROTTLE_MS = 16;
  var SCROLL_THROTTLE_MS = 100;
  var events = [];
  var listeners = [];
  var scrollTimer = null;
  var pointerMoveTimer = null;
  var pendingPointerMove = null;
  var startedAt = null;
  var sequence = 0;
  var active = false;
  var pageHideSent = false;
  var sessionId = "s-" + Math.random().toString(36).slice(2) + "-" + Date.now();
  var sentThrough = 0;
  var flushTimer = null;
  var flushInFlight = null;
  var controlTimer = null;
  var controlInFlight = null;
  var finalizing = false;
  var finalAcknowledged = false;
  var finalSeq = null;
  var lastUploadError = null;
  var interactionGate = null;
  var interactionGateTimer = null;
  var interactionCountdownTimer = null;
  var interactionGateStartedAt = null;
  var interactionLoadListener = null;
  var interactionBlockers = [];
  var BLOCKED_DURING_STARTUP = [
    "click", "dblclick", "contextmenu", "mousedown", "mouseup", "mousemove",
    "pointerdown", "pointerup", "pointermove", "pointerover", "pointerout",
    "pointerenter", "pointerleave", "touchstart", "touchend", "touchmove",
    "wheel", "keydown", "keyup", "keypress", "beforeinput", "input", "change",
    "submit", "reset", "paste", "dragstart", "dragover", "drop"
  ];

  function scheduleFlush() {
    var pendingFinalAck = finalizing && !finalAcknowledged;
    if (flushTimer !== null || flushInFlight || (sentThrough >= events.length && !pendingFinalAck)) return;
    flushTimer = global.setTimeout(function () {
      flushTimer = null;
      fetchFlush();
    }, FLUSH_DELAY_MS);
  }

  function fetchWithTimeout(url, options) {
    var controller = global.AbortController ? new global.AbortController() : null;
    if (controller) options.signal = controller.signal;
    return new Promise(function (resolve, reject) {
      var settled = false;
      var timer = global.setTimeout(function () {
        if (settled) return;
        settled = true;
        if (controller) controller.abort();
        reject(new Error("trace collector request timed out"));
      }, UPLOAD_TIMEOUT_MS);
      global.fetch(url, options).then(function (response) {
        if (settled) return;
        settled = true;
        global.clearTimeout(timer);
        resolve(response);
      }, function (error) {
        if (settled) return;
        settled = true;
        global.clearTimeout(timer);
        reject(error);
      });
    });
  }

  function fetchFlush() {
    var config = endpointConfig();
    var pendingFinalAck = finalizing && !finalAcknowledged;
    if (!config.endpoint || !global.fetch || (sentThrough >= events.length && !pendingFinalAck)) return null;
    if (flushInFlight) return flushInFlight;
    var batchStart = sentThrough;
    var batchEnd = Math.min(events.length, batchStart + MAX_BATCH_EVENTS);
    var sendsFinalMarker = finalizing && batchEnd >= finalSeq;
    var body = JSON.stringify({
      protocol_version: PROTOCOL_VERSION,
      run_id: config.run_id,
      session_id: sessionId,
      events: events.slice(batchStart, batchEnd),
      final: sendsFinalMarker,
      final_seq: sendsFinalMarker ? finalSeq : null
    });
    flushInFlight = fetchWithTimeout(config.endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body,
      keepalive: true
    }).then(function (response) {
      if (!response.ok) throw new Error("trace collector returned " + response.status);
      return response.json();
    }).then(function (acknowledgement) {
      var ackSeq = Number(acknowledgement && acknowledgement.ack_seq);
      if (!isFinite(ackSeq) || ackSeq < 0) throw new Error("trace collector returned an invalid ACK");
      sentThrough = Math.max(sentThrough, Math.min(Math.floor(ackSeq), events.length));
      if (acknowledgement.finalized) finalAcknowledged = true;
      if (acknowledgement.finalize_requested && !finalizing) stop(false);
      lastUploadError = null;
      return true;
    }).catch(function (error) {
      lastUploadError = error && error.message ? error.message : String(error);
      return false;
    }).then(function (result) {
      flushInFlight = null;
      if (sentThrough < events.length || (finalizing && !finalAcknowledged)) scheduleFlush();
      return result;
    });
    return flushInFlight;
  }

  function beaconFlush(markFinal) {
    var config = endpointConfig();
    if (!config.endpoint || (!markFinal && sentThrough >= events.length) || !global.navigator || !navigator.sendBeacon) return false;
    var acceptedAll = true;
    var batchStart = sentThrough;
    do {
      var batchEnd = Math.min(events.length, batchStart + MAX_BATCH_EVENTS);
      var sendsFinalMarker = !!markFinal && batchEnd >= events.length;
      var body = JSON.stringify({
        protocol_version: PROTOCOL_VERSION,
        run_id: config.run_id,
        session_id: sessionId,
        events: events.slice(batchStart, batchEnd),
        final: sendsFinalMarker,
        final_seq: sendsFinalMarker ? events.length : null
      });
      try {
        if (!navigator.sendBeacon(config.endpoint, new Blob([body], { type: "application/json" }))) {
          acceptedAll = false;
          break;
        }
      } catch (_) {
        acceptedAll = false;
        break;
      }
      batchStart = batchEnd;
    } while (batchStart < events.length);
    // sendBeacon() only confirms that the browser queued the request.  The
    // normal POST path is the sole owner of sentThrough because it receives a
    // server-side sequence ACK.
    return acceptedAll;
  }

  function controlUrl(config) {
    var separator = config.endpoint.indexOf("?") === -1 ? "?" : "&";
    return config.endpoint + separator +
      "control=1&protocol_version=" + PROTOCOL_VERSION +
      "&run_id=" + encodeURIComponent(config.run_id || "") +
      "&session_id=" + encodeURIComponent(sessionId);
  }

  function pollControl() {
    var config = endpointConfig();
    if (!config.endpoint || !global.fetch || controlInFlight || finalAcknowledged) return;
    controlInFlight = fetchWithTimeout(controlUrl(config), {
      method: "GET",
      cache: "no-store"
    }).then(function (response) {
      if (!response.ok) throw new Error("trace control returned " + response.status);
      return response.json();
    }).then(function (control) {
      var ackSeq = Number(control && control.ack_seq);
      if (isFinite(ackSeq) && ackSeq >= 0) {
        sentThrough = Math.max(sentThrough, Math.min(Math.floor(ackSeq), events.length));
      }
      if (control && control.finalized) finalAcknowledged = true;
      if (control && control.finalize_requested && !finalizing) stop(false);
      return true;
    }).catch(function () {
      return false;
    }).then(function (result) {
      controlInFlight = null;
      if (finalAcknowledged && controlTimer !== null) {
        global.clearInterval(controlTimer);
        controlTimer = null;
      }
      return result;
    });
  }

  function now() {
    return {
      epoch_ms: Date.now(),
      monotonic_ms: Math.round((global.performance && performance.now ? performance.now() : 0) * 1000) / 1000
    };
  }

  function cssPath(element) {
    if (!element || element.nodeType !== 1) return null;
    if (element.id) return "#" + element.id;
    var parts = [];
    var current = element;
    while (current && current.nodeType === 1 && parts.length < 5) {
      var name = current.tagName.toLowerCase();
      var parent = current.parentElement;
      if (parent) {
        var same = Array.prototype.filter.call(parent.children, function (child) {
          return child.tagName === current.tagName;
        });
        if (same.length > 1) name += ":nth-of-type(" + (same.indexOf(current) + 1) + ")";
      }
      parts.unshift(name);
      current = parent;
    }
    return parts.join(" > ");
  }

  function elementInfo(element) {
    if (!element || element.nodeType !== 1) return null;
    var rect = typeof element.getBoundingClientRect === "function" ? element.getBoundingClientRect() : null;
    var info = {
      tag: element.tagName.toLowerCase(),
      href: element.getAttribute("href"),
      id: element.id || null,
      role: element.getAttribute("role"),
      name: element.getAttribute("name"),
      type: element.getAttribute("type"),
      aria_label: element.getAttribute("aria-label"),
      text: (element.innerText || element.textContent || "").trim().slice(0, 160),
      css_path: cssPath(element)
    };
    if (rect) {
      info.rect = {
        x: Math.round(rect.x), y: Math.round(rect.y),
        width: Math.round(rect.width), height: Math.round(rect.height)
      };
    }
    return info;
  }

  // A privacy-preserving snapshot of browser properties that are stable for a
  // context.  No page text, cookies, storage values, or form contents are
  // included.  The snapshot is attached to monitor_start so the dynamic
  // event stream remains backwards compatible.
  function staticFingerprint() {
    var nav = global.navigator || {};
    var screen = global.screen || {};
    var timezone = null;
    try { timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (_) {}
    var webgl = {};
    try {
      var canvas = document.createElement("canvas");
      var context = canvas.getContext("webgl") || canvas.getContext("experimental-webgl");
      if (context) {
        var debug = context.getExtension("WEBGL_debug_renderer_info");
        webgl.vendor = debug ? context.getParameter(debug.UNMASKED_VENDOR_WEBGL) : null;
        webgl.renderer = debug ? context.getParameter(debug.UNMASKED_RENDERER_WEBGL) : null;
      }
    } catch (_) {}
    return {
      user_agent: nav.userAgent || null,
      platform: nav.platform || null,
      language: nav.language || null,
      languages: Array.isArray(nav.languages) ? nav.languages.slice(0, 16) : [],
      hardware_concurrency: nav.hardwareConcurrency || null,
      device_memory_gb: nav.deviceMemory || null,
      max_touch_points: nav.maxTouchPoints || 0,
      cookie_enabled: !!nav.cookieEnabled,
      do_not_track: nav.doNotTrack == null ? null : String(nav.doNotTrack),
      webdriver: nav.webdriver == null ? null : !!nav.webdriver,
      screen: {
        width: screen.width || null, height: screen.height || null,
        avail_width: screen.availWidth || null, avail_height: screen.availHeight || null,
        color_depth: screen.colorDepth || null, pixel_depth: screen.pixelDepth || null
      },
      viewport: { width: global.innerWidth || null, height: global.innerHeight || null, device_pixel_ratio: global.devicePixelRatio || 1 },
      timezone: timezone,
      locale: (global.document && document.documentElement && document.documentElement.lang) || null,
      webgl: webgl
    };
  }

  function record(type, data) {
    // Pointer input is treated as mouse input by the browser fingerprint
    // schema.  Keep one canonical vocabulary in the raw trace as well.
    if (type === "pointermove") type = "mousemove";
    else if (type === "pointerdown") type = "mousedown";
    else if (type === "pointerup" || type === "pointercancel") type = "mouseup";
    var stamp = now();
    var event = {
      seq: ++sequence,
      type: type,
      epoch_ms: stamp.epoch_ms,
      monotonic_ms: stamp.monotonic_ms,
      elapsed_ms: startedAt === null ? null : Math.round((stamp.monotonic_ms - startedAt) * 1000) / 1000,
      url: global.location ? global.location.href : null
    };
    if (data) {
      Object.keys(data).forEach(function (key) { event[key] = data[key]; });
    }
    events.push(event);

    // console log
    var ignoredTypes = [];
    if (ignoredTypes.indexOf(type) === -1) {
      console.log(
        "%c[Trace Monitor]%c " + type, 
        "color: white; background: #007acc; padding: 2px 4px; border-radius: 3px;", 
        "color: #007acc; font-weight: bold;", 
        event
      );
    }
    // Browser automation often closes Chromium without dispatching pagehide.
    // Persist incrementally so process teardown cannot discard the whole run.
    if (events.length - sentThrough >= MAX_BUFFER) beaconFlush(false);
    scheduleFlush();
    return event;
  }

  function endpointConfig() {
    var config = global.__AGENT_FINGERPRINT_CONFIG__ || {};
    return {
      endpoint: config.endpoint || null,
      run_id: config.run_id || null,
      interaction_delay_ms: Number(config.interaction_delay_ms) || 0
    };
  }

  function blockStartupInteraction(event) {
    if (event.cancelable) event.preventDefault();
    event.stopPropagation();
    if (event.stopImmediatePropagation) event.stopImmediatePropagation();
  }

  function removeInteractionBlockers() {
    interactionBlockers.forEach(function (item) {
      global.removeEventListener(item.name, item.listener, true);
    });
    interactionBlockers = [];
  }

  function cancelInteractionGate() {
    if (interactionGateTimer !== null) {
      global.clearTimeout(interactionGateTimer);
      interactionGateTimer = null;
    }
    if (interactionCountdownTimer !== null) {
      global.clearInterval(interactionCountdownTimer);
      interactionCountdownTimer = null;
    }
    if (interactionLoadListener !== null) {
      global.removeEventListener("load", interactionLoadListener, true);
      interactionLoadListener = null;
    }
    removeInteractionBlockers();
    if (interactionGate && interactionGate.parentNode) interactionGate.parentNode.removeChild(interactionGate);
    interactionGate = null;
  }

  function releaseInteractionGate(delayMs) {
    var waitedMs = interactionGateStartedAt === null ? delayMs : Math.max(0, Date.now() - interactionGateStartedAt);
    cancelInteractionGate();
    global.__AGENT_FINGERPRINT_READY__ = true;
    global.__AGENT_FINGERPRINT_READY_AT__ = Date.now();
    if (active) record("interaction_ready", { configured_delay_ms: delayMs, waited_ms: waitedMs });
    try {
      global.dispatchEvent(new CustomEvent("agent-fingerprint-ready", {
        detail: { configured_delay_ms: delayMs, waited_ms: waitedMs }
      }));
    } catch (_) {}
  }

  function installInteractionGate() {
    var delayMs = Math.max(0, endpointConfig().interaction_delay_ms);
    if (!delayMs) {
      global.__AGENT_FINGERPRINT_READY__ = true;
      global.__AGENT_FINGERPRINT_READY_AT__ = Date.now();
      return;
    }
    global.__AGENT_FINGERPRINT_READY__ = false;
    global.__AGENT_FINGERPRINT_READY_AT__ = null;

    BLOCKED_DURING_STARTUP.forEach(function (name) {
      global.addEventListener(name, blockStartupInteraction, { capture: true, passive: false });
      interactionBlockers.push({ name: name, listener: blockStartupInteraction });
    });

    interactionGate = document.createElement("div");
    interactionGate.id = "agent-fingerprint-interaction-gate";
    interactionGate.setAttribute("role", "status");
    interactionGate.setAttribute("aria-live", "polite");
    interactionGate.style.cssText = [
      "position:fixed!important", "inset:0!important", "z-index:2147483647!important",
      "display:flex!important", "align-items:center!important", "justify-content:center!important",
      "background:rgba(10,14,20,.88)!important", "color:#fff!important", "pointer-events:auto!important",
      "font-family:system-ui,-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif!important"
    ].join(";");
    var panel = document.createElement("div");
    panel.style.cssText = [
      "max-width:520px!important", "padding:28px 34px!important", "border:1px solid rgba(255,255,255,.3)!important",
      "border-radius:12px!important", "background:#151b24!important", "box-shadow:0 20px 70px #0009!important",
      "text-align:center!important", "font-size:18px!important", "line-height:1.6!important"
    ].join(";");
    var title = document.createElement("strong");
    title.style.cssText = "display:block!important;font-size:24px!important;margin-bottom:8px!important";
    title.textContent = "页面正在初始化";
    var countdown = document.createElement("span");
    var countdownText = document.createTextNode("");
    countdown.appendChild(countdownText);
    panel.appendChild(title);
    panel.appendChild(countdown);
    interactionGate.appendChild(panel);
    (document.documentElement || document).appendChild(interactionGate);

    function updateCountdown() {
      if (interactionGateStartedAt === null) {
        countdownText.nodeValue = "正在等待页面加载完成";
        return;
      }
      var remaining = Math.max(0, global.__AGENT_FINGERPRINT_READY_AT__ - Date.now());
      // MutationObserver intentionally does not watch characterData, so this
      // countdown does not add five synthetic DOM-mutation events per second
      // to the behavioral fingerprint.
      countdownText.nodeValue = "Agent 将在 " + Math.ceil(remaining / 1000) + " 秒后开始操作";
    }
    updateCountdown();
    function beginCountdown() {
      if (interactionGateStartedAt !== null) return;
      interactionGateStartedAt = Date.now();
      global.__AGENT_FINGERPRINT_READY_AT__ = interactionGateStartedAt + delayMs;
      updateCountdown();
      interactionCountdownTimer = global.setInterval(updateCountdown, 200);
      interactionGateTimer = global.setTimeout(function () { releaseInteractionGate(delayMs); }, delayMs);
    }
    if (document.readyState === "complete") beginCountdown();
    else {
      interactionLoadListener = beginCountdown;
      global.addEventListener("load", interactionLoadListener, true);
    }
  }

  function targetOf(event) {
    return event.target && event.target.nodeType === 1 ? elementInfo(event.target) : null;
  }

  function onDomEvent(event) {
    if (!active) return;
    var data = { target: targetOf(event) };
    if (event.type === "click" || event.type === "dblclick" || event.type === "mousedown" || event.type === "mouseup" || event.type.indexOf("pointer") === 0) {
      data.button = typeof event.button === "number" ? event.button : null;
      data.client_x = typeof event.clientX === "number" ? event.clientX : null;
      data.client_y = typeof event.clientY === "number" ? event.clientY : null;
      data.detail = typeof event.detail === "number" ? event.detail : null;
    }
    if (event.type === "input" || event.type === "change") {
      // Never record a form value: it may contain a password or personal data.
      data.input_type = event.target && event.target.type ? event.target.type : null;
      data.value_length = event.target && typeof event.target.value === "string" ? event.target.value.length : null;
    }
    if (event.type === "keydown" || event.type === "keyup") {
      data.key = event.key;
      data.code = event.code;
      data.modifiers = { alt: event.altKey, ctrl: event.ctrlKey, meta: event.metaKey, shift: event.shiftKey };
    }
    if (event.type.indexOf("touch") === 0) {
      var touch = event.touches && event.touches[0] || event.changedTouches && event.changedTouches[0];
      if (touch) {
        data.client_x = touch.clientX;
        data.client_y = touch.clientY;
        data.touch_count = event.touches ? event.touches.length : 0;
      }
    }
    if (event.type === "selectionchange") {
      var selection = global.getSelection && global.getSelection();
      data.selection_length = selection ? selection.toString().length : 0;
    }
    if (event.type === "paste") {
      var clipboard = event.clipboardData;
      var pasted = clipboard && clipboard.getData ? clipboard.getData("text") : "";
      data.value_length = pasted.length;
    }
    if (event.type === "wheel") {
      data.delta_x = event.deltaX;
      data.delta_y = event.deltaY;
      data.delta_mode = event.deltaMode;
    }
    if (event.type === "resize") {
      data.viewport_width = global.innerWidth || null;
      data.viewport_height = global.innerHeight || null;
    }
    record(event.type, data);
  }

  function onScroll() {
    if (!active || scrollTimer !== null) return;
    scrollTimer = global.setTimeout(function () {
      scrollTimer = null;
      record("scroll", {
        x: global.scrollX || 0,
        y: global.scrollY || 0,
        viewport_width: global.innerWidth || null,
        viewport_height: global.innerHeight || null,
        document_height: document.documentElement ? document.documentElement.scrollHeight : null
      });
    }, SCROLL_THROTTLE_MS);
  }

  function onPointerMove(event) {
    if (!active) return;
    // Keep only the newest sample in each throttle window.  This preserves a
    // useful cursor trajectory without flooding the collector at display
    // refresh rate.
    pendingPointerMove = {
      client_x: typeof event.clientX === "number" ? event.clientX : null,
      client_y: typeof event.clientY === "number" ? event.clientY : null,
      pointer_type: event.pointerType || "mouse",
      buttons: typeof event.buttons === "number" ? event.buttons : null,
      target: targetOf(event)
    };
    if (pointerMoveTimer !== null) return;
    pointerMoveTimer = global.setTimeout(function () {
      pointerMoveTimer = null;
      if (pendingPointerMove) record("mousemove", pendingPointerMove);
      pendingPointerMove = null;
    }, POINTER_MOVE_THROTTLE_MS);
  }

  // Some automation stacks emit mousemove rather than pointermove.  Normalize
  // both into the same throttled trajectory stream so either browser API is
  // represented in the raw trace.
  function onMouseMove(event) {
    if (!active) return;
    onPointerMove(event);
  }

  function start() {
    if (active) return api;
    if (startedAt !== null) sessionId = "s-" + Math.random().toString(36).slice(2) + "-" + Date.now();
    events = [];
    sequence = 0;
    sentThrough = 0;
    finalizing = false;
    finalAcknowledged = false;
    finalSeq = null;
    lastUploadError = null;
    var stamp = now();
    startedAt = stamp.monotonic_ms;
    pageHideSent = false;
    active = true;
    EVENTS.forEach(function (name) {
      var listener = onDomEvent;
      document.addEventListener(name, listener, true);
      listeners.push({ target: document, name: name, listener: listener });
    });
    // Window-level events are not reliably delivered through document.
    ["resize"].forEach(function (name) {
      global.addEventListener(name, onDomEvent, true);
      listeners.push({ target: global, name: name, listener: onDomEvent });
    });
    global.addEventListener("scroll", onScroll, true);
    listeners.push({ target: global, name: "scroll", listener: onScroll });
    if (global.PointerEvent) {
      document.addEventListener("pointermove", onPointerMove, true);
      listeners.push({ target: document, name: "pointermove", listener: onPointerMove });
    } else {
      document.addEventListener("mousemove", onMouseMove, true);
      listeners.push({ target: document, name: "mousemove", listener: onMouseMove });
    }
    // DOM mutation events are intentionally not part of the behavioral trace.
    // Do not install a MutationObserver: page/framework rendering would
    // otherwise dominate the interaction signal.
    record("monitor_start", { static_fingerprint: staticFingerprint(), navigation_tracking: true });
    if (controlTimer !== null) global.clearInterval(controlTimer);
    controlTimer = global.setInterval(pollControl, CONTROL_POLL_MS);
    pollControl();
    return api;
  }

  function stop(useBeacon) {
    if (!active) {
      if (finalizing && !finalAcknowledged) fetchFlush();
      return api;
    }
    cancelInteractionGate();
    if (scrollTimer !== null) {
      global.clearTimeout(scrollTimer);
      scrollTimer = null;
    }
    if (pointerMoveTimer !== null) {
      global.clearTimeout(pointerMoveTimer);
      pointerMoveTimer = null;
      if (pendingPointerMove) record("mousemove", pendingPointerMove);
      pendingPointerMove = null;
    }
    listeners.forEach(function (item) { item.target.removeEventListener(item.name, item.listener, true); });
    listeners = [];
    record("monitor_stop");
    active = false;
    finalizing = true;
    finalSeq = events.length;
    if (flushTimer !== null) {
      global.clearTimeout(flushTimer);
      flushTimer = null;
    }
    if (controlTimer !== null) {
      global.clearInterval(controlTimer);
      controlTimer = null;
    }
    fetchFlush();
    if (useBeacon) beaconFlush(true);
    return api;
  }

  function onPageHide() {
    if (!active || pageHideSent) return;
    pageHideSent = true;
    // Navigation often happens before the 250 ms timer fires.  Beacon is
    // specifically designed to survive document teardown and is preferable
    // to waiting on a normal fetch during unload.
    var root = document.documentElement;
    var maxScroll = root ? Math.max(0, (root.scrollHeight || 0) - (global.innerHeight || 0)) : 0;
    record("beforeunload", {
      scroll_pct: maxScroll > 0 ? Math.max(0, Math.min(1, (global.scrollY || 0) / maxScroll)) * 100 : null
    });
    stop(true);
  }

  function getEvents() {
    return events.map(function (event) { return Object.assign({}, event); });
  }

  function clear() {
    if (flushTimer !== null) {
      global.clearTimeout(flushTimer);
      flushTimer = null;
    }
    events = [];
    sequence = 0;
    sentThrough = 0;
    finalizing = false;
    finalAcknowledged = false;
    finalSeq = null;
    lastUploadError = null;
    return api;
  }

  function exportTrace() {
    return {
      schema: "agent-fingerprint-ui-trace/v1",
      started: startedAt !== null,
      active: active,
      session_id: sessionId,
      run_id: endpointConfig().run_id,
      url: global.location ? global.location.href : null,
      event_count: events.length,
      acknowledged_seq: sentThrough,
      pending_event_count: Math.max(0, events.length - sentThrough),
      finalizing: finalizing,
      finalized: finalAcknowledged,
      last_upload_error: lastUploadError,
      events: getEvents()
    };
  }

  var api = {
    start: start,
    stop: stop,
    clear: clear,
    flush: fetchFlush,
    getEvents: getEvents,
    export: exportTrace,
    isActive: function () { return active; }
  };
  global.__agentFingerprintMonitor = api;
  global.logEvent = function (type, data) { record(type, data || {}); };
  global.addLog = function (logId, eventType, detail) {
    record("app_event", { log_id: logId, event: eventType, detail: detail });
  };
  global.clearLog = function (logId) {
    record("app_event", { log_id: logId, event: "clear", detail: "log cleared" });
  };
  // Same-document history changes do not install a new document monitor.
  global.addEventListener("popstate", function () {
    if (active) record("navigation", { reason: "popstate" });
  }, true);
  if (global.history) {
    ["pushState", "replaceState"].forEach(function (name) {
      var original = global.history[name];
      if (typeof original !== "function") return;
      global.history[name] = function () {
        var before = global.location.href;
        var result = original.apply(this, arguments);
        if (active && global.location.href !== before) record("navigation", { reason: name });
        return result;
      };
    });
  }
  global.addEventListener("beforeunload", onPageHide, true);
  global.addEventListener("pagehide", onPageHide, true);
  document.addEventListener("visibilitychange", function () {
    if (!active) return;
    record("visibilitychange", { state: document.visibilityState });
    if (document.visibilityState === "hidden") beaconFlush(false);
  }, true);
  start();
  installInteractionGate();
})(window);
