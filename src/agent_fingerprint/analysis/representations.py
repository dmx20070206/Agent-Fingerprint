"""Versioned numeric representations; semantic inputs exclude audit metadata."""
from collections import Counter
from statistics import mean, pstdev


from agent_fingerprint.features.feature_schema import SCHEMA, FEATURE_NAMES
from agent_fingerprint.semantic_actions.schema import ACTION_TYPES, TARGET_ROLES

SEMANTIC_SCHEMA = 'agent-fingerprint-semantic-features/v1'
ACTIONS = sorted(ACTION_TYPES)
PAIRS = [(a, r) for a in ACTIONS for r in sorted(TARGET_ROLES)]
TRANSITIONS = [(a, b) for a in ACTIONS for b in ACTIONS]
SEMANTIC_NAMES = tuple(
    ['action_count'] + [f'count:{a}:{r}' for a, r in PAIRS]
    + [f'transition:{a}:{b}' for a, b in TRANSITIONS]
    + [f'{field}:{stat}' for field in ('duration_ms', 'inter_action_latency_ms')
       for stat in ('observed', 'mean', 'std', 'max')])


def semantic_features(parsed):
    # Use only the explicitly whitelisted content and temporal projections.
    content = parsed['action_sequence_content']
    temporal = parsed['action_sequence_temporal']
    counts = Counter(tuple(pair) for pair in content)
    transitions = Counter((a[0], b[0]) for a, b in zip(content, content[1:]))
    values = [len(content)] + [counts[p] for p in PAIRS] + [transitions[p] for p in TRANSITIONS]
    for index in (2, 3):
        times = [row[index] for row in temporal if row[index] is not None]
        values.extend([len(times), mean(times) if times else None,
                       pstdev(times) if times else None, max(times) if times else None])
    return dict(zip(SEMANTIC_NAMES, values))


def specification(representation):
    if representation == 'statistics':
        return SCHEMA, FEATURE_NAMES, 'features'
    if representation == 'semantic':
        return SEMANTIC_SCHEMA, SEMANTIC_NAMES, 'semantic_features'
    raise ValueError('representation must be statistics or semantic')


def matrix(data, representation):
    import numpy as np
    schema, names, key = specification(representation)
    header = data if representation == 'statistics' else data.get('representations', {}).get('semantic', {})
    if header.get('schema') != schema or header.get('feature_names') != list(names):
        raise ValueError('Feature schema/order mismatch; rebuild the dataset')
    rows = data['samples']
    if any(set(row.get(key, {})) != set(names) for row in rows):
        raise ValueError('Sample feature names mismatch; rebuild the dataset')
    values = [[np.nan if row[key][n] is None else row[key][n] for n in names] for row in rows]
    X = np.array(values, dtype=float).reshape(len(rows), len(names))
    if np.isinf(X).any() or any(v is not None and not np.isfinite(v) for row in rows for v in row[key].values()):
        raise ValueError('Use JSON null for missing values; non-finite numbers are invalid')
    return X
