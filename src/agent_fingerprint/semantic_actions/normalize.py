"""Non-mutating lexer with original indices, hard boundaries and state evidence."""
from __future__ import annotations

from .schema import NormalizedEvent
from .targets import canonical_target

BOUNDARY_START = {"monitor_start", "navigate", "navigation", "popstate", "history_nav"}
BOUNDARY_END = {"monitor_stop", "beforeunload", "pagehide"}


class EventNormalizer:
    def normalize(self, raw):
        # Reuse finite-number parsing and the existing pointer/mouse pairing
        # predicate. v5's segmenter is intentionally not altered: semantics
        # additionally requires document IDs, monitor_stop and epoch-first time.
        from agent_fingerprint.events.primitives import finite_number as _num, same_pointer_event as _same_pointer_event

        result, states, scrolls = [], {}, {}
        session = raw.get("session_id")
        document, generation, block = None, 0, 0
        last_epoch = last_mono = None
        after_end = False
        for index, event in enumerate(raw.get("events", [])):
            if not isinstance(event, dict):
                continue  # Original indices are never compacted.
            kind = str(event.get("semantic_event_type", event.get("type", event.get("event_type", "")))).lower()
            next_session = event.get("session_id", session)
            epoch, mono = _num(event, "epoch_ms"), _num(event, "monotonic_ms")
            if mono is None:
                mono = next((_num(event, k) for k in ("ts", "timestamp") if _num(event, k) is not None), None)
            explicit_doc = event.get("document_id", raw.get("document_id"))
            inferred = explicit_doc is None
            new_document = (document is None or next_session != session or
                            (explicit_doc is not None and str(explicit_doc) != document) or
                            (kind == "monitor_start" and inferred))
            if new_document:
                generation += 1
            next_document = str(explicit_doc) if explicit_doc is not None else (
                f"{next_session or 'unknown'}:document-{generation}" if new_document else document)
            reason = None
            if next_session != session:
                reason = "session_change"
            elif document is not None and next_document != document:
                reason = "document_change"
            elif kind in BOUNDARY_START:
                reason = kind
            elif after_end:
                reason = "after_boundary"
            elif ((epoch is not None and last_epoch is not None and epoch < last_epoch) or
                  (mono is not None and last_mono is not None and mono < last_mono)):
                reason = "clock_rollback"
            if reason or new_document:
                block += 1
                states, scrolls = {}, {}
                last_epoch = last_mono = None
            session, document = next_session, next_document
            node, role, identity = canonical_target(event)
            _, _, related = canonical_target(event, related=True)
            identity = identity or f"unknown:{index}"
            previous = states.setdefault(identity, {})

            def changed(flag, names):
                if isinstance(event.get(flag), bool):
                    return event[flag]
                for name in names:
                    current = event.get(name, node.get(name))
                    if current is not None:
                        old = event.get("previous_" + name, previous.get(name))
                        previous[name] = current
                        if old is not None:
                            # Equal lengths are not equal values (e.g. "ab" -> "cd").
                            if name != "value_length" or current != old:
                                return current != old
                return None

            value_changed = changed("value_changed", ("value", "selected_index", "value_length"))
            checked_changed = changed("checked_changed", ("checked", "aria_checked", "selected"))

            def coordinate(axis):
                return next((_num(event, k) for k in ("scroll_" + axis, "scroll" + axis.upper(), axis)
                             if _num(event, k) is not None), None)

            sx, sy = (coordinate("x"), coordinate("y")) if kind in {"scroll", "scrollend", "monitor_start", "wheel"} else (None, None)
            dx = dy = scroll_changed = None
            if sx is not None or sy is not None:
                old = scrolls.get(identity, (None, None))
                px, py = _num(event, "previous_scroll_x"), _num(event, "previous_scroll_y")
                px, py = old[0] if px is None else px, old[1] if py is None else py
                dx = sx - px if sx is not None and px is not None else None
                dy = sy - py if sy is not None and py is not None else None
                if dx is not None or dy is not None:
                    scroll_changed = bool(dx or dy)
                scrolls[identity] = (sx if sx is not None else old[0], sy if sy is not None else old[1])
            if isinstance(event.get("scroll_changed"), bool):
                scroll_changed = event["scroll_changed"]
            key = event.get("key", event.get("code"))
            key_category = "PRINTABLE" if isinstance(key, str) and len(key) == 1 else "STRUCTURAL" if key else None
            item = NormalizedEvent(index, kind, epoch, mono, session, document, block,
                                   identity, role, event, target=node,
                                   x=_num(event, "client_x"), y=_num(event, "client_y"), button=event.get("button"),
                                   key=key, key_category=key_category, value_changed=value_changed,
                                   checked_changed=checked_changed, scroll_x=sx, scroll_y=sy,
                                   scroll_changed=scroll_changed, scroll_dx=dx, scroll_dy=dy,
                                   related_target_id=related, url=event.get("url"),
                                   boundary_reason=reason, document_inferred=inferred)
            if result and kind in {"mousedown", "mouseup"}:
                prior = result[-1]
                # Pair only inside the same semantic block/target. Feeding the
                # preferred clock to the existing predicate avoids mixing clocks.
                def pair_raw(e):
                    return {**e.raw_event, "type": e.event_type, "monotonic_ms": e.epoch_ms if e.epoch_ms is not None else e.monotonic_ms}
                if (prior.block_id == block and prior.canonical_target_id == identity and
                        _same_pointer_event(pair_raw(prior), pair_raw(item))):
                    item.duplicate_of = prior.index
            result.append(item)
            if epoch is not None:
                last_epoch = epoch
            if mono is not None:
                last_mono = mono
            after_end = kind in BOUNDARY_END
        return result
