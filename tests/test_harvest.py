"""Parser tests for scripts/harvest_maps.py against a hand-made
Folium-style fixture that mirrors the real export structure: polygon
literals, popup html divs, setContent and bindPopup wiring, plus a POI
marker popup that must be ignored."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from harvest_maps import harvest  # noqa: E402


def _fixture(bind_extra: str = "", drop_bind: bool = False) -> str:
    bind1 = "" if drop_bind else "polygon_aa1.bindPopup(popup_e1);"
    return f"""
    var polygon_aa1 = L.polygon(
        [[45.0, 7.0], [45.0, 7.2], [45.2, 7.1]],
        {{"fill": true}}
    ).addTo(fg);
    var popup_e1 = L.popup({{"maxWidth": "100%"}});
    var html_a1 = $(`<div id="html_a1" style="width: 100.0%;"> Accessibility to POI (3h): 119.43 min <br> Population: 166 </div>`)[0];
    popup_e1.setContent(html_a1);
    {bind1}

    var polygon_bb2 = L.polygon(
        [[[46.0, 8.0], [46.0, 8.2], [46.2, 8.1]]],
        {{"fill": true}}
    ).addTo(fg);
    var popup_e2 = L.popup({{}});
    var html_a2 = $(`<div id="html_a2" style="width: 100.0%;"> Accessibility to POI (8h): Not reachable <br> Population: 42 </div>`)[0];
    popup_e2.setContent(html_a2);
    polygon_bb2.bindPopup(popup_e2);

    var marker_cc3 = L.marker([45.5, 7.5]);
    var popup_e3 = L.popup({{}});
    var html_a3 = $(`<div id="html_a3" style="width: 100.0%;">Point of Interest</div>`)[0];
    popup_e3.setContent(html_a3);
    marker_cc3.bindPopup(popup_e3);
    {bind_extra}
    """


def test_harvest_pairs_polygons_with_popups():
    rows, meta = harvest(_fixture())
    assert meta["n_cells"] == 2
    assert meta["n_not_reachable"] == 1
    assert meta["popup_labels"] == [
        "Accessibility to POI (3h)", "Accessibility to POI (8h)",
    ]
    assert meta["hours"] == [3, 8]
    assert meta["n_cells_per_hour"] == {3: 1, 8: 1}
    assert meta["total_population_per_hour"] == {3: 166, 8: 42}

    reachable = next(r for r in rows if r["travel_time_min"] != "")
    assert reachable["travel_time_min"] == pytest.approx(119.43)
    assert reachable["hour"] == 3
    assert reachable["population"] == 166
    # centroid of the first triangle
    assert reachable["lat"] == pytest.approx(45.0667, abs=1e-3)
    assert reachable["lon"] == pytest.approx(7.1, abs=1e-3)

    not_reachable = next(r for r in rows if r["travel_time_min"] == "")
    assert not_reachable["population"] == 42
    assert not_reachable["hour"] == 8
    # nested-ring polygon centroid still parsed
    assert not_reachable["lat"] == pytest.approx(46.0667, abs=1e-3)


def test_harvest_rejects_unbound_polygon():
    with pytest.raises(ValueError, match="pairing mismatch"):
        harvest(_fixture(drop_bind=True))


def test_harvest_rejects_double_bind():
    with pytest.raises(ValueError, match="more than one popup"):
        harvest(_fixture(bind_extra="polygon_aa1.bindPopup(popup_e2);"))


def test_harvest_rejects_non_hex_popup_on_polygon():
    bad = """
    var polygon_dd4 = L.polygon([[45.0, 7.0], [45.1, 7.1], [45.2, 7.0]], {});
    var popup_e4 = L.popup({});
    var html_a4 = $(`<div id="html_a4" style="width: 100.0%;">Point of Interest</div>`)[0];
    popup_e4.setContent(html_a4);
    polygon_dd4.bindPopup(popup_e4);
    """
    with pytest.raises(ValueError, match="did not parse"):
        harvest(bad)


def test_harvest_rejects_empty_input():
    with pytest.raises(ValueError, match="no hex polygons"):
        harvest("<html><body>nothing here</body></html>")
