"""Shared numeric and pointer-pair primitives; preserves v5 semantics."""
import math

def event_time(e):
    for k in ("monotonic_ms", "epoch_ms", "ts", "timestamp"):
        try:
            v = float(e[k])
            if math.isfinite(v):
                return v
        except (KeyError, TypeError, ValueError):
            pass
    return None


def finite_number(e, key):
    try:
        value = float(e[key])
        return value if math.isfinite(value) else None
    except (KeyError, TypeError, ValueError):
        return None


def same_pointer_event(pointer, mouse):
    """Identify the paired PointerEvent/MouseEvent emitted for one action."""
    if pointer.get("session_id") != mouse.get("session_id"):
        return False
    if pointer.get("type") == "pointermove" and mouse.get("type") == "mousemove":
        tolerance = 2.0
    elif pointer.get("type") == "pointerdown" and mouse.get("type") == "mousedown":
        tolerance = 2.0
    elif pointer.get("type") == "pointerup" and mouse.get("type") == "mouseup":
        tolerance = 2.0
    else:
        return False
    pt, mt = event_time(pointer), event_time(mouse)
    if pt is None or mt is None or abs(pt - mt) > 5.0:
        return False
    if pointer.get("button", 0) != mouse.get("button", 0):
        return False
    for key in ("client_x", "client_y"):
        pv, mv = finite_number(pointer, key), finite_number(mouse, key)
        if pv is not None and mv is not None and abs(pv - mv) > tolerance:
            return False
    return True


