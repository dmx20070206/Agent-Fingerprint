"""Reproducible random splits of complete runs, without metadata grouping."""

from collections import Counter

import numpy as np

from .data import dataset_fingerprint, digest

PARTITIONS = ("train", "val", "test")


def _validate_classes(sessions, assignments):
    for target in ("agent", "llm"):
        known = {
            getattr(s, target) for s in sessions if assignments[s.session_id] == "train"
        }
        evaluated = {
            getattr(s, target) for s in sessions if assignments[s.session_id] != "train"
        }
        if not evaluated <= known:
            raise ValueError(f"{target}: evaluation contains a class absent from train")


def make_split(sessions, *, seed=42, val_fraction=0.2, test_fraction=0.2):
    if not (
        0 < val_fraction < 1
        and 0 < test_fraction < 1
        and val_fraction + test_fraction < 1
    ):
        raise ValueError(
            "Validation/test fractions must be positive and sum to less than one"
        )
    sessions = sorted(sessions, key=lambda s: s.session_id)
    size = len(sessions)
    if size < 3:
        raise ValueError("Random split needs at least 3 runs")
    if len({s.session_id for s in sessions}) != size:
        raise ValueError("Expected one unique row per run")
    n_val = max(1, round(size * val_fraction))
    n_test = max(1, round(size * test_fraction))
    if n_val + n_test >= size:
        raise ValueError(
            "Requested fractions leave no training runs; reduce fractions or add runs"
        )
    rng = np.random.default_rng(seed)
    totals = {
        target: Counter(getattr(s, target) for s in sessions)
        for target in ("agent", "llm")
    }
    best = None
    # Choose a seeded random candidate with training class coverage and balanced labels.
    for _ in range(512):
        order = rng.permutation(size)
        assignments = {
            sessions[int(index)].session_id: "val"
            if i < n_val
            else "test"
            if i < n_val + n_test
            else "train"
            for i, index in enumerate(order)
        }
        try:
            _validate_classes(sessions, assignments)
        except ValueError:
            continue
        score = 0.0
        for target, total in totals.items():
            for partition in PARTITIONS:
                observed = Counter(
                    getattr(s, target)
                    for s in sessions
                    if assignments[s.session_id] == partition
                )
                count = sum(observed.values())
                score += sum(
                    abs(observed[k] / count - total[k] / size) for k in sorted(total)
                )
        if best is None or score < best[0]:
            best = score, assignments
    if best is None:
        raise ValueError(
            "No random split found with training class coverage; add runs or increase the training fraction"
        )
    result = {
        "schema": "attribution-split/v2",
        "dataset_fingerprint": dataset_fingerprint(sessions),
        "mode": "random",
        "seed": seed,
        "val_fraction": val_fraction,
        "test_fraction": test_fraction,
        "assignments": dict(sorted(best[1].items())),
        "counts": dict(Counter(best[1].values())),
    }
    result["split_id"] = digest(result)
    validate_split(sessions, result)
    return result


def validate_split(sessions, manifest, target=None):
    if manifest.get("schema") != "attribution-split/v2":
        raise ValueError(
            "Unsupported split manifest schema; regenerate with attribution split"
        )
    if manifest.get("split_id") != digest(
        {k: v for k, v in manifest.items() if k != "split_id"}
    ):
        raise ValueError("Split manifest checksum mismatch")
    if manifest["dataset_fingerprint"] != dataset_fingerprint(sessions):
        raise ValueError("Split belongs to a different dataset")
    assignments = manifest["assignments"]
    if len({s.session_id for s in sessions}) != len(sessions):
        raise ValueError("Expected one unique row per run")
    if set(assignments) != {s.session_id for s in sessions}:
        raise ValueError("Split must assign every dataset run exactly once")
    if set(assignments.values()) - set(PARTITIONS):
        raise ValueError("Invalid split assignment")
    if any(p not in assignments.values() for p in PARTITIONS):
        raise ValueError("Train/validation/test must all be nonempty")
    if manifest.get("mode") != "random":
        raise ValueError("Unsupported split mode; regenerate with attribution split")
    _validate_classes(sessions, assignments)
    return {
        p: [i for i, s in enumerate(sessions) if assignments[s.session_id] == p]
        for p in PARTITIONS
    }
