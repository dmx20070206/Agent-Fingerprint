"""Extract browser behaviour features from raw events."""

from __future__ import annotations
import math
from statistics import mean, median, pstdev
from typing import Any

try:
    from ._common import parser, paths, read_json, write_json
except ImportError:
    from _common import parser, paths, read_json, write_json
MISSING = -1.0
# Pointer movement is treated as mouse movement throughout the feature
# extraction pipeline (pointer events also cover a physical mouse).
MOUSE_MOVE_TYPES = ("mousemove", "pointermove")


def _t(e):
    for k in ("monotonic_ms", "epoch_ms", "ts", "timestamp"):
        try:
            v = float(e[k])
            return v if math.isfinite(v) else None
        except (KeyError, TypeError, ValueError):
            pass
    return None


def _stat(x, op):
    if not x or (op in ("std", "range") and len(x) < 2):
        return MISSING
    return float({"mean": mean, "median": median, "std": pstdev, "range": lambda z: max(z) - min(z)}[op](x))


def _ratio(a, b):
    return MISSING if not b else a / b


def _num(e, key):
    try:
        value = float(e[key])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError):
        return None


def _same_pointer_event(pointer, mouse):
    """Identify the paired PointerEvent/MouseEvent emitted for one action."""
    if pointer.get("type") == "pointermove" and mouse.get("type") == "mousemove":
        tolerance = 2.0
    elif pointer.get("type") == "pointerdown" and mouse.get("type") == "mousedown":
        tolerance = 2.0
    elif pointer.get("type") == "pointerup" and mouse.get("type") == "mouseup":
        tolerance = 2.0
    else:
        return False
    pt, mt = _t(pointer), _t(mouse)
    if pt is None or mt is None or abs(pt - mt) > 5.0:
        return False
    if pointer.get("button", 0) != mouse.get("button", 0):
        return False
    for key in ("client_x", "client_y"):
        pv, mv = _num(pointer, key), _num(mouse, key)
        if pv is not None and mv is not None and abs(pv - mv) > tolerance:
            return False
    return True


def _canonical_events(events):
    """Normalize pointer input to mouse input and drop paired duplicate events."""
    result = []
    for event in events:
        event = dict(event)
        pointer_type = event.get("type")
        if pointer_type in ("mousemove", "mousedown", "mouseup") and result:
            prior = result[-1]
            if prior.get("type") == pointer_type:
                pt, mt = _t(prior), _t(event)
                if pt is not None and mt is not None and abs(pt - mt) <= 5 and prior.get("button", 0) == event.get("button", 0):
                    same_coords = all(
                        _num(prior, key) is None or _num(event, key) is None or abs(_num(prior, key) - _num(event, key)) <= 2
                        for key in ("client_x", "client_y")
                    )
                    if same_coords: continue
        if pointer_type == "pointermove": event["type"] = "mousemove"
        elif pointer_type == "pointerdown": event["type"] = "mousedown"
        elif pointer_type in ("pointerup", "pointercancel"): event["type"] = "mouseup"
        result.append(event)
    return result


def _unwrap_angles(values):
    if not values:
        return []
    result = [values[0]]
    for value in values[1:]:
        result.append(result[-1] + math.atan2(math.sin(value - result[-1]), math.cos(value - result[-1])))
    return result


def format_l3(raw: dict[str, Any]) -> dict[str, Any]:
    ev = [e for e in raw.get("events", []) if isinstance(e, dict)]
    typ = [str(e.get("type", "")) for e in ev]
    kd = sorted((_t(e), e.get("key", e.get("code"))) for e in ev if e.get("type") == "keydown" and _t(e) is not None)
    ku = {}
    for e in ev:
        if e.get("type") == "keyup" and _t(e) is not None:
            ku.setdefault(e.get("key", e.get("code")), []).append(_t(e))
    holds = []
    complete = []
    complete_keys = []
    dangling_down = False
    for t, k in kd:
        c = [u for u in ku.get(k, []) if u >= t]
        if c:
            u = min(c)
            ku[k].remove(u)
            holds.append(u - t)
            complete.append(t)
            complete_keys.append(k)
        else:
            dangling_down = True
    dangling_up = any(ku.values())
    inter = [b - a for a, b in zip(complete, complete[1:])]
    mouse_ev = _canonical_events(ev)
    typ = [str(e.get("type", "")) for e in mouse_ev]
    pts = []
    dist = []
    dire = []
    last_move_time = None
    movement_open = False
    for e in mouse_ev:
        if e.get("type") in ("mousedown", "mouseup", "click"):
            movement_open = False
            last_move_time = None
            continue
        if e.get("type") not in MOUSE_MOVE_TYPES:
            continue
        x, y = _num(e, "client_x"), _num(e, "client_y")
        if x is None or y is None:
            continue
        t = _t(e)
        if last_move_time is not None and t is not None and t - last_move_time > 250:
            movement_open = False
        if movement_open and pts:
            X, Y = x, y
            px, py = pts[-1]
            dx, dy = X - px, Y - py
            d = math.hypot(dx, dy)
            if d:
                dist.append(d)
                dire.append(math.atan2(dy, dx))
        pts.append((x, y))
        movement_open = True
        last_move_time = t
    # Do not connect the first point after a segment boundary to the prior point.
    # The loop above retains points for presence, while distances are only added
    # while movement_open is true within the same segment.
    unwrapped_dire = _unwrap_angles(dire)
    ang = [math.atan2(math.sin(b - a), math.cos(b - a)) for a, b in zip(dire, dire[1:])]
    scroll = [e for e in ev if e.get("type") == "scroll"]
    sd = []
    st = []
    for a, b in zip(scroll, scroll[1:]):
        try:
            p = (a.get("scroll_x", a.get("scrollX", a.get("x", 0))), a.get("scroll_y", a.get("scrollY", a.get("y", 0))))
            q = (b.get("scroll_x", b.get("scrollX", b.get("x", 0))), b.get("scroll_y", b.get("scrollY", b.get("y", 0))))
            sd.append(math.hypot(float(q[0]) - float(p[0]), float(q[1]) - float(p[1])))
        except (TypeError, ValueError, IndexError):
            pass
    ts = [_t(e) for e in scroll if _t(e) is not None]
    st = [b - a for a, b in zip(ts, ts[1:])]
    scroll_depths = []
    for e in scroll:
        value = _num(e, "scroll_y")
        if value is None: value = _num(e, "scrollY")
        if value is None: value = _num(e, "y")
        if value is not None: scroll_depths.append(value)
    scroll_deltas = [b - a for a, b in zip(scroll_depths, scroll_depths[1:])]
    scroll_reversals = sum(
        1 for a, b in zip(scroll_deltas, scroll_deltas[1:])
        if a and b and ((a > 0) != (b > 0))
    )
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
                viewport_width = viewport_width or float(viewport.get("width") or 0)
                viewport_height = viewport_height or float(viewport.get("height") or 0)
        viewport_area = viewport_width * viewport_height
        if not viewport_area:
            viewport_area = float((raw.get("viewport_width") or 0) * (raw.get("viewport_height") or 0))
        if not viewport_area:
            viewport_area = 1.0
        bbox_frac = ((max(click_x) - min(click_x)) * (max(click_y) - min(click_y))) / viewport_area
    key_count = len(kd)
    structural_keys = {"Tab", "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "Escape", "Esc", "Backspace"}
    structural_count = sum(k in structural_keys for _, k in kd)
    printable_count = sum(isinstance(k, str) and len(k) == 1 and k.isprintable() for _, k in kd)
    structural_key_ratio = _ratio(structural_count, structural_count + printable_count)
    link_clicks = sum((e.get("target") or {}).get("tag") == "a" for e in clicks)
    nav_count = sum(e.get("type") in ("beforeunload", "pagehide", "navigate", "navigation") for e in ev)
    exit_scroll = [_num(e, "scroll_pct") for e in ev if e.get("type") == "beforeunload" and _num(e, "scroll_pct") is not None]
    f = {}

    def put(_position, n, v):
        f[n] = v

    vals = [
        (1, "paste_count", typ.count("paste")),
        (2, "mouse_curvature_angle_range", _stat(ang, "range")),
        (3, "hold_latency_median", _stat(holds, "median")),
        (4, "inter_key_latency_median", _stat(inter, "median")),
        (5, "hold_latency_mean", _stat(holds, "mean")),
        (6, "scroll_distance_std", _stat(sd, "std")),
        (7, "change_count", typ.count("change")),
        (8, "scroll_distance_mean", _stat(sd, "mean")),
        (9, "scroll_time_median", _stat(st, "median")),
        (10, "input_count", typ.count("input")),
        (
            11,
            "mouse_event_count",
            sum(
                e.get("type")
                in ("mousemove", "mousedown", "mouseup", "click", "pointermove", "pointerdown", "pointerup")
                for e in mouse_ev
            ),
        ),
        (12, "scroll_distance_range", _stat(sd, "range")),
        (13, "button0_count", sum(btn[0])),
        (14, "hold_latency_range", _stat(holds, "range")),
        (15, "inter_key_latency_mean", _stat(inter, "mean")),
        (16, "scroll_time_mean", _stat(st, "mean")),
        (17, "scroll_distance_median", _stat(sd, "median")),
        (18, "mouse_curvature_angle_mean", _stat(ang, "mean")),
        (19, "dangling_keydown", int(dangling_down)),
        (20, "scroll_time_std", _stat(st, "std")),
        (21, "scroll_count", typ.count("scroll")),
        (22, "mouse_direction_mean", _stat(unwrapped_dire, "mean")),
        (23, "inter_key_latency_range", _stat(inter, "range")),
        (24, "button0_down_up_ratio", _ratio(*btn[0])),
        (25, "mouse_curvature_angle_std", _stat(ang, "std")),
        (26, "mouse_curvature_distance_median", _stat(dist, "median")),
        (27, "mouse_direction_range", _stat(unwrapped_dire, "range")),
        (28, "scroll_time_range", _stat(st, "range")),
        (29, "hold_latency_std", _stat(holds, "std")),
        (30, "scrollend_count", typ.count("scrollend")),
        (31, "inter_key_latency_std", _stat(inter, "std")),
        (32, "backspace_delete_count", sum(k in ("Backspace", "Delete") for k in complete_keys)),
        (33, "mouse_curvature_distance_mean", _stat(dist, "mean")),
        (34, "mousemove_count", typ.count("mousemove")),
        (35, "keypress_count", typ.count("keydown")),
        (36, "mouse_direction_std", _stat(unwrapped_dire, "std")),
    ]
    for x in vals:
        put(*x)
    # The feature vector is a fixed 50-dimensional schema.  Ranks 37-50 are
    # ordinary feature positions, not classifier-derived importance scores.
    put(37, "dangling_keyup", int(dangling_up))
    put(38, "backspace_delete_ratio", _ratio(f["backspace_delete_count"], len(holds)))
    rank = 39
    for b in range(1, 5):
        put(rank, f"button{b}_count", sum(btn[b]))
        rank += 1
        put(rank, f"button{b}_down_up_ratio", _ratio(*btn[b]))
        rank += 1
    for n, x in [
        ("mouse_direction_median", _stat(dire, "median")),
        ("mouse_curvature_angle_median", _stat(ang, "median")),
        ("mouse_curvature_distance_range", _stat(dist, "range")),
        ("mouse_curvature_distance_std", _stat(dist, "std")),
        ("scroll_reversals", scroll_reversals),
        ("structural_key_ratio", structural_key_ratio),
        ("mean_key_iei_ms", _stat(inter, "mean")),
        ("std_key_iei_ms", _stat(inter, "std")),
        ("click_x_std", _stat(click_x, "std")),
        ("click_y_std", _stat(click_y, "std")),
        ("click_bbox_area_frac", bbox_frac),
        ("link_click_ratio", _ratio(link_clicks, len(clicks))),
        ("nav_to_click_ratio", _ratio(nav_count, len(clicks))),
        ("mean_exit_scroll_pct", _stat(exit_scroll, "mean")),
    ]:
        put(rank, n, x)
        rank += 1
    stats = ("mean", "median", "range", "std")
    movement = {
        "mouse_event_count": f["mouse_event_count"],
        "mousemove_count": f["mousemove_count"],
        "mouse_direction": {stat: f[f"mouse_direction_{stat}"] for stat in stats},
        "mouse_curvature_angle": {stat: f[f"mouse_curvature_angle_{stat}"] for stat in stats},
        "mouse_curvature_distance": {stat: f[f"mouse_curvature_distance_{stat}"] for stat in stats},
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
        "mean_key_iei_ms": f["mean_key_iei_ms"],
        "std_key_iei_ms": f["std_key_iei_ms"],
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
        "schema": "agent-fingerprint-browser-features/v2",
        "run_id": raw.get("run_id"),
        "feature_count": len(f),
        "features": f,
        "feature_vector": list(f.values()),
        "mouse_movement_behavior": movement,
        "mouse_button_behavior": buttons,
        "keyboard_typing_behavior": keyboard,
        "scroll_behavior": scroll_behavior,
    }


def main():
    a = parser(__doc__).parse_args()
    s, d = paths(a.task_id, a.input_dir, a.output_dir, "l3_browser_dynamic.json")
    write_json(d, format_l3(read_json(s)))


if __name__ == "__main__":
    main()
