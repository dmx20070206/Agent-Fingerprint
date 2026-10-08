"""Evidence-bounded folding. Absorption never joins actions across documents."""
from dataclasses import replace

from .schema import SemanticAction, elapsed
from .recognizers import within


class SemanticReducer:
    def reduce(self, candidates, events, config):
        actions = [replace(c, evidence=dict(c.evidence)) for c in candidates]
        actions.sort(key=lambda c: (c.start.index, c.end.index, c.action_type))
        removed, trace = set(), []

        def absorb(low, high, rule, *, trigger_time=False):
            removed.add(id(low))
            trace.append({"rule": rule, "absorbed": low.action_type, "into": high.action_type,
                          "source_event_start_index": low.source_event_start_index,
                          "source_event_end_index": low.source_event_end_index,
                          "retained_event_index": high.start.index})
            high.source_event_start_index = min(high.source_event_start_index, low.source_event_start_index)
            high.source_event_end_index = max(high.source_event_end_index, low.source_event_end_index)
            # Getting focus is provenance for text entry, not text editing time.
            if trigger_time:
                high.start = low.start
            high.recognition_rule += "+" + rule

        # More specific outcomes absorb only their nearest compatible trigger.
        # Intervening decisions block causal guesses based solely on time.
        for high in actions:
            if high.action_type not in {"TEXT_ENTRY", "SELECT", "TOGGLE", "SUBMIT"}:
                continue
            possible = [c for c in actions if c.action_type == "CLICK" and id(c) not in removed
                        and c.start.block_id == high.start.block_id and c.end.index <= high.end.index]
            for low in reversed(possible):
                compatible = (low.target_id == high.target_id and low.target_role == high.target_role)
                if high.action_type == "SUBMIT":
                    compatible = low.evidence.get("submit_semantics", False)
                    lf, hf = low.evidence.get("form_id"), high.evidence.get("form_id")
                    if lf is not None and hf is not None and str(lf) != str(hf):
                        compatible = False
                    submitter = high.evidence.get("submitter_id")
                    if submitter is not None and str(submitter) != low.target_id:
                        compatible = False
                if not compatible:
                    continue
                overlap = high.start.index <= low.start.index <= high.end.index
                limit = config.T_TEXT_ENTRY_IDLE_MS if high.action_type == "TEXT_ENTRY" else config.T_CONTROL_CHANGE_MS
                close = within(low.end, high.start, limit)
                # Missing clocks permit only a directly adjacent evidence
                # sequence, with no intervening semantic candidate.
                mechanics = {"focus", "keydown", "keyup", "input", "change", "beforeinput", "paste",
                             "compositionstart", "compositionupdate", "compositionend", "selectionchange"}
                untimed_adjacent = (elapsed(low.end, high.start) is None and low.end.index < high.start.index
                                    and all(e.event_type in mechanics and e.canonical_target_id == low.target_id
                                            for e in events if low.end.index < e.index < high.start.index))
                if not (overlap or close or untimed_adjacent):
                    continue
                blockers = [c for c in actions if c is not high and c is not low and id(c) not in removed
                            and c.action_type != "HOVER" and low.end.index < c.start.index < high.start.index]
                if blockers:
                    continue
                absorb(low, high, "click_absorbed_by_" + high.action_type.lower(), trigger_time=high.action_type != "TEXT_ENTRY")
                break

        # Hover transportation to a control is absorbed even when the click
        # itself was subsequently folded into SELECT/TEXT_ENTRY/SUBMIT.
        for hover in actions:
            if hover.action_type != "HOVER":
                continue
            for click in actions:
                if (click.action_type == "CLICK" and hover.target_id == click.target_id
                        and hover.start.block_id == click.start.block_id
                        and within(hover.end, click.start, config.T_HOVER_CLICK_MS)):
                    removed.add(id(hover))
                    trace.append({"rule": "hover_absorbed_by_click", "absorbed": "HOVER", "into": "CLICK",
                                  "source_event_start_index": hover.source_event_start_index,
                                  "source_event_end_index": hover.source_event_end_index,
                                  "retained_event_index": click.start.index})
                    break

        kept = [c for c in actions if id(c) not in removed]
        kept.sort(key=lambda c: (c.start.index, c.end.index))
        final = []
        for action in kept:
            if action.action_type == "NAVIGATE" and final:
                cause = final[-1]
                valid_cause = (cause.action_type in {"SUBMIT", "HISTORY_NAV", "NAVIGATE"}
                               or (cause.action_type == "CLICK" and cause.target_role in {"LINK", "BUTTON"}))
                dt = elapsed(cause.end, action.start, cross_document=True)
                middle = [e for e in events if cause.end.index < e.index <= action.start.index]
                rollback = any(e.boundary_reason == "clock_rollback" for e in middle)
                if (dt is None and not rollback and cause.end.session_id == action.start.session_id
                        and cause.end.document_id == action.start.document_id
                        and cause.end.monotonic_ms is not None and action.start.monotonic_ms is not None):
                    # A same-document navigation splits semantic blocks, but
                    # does not reset performance.now(). Use it only for causal
                    # attribution; the final temporal gap still remains null.
                    local_dt = action.start.monotonic_ms - cause.end.monotonic_ms
                    dt = local_dt if local_dt >= 0 else None
                # Old probe session_id is a per-document installation ID.
                # Explicit document/session metadata retains strict session isolation.
                session_ok = (cause.end.session_id == action.start.session_id or
                              (cause.end.document_inferred and action.start.document_inferred
                               and action.start.event_type == "monitor_start"))
                # monitor_stop without unload is an explicit stop, not a page transition.
                stopped = any(e.event_type == "monitor_stop" for e in middle) and not any(e.event_type in {"beforeunload", "pagehide"} for e in middle)
                if valid_cause and session_ok and not rollback and not stopped and dt is not None and dt <= config.T_NAV_CAUSAL_WINDOW_MS:
                    cause.outcome = "NEW_DOCUMENT" if action.evidence.get("new_document") else "SAME_DOCUMENT_NAVIGATION"
                    trace.append({"rule": "navigation_is_outcome", "absorbed": "NAVIGATE", "into": cause.action_type,
                                  "source_event_start_index": action.source_event_start_index,
                                  "source_event_end_index": action.source_event_end_index,
                                  "retained_event_index": cause.start.index})
                    # Do not extend the action's source span/duration across a page.
                    continue
            final.append(action)
        return final, trace


class TimingAnnotator:
    def annotate(self, candidates):
        result = []
        for i, c in enumerate(candidates):
            a, b = c.start, c.end
            if a.epoch_ms is not None and b.epoch_ms is not None:
                start, end, basis = a.epoch_ms, b.epoch_ms, "epoch_ms"
            elif a.monotonic_ms is not None and b.monotonic_ms is not None:
                start, end, basis = a.monotonic_ms, b.monotonic_ms, "monotonic_ms"
            else:
                start = end = basis = None
            gap = elapsed(b, candidates[i + 1].start) if i + 1 < len(candidates) else None
            result.append(SemanticAction(i, c.action_type, c.target_role, start, end,
                                         elapsed(a, b), gap, c.source_event_start_index,
                                         c.source_event_end_index, c.target_id, a.document_id,
                                         a.session_id, c.recognition_rule, c.confidence,
                                         a.block_id, basis, c.subtype, c.outcome))
        return result
