"""Equity metrics over population-weighted accessibility.

Implements the equity-assessment methodology of my first-author papers:

- Badeanlou, Araldo, Diana (2022) "Assessing Transportation Accessibility
  Equity via Open Data", hEART 2022 — https://arxiv.org/abs/2206.09037
- Badeanlou, Araldo, Diana, Gauthier (2023) "Equity Scores for Public
  Transit Lines from Open Data and Accessibility Measures", TRB 2023 —
  https://arxiv.org/abs/2210.00128

Accessibility is distributed over people, not places: every metric here
weights each zone's accessibility by its population, then asks how evenly
that accessibility is spread (Lorenz curve) and summarises the inequality
in one number (Gini index; 0 = perfectly equal, 1 = maximally unequal).

Pure NumPy — no database or routing dependencies, so this module is unit
tested standalone.
"""
from __future__ import annotations

import numpy as np


def _validate(values, weights):
    v = np.asarray(values, dtype=float)
    if weights is None:
        w = np.ones_like(v)
    else:
        w = np.asarray(weights, dtype=float)
    if v.shape != w.shape or v.ndim != 1:
        raise ValueError("values and weights must be 1-D arrays of equal length")
    if len(v) == 0:
        raise ValueError("empty input")
    if (v < 0).any() or (w < 0).any():
        raise ValueError("values and weights must be non-negative")
    if w.sum() == 0:
        raise ValueError("total weight is zero")
    return v, w


def gini(values, weights=None) -> float:
    """Population-weighted Gini index of an accessibility distribution."""
    v, w = _validate(values, weights)
    order = np.argsort(v)
    v, w = v[order], w[order]
    cumw = np.cumsum(w)
    total_w = cumw[-1]
    total_v = float((v * w).sum())
    if total_v == 0:
        return 0.0
    # Weighted Gini via the covariance-free rank formulation:
    # G = sum_i w_i v_i (cumw_i - w_i/2) normalised to [0, 1].
    mean_rank = (cumw - w / 2.0) / total_w
    g = 2.0 * float((w * v * mean_rank).sum()) / total_v - 1.0
    # Guard tiny negative values from floating error on uniform inputs.
    return max(0.0, g)


def lorenz_points(values, weights=None) -> np.ndarray:
    """Lorenz curve as an (n+1, 2) array of
    (cumulative population share, cumulative accessibility share),
    starting at (0, 0) and ending at (1, 1)."""
    v, w = _validate(values, weights)
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum_pop = np.concatenate([[0.0], np.cumsum(w)]) / w.sum()
    va = v * w
    total_v = va.sum()
    if total_v == 0:
        cum_acc = np.concatenate([[0.0], np.cumsum(np.zeros_like(va))])
    else:
        cum_acc = np.concatenate([[0.0], np.cumsum(va)]) / total_v
    return np.column_stack([cum_pop, cum_acc])


def equity_summary(accessibility, population) -> dict:
    """Headline equity numbers for a zonal accessibility distribution."""
    v, w = _validate(accessibility, population)
    g = gini(v, w)
    order = np.argsort(v)
    cumw = np.cumsum(w[order]) / w.sum()
    # Share of total accessibility held by the least-accessible half of people
    half = np.searchsorted(cumw, 0.5, side="right")
    va = (v[order] * w[order])
    bottom_half_share = float(va[: half + 1].sum() / va.sum()) if va.sum() else 0.0
    return {
        "gini": round(g, 4),
        "theil": round(theil(v, w), 4),
        "atkinson_e05": round(atkinson(v, w, epsilon=0.5), 4),
        "palma_ratio": round(palma_ratio(v, w), 4),
        "population": float(w.sum()),
        "mean_accessibility_per_capita": round(float((v * w).sum() / w.sum()), 4),
        "bottom_half_accessibility_share": round(bottom_half_share, 4),
    }


def theil(values, weights=None) -> float:
    """Population-weighted Theil T index.

    0 = perfect equality; unbounded above. Decomposable: the Theil index of
    a city equals within-district plus between-district inequality, which
    makes it the tool of choice for asking WHERE inequality lives.
    Zero-accessibility observations contribute 0 (the x·ln x limit).
    """
    v, w = _validate(values, weights)
    mu = float((v * w).sum() / w.sum())
    if mu == 0:
        return 0.0
    share = w / w.sum()
    r = v / mu
    pos = r > 0
    return float((share[pos] * r[pos] * np.log(r[pos])).sum())


def atkinson(values, weights=None, epsilon: float = 0.5) -> float:
    """Population-weighted Atkinson index with inequality aversion epsilon.

    0 = perfect equality, 1 = maximal inequality. Larger epsilon weights
    the low-accessibility end more heavily; with epsilon >= 1 any person
    with zero accessibility drives the index to 1 (by design).
    """
    if epsilon <= 0:
        raise ValueError("epsilon must be > 0")
    v, w = _validate(values, weights)
    mu = float((v * w).sum() / w.sum())
    if mu == 0:
        return 0.0
    share = w / w.sum()
    if epsilon == 1.0:
        if (v == 0).any():
            return 1.0
        geo = float(np.exp((share * np.log(v / mu)).sum()))
        return 1.0 - geo
    ede = float((share * (v / mu) ** (1.0 - epsilon)).sum()) ** (1.0 / (1.0 - epsilon))
    return 1.0 - ede


def palma_ratio(values, weights=None) -> float:
    """Palma ratio: accessibility share of the best-served 10% of people
    divided by the share of the worst-served 40%.

    1 would mean the top decile holds exactly 2.5x ... — for reference,
    perfect equality gives 0.25 (10% of people hold 10%, 40% hold 40%).
    People are assigned to deciles by cumulative population weight ordered
    by accessibility; boundary cells are not interpolated.
    """
    v, w = _validate(values, weights)
    order = np.argsort(v)
    v, w = v[order], w[order]
    cum = np.cumsum(w) / w.sum()
    va = v * w
    total = va.sum()
    if total == 0:
        return 0.0
    bottom = float(va[cum <= 0.4].sum() / total)
    top = float(va[cum > 0.9].sum() / total)
    if bottom == 0:
        return float("inf")
    return top / bottom
