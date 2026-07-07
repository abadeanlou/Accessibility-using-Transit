"""Smoke tests for scripts/build_equity.py on tiny synthetic CSVs."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import build_equity  # noqa: E402


def _write_csvs(data_dir: Path) -> None:
    data_dir.mkdir()
    for city in build_equity.CITIES:
        rows = [
            "lat,lon,hour,travel_time_min,population",
            "45.0,7.0,3,99.0,50",          # other hour: must be ignored
            "45.0,7.0,8,10.0,100",
            "45.1,7.1,8,0.0,10",           # zero time: floored to 1 min
            "45.2,7.2,8,40.0,200",
            "45.3,7.3,8,,30",              # not reachable: accessibility 0
        ]
        (data_dir / f"hexes_{city}_P2P.csv").write_text(
            "\n".join(rows) + "\n", encoding="utf-8"
        )
        (data_dir / f"hexes_{city}_P2POI.csv").write_text(
            "\n".join(rows) + "\n", encoding="utf-8"
        )
        counts = [
            "lat,lon,population,reachable_pois",
            "45.0,7.0,100,120",
            "45.1,7.1,10,0",               # zero-count hex stays in
            "45.2,7.2,200,45",
        ]
        (data_dir / f"numpoi_{city}.csv").write_text(
            "\n".join(counts) + "\n", encoding="utf-8"
        )


def test_compute_all_and_render(tmp_path):
    _write_csvs(tmp_path / "data")
    results = build_equity.compute_all(tmp_path / "data")

    assert len(results) == 9
    r = results["Torino_P2P"]
    assert r["n_cells"] == 4  # hour-3 row excluded
    assert 0 < r["gini"] < 1
    assert r["lorenz"][0] == [0.0, 0.0]
    assert r["lorenz"][-1] == [1.0, 1.0]

    a = results["Torino_AMENITIES"]
    assert a["n_cells"] == 3
    assert 0 < a["gini"] < 1
    assert results["Torino_P2POI"]["n_cells"] == 4

    section = build_equity.render_section(results)
    assert '<h2 id="equity">' in section
    assert section.count("<tr>") == 10  # header + 9 rows
    assert section.count("<svg") == 3
    assert section.count("<path") == 9  # 3 city curves per chart


def test_update_index_replaces_only_marker_block(tmp_path):
    _write_csvs(tmp_path / "data")
    results = build_equity.compute_all(tmp_path / "data")
    page = tmp_path / "index.html"
    page.write_text(
        "<body>KEEP-TOP\n<!-- EQUITY:START -->STALE-BLOCK<!-- EQUITY:END -->\n"
        "KEEP-BOTTOM</body>",
        encoding="utf-8",
    )
    build_equity.update_index(page, build_equity.render_section(results))
    html = page.read_text(encoding="utf-8")
    assert "KEEP-TOP" in html and "KEEP-BOTTOM" in html
    assert "STALE-BLOCK" not in html
    assert '<h2 id="equity">' in html
    # idempotent: running again still yields exactly one section
    build_equity.update_index(page, build_equity.render_section(results))
    assert page.read_text(encoding="utf-8").count('<h2 id="equity">') == 1


def test_load_hexes_rejects_missing_hour(tmp_path):
    p = tmp_path / "h.csv"
    p.write_text(
        "lat,lon,hour,travel_time_min,population\n45.0,7.0,3,10.0,5\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="no rows for hour"):
        build_equity.load_hexes(p)
