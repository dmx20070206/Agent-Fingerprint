"""Generate comparison/ablation tables without mixing protocols."""

import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from agent_fingerprint.storage.io import write_json

from .data import digest

REPRESENTATIONS = ("98D", "E", "Z_content", "Z_timing")


def representation_name(config):
    return {"stat98": "98D", "event": "E", "semantic": "Z_" + config["semantic_mode"]}[
        config["representation"]
    ]


def summarize(values):
    if not values:
        return "--"
    return f"{np.mean(values):.3f} ± {np.std(values):.3f} (n={len(values)})"


def table(headers, rows):
    return "\n".join(
        [
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join(["---"] * len(headers)) + " |",
        ]
        + ["| " + " | ".join(map(str, row)) + " |" for row in rows]
    )


def aggregate(root, output_dir):
    protocols, cells, seen, signatures = {}, defaultdict(list), set(), {}
    for path in sorted(Path(root).rglob("config.json")):
        config = json.loads(path.read_text())
        if (
            config.get("schema") != "attribution-experiment/v1"
            or config.get("status") != "completed"
        ):
            continue
        split = json.loads((path.parent / "split_manifest.json").read_text())
        if config["split_id"] != split["split_id"] or split["split_id"] != digest(
            {k: v for k, v in split.items() if k != "split_id"}
        ):
            raise ValueError(f"Invalid split snapshot: {path}")
        protocol = (config["dataset_fingerprint"], config["split_id"])
        representation, target = representation_name(config), config["target"]
        key = (*protocol, representation, target)
        run_key = (*key, config["seed"])
        if run_key in seen:
            raise ValueError(f"Duplicate seed/result would inflate aggregation: {path}")
        seen.add(run_key)
        # Model comparisons may differ in architecture across representations,
        # but multi-seed repetitions within a cell must use one configuration.
        parameters = {
            k: config.get(k)
            for k in (
                "model",
                "epochs",
                "batch_size",
                "lr",
                "weight_decay",
                "class_weight",
                "early_stopping_patience",
                "sequence_config",
            )
        }
        signature = digest(parameters)
        if key in signatures and signatures[key] != signature:
            raise ValueError(
                f"Mixed hyperparameters within an aggregation cell: {path}"
            )
        signatures[key] = signature
        report = json.loads((path.parent / "metrics.json").read_text())
        cells[key].append(float(report["test"]["macro_f1"]))
        protocols[protocol] = split
    if not cells:
        raise ValueError("No completed attribution experiments found")
    sections = [
        "# Attribution results",
        (
            "Test Macro-F1; mean ± population standard deviation across model seeds on the same split. "
            "n=1 has zero estimated spread; these tables do not establish statistical significance."
        ),
    ]
    raw_summary = []
    for protocol, split in sorted(protocols.items()):
        label = f"{split['mode']} / split={split['split_id'][:12]}"
        sections += [
            "## " + label,
            table(
                ["Representation", "Agent Macro-F1", "LLM Macro-F1"],
                [
                    [
                        r,
                        summarize(cells[(*protocol, r, "agent")]),
                        summarize(cells[(*protocol, r, "llm")]),
                    ]
                    for r in REPRESENTATIONS
                ],
            ),
            "### Timing ablation",
            table(
                ["Semantic Representation", "Agent F1", "LLM F1"],
                [
                    [
                        r,
                        summarize(cells[(*protocol, r, "agent")]),
                        summarize(cells[(*protocol, r, "llm")]),
                    ]
                    for r in ("Z_content", "Z_timing")
                ],
            ),
        ]
    for key, scores in sorted(cells.items()):
        if scores:
            raw_summary.append(
                {
                    "dataset_fingerprint": key[0],
                    "split_id": key[1],
                    "representation": key[2],
                    "target": key[3],
                    "n": len(scores),
                    "mean": float(np.mean(scores)),
                    "std": float(np.std(scores)),
                    "scores": scores,
                }
            )
    destination = Path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "tables.md").write_text("\n\n".join(sections) + "\n")
    write_json(destination / "summary.json", raw_summary)
    return raw_summary
