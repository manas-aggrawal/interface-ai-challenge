"""Dev helpers: load the snapshots exactly like the harness does."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.insert(0, str(ROOT / "submission"))

from run_eval import load_snapshots  # noqa: E402


def snapshots():
    return load_snapshots(ROOT / "snapshots")
