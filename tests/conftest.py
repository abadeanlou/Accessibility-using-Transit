"""Put the repo root on sys.path so tests import Library/ and scripts/
regardless of how pytest is invoked (pytest binary vs python -m pytest)
or which test module gets collected first."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "scripts"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
