# Accessibility-using-Transit

[![tests](https://github.com/abadeanlou/Accessibility-using-Transit/actions/workflows/ci.yml/badge.svg)](https://github.com/abadeanlou/Accessibility-using-Transit/actions/workflows/ci.yml)

Compute public-transport **accessibility** and **equity** for any city from
open GTFS data - a modern, self-contained successor to my M.Sc. thesis work
([public-transport-analysis](https://github.com/abadeanlou/public-transport-analysis),
built on the POLITO/CityChrone codebase), implementing the methodology of my
first-author papers:

- *Assessing Transportation Accessibility Equity via Open Data* - hEART 2022
  ([arXiv:2206.09037](https://arxiv.org/abs/2206.09037))
- *Equity Scores for Public Transit Lines from Open Data and Accessibility
  Measures* - TRB 2023 ([arXiv:2210.00128](https://arxiv.org/abs/2210.00128))

**Live demo: <https://abadeanlou.com/accessibility/>** - interactive
accessibility maps for Torino, Milano, and Paris produced by this
pipeline. The Torino maps live in this repository; the other cities'
exports are large and are hosted server-side.

## Pipeline

```
GTFS zip --> MongoDB (stops, trips, calendars)
         --> stop-to-stop edge list for the busiest service day
         --> hexagonal grid over the area of interest
         --> travel times (transit graph + OSRM walking legs)
         --> accessibility per hex cell:  P2P / P2POI / POI2P
         --> equity metrics (Library/equity.py): population-weighted
             Lorenz curves + Gini indices
         --> interactive Folium maps (Maps/)
```

- `Accessibility_Calculation.ipynb` - end-to-end driver notebook (Torino
  example; swap the GTFS zip and boundary to run any city).
- `Library/` - the actual implementation (~3,700 lines): GTFS processing,
  grid construction, routing, accessibility kernels (Numba-optimised), and
  the equity module.
- `Maps/` - self-contained interactive outputs.

## Equity metrics

Accessibility is distributed over *people*, not places. `Library/equity.py`
weights each cell's accessibility by its population and computes Lorenz
curves plus the standard inequality-index family - **Gini**, **Theil**
(decomposable by district), **Atkinson** (explicit inequality-aversion
parameter), and the **Palma ratio** (top-10% vs bottom-40% share) - the
same lens the papers use to compare how fairly
transit serves a city. Pure NumPy, unit-tested standalone:

```bash
pip install numpy pytest
pytest tests -v
```

## Equity results (published on the live demo)

Two population-weighted views, Lorenz curves and caveats on the
[demo page](https://abadeanlou.com/accessibility/#equity), full numbers
in `data/equity_results.json`:

- **Whole-city reach (P2P)**: per-hex average travel time recovered out
  of the map exports (`scripts/harvest_maps.py`, 8:00 layer),
  accessibility = 1 / travel time.
- **Essential services**: how many schools, universities, healthcare
  places, supermarkets and markets (OpenStreetMap) each hex reaches by
  transit within 60 minutes at 8:00 - a cumulative-opportunities
  measure computed by `scripts/reachable_pois.py` from current GTFS
  feeds (busiest weekday, 15-min walk to stops, 5-min transfers,
  straight-line walking x1.3 detour). A self-contained successor to the
  original MongoDB + OSRM pipeline: the recomputed Milano surface
  matches the original research export with Spearman rho = 0.81
  (`scripts/validate_milano_counts.py`).

| City | View | Gini | Theil | Atkinson (e=0.5) | Palma |
|---|---|---|---|---|---|
| Torino | P2P - whole-city reach | 0.087 | 0.012 | 0.006 | 0.36 |
| Milano | P2P - whole-city reach | 0.107 | 0.018 | 0.009 | 0.40 |
| Paris | P2P - whole-city reach | 0.081 | 0.010 | 0.005 | 0.36 |
| Torino | Essential services in 60 min | 0.259 | 0.153 | 0.097 | 0.78 |
| Milano | Essential services in 60 min | 0.287 | 0.200 | 0.134 | 0.90 |
| Paris | Essential services in 60 min | 0.234 | 0.118 | 0.074 | 0.67 |

The headline finding: average reach to the *whole city* is spread
almost evenly everywhere (Gini < 0.11, expected - the average is
dominated by geography every resident shares), but access to essential
services is far less equal, and the ranking is consistent on every
index: Milano is the most unequal of the three, Paris the most equal.

Regenerate:

```bash
python scripts/harvest_maps.py Maps/accessibility_map_<City>_P2P.html data/hexes_<City>_P2P.csv
python scripts/fetch_pois_osm.py data/hexes_<City>_P2P.csv data/pois_<City>.csv
python scripts/reachable_pois.py <gtfs.zip> data/hexes_<City>_P2P.csv data/pois_<City>.csv data/numpoi_<City>.csv
python scripts/build_equity.py    # rewrites the equity section of Maps/index.html
python scripts/build_hex_maps.py  # compact interactive maps (P2P, P2POI, essential services)
```

## Requirements (full pipeline)

Python 3.8+, MongoDB, OSRM (see `osrm/` for the Docker setup), plus
`pip install -r requirements.txt`. The full pipeline needs a running
MongoDB and OSRM instance; the equity module and its tests do not.
