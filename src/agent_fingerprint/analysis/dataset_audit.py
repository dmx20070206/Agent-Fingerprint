"""Print collection counts and L3 lengths; optionally quarantine low-quality runs."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tempfile
import unicodedata
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

L3_FILE = "fingerprints/l3_browser_dynamic.json"
FAILED = {"failed", "error", "aborted", "timeout", "cancelled", "canceled"}
ACTIVE = {"running", "pending", "queued", "started", "initializing"}
QUARANTINE_MARKER = ".dataset-audit-quarantine"


def obj(value):
    return value if isinstance(value, dict) else {}


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError("JSON 顶层必须为对象")
    return value


def discover(root):
    """Stop at run boundaries; never descend into artifacts or quarantine batches."""
    for directory, children, files in os.walk(root):
        children.sort()
        path = Path(directory)
        if QUARANTINE_MARKER in files:
            children.clear()
            continue
        if "manifest.json" in files or (path / L3_FILE).is_file():
            children.clear()
            yield path


def task_result(manifest):
    status = str(manifest.get("status", "unknown"))
    outcome = obj(manifest.get("outcome"))
    verification = obj(outcome.get("verification")).get("status")
    if status in ACTIVE:
        return "进行中"
    if status in FAILED or outcome.get("success") is False or verification == "failed":
        return "失败"
    if (
        status == "success"
        or outcome.get("success") is True
        or verification == "passed"
    ):
        return "成功"
    return "未知"


def audit_run(path, root):
    relative = path.relative_to(root)
    parts = relative.parts
    row = {
        "sample_id": str(relative),
        "agent": parts[-3] if len(parts) >= 4 else "unknown",
        "llm": parts[-2] if len(parts) >= 4 else "unknown",
        "status": "unknown",
        "task_result": "未知",
        "event_count": None,
        "notes": [],
    }
    try:
        manifest = read_object(path / "manifest.json")
    except (OSError, ValueError, TypeError) as exc:
        manifest = {}
        row["notes"].append(f"manifest 无法读取: {exc}")
    agent = obj(manifest.get("agent"))
    row["agent"] = str(agent.get("name") or row["agent"])
    row["llm"] = str(agent.get("model") or row["llm"])
    row["status"] = str(manifest.get("status", "unknown"))
    row["task_result"] = task_result(manifest)
    ref = obj(manifest.get("fingerprints")).get("l3", L3_FILE)
    source = path / (ref if isinstance(ref, str) else L3_FILE)
    row["l3_file"] = os.path.relpath(source, root)
    try:
        if not source.resolve().is_relative_to(path.resolve()):
            raise ValueError("L3 路径超出运行目录")
        raw = read_object(source)
        events = raw.get("events")
        if not isinstance(events, list):
            raise TypeError("events 必须为数组")
        row["event_count"] = len(events)
        if raw.get("event_count", len(events)) != len(events):
            row["notes"].append("event_count 与实际长度不符，采用 len(events)")
    except (OSError, ValueError, TypeError) as exc:
        row["notes"].append(f"L3 无法计数: {exc}")
    return row


def filter_reasons(row, min_events=1000, *, l3_only=False):
    # A collector may still be writing these files. Do not move active runs.
    if row["task_result"] == "进行中":
        return []
    reasons = []
    if not l3_only and row["task_result"] == "失败":
        reasons.append("任务失败")
    if row["event_count"] is None:
        reasons.append("L3 缺失或损坏")
    elif row["event_count"] < min_events:
        reasons.append(f"L3 < {min_events}")
    return reasons


def print_table(headers, rows):
    cells = [
        [" ".join(str(value).split()) for value in row] for row in [headers, *rows]
    ]

    def width(value):
        return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in value)

    widths = [max(width(row[i]) for row in cells) for i in range(len(headers))]
    for i, row in enumerate(cells):
        print(
            "  ".join(
                value + " " * (size - width(value)) for value, size in zip(row, widths)
            ).rstrip()
        )
        if i == 0:
            print("  ".join("-" * size for size in widths))


def print_summary(rows, title):
    print(f"\n{title}：{len(rows)} 条运行")
    counts = Counter((row["agent"], row["llm"]) for row in rows)
    print_table(
        ["Agent", "LLM", "收集数"],
        [[*key, count] for key, count in sorted(counts.items())],
    )


def atomic_write(path, content):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as stream:
        temp = Path(stream.name)
        try:
            stream.write(content)
            stream.close()
            temp.replace(path)
        finally:
            temp.unlink(missing_ok=True)


def quarantine_runs(root, destination, selected):
    """Move complete runs, preserving relative paths and index entries for recovery."""
    root, destination = root.resolve(), destination.resolve()
    if destination.is_relative_to(root) or root.is_relative_to(destination):
        raise ValueError("隔离目录必须在输入目录之外，且不能是其上级目录")
    if destination.exists():
        raise ValueError(f"隔离目录已存在，拒绝覆盖: {destination}")
    if not selected:
        return
    for row in selected:
        source = root / row["sample_id"]
        if source == root or not source.resolve().is_relative_to(root):
            raise ValueError("过滤时输入必须是包含运行目录的数据集文件夹")
        # Re-read before moving in case a run was restarted during inspection.
        current = audit_run(source, root)
        if any(
            current[key] != row[key] for key in ("event_count", "status", "task_result")
        ):
            raise ValueError(f"运行在检查期间发生变化，请重新扫描: {source}")
    index = root / "index.jsonl"
    entries = []
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines(keepends=True):
            if not line.strip():
                continue
            entry = json.loads(line)
            if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
                raise TypeError("index.jsonl 存在无效条目，未移动数据")
            entries.append((entry, line))
    destination.mkdir(parents=True)
    (destination / QUARANTINE_MARKER).touch()
    moved = set()
    try:
        for row in selected:
            relative = row["sample_id"]
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(root / relative), str(target))
            moved.add(relative)
    finally:
        # Even a partial move must leave the location index consistent.
        if entries:
            atomic_write(
                destination / "index.jsonl",
                "".join(line for entry, line in entries if entry["path"] in moved),
            )
            atomic_write(
                index,
                "".join(line for entry, line in entries if entry["path"] not in moved),
            )
        atomic_write(
            destination / "moves.json",
            json.dumps(
                {
                    "input_dir": str(root),
                    "runs": [row for row in selected if row["sample_id"] in moved],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
        )


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="统计 agent + LLM 数量、逐文件 L3 条数和任务状态；可选转移不合格数据。"
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=Path("data/runs/final"),
        help="递归扫描的数据目录",
    )
    parser.add_argument(
        "--filter", action="store_true", help="转移任务失败或 L3 不足阈值的运行"
    )
    parser.add_argument(
        "--min-events",
        type=int,
        default=1000,
        help="L3 最低条数（默认 1000，严格小于才过滤）",
    )
    parser.add_argument(
        "--l3-only", action="store_true", help="只按 L3 条数过滤，不按任务失败过滤"
    )
    parser.add_argument(
        "--quarantine-dir", type=Path, help="隔离目录，必须是输入目录外的新目录"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="配合 --filter 预览，不移动数据"
    )
    args = parser.parse_args(argv)
    root = args.input_dir.expanduser().resolve()
    if not root.is_dir():
        parser.error("输入目录不存在")
    if args.min_events < 0:
        parser.error("--min-events 不能为负数")
    if (args.dry_run or args.quarantine_dir) and not args.filter:
        parser.error("--dry-run / --quarantine-dir 需要配合 --filter")
    rows = [audit_run(path, root) for path in discover(root)]
    for row in rows:
        row["filter_reasons"] = filter_reasons(
            row, args.min_events, l3_only=args.l3_only
        )
    selected = [row for row in rows if row["filter_reasons"]]
    print(f"输入目录：{root}")
    print_summary(rows, "Agent + LLM 收集数量（扫描时）")
    print("\n逐文件明细（路径相对输入目录；条数为 len(events)）")
    print_table(
        ["L3 文件", "L3 条数", "任务", "原始状态", "筛选原因 / 备注"],
        [
            [
                row["l3_file"],
                row["event_count"] if row["event_count"] is not None else "未知",
                row["task_result"],
                row["status"],
                "; ".join(row["filter_reasons"] + row["notes"]) or "—",
            ]
            for row in rows
        ],
    )
    rule = (
        f"L3 < {args.min_events}"
        if args.l3_only
        else f"任务失败 or L3 < {args.min_events}"
    )
    print(f"\n筛选条件：{rule}（L3 缺失/损坏也选中；进行中的运行跳过）")
    print(
        f"共 {len(rows)} 条；符合筛选条件 {len(selected)} 条；其余 {len(rows) - len(selected)} 条。"
    )
    if not args.filter or args.dry_run:
        print("仅预览，未移动数据。")
        return 0
    if not selected:
        print("无需转移。")
        return 0
    stamp = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d_%H%M%S-%f")
    destination = (
        args.quarantine_dir or root.parent.parent / "quarantine" / root.name / stamp
    )
    try:
        quarantine_runs(root, destination, selected)
    except (OSError, ValueError, TypeError, shutil.Error) as exc:
        parser.exit(2, f"转移失败: {exc}\n")
    print(f"已转移 {len(selected)} 条至：{destination.resolve()}")
    print_summary(
        [row for row in rows if not row["filter_reasons"]],
        "过滤后 Agent + LLM 收集数量",
    )
    return 0
