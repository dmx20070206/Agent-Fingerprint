"""Paths shared by source checkouts and editable installations."""
import os
from pathlib import Path
PROJECT_ROOT = Path(os.environ.get("AGENT_FINGERPRINT_ROOT", Path(__file__).resolve().parents[2])).resolve()
PACKAGE_ROOT = Path(__file__).resolve().parent
