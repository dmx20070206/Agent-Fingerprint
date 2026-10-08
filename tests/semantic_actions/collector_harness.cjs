const assert = require("assert");
const dom = {}, win = {};
let now = 0;
global.window = global;
Date.now = () => 100000 + now++;
Object.defineProperty(global, "performance", {value: {now: () => now}});
global.location = {href: "https://example.test/"};
global.addEventListener = (name, fn) => { (win[name] || (win[name] = [])).push(fn); };
global.removeEventListener = () => {};
global.setTimeout = () => 1;
global.setInterval = () => 1;
global.PointerEvent = function () {};
global.scrollX = global.scrollY = 0;
function node(tag, props = {}) {
  return Object.assign({nodeType: 1, tagName: tag.toUpperCase(), id: tag,
    getAttribute(name) {return name === "type" ? this.type || null : name === "role" ? this.role || null : null;},
    children: [], getBoundingClientRect: () => ({x: 0, y: 0, width: 10, height: 10})}, props);
}
const input = node("input", {type: "text", value: ""});
const toggle = node("input", {id: "toggle", type: "checkbox", value: "on", checked: false});
const select = node("select", {value: "a", selectedIndex: 0});
const button = node("button", {type: "button"});
const span = node("span", {parentElement: button});
const svg = node("svg", {parentElement: span});
button.children = [span]; span.children = [svg];
const panel = node("div", {scrollLeft: 0, scrollTop: 0});
global.document = {
  documentElement: {lang: "en", scrollHeight: 1000}, body: {},
  addEventListener: (name, fn) => { (dom[name] || (dom[name] = [])).push(fn); }, removeEventListener: () => {},
  createElement: () => ({getContext: () => null}),
  querySelectorAll: () => [input, toggle, select]
};
global.history = {pushState() {}, replaceState() {}};
console.log = () => {};
require(process.argv[2]);
function emit(type, target, extra = {}) {
  const event = Object.assign({type, target, button: 0, relatedTarget: null,
    composedPath() {const p = []; let n = target; while (n) {p.push(n); n = n.parentElement;} return p;}}, extra);
  for (const fn of (type === "scroll" ? win[type] : dom[type]) || []) fn(event);
}
emit("pointerdown", svg); emit("mousedown", svg); emit("pointerup", svg); emit("mouseup", svg); emit("click", svg);
emit("focus", input); emit("beforeinput", input); emit("compositionstart", input);
input.value = "SECRET";
emit("compositionupdate", input); emit("input", input); emit("compositionend", input); emit("change", input);
emit("click", toggle); toggle.checked = true; emit("input", toggle); emit("change", toggle);
emit("click", select); select.value = "b"; select.selectedIndex = 1; emit("input", select); emit("change", select);
global.scrollY = 20; emit("scroll", document); global.scrollY = 40; emit("scroll", document); emit("scrollend", document);
emit("scroll", panel); panel.scrollTop = 10; emit("scroll", panel); emit("scrollend", panel);
emit("pointerdown", button); emit("pointercancel", button); emit("click", button);
__agentFingerprintMonitor.stop();
const events = __agentFingerprintMonitor.getEvents();
assert(events[0].semantic_tracking);
process.stdout.write(JSON.stringify(events));
