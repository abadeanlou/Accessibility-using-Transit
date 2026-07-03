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
accessibility maps of Torino produced by this pipeline.

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
curves and Gini indices - the same lens the papers use to compare how fairly
transit serves a city. Pure NumPy, unit-tested standalone:

```bash
pip install numpy pytest
pytest tests -v
```

## Requirements (full pipeline)

Python 3.8+, MongoDB, OSRM (see `osrm/` for the Docker setup), plus
`pip install -r requirements.txt`. The full pipeline needs a running
MongoDB and OSRM instance; the equity module and its tests do not.
