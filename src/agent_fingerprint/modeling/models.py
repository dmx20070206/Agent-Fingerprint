"""One lightweight backbone; each experiment instantiates independent weights."""

from dataclasses import asdict, dataclass

import torch
from torch import nn


@dataclass(frozen=True)
class TransformerConfig:
    d_model: int = 128
    n_heads: int = 4
    num_layers: int = 2
    ffn_dim: int = 256
    dropout: float = 0.1
    category_dim: int = 32
    timing_dim: int = 16

    def to_dict(self):
        return asdict(self)


class SequenceTransformer(nn.Module):
    def __init__(
        self,
        vocabulary_size,
        role_size,
        timing_features,
        max_seq_len,
        num_classes,
        config=None,
    ):
        super().__init__()
        config = config or TransformerConfig()
        if config.d_model % config.n_heads:
            raise ValueError("d_model must be divisible by n_heads")
        self.event_embedding = nn.Embedding(
            vocabulary_size, config.category_dim, padding_idx=0
        )
        self.role_embedding = nn.Embedding(
            role_size, config.category_dim, padding_idx=0
        )
        self.timing_mlp = (
            nn.Sequential(
                nn.Linear(timing_features, config.timing_dim),
                nn.GELU(),
                nn.Linear(config.timing_dim, config.timing_dim),
            )
            if timing_features
            else None
        )
        width = 2 * config.category_dim + (config.timing_dim if timing_features else 0)
        self.projection = nn.Linear(width, config.d_model)
        self.positions = nn.Embedding(max_seq_len + 1, config.d_model)
        self.encoder = nn.TransformerEncoder(
            nn.TransformerEncoderLayer(
                d_model=config.d_model,
                nhead=config.n_heads,
                dim_feedforward=config.ffn_dim,
                dropout=config.dropout,
                batch_first=True,
                activation="gelu",
            ),
            num_layers=config.num_layers,
            enable_nested_tensor=False,
        )
        self.classifier = nn.Sequential(
            nn.LayerNorm(config.d_model), nn.Linear(config.d_model, num_classes)
        )

    def forward(self, token_ids, role_ids, timing, padding_mask):
        pieces = [self.event_embedding(token_ids), self.role_embedding(role_ids)]
        if self.timing_mlp is not None:
            pieces.append(self.timing_mlp(timing))
        hidden = self.projection(torch.cat(pieces, dim=-1))
        hidden = hidden + self.positions(
            torch.arange(hidden.shape[1], device=hidden.device)
        )
        hidden = self.encoder(hidden, src_key_padding_mask=padding_mask)
        return self.classifier(hidden[:, 0])


class EventTransformer(SequenceTransformer):
    """Execution baseline: event category, target role, delta_t and its mask."""


class SemanticTransformer(SequenceTransformer):
    """Content/timing variants use the same implementation, with separate weights."""


def build_transformer(preprocessor, num_classes, config=None):
    config = config or TransformerConfig()
    model_class = (
        EventTransformer
        if preprocessor.representation == "event"
        else SemanticTransformer
    )
    return model_class(
        len(preprocessor.vocabulary.tokens),
        len(preprocessor.roles.tokens),
        2 * len(preprocessor.normalizer.names),
        preprocessor.max_seq_len,
        num_classes,
        config,
    )
