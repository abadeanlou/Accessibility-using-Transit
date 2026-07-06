"""Compute equity metrics per city and publish them as a section of the
live gallery page.

Two views, both population-weighted via Library/equity.py:

- P2P (whole-city reach): from data/hexes_<City>_P2P.csv (harvested out
  of the map exports, 8:00 layer), accessibility = 1 / travel time.
- Amenities: from data/numpoi_<City>.csv (scripts/reachable_pois.py),
  accessibility = number of essential services reachable within 60 min
  at 8:00 - a cumulative-opportunities measure, no transform needed.

Output:
- data/equity_results.json — all numbers, for the README and reuse
- Maps/index.html — the block between <!-- EQUITY:START --> and
  <!-- EQUITY:END --> is rewritten with a comparison table and one
  inline-SVG Lorenz chart per view

Usage:
    python scripts/build_equity.py
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from Library import equity  # noqa: E402

HOUR = 8
# A cell containing the destination exports a ~0-minute travel time;
# inverting that raw would hand one cell a near-infinite accessibility.
MIN_TRAVEL_TIME_MIN = 1.0
CITIES = ["Torino", "Milano", "Paris"]
VIEWS = {"P2P": "whole-city reach", "AMENITIES": "reachable essential services"}
CITY_COLORS = {"Torino": "#8a6d3b", "Milano": "#4a7c59", "Paris": "#5b6d9a"}
_MAX_CURVE_POINTS = 250


def load_hexes(csv_path: Path, hour: int = HOUR) -> tuple[list[float], list[float]]:
    """Return (accessibility 1/min, population) for one export's hour layer."""
    access, pop = [], []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            if int(row["hour"]) != hour:
                continue
            t = row["travel_time_min"]
            access.append(
                0.0 if t == "" else 1.0 / max(float(t), MIN_TRAVEL_TIME_MIN)
            )
            pop.append(float(row["population"]))
    if not access:
        raise ValueError(f"{csv_path.name}: no rows for hour {hour}")
    return access, pop


def load_numpoi(csv_path: Path) -> tuple[list[float], list[float]]:
    """Return (reachable-POI count, population) per hex."""
    access, pop = [], []
    with csv_path.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            access.append(float(row["reachable_pois"]))
            pop.append(float(row["population"]))
    if not access:
        raise ValueError(f"{csv_path.name}: no rows")
    return access, pop


def _load_view(data_dir: Path, city: str, view: str):
    if view == "P2P":
        return load_hexes(data_dir / f"hexes_{city}_P2P.csv")
    return load_numpoi(data_dir / f"numpoi_{city}.csv")


def compute_all(data_dir: Path) -> dict:
    results = {}
    for map_type in VIEWS:
        for city in CITIES:
            access, pop = _load_view(data_dir, city, map_type)
            summary = equity.equity_summary(access, pop)
            curve = equity.lorenz_points(access, pop)
            step = max(1, len(curve) // _MAX_CURVE_POINTS)
            sampled = [list(p) for p in curve[::step]]
            if sampled[-1] != [1.0, 1.0]:
                sampled.append([1.0, 1.0])
            results[f"{city}_{map_type}"] = {
                "city": city,
                "map_type": map_type,
                "n_cells": len(access),
                **summary,
                "lorenz": sampled,
            }
    return results


def _svg_path(points: list[list[float]], size: int, pad: int) -> str:
    scale = size - 2 * pad
    coords = [
        f"{pad + x * scale:.1f},{pad + (1 - y) * scale:.1f}" for x, y in points
    ]
    return "M" + " L".join(coords)


def render_lorenz_svg(results: dict, map_type: str) -> str:
    size, pad = 340, 34
    scale = size - 2 * pad
    parts = [
        f'<svg viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="Lorenz curves, {VIEWS[map_type]}" '
        f'style="width:100%;max-width:{size}px;height:auto;">',
        f'<rect x="{pad}" y="{pad}" width="{scale}" height="{scale}" '
        f'fill="none" stroke="var(--border)"/>',
        f'<line x1="{pad}" y1="{size - pad}" x2="{size - pad}" y2="{pad}" '
        f'stroke="var(--muted)" stroke-dasharray="4 4"/>',
    ]
    for city in CITIES:
        r = results[f"{city}_{map_type}"]
        parts.append(
            f'<path d="{_svg_path(r["lorenz"], size, pad)}" fill="none" '
            f'stroke="{CITY_COLORS[city]}" stroke-width="2"/>'
        )
    legend_y = pad + 14
    for city in CITIES:
        g = results[f"{city}_{map_type}"]["gini"]
        parts.append(
            f'<line x1="{pad + 10}" y1="{legend_y - 4}" x2="{pad + 30}" '
            f'y2="{legend_y - 4}" stroke="{CITY_COLORS[city]}" stroke-width="2"/>'
            f'<text x="{pad + 36}" y="{legend_y}" font-size="12" '
            f'fill="var(--ink)">{city} · Gini {g:.3f}</text>'
        )
        legend_y += 18
    parts.append(
        f'<text x="{size / 2:.0f}" y="{size - 6}" font-size="11" '
        f'fill="var(--muted)" text-anchor="middle">cumulative population share '
        f'(least accessible first)</text>'
        f'<text x="12" y="{size / 2:.0f}" font-size="11" fill="var(--muted)" '
        f'text-anchor="middle" transform="rotate(-90 12 {size / 2:.0f})">'
        f'cumulative accessibility share</text>'
    )
    parts.append("</svg>")
    return "".join(parts)


def _fmt_palma(v: float) -> str:
    return "∞" if v == float("inf") else f"{v:.2f}"


_VIEW_LABELS = {"P2P": "P2P — whole-city reach",
                "AMENITIES": "Essential services within 60 min"}


def render_section(results: dict) -> str:
    rows = []
    for map_type in VIEWS:
        for city in CITIES:
            r = results[f"{city}_{map_type}"]
            rows.append(
                f"<tr><td>{city}</td><td>{_VIEW_LABELS[map_type]}</td>"
                f"<td>{r['gini']:.3f}</td><td>{r['theil']:.3f}</td>"
                f"<td>{r['atkinson_e05']:.3f}</td>"
                f"<td>{_fmt_palma(r['palma_ratio'])}</td>"
                f"<td>{r['bottom_half_accessibility_share'] * 100:.1f}%</td></tr>"
            )
    table = (
        '<div style="overflow-x:auto;"><table class="equity-table">'
        "<thead><tr><th>City</th><th>View</th><th>Gini</th><th>Theil</th>"
        "<th>Atkinson (ε=0.5)</th><th>Palma</th><th>Bottom-50% share</th>"
        "</tr></thead><tbody>" + "".join(rows) + "</tbody></table></div>"
    )
    charts = "".join(
        f'<figure><figcaption>{_VIEW_LABELS[map_type]}</figcaption>'
        f"{render_lorenz_svg(results, map_type)}</figure>"
        for map_type in VIEWS
    )
    return f"""
    <h2 id="equity">Equity</h2>
    <p>How evenly is that accessibility spread over <em>people</em>? Two
    population-weighted views, using the inequality-index family of my
    <a href="https://arxiv.org/abs/2206.09037">hEART 2022</a> and
    <a href="https://arxiv.org/abs/2210.00128">TRB 2023</a> papers.
    <b>Whole-city reach</b>: each hex's average travel time t to the entire
    city (recovered from the maps above, 8:00 layer) as a velocity-like
    accessibility 1/t. <b>Essential services</b>: how many schools,
    universities, hospitals, clinics, doctors, pharmacies, supermarkets and
    markets (OpenStreetMap) each hex reaches by transit within 60 minutes
    at 8:00 — a cumulative-opportunities measure recomputed from current
    GTFS timetables with the same limits as the original research (15-min
    walk to stops, 5-min transfers). 0 means perfectly equal on every
    index; the Palma ratio is the accessibility share of the best-served
    10% of people over the worst-served 40% (0.25 at perfect equality).</p>
    {table}
    <div class="equity-charts">{charts}</div>
    <p class="equity-note">Read a Lorenz curve as: the bottom x% of people
    (ranked by accessibility) hold y% of the city's total accessibility —
    the farther below the diagonal, the less equal. Near-equality on
    whole-city reach is expected: an average over the entire city is
    dominated by geography every resident shares, and the source exports
    cap travel times at 120 minutes. Caveats: population weights come from
    the original research exports (one assumed service day per city);
    unreachable cells were dropped at export time; the essential-services
    view walks straight-line distances scaled by 1.3 rather than routed
    streets, and uses 2026 timetables while the maps above show the
    original research runs.</p>
    """


def update_index(index_path: Path, section_html: str) -> None:
    html = index_path.read_text(encoding="utf-8")
    start_marker = "<!-- EQUITY:START -->"
    end_marker = "<!-- EQUITY:END -->"
    start = html.index(start_marker) + len(start_marker)
    end = html.index(end_marker)
    index_path.write_text(html[:start] + section_html + html[end:], encoding="utf-8")


def main() -> int:
    results = compute_all(ROOT / "data")
    out = {
        "hour": HOUR,
        "views": {
            "P2P": "1 / max(average_travel_time_min, 1.0)",
            "AMENITIES": "essential services reachable by transit within "
                         "60 min (scripts/reachable_pois.py)",
        },
        "results": {
            k: {kk: vv for kk, vv in v.items() if kk != "lorenz"}
            for k, v in results.items()
        },
    }
    (ROOT / "data" / "equity_results.json").write_text(
        json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    update_index(ROOT / "Maps" / "index.html", render_section(results))
    for key, r in results.items():
        print(f"{key}: gini={r['gini']:.3f} theil={r['theil']:.3f} "
              f"atkinson={r['atkinson_e05']:.3f} palma={_fmt_palma(r['palma_ratio'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
