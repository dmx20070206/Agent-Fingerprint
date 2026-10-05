"""Build one validated L3 sample per run; labels never enter feature vectors."""
from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from pathlib import Path

from .feature_schema import SCHEMA, FEATURE_NAMES
from .l3_browser_dynamic import format_l3
from .selection import add_selection_arguments, selections, site_label, matches, validate_selection


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def build_dataset(input_dir, output_dir, *, sites=None, agents=None, llms=None):
    selection = selections(sites, agents, llms)
    available = {key: set() for key in selection}
    filtered_out = []
    root, destination = Path(input_dir).resolve(), Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError(f'Output directory must be empty: {destination}')
    manifests = sorted(root.rglob('manifest.json'))
    if not manifests:
        raise ValueError(f'No manifests found under {root}')
    rows, excluded, warnings = [], [], []
    seen_ids, seen_hashes = set(), set()
    for path in manifests:
        sample_id = path.parent.relative_to(root).as_posix()
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
            agent = manifest.get('agent') or {}
            site = site_label(path.parent, agent.get('name'), agent.get('model'))
            labels = {'sites': site, 'agents': agent.get('name'), 'llms': agent.get('model')}
            for key, value in labels.items():
                if isinstance(value, str) and value:
                    available[key].add(value)
            if not matches(labels, selection):
                filtered_out.append(sample_id)
                continue
            source = path.parent / 'fingerprints/l3_browser_dynamic.json'
            raw_bytes = source.read_bytes()
            raw = json.loads(raw_bytes)
            run_id = manifest.get('run_id')
            if not isinstance(run_id, str) or not run_id:
                raise ValueError('missing run_id')
            if run_id != path.parent.name or raw.get('run_id') != run_id:
                raise ValueError('directory/manifest/L3 run_id mismatch')
            if run_id in seen_ids:
                raise ValueError('duplicate run_id')
            agent = manifest.get('agent') or {}
            if not all(isinstance(agent.get(k), str) and agent[k].strip() for k in ('name', 'model')):
                raise ValueError('missing agent/model label')
            events = raw.get('events')
            if not isinstance(events, list) or not events:
                raise ValueError('empty or invalid events')
            if not all(isinstance(e, dict) for e in events):
                raise ValueError('non-object event')
            if raw.get('enabled') is False:
                raise ValueError('L3 capture disabled')
            if not any(e.get('type') in {'click', 'pointermove', 'mousemove', 'keydown', 'input', 'scroll', 'mousedown', 'pointerdown'} for e in events):
                raise ValueError('no interaction events')
            event_hash = hashlib.sha256(json.dumps(events, sort_keys=True).encode()).hexdigest()
            if event_hash in seen_hashes:
                raise ValueError('duplicate event trace')
            result = format_l3(raw, manifest)
            task = manifest.get('task') or {}
            # Stable task key excludes ephemeral host ports. Explicit task_id wins.
            task_label = task.get('task_id') or hashlib.sha256(json.dumps(
                [task.get('requested_url'), task.get('prompt')], ensure_ascii=False).encode()).hexdigest()[:16]
            rows.append({'sample_id': sample_id, 'source_path': str(source), 'run_id': run_id,
                         'agent_label': agent['name'], 'llm_label': agent['model'],
                         'site_label': site, 'task_label': task_label, 'status': manifest.get('status'),
                         'raw_sha256': hashlib.sha256(raw_bytes).hexdigest(),
                         'manifest_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                         'event_count': len(events), 'features': result['features']})
            seen_ids.add(run_id)
            seen_hashes.add(event_hash)
            if manifest.get('status') != 'success':
                warnings.append({'sample_id': sample_id, 'reason': 'non-success run retained: trace is usable'})
        except (ValueError, TypeError, KeyError, AttributeError, OSError) as exc:
            excluded.append({'sample_id': sample_id, 'reason': str(exc)})
    validate_selection(selection, available)
    if any(selection.values()) and not rows:
        raise ValueError('No usable runs match the selected sites/agents/llms')
    destination.mkdir(parents=True, exist_ok=True)
    dataset = {'schema': SCHEMA, 'feature_names': list(FEATURE_NAMES), 'samples': rows, 'selection': selection}
    write_json(destination / 'samples.json', dataset)
    report = {'input_dir': str(root), 'runs_found': len(manifests), 'included': len(rows),
              'excluded': excluded, 'warnings': warnings,
              'selection': selection, 'filtered_out': filtered_out,
              'site_counts': dict(Counter(r['site_label'] for r in rows if r['site_label'])),
              'agent_counts': dict(Counter(r['agent_label'] for r in rows)),
              'llm_counts': dict(Counter(r['llm_label'] for r in rows)),
              'missing_counts': {n: sum(r['features'][n] is None for r in rows) for n in FEATURE_NAMES}}
    write_json(destination / 'quality.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-dir', type=Path, default=Path('data/runs/final'))
    parser.add_argument('--output-dir', type=Path, required=True)
    add_selection_arguments(parser)
    args = parser.parse_args()
    print(json.dumps(build_dataset(args.input_dir, args.output_dir, sites=args.sites, agents=args.agents, llms=args.llms), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
