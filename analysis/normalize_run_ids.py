"""Normalize structured run_id fields to run directory names (preview by default)."""
import argparse
import json
from pathlib import Path

from .dataset import write_json


def normalize(root, apply=False, *, run_ids=None):
    changes = []

    def visit(value, run_id):
        count = 0
        if isinstance(value, dict):
            for key, child in value.items():
                if key == 'run_id' and child != run_id:
                    value[key] = run_id
                    count += 1
                else:
                    count += visit(child, run_id)
        elif isinstance(value, list):
            for child in value:
                count += visit(child, run_id)
        return count

    for manifest in sorted(Path(root).rglob('manifest.json')):
        directory = manifest.parent
        run_id = (run_ids or {}).get(str(directory.resolve()), directory.name)
        for path in sorted(directory.rglob('*')):
            if not path.is_file() or path.suffix not in {'.json', '.jsonl'}:
                continue
            original = path.read_text(encoding='utf-8')
            try:
                value = json.loads(original) if path.suffix == '.json' else [json.loads(line) for line in original.splitlines() if line.strip()]
            except ValueError:
                continue  # Unstructured framework artifacts are not rewritten.
            count = visit(value, run_id)
            if count:
                changes.append({'path': str(path), 'run_id': run_id, 'fields_changed': count})
                if apply:
                    if path.suffix == '.json':
                        write_json(path, value)
                    else:
                        path.write_text(''.join(json.dumps(row, ensure_ascii=False, allow_nan=False) + '\n' for row in value), encoding='utf-8')
    return changes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('data/runs/final'))
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    changes = normalize(args.input_dir, args.apply)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        write_json(args.report, {'applied': args.apply, 'changes': changes})
    print(json.dumps(changes, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
