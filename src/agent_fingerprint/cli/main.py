"""Unified CLI; subcommands load only their own dependencies."""
import sys


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    commands = {
        "collect": "agent_fingerprint.cli.collect",
        "experiment": "agent_fingerprint.cli.experiment",
        "extract": "agent_fingerprint.cli.extract",
        "prepare": "agent_fingerprint.storage.prepare_runs",
        "normalize": "agent_fingerprint.storage.normalize_run_ids",
        "organize": "agent_fingerprint.storage.organize_runs",
        "migrate": "agent_fingerprint.storage.migrate",
        "dataset": "agent_fingerprint.analysis.dataset",
        "train": "agent_fingerprint.analysis.train",
        "attribution": "agent_fingerprint.modeling.cli",
        "test": "agent_fingerprint.analysis.test_models",
        "predict": "agent_fingerprint.analysis.predict",
        "plot": "agent_fingerprint.analysis.plot_results",
    }
    if not args or args[0] in {"-h", "--help"}:
        print("Usage: af <command> [options]\nCommands: " + ", ".join(commands))
        return 0
    command = args.pop(0)
    if command not in commands:
        print(f"Unknown command: {command}", file=sys.stderr)
        return 2
    from importlib import import_module
    module = import_module(commands[command])
    previous = sys.argv
    try:
        sys.argv = ["af " + command, *args]
        return module.main()
    finally:
        sys.argv = previous
