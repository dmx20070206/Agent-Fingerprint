"""Public E -> Z API and audit rendering. No model calls or third-party dependencies."""
from dataclasses import asdict, dataclass
import json

from .normalize import EventNormalizer
from .recognizers import RECOGNIZERS
from .reducer import SemanticReducer, TimingAnnotator
from .schema import SemanticActionConfig


@dataclass
class ParseResult:
    raw: dict
    normalized_events: list
    candidates: list
    actions: list
    reductions: list
    config: SemanticActionConfig

    def to_dict(self, existing_statistics=None):
        result = {
            "schema": "semantic-actions/v1",
            "run_id": self.raw.get("run_id"),
            "raw_trace": self.raw,
            "config": asdict(self.config),
            "actions": [asdict(a) for a in self.actions],
            # Whitelisting makes metadata leakage impossible even as the
            # internal debug schema evolves. Timing has a separate view.
            "action_sequence_content": [[a.action_type, a.target_role] for a in self.actions],
            "action_sequence_temporal": [[a.action_type, a.target_role, a.duration_ms,
                                          a.inter_action_latency_ms] for a in self.actions],
            "audit": {"reductions": self.reductions,
                      "candidate_count": len(self.candidates),
                      "source_indices": "zero-based inclusive; raw_trace.events[start:end + 1]"},
        }
        if existing_statistics is not None:
            result["existing_statistics"] = existing_statistics
        return result

    def to_json(self, existing_statistics=None):
        return json.dumps(self.to_dict(existing_statistics), ensure_ascii=False, indent=2, allow_nan=False)

    def debug_trace(self, session_id=None):
        selected = [e for e in self.normalized_events if session_id is None or e.session_id == session_id]
        indices = {e.index for e in selected}
        lines = ["Raw Event:"]
        for e in selected:
            duplicate = f" duplicate_of=E[{e.duplicate_of}]" if e.duplicate_of is not None else ""
            boundary = f" boundary={e.boundary_reason}" if e.boundary_reason else ""
            lines.append(f"E[{e.index}] {e.event_type} {e.target_role}{duplicate}{boundary}")
        lines.append("\n↓ Candidates:")
        for i, c in enumerate(self.candidates):
            if c.start.index in indices:
                lines.append(f"C[{i}] {c.action_type}({c.target_role}) E[{c.source_event_start_index}..{c.source_event_end_index}] {c.recognition_rule}")
        lines.append("\n↓ Reduction:")
        for r in self.reductions:
            if r["source_event_start_index"] in indices or r["retained_event_index"] in indices:
                lines.append(f"{r['absorbed']} absorbed by {r['into']}: {r['rule']} E[{r['source_event_start_index']}..{r['source_event_end_index']}]")
        lines.append("\n↓ Final:")
        for a in self.actions:
            if a.source_event_start_index in indices:
                lines.append(f"Z[{a.index}] {a.action_type}({a.target_role}) E[{a.source_event_start_index}..{a.source_event_end_index}] duration={a.duration_ms} gap={a.inter_action_latency_ms}")
        return "\n".join(lines) + "\n"


def parse_semantic_actions(raw, config=None):
    """Parse without sorting, removing or modifying any source event."""
    if isinstance(raw, list):
        raw = {"events": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("events", []), list):
        raise ValueError("Expected a raw trace object with an events array, or an event array")
    config = config or SemanticActionConfig()
    normalized = EventNormalizer().normalize(raw)
    candidates = [c for recognizer in RECOGNIZERS for c in recognizer().recognize(normalized, config)]
    candidates.sort(key=lambda c: (c.start.index, c.end.index, c.action_type))
    reduced, reductions = SemanticReducer().reduce(candidates, normalized, config)
    actions = TimingAnnotator().annotate(reduced)
    return ParseResult(raw, normalized, candidates, actions, reductions, config)


def abstract_events(raw, config=None, existing_statistics=None):
    return parse_semantic_actions(raw, config).to_dict(existing_statistics)


def debug_trace(raw, config=None, session_id=None):
    return parse_semantic_actions(raw, config).debug_trace(session_id)
