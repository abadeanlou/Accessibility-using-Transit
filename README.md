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

Computed from the per-hex data recovered out of the map exports
(`scripts/harvest_maps.py`), 8:00 layer, accessibility = 1 / average
travel time (floored at one minute), population-weighted throughout.
Lorenz curves and caveats live on the
[demo page](https://abadeanlou.com/accessibility/#equity); full numbers
in `data/equity_results.json`.

| City | View | Gini | Theil | Atkinson (e=0.5) | Palma |
|---|---|---|---|---|---|
| Torino | P2P - whole-city reach | 0.087 | 0.012 | 0.006 | 0.36 |
| Milano | P2P - whole-city reach | 0.107 | 0.018 | 0.009 | 0.40 |
| Paris | P2P - whole-city reach | 0.081 | 0.010 | 0.005 | 0.36 |
| Torino | P2POI - reach to amenities | 0.344 | 0.214 | 0.097 | 1.44 |
| Milano | P2POI - reach to amenities | 0.414 | 0.275 | 0.138 | 1.78 |
| Paris | P2POI - reach to amenities | 0.234 | 0.088 | 0.042 | 0.77 |

The headline finding: average reach to the *whole city* is spread almost
evenly everywhere (Gini < 0.11), but access to *amenities* is far less
equal - and Milano is the most unequal of the three (the best-served 10%
of residents hold 1.78x the accessibility of the worst-served 40%).

Regenerate after new map exports:

```bash
python scripts/harvest_maps.py Maps/accessibility_map_<City>_<Type>.html data/hexes_<City>_<Type>.csv
python scripts/build_equity.py   # rewrites the equity section of Maps/index.html
```

## Requirements (full pipeline)

Python 3.8+, MongoDB, OSRM (see `osrm/` for the Docker setup), plus
`pip install -r requirements.txt`. The full pipeline needs a running
MongoDB and OSRM instance; the equity module and its tests do not.
