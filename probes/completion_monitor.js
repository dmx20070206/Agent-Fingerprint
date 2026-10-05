/* Observe workflow completion independently of the optional fingerprint probe. */
(function () {
  "use strict";
  if (window.top !== window) return;
  const config = JSON.parse(document.currentScript.dataset.completion);
  let sequence = 0;
  let latest = null;
  let acknowledged = 0;

  async function upload() {
    if (!latest || acknowledged >= latest.sequence) return;
    const message = latest;
    try {
      const response = await fetch(config.endpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(message),
        keepalive: true
      });
      if (response.ok && (await response.json()).ok) {
        acknowledged = Math.max(acknowledged, message.sequence);
      }
    } catch (_) {
      // Retry until acknowledged; sequence numbers make reordered retries safe.
    }
  }

  function observe() {
    const status = document.body.dataset.taskStatus || "unknown";
    if (latest && latest.status === status) return;
    latest = { run_id: config.run_id, document_id: config.document_id,
               sequence: ++sequence, status: status };
    upload();
  }

  function start() {
    if (!document.body) return;
    new MutationObserver(observe).observe(document.body, {
      attributes: true, attributeFilter: ["data-task-status"]
    });
    observe();
    window.setInterval(upload, 500);
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start, { once: true });
  } else {
    start();
  }
  window.addEventListener("pagehide", function () {
    if (latest) navigator.sendBeacon(config.endpoint,
      new Blob([JSON.stringify(latest)], { type: "application/json" }));
  });
}());
