"""Dataset feasibility and model-input inspection without training."""

import math
from collections import Counter, defaultdict

from .data import dataset_fingerprint, length_summary
from .splits import make_split


def content_ambiguity(sessions, target):
    """Empirical ceiling for deterministic content-only predictions on these rows."""
    groups = defaultdict(list)
    for session in sessions:
        groups[tuple((t.kind, t.role) for t in session.semantic)].append(session)
    correct, entropy, collisions = 0, 0.0, []
    for group in groups.values():
        counts = Counter(getattr(s, target) for s in group)
        correct += max(counts.values())
        entropy -= sum(n * math.log(n / len(group)) for n in counts.values())
        if len(counts) > 1:
            collisions.append(
                {"session_ids": [s.session_id for s in group], "labels": dict(counts)}
            )
    return {
        "conflicting_groups": collisions,
        "accuracy_ceiling": correct / len(sessions),
        "cross_entropy_lower_bound": entropy / len(sessions),
        "scope": "This finite sample; content only, no PAGE_BOUNDARY, no population inference",
    }


def audit_sessions(sessions):
    report = {
        "dataset_fingerprint": dataset_fingerprint(sessions),
        "sessions": len(sessions),
        "agent_counts": dict(Counter(s.agent for s in sessions)),
        "llm_counts": dict(Counter(s.llm for s in sessions)),
        "pair_counts": [
            {"agent": a, "llm": m, "count": count}
            for (a, m), count in sorted(
                Counter((s.agent, s.llm) for s in sessions).items()
            )
        ],
        "event_lengths": length_summary([len(s.events) for s in sessions]),
        "semantic_lengths": length_summary([len(s.semantic) for s in sessions]),
        "reliable_document_sessions": sum(
            bool(s.semantic) and all(s.semantic_documents) for s in sessions
        ),
    }
    try:
        split = make_split(sessions)
        report["random_split_feasibility"] = {
            "feasible": True,
            "counts": split["counts"],
        }
    except ValueError as exc:
        report["random_split_feasibility"] = {"feasible": False, "reason": str(exc)}
    report["notes"] = [
        "Each complete run is one sample in a random train/validation/test split.",
        "Task, prompt and browser session identities do not constrain the split.",
        "Default max lengths use training P95 only, rounded up, excluding CLS.",
        "PAGE_BOUNDARY defaults on only when all training action documents are reliable.",
    ]
    report["content_ambiguity"] = {
        target: content_ambiguity(sessions, target) for target in ("agent", "llm")
    }
    return report


def inspect_batch(sessions, preprocessor, target, classes):
    from .preprocessing import SequenceDataset, collate_sequences

    dataset = SequenceDataset(sessions, preprocessor, target, classes)
    batch = collate_sequences([dataset[i] for i in range(len(dataset))])
    return {
        key: value.tolist() if hasattr(value, "tolist") else value
        for key, value in batch.items()
    }


def inspect_sample(session, preprocessor, target, classes):
    from .data import sequence_view

    batch = inspect_batch([session], preprocessor, target, classes)
    return {
        "session_id": session.session_id,
        "representation": preprocessor.representation,
        "target": getattr(session, target),
        "cls": "[CLS]",
        "sequence": [
            {
                "type": t.kind,
                "role": t.role,
                # Content inspection also excludes timing.
                "timing_ms": list(t.timing) if preprocessor.normalizer.names else [],
            }
            for t in sequence_view(
                session, preprocessor.representation, preprocessor.use_page_boundary
            )[: preprocessor.max_seq_len]
        ],
        "encoded_batch": batch,
    }
