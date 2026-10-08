import copy
import json
from pathlib import Path

import numpy as np
import pytest

from agent_fingerprint.features.feature_schema import FEATURE_NAMES
from agent_fingerprint.modeling.data import (
    digest,
    event_tokens,
    load_sessions,
    semantic_tokens,
)
from agent_fingerprint.modeling.splits import make_split, validate_split


def test_stat98_order_and_missing_are_preserved(dataset_file):
    sessions = load_sessions(dataset_file)
    assert len(FEATURE_NAMES) == 98
    assert sessions[0].statistical.shape == (98,)
    assert np.isnan(sessions[0].statistical[2:]).all()
    data = json.loads(dataset_file.read_text())
    data["feature_names"].reverse()
    dataset_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="order mismatch"):
        load_sessions(dataset_file)


def test_labels_must_be_explicit_and_match_manifest(dataset_file):
    data = json.loads(dataset_file.read_text())
    data["samples"][0]["agent_label"] = "wrong"
    dataset_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="label mismatch"):
        load_sessions(dataset_file)


def test_source_mutation_is_rejected(dataset_file):
    data = json.loads(dataset_file.read_text())
    data["samples"][0]["raw_sha256"] = "wrong"
    dataset_file.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="hash mismatch"):
        load_sessions(dataset_file)


def test_event_clocks_never_cross_sessions_or_incomparable_documents():
    def event(session, document, mono, epoch=None):
        return {
            "type": "click",
            "session_id": session,
            "document_id": document,
            "monotonic_ms": mono,
            "epoch_ms": epoch,
            "target": {"tag": "button"},
        }

    tokens = event_tokens(
        {
            "events": [
                event("s1", "d1", 10),
                event("s1", "d1", 10),
                event("s1", "d2", 100),
                event("s2", "d2", 120),
                event("s2", "d2", 1),
                event("s2", "d2", 3),
            ]
        }
    )
    assert [t.timing[0] for t in tokens] == [None, 0, None, None, None, 2]
    epoch_tokens = event_tokens(
        {
            "events": [
                event("s", "d1", 10, 100),
                event("s", "d2", 1, 120),
                event("other", "d3", 1, 150),
                event("other", "d3", 2, 140),
            ]
        }
    )
    assert [t.timing[0] for t in epoch_tokens] == [None, 20, None, None]


def test_low_level_type_not_semantic_override():
    raw = {
        "events": [
            {"type": "mousedown", "semantic_event_type": "CLICK"},
            {"type": "click"},
        ]
    }
    assert [t.kind for t in event_tokens(raw)] == ["mousedown", "click"]


def test_semantic_whitelist_and_alignment():
    row = {
        "action_sequence_content": [["CLICK", "BUTTON"]],
        "action_sequence_temporal": [["CLICK", "BUTTON", 0, None]],
        "actual_text": "secret",
        "x": 42,
    }
    assert semantic_tokens(row)[0].timing == (0, None)
    row["action_sequence_temporal"][0][0] = "SCROLL"
    with pytest.raises(ValueError, match="mismatch"):
        semantic_tokens(row)


def test_split_is_stable_and_shared_across_all_representations(sessions):
    split = make_split(sessions, seed=12)
    assert split == make_split(list(reversed(sessions)), seed=12)
    agent = validate_split(sessions, split, "agent")
    llm = validate_split(sessions, split, "llm")
    assert agent == llm
    assert all(agent[p] for p in ("train", "val", "test"))
    assert set(agent["train"]).isdisjoint(agent["test"])


@pytest.mark.parametrize("task", [{}, {"task_id": "shared", "prompt": "same prompt"}])
def test_random_split_does_not_require_or_group_task_metadata(dataset_file, task):
    data = json.loads(dataset_file.read_text())
    for row in data["samples"]:
        manifest_path = Path(row["source_path"]).parent.parent / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["task"] = task
        manifest_path.write_text(json.dumps(manifest))
        raw_path = Path(row["source_path"])
        raw = json.loads(raw_path.read_text())
        for event in raw["events"]:
            event["session_id"] = "shared-browser-session"
        raw_path.write_text(json.dumps(raw))
    sessions = load_sessions(dataset_file)
    split = make_split(sessions)
    assert split["counts"] == {"train": 32, "val": 11, "test": 11}
    assert set(split["assignments"]) == {s.session_id for s in sessions}
    assert set(split["assignments"].values()) == {"train", "val", "test"}
    validate_split(sessions, split)


def test_random_split_rejects_too_few_or_duplicate_runs(sessions):
    with pytest.raises(ValueError, match="at least 3"):
        make_split(sessions[:2])
    with pytest.raises(ValueError, match="unique"):
        make_split(sessions + sessions[:1])


def test_random_split_requires_training_class_coverage(sessions):
    for i, session in enumerate(sessions):
        session.agent = f"unique-{i}"
    with pytest.raises(ValueError, match="training class coverage"):
        make_split(sessions)


def test_old_grouped_manifest_requires_regeneration(sessions):
    split = make_split(sessions)
    split["schema"] = "attribution-split/v1"
    with pytest.raises(ValueError, match="regenerate"):
        validate_split(sessions, split)


def test_loaded_manifest_integrity_and_dataset_are_checked(sessions):
    split = make_split(sessions)
    tampered = copy.deepcopy(split)
    tampered["assignments"][sessions[0].session_id] = "bad"
    with pytest.raises(ValueError, match="checksum"):
        validate_split(sessions, tampered)
    tampered["split_id"] = digest(
        {k: v for k, v in tampered.items() if k != "split_id"}
    )
    with pytest.raises(ValueError, match="Invalid split assignment"):
        validate_split(sessions, tampered)
    sessions[0].agent = "changed"
    with pytest.raises(ValueError, match="different dataset"):
        validate_split(sessions, split)
