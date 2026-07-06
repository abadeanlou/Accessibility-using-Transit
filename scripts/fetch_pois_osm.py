"""Fetch the essential-services POI basket from OpenStreetMap.

The basket (same for every city, published with the equity results):
- education: amenity = school, university, college
- healthcare: amenity = hospital, clinic, doctors, pharmacy
- daily needs: shop = supermarket, amenity = marketplace

The query area is the bounding box of the city's hex grid (from the
harvested CSV), padded by ~1 km; results are then filtered to points
within 1.2 km of some hex centroid so distant out-of-area POIs don't
leak in.

Usage:
    python scripts/fetch_pois_osm.py data/hexes_<City>_P2P.csv data/pois_<City>.csv
"""
from __future__ import annotations

import csv
import json
import sys
import time
import urllib.request
from pathlib import Path

OVERPASS_URLS = [
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass-api.de/api/interpreter",
]
AMENITIES = "school|university|college|hospital|clinic|doctors|pharmacy|marketplace"
PAD_DEG = 0.012           # ~1.2 km bbox padding
MAX_DIST_TO_GRID_M = 1200.0


def _grid_centroids(hex_csv: Path) -> list[tuple[float, float]]:
    pts = set()
    with hex_csv.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            pts.add((float(row["lat"]), float(row["lon"])))
    if not pts:
        raise ValueError(f"{hex_csv}: no hex centroids")
    return sorted(pts)


def _query(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    box = f"({s},{w},{n},{e})"
    return (
        f"[out:json][timeout:300];"
        f"(nwr[\"amenity\"~\"^({AMENITIES})$\"]{box};"
        f"nwr[\"shop\"=\"supermarket\"]{box};"
        f");out center;"
    )


def fetch(hex_csv: Path) -> list[dict]:
    import numpy as np
    from scipy.spatial import cKDTree

    grid = _grid_centroids(hex_csv)
    lats = [p[0] for p in grid]
    lons = [p[1] for p in grid]
    bbox = (min(lats) - PAD_DEG, min(lons) - PAD_DEG,
            max(lats) + PAD_DEG, max(lons) + PAD_DEG)

    body = _query(bbox).encode()
    data = None
    last_err = None
    for attempt in range(4):
        url = OVERPASS_URLS[attempt % len(OVERPASS_URLS)]
        req = urllib.request.Request(
            url, data=body,
            headers={"User-Agent": "transit-access/0.1 (equity research)"},
        )
        try:
            with urllib.request.urlopen(req, timeout=360) as r:
                data = json.load(r)
            break
        except Exception as e:  # noqa: BLE001 - retry then re-raise
            last_err = e
            print(f"attempt {attempt + 1} via {url} failed: {e}")
            time.sleep(20)
    if data is None:
        raise last_err

    pois = []
    for el in data.get("elements", []):
        if "lat" in el:
            lat, lon = el["lat"], el["lon"]
        elif "center" in el:
            lat, lon = el["center"]["lat"], el["center"]["lon"]
        else:
            continue
        tags = el.get("tags", {})
        kind = tags.get("amenity") or ("supermarket" if tags.get("shop") == "supermarket" else "")
        if not kind:
            continue
        pois.append({"lat": lat, "lon": lon, "kind": kind})

    if not pois:
        raise ValueError("Overpass returned no POIs — check bbox/endpoint")

    # keep only POIs near the actual hex grid (planar approx is fine here)
    mean_lat = float(np.mean(lats))
    scale = np.array([111320.0, 111320.0 * np.cos(np.radians(mean_lat))])
    tree = cKDTree(np.array(grid) * scale)
    pts = np.array([[p["lat"], p["lon"]] for p in pois]) * scale
    dist, _ = tree.query(pts, k=1)
    kept = [p for p, d in zip(pois, dist) if d <= MAX_DIST_TO_GRID_M]
    print(f"fetched {len(pois)} POIs, kept {len(kept)} within "
          f"{MAX_DIST_TO_GRID_M:.0f} m of the grid")
    return kept


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    hex_csv, dst = Path(argv[1]), Path(argv[2])
    pois = fetch(hex_csv)
    with dst.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["lat", "lon", "kind"])
        w.writeheader()
        w.writerows(pois)
    kinds = {}
    for p in pois:
        kinds[p["kind"]] = kinds.get(p["kind"], 0) + 1
    print(f"-> {dst}: {len(pois)} POIs {dict(sorted(kinds.items()))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
