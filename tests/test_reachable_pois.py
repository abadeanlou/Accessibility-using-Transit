"""End-to-end test of the reachable-POIs engine on a tiny synthetic GTFS:
one route A->B every 10 minutes. A hex near stop A must reach the POI near
stop B (via transit) and the POI next door (direct walk), but not the one
across the region."""
import csv
import zipfile
from datetime import date, timedelta

import pytest

pytest.importorskip("scipy")
pytest.importorskip("pandas")

from reachable_pois import compute_counts, load_network, _load_rows  # noqa: E402

HEX = (45.000, 7.000)
STOP_A = (45.001, 7.000)      # ~110 m from the hex
STOP_B = (45.020, 7.000)      # ~2.2 km away: beyond direct walk
POI_TRANSIT = (45.021, 7.000)  # ~110 m from stop B
POI_WALK = (45.0005, 7.0005)   # next door to the hex
POI_FAR = (45.500, 7.500)


def _make_gtfs(path):
    monday = date.today() + timedelta(days=(7 - date.today().weekday()) % 7)
    start = monday.strftime("%Y%m%d")
    end = (monday + timedelta(days=60)).strftime("%Y%m%d")
    stop_times = ["trip_id,arrival_time,departure_time,stop_id,stop_sequence"]
    trips = ["route_id,service_id,trip_id,direction_id"]
    for i in range(12):
        dep = 7 * 3600 + 30 * 60 + i * 600
        arr = dep + 300
        t = f"t{i}"
        trips.append(f"R,WK,{t},0")
        stop_times.append(f"{t},{_hms(dep)},{_hms(dep)},A,1")
        stop_times.append(f"{t},{_hms(arr)},{_hms(arr)},B,2")
    files = {
        "agency.txt": "agency_id,agency_name,agency_url,agency_timezone\n"
                      "x,X,https://x.example,Europe/Rome",
        "routes.txt": "route_id,agency_id,route_short_name,route_type\nR,x,1,3",
        "calendar.txt": "service_id,monday,tuesday,wednesday,thursday,friday,"
                        "saturday,sunday,start_date,end_date\n"
                        f"WK,1,1,1,1,1,0,0,{start},{end}",
        "trips.txt": "\n".join(trips),
        "stop_times.txt": "\n".join(stop_times),
        "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\n"
                     f"A,A,{STOP_A[0]},{STOP_A[1]}\n"
                     f"B,B,{STOP_B[0]},{STOP_B[1]}",
    }
    with zipfile.ZipFile(path, "w") as z:
        for name, content in files.items():
            z.writestr(name, content + "\n")
    return path


def _hms(s):
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def _write_csv(path, header, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def test_transit_and_walk_reachability(tmp_path):
    gtfs = _make_gtfs(tmp_path / "gtfs.zip")
    hex_csv = tmp_path / "hexes.csv"
    poi_csv = tmp_path / "pois.csv"
    _write_csv(hex_csv, ["lat", "lon", "hour", "travel_time_min", "population"],
               [[*HEX, 8, 10.0, 100]])
    _write_csv(poi_csv, ["lat", "lon", "kind"],
               [[*POI_TRANSIT, "school"], [*POI_WALK, "pharmacy"],
                [*POI_FAR, "hospital"]])

    stops, lines = load_network(gtfs)
    assert len(stops) == 2
    assert len(lines) == 1
    line = next(iter(lines.values()))
    assert line["wait_s"] == pytest.approx(300.0)   # 10-min headway / 2
    assert line["segments"][("A", "B")] == pytest.approx(300.0)

    hexes = _load_rows(hex_csv, hour=8)
    pois = _load_rows(poi_csv)
    counts = compute_counts(stops, lines, hexes, pois)
    assert list(counts) == [2]   # transit POI + next-door POI, not the far one
