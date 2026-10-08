"""Internal interval schema. Only the explicit content/temporal projections are model inputs."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import math
from typing import Any

ACTION_TYPES = frozenset({"TEXT_ENTRY", "CLICK", "SCROLL", "SELECT", "TOGGLE",
                          "SUBMIT", "HOVER", "NAVIGATE", "HISTORY_NAV"})
TARGET_ROLES = frozenset({"LINK", "BUTTON", "TEXT_INPUT", "SELECT", "TOGGLE",
                         "CONTAINER", "PAGE", "OTHER"})


@dataclass(frozen=True)
class SemanticActionConfig:
    T_SCROLL_GAP_MS: float = 350
    T_HOVER_MIN_MS: float = 800
    T_HOVER_CLICK_MS: float = 400
    T_ACTIVATION_MAX_GAP_MS: float = 1500
    T_TEXT_ENTRY_IDLE_MS: float = 30000
    T_NAV_CAUSAL_WINDOW_MS: float = 5000
    T_CONTROL_CHANGE_MS: float = 1500

    def __post_init__(self):
        for name, value in asdict(self).items():
            if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be a finite nonnegative number")


@dataclass
class NormalizedEvent:
    index: int
    event_type: str
    epoch_ms: float | None
    monotonic_ms: float | None
    session_id: str | None
    document_id: str
    block_id: int
    canonical_target_id: str
    target_role: str
    raw_event: dict[str, Any]
    target: dict[str, Any] = field(default_factory=dict)
    x: float | None = None
    y: float | None = None
    button: int | None = None
    key: str | None = None
    key_category: str | None = None
    value_changed: bool | None = None
    checked_changed: bool | None = None
    scroll_x: float | None = None
    scroll_y: float | None = None
    scroll_changed: bool | None = None
    scroll_dx: float | None = None
    scroll_dy: float | None = None
    related_target_id: str | None = None
    url: str | None = None
    duplicate_of: int | None = None
    boundary_reason: str | None = None
    document_inferred: bool = False


def elapsed(a: NormalizedEvent, b: NormalizedEvent, *, cross_document=False) -> float | None:
    """Reuse the episode view's epoch-first, pairwise local-clock fallback policy."""
    same_block = (a.session_id, a.document_id, a.block_id) == (b.session_id, b.document_id, b.block_id)
    if not same_block and not cross_document:
        return None
    if a.epoch_ms is not None and b.epoch_ms is not None:
        dt = b.epoch_ms - a.epoch_ms
    elif same_block and a.monotonic_ms is not None and b.monotonic_ms is not None:
        dt = b.monotonic_ms - a.monotonic_ms
    else:
        return None
    return dt if dt >= 0 else None


@dataclass
class CandidateAction:
    action_type: str
    target_role: str
    start: NormalizedEvent
    end: NormalizedEvent
    recognition_rule: str
    confidence: str = "HIGH"
    source_event_start_index: int | None = None
    source_event_end_index: int | None = None
    subtype: str | None = None
    outcome: str | None = None
    evidence: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.action_type not in ACTION_TYPES or self.target_role not in TARGET_ROLES:
            raise ValueError("Unknown semantic vocabulary")
        if self.source_event_start_index is None:
            self.source_event_start_index = self.start.index
        if self.source_event_end_index is None:
            self.source_event_end_index = self.end.index

    def extend(self, event):
        self.end = event
        self.source_event_end_index = event.index

    @property
    def target_id(self):
        return self.start.canonical_target_id


@dataclass
class SemanticAction:
    index: int
    action_type: str
    target_role: str
    start_time_ms: float | None
    end_time_ms: float | None
    duration_ms: float | None
    inter_action_latency_ms: float | None
    source_event_start_index: int
    source_event_end_index: int
    canonical_target_id: str
    document_id: str
    session_id: str | None
    recognition_rule: str
    confidence: str
    block_id: int
    time_basis: str | None
    subtype: str | None = None
    outcome: str | None = None
