"""Expand a versioned experiment matrix into reproducible collection commands."""
import argparse
from datetime import datetime, timezone
import itertools
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid

import yaml
from agent_fingerprint.paths import PROJECT_ROOT
from agent_fingerprint.storage.io import write_json


def resolve_option(value):
    """Expand the supported ${NAME:-default} notation without running a shell."""
    if not isinstance(value, str):
        return value
    def replace(match):
        return os.environ.get(match[1]) or match[2]
    expanded = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*):-([^{}]*)\}", replace, value)
    if "${" in expanded:
        raise ValueError(f"Unsupported environment expression: {value}")
    return expanded


def build_plan(config, *, agents=None, models=None, tasks=None, repeats=None):
    matrix = config["matrix"]
    selected = [agents or matrix["agents"], models or matrix["models"], tasks or matrix["tasks"]]
    for values, allowed, label in zip(selected, (config["agents"], config["models"], config["tasks"]), ("agents", "models", "tasks")):
        unknown = set(values) - set(allowed)
        if unknown:
            raise ValueError(f"Unknown {label}: {sorted(unknown)}")
    count = config.get("repeats", 1) if repeats is None else repeats
    if type(count) is not int or count < 1:
        raise ValueError("repeats must be a positive integer")
    rows = []
    for agent, model, task in itertools.product(*selected):
        options = {**config.get("defaults", {}), **config["tasks"][task],
                   **config["agents"][agent], **(config["models"][model] or {}),
                   **config.get("overrides", {}).get(f"{agent}/{model}/{task}", {})}
        options.update(agent=agent, model=model)
        for iteration in range(1, count + 1):
            argv = []
            for key, value in options.items():
                if value is None or value is False:
                    continue
                argv.append("--" + key.replace("_", "-"))
                if value is not True:
                    argv.append(str(resolve_option(value)))
            rows.append({"agent": agent, "model": model, "task": task, "repeat": iteration, "argv": argv})
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=PROJECT_ROOT / "configs/experiments/default.yaml")
    for name in ("agents", "models", "tasks"):
        parser.add_argument("--" + name, help="Comma-separated selection")
    parser.add_argument("--repeats", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args(argv)
    config = yaml.safe_load(args.config.read_text())
    if config.get("schema") != "agent-fingerprint-experiment/v1":
        parser.error("Unsupported experiment schema")
    try:
        plan = build_plan(config, **{name: getattr(args, name).split(",") if getattr(args, name) else None
                                   for name in ("agents", "models", "tasks")}, repeats=args.repeats)
        # Validate the entire matrix before starting any expensive collection.
        from .collect import _build_parser
        for row in plan:
            _build_parser().parse_args(row["argv"])
    except (ValueError, KeyError) as exc:
        parser.error(str(exc))
    if args.dry_run:
        print(json.dumps(plan, ensure_ascii=False, indent=2))
        return 0
    name = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:8]
    report = args.report or PROJECT_ROOT / "data/experiments" / name / "collection.json"
    if report.exists():
        parser.error(f"Report already exists: {report}")
    report.parent.mkdir(parents=True, exist_ok=True)
    snapshot = {"schema": config["schema"], "config": config, "plan": plan, "results": []}
    write_json(report, snapshot)
    for row in plan:
        command = [sys.executable, "-m", "agent_fingerprint", "collect", *row["argv"]]
        if row["agent"] == "agente":
            command = ["bash", str(PROJECT_ROOT / "scripts/agente/execute.sh"), *command]
        result = subprocess.run(command, cwd=PROJECT_ROOT)
        snapshot["results"].append({**row, "returncode": result.returncode})
        write_json(report, snapshot)
    print(f"Experiment report: {report}")
    return 1 if any(row["returncode"] != 0 for row in snapshot["results"]) else 0
