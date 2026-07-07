"""Grid-geometry regression for the essential-services maps: hexagons
must tile, not overlap. Ground truth for base angle and radius comes
from a synthetic tiling that mirrors the real exports' geometry
(compass base ~28.9 deg, R ~250 m at Torino's latitude)."""
import math

import pytest

pytest.importorskip("scipy")

from build_numpoi_maps import grid_geometry  # noqa: E402


def _synthetic_grid(base_deg: float, r_m: float, lat0=45.0, lon0=7.6):
    """Centroids of a hex tiling: neighbours at distance sqrt(3)*R, at
    compass bearings base_deg +/- 30 (mod 60)."""
    scale_lat = 111320.0
    scale_lon = 111320.0 * math.cos(math.radians(lat0))
    spacing = math.sqrt(3.0) * r_m
    rows = [[lat0, lon0, 10, 100]]
    for k in range(6):
        b = math.radians(base_deg + 30.0 + 60.0 * k)
        d_north = spacing * math.cos(b)
        d_east = spacing * math.sin(b)
        rows.append([lat0 + d_north / scale_lat, lon0 + d_east / scale_lon,
                     20, 100])
    return rows


def test_recovers_export_like_geometry():
    radius, base = grid_geometry(_synthetic_grid(28.9, 250.0))
    assert radius == pytest.approx(250.0, abs=2.0)
    assert base == pytest.approx(28.9, abs=1.0)


def test_recovers_zero_base_grid():
    radius, base = grid_geometry(_synthetic_grid(0.0, 250.0))
    assert radius == pytest.approx(250.0, abs=2.0)
    # 0 and 60 are the same orientation for a hexagon
    assert min(base % 60.0, 60.0 - base % 60.0) == pytest.approx(0.0, abs=1.0)


def test_single_hex_falls_back():
    radius, base = grid_geometry([[45.0, 7.6, 10, 100]])
    assert radius == 250.0
