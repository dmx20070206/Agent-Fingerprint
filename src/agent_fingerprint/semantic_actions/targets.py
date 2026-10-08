"""Structural target lifting; never infer intent from text, URLs, IDs or classes."""
from __future__ import annotations

import json

SEMANTIC_ROLES = {"LINK", "BUTTON", "TEXT_INPUT", "SELECT", "TOGGLE"}


def attr(node, *names):
    attributes = node.get("attributes") or {}
    for name in names:
        value = node.get(name, attributes.get(name))
        if value is not None:
            return value
    return None


def target_role(node):
    tag = str(attr(node, "tag", "tagName", "nodeName") or "").lower()
    role = str(attr(node, "role") or "").lower().split()
    role = role[0] if role else ""
    kind = str(attr(node, "type", "input_type") or "text").lower()
    if role == "link" or (tag == "a" and attr(node, "href") is not None):
        return "LINK"
    if role in {"combobox", "listbox"} or tag == "select":
        return "SELECT"
    if role in {"checkbox", "radio", "switch"} or (tag == "input" and kind in {"checkbox", "radio"}):
        return "TOGGLE"
    if role == "button" or tag == "button" or (tag == "input" and kind in {"button", "submit", "reset", "image"}):
        return "BUTTON"
    editable = attr(node, "contenteditable", "content_editable", "isContentEditable")
    if (role in {"textbox", "searchbox"} or tag == "textarea"
            or editable is True or editable in ("", "true", "plaintext-only")
            or (tag == "input" and kind in {"text", "search", "email", "url", "tel", "password", "number"})):
        return "TEXT_INPUT"
    if tag in {"window", "document", "#document", "html", "body"}:
        return "PAGE"
    if tag in {"div", "section", "main", "article", "aside", "nav", "ul", "ol", "li", "form", "table"} or node.get("scrollable"):
        return "CONTAINER"
    return "OTHER"


def node_path(node):
    if isinstance(node, str):
        node = {"tag": node}
    if not isinstance(node, dict):
        return []
    result, current, seen = [node], node, {id(node)}
    ancestors = node.get("ancestors") or []
    if isinstance(ancestors, list):
        result.extend(n for n in ancestors if isinstance(n, dict))
    while isinstance(current, dict):
        current = current.get("parent") or current.get("parentElement")
        if not isinstance(current, dict) or id(current) in seen:
            break
        seen.add(id(current))
        result.append(current)
    return result


def canonical_target(event, *, related=False):
    prefix = "related_" if related else ""
    target = event.get("related_target", event.get("relatedTarget")) if related else event.get("target")
    path = event.get(prefix + "composed_path", event.get("relatedComposedPath" if related else "composedPath"))
    nodes = [n for n in path if isinstance(n, dict)] if isinstance(path, list) else []
    if not nodes:
        nodes = node_path(target)
        ancestors = event.get(prefix + "ancestors") or []
        if isinstance(ancestors, list):
            nodes.extend(n for n in ancestors if isinstance(n, dict))
    kind = str(event.get("type", event.get("event_type", ""))).lower()
    if not nodes:
        if not related and kind in {"scroll", "scrollend", "wheel", "monitor_start", "navigate", "navigation", "popstate", "history_nav"}:
            nodes = [{"tag": "document"}]
        else:
            return {}, "OTHER", None
    # Scroll coordinates belong to the original scroll container, even when
    # that container sits inside a semantic control.
    node = nodes[0]
    if kind not in {"scroll", "scrollend"} or related:
        node = next((n for n in nodes if target_role(n) in SEMANTIC_ROLES), node)
    role = target_role(node)
    identity = next((str(node[k]) for k in ("node_id", "canonical_target_id", "id", "css_path", "selector") if node.get(k)), None)
    if role == "PAGE":
        identity = "page"
    if identity is None:
        # Anonymous legacy nodes cannot be disambiguated perfectly. Keep only
        # structural identity; never use values/text or guess a task meaning.
        identity = "anonymous:" + json.dumps({k: attr(node, k) for k in ("tag", "tagName", "type", "role", "name")}, sort_keys=True)
    return node, role, identity


def is_submit(node):
    tag = str(attr(node, "tag", "tagName") or "").lower()
    kind = str(attr(node, "type") or ("submit" if tag == "button" else "")).lower()
    return (tag in {"input", "button"} and kind in {"submit", "image"}) or node.get("submit_semantics") is True
