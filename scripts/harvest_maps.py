"""Recover the per-hex data table from a Folium accessibility map export.

The interactive maps in Maps/ inline every hexagon and popup as Leaflet
JavaScript literals, which makes them a recoverable data store: this
script parses one export back into a tidy CSV of
(lat, lon, travel_time_min, population) rows — the inputs the equity
metrics need — without rerunning the MongoDB + OSRM pipeline.

Pairing goes through the explicit ``polygon.bindPopup(popup)`` calls,
never file order, and any polygon that does not resolve to exactly one
parsed popup aborts the run. Cells whose popup says "Not reachable" get
an empty travel_time_min (downstream treats them as zero accessibility).

Usage:
    python scripts/harvest_maps.py INPUT.html OUTPUT.csv

Writes OUTPUT.csv plus OUTPUT.csv.meta.json (verbatim popup label,
row counts, source name). Standard library only.
"""
from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

_POLYGON_RE = re.compile(
    r"var (polygon_[0-9a-f]+) = L\.polygon\(\s*(\[\[.*?\]\]),", re.DOTALL
)
_HTML_RE = re.compile(
    r"var (html_[0-9a-f]+) = \$\(`<div id=\"html_[0-9a-f]+\"[^>]*>(.*?)</div>`\)",
    re.DOTALL,
)
_SET_CONTENT_RE = re.compile(r"(popup_[0-9a-f]+)\.setContent\((html_[0-9a-f]+)\)")
_BIND_RE = re.compile(r"(polygon_[0-9a-f]+)\.bindPopup\((popup_[0-9a-f]+)\)")

_TIME_RE = re.compile(r"([^:<>]+?):\s*([\d.]+)\s*min")
_NOT_REACHABLE_RE = re.compile(r"([^:<>]+?):\s*Not reachable", re.IGNORECASE)
_POPULATION_RE = re.compile(r"Population:\s*([\d.]+)")
_HOUR_RE = re.compile(r"\((\d+)h\)")


def _centroid(coords_literal: str) -> tuple[float, float]:
    ring = json.loads(coords_literal)
    # L.polygon accepts a ring or a list of rings; take the outer ring.
    if ring and isinstance(ring[0][0], list):
        ring = ring[0]
    lats = [p[0] for p in ring]
    lons = [p[1] for p in ring]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _parse_popup(text: str):
    """Return (label, travel_time_min or None, population) or None if the
    popup is not a hex-cell popup (e.g. a POI marker)."""
    pop_m = _POPULATION_RE.search(text)
    if not pop_m:
        return None
    time_m = _TIME_RE.search(text)
    if time_m:
        return time_m.group(1).strip(), float(time_m.group(2)), float(pop_m.group(1))
    nr_m = _NOT_REACHABLE_RE.search(text)
    if nr_m:
        return nr_m.group(1).strip(), None, float(pop_m.group(1))
    return None


def harvest(html_text: str) -> tuple[list[dict], dict]:
    polygons = {m.group(1): m.group(2) for m in _POLYGON_RE.finditer(html_text)}
    htmls = {m.group(1): m.group(2) for m in _HTML_RE.finditer(html_text)}
    popup_html = dict(_SET_CONTENT_RE.findall(html_text))
    binds = _BIND_RE.findall(html_text)

    bound = {}
    for polygon_id, popup_id in binds:
        if polygon_id in bound:
            raise ValueError(f"{polygon_id} bound to more than one popup")
        bound[polygon_id] = popup_id

    if set(bound) != set(polygons):
        missing = set(polygons) - set(bound)
        extra = set(bound) - set(polygons)
        raise ValueError(
            f"polygon/popup pairing mismatch: {len(missing)} polygons without "
            f"a popup, {len(extra)} binds to unknown polygons"
        )

    rows = []
    labels = set()
    n_not_reachable = 0
    for polygon_id, coords_literal in polygons.items():
        popup_id = bound[polygon_id]
        html_id = popup_html.get(popup_id)
        text = htmls.get(html_id, "")
        parsed = _parse_popup(text)
        if parsed is None:
            raise ValueError(
                f"popup for {polygon_id} did not parse as a hex-cell popup: "
                f"{text[:120]!r}"
            )
        label, travel_time_min, population = parsed
        if population < 0:
            raise ValueError(f"negative population in popup for {polygon_id}")
        hour_m = _HOUR_RE.search(label)
        if not hour_m:
            raise ValueError(f"no (Nh) hour in popup label {label!r}")
        labels.add(label)
        if travel_time_min is None:
            n_not_reachable += 1
        lat, lon = _centroid(coords_literal)
        rows.append(
            {
                "lat": round(lat, 6),
                "lon": round(lon, 6),
                "hour": int(hour_m.group(1)),
                "travel_time_min": "" if travel_time_min is None else travel_time_min,
                "population": population,
            }
        )

    if not rows:
        raise ValueError("no hex polygons found — not a Folium accessibility export?")

    hours = sorted({r["hour"] for r in rows})
    meta = {
        "popup_labels": sorted(labels),
        "hours": hours,
        "n_cells_per_hour": {h: sum(1 for r in rows if r["hour"] == h) for h in hours},
        "n_cells": len(rows),
        "n_not_reachable": n_not_reachable,
        "total_population_per_hour": {
            h: sum(r["population"] for r in rows if r["hour"] == h) for h in hours
        },
    }
    return rows, meta


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, dst = Path(argv[1]), Path(argv[2])
    rows, meta = harvest(src.read_text(encoding="utf-8"))
    meta["source"] = src.name

    dst.parent.mkdir(parents=True, exist_ok=True)
    with dst.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f, fieldnames=["lat", "lon", "hour", "travel_time_min", "population"]
        )
        writer.writeheader()
        writer.writerows(rows)
    Path(str(dst) + ".meta.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(f"{src.name}: {meta['n_cells']} cells across hours {meta['hours']} "
          f"({meta['n_not_reachable']} not reachable), "
          f"labels {meta['popup_labels']} -> {dst}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
