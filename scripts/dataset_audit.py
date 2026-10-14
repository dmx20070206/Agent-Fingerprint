#!/usr/bin/env python3
"""Print dataset counts and optionally quarantine low-quality runs. See docs/workflow.md#检查与过滤."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agent_fingerprint.analysis.dataset_audit import main

if __name__ == "__main__":
    raise SystemExit(main())
