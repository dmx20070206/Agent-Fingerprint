"""Extract browser behaviour features from raw events."""

from __future__ import annotations
import math
from statistics import mean, median, pstdev
from typing import Any

from agent_fingerprint.features.feature_schema import SCHEMA, FEATURE_NAMES
from agent_fingerprint.features.episode_features import episode_features, load_run_metadata
from agent_fingerprint.events.primitives import event_time as _t, finite_number as _num, same_pointer_event as _same_pointer_event
MISSING = None
# Pointer movement is treated as mouse movement throughout the feature
# extraction pipeline (pointer events also cover a physical mouse).
MOUSE_MOVE_TYPES = ("mousemove", "pointermove")




def _stat(x, op):
    if not x or (op in ("std", "range") and len(x) < 2):
        return MISSING
    return float({"mean": mean, "median": median, "std": pstdev, "range": lambda z: max(z) - min(z)}[op](x))


def _ratio(a, b):
    return MISSING if not b else a / b






def _canonical_events(events):
    """Drop only adjacent pointer/mouse pairs, retaining genuine mouse moves."""
    result = []
    previous = None
    mapping = {"pointermove": "mousemove", "pointerdown": "mousedown",
               "pointerup": "mouseup", "pointercancel": "mouseup"}
    for event in events:
        if previous is not None and _same_pointer_event(previous, event):
            previous = event
            continue
        normalized = dict(event)
        normalized["type"] = mapping.get(event.get("type"), event.get("type"))
        result.append(normalized)
        previous = event
    return result


def _segments(events):
    """Separate document sessions, navigation and backwards clocks."""
    groups = []
    current = []
    last_session = None
    last_time = None
    for event in events:
        session, time = event.get("session_id"), _t(event)
        boundary = (session != last_session or
                    event.get("type") in {"monitor_start", "navigate", "navigation"} or
                    (time is not None and last_time is not None and time < last_time))
        if boundary and current:
            groups.append(current)
            current = []
        current.append(event)
        last_session, last_time = session, time
        if event.get("type") in {"beforeunload", "pagehide"}:
            groups.append(current)
            current = []
            last_time = None
    if current:
        groups.append(current)
    return groups


def _unwrap_angles(values):
    if not values:
        return []
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1] + math.atan2(math.sin(value - result[-1]), math.cos(value - result[-1])))
    return result


def format_l3(raw: dict[str, Any], metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    ev = [e for e in raw.get("events", []) if isinstance(e, dict)]
    kd = []
    holds, inter, complete_keys = [], [], []
    dangling_down = dangling_up = False
    dist, dire, unwrapped_dire, ang = [], [], [], []
    transitions = {name: [] for name in ("distance", "direction", "turn_angle", "interval")}
    valid_moves = connected_moves = 0
    sd, st = [], []
    scroll_reversals = 0
    mouse_ev = []
    for segment in _segments(ev):
        downs = sorted(((_t(e), e.get("key", e.get("code"))) for e in segment
                        if e.get("type") == "keydown" and _t(e) is not None),
                       key=lambda item: item[0])
        kd.extend(downs)
        ups = {}
        for e in segment:
            if e.get("type") == "keyup" and _t(e) is not None:
                ups.setdefault(e.get("key", e.get("code")), []).append(_t(e))
        complete = []
        for t, key in downs:
            candidates = [u for u in ups.get(key, []) if u >= t]
            if candidates:
                u = min(candidates)
                ups[key].remove(u)
                holds.append(u - t)
                complete.append(t)
                complete_keys.append(key)
            else:
                dangling_down = True
        dangling_up |= any(ups.values())
        inter.extend(b - a for a, b in zip(complete, complete[1:]))
        normalized = _canonical_events(segment)
        mouse_ev.extend(normalized)
        # Discrete viewport positions retain acquisition order across clicks,
        # scrolls and waits. Intervals include agent thinking, not travel time.
        moves = [e for e in normalized if e.get("type") == "mousemove"
                 and _num(e, "client_x") is not None and _num(e, "client_y") is not None]
        valid_moves += len(moves)
        transition_directions = []
        previous_direction = None
        for a, b in zip(moves, moves[1:]):
            dx = _num(b, "client_x") - _num(a, "client_x")
            dy = _num(b, "client_y") - _num(a, "client_y")
            distance = math.hypot(dx, dy)
            transitions["distance"].append(distance)
            ta, tb = _t(a), _t(b)
            if ta is not None and tb is not None:
                transitions["interval"].append(tb - ta)
            direction = math.atan2(dy, dx) if distance else None
            if direction is not None:
                transition_directions.append(direction)
                if previous_direction is not None:
                    delta = direction - previous_direction
                    transitions["turn_angle"].append(math.atan2(math.sin(delta), math.cos(delta)))
            # A stationary transition has no heading and cannot form a turn.
            previous_direction = direction
        transitions["direction"].extend(_unwrap_angles(transition_directions))
        connected = set()
        previous_index = None
        previous = None
        directions = []

        def flush():
            dire.extend(directions)
            unwrapped_dire.extend(_unwrap_angles(directions))
            ang.extend(math.atan2(math.sin(b-a), math.cos(b-a))
                       for a, b in zip(directions, directions[1:]))
            directions.clear()

        for move_index, e in enumerate(normalized):
            if e.get("type") in {"mousedown", "mouseup", "click"}:
                flush()
                previous = None
            if e.get("type") != "mousemove":
                continue
            x, y, t = _num(e, "client_x"), _num(e, "client_y"), _t(e)
            if x is None or y is None or t is None:
                flush()
                previous = None
                continue
            if previous is not None:
                px, py, pt = previous
                if 0 <= t - pt <= 250:
                    distance = math.hypot(x-px, y-py)
                    if distance:
                        connected.update((previous_index, move_index))
                        dist.append(distance)
                        directions.append(math.atan2(y-py, x-px))
                else:
                    flush()
            previous = (x, y, t)
            previous_index = move_index
        flush()
        connected_moves += len(connected)
        # Scroll positions from different elements are different coordinate systems.
        scroll_groups = {}
        for e in segment:
            if e.get("type") == "scroll":
                target = e.get("target") or {}
                identity = target.get("css_path", target.get("id", "document"))
                scroll_groups.setdefault(identity, []).append(e)
        for scroll in scroll_groups.values():
            previous_delta = None
            for a, b in zip(scroll, scroll[1:]):
                def coord(event, axis):
                    for name in ("scroll_" + axis, "scroll" + axis.upper(), axis):
                        value = _num(event, name)
                        if value is not None:
                            return value
                    return None
                ax, ay, bx, by = coord(a, "x"), coord(a, "y"), coord(b, "x"), coord(b, "y")
                if None not in (ax, ay, bx, by):
                    sd.append(math.hypot(bx-ax, by-ay))
                ta, tb = _t(a), _t(b)
                if ta is not None and tb is not None:
                    st.append(tb-ta)
                delta = by-ay if ay is not None and by is not None else None
                if delta and previous_delta and (delta > 0) != (previous_delta > 0):
                    scroll_reversals += 1
                previous_delta = delta
    typ = [str(e.get("type", "")) for e in mouse_ev]
    btn = {
        b: (
            sum(e.get("type") in ("mousedown", "pointerdown") and e.get("button", 0) == b for e in mouse_ev),
            sum(e.get("type") in ("mouseup", "pointerup") and e.get("button", 0) == b for e in mouse_ev),
        )
        for b in range(5)
    }
    clicks = [e for e in mouse_ev if e.get("type") == "click"]
    click_x = [_num(e, "client_x") for e in clicks]
    click_y = [_num(e, "client_y") for e in clicks]
    click_x = [x for x in click_x if x is not None]
    click_y = [y for y in click_y if y is not None]
    bbox_frac = MISSING
    if click_x and click_y:
        viewport_width = viewport_height = 0
        for candidate in ev:
            viewport_width = viewport_width or (_num(candidate, "viewport_width") or 0)
            viewport_height = viewport_height or (_num(candidate, "viewport_height") or 0)
            static = candidate.get("static_fingerprint") if isinstance(candidate, dict) else None
            viewport = static.get("viewport") if isinstance(static, dict) else None
            if isinstance(viewport, dict):
                viewport_width = viewport_width or (_num(viewport, "width") or 0)
                viewport_height = viewport_height or (_num(viewport, "height") or 0)
        viewport_area = viewport_width * viewport_height
        if not viewport_area:
            viewport_area = (_num(raw, "viewport_width") or 0) * (_num(raw, "viewport_height") or 0)
        if viewport_area > 0:
            bbox_frac = ((max(click_x) - min(click_x)) * (max(click_y) - min(click_y))) / viewport_area
    structural_keys = {"Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Escape", "Esc", "Backspace"}
    structural_count = sum(k in structural_keys for _, k in kd)
    printable_count = sum(isinstance(k, str) and len(k) == 1 and k.isprintable() for _, k in kd)
    structural_key_ratio = _ratio(structural_count, structural_count + printable_count)
    link_clicks = sum((e.get("target") or {}).get("tag") == "a" for e in clicks)
    nav_count = sum(e.get("type") in ("beforeunload", "pagehide", "navigate", "navigation") for e in ev)
    exit_scroll = [_num(e, "scroll_pct") for e in ev if e.get("type") == "beforeunload" and _num(e, "scroll_pct") is not None]
    f = {}

    def put(n, v):
        f[n] = v

    vals = [
        ("paste_count", typ.count("paste")),
        ("mouse_curvature_angle_range", _stat(ang, "range")),
        ("hold_latency_median", _stat(holds, "median")),
        ("inter_key_latency_median", _stat(inter, "median")),
        ("hold_latency_mean", _stat(holds, "mean")),
        ("scroll_distance_std", _stat(sd, "std")),
        ("change_count", typ.count("change")),
        ("scroll_distance_mean", _stat(sd, "mean")),
        ("scroll_time_median", _stat(st, "median")),
        ("input_count", typ.count("input")),
        (
            "mouse_event_count",
            sum(
                e.get("type")
                in ("mousemove", "mousedown", "mouseup", "click", "pointermove", "pointerdown", "pointerup")
                for e in mouse_ev
            ),
        ),
        ("scroll_distance_range", _stat(sd, "range")),
        ("button0_count", sum(btn[0])),
        ("hold_latency_range", _stat(holds, "range")),
        ("inter_key_latency_mean", _stat(inter, "mean")),
        ("scroll_time_mean", _stat(st, "mean")),
        ("scroll_distance_median", _stat(sd, "median")),
        ("mouse_curvature_angle_mean", _stat(ang, "mean")),
        ("dangling_keydown", int(dangling_down)),
        ("scroll_time_std", _stat(st, "std")),
        ("scroll_count", typ.count("scroll")),
        ("mouse_direction_mean", _stat(unwrapped_dire, "mean")),
        ("inter_key_latency_range", _stat(inter, "range")),
        ("button0_down_up_ratio", _ratio(*btn[0])),
        ("mouse_curvature_angle_std", _stat(ang, "std")),
        ("mouse_curvature_distance_median", _stat(dist, "median")),
        ("mouse_direction_range", _stat(unwrapped_dire, "range")),
        ("scroll_time_range", _stat(st, "range")),
        ("hold_latency_std", _stat(holds, "std")),
        ("scrollend_count", typ.count("scrollend")),
        ("inter_key_latency_std", _stat(inter, "std")),
        ("backspace_delete_count", sum(k in ("Backspace", "Delete") for k in complete_keys)),
        ("mouse_curvature_distance_mean", _stat(dist, "mean")),
        ("mousemove_count", typ.count("mousemove")),
        ("keypress_count", typ.count("keydown")),
        ("mouse_direction_std", _stat(unwrapped_dire, "std")),
    ]
    for x in vals:
        put(*x)
    put("dangling_keyup", int(dangling_up))
    put("backspace_delete_ratio", _ratio(f["backspace_delete_count"], len(holds)))
    for b in range(1, 5):
        put(f"button{b}_count", sum(btn[b]))
        put(f"button{b}_down_up_ratio", _ratio(*btn[b]))
    for n, x in [
        ("mouse_direction_median", _stat(unwrapped_dire, "median")),
        ("mouse_curvature_angle_median", _stat(ang, "median")),
        ("mouse_curvature_distance_range", _stat(dist, "range")),
        ("mouse_curvature_distance_std", _stat(dist, "std")),
        ("scroll_reversals", scroll_reversals),
        ("structural_key_ratio", structural_key_ratio),
        ("click_x_std", _stat(click_x, "std")),
        ("click_y_std", _stat(click_y, "std")),
        ("click_bbox_area_frac", bbox_frac),
        ("link_click_ratio", _ratio(link_clicks, len(clicks))),
        ("nav_to_click_ratio", _ratio(nav_count, len(clicks))),
        ("mean_exit_scroll_pct", _stat(exit_scroll, "mean")),
    ]:
        put(n, x)
    for name, values in transitions.items():
        for stat in ("mean", "std"):
            put(f"mouse_transition_{name}_{stat}", _stat(values, stat))
    put("mouse_isolated_move_ratio", _ratio(valid_moves - connected_moves, valid_moves))
    episode = episode_features(raw, ev, metadata or {}, _num, _stat, _ratio)
    f.update(episode)
    if set(f) != set(FEATURE_NAMES):
        raise ValueError("L3 feature schema mismatch")
    f = {name: f[name] for name in FEATURE_NAMES}
    stats = ("mean", "median", "range", "std")
    movement = {
        "mouse_event_count": f["mouse_event_count"],
        "mousemove_count": f["mousemove_count"],
        "mouse_direction": {stat: f[f"mouse_direction_{stat}"] for stat in stats},
        "mouse_curvature_angle": {stat: f[f"mouse_curvature_angle_{stat}"] for stat in stats},
        "mouse_curvature_distance": {stat: f[f"mouse_curvature_distance_{stat}"] for stat in stats},
        **{f"mouse_transition_{name}": {
            stat: f[f"mouse_transition_{name}_{stat}"] for stat in ("mean", "std")
        } for name in transitions},
        "mouse_isolated_move_ratio": f["mouse_isolated_move_ratio"],
        "click_x_std": f["click_x_std"],
        "click_y_std": f["click_y_std"],
        "click_bbox_area_frac": f["click_bbox_area_frac"],
        "link_click_ratio": f["link_click_ratio"],
        "nav_to_click_ratio": f["nav_to_click_ratio"],
    }
    buttons = {
        **{f"button{b}_count": f[f"button{b}_count"] for b in range(5)},
        **{f"button{b}_down_up_ratio": f[f"button{b}_down_up_ratio"] for b in range(5)},
    }
    keyboard = {
        "paste_count": f["paste_count"],
        "keypress_count": f["keypress_count"],
        "dangling_keyup": f["dangling_keyup"],
        "dangling_keydown": f["dangling_keydown"],
        "inter_key_latency": {stat: f[f"inter_key_latency_{stat}"] for stat in stats},
        "hold_latency": {stat: f[f"hold_latency_{stat}"] for stat in stats},
        "change_count": f["change_count"],
        "input_count": f["input_count"],
        "backspace_delete_count": f["backspace_delete_count"],
        "backspace_delete_ratio": f["backspace_delete_ratio"],
        "structural_key_ratio": f["structural_key_ratio"],
    }
    scroll_behavior = {
        "scroll_count": f["scroll_count"],
        "scrollend_count": f["scrollend_count"],
        "scroll_reversals": f["scroll_reversals"],
        "mean_exit_scroll_pct": f["mean_exit_scroll_pct"],
        "scroll_distance": {stat: f[f"scroll_distance_{stat}"] for stat in stats},
        "scroll_time": {stat: f[f"scroll_time_{stat}"] for stat in stats},
    }
    return {
        "schema": SCHEMA,
        "run_id": raw.get("run_id"),
        "feature_count": len(f),
        "feature_names": list(FEATURE_NAMES),
        "features": f,
        "feature_vector": list(f.values()),
        "mouse_movement_behavior": movement,
        "mouse_button_behavior": buttons,
        "keyboard_typing_behavior": keyboard,
        "scroll_behavior": scroll_behavior,
        "episode_behavior": episode,
    }


def main():
    from agent_fingerprint.cli.extract import main as extract_main
    return extract_main()


if __name__ == "__main__":
    main()
