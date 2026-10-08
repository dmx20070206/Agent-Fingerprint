from dataclasses import replace

import pytest
import torch

from agent_fingerprint.modeling.data import Token, sequence_view
from agent_fingerprint.modeling.models import TransformerConfig, build_transformer
from agent_fingerprint.modeling.preprocessing import (
    EVENT_TYPES,
    EventSequenceDataset,
    SemanticActionDataset,
    SequencePreprocessor,
    TimingNormalizer,
    Vocabulary,
    collate_sequences,
)
from agent_fingerprint.modeling.training import (
    load_checkpoint,
    model_inputs,
    save_checkpoint,
    seed_everything,
)
from agent_fingerprint.semantic_actions.schema import ACTION_TYPES, TARGET_ROLES


@pytest.mark.parametrize("values", [EVENT_TYPES, ACTION_TYPES, TARGET_ROLES])
def test_stable_vocabulary_and_unknown(values):
    a, b = Vocabulary.build(values), Vocabulary.build(sorted(values, reverse=True))
    assert a == b
    assert a.tokens[:3] == ("[PAD]", "[CLS]", "[UNK]")
    assert a.encode("unobserved-token") == 2
    assert a.encode(a.tokens[3]) == 3


def test_train_only_timing_missing_zero_and_roundtrip():
    train = [[Token("CLICK", "BUTTON", (None, 0)), Token("CLICK", "BUTTON", (0, 10))]]
    norm = TimingNormalizer.fit(train, ("duration", "gap"), partition="train")
    assert norm.transform((None, 0))[2:] == [0, 1]
    assert norm.transform((0, 0))[2:] == [1, 1]
    before = norm.to_dict()
    norm.transform((100000, 900000))
    assert norm.to_dict() == before
    assert TimingNormalizer.from_dict(before) == norm
    with pytest.raises(ValueError, match="train partition"):
        TimingNormalizer.fit(train, ("duration", "gap"), partition="test")
    assert norm.transform((-5, 0)) == norm.transform((0, 0))


def test_page_boundaries_are_structural_with_no_timing(sessions):
    session = sessions[2]
    session.semantic_documents = ["d1", "d2", "d2"]
    sequence = sequence_view(session, "semantic", True)
    assert [t.kind for t in sequence].count("PAGE_BOUNDARY") == 1
    assert sequence[1].timing == (None, None)
    pp = SequencePreprocessor.fit(
        [session], representation="semantic", semantic_mode="timing"
    )
    assert pp.use_page_boundary
    encoded = pp.encode(session)
    assert encoded["timing"][2].tolist() == [0, 0, 0, 0]
    session.semantic_documents = [None] * 3
    assert not SequencePreprocessor.fit(
        [session], representation="semantic"
    ).use_page_boundary
    with pytest.raises(ValueError, match="not reliable"):
        SequencePreprocessor.fit(
            [session], representation="semantic", use_page_boundary=True
        )


def test_truncation_statistics_and_train_only_length(sessions):
    pp = SequencePreprocessor.fit(sessions[:1], representation="event", max_seq_len=1)
    report = pp.report(sessions[:3])
    assert report["truncated_sessions"] == 3
    assert report["truncated_fraction"] == 1
    assert report["mean_lost_tokens"] == 1
    auto = SequencePreprocessor.fit(sessions[:1], representation="event")
    assert auto.max_seq_len == 2
    auto.encode(sessions[-1])
    assert auto.max_seq_len == 2


@pytest.mark.parametrize(
    ("representation", "mode"),
    [("event", "content"), ("semantic", "content"), ("semantic", "timing")],
)
def test_forward_empty_single_and_variable_batches_padding_and_reload(
    sessions, tmp_path, representation, mode
):
    seed_everything(3)
    selected = [
        replace(sessions[0], events=[], semantic=[], semantic_documents=[]),
        replace(
            sessions[1],
            events=[sessions[1].events[0]],
            semantic=[sessions[1].semantic[0]],
            semantic_documents=["d1"],
        ),
        sessions[2],
    ]
    pp = SequencePreprocessor.fit(
        selected,
        representation=representation,
        semantic_mode=mode,
        use_page_boundary=False,
    )
    classes = ["a0", "a1", "a2"]
    dataset_class = (
        EventSequenceDataset if representation == "event" else SemanticActionDataset
    )
    dataset = dataset_class(selected, pp, "agent", classes)
    batch = collate_sequences([dataset[i] for i in range(3)])
    assert batch["token_ids"][:, 0].tolist() == [1, 1, 1]
    assert not batch["padding_mask"][:, 0].any()
    assert batch["padding_mask"][0, 1:].all()
    assert batch["token_ids"][0, 1:].eq(0).all()
    assert batch["timing"].shape[-1] == (
        2 if representation == "event" else 4 if mode == "timing" else 0
    )
    config = TransformerConfig(
        d_model=16, n_heads=2, num_layers=1, ffn_dim=32, dropout=0
    )
    model = build_transformer(pp, 3, config).eval()
    with torch.no_grad():
        output = model(**model_inputs(batch, "cpu"))
    assert output.shape == (3, 3) and torch.isfinite(output).all()
    # Padded content must not affect the visible CLS representation.
    changed = {
        k: v.clone() if isinstance(v, torch.Tensor) else v for k, v in batch.items()
    }
    changed["token_ids"][changed["padding_mask"]] = 3
    changed["role_ids"][changed["padding_mask"]] = 3
    changed["timing"][changed["padding_mask"]] = 100
    with torch.no_grad():
        assert torch.allclose(output, model(**model_inputs(changed, "cpu")), atol=1e-6)
        single = collate_sequences([dataset[0]])
        assert torch.allclose(
            output[:1], model(**model_inputs(single, "cpu")), atol=1e-6
        )
    path = tmp_path / "checkpoint.pt"
    save_checkpoint(path, model, pp, config, classes, "agent")
    reloaded, saved_pp, saved_classes, target = load_checkpoint(path)
    with torch.no_grad():
        assert torch.equal(output, reloaded(**model_inputs(batch, "cpu")))
    assert saved_pp == pp and saved_classes == classes and target == "agent"


def test_content_model_cannot_see_timing(sessions):
    pp = SequencePreprocessor.fit(
        sessions[:2], representation="semantic", semantic_mode="content"
    )
    original = pp.encode(sessions[0])
    sessions[0].semantic = [
        replace(t, timing=(9999, 1000000)) for t in sessions[0].semantic
    ]
    changed = pp.encode(sessions[0])
    assert all(
        torch.equal(original[k], changed[k])
        for k in ("token_ids", "role_ids", "timing")
    )


def test_validation_transform_does_not_fit_or_change_training_statistics(sessions):
    pp = SequencePreprocessor.fit(
        sessions[:3], representation="semantic", semantic_mode="timing"
    )
    saved = pp.to_dict()
    validation = replace(
        sessions[-1],
        semantic=[Token("UNSEEN_ACTION", "UNSEEN_ROLE", (1e12, None))],
        semantic_documents=[None],
    )
    encoded = pp.encode(validation)
    assert encoded["token_ids"][1].item() == 2
    assert encoded["role_ids"][1].item() == 2
    assert pp.to_dict() == saved
    assert encoded["timing"][1, -2:].tolist() == [1, 0]


def test_tiny_diagnostic_reports_contradictory_inputs(sessions):
    from agent_fingerprint.modeling.audit import content_ambiguity
    from agent_fingerprint.modeling.overfit import select_tiny_sessions

    a, b = sessions[0], replace(sessions[0], session_id="copy", agent="a1")
    report = content_ambiguity([a, b], "agent")
    assert report["accuracy_ceiling"] == 0.5
    assert len(report["conflicting_groups"]) == 1
    with pytest.raises(ValueError, match="eligible tiny sessions"):
        select_tiny_sessions([a, b], "agent", 2, unique_content=True)
