"""Deliberate training-set memorization diagnostic; never a held-out result."""

from dataclasses import replace
from pathlib import Path

import torch
from torch import nn

from agent_fingerprint.storage.io import write_json

from .models import TransformerConfig, build_transformer
from .preprocessing import SequencePreprocessor
from .training import (
    TrainingConfig,
    make_loaders,
    model_inputs,
    predict_loader,
    save_checkpoint,
    seed_everything,
)


def select_tiny_sessions(sessions, target, size, *, unique_content=False):
    """Round-robin classes, choosing short traces to keep the diagnostic cheap."""
    groups = {}
    for session in sorted(sessions, key=lambda s: (len(s.events), s.session_id)):
        groups.setdefault(getattr(session, target), []).append(session)
    selected = []
    seen = set()
    while len(selected) < min(size, len(sessions)) and any(groups.values()):
        for name in sorted(groups):
            while groups[name] and len(selected) < size:
                session = groups[name].pop(0)
                content = tuple((t.kind, t.role) for t in session.semantic)
                if unique_content and content in seen:
                    continue
                selected.append(session)
                seen.add(content)
                break
    if len(selected) < size:
        raise ValueError(
            f"Only {len(selected)} eligible tiny sessions; requested {size}"
        )
    return selected


def run_overfit(
    sessions,
    output_dir,
    *,
    target="agent",
    size=24,
    epochs=300,
    seed=42,
    device="cpu",
    unique_content=False,
):
    if not 16 <= size <= 32:
        raise ValueError("Tiny overfit size must be between 16 and 32")
    if len(sessions) < size:
        raise ValueError(
            f"Overfit requested {size} sessions but only {len(sessions)} are available"
        )
    destination = Path(output_dir)
    if destination.exists() and any(destination.iterdir()):
        raise ValueError("Overfit output directory must be empty")
    destination.mkdir(parents=True, exist_ok=True)
    selected = select_tiny_sessions(
        sessions, target, size, unique_content=unique_content
    )
    classes = sorted({getattr(s, target) for s in selected})
    if len(classes) < 2:
        raise ValueError("Overfit diagnostic needs at least two classes")
    reports = {
        "diagnostic_only": True,
        "target": target,
        "seed": seed,
        "session_ids": [s.session_id for s in selected],
        "selection": "short, class-round-robin; unique content"
        if unique_content
        else "short, class-round-robin",
        "results": {},
    }
    from .audit import content_ambiguity

    reports["content_ambiguity"] = content_ambiguity(selected, target)
    model_config = TransformerConfig(
        d_model=64, n_heads=4, num_layers=2, ffn_dim=128, dropout=0
    )
    base = TrainingConfig(
        target=target,
        seed=seed,
        batch_size=size,
        epochs=epochs,
        lr=0.003,
        weight_decay=0,
        device=device,
    )
    for name, representation, mode in [
        ("E", "event", "content"),
        ("Z_content", "semantic", "content"),
        ("Z_timing", "semantic", "timing"),
    ]:
        seed_everything(seed)
        config = replace(base, representation=representation, semantic_mode=mode)
        preprocessor = SequencePreprocessor.fit(
            selected,
            representation=representation,
            semantic_mode=mode,
            max_seq_len=max(
                1,
                max(
                    len(s.events if representation == "event" else s.semantic)
                    for s in selected
                ),
            ),
            use_page_boundary=False,
        )
        loader, evaluation = make_loaders(
            selected,
            {"train": list(range(len(selected)))},
            preprocessor,
            config,
            classes,
        )
        model = build_transformer(preprocessor, len(classes), model_config).to(device)
        optimizer = torch.optim.AdamW(model.parameters(), lr=config.lr, weight_decay=0)
        loss_function = nn.CrossEntropyLoss()
        history = []
        for epoch in range(1, epochs + 1):
            model.train()
            for batch in loader:
                optimizer.zero_grad(set_to_none=True)
                loss = loss_function(
                    model(**model_inputs(batch, device)), batch["labels"].to(device)
                )
                loss.backward()
                optimizer.step()
            model.eval()
            with torch.no_grad():
                losses = [
                    loss_function(
                        model(**model_inputs(batch, device)), batch["labels"].to(device)
                    ).item()
                    for batch in evaluation["train"]
                ]
            predicted, truth = predict_loader(model, evaluation["train"], device)
            accuracy = sum(a == b for a, b in zip(predicted, truth)) / len(truth)
            entry = {
                "epoch": epoch,
                "loss": sum(losses) / len(losses),
                "accuracy": accuracy,
            }
            history.append(entry)
            if entry["loss"] < 0.05 and accuracy == 1:
                break
        save_checkpoint(
            destination / f"{name}.pt",
            model,
            preprocessor,
            model_config,
            classes,
            target,
        )
        reports["results"][name] = {
            "passed": history[-1]["loss"] < 0.05 and history[-1]["accuracy"] == 1,
            "initial_loss": history[0]["loss"],
            "final": history[-1],
            "history": history,
            "model_config": model_config.to_dict(),
            "training_config": config.__dict__,
            "preprocessor": preprocessor.to_dict(),
        }
        write_json(destination / "overfit_report.json", reports)
        print(
            f"Overfit {name}: loss={history[-1]['loss']:.5f}, accuracy={history[-1]['accuracy']:.3f}",
            flush=True,
        )
    reports["passed"] = all(r["passed"] for r in reports["results"].values())
    write_json(destination / "overfit_report.json", reports)
    return reports
