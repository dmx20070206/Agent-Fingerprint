/* Semantic collector plugin for AF_MONITOR_EXTENSION_API:1. */
(function (root) {
root.__AF_MONITOR_EXTENSION__ = function (global, document, context) {
/* Inserted inside the existing monitor closure by collector.build_probe().
 * Values are compared in memory only. Text and hashes of text are never emitted.
 */
var semanticDocumentId = "d-" + Math.random().toString(36).slice(2) + "-" + Date.now();
var semanticIds = new WeakMap();
var semanticStates = new WeakMap();
var semanticScrolls = new WeakMap();
var semanticNextId = 0;
document.addEventListener("DOMContentLoaded", function () {
  // The sandbox injects the monitor in <head>, before most controls exist.
  if (context.isActive()) semanticPrime();
}, true);

function semanticId(node) {
  if (!node || (typeof node !== "object" && typeof node !== "function")) return null;
  if (!semanticIds.has(node)) semanticIds.set(node, "node-" + (++semanticNextId));
  return semanticIds.get(node);
}

function semanticNode(node) {
  if (node === global || node === document || node === document.documentElement || node === document.body) {
    return { tag: "document", node_id: "page" };
  }
  if (!node || node.nodeType !== 1) return null;
  return {
    node_id: semanticId(node), tag: node.tagName.toLowerCase(),
    type: node.type || node.getAttribute("type"), role: node.getAttribute("role"),
    href: node.getAttribute("href"), contenteditable: !!node.isContentEditable,
    form_id: node.form ? semanticId(node.form) : null
  };
}

function semanticPath(node) {
  var nodes = [];
  while (node) {
    var metadata = semanticNode(node);
    if (metadata) nodes.push(metadata);
    node = node.parentElement || node.host || null;
  }
  return nodes;
}

function semanticState(node) {
  return {
    value: typeof node.value === "string" ? node.value : node.isContentEditable ? node.textContent : null,
    checked: typeof node.checked === "boolean" ? node.checked : node.getAttribute("aria-checked"),
    selected_index: typeof node.selectedIndex === "number" ? node.selectedIndex : null
  };
}

function semanticPrime() {
  semanticScrolls.set(document, { x: global.scrollX || 0, y: global.scrollY || 0 });
  if (!document.querySelectorAll) return;
  document.querySelectorAll("input, textarea, select, [contenteditable], [role]").forEach(function (node) {
    semanticStates.set(node, semanticState(node));
  });
  // Baseline pre-existing containers without emitting events. For a container
  // inserted later, its first observed position remains an unknown baseline.
  document.querySelectorAll("*").forEach(function (node) {
    if (node.scrollHeight > node.clientHeight || node.scrollWidth > node.clientWidth) {
      semanticScrolls.set(node, { x: node.scrollLeft, y: node.scrollTop });
    }
  });
}

function semanticEventData(event) {
  var node = event.target;
  var path = typeof event.composedPath === "function" ? event.composedPath() : [];
  var data = { composed_path: path.length ? path.map(semanticNode).filter(Boolean) : semanticPath(node) };
  if ("relatedTarget" in event) {
    data.related_target = semanticNode(event.relatedTarget);
    data.related_composed_path = semanticPath(event.relatedTarget);
  }
  if (node && node.nodeType === 1) {
    data.form_id = node.form ? semanticId(node.form) : node.tagName.toLowerCase() === "form" ? semanticId(node) : null;
    if (event.submitter) data.submitter_id = semanticId(event.submitter);
    var old = semanticStates.get(node), current = semanticState(node);
    if (event.type === "input" || event.type === "change") {
      data.value_changed = old && current.value !== null && old.value !== null ? current.value !== old.value : null;
      data.checked_changed = old && current.checked !== null && old.checked !== null ? current.checked !== old.checked : null;
      data.checked = current.checked;
      data.selected_index = current.selected_index;
      // A click listener runs after the browser's default checkbox activation;
      // retain the pre-click snapshot until input/change commits the state.
      semanticStates.set(node, current);
    } else if (["focus", "pointerdown", "mousedown", "keydown", "beforeinput", "compositionstart"].indexOf(event.type) !== -1) {
      semanticStates.set(node, current);
    }
  }
  if (event.type === "scroll" || event.type === "scrollend") {
    var page = node === global || node === document || node === document.documentElement || node === document.body;
    var scrollNode = page ? document : node;
    var position = { x: page ? global.scrollX || 0 : node.scrollLeft, y: page ? global.scrollY || 0 : node.scrollTop };
    var previous = semanticScrolls.get(scrollNode);
    data.target = semanticNode(scrollNode);
    data.composed_path = [data.target];
    data.scroll_x = position.x;
    data.scroll_y = position.y;
    data.previous_scroll_x = previous ? previous.x : null;
    data.previous_scroll_y = previous ? previous.y : null;
    data.scroll_changed = previous ? previous.x !== position.x || previous.y !== position.y : null;
    data.viewport_width = global.innerWidth || null;
    data.viewport_height = global.innerHeight || null;
    data.document_height = document.documentElement ? document.documentElement.scrollHeight : null;
    semanticScrolls.set(scrollNode, position);
  }
  return data;
}

return {
  events: ["beforeinput", "compositionstart", "compositionupdate", "compositionend", "scrollend"],
  eventData: semanticEventData,
  scrollData: semanticEventData,
  enrichRecord: function (event, originalType) {
    event.document_id = semanticDocumentId;
    event.session_id = context.sessionId();
    event.semantic_event_type = originalType;
  },
  onStart: function () {
    semanticPrime();
    return {semantic_tracking: true, scroll_x: global.scrollX || 0, scroll_y: global.scrollY || 0};
  }
};
};
})(typeof window !== "undefined" ? window : globalThis);
