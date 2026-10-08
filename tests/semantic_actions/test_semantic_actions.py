"""Behavioral contracts, adversarial boundaries and the 25 TASK.md scenarios."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from agent_fingerprint.features.l3_browser_dynamic import format_l3
from agent_fingerprint.semantic_actions import SemanticActionConfig, abstract_events, parse_semantic_actions
from agent_fingerprint.semantic_actions.targets import target_role

INPUT = {"tag": "input", "type": "text", "id": "input"}
BUTTON = {"tag": "button", "type": "button", "id": "button"}
SUBMIT = {"tag": "button", "type": "submit", "id": "submit", "form_id": "form"}
FORM = {"tag": "form", "id": "form"}
SELECT = {"tag": "select", "id": "select"}
TOGGLE = {"tag": "input", "type": "checkbox", "id": "check"}
LINK = {"tag": "a", "href": "/next", "id": "link"}
PAGE = {"tag": "document"}
ROOT = Path(__file__).resolve().parents[2]


def e(kind, time, target=None, **kwargs):
    return {"type": kind, "epoch_ms": time, "session_id": "s", "document_id": "d1", "target": target, **kwargs}


def parse(events, **config):
    return parse_semantic_actions({"events": events}, SemanticActionConfig(**config))


def tokens(events, **config):
    return parse(events, **config).to_dict()["action_sequence_content"]


@pytest.mark.parametrize("middle", [
    [e("keydown", 10, INPUT, key="h"), e("input", 11, INPUT), e("keyup", 12, INPUT, key="h")],
    [e("paste", 10, INPUT), e("input", 11, INPUT)],
    [e("input", 11, INPUT)],
])
def test_text_framework_equivalence(middle):
    events = [e("focus", 0, INPUT), *middle, e("change", 20, INPUT)]
    assert tokens(events) == [["TEXT_ENTRY", "TEXT_INPUT"]]


def test_focus_is_not_editing_and_focus_click_is_absorbed():
    assert tokens([e("focus", 0, INPUT), e("keydown", 1, INPUT, key="a")]) == []
    r = parse([e("click", 1, INPUT), e("focus", 2, INPUT), e("input", 20, INPUT), e("change", 30, INPUT)])
    assert r.to_dict()["action_sequence_content"] == [["TEXT_ENTRY", "TEXT_INPUT"]]
    assert r.actions[0].start_time_ms == 20
    assert r.actions[0].source_event_start_index == 0
    assert "CLICK absorbed by TEXT_ENTRY" in r.debug_trace()


def test_pointer_mouse_cycle_and_transportation():
    events = [e("mousemove", 0, BUTTON), e("pointerdown", 10, BUTTON, button=0),
              e("mousedown", 11, BUTTON, button=0), e("pointerup", 20, BUTTON, button=0),
              e("mouseup", 21, BUTTON, button=0), e("click", 22, BUTTON, button=0)]
    r = parse(events)
    assert r.to_dict()["action_sequence_content"] == [["CLICK", "BUTTON"]]
    assert (r.actions[0].start_time_ms, r.actions[0].duration_ms) == (10, 12)
    assert r.normalized_events[2].duplicate_of == 1
    assert r.normalized_events[4].duplicate_of == 3


def test_click_only_and_cancel():
    assert parse([e("click", 3, BUTTON)]).actions[0].duration_ms == 0
    r = parse([e("pointerdown", 0, BUTTON), e("pointercancel", 2, BUTTON), e("click", 4, BUTTON)])
    assert r.actions[0].start_time_ms == 4


@pytest.mark.parametrize("target,action,flags", [
    (SELECT, "SELECT", {}), (TOGGLE, "TOGGLE", {"checked_changed": True})])
def test_select_and_toggle_absorb_click(target, action, flags):
    assert tokens([e("click", 0, target), e("change", 10, target, **flags)]) == [[action, action]]


@pytest.mark.parametrize("target", [SELECT, TOGGLE])
def test_control_attempt_without_change_is_click(target):
    assert tokens([e("click", 0, target)]) == [["CLICK", "SELECT" if target == SELECT else "TOGGLE"]]
    flags = {"value_changed": False} if target == SELECT else {"checked_changed": False}
    assert tokens([e("click", 0, target), e("change", 1, target, **flags)])[0][0] == "CLICK"


@pytest.mark.parametrize("target,flags,action", [(SELECT, {"value_changed": True}, "SELECT"),
                                               (TOGGLE, {"checked_changed": True}, "TOGGLE")])
def test_input_change_confirmation_is_one_action(target, flags, action):
    assert tokens([e("click", 0, target), e("input", 1, target, **flags), e("change", 2, target, **flags)]) == [[action, action]]


def test_checked_snapshots_and_two_independent_toggles():
    events = [e("focus", 0, TOGGLE, checked=False), e("click", 1, TOGGLE, checked=True),
              e("input", 2, TOGGLE, checked=True), e("change", 3, TOGGLE, checked=True),
              e("click", 4, TOGGLE, checked=False), e("change", 5, TOGGLE, checked=False)]
    # The browser click contains its post-activation state; changed evidence
    # can be on click, while change remains the confirmation event.
    assert tokens(events) == [["TOGGLE", "TOGGLE"], ["TOGGLE", "TOGGLE"]]


def test_submit_click_and_enter_equivalence():
    a = [e("click", 10, SUBMIT), e("submit", 12, FORM)]
    b = [e("keydown", 10, INPUT, key="Enter"), e("submit", 12, FORM)]
    assert tokens(a) == tokens(b) == [["SUBMIT", "OTHER"]]
    assert parse(a).actions[0].duration_ms == parse(b).actions[0].duration_ms == 2


def test_real_text_entry_survives_enter_submit():
    events = [e("input", 0, INPUT), e("keyup", 1, INPUT, key="a"),
              e("keydown", 10, INPUT, key="Enter"), e("submit", 12, FORM)]
    assert tokens(events) == [["TEXT_ENTRY", "TEXT_INPUT"], ["SUBMIT", "OTHER"]]
    assert parse(events).actions[0].end_time_ms == 1


def test_non_submit_or_different_form_click_not_swallowed():
    for target in [BUTTON, {**SUBMIT, "form_id": "other"}]:
        assert len(tokens([e("click", 0, target), e("submit", 1, FORM)])) == 2


def scroll(time, y, **kwargs):
    return e("scroll", time, PAGE, scroll_x=0, scroll_y=y, **kwargs)


def test_scroll_burst_with_scrollend():
    r = parse([scroll(0, 0), scroll(10, 10), scroll(20, 23), scroll(30, 45), e("scrollend", 40, PAGE)])
    assert r.to_dict()["action_sequence_content"] == [["SCROLL", "PAGE"]]
    assert (r.actions[0].start_time_ms, r.actions[0].end_time_ms, r.actions[0].subtype) == (10, 40, "DOWN")


def test_scroll_three_bursts_with_pauses():
    assert tokens([scroll(0, 0), scroll(10, 10), scroll(20, 20), scroll(2020, 30), scroll(2030, 40), scroll(5030, 50)]) == [["SCROLL", "PAGE"]] * 3


def test_wheel_and_stationary_positions_do_not_scroll():
    assert tokens([e("wheel", 0, PAGE, delta_y=100), scroll(10, 5), scroll(20, 5)]) == []
    assert tokens([scroll(0, 100)]) == []  # Missing baseline is not assumed zero.
    assert tokens([scroll(0, 100, previous_scroll_y=0)]) == [["SCROLL", "PAGE"]]


def test_new_action_and_scrollend_split_scrolls():
    events = [scroll(0, 0), scroll(1, 1), e("click", 2, BUTTON), scroll(3, 2), e("scrollend", 4, PAGE), scroll(5, 3)]
    assert [t[0] for t in tokens(events)] == ["SCROLL", "CLICK", "SCROLL", "SCROLL"]


def test_conservative_hover_and_click_absorption():
    assert tokens([e("pointerover", 0, BUTTON, relatedTarget=None), e("click", 100, BUTTON)]) == [["CLICK", "BUTTON"]]
    assert tokens([e("pointerover", 0, BUTTON, relatedTarget=None), e("pointerout", 1000, BUTTON, relatedTarget=None)]) == [["HOVER", "BUTTON"]]
    assert tokens([e("pointerover", 0, BUTTON), e("monitor_stop", 1000)]) == []
    assert tokens([e("pointerover", 0, BUTTON, relatedTarget=None), e("pointerout", 1000, BUTTON, relatedTarget=None), e("click", 1100, BUTTON)]) == [["CLICK", "BUTTON"]]


def test_internal_hover_movement_does_not_create_or_reset_hover():
    child = {"tag": "svg", "ancestors": [BUTTON]}
    r = parse([e("pointerover", 0, BUTTON, relatedTarget=None),
               e("pointerout", 100, BUTTON, relatedTarget=child),
               e("pointerover", 200, child, relatedTarget=BUTTON),
               e("pointerout", 900, BUTTON, relatedTarget=None)])
    assert len(r.actions) == 1 and r.actions[0].duration_ms == 900


def test_link_and_button_navigation_outcome():
    for target in [LINK, BUTTON]:
        events = [e("click", 0, target), e("beforeunload", 10), e("monitor_start", 100, document_id="d2")]
        r = parse(events)
        assert r.to_dict()["action_sequence_content"] == [["CLICK", "LINK" if target == LINK else "BUTTON"]]
        assert r.actions[0].outcome == "NEW_DOCUMENT"
        assert r.actions[0].source_event_end_index == 0


def test_direct_navigation_and_initial_monitor():
    assert tokens([e("monitor_start", 0)]) == []
    assert tokens([e("navigation", 10, url="https://example.test")]) == [["NAVIGATE", "PAGE"]]
    assert tokens([e("monitor_start", 0), e("monitor_start", 10, document_id="d2")]) == [["NAVIGATE", "PAGE"]]


@pytest.mark.parametrize("event", [e("popstate", 0), e("navigation", 0, reason="popstate")])
def test_history_is_not_inferred_back(event):
    r = parse([event])
    assert r.to_dict()["action_sequence_content"] == [["HISTORY_NAV", "PAGE"]]
    assert r.actions[0].subtype is None


def test_history_index_direction_and_document_outcome():
    r = parse([e("popstate", 0, previous_history_index=3, history_index=2), e("monitor_start", 10, document_id="d2")])
    assert len(r.actions) == 1 and r.actions[0].subtype == "BACK"


@pytest.mark.parametrize("boundary", ["document", "session", "monitor_stop", "beforeunload", "pagehide", "navigation", "clock"])
def test_text_hard_boundaries(boundary):
    events = [e("input", 100, INPUT)]
    if boundary == "document":
        events.append(e("input", 110, INPUT, document_id="d2"))
    elif boundary == "session":
        events.append(e("input", 110, INPUT, session_id="other"))
    elif boundary == "clock":
        events.append(e("input", 10, INPUT))
    else:
        events += [e(boundary, 105), e("input", 110, INPUT)]
    r = parse(events)
    texts = [a for a in r.actions if a.action_type == "TEXT_ENTRY"]
    assert len(texts) == 2
    assert texts[0].inter_action_latency_ms is None
    assert all(a.duration_ms == 0 for a in texts)


def test_nested_button_and_option_lifting():
    assert tokens([e("click", 0, {"tag": "svg"}, composedPath=[{"tag": "svg"}, {"tag": "span"}, BUTTON])]) == [["CLICK", "BUTTON"]]
    assert tokens([e("click", 0, {"tag": "svg", "parent": {"tag": "span", "parent": BUTTON}})]) == [["CLICK", "BUTTON"]]
    assert tokens([e("click", 0, {"tag": "option", "ancestors": [SELECT]}), e("change", 1, SELECT)]) == [["SELECT", "SELECT"]]
    assert tokens([e("click", 0, {"tag": "option"})]) == [["CLICK", "OTHER"]]


@pytest.mark.parametrize("node,role", [({"tag": "a"}, "OTHER"), ({"tag": "a", "href": ""}, "LINK"),
    ({"role": "link"}, "LINK"), ({"tag": "textarea"}, "TEXT_INPUT"), ({"tag": "input"}, "TEXT_INPUT"),
    ({"role": "searchbox"}, "TEXT_INPUT"), ({"contenteditable": ""}, "TEXT_INPUT"),
    ({"contenteditable": "false", "tag": "div"}, "CONTAINER"), ({"role": "combobox"}, "SELECT"),
    ({"role": "listbox"}, "SELECT"), ({"role": "switch"}, "TOGGLE"), ({"tag": "body"}, "PAGE"),
    ({"tag": "input", "type": "image"}, "BUTTON"), ({"tag": "span"}, "OTHER")])
def test_role_vocabulary(node, role):
    assert target_role(node) == role


def test_real_mousemoves_preserved_and_raw_is_not_mutated():
    raw = {"events": [e("mousemove", 1), e("mousemove", 2), e("click", 3, BUTTON)], "extra": {"keep": True}}
    original = deepcopy(raw)
    r = parse_semantic_actions(raw)
    assert raw == original and len(r.normalized_events) == 3
    assert all(e.duplicate_of is None for e in r.normalized_events)
    assert r.to_dict()["raw_trace"] == original


def test_ordinary_key_gaps_and_ime_are_one_edit():
    assert tokens([e("input", 0, INPUT), e("input", 5000, INPUT), e("change", 6000, INPUT)]) == [["TEXT_ENTRY", "TEXT_INPUT"]]
    assert tokens([e("compositionstart", 0, INPUT), e("compositionupdate", 20, INPUT), e("input", 40, INPUT), e("compositionend", 60, INPUT)]) == [["TEXT_ENTRY", "TEXT_INPUT"]]
    assert len(tokens([e("input", 0, INPUT), e("input", 50, INPUT)], T_TEXT_ENTRY_IDLE_MS=40)) == 2


def test_equal_length_text_replacement_is_an_edit():
    assert tokens([e("focus", 0, INPUT, value_length=2), e("input", 1, INPUT, value_length=2)]) == [["TEXT_ENTRY", "TEXT_INPUT"]]


def test_target_switch_and_intervening_actions_block_folding():
    other = {**INPUT, "id": "other"}
    assert len(tokens([e("input", 0, INPUT), e("input", 1, other)])) == 2
    assert tokens([e("click", 0, INPUT), e("click", 1, BUTTON), e("input", 2, INPUT)]) == [["CLICK", "TEXT_INPUT"], ["CLICK", "BUTTON"], ["TEXT_ENTRY", "TEXT_INPUT"]]


@pytest.mark.parametrize("target,kind", [(SELECT, "SELECT"), (TOGGLE, "TOGGLE")])
def test_programmatic_control_change_ends_text_interval(target, kind):
    assert tokens([e("input", 0, INPUT), e("change", 1, target), e("input", 2, INPUT)]) == [["TEXT_ENTRY", "TEXT_INPUT"], [kind, kind], ["TEXT_ENTRY", "TEXT_INPUT"]]


def test_epoch_preferred_and_local_fallback_never_crosses_documents():
    r = parse([e("pointerdown", 100, BUTTON, monotonic_ms=1), e("click", 120, BUTTON, monotonic_ms=10), e("click", 150, LINK, monotonic_ms=11)])
    assert r.actions[0].duration_ms == 20 and r.actions[0].inter_action_latency_ms == 30
    r = parse([e("input", None, INPUT, monotonic_ms=10), e("change", None, INPUT, monotonic_ms=25), e("click", None, BUTTON, monotonic_ms=30)])
    assert r.actions[0].duration_ms == 15 and r.actions[0].inter_action_latency_ms == 5
    r = parse([e("click", None, BUTTON, monotonic_ms=30), e("click", None, LINK, document_id="d2", monotonic_ms=40)])
    assert r.actions[0].inter_action_latency_ms is None


def test_mixed_clock_endpoints_and_unobserved_clock_rollback():
    r = parse([e("input", 1000, INPUT, monotonic_ms=10), e("change", None, INPUT, monotonic_ms=20)])
    assert r.actions[0].duration_ms == 10 and r.actions[0].time_basis == "monotonic_ms"
    r = parse([e("input", 1000, INPUT), e("keyup", None, INPUT), e("input", 10, INPUT)])
    assert len(r.actions) == 2 and r.actions[0].inter_action_latency_ms is None


def test_content_and_temporal_are_whitelisted_and_deterministic():
    raw = {"events": [e("click", 1, {**LINK, "text": "Buy SECRET", "css_path": "#secret"}, client_x=987)]}
    first = abstract_events(raw, existing_statistics=format_l3(raw))
    second = abstract_events(deepcopy(raw), existing_statistics=format_l3(raw))
    assert first == second
    assert first["action_sequence_content"] == [["CLICK", "LINK"]]
    assert first["action_sequence_temporal"] == [["CLICK", "LINK", 0, None]]
    assert first["existing_statistics"]["feature_count"] == 98
    json.dumps(first, allow_nan=False)


def test_invalid_entries_keep_original_source_indices_and_no_negative_timing():
    r = parse([None, e("click", 10, BUTTON), "invalid", e("click", 0, BUTTON)])
    assert [a.source_event_start_index for a in r.actions] == [1, 3]
    assert r.actions[0].inter_action_latency_ms is None


def test_navigation_window_sessions_and_intervening_decisions():
    assert [t[0] for t in tokens([e("click", 0, LINK), e("navigation", 6000)])] == ["CLICK", "NAVIGATE"]
    assert [t[0] for t in tokens([e("click", 0, LINK), e("input", 1, INPUT), e("navigation", 2)])] == ["CLICK", "TEXT_ENTRY", "NAVIGATE"]
    assert [t[0] for t in tokens([e("click", 0, LINK), e("monitor_start", 10, session_id="other", document_id="d2")])] == ["CLICK", "NAVIGATE"]


def test_legacy_probe_session_is_document_and_initial_start_not_navigation():
    events = [{"type": "monitor_start", "epoch_ms": 0, "session_id": "page1"},
              {"type": "click", "epoch_ms": 10, "session_id": "page1", "target": LINK},
              {"type": "beforeunload", "epoch_ms": 20, "session_id": "page1"},
              {"type": "monitor_start", "epoch_ms": 100, "session_id": "page2"}]
    assert tokens(events) == [["CLICK", "LINK"]]


def test_same_document_causal_monotonic_clock_but_no_temporal_gap():
    r = parse([e("click", None, LINK, monotonic_ms=10), e("navigation", None, monotonic_ms=20),
               e("click", None, BUTTON, monotonic_ms=30)])
    assert [a.action_type for a in r.actions] == ["CLICK", "CLICK"]
    assert r.actions[0].inter_action_latency_ms is None


def test_missing_clock_absorption_requires_contiguous_mechanical_evidence():
    assert tokens([e("click", None, INPUT), e("focus", None, INPUT), e("input", None, INPUT)]) == [["TEXT_ENTRY", "TEXT_INPUT"]]
    assert len(tokens([e("click", None, INPUT), e("wheel", None, PAGE), e("input", None, INPUT)])) == 2


def test_no_dedup_across_documents_or_different_targets():
    r = parse([e("pointerdown", 0, BUTTON), e("mousedown", 1, SUBMIT),
               e("pointerup", 2, BUTTON), e("mouseup", 3, BUTTON, document_id="d2")])
    assert all(e.duplicate_of is None for e in r.normalized_events)


def test_timing_null_when_no_comparable_clock():
    r = parse([e("input", 1000, INPUT), e("change", None, INPUT, monotonic_ms=20)])
    assert r.actions[0].start_time_ms is None and r.actions[0].duration_ms is None


def test_monotonic_rollback_flushes_despite_increasing_epoch():
    r = parse([e("input", 1000, INPUT, monotonic_ms=10), e("input", 1001, INPUT, monotonic_ms=1)])
    assert len(r.actions) == 2 and r.actions[0].inter_action_latency_ms is None


def test_scroll_and_hover_do_not_cross_documents_or_clock_resets():
    r = parse([scroll(100, 0), scroll(110, 10), scroll(1, 20), scroll(2, 30)])
    assert len(r.actions) == 2
    assert r.actions[0].inter_action_latency_ms is None
    r = parse([e("pointerover", 0, BUTTON, relatedTarget=None),
               e("pointerout", 1000, BUTTON, relatedTarget=None, document_id="d2")])
    assert not any(a.action_type == "HOVER" for a in r.actions)


def test_foreign_enter_does_not_extend_submit_and_focus_does_not_extend_click():
    r = parse([e("keydown", 0, INPUT, key="Enter", form_id="other"), e("submit", 10, FORM)])
    assert r.actions[0].start_time_ms == 10
    r = parse([e("pointerdown", 0, BUTTON), e("input", 1, INPUT), e("click", 2, BUTTON)])
    assert r.actions[-1].duration_ms == 0


def test_frozen_v5_feature_regression():
    fixture = json.loads(Path(__file__).with_name("fixtures").joinpath("v5_regression.json").read_text())
    raw = fixture["raw"]
    assert format_l3(raw) == fixture["expected_v5"]
    result = abstract_events(raw, existing_statistics=format_l3(raw))
    assert result["existing_statistics"] == fixture["expected_v5"]


def test_complete_task_example():
    events = [e("monitor_start", 0), e("click", 10, INPUT), e("focus", 11, INPUT),
              e("keydown", 20, INPUT, key="h"), e("input", 21, INPUT), e("keyup", 22, INPUT, key="h"),
              e("change", 30, INPUT), e("pointerdown", 40, SUBMIT), e("mousedown", 41, SUBMIT),
              e("pointerup", 50, SUBMIT), e("mouseup", 51, SUBMIT), e("click", 52, SUBMIT),
              e("submit", 53, FORM), e("beforeunload", 60),
              e("monitor_start", 100, document_id="d2", scroll_x=0, scroll_y=0),
              e("wheel", 110, PAGE, document_id="d2", delta_y=100),
              scroll(120, 10, document_id="d2"), scroll(130, 20, document_id="d2"),
              e("scrollend", 140, PAGE, document_id="d2"), e("click", 150, LINK, document_id="d2"),
              e("beforeunload", 160, document_id="d2"), e("monitor_start", 200, document_id="d3")]
    r = parse(events)
    assert r.to_dict()["action_sequence_content"] == [["TEXT_ENTRY", "TEXT_INPUT"], ["SUBMIT", "OTHER"], ["SCROLL", "PAGE"], ["CLICK", "LINK"]]
    assert r.actions[1].inter_action_latency_ms is None
    for a in r.actions:
        assert {v["document_id"] for v in events[a.source_event_start_index:a.source_event_end_index + 1]} == {a.document_id}


@pytest.mark.parametrize("value", [-1, float("nan"), float("inf"), True, "bad"])
def test_invalid_config(value):
    with pytest.raises(ValueError):
        SemanticActionConfig(T_SCROLL_GAP_MS=value)


def test_file_cli_preserves_raw_and_emits_all_views(tmp_path):
    raw = {"events": [e("click", 1, BUTTON)]}
    source, destination, debug = tmp_path / "raw.json", tmp_path / "z.json", tmp_path / "debug.txt"
    source.write_text(json.dumps(raw))
    cmd = [sys.executable, "-m", "agent_fingerprint", "extract", "--input-file", str(source), "--output-file", str(destination), "--debug-output", str(debug)]
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(ROOT / "src"), env.get("PYTHONPATH", "")) if part
    )
    subprocess.run(cmd, cwd=tmp_path, env=env, check=True, capture_output=True)
    result = json.loads(destination.read_text())
    assert result["existing_statistics"] == format_l3(raw)
    assert result["raw_trace"] == json.loads(source.read_text()) == raw
    assert "Z[0] CLICK(BUTTON)" in debug.read_text()
    cmd[cmd.index("--output-file") + 1] = str(source)
    assert subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True).returncode != 0
    assert json.loads(source.read_text()) == raw
    cmd[cmd.index("--output-file") + 1] = str(destination)
    cmd += ["--write-semantic-probe", str(source)]
    assert subprocess.run(cmd, cwd=tmp_path, env=env, capture_output=True).returncode != 0
    assert json.loads(source.read_text()) == raw


def test_task_cli_emits_unchanged_feature_file_and_semantic_sidecar(tmp_path):
    directory = tmp_path / "runs/run1/fingerprints"
    directory.mkdir(parents=True)
    raw = {"run_id": "run1", "events": [e("input", 1, INPUT)]}
    (directory / "l3_browser_dynamic.json").write_text(json.dumps(raw))
    out = tmp_path / "results"
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(ROOT / "src"), env.get("PYTHONPATH", "")) if part
    )
    subprocess.run([sys.executable, "-m", "agent_fingerprint", "extract", "--task-id", "run1", "--input-dir", str(tmp_path / "runs"), "--output-dir", str(out)], cwd=ROOT, env=env, check=True, capture_output=True)
    assert json.loads((out / "run1/l3_browser_dynamic.json").read_text()) == format_l3(raw)
    assert json.loads((out / "run1/semantic_actions.json").read_text())["existing_statistics"] == format_l3(raw)
