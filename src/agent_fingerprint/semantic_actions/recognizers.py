"""Independent deterministic recognizers produce evidence intervals, not final tokens."""
from __future__ import annotations

from itertools import groupby

from .schema import CandidateAction, elapsed
from .targets import is_submit

EDIT_EVIDENCE = {"input", "paste", "beforeinput", "compositionstart", "compositionupdate", "compositionend"}
ACTIVATIONS = {"click", "dblclick", "contextmenu"}
NAV_EVENTS = {"navigate", "navigation", "popstate", "history_nav"}


def blocks(events):
    for _, items in groupby(events, key=lambda e: e.block_id):
        yield list(items)


def within(a, b, threshold):
    dt = elapsed(a, b)
    return dt is not None and dt <= threshold


def candidate(kind, start, end=None, **kwargs):
    return CandidateAction(kind, start.target_role, start, end or start, **kwargs)


class TextEntryRecognizer:
    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            active = None
            for e in group:
                kind = e.event_type
                same = active is not None and e.canonical_target_id == active.target_id
                idle = elapsed(active.end, e) if active else None
                stop = (kind in {"submit", "reset", "blur", "monitor_stop", "beforeunload", "pagehide"}
                        or (kind == "keydown" and e.key in {"Enter", "NumpadEnter"})
                        or (kind in ACTIVATIONS | {"focus", "pointerdown", "mousedown"} and not same)
                        or (kind == "scroll" and e.scroll_changed is True)
                        or (kind == "change" and e.target_role in {"SELECT", "TOGGLE"})
                        or (kind in EDIT_EVIDENCE and not same)
                        or (idle is not None and idle > config.T_TEXT_ENTRY_IDLE_MS))
                if active and stop:
                    result.append(active)
                    active = None
                evidence = (e.target_role == "TEXT_INPUT" and
                            (kind in EDIT_EVIDENCE or (kind == "change" and e.value_changed is True)))
                # Equal lengths cannot prove equal text. An explicit unchanged
                # snapshot, however, must not become a successful edit.
                if kind == "input" and e.value_changed is False:
                    evidence = False
                if evidence:
                    if active is None:
                        active = candidate("TEXT_ENTRY", e, recognition_rule="text.edit_interval",
                                           confidence="HIGH" if kind in {"input", "change"} else "MEDIUM")
                    else:
                        active.extend(e)
                    if kind in {"input", "change"}:
                        active.confidence = "HIGH"
                elif (active and same and kind in {"keydown", "keyup", "change"}
                      and e.key not in {"Enter", "NumpadEnter"}):
                    active.extend(e)
            if active:
                result.append(active)
        return result


class ActivationRecognizer:
    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            down = None
            last_click = None
            for e in group:
                if e.duplicate_of is not None:
                    continue
                kind = e.event_type
                if kind in {"pointerdown", "mousedown"}:
                    # Legacy probes already renamed pointerdown to mousedown.
                    # One pending cycle still produces exactly one click token.
                    if down is None or down.canonical_target_id != e.canonical_target_id or not within(down, e, config.T_ACTIVATION_MAX_GAP_MS):
                        down = e
                elif kind == "pointercancel":
                    down = None
                elif kind in EDIT_EVIDENCE | {"submit", "reset"}:
                    down = None
                elif kind in ACTIVATIONS:
                    if kind == "dblclick" and last_click and last_click.canonical_target_id == e.canonical_target_id and within(last_click, e, config.T_ACTIVATION_MAX_GAP_MS):
                        continue  # dblclick is a summary following two clicks.
                    start = down if (down and down.canonical_target_id == e.canonical_target_id
                                     and (down.button is None or e.button is None or down.button == e.button)
                                     and within(down, e, config.T_ACTIVATION_MAX_GAP_MS)) else e
                    result.append(candidate("CLICK", start, e, recognition_rule="activation.cycle" if start is not e else "activation.click_only",
                                            evidence={"submit_semantics": is_submit(e.target),
                                                      "form_id": e.raw_event.get("form_id", e.target.get("form_id"))},
                                            subtype="CONTEXT" if kind == "contextmenu" else None))
                    down = None
                    last_click = e
        return result


class ControlChangeRecognizer:
    role = action_type = ""

    def changed(self, event):
        raise NotImplementedError

    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            active = None
            for e in group:
                if (active and (e.event_type not in {"input", "change"}
                                or e.canonical_target_id != active.target_id
                                or not within(active.end, e, config.T_CONTROL_CHANGE_MS))):
                    result.append(active)
                    active = None
                if e.target_role != self.role or e.event_type not in {"click", "input", "change"}:
                    continue
                changed = self.changed(e)
                # input -> change often reports the same post-change state;
                # include that confirmation, but do not merge two edits.
                if active and (active.end.event_type == "click" or
                               (e.event_type == "change" and active.end.event_type == "input")):
                    active.extend(e)
                    continue
                if not changed:
                    continue
                if active:
                    result.append(active)
                active = candidate(self.action_type, e, recognition_rule=self.action_type.lower() + ".state_change")
            if active:
                result.append(active)
        return result


class SelectRecognizer(ControlChangeRecognizer):
    role = action_type = "SELECT"

    def changed(self, e):
        return e.value_changed is True or (e.event_type == "change" and e.value_changed is None)


class ToggleRecognizer(ControlChangeRecognizer):
    role = action_type = "TOGGLE"

    def changed(self, e):
        return e.checked_changed is True or (e.event_type == "change" and e.checked_changed is None)


class SubmitRecognizer:
    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            enter = None
            for e in group:
                if e.event_type == "keydown" and e.key in {"Enter", "NumpadEnter"}:
                    enter = e
                elif e.event_type == "submit":
                    start = enter if enter and within(enter, e, config.T_ACTIVATION_MAX_GAP_MS) else e
                    form_id = e.raw_event.get("form_id", e.target.get("node_id", e.target.get("id")))
                    if start is not e:
                        enter_form = start.raw_event.get("form_id", start.target.get("form_id"))
                        if form_id and enter_form and str(form_id) != str(enter_form):
                            start = e
                    action = candidate("SUBMIT", start, e, recognition_rule="submit.enter" if start is not e else "submit.event",
                                       evidence={"form_id": form_id, "submitter_id": e.raw_event.get("submitter_id")})
                    action.target_role = "OTHER"  # Form/button/Enter normalize to the same decision.
                    result.append(action)
                    enter = None
                elif e.event_type in ACTIVATIONS | EDIT_EVIDENCE:
                    enter = None
        return result


class ScrollRecognizer:
    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            active = None
            for e in group:
                gap = elapsed(active.end, e) if active else None
                end_action = (e.event_type in ACTIVATIONS | EDIT_EVIDENCE | {"submit", "reset", "pointerdown", "mousedown"}
                              or (e.event_type == "change" and e.target_role in {"SELECT", "TOGGLE"}))
                if active and (end_action or (gap is not None and gap > config.T_SCROLL_GAP_MS)
                               or (e.event_type in {"scroll", "scrollend"} and active.target_id != e.canonical_target_id)):
                    result.append(active)
                    active = None
                if e.event_type == "scroll" and e.scroll_changed is True:
                    if active is None:
                        active = candidate("SCROLL", e, recognition_rule="scroll.position_burst")
                    else:
                        active.extend(e)
                    for axis in ("x", "y"):
                        active.evidence[axis] = active.evidence.get(axis, 0) + (getattr(e, "scroll_d" + axis) or 0)
                    dx, dy = active.evidence["x"], active.evidence["y"]
                    active.subtype = ("DOWN" if dy > 0 else "UP") if abs(dy) >= abs(dx) and dy else ("RIGHT" if dx > 0 else "LEFT") if dx else None
                elif e.event_type == "scrollend" and active:
                    active.extend(e)
                    result.append(active)
                    active = None
            if active:
                result.append(active)
        return result


class HoverRecognizer:
    def recognize(self, events, config):
        result = []
        for group in blocks(events):
            enter = last = None

            def finish(end):
                if enter is None or end is None:
                    return
                dwell = elapsed(enter, end)
                if dwell is not None and dwell >= config.T_HOVER_MIN_MS:
                    result.append(candidate("HOVER", enter, end, recognition_rule="hover.external_entry_dwell",
                                            confidence="MEDIUM" if end.raw_event.get("hover_effect") else "LOW"))

            for e in group:
                kind = e.event_type
                if kind in {"pointerover", "mouseover", "pointerenter", "mouseenter"}:
                    if e.related_target_id == e.canonical_target_id:
                        continue  # span -> svg inside the same button.
                    if enter and enter.canonical_target_id == e.canonical_target_id:
                        continue  # pointerover + mouseover compatibility events.
                    finish(last)
                    # Missing relatedTarget evidence cannot establish an
                    # external entry. Null explicitly means outside the page.
                    has_related = any(k in e.raw_event for k in ("related_target", "relatedTarget", "related_composed_path", "relatedComposedPath"))
                    enter = e if has_related and e.target_role not in {"PAGE", "OTHER"} else None
                elif enter and kind in {"pointerout", "mouseout", "pointerleave", "mouseleave"}:
                    if e.canonical_target_id == enter.canonical_target_id and e.related_target_id != enter.canonical_target_id:
                        finish(e)
                        enter = None
                elif enter and (kind in ACTIVATIONS | EDIT_EVIDENCE | {"scroll", "submit", "pointerdown", "mousedown"}
                                or (kind == "change" and e.target_role in {"SELECT", "TOGGLE"})):
                    finish(e)
                    enter = None
                last = e
            finish(last)
        return result


class NavigationRecognizer:
    def recognize(self, events, config):
        result = []
        seen_document = None
        for e in events:
            kind = e.event_type
            reason = e.raw_event.get("reason")
            history = (kind in {"popstate", "history_nav"} or reason in {"popstate", "back", "forward", "traverse"}
                       or e.raw_event.get("navigation_type") in {"traverse", "back_forward"})
            new_doc = seen_document is not None and seen_document != e.document_id
            if history or kind in NAV_EVENTS or new_doc:
                action = candidate("HISTORY_NAV" if history else "NAVIGATE", e,
                                   recognition_rule="navigation.history" if history else "navigation.document" if new_doc else "navigation.explicit",
                                   evidence={"new_document": new_doc})
                action.target_role = "PAGE"
                before, after = e.raw_event.get("previous_history_index"), e.raw_event.get("history_index")
                if history and isinstance(before, int) and isinstance(after, int) and before != after:
                    action.subtype = "BACK" if after < before else "FORWARD"
                result.append(action)
            seen_document = e.document_id
        return result


RECOGNIZERS = (TextEntryRecognizer, ActivationRecognizer, ScrollRecognizer,
               SelectRecognizer, ToggleRecognizer, SubmitRecognizer, HoverRecognizer,
               NavigationRecognizer)
