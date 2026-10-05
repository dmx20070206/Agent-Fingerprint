"""Predict one raw run using a saved classifier and its complete preprocessing."""
import argparse
import json
from pathlib import Path

import joblib
import numpy as np

from .feature_schema import SCHEMA, FEATURE_NAMES
from .l3_browser_dynamic import format_l3
from .episode_features import load_run_metadata


def predict(model_path, raw_path):
    bundle = joblib.load(model_path)
    if bundle['schema'] != SCHEMA or bundle['feature_names'] != list(FEATURE_NAMES):
        raise ValueError('Model feature schema mismatch')
    raw = json.loads(Path(raw_path).read_text(encoding='utf-8'))
    if not raw.get('events') or raw.get('enabled') is False:
        raise ValueError('Cannot predict an empty or disabled trace')
    result = format_l3(raw, load_run_metadata(raw_path))
    X = np.array([[np.nan if result['features'][n] is None else result['features'][n] for n in FEATURE_NAMES]])
    probabilities = bundle['pipeline'].predict_proba(X)[0]
    return {'run_id': raw.get('run_id'), 'target': bundle['target'],
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
