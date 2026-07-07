"""Render the recomputed essential-services counts as interactive maps.

One self-contained Leaflet page per city (Maps/essential_services_<City>.html):
every hex drawn as a hexagon colored by how many essential services it
reaches by transit within 60 minutes at 8:00 (data/numpoi_<City>.csv),
with the POI layer toggleable. Hundreds of KB instead of the multi-MB
Folium exports - hex geometry is reconstructed client-side from the
centroid, the grid's circumradius and orientation (estimated from
nearest-neighbour spacing).

Usage:
    python scripts/build_numpoi_maps.py            # all three cities
    python scripts/build_numpoi_maps.py Torino     # one city
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CITIES = {
    "Torino": (45.07, 7.68, 12),
    "Milano": (45.46, 9.19, 11),
    "Paris": (48.86, 2.35, 10),
}
VIRIDIS = ["#440154", "#443983", "#31688e", "#21918c",
           "#35b779", "#90d743", "#fde725"]

LEAFLET_CSS = ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
               "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H")
LEAFLET_JS = ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
              "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH")


def load_hexes(city: str) -> list[list]:
    rows = []
    with (ROOT / "data" / f"numpoi_{city}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([round(float(r["lat"]), 5), round(float(r["lon"]), 5),
                         int(r["reachable_pois"]), int(float(r["population"]))])
    if not rows:
        raise ValueError(f"no hexes for {city}")
    return rows


def load_pois(city: str) -> list[list]:
    rows = []
    with (ROOT / "data" / f"pois_{city}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([round(float(r["lat"]), 5), round(float(r["lon"]), 5)])
    return rows


def grid_geometry(rows: list[list]) -> tuple[float, float]:
    """Estimate (circumradius_m, base_angle_deg) of the hex tiling from
    nearest-neighbour spacing; fall back to the notebook's 250 m edge."""
    from scipy.spatial import cKDTree

    if len(rows) < 2:
        return 250.0, 30.0
    mean_lat = float(np.mean([r[0] for r in rows]))
    scale = np.array([111320.0, 111320.0 * np.cos(np.radians(mean_lat))])
    xy = np.array([[r[0], r[1]] for r in rows]) * scale
    tree = cKDTree(xy)
    dist, idx = tree.query(xy[: min(500, len(xy))], k=2)
    spacing = float(np.median(dist[:, 1]))
    vecs = xy[idx[:, 1]] - xy[: len(idx)]
    # neighbour bearings repeat every 60 deg; vertices sit 30 deg off them
    ang = np.degrees(np.arctan2(vecs[:, 0], vecs[:, 1])) % 60.0
    base = float(np.median(ang)) + 30.0
    return spacing / np.sqrt(3.0), base


def class_breaks(counts: list[int]) -> list[int]:
    qs = np.quantile(counts, np.linspace(0, 1, len(VIRIDIS) + 1))
    breaks = sorted(set(int(q) for q in qs))
    while len(breaks) < 2:
        breaks.append(breaks[-1] + 1)
    return breaks


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Essential services by transit — {city}</title>
<link rel="stylesheet" href="{css_url}" integrity="{css_sri}" crossorigin="anonymous">
<style>
  html, body {{ height:100%; margin:0; }}
  #map {{ height:100%; }}
  .panel {{ font-family: Verdana, sans-serif; font-size:12px; background:#fff;
           padding:10px 12px; border-radius:4px; box-shadow:0 1px 5px rgba(0,0,0,.3);
           max-width:270px; line-height:1.5; }}
  .panel h1 {{ font-size:13px; margin:0 0 4px; }}
  .panel a {{ color:#8a6d3b; }}
  .legend i {{ display:inline-block; width:14px; height:14px; margin-right:6px;
              vertical-align:-2px; }}
</style>
</head>
<body>
<div id="map"></div>
<script src="{js_url}" integrity="{js_sri}" crossorigin="anonymous"></script>
<script>
var HEXES = {hexes_json};
var POIS = {pois_json};
var R_M = {radius:.1f}, BASE_DEG = {base:.1f}, BREAKS = {breaks_json};
var COLORS = {colors_json};

var map = L.map('map', {{preferCanvas: true}}).setView([{lat}, {lon}], {zoom});
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

function color(v) {{
  for (var i = BREAKS.length - 2; i > 0; i--) if (v >= BREAKS[i]) return COLORS[Math.min(i, COLORS.length - 1)];
  return COLORS[0];
}}
function hexRing(lat, lon) {{
  var ring = [], dLat = 1 / 111320, dLon = 1 / (111320 * Math.cos(lat * Math.PI / 180));
  for (var k = 0; k < 6; k++) {{
    var a = (BASE_DEG + 60 * k) * Math.PI / 180;
    ring.push([lat + R_M * Math.cos(a) * dLat, lon + R_M * Math.sin(a) * dLon]);
  }}
  return ring;
}}
var hexLayer = L.layerGroup();
HEXES.forEach(function (h) {{
  L.polygon(hexRing(h[0], h[1]), {{stroke: false, fillColor: color(h[2]), fillOpacity: 0.65}})
    .bindPopup('Essential services within 60 min: <b>' + h[2] + '</b><br>Population: ' + h[3])
    .addTo(hexLayer);
}});
hexLayer.addTo(map);
var poiLayer = L.layerGroup();
POIS.forEach(function (p) {{
  L.circleMarker([p[0], p[1]], {{radius: 2, stroke: false, fillColor: '#c0392b', fillOpacity: 0.8}})
    .addTo(poiLayer);
}});
L.control.layers(null, {{'Hexes': hexLayer, 'POIs': poiLayer}}, {{collapsed: true}}).addTo(map);

var info = L.control({{position: 'topright'}});
info.onAdd = function () {{
  var d = L.DomUtil.create('div', 'panel');
  d.innerHTML = '<h1>{city}: essential services by transit</h1>' +
    'Each hexagon: how many schools, healthcare places, supermarkets and markets ' +
    '(OpenStreetMap) are reachable by public transport within 60 minutes at 8:00. ' +
    'Recomputed 2026 from current GTFS. ' +
    '<a href="./">back to all maps</a>';
  return d;
}};
info.addTo(map);

var legend = L.control({{position: 'bottomright'}});
legend.onAdd = function () {{
  var d = L.DomUtil.create('div', 'panel legend');
  var html = '<b>Reachable services</b><br>';
  for (var i = 0; i < BREAKS.length - 1; i++) {{
    html += '<i style="background:' + COLORS[Math.min(i, COLORS.length - 1)] + '"></i>' +
            BREAKS[i] + ' &ndash; ' + BREAKS[i + 1] + '<br>';
  }}
  d.innerHTML = html;
  return d;
}};
legend.addTo(map);
</script>
</body>
</html>
"""


def build_city(city: str) -> Path:
    hexes = load_hexes(city)
    pois = load_pois(city)
    radius, base = grid_geometry(hexes)
    breaks = class_breaks([h[2] for h in hexes])
    lat, lon, zoom = CITIES[city]
    html = PAGE.format(
        city=city, lat=lat, lon=lon, zoom=zoom,
        css_url=LEAFLET_CSS[0], css_sri=LEAFLET_CSS[1],
        js_url=LEAFLET_JS[0], js_sri=LEAFLET_JS[1],
        hexes_json=json.dumps(hexes, separators=(",", ":")),
        pois_json=json.dumps(pois, separators=(",", ":")),
        breaks_json=json.dumps(breaks), colors_json=json.dumps(VIRIDIS),
        radius=radius, base=base,
    )
    out = ROOT / "Maps" / f"essential_services_{city}.html"
    out.write_text(html, encoding="utf-8")
    print(f"{city}: {len(hexes)} hexes, {len(pois)} POIs, "
          f"R={radius:.0f} m, base={base:.0f} deg, breaks={breaks} "
          f"-> {out.name} ({out.stat().st_size / 1024:.0f} KB)")
    return out


def main(argv: list[str]) -> int:
    cities = argv[1:] or list(CITIES)
    for city in cities:
        build_city(city)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
