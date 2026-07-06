"""Recover POI locations from a Folium accessibility map export.

The original pipeline drew every POI as an L.marker in a dedicated
feature group; the processed MongoDB that held the POIs is gone, but the
markers survive in the exports. This pulls their coordinates back out.

Usage:
    python scripts/extract_pois.py INPUT.html OUTPUT.csv
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

_MARKER_RE = re.compile(
    r"L\.marker\(\s*\[([0-9.\-]+),\s*([0-9.\-]+)\]", re.DOTALL
)


def extract(html_text: str) -> list[tuple[float, float]]:
    pois = [(float(m.group(1)), float(m.group(2)))
            for m in _MARKER_RE.finditer(html_text)]
    if not pois:
        raise ValueError("no L.marker calls found — wrong file?")
    return pois


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    pois = extract(src.read_text(encoding="utf-8"))
    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon"])
        w.writerows(pois)
    print(f"{src.name}: {len(pois)} POIs -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
