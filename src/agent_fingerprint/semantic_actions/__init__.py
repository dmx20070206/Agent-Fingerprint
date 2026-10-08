"""Deterministic, auditable browser interaction abstraction."""
from .pipeline import abstract_events, debug_trace, parse_semantic_actions
from .schema import SemanticActionConfig, SemanticAction, CandidateAction, NormalizedEvent

__all__ = ["abstract_events", "debug_trace", "parse_semantic_actions", "SemanticActionConfig",
           "SemanticAction", "CandidateAction", "NormalizedEvent"]
