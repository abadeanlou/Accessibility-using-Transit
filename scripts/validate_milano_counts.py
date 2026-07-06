"""Compare the recomputed Milano reachable-POI counts against the
original research export (accessibility_map_Milano_P2NumPOI_8.html).

Levels are NOT expected to match: the original used a different POI set
(and 2022-era timetables + OSRM street walking); this checks that the
SPATIAL PATTERN agrees - hexes the original scored high should score
high now. Reports Pearson/Spearman correlation on hexes matched by
centroid (<=30 m).

Usage:
    python scripts/validate_milano_counts.py ORIGINAL_EXPORT.html data/numpoi_Milano.csv
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from harvest_maps import _BIND_RE, _HTML_RE, _POLYGON_RE, _SET_CONTENT_RE, _centroid  # noqa: E402

_COUNT_RE = re.compile(r"Reachable POIs \((\d+)h\):\s*(\d+)")


def parse_original(html_text: str) -> list[tuple[float, float, int]]:
    polygons = {m.group(1): m.group(2) for m in _POLYGON_RE.finditer(html_text)}
    htmls = {m.group(1): m.group(2) for m in _HTML_RE.finditer(html_text)}
    popup_html = dict(_SET_CONTENT_RE.findall(html_text))
    rows = []
    for polygon_id, popup_id in _BIND_RE.findall(html_text):
        coords = polygons.get(polygon_id)
        text = htmls.get(popup_html.get(popup_id, ""), "")
        m = _COUNT_RE.search(text)
        if coords and m and m.group(1) == "8":
            lat, lon = _centroid(coords)
            rows.append((lat, lon, int(m.group(2))))
    if not rows:
        raise ValueError("no count popups parsed from the original export")
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    original = parse_original(Path(argv[1]).read_text(encoding="utf-8"))

    new_rows = []
    with Path(argv[2]).open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            new_rows.append((float(r["lat"]), float(r["lon"]),
                             int(r["reachable_pois"])))

    from scipy.spatial import cKDTree
    from scipy.stats import pearsonr, spearmanr

    mean_lat = float(np.mean([r[0] for r in new_rows]))
    scale = np.array([111320.0, 111320.0 * np.cos(np.radians(mean_lat))])
    new_xy = np.array([[r[0], r[1]] for r in new_rows]) * scale
    tree = cKDTree(new_xy)
    orig_xy = np.array([[r[0], r[1]] for r in original]) * scale
    dist, idx = tree.query(orig_xy, k=1)

    matched = dist <= 30.0
    o = np.array([r[2] for r in original])[matched]
    n = np.array([new_rows[i][2] for i in idx[matched]])
    print(f"matched {matched.sum()}/{len(original)} original hexes "
          f"(median centroid offset {np.median(dist[matched]):.1f} m)")
    print(f"original counts: mean {o.mean():.1f}, max {o.max()}")
    print(f"recomputed:      mean {n.mean():.1f}, max {n.max()}")
    print(f"Pearson r  = {pearsonr(o, n)[0]:.3f}")
    print(f"Spearman ρ = {spearmanr(o, n)[0]:.3f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
