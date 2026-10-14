"""Archive runs with short IDs local to each task/agent/model folder; preview unless --apply is set."""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import defaultdict
from pathlib import Path

from .normalize_run_ids import normalize
from .metadata import site_from_manifest
from .run_store import iter_run_manifests


def atomic_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    temporary.replace(path)


def _prepare_legacy(root, *, apply=False, index=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    if any(root.glob('.prepare-*')):
        raise ValueError('Unfinished preparation staging directory found; recover it before retrying')
    index = Path(index).resolve() if index else root / 'index.jsonl'
    history_path = root / 'run_names.json'
    history = json.loads(history_path.read_text(encoding='utf-8')) if history_path.exists() else []
    manifests = sorted(iter_run_manifests(root))
    run_directories = {manifest.parent for manifest in manifests}
    if not manifests:
        raise ValueError(f'No manifests found under {root}')
    empty_directories = []
    for directory in sorted(root.glob('*/*/*/*')):
        if any(parent in run_directories for parent in directory.parents):
            continue  # Nested framework artifacts belonging to a flat run.
        if directory.is_dir() and not (directory / 'manifest.json').exists():
            if any(directory.iterdir()):
                raise ValueError(f'Nonempty run directory is missing manifest.json: {directory}')
            empty_directories.append(directory)
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
            _, parent_agent, parent_model, _ = parts
            if (parent_agent, parent_model) != (agent, model):
                raise ValueError(f'Directory labels disagree with manifest: {source}')
        task = site_from_manifest(data, source)
        if (not isinstance(task, str) or task == 'unknown'
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', task)):
            raise ValueError(f'Cannot determine safe task/site; set manifest task.site: {source}')
        if re.fullmatch(r'run_\d{4,}', source.name):
            identity = (task, agent, model, source.name)
            if identity in existing:
                raise ValueError(f'Duplicate short run ID: {source.name}')
            existing.add(identity)
        records.append((source, data, task, agent, model))

    # Existing numbered runs precede new acquisitions, in numeric order.
    records.sort(key=lambda row: (row[2:],
                 (0, int(row[0].name[4:])) if re.fullmatch(r'run_\d{4,}', row[0].name)
                 else (1, str(row[0]))))
    numbers = defaultdict(int)
    plans, entries, additions = [], [], []
    for source, data, task, agent, model in records:
        group = (task, agent, model)
        numbers[group] += 1
        run_id = f'run_{numbers[group]:04d}'
        target = root / task / agent / model / run_id
        if target != source and target.exists() and target not in run_directories:
            raise FileExistsError(target)
        entry = {'run_id': run_id, 'path': target.relative_to(index.parent).as_posix(),
                 'status': data.get('status'), 'started_at': data.get('started_at'),
                 'agent': agent, 'model': model, 'site': task}
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
    current = {entry['path']: entry for entry in old_entries}
    updates = [entry for entry in entries if current.get(entry['path']) != entry]
    retained = [entry for entry in old_entries
                if not (index.parent / entry['path']).resolve().is_relative_to(root)]
    new_entries = retained + entries
    index_changed = old_entries != new_entries
    id_changes = normalize(root, from_directory=True,
                           run_ids={str(source): target.name for source, target in plans})
    if apply:
        # Persist the mapping first so an interrupted migration retains its provenance.
        if additions:
            atomic_json(history_path, history + additions)
        # Stage every move before filling destinations, including occupied old IDs.
        if plans:
            staging = Path(tempfile.mkdtemp(prefix='.prepare-', dir=root))
            atomic_json(staging / 'recovery.json', [
                {'staged': str(number), 'source': str(source), 'destination': str(target)}
                for number, (source, target) in enumerate(plans)])
            for number, (source, target) in enumerate(plans):
                source.rename(staging / str(number))
            for number, (source, target) in enumerate(plans):
                target.parent.mkdir(parents=True, exist_ok=True)
                (staging / str(number)).rename(target)
            (staging / 'recovery.json').unlink()
            staging.rmdir()
        id_changes = normalize(root, apply=True, from_directory=True)
        for directory in empty_directories:
            directory.rmdir()
        if index_changed:
            index.parent.mkdir(parents=True, exist_ok=True)
            text = ''.join(json.dumps(entry, ensure_ascii=False) + '\n' for entry in new_entries)
            temporary = index.with_name(index.name + '.tmp')
            temporary.write_text(text, encoding='utf-8')
            temporary.replace(index)
    return {'applied': apply, 'runs_found': len(records), 'renamed': additions,
            'empty_directories': [str(path) for path in empty_directories],
            'normalized_files': id_changes, 'index_updates': len(updates),
            'mapping': str(history_path), 'index': str(index)}


def prepare(root, *, apply=False, index=None, legacy_short_ids=False):
    if legacy_short_ids:
        return _prepare_legacy(root, apply=apply, index=index)
    from .prepare import prepare_stable
    return prepare_stable(root, apply=apply, index=index)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('data/runs/final'))
    parser.add_argument('--index', type=Path)
    parser.add_argument('--short-ids', '--legacy-short-ids', dest='legacy_short_ids',
                        action='store_true', help='Group by task/agent/model and number each group from run_0001; records old/new mapping')
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--report', type=Path)
    args = parser.parse_args()
    report = prepare(args.input_dir, apply=args.apply, index=args.index, legacy_short_ids=args.legacy_short_ids)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(args.report, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
