"""Frozen vocabularies, train-only timing statistics and sequence batching."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import torch
from torch.utils.data import Dataset

from agent_fingerprint.semantic_actions.schema import ACTION_TYPES, TARGET_ROLES

from .data import length_summary, sequence_view

SPECIAL_TOKENS = ("[PAD]", "[CLS]", "[UNK]")
EVENT_TYPES = tuple(
    sorted(
        {
            "beforeinput",
            "beforeunload",
            "blur",
            "change",
            "click",
            "compositionend",
            "compositionstart",
            "compositionupdate",
            "contextmenu",
            "dblclick",
            "focus",
            "focusin",
            "focusout",
            "history_nav",
            "input",
            "keydown",
            "keypress",
            "keyup",
            "monitor_start",
            "monitor_stop",
            "mousedown",
            "mouseenter",
            "mouseleave",
            "mousemove",
            "mouseout",
            "mouseover",
            "mouseup",
            "navigate",
            "navigation",
            "pagehide",
            "pageshow",
            "paste",
            "pointercancel",
            "pointerdown",
            "pointerenter",
            "pointerleave",
            "pointermove",
            "pointerout",
            "pointerover",
            "pointerup",
            "popstate",
            "scroll",
            "scrollend",
            "selectionchange",
            "submit",
            "touchend",
            "touchmove",
            "touchstart",
            "visibilitychange",
            "wheel",
        }
    )
)


@dataclass(frozen=True)
class Vocabulary:
    tokens: tuple[str, ...]

    @classmethod
    def build(cls, values):
        return cls(SPECIAL_TOKENS + tuple(sorted(set(values) - set(SPECIAL_TOKENS))))

    def encode(self, token):
        try:
            return self.tokens.index(token)
        except ValueError:
            return 2

    def to_dict(self):
        return {"tokens": list(self.tokens)}


@dataclass(frozen=True)
class TimingNormalizer:
    names: tuple[str, ...]
    means: tuple[float, ...]
    stds: tuple[float, ...]
    counts: tuple[int, ...]

    @classmethod
    def fit(cls, sequences, names, *, partition):
        if partition != "train":
            raise ValueError("TimingNormalizer can only fit the train partition")
        columns = [[] for _ in names]
        for sequence in sequences:
            for token in sequence:
                for i in range(len(names)):
                    value = token.timing[i]
                    if value is not None:
                        if not math.isfinite(value):
                            raise ValueError("Nonfinite timing value")
                        columns[i].append(math.log1p(max(value, 0)))
        means = tuple(float(np.mean(c)) if c else 0.0 for c in columns)
        stds = tuple(
            float(np.std(c)) if c and np.std(c) > 1e-8 else 1.0 for c in columns
        )
        return cls(tuple(names), means, stds, tuple(map(len, columns)))

    def transform(self, timing):
        values, masks = [], []
        for i in range(len(self.names)):
            value = timing[i]
            masks.append(float(value is not None))
            values.append(
                0.0
                if value is None
                else (math.log1p(max(value, 0)) - self.means[i]) / self.stds[i]
            )
        return values + masks

    def to_dict(self):
        result = {"names": list(self.names), "fitted_on": "train"}
        for name, mean, std, count in zip(
            self.names, self.means, self.stds, self.counts
        ):
            result.update(
                {name + "_mean": mean, name + "_std": std, name + "_observed": count}
            )
        return result

    @classmethod
    def from_dict(cls, data):
        names = tuple(data["names"])
        return cls(
            names,
            tuple(data[n + "_mean"] for n in names),
            tuple(data[n + "_std"] for n in names),
            tuple(data[n + "_observed"] for n in names),
        )


@dataclass(frozen=True)
class SequencePreprocessor:
    representation: str
    semantic_mode: str
    use_page_boundary: bool
    max_seq_len: int
    vocabulary: Vocabulary
    roles: Vocabulary
    normalizer: TimingNormalizer

    @classmethod
    def fit(
        cls,
        train_sessions,
        *,
        representation,
        semantic_mode="content",
        use_page_boundary=None,
        max_seq_len=None,
    ):
        if representation not in {"event", "semantic"} or semantic_mode not in {
            "content",
            "timing",
        }:
            raise ValueError("Invalid sequence representation/mode")
        reliable = any(s.semantic for s in train_sessions) and all(
            all(s.semantic_documents) for s in train_sessions
        )
        if representation == "event":
            use_page_boundary = False
        elif use_page_boundary is True and not reliable:
            raise ValueError(
                "PAGE_BOUNDARY requested but training document IDs are not reliable; rebuild dataset or disable it"
            )
        elif use_page_boundary is None:
            use_page_boundary = reliable
        sequences = [
            sequence_view(s, representation, use_page_boundary) for s in train_sessions
        ]
        if not sequences:
            raise ValueError("Cannot fit preprocessing without training sessions")
        if max_seq_len is None:
            max_seq_len = max(
                1, math.ceil(np.percentile([len(s) for s in sequences], 95))
            )
        if max_seq_len < 1:
            raise ValueError("max_seq_len must be >= 1 (excludes CLS)")
        names = (
            ("delta_t",)
            if representation == "event"
            else ("duration", "gap")
            if semantic_mode == "timing"
            else ()
        )
        vocabulary = Vocabulary.build(
            EVENT_TYPES
            if representation == "event"
            else (*ACTION_TYPES, "PAGE_BOUNDARY")
        )
        return cls(
            representation,
            semantic_mode,
            bool(use_page_boundary),
            max_seq_len,
            vocabulary,
            Vocabulary.build(TARGET_ROLES),
            TimingNormalizer.fit(
                [s[:max_seq_len] for s in sequences], names, partition="train"
            ),
        )

    def encode(self, session):
        tokens = sequence_view(session, self.representation, self.use_page_boundary)[
            : self.max_seq_len
        ]
        return {
            "token_ids": torch.tensor(
                [1] + [self.vocabulary.encode(t.kind) for t in tokens], dtype=torch.long
            ),
            "role_ids": torch.tensor(
                [1] + [self.roles.encode(t.role) for t in tokens], dtype=torch.long
            ),
            "timing": torch.tensor(
                [[0.0] * (2 * len(self.normalizer.names))]
                + [self.normalizer.transform(t.timing) for t in tokens],
                dtype=torch.float32,
            ),
            "session_id": session.session_id,
        }

    def report(self, sessions):
        lengths = [
            len(sequence_view(s, self.representation, self.use_page_boundary))
            for s in sessions
        ]
        lost = [max(0, length - self.max_seq_len) for length in lengths]
        truncated = sum(n > 0 for n in lost)
        return {
            "lengths": length_summary(lengths),
            "max_seq_len_excluding_cls": self.max_seq_len,
            "truncated_sessions": truncated,
            "truncated_fraction": truncated / len(lengths),
            "mean_lost_tokens": float(np.mean(lost)),
            "mean_lost_tokens_among_truncated": sum(lost) / truncated
            if truncated
            else 0.0,
            "page_boundary_enabled": self.use_page_boundary,
            "sessions_without_reliable_document_ids": sum(
                not all(s.semantic_documents) for s in sessions
            ),
        }

    def to_dict(self):
        return {
            "representation": self.representation,
            "semantic_mode": self.semantic_mode,
            "use_page_boundary": self.use_page_boundary,
            "max_seq_len": self.max_seq_len,
            "vocabulary": self.vocabulary.to_dict(),
            "roles": self.roles.to_dict(),
            "normalizer": self.normalizer.to_dict(),
        }

    @classmethod
    def from_dict(cls, data):
        return cls(
            data["representation"],
            data["semantic_mode"],
            data["use_page_boundary"],
            data["max_seq_len"],
            Vocabulary(tuple(data["vocabulary"]["tokens"])),
            Vocabulary(tuple(data["roles"]["tokens"])),
            TimingNormalizer.from_dict(data["normalizer"]),
        )


class SequenceDataset(Dataset):
    def __init__(self, sessions, preprocessor, target, classes):
        self.samples = []
        for session in sessions:
            sample = preprocessor.encode(session)
            sample["label"] = classes.index(getattr(session, target))
            self.samples.append(sample)

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, index):
        return self.samples[index]


class EventSequenceDataset(SequenceDataset):
    def __init__(self, sessions, preprocessor, target, classes):
        if preprocessor.representation != "event":
            raise ValueError("EventSequenceDataset requires event preprocessing")
        super().__init__(sessions, preprocessor, target, classes)


class SemanticActionDataset(SequenceDataset):
    def __init__(self, sessions, preprocessor, target, classes):
        if preprocessor.representation != "semantic":
            raise ValueError("SemanticActionDataset requires semantic preprocessing")
        super().__init__(sessions, preprocessor, target, classes)


class StatisticalDataset(Dataset):
    def __init__(self, sessions):
        self.values = np.stack([s.statistical for s in sessions])
        if self.values.shape[1] != 98:
            raise ValueError("Expected the fixed v5 98-dimensional schema")

    def __len__(self):
        return len(self.values)

    def __getitem__(self, index):
        return self.values[index]


def collate_sequences(samples):
    """True means ignored padding. CLS always remains visible, even if empty."""
    size = max(len(s["token_ids"]) for s in samples)
    batch = {
        "token_ids": torch.zeros(len(samples), size, dtype=torch.long),
        "role_ids": torch.zeros(len(samples), size, dtype=torch.long),
        "timing": torch.zeros(len(samples), size, samples[0]["timing"].shape[-1]),
        "padding_mask": torch.ones(len(samples), size, dtype=torch.bool),
        "labels": torch.tensor([s["label"] for s in samples], dtype=torch.long),
        "session_ids": [s["session_id"] for s in samples],
    }
    for i, sample in enumerate(samples):
        length = len(sample["token_ids"])
        for key in ("token_ids", "role_ids", "timing"):
            batch[key][i, :length] = sample[key]
        batch["padding_mask"][i, :length] = False
    return batch
