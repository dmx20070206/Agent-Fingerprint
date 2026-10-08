"""Unified attribution CLI; legacy analysis commands remain available."""

import argparse
import json
from dataclasses import fields, replace
from pathlib import Path

from agent_fingerprint.storage.io import read_json, write_json

from .data import load_sessions
from .splits import make_split


def boolean(value):
    if value.lower() not in {"true", "false"}:
        raise argparse.ArgumentTypeError("Expected true or false")
    return value.lower() == "true"


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    commands = result.add_subparsers(dest="command", required=True)
    audit = commands.add_parser(
        "audit", help="Inspect lengths, labels and random split feasibility"
    )
    audit.add_argument("--dataset", type=Path, required=True)
    audit.add_argument("--output", type=Path, required=True)
    split = commands.add_parser(
        "split",
        help="Create one manifest reused by all representations and model seeds",
    )
    split.add_argument("--dataset", type=Path, required=True)
    split.add_argument("--output", type=Path, required=True)
    split.add_argument(
        "--seed", type=int, default=42, help="Split seed; separate from model seeds"
    )
    split.add_argument("--val-fraction", type=float, default=0.2)
    split.add_argument("--test-fraction", type=float, default=0.2)
    train = commands.add_parser(
        "train", help="Train exactly one representation and one target"
    )
    train.add_argument("--dataset", type=Path, required=True)
    train.add_argument("--split-manifest", type=Path, required=True)
    train.add_argument("--output-dir", type=Path, required=True)
    train.add_argument(
        "--representation", choices=["stat98", "event", "semantic"], required=True
    )
    train.add_argument("--target", choices=["agent", "llm"], required=True)
    train.add_argument(
        "--semantic-mode", choices=["content", "timing"], default="content"
    )
    train.add_argument("--seed", type=int, default=42)
    train.add_argument("--seeds", type=int, nargs="+")
    for name, default in [
        ("batch-size", 16),
        ("epochs", 50),
        ("early-stopping-patience", 8),
        ("threads", 2),
        ("xgb-estimators", 200),
        ("xgb-max-depth", 4),
        ("d-model", 128),
        ("n-heads", 4),
        ("num-layers", 2),
        ("ffn-dim", 256),
    ]:
        train.add_argument("--" + name, type=int, default=default)
    for name, default in [
        ("lr", 1e-4),
        ("weight-decay", 1e-2),
        ("xgb-learning-rate", 0.05),
        ("dropout", 0.1),
    ]:
        train.add_argument("--" + name, type=float, default=default)
    train.add_argument(
        "--max-seq-len",
        type=int,
        help="Token limit excluding CLS; default is ceil(train P95)",
    )
    train.add_argument(
        "--use-page-boundary",
        type=boolean,
        default=None,
        help="Default auto: enabled only if training document IDs are reliable",
    )
    train.add_argument("--class-weight", choices=["none", "balanced"], default="none")
    train.add_argument("--device", default="cpu")
    overfit = commands.add_parser(
        "overfit", help="Memorize 16–32 sessions; diagnostic only"
    )
    overfit.add_argument("--dataset", type=Path, required=True)
    overfit.add_argument("--output-dir", type=Path, required=True)
    overfit.add_argument("--target", choices=["agent", "llm"], default="agent")
    overfit.add_argument("--size", type=int, default=24)
    overfit.add_argument("--epochs", type=int, default=300)
    overfit.add_argument("--seed", type=int, default=42)
    overfit.add_argument("--device", default="cpu")
    overfit.add_argument(
        "--unique-content",
        action="store_true",
        help="Diagnostic only: select distinct content sequences to avoid contradictory identical inputs",
    )
    inspect = commands.add_parser(
        "inspect", help="Inspect samples/batches using saved preprocessing"
    )
    inspect.add_argument("--dataset", type=Path, required=True)
    inspect.add_argument("--checkpoint", type=Path, required=True)
    inspect.add_argument("--session-id", action="append", required=True)
    inspect.add_argument("--output", type=Path, required=True)
    evaluation = commands.add_parser(
        "evaluate",
        help="Reload a checkpoint and evaluate its frozen split without fitting",
    )
    evaluation.add_argument("--dataset", type=Path, required=True)
    evaluation.add_argument("--experiment-dir", type=Path, required=True)
    evaluation.add_argument("--output-dir", type=Path, required=True)
    evaluation.add_argument("--device", default="cpu")
    summary = commands.add_parser(
        "aggregate", help="Generate comparison and timing tables"
    )
    summary.add_argument("--input-dir", type=Path, required=True)
    summary.add_argument("--output-dir", type=Path, required=True)
    return result


def main():
    argument_parser = parser()
    args = argument_parser.parse_args()
    try:
        if args.command == "aggregate":
            from .aggregate import aggregate

            result = aggregate(args.input_dir, args.output_dir)
            print(
                f"Aggregated {len(result)} cells into {args.output_dir / 'tables.md'}"
            )
            return 0
        sessions = load_sessions(args.dataset)
        if args.command == "evaluate":
            from .inference import evaluate_saved_experiment

            result = evaluate_saved_experiment(
                sessions, args.experiment_dir, args.output_dir, device=args.device
            )
            print(json.dumps(result["test"], ensure_ascii=False, indent=2))
            return 0
        if args.command in {"audit", "split"}:
            if args.output.exists():
                raise ValueError(f"Refusing to overwrite {args.output}")
            if args.command == "audit":
                from .audit import audit_sessions

                result = audit_sessions(sessions)
            else:
                result = make_split(
                    sessions,
                    seed=args.seed,
                    val_fraction=args.val_fraction,
                    test_fraction=args.test_fraction,
                )
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(
                json.dumps(
                    result if args.command == "audit" else result["counts"],
                    ensure_ascii=False,
                    indent=2,
                )
            )
        elif args.command == "train":
            from .models import TransformerConfig
            from .training import TrainingConfig, train_experiment

            config = TrainingConfig(
                **{f.name: getattr(args, f.name) for f in fields(TrainingConfig)}
            )
            model_config = TransformerConfig(
                **{
                    name: getattr(args, name)
                    for name in (
                        "d_model",
                        "n_heads",
                        "num_layers",
                        "ffn_dim",
                        "dropout",
                    )
                }
            )
            seeds = args.seeds or [args.seed]
            if len(seeds) != len(set(seeds)):
                raise ValueError("Duplicate model seeds are not allowed")
            manifest = read_json(args.split_manifest)
            for seed in seeds:
                destination = (
                    args.output_dir / f"seed_{seed}" if args.seeds else args.output_dir
                )
                result = train_experiment(
                    sessions,
                    manifest,
                    destination,
                    replace(config, seed=seed),
                    model_config,
                )
                print(
                    json.dumps(
                        {"seed": seed, "test": result["test"]}, ensure_ascii=False
                    ),
                    flush=True,
                )
        elif args.command == "overfit":
            from .overfit import run_overfit

            result = run_overfit(
                sessions,
                args.output_dir,
                target=args.target,
                size=args.size,
                epochs=args.epochs,
                seed=args.seed,
                device=args.device,
                unique_content=args.unique_content,
            )
            return 0 if result["passed"] else 1
        elif args.command == "inspect":
            from .audit import inspect_batch, inspect_sample
            from .training import load_checkpoint

            _, preprocessor, classes, target = load_checkpoint(args.checkpoint)
            by_id = {s.session_id: s for s in sessions}
            if set(args.session_id) - by_id.keys():
                raise ValueError("Requested session_id not found in dataset")
            selected = [by_id[sid] for sid in args.session_id]
            result = {
                "samples": [
                    inspect_sample(s, preprocessor, target, classes) for s in selected
                ],
                "batch": inspect_batch(selected, preprocessor, target, classes),
            }
            args.output.parent.mkdir(parents=True, exist_ok=True)
            write_json(args.output, result)
            print(f"Wrote input inspection to {args.output}")
    except (ValueError, OSError, KeyError) as exc:
        argument_parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
