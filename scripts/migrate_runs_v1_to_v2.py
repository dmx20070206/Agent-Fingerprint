"""Compatibility command for the explicit v1 artifact migration."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_fingerprint.storage.migrate import main, migrate

if __name__ == "__main__":
    main()
