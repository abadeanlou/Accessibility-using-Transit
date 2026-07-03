import numpy as np
import pytest

from Library.equity import atkinson, equity_summary, gini, lorenz_points, palma_ratio, theil


def test_equal_distribution_has_zero_gini():
    assert gini([5, 5, 5, 5]) == pytest.approx(0.0, abs=1e-9)


def test_hand_computed_two_values():
    # For [1, 3]: G = sum|xi-xj| / (2 n^2 mu) = 4 / 16 = 0.25
    assert gini([1, 3]) == pytest.approx(0.25)


def test_extreme_concentration_approaches_one():
    v = np.zeros(1000); v[-1] = 1.0
    assert gini(v) > 0.99


def test_population_weights_matter():
    # Poor zone holds most people -> more unequal than unweighted view
    unweighted = gini([1, 10])
    weighted = gini([1, 10], weights=[90, 10])
    assert weighted > unweighted


def test_lorenz_endpoints_and_monotonicity():
    pts = lorenz_points([2, 1, 4], weights=[1, 2, 1])
    assert pts[0].tolist() == [0.0, 0.0]
    assert pts[-1] == pytest.approx([1.0, 1.0])
    assert (np.diff(pts[:, 0]) >= 0).all() and (np.diff(pts[:, 1]) >= -1e-12).all()
    # Lorenz curve lies on or below the equality diagonal
    assert (pts[:, 1] <= pts[:, 0] + 1e-12).all()


def test_summary_shape():
    s = equity_summary([1, 2, 3], [100, 100, 100])
    assert set(s) == {"gini", "theil", "atkinson_e05", "palma_ratio",
                      "population", "mean_accessibility_per_capita",
                      "bottom_half_accessibility_share"}
    assert 0 <= s["gini"] <= 1


def test_rejects_bad_input():
    with pytest.raises(ValueError):
        gini([])
    with pytest.raises(ValueError):
        gini([1, 2], weights=[1])
    with pytest.raises(ValueError):
        gini([-1, 2])


def test_theil_hand_computed_two_values():
    # [1, 3]: mu=2 -> T = 0.5*0.5*ln(0.5) + 0.5*1.5*ln(1.5) = 0.13081...
    assert theil([1, 3]) == pytest.approx(0.13081, abs=1e-4)


def test_theil_zero_on_equality():
    assert theil([4, 4, 4]) == pytest.approx(0.0, abs=1e-12)


def test_atkinson_hand_computed():
    # [1, 3], eps=0.5: EDE = (0.5*sqrt(0.5)+0.5*sqrt(1.5))^2 = 0.93301 -> A = 0.06699
    assert atkinson([1, 3], epsilon=0.5) == pytest.approx(0.06699, abs=1e-4)


def test_atkinson_eps1_zero_value_gives_one():
    assert atkinson([0, 5], epsilon=1.0) == 1.0


def test_palma_equality_is_a_quarter():
    v = np.ones(1000)
    assert palma_ratio(v) == pytest.approx(0.25, abs=0.02)


def test_palma_grows_with_concentration():
    modest = palma_ratio([1, 1, 1, 1, 10])
    extreme = palma_ratio([1, 1, 1, 1, 100])
    assert extreme > modest


def test_summary_includes_all_metrics():
    s = equity_summary([1, 2, 3], [100, 100, 100])
    for k in ("gini", "theil", "atkinson_e05", "palma_ratio"):
        assert k in s
