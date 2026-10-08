"""CLI implementation for extracting statistical features and semantic actions."""
import argparse
from pathlib import Path

from agent_fingerprint.semantic_actions.pipeline import parse_semantic_actions
from agent_fingerprint.semantic_actions.schema import SemanticActionConfig


def main(argv=None):
    from agent_fingerprint.features.l3_browser_dynamic import format_l3
    from agent_fingerprint.analysis._common import paths, read_json, write_json
    from agent_fingerprint.features.episode_features import load_run_metadata

    parser = argparse.ArgumentParser(description="Extract unchanged v5 features and semantic-actions/v1 from raw L3 events")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--task-id")
    source.add_argument("--input-file", type=Path, help="Raw JSON trace or event array")
    parser.add_argument("--input-dir", type=Path, default=Path("data/runs"))
    parser.add_argument("--output-dir", type=Path, default=Path("data/results"))
    parser.add_argument("--output-file", type=Path, help="Combined E/Z/v5 output (default: <input>.semantic.json in file mode)")
    parser.add_argument("--semantic-config", type=Path, help="JSON object of SemanticActionConfig overrides")
    parser.add_argument("--debug-output", type=Path, help="Write full raw/candidate/reduction/final audit trace")
    parser.add_argument("--debug-session", help="Restrict debug trace to this session ID")
    parser.add_argument("--write-semantic-probe", type=Path, help="Generate an enhanced probe using the existing collector")
    args = parser.parse_args(argv)
    def write_probe():
        from agent_fingerprint.semantic_actions.collector import build_probe
        source = build_probe()
        args.write_semantic_probe.parent.mkdir(parents=True, exist_ok=True)
        args.write_semantic_probe.write_text(source, encoding="utf-8")
        print(f"Semantic collector: {args.write_semantic_probe}")

    if not args.task_id and not args.input_file:
        if args.write_semantic_probe:
            write_probe()
            return
        parser.error("--input-file or --task-id is required")
    legacy_destination = None
    if args.task_id:
        input_path, legacy_destination = paths(args.task_id, args.input_dir, args.output_dir, "l3_browser_dynamic.json")
        destination = args.output_file or legacy_destination.with_name("semantic_actions.json")
    else:
        input_path = args.input_file
        destination = args.output_file or input_path.with_name(input_path.stem + ".semantic.json")
    # The raw source must survive even an accidental --output-file typo.
    outputs = [p for p in (legacy_destination, destination, args.debug_output, args.write_semantic_probe) if p]
    resolved = [p.resolve() for p in outputs]
    if input_path.resolve() in resolved or len(set(resolved)) != len(resolved):
        parser.error("input, feature output, semantic output, debug output and probe output must be distinct paths")
    config = SemanticActionConfig(**read_json(args.semantic_config)) if args.semantic_config else SemanticActionConfig()
    raw = read_json(input_path)
    if isinstance(raw, list):
        raw = {"events": raw}
    parsed = parse_semantic_actions(raw, config)
    statistics = format_l3(raw, load_run_metadata(input_path))
    serialized = parsed.to_json(statistics)
    if args.write_semantic_probe:
        write_probe()
    if legacy_destination:
        write_json(legacy_destination, statistics)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(serialized, encoding="utf-8")
    if args.debug_output:
        args.debug_output.parent.mkdir(parents=True, exist_ok=True)
        args.debug_output.write_text(parsed.debug_trace(args.debug_session), encoding="utf-8")
    print(f"{len(raw.get('events', []))} events -> {len(parsed.actions)} semantic actions; {statistics['feature_count']} v5 features -> {destination}")
