"""Count POIs reachable by transit within 60 minutes from every hex.

A self-contained successor to the original MongoDB + OSRM computation
(Library.process_reachable_poi_counts), runnable from a GTFS zip and two
CSVs. Parameters mirror the original notebook run:

- depart around 08:00 (service window 07:30-09:30)
- max travel time 60 min, walking speed 1.4 m/s
- max 15-min walk to/from stops and hex->POI direct, 5-min transfers
- straight-line walking distances scaled by a 1.3 detour factor
  (the original routed real streets via OSRM - this is the approximation)

Graph: hex -> stop (walk) -> per-line platform (board, wait = headway/2)
-> platform (ride, median segment time) -> stop (alight) -> POI (walk),
plus stop<->stop transfer walks and hex -> POI direct walks. One Dijkstra
pass (scipy, cost-limited) from every hex, then count POI nodes within
the budget.

Usage:
    python scripts/reachable_pois.py GTFS.zip data/hexes_<City>_P2P.csv \
        data/pois_<City>.csv data/numpoi_<City>.csv
"""
from __future__ import annotations

import csv
import io
import sys
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np

WALK_SPEED = 1.4          # m/s (original notebook)
DETOUR = 1.3              # straight-line -> street-network factor
MAX_WALK_ACCESS_S = 15 * 60
MAX_WALK_TRANSFER_S = 5 * 60
MAX_TRAVEL_TIME_S = 60 * 60
WINDOW = (7 * 3600 + 1800, 9 * 3600 + 1800)   # 07:30-09:30
_CHUNK = 250              # dijkstra sources per chunk (memory bound)

_ACCESS_RADIUS_M = MAX_WALK_ACCESS_S * WALK_SPEED / DETOUR
_TRANSFER_RADIUS_M = MAX_WALK_TRANSFER_S * WALK_SPEED / DETOUR


def _read_csv(z: zipfile.ZipFile, name: str, usecols):
    import pandas as pd

    with z.open(name) as f:
        return pd.read_csv(
            io.TextIOWrapper(f, encoding="utf-8-sig"),
            usecols=lambda c: c in usecols, dtype=str,
        )


def _seconds(series):
    import pandas as pd

    parts = series.str.split(":", expand=True).astype(float)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def busiest_weekday(z: zipfile.ZipFile) -> str:
    """Pick the Mon-Fri date with the most active trips over the feed's
    next two weeks (mirrors the original 'busiest network day' default)."""
    import pandas as pd

    trips = _read_csv(z, "trips.txt", {"service_id", "trip_id"})
    trips_per_service = trips.groupby("service_id").size()

    cal = None
    if "calendar.txt" in z.namelist():
        cal = _read_csv(z, "calendar.txt",
                        {"service_id", "monday", "tuesday", "wednesday",
                         "thursday", "friday", "saturday", "sunday",
                         "start_date", "end_date"})
        if cal.empty:
            cal = None
    cdates = None
    if "calendar_dates.txt" in z.namelist():
        cdates = _read_csv(z, "calendar_dates.txt",
                           {"service_id", "date", "exception_type"})
        if cdates.empty:
            cdates = None
    if cal is None and cdates is None:
        raise ValueError("feed has neither calendar.txt nor calendar_dates.txt data")

    start = date.today()
    weekday_cols = ["monday", "tuesday", "wednesday", "thursday",
                    "friday", "saturday", "sunday"]
    best_date, best_trips = None, -1
    for offset in range(14):
        d = start + timedelta(days=offset)
        if d.weekday() >= 5:
            continue
        ds = d.strftime("%Y%m%d")
        active = set()
        if cal is not None:
            col = weekday_cols[d.weekday()]
            m = ((cal[col] == "1")
                 & (cal["start_date"] <= ds) & (cal["end_date"] >= ds))
            active |= set(cal.loc[m, "service_id"])
        if cdates is not None:
            day = cdates[cdates["date"] == ds]
            active |= set(day.loc[day["exception_type"] == "1", "service_id"])
            active -= set(day.loc[day["exception_type"] == "2", "service_id"])
        n = int(trips_per_service.reindex(sorted(active)).fillna(0).sum())
        if n > best_trips:
            best_date, best_trips = ds, n
    if not best_trips or best_trips <= 0:
        raise ValueError("no weekday with active service found in the next 14 days")
    print(f"service day {best_date}: {best_trips} trips")
    return best_date


def _active_services(z: zipfile.ZipFile, ds: str) -> set:
    import pandas as pd

    active = set()
    if "calendar.txt" in z.namelist():
        cal = _read_csv(z, "calendar.txt",
                        {"service_id", "monday", "tuesday", "wednesday",
                         "thursday", "friday", "saturday", "sunday",
                         "start_date", "end_date"})
        if not cal.empty:
            col = ["monday", "tuesday", "wednesday", "thursday", "friday",
                   "saturday", "sunday"][datetime.strptime(ds, "%Y%m%d").weekday()]
            m = ((cal[col] == "1")
                 & (cal["start_date"] <= ds) & (cal["end_date"] >= ds))
            active |= set(cal.loc[m, "service_id"])
    if "calendar_dates.txt" in z.namelist():
        cdates = _read_csv(z, "calendar_dates.txt",
                           {"service_id", "date", "exception_type"})
        if not cdates.empty:
            day = cdates[cdates["date"] == ds]
            active |= set(day.loc[day["exception_type"] == "1", "service_id"])
            active -= set(day.loc[day["exception_type"] == "2", "service_id"])
    return active


def load_network(gtfs_zip: Path):
    """Return (stops_df, lines) for the busiest weekday's 07:30-09:30 window.

    lines: dict line_key -> {"wait_s": float,
                             "segments": {(stop_a, stop_b): median_run_s}}
    """
    import pandas as pd

    z = zipfile.ZipFile(gtfs_zip)
    ds = busiest_weekday(z)
    services = _active_services(z, ds)

    trips = _read_csv(z, "trips.txt", {"trip_id", "route_id", "service_id",
                                       "direction_id"})
    trips = trips[trips["service_id"].isin(services)]
    if "direction_id" not in trips.columns:
        trips["direction_id"] = "0"
    trips["direction_id"] = trips["direction_id"].fillna("0")
    trip_line = dict(zip(
        trips["trip_id"], zip(trips["route_id"], trips["direction_id"])
    ))

    chunks = []
    with z.open("stop_times.txt") as f:
        for chunk in pd.read_csv(
            io.TextIOWrapper(f, encoding="utf-8-sig"),
            usecols=["trip_id", "arrival_time", "departure_time",
                     "stop_id", "stop_sequence"],
            dtype=str, chunksize=2_000_000,
        ):
            chunk = chunk[chunk["trip_id"].isin(trip_line)]
            if not chunk.empty:
                chunks.append(chunk)
    st = pd.concat(chunks, ignore_index=True)
    st["dep_s"] = _seconds(st["departure_time"])
    st["arr_s"] = _seconds(st["arrival_time"])
    st["seq"] = st["stop_sequence"].astype(int)

    # keep trips that have at least one departure inside the window
    in_window = st[(st["dep_s"] >= WINDOW[0]) & (st["dep_s"] <= WINDOW[1])]
    keep = set(in_window["trip_id"])
    st = st[st["trip_id"].isin(keep)].sort_values(["trip_id", "seq"])

    same_trip = st["trip_id"] == st["trip_id"].shift(-1)
    runs = pd.DataFrame({
        "trip_id": st["trip_id"][same_trip],
        "a": st["stop_id"][same_trip],
        "b": st["stop_id"].shift(-1)[same_trip],
        "run_s": (st["arr_s"].shift(-1) - st["dep_s"])[same_trip],
    })
    runs = runs[runs["run_s"] >= 0]
    runs["line"] = runs["trip_id"].map(trip_line)

    window_len = WINDOW[1] - WINDOW[0]
    trips_per_line = (
        in_window[["trip_id"]].drop_duplicates()
        .assign(line=lambda d: d["trip_id"].map(trip_line))
        .groupby("line").size()
    )

    lines = {}
    med = runs.groupby(["line", "a", "b"])["run_s"].median()
    for (line, a, b), run in med.items():
        entry = lines.setdefault(
            line,
            {"wait_s": window_len / trips_per_line[line] / 2.0, "segments": {}},
        )
        entry["segments"][(a, b)] = max(float(run), 1.0)

    stops = _read_csv(z, "stops.txt", {"stop_id", "stop_lat", "stop_lon"})
    stops = stops.dropna(subset=["stop_lat", "stop_lon"])
    stops["lat"] = stops["stop_lat"].astype(float)
    stops["lon"] = stops["stop_lon"].astype(float)
    used = {s for line in lines.values() for ab in line["segments"] for s in ab}
    stops = stops[stops["stop_id"].isin(used)][["stop_id", "lat", "lon"]]
    print(f"{len(lines)} lines, {len(stops)} stops in service window")
    return stops.reset_index(drop=True), lines


def _walk_pairs(from_xy, to_xy, radius_m):
    """(i, j, walk_seconds) for pairs within radius (planar approx)."""
    from scipy.spatial import cKDTree

    tree = cKDTree(to_xy)
    out = []
    for i, hits in enumerate(cKDTree(from_xy).query_ball_tree(tree, radius_m)):
        for j in hits:
            d = float(np.hypot(*(from_xy[i] - to_xy[j])))
            out.append((i, j, d * DETOUR / WALK_SPEED))
    return out


def compute_counts(stops, lines, hexes, pois) -> np.ndarray:
    """Reachable-POI count per hex row."""
    from scipy.sparse import csr_matrix
    from scipy.sparse.csgraph import dijkstra

    mean_lat = float(np.mean([h["lat"] for h in hexes]))
    scale = np.array([111320.0, 111320.0 * np.cos(np.radians(mean_lat))])

    def xy(rows):
        return np.array([[r["lat"], r["lon"]] for r in rows]) * scale

    stop_rows = stops.to_dict("records")
    stop_xy, hex_xy, poi_xy = xy(stop_rows), xy(hexes), xy(pois)

    n_stops = len(stop_rows)
    stop_index = {r["stop_id"]: i for i, r in enumerate(stop_rows)}
    platform_index = {}
    for line, data in lines.items():
        for a, b in data["segments"]:
            platform_index.setdefault((line, a), len(platform_index))
            platform_index.setdefault((line, b), len(platform_index))

    def platform(line, stop):
        return n_stops + platform_index[(line, stop)]

    hex0 = n_stops + len(platform_index)
    poi0 = hex0 + len(hexes)
    n_nodes = poi0 + len(pois)

    rows, cols, weights = [], [], []

    def edge(u, v, w):
        rows.append(u); cols.append(v); weights.append(w)

    for line, data in lines.items():
        for (a, b), run in data["segments"].items():
            edge(platform(line, a), platform(line, b), run)
    for (line, stop), _idx in platform_index.items():
        s = stop_index[stop]
        edge(s, platform(line, stop), lines[line]["wait_s"])   # board
        edge(platform(line, stop), s, 0.0)                     # alight

    for i, j, w in _walk_pairs(stop_xy, stop_xy, _TRANSFER_RADIUS_M):
        if i != j:
            edge(i, j, w)
    for i, j, w in _walk_pairs(hex_xy, stop_xy, _ACCESS_RADIUS_M):
        edge(hex0 + i, j, w)
    for i, j, w in _walk_pairs(stop_xy, poi_xy, _ACCESS_RADIUS_M):
        edge(i, poi0 + j, w)
    for i, j, w in _walk_pairs(hex_xy, poi_xy, _ACCESS_RADIUS_M):
        edge(hex0 + i, poi0 + j, w)

    graph = csr_matrix(
        (np.array(weights), (np.array(rows), np.array(cols))),
        shape=(n_nodes, n_nodes),
    )
    print(f"graph: {n_nodes} nodes, {graph.nnz} edges")

    counts = np.zeros(len(hexes), dtype=np.int32)
    for s in range(0, len(hexes), _CHUNK):
        idx = np.arange(hex0 + s, hex0 + min(s + _CHUNK, len(hexes)))
        dist = dijkstra(graph, indices=idx, limit=MAX_TRAVEL_TIME_S)
        counts[s:s + len(idx)] = (
            dist[:, poi0:] <= MAX_TRAVEL_TIME_S
        ).sum(axis=1)
        print(f"  hexes {s + len(idx)}/{len(hexes)}")
    return counts


def _load_rows(path: Path, hour: int | None = None) -> list[dict]:
    rows = []
    with path.open(encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if hour is not None and int(r["hour"]) != hour:
                continue
            rows.append({"lat": float(r["lat"]), "lon": float(r["lon"]),
                         **({"population": float(r["population"])}
                            if "population" in r else {})})
    if not rows:
        raise ValueError(f"{path}: no rows")
    return rows


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(__doc__)
        return 2
    gtfs_zip, hex_csv, poi_csv, out_csv = map(Path, argv[1:])
    stops, lines = load_network(gtfs_zip)
    hexes = _load_rows(hex_csv, hour=8)
    pois = _load_rows(poi_csv)
    counts = compute_counts(stops, lines, hexes, pois)

    with out_csv.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["lat", "lon", "population", "reachable_pois"])
        for h, c in zip(hexes, counts):
            w.writerow([h["lat"], h["lon"], h["population"], int(c)])
    print(f"-> {out_csv}: mean {counts.mean():.1f}, median "
          f"{np.median(counts):.0f}, zero-count hexes {(counts == 0).sum()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
