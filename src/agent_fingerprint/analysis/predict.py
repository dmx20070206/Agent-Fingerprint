"""Predict one raw run using a saved classifier and its complete preprocessing."""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from .representations import specification, semantic_features
from agent_fingerprint.semantic_actions.pipeline import abstract_events
from agent_fingerprint.features.l3_browser_dynamic import format_l3
from agent_fingerprint.features.episode_features import load_run_metadata


def predict(model_path, raw_path):
    bundle = joblib.load(model_path)
    representation = bundle.get('representation', 'statistics')
    schema, names, _ = specification(representation)
    if bundle['schema'] != schema or bundle['feature_names'] != list(names):
        raise ValueError('Model feature schema mismatch')
    raw = json.loads(Path(raw_path).read_text(encoding='utf-8'))
    if not raw.get('events') or raw.get('enabled') is False:
        raise ValueError('Cannot predict an empty or disabled trace')
    features = (semantic_features(abstract_events(raw)) if representation == 'semantic'
                else format_l3(raw, load_run_metadata(raw_path))['features'])
    X = np.array([[np.nan if features[n] is None else features[n] for n in names]])
    probabilities = bundle['pipeline'].predict_proba(X)[0]
    return {'run_id': raw.get('run_id'), 'target': bundle['target'], 'representation': representation,
            'predicted': str(bundle['pipeline'].predict(X)[0]),
            'probabilities': dict(zip(bundle['classes'], probabilities.tolist()))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--model', type=Path, required=True)
    parser.add_argument('--input', type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(predict(args.model, args.input), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
