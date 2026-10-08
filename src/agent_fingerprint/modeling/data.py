"""Validated session records and strictly whitelisted representation views."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agent_fingerprint.analysis.representations import matrix
from agent_fingerprint.semantic_actions.normalize import EventNormalizer


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    ).hexdigest()


@dataclass(frozen=True)
class Token:
    kind: str
    role: str
    timing: tuple[float | None, ...] = ()


@dataclass
class Session:
    """A run/trial is the experimental unit, including all its browser documents."""

    session_id: str
    agent: str
    llm: str
    statistical: np.ndarray
    events: list[Token]
    semantic: list[Token]
    semantic_documents: list[str | None]


def event_tokens(raw):
    """Preserve capture order across clock domains; sort only comparable blocks.

    The existing normalizer identifies document/session changes and clock
    rollback. No pointer/mouse deduplication or semantic reduction is applied.
    A rollback starts a new block, so local clocks are never globally sorted.
    """
    normalized = EventNormalizer().normalize(raw)
    blocks = []
    for event in normalized:
        if not blocks or blocks[-1][0].block_id != event.block_id:
            blocks.append([])
        blocks[-1].append(event)
    ordered = []
    for block in blocks:
        clock = next(
            (
                key
                for key in ("epoch_ms", "monotonic_ms")
                if all(getattr(e, key) is not None for e in block)
            ),
            None,
        )
        ordered.extend(
            sorted(block, key=lambda e: getattr(e, clock)) if clock else block
        )
    tokens = []
    previous = None
    for event in ordered:
        dt = None
        # Never cross session IDs, even if epoch timestamps are available.
        if previous is not None and previous.session_id == event.session_id:
            if previous.epoch_ms is not None and event.epoch_ms is not None:
                dt = event.epoch_ms - previous.epoch_ms
            elif (
                (previous.document_id, previous.block_id)
                == (event.document_id, event.block_id)
                and previous.monotonic_ms is not None
                and event.monotonic_ms is not None
            ):
                dt = event.monotonic_ms - previous.monotonic_ms
        if dt is not None and dt < 0:
            dt = None
        # Use the raw low-level type, never semantic_event_type overrides.
        kind = str(
            event.raw_event.get("type", event.raw_event.get("event_type", ""))
        ).lower()
        tokens.append(Token(kind, event.target_role, (dt,)))
        previous = event
    return tokens


def semantic_tokens(row):
    content = row.get("action_sequence_content")
    temporal = row.get("action_sequence_temporal")
    if (
        not isinstance(content, list)
        or not isinstance(temporal, list)
        or len(content) != len(temporal)
    ):
        raise ValueError(
            "Missing/alignment error in semantic projections; rebuild dataset"
        )
    tokens = []
    for pair, timed in zip(content, temporal):
        if len(pair) != 2 or len(timed) != 4 or timed[:2] != pair:
            raise ValueError("Semantic content/temporal projection mismatch")
        if not all(isinstance(value, str) for value in pair):
            raise ValueError("Semantic categories must be strings")
        times = []
        for value in timed[2:]:
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not np.isfinite(value)
            ):
                raise ValueError("Timing must be finite numeric milliseconds or null")
            times.append(value)
        tokens.append(Token(*pair, tuple(times)))
    return tokens


def load_sessions(path):
    """Load all representations together so no pipeline silently drops samples.

    Original files are verified against dataset hashes and manifest labels.
    Task/prompt metadata is not required and never enters the tensors.
    """
    path = Path(path).resolve()
    data = json.loads(path.read_text())
    statistics = matrix(data, "statistics")
    sessions = []
    for row, statistical in zip(data["samples"], statistics):
        run_id = row.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("Every row needs an explicit run_id")
        source = Path(row["source_path"])
        if not source.is_absolute():
            source = path.parent / source
        raw_bytes = source.read_bytes()
        if (
            row.get("raw_sha256")
            and hashlib.sha256(raw_bytes).hexdigest() != row["raw_sha256"]
        ):
            raise ValueError(f"{run_id}: raw source hash mismatch")
        raw = json.loads(raw_bytes)
        if raw.get("run_id") != run_id:
            raise ValueError(f"{run_id}: raw/dataset run ID mismatch")
        manifest_path = source.parent.parent / "manifest.json"
        manifest = {}
        if manifest_path.exists():
            manifest_bytes = manifest_path.read_bytes()
            if (
                row.get("manifest_sha256")
                and hashlib.sha256(manifest_bytes).hexdigest() != row["manifest_sha256"]
            ):
                raise ValueError(f"{run_id}: manifest hash mismatch")
            manifest = json.loads(manifest_bytes)
            if manifest.get("run_id") != run_id:
                raise ValueError(f"{run_id}: manifest run ID mismatch")
        labels = [row.get("agent_label"), row.get("llm_label")]
        if not all(isinstance(label, str) and label.strip() for label in labels):
            raise ValueError(f"{run_id}: missing explicit Agent/LLM label")
        if manifest and labels != [
            manifest.get("agent", {}).get("name"),
            manifest.get("agent", {}).get("model"),
        ]:
            raise ValueError(f"{run_id}: manifest label mismatch")
        semantic = semantic_tokens(row)
        contexts = row.get("action_context", [])
        if contexts and len(contexts) != len(semantic):
            raise ValueError(f"{run_id}: action_context alignment mismatch")
        # Only explicitly recorded source document IDs qualify. Inferred IDs
        # produced by the semantic parser are deliberately not promoted here.
        documents = [
            c.get("document_id") if c.get("document_id_reliable") else None
            for c in contexts
        ]
        documents = documents or [None] * len(semantic)
        sessions.append(
            Session(
                run_id,
                *labels,
                statistical,
                event_tokens(raw),
                semantic,
                documents,
            )
        )
    if not sessions or len({s.session_id for s in sessions}) != len(sessions):
        raise ValueError("Expected nonempty data with one unique row per run/session")
    return sessions


def dataset_fingerprint(sessions):
    return digest(
        [
            {
                "id": s.session_id,
                "agent": s.agent,
                "llm": s.llm,
                "stat98": [None if np.isnan(v) else float(v) for v in s.statistical],
                "event": [(t.kind, t.role, t.timing) for t in s.events],
                "semantic": [(t.kind, t.role, t.timing) for t in s.semantic],
                "documents": s.semantic_documents,
            }
            for s in sorted(sessions, key=lambda s: s.session_id)
        ]
    )


def sequence_view(session, representation, use_page_boundary=False):
    if representation == "event":
        return session.events
    tokens = []
    previous_document = None
    for action, document in zip(session.semantic, session.semantic_documents):
        if (
            use_page_boundary
            and previous_document
            and document
            and previous_document != document
        ):
            tokens.append(Token("PAGE_BOUNDARY", "PAGE", (None, None)))
        tokens.append(action)
        previous_document = document
    return tokens


def length_summary(lengths):
    values = np.asarray(lengths)
    return {
        "count": len(lengths),
        "min": int(values.min()),
        "median": float(np.median(values)),
        "mean": float(values.mean()),
        "p90": float(np.percentile(values, 90)),
        "p95": float(np.percentile(values, 95)),
        "p99": float(np.percentile(values, 99)),
        "max": int(values.max()),
    }
