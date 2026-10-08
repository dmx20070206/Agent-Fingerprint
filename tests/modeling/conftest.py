"""Small, explicitly synthetic trajectories with independent task instances."""

import json

import numpy as np
import pytest

from agent_fingerprint.features.feature_schema import FEATURE_NAMES, SCHEMA
from agent_fingerprint.modeling.data import Session, Token


@pytest.fixture
def sessions():
    records = []
    for agent in range(3):
        for llm in range(3):
            for repeat in range(6):
                sid = f"a{agent}-m{llm}-r{repeat}"
                records.append(
                    Session(
                        sid,
                        f"a{agent}",
                        f"m{llm}",
                        np.array([float(agent), float(llm)] + [np.nan] * 96),
                        [
                            Token("click", "BUTTON", (None,)),
                            Token("input", "TEXT_INPUT", (llm * 10.0,)),
                        ]
                        * (agent + 1),
                        [
                            Token(
                                ["CLICK", "SCROLL", "TEXT_ENTRY"][agent],
                                ["BUTTON", "PAGE", "TEXT_INPUT"][agent],
                                (float(llm), None),
                            )
                        ]
                        * (repeat % 3 + 1),
                        ["d1"] * (repeat % 3 + 1),
                    )
                )
    return records


@pytest.fixture
def dataset_file(tmp_path, sessions):
    rows = []
    for i, session in enumerate(sessions):
        run = tmp_path / "runs" / session.session_id
        (run / "fingerprints").mkdir(parents=True)
        source = run / "fingerprints/l3_browser_dynamic.json"
        source.write_text(
            json.dumps(
                {
                    "run_id": session.session_id,
                    "events": [
                        {
                            "type": "click",
                            "session_id": session.session_id,
                            "epoch_ms": i * 100 + j,
                            "target": {"tag": "button"},
                        }
                        for j in range(i % 4 + 1)
                    ],
                }
            )
        )
        (run / "manifest.json").write_text(
            json.dumps(
                {
                    "run_id": session.session_id,
                    "agent": {"name": session.agent, "model": session.llm},
                    "task": {
                        "task_id": f"t-{i}",
                        "prompt": f"synthetic independent task {i}",
                    },
                }
            )
        )
        rows.append(
            {
                "run_id": session.session_id,
                "agent_label": session.agent,
                "llm_label": session.llm,
                "source_path": str(source),
                "features": {
                    name: (None if np.isnan(v) else float(v))
                    for name, v in reversed(
                        list(zip(FEATURE_NAMES, session.statistical))
                    )
                },
                "action_sequence_content": [[t.kind, t.role] for t in session.semantic],
                "action_sequence_temporal": [
                    [t.kind, t.role, *t.timing] for t in session.semantic
                ],
            }
        )
    path = tmp_path / "samples.json"
    path.write_text(
        json.dumps(
            {"schema": SCHEMA, "feature_names": list(FEATURE_NAMES), "samples": rows}
        )
    )
    return path
