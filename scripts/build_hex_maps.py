"""Render all hex maps as compact self-contained Leaflet pages.

Three map kinds per city, one visual language (hexagons reconstructed
client-side from centroids, canvas rendering, quantile classes, yellow
always = best served):

- p2p_<City>.html       - average travel time to the whole city, per
                          hour layer (values recovered from the original
                          research exports via harvest_maps.py)
- p2poi_<City>.html     - average travel time to amenities, same source
- essential_services_<City>.html - services reachable in 60 min at 8:00,
                          recomputed 2026 (reachable_pois.py), POI layer

Hundreds of KB per page instead of the 6-55 MB Folium exports.

Usage:
    python scripts/build_hex_maps.py             # all maps, all cities
    python scripts/build_hex_maps.py Torino      # one city
"""
from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CITIES = {
    "Torino": (45.07, 7.68, 11),
    "Milano": (45.46, 9.19, 11),
    "Paris": (48.86, 2.35, 10),
}
VIRIDIS = ["#440154", "#443983", "#31688e", "#21918c",
           "#35b779", "#90d743", "#fde725"]

LEAFLET_CSS = ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css",
               "sha384-sHL9NAb7lN7rfvG5lfHpm643Xkcjzp4jFvuavGOndn6pjVqS6ny56CAt3nsEVT4H")
LEAFLET_JS = ("https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js",
              "sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH")


def load_time_layers(city: str, map_type: str) -> dict[int, list[list]]:
    """{hour: [[lat, lon, minutes, population], ...]} from a harvest CSV."""
    layers = defaultdict(list)
    path = ROOT / "data" / f"hexes_{city}_{map_type}.csv"
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["travel_time_min"] == "":
                continue
            layers[int(r["hour"])].append(
                [round(float(r["lat"]), 5), round(float(r["lon"]), 5),
                 round(float(r["travel_time_min"]), 1),
                 int(float(r["population"]))]
            )
    if not layers:
        raise ValueError(f"{path.name}: no rows")
    return dict(layers)


def load_numpoi_layers(city: str) -> dict[int, list[list]]:
    rows = []
    with (ROOT / "data" / f"numpoi_{city}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([round(float(r["lat"]), 5), round(float(r["lon"]), 5),
                         int(r["reachable_pois"]), int(float(r["population"]))])
    if not rows:
        raise ValueError(f"no hexes for {city}")
    return {8: rows}


def load_pois(city: str) -> list[list]:
    rows = []
    with (ROOT / "data" / f"pois_{city}.csv").open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append([round(float(r["lat"]), 5), round(float(r["lon"]), 5)])
    return rows


KINDS = {
    "P2P": {
        "file": "p2p_{city}.html",
        "title": "{city}: average travel time to the whole city",
        "blurb": ("Each hexagon: the average public-transport travel time from "
                  "that cell to the entire city, in minutes, by departure hour. "
                  "Values from the original research runs (GTFS + MongoDB + "
                  "OSRM), re-rendered."),
        "value_label": "Average travel time to the city",
        "unit": " min",
        "legend": "Travel time (min)",
        "loader": load_time_layers,
        "higher_better": False,
        "pois": False,
    },
    "P2POI": {
        "file": "p2poi_{city}.html",
        "title": "{city}: average travel time to amenities",
        "blurb": ("Each hexagon: the average public-transport travel time from "
                  "that cell to the points of interest, in minutes, by "
                  "departure hour. Values from the original research runs "
                  "(GTFS + MongoDB + OSRM), re-rendered."),
        "value_label": "Average travel time to amenities",
        "unit": " min",
        "legend": "Travel time (min)",
        "loader": load_time_layers,
        "higher_better": False,
        "pois": False,
    },
    "NUMPOI": {
        "file": "essential_services_{city}.html",
        "title": "{city}: essential services by transit",
        "blurb": ("Each hexagon: how many schools, healthcare places, "
                  "supermarkets and markets (OpenStreetMap) are reachable by "
                  "public transport within 60 minutes at 8:00. Recomputed "
                  "2026 from current GTFS."),
        "value_label": "Essential services within 60 min",
        "unit": "",
        "legend": "Reachable services",
        "loader": lambda city, _mt=None: load_numpoi_layers(city),
        "higher_better": True,
        "pois": True,
    },
}


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
    # Compass bearing (from north toward east) to match the JS vertex
    # formula (lat += R cos a, lon += R sin a). Neighbour bearings repeat
    # every 60 deg; vertices sit 30 deg off them.
    ang = np.degrees(np.arctan2(vecs[:, 1], vecs[:, 0])) % 60.0
    base = (float(np.median(ang)) + 30.0) % 60.0
    return spacing / np.sqrt(3.0), base


def class_breaks(values: list[float]) -> list[float]:
    qs = np.quantile(values, np.linspace(0, 1, len(VIRIDIS) + 1))
    ints = all(float(v).is_integer() for v in values[:50])
    breaks = sorted(set((int(q) if ints else round(float(q), 1)) for q in qs))
    while len(breaks) < 2:
        breaks.append(breaks[-1] + 1)
    return breaks


PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title}</title>
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
var LAYERS = {layers_json};
var POIS = {pois_json};
var R_M = {radius:.1f}, BASE_DEG = {base:.1f}, BREAKS = {breaks_json};
var COLORS = {colors_json};
var HIGHER_BETTER = {higher_better};
var VALUE_LABEL = {value_label_json}, UNIT = {unit_json};

var map = L.map('map', {{preferCanvas: true}}).setView([{lat}, {lon}], {zoom});
L.tileLayer('https://tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
  maxZoom: 18, attribution: '&copy; OpenStreetMap contributors'
}}).addTo(map);

function color(v) {{
  var i = 0;
  for (var k = BREAKS.length - 2; k > 0; k--) if (v >= BREAKS[k]) {{ i = k; break; }}
  var idx = HIGHER_BETTER ? i : (BREAKS.length - 2 - i);
  return COLORS[Math.max(0, Math.min(idx, COLORS.length - 1))];
}}
function hexRing(lat, lon) {{
  var ring = [], dLat = 1 / 111320, dLon = 1 / (111320 * Math.cos(lat * Math.PI / 180));
  for (var k = 0; k < 6; k++) {{
    var a = (BASE_DEG + 60 * k) * Math.PI / 180;
    ring.push([lat + R_M * Math.cos(a) * dLat, lon + R_M * Math.sin(a) * dLon]);
  }}
  return ring;
}}
function buildHourLayer(rows) {{
  var g = L.layerGroup();
  rows.forEach(function (h) {{
    L.polygon(hexRing(h[0], h[1]), {{stroke: false, fillColor: color(h[2]), fillOpacity: 0.65}})
      .bindPopup(VALUE_LABEL + ': <b>' + h[2] + UNIT + '</b><br>Population: ' + h[3])
      .addTo(g);
  }});
  return g;
}}
var hours = Object.keys(LAYERS).sort(function (a, b) {{ return a - b; }});
var baseLayers = {{}};
hours.forEach(function (h) {{ baseLayers[h + ':00'] = buildHourLayer(LAYERS[h]); }});
var defaultHour = hours.indexOf('8') >= 0 ? '8' : hours[0];
baseLayers[defaultHour + ':00'].addTo(map);

var overlays = {{}};
if (POIS.length) {{
  var poiLayer = L.layerGroup();
  POIS.forEach(function (p) {{
    L.circleMarker([p[0], p[1]], {{radius: 2, stroke: false, fillColor: '#c0392b', fillOpacity: 0.8}})
      .addTo(poiLayer);
  }});
  overlays['POIs'] = poiLayer;
}}
if (hours.length > 1 || POIS.length) {{
  L.control.layers(hours.length > 1 ? baseLayers : null, overlays, {{collapsed: true}}).addTo(map);
}}

var info = L.control({{position: 'topright'}});
info.onAdd = function () {{
  var d = L.DomUtil.create('div', 'panel');
  d.innerHTML = '<h1>{title}</h1>{blurb} <a href="./">back to all maps</a>';
  return d;
}};
info.addTo(map);

var legend = L.control({{position: 'bottomright'}});
legend.onAdd = function () {{
  var d = L.DomUtil.create('div', 'panel legend');
  var html = '<b>{legend}</b><br>';
  for (var i = 0; i < BREAKS.length - 1; i++) {{
    var idx = HIGHER_BETTER ? i : (BREAKS.length - 2 - i);
    html += '<i style="background:' + COLORS[Math.max(0, Math.min(idx, COLORS.length - 1))] + '"></i>' +
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


def build_map(city: str, kind: str) -> Path:
    spec = KINDS[kind]
    layers = spec["loader"](city, kind)
    pois = load_pois(city) if spec["pois"] else []
    first = next(iter(layers.values()))
    radius, base = grid_geometry(first)
    all_values = [r[2] for rows in layers.values() for r in rows]
    breaks = class_breaks(all_values)
    lat, lon, zoom = CITIES[city]
    title = spec["title"].format(city=city)
    html = PAGE.format(
        title=title, blurb=spec["blurb"].format(city=city),
        legend=spec["legend"], lat=lat, lon=lon, zoom=zoom,
        css_url=LEAFLET_CSS[0], css_sri=LEAFLET_CSS[1],
        js_url=LEAFLET_JS[0], js_sri=LEAFLET_JS[1],
        layers_json=json.dumps(layers, separators=(",", ":")),
        pois_json=json.dumps(pois, separators=(",", ":")),
        breaks_json=json.dumps(breaks), colors_json=json.dumps(VIRIDIS),
        higher_better=("true" if spec["higher_better"] else "false"),
        value_label_json=json.dumps(spec["value_label"]),
        unit_json=json.dumps(spec["unit"]),
        radius=radius, base=base,
    )
    out = ROOT / "Maps" / spec["file"].format(city=city)
    out.write_text(html, encoding="utf-8")
    n = sum(len(rows) for rows in layers.values())
    print(f"{city} {kind}: {n} hex-layers rows, hours {sorted(layers)}, "
          f"R={radius:.0f} m, base={base:.0f} deg "
          f"-> {out.name} ({out.stat().st_size / 1024:.0f} KB)")
    return out


def main(argv: list[str]) -> int:
    cities = argv[1:] or list(CITIES)
    for city in cities:
        for kind in KINDS:
            build_map(city, kind)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
