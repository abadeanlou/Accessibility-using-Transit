# Equity results for the accessibility maps — design

Date: 2026-07-06 · Status: approved

## Goal

Publish population-weighted equity metrics (Gini, Theil, Atkinson ε=0.5,
Palma ratio) and Lorenz curves for Torino, Milano, and Paris on the live
gallery page (https://abadeanlou.com/accessibility/), computed from the
per-hex data embedded in the existing Folium map exports. This makes the
"modern successor to my published equity research" claim concretely true:
the same inequality-index family as the hEART 2022 / TRB 2023 papers,
visible next to the maps.

## Decisions (made with the author)

- **Cities**: Torino, Milano, Paris only (the four cities whose exports
  lack per-cell population were removed from the demo on 2026-07-06).
- **Accessibility variable**: `accessibility = 1 / travel_time_min` — a
  velocity-like measure, closest in spirit to the papers' scores. All
  indices are population-weighted.
- **Map types**: P2P and P2POI per city (both carry per-hex population in
  their popups). POI2P catchment maps are excluded — no per-hex population.
- **Presentation**: a new "Equity" section on the existing gallery index
  page (`Maps/index.html`) — comparison table plus inline-SVG Lorenz
  curves. No separate page.

## Components

1. `scripts/harvest_maps.py` — parses one Folium export (HTML) into a tidy
   CSV `data/hexes_<City>_<Type>.csv` with columns
   `lat, lon, travel_time_min, population` (lat/lon = polygon centroid).
   Parsing strategy: from the generated Leaflet JS, build three maps —
   `polygon_id -> coordinates`, `popup_id -> (time, population)` parsed
   from the popup HTML, and `polygon_id -> popup_id` from the explicit
   `.bindPopup(...)` calls. Pairing goes through the bind calls, never
   file order. Any polygon without exactly one parsed popup, or any popup
   that does not yield `time > 0` and `population >= 0`, aborts with an
   error. CSVs are committed (small); the multi-MB source exports are not
   (Torino's already live in `Maps/`; Milano/Paris are fetched from the
   live site at harvest time and discarded).
2. `Library/equity.py` — used as-is (`equity_summary`, `lorenz_points`
   already exist; no changes).
3. `scripts/build_equity.py` — reads the six CSVs, computes
   `1/travel_time_min` per hex, produces the equity table and one Lorenz
   SVG per map type (three city curves each, plus the equality diagonal),
   and rewrites the block between `<!-- EQUITY:START -->` and
   `<!-- EQUITY:END -->` in `Maps/index.html`. Refuses to run if a CSV is
   missing or empty. Also writes `data/equity_results.json` for the README
   table and for reuse.
4. Tests — `tests/test_harvest.py` runs the parser against a small
   hand-made Folium-style fixture; existing `tests/test_equity.py` stays.
   CI (`pip install numpy pytest`) keeps working: the harvester uses only
   the standard library, so no new CI dependencies.

## Data flow

Map exports (Torino from repo, Milano/Paris downloaded once from the live
site) → `harvest_maps.py` → committed CSVs → `build_equity.py` → updated
`Maps/index.html` + `data/equity_results.json` → git push → VM
`git pull` (the `Maps/` dir is mounted read-only into Caddy, so the
section goes live on pull).

## Honest labeling

- The popup label in the "P2P" exports reads "Accessibility to POI (3h)",
  so the filename-implied semantics may not match the popup content. The
  harvester records the popup label verbatim; the published section
  describes what the label says, not what the filename claims. If P2P and
  P2POI turn out to embed the same quantity, publish one map type, not a
  fake distinction.
- Caveats stated on the page: values recovered from the original research
  exports; one assumed service day per city; hex-grid population; Palma
  deciles not interpolated at boundaries.

## Out of scope

The four archived cities, line-level equity scores (TRB paper method),
POI2P maps, and any pipeline modernization (MongoDB/OSRM replacement).
