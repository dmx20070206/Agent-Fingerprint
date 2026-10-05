"""Archive runs with globally unique short IDs; preview unless --apply is set."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .normalize_run_ids import normalize


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def prepare(root, *, apply=False, index=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    index = Path(index).resolve() if index else root / 'index.jsonl'
    history_path = root / 'run_names.json'
    history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else []
    manifests = sorted(root.rglob('manifest.json'))
    if not manifests:
        raise ValueError(f'No manifests found under {root}')
    empty_directories = []
    for directory in sorted(root.glob('*/*/*/*')):
        if (root / directory.relative_to(root).parts[0] / 'manifest.json').is_file():
            continue  # Nested framework artifacts belonging to a flat run.
        if directory.is_dir() and not (directory / 'manifest.json').exists():
            if any(directory.iterdir()):
                raise ValueError(f'Nonempty run directory is missing manifest.json: {directory}')
            empty_directories.append(directory)
    used = {entry['run_id'] for entry in history}
    existing = set()
    records = []
    for manifest in manifests:
        source = manifest.parent
        data = json.loads(manifest.read_text(encoding='utf-8'))
        agent, model = data['agent']['name'], data['agent']['model']
        for label in (agent, model):
            if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', label):
                raise ValueError(f'Unsafe directory label: {label!r}')
        parts = source.relative_to(root).parts
        if len(parts) == 4:
            task, parent_agent, parent_model, _ = parts
            if (parent_agent, parent_model) != (agent, model):
                raise ValueError(f'Directory labels disagree with manifest: {source}')
        elif len(parts) == 1:
            match = re.search(r'_(flights|shop|forums?|keyboard|mouse_move|mouse_click|wheel_scroll)(?:_\d+)?$', source.name)
            if not match:
                raise ValueError(f'Cannot determine task: {source}')
            task = {'forums': 'forum'}.get(match[1], match[1])
        else:
            raise ValueError(f'Expected flat or task/agent/model/run layout: {source}')
        if re.fullmatch(r'run_\d{4,}', source.name):
            if source.name in existing:
                raise ValueError(f'Duplicate short run ID: {source.name}')
            existing.add(source.name)
            used.add(source.name)
        records.append((source, data, task, agent, model))

    number = max((int(name[4:]) for name in used if re.fullmatch(r'run_\d{4,}', name)), default=0)
    plans, entries, additions = [], [], []
    for source, data, task, agent, model in records:
        if source.name in existing:
            run_id = source.name
        else:
            number += 1
            run_id = f'run_{number:04d}'
        target = root / task / agent / model / run_id
        if target != source and target.exists():
            raise FileExistsError(target)
        entry = {'run_id': run_id, 'path': target.relative_to(index.parent).as_posix(),
                 'status': data.get('status'), 'started_at': data.get('started_at'),
                 'agent': agent, 'model': model}
        entries.append(entry)
        if target != source:
            change = {'source': source.relative_to(root).as_posix(),
                      'destination': target.relative_to(root).as_posix(),
                      'old_run_id': data.get('run_id'), 'run_id': run_id}
            plans.append((source, target))
            additions.append(change)

    # Read and validate the index before making any changes. Preserve unrelated runs.
    previous = index.read_text(encoding='utf-8') if index.exists() else ''
    old_entries = [json.loads(line) for line in previous.splitlines() if line.strip()]
    current = {entry['run_id']: entry for entry in old_entries}
    updates = [entry for entry in entries if current.get(entry['run_id']) != entry]
    id_changes = normalize(root, run_ids={str(source): target.name for source, target in plans}) if not apply else []
    if apply:
        # Persist the mapping first so an interrupted migration retains its provenance.
        if additions:
            atomic_json(history_path, history + additions)
        for source, target in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        id_changes = normalize(root, apply=True)
        for directory in empty_directories:
            directory.rmdir()
        if updates:
            text = previous + ('\n' if previous and not previous.endswith('\n') else '')
            text += ''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in updates)
            temporary = index.with_name(index.name + '.tmp')
            temporary.write_text(text, encoding='utf-8')
            temporary.replace(index)
    return {'applied': apply, 'runs_found': len(records), 'renamed': additions,
            'empty_directories': [str(path) for path in empty_directories],
            'normalized_files': id_changes, 'index_updates': len(updates),
            'mapping': str(history_path), 'index': str(index)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('data/runs/final'))
    parser.add_argument('--index', type=Path)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = prepare(args.input_dir, apply=args.apply, index=args.index)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
