"""Group flat curated runs by task/agent/model; preview unless --apply is set."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


def organize(root, *, apply=False, index=None):
    root = Path(root).resolve()
    if not root.is_dir():
        raise FileNotFoundError(root)
    index = Path(index).resolve() if index else root / 'index.jsonl'
    plans = []
    for source in sorted(root.iterdir()):
        manifest = source / 'manifest.json'
        if not source.is_dir() or not manifest.is_file():
            continue
        data = json.loads(manifest.read_text(encoding='utf-8'))
        from .metadata import site_from_manifest
        task = site_from_manifest(data, source)
        if not task or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', task):
            raise ValueError(f'Cannot determine safe site from manifest: {source}')
        agent, model = data['agent']['name'], data['agent']['model']
        for label in (agent, model):
            if not isinstance(label, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*', label):
                raise ValueError(f'Unsafe directory label: {label!r}')
        target = root / task / agent / model / source.name
        if target.exists():
            raise FileExistsError(target)
        # Validate the index location before moving any data.
        relative = target.relative_to(index.parent).as_posix()
        entry = {'run_id': data.get('run_id', source.name), 'path': relative,
                 'status': data.get('status'), 'started_at': data.get('started_at'),
                 'agent': agent, 'model': model}
        plans.append((source, target, entry))
    if apply and plans:
        # Keep historical entries; the resolver uses the latest entry for an ID.
        previous = index.read_text(encoding='utf-8') if index.exists() else ''
        for source, target, _ in plans:
            target.parent.mkdir(parents=True, exist_ok=True)
            source.rename(target)
        text = previous + ('\n' if previous and not previous.endswith('\n') else '')
        text += ''.join(json.dumps(entry, ensure_ascii=False) + '\n' for _, _, entry in plans)
        temporary = index.with_suffix('.jsonl.tmp')
        temporary.write_text(text, encoding='utf-8')
        temporary.replace(index)
    return [{'source': str(source), 'destination': str(target)} for source, target, _ in plans]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('data/runs/final'))
    parser.add_argument('--index', type=Path, help='Index to update (default: INPUT_DIR/index.jsonl)')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(organize(args.input_dir, apply=args.apply, index=args.index), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
