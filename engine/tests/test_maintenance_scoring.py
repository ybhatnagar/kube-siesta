"""Instant scoring + sliding-window minimization."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from engine.recommenders.maintenance.scoring import min_window, score_instants


def _bool_series(values, start="2026-08-01T00:00:00Z"):
    idx = pd.date_range(start=start, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(np.array(values, dtype=bool), index=idx)


def _int_series(values, start="2026-08-01T00:00:00Z"):
    idx = pd.date_range(start=start, periods=len(values), freq="1h", tz="UTC")
    return pd.Series(np.array(values, dtype="int64"), index=idx)


def test_score_instants_sums_active_flags():
    a = _bool_series([1, 1, 0, 0])
    b = _bool_series([1, 0, 1, 0])
    c = _bool_series([0, 0, 1, 1])
    scores = score_instants([a, b, c])
    assert scores.to_numpy().tolist() == [2, 1, 2, 1]


def test_score_instants_raises_on_empty():
    with pytest.raises(ValueError):
        score_instants([])


def test_min_window_earliest_min_wins():
    # Two windows tie at min sum = 2 (pair values [1,1]); pick the earlier one.
    scores = _int_series([2, 2, 1, 1, 2, 1, 1, 2])
    pick = min_window(scores, window_samples=2)
    assert pick.start_idx == 2
    assert pick.end_idx == 3
    assert pick.score_sum == 2.0
    assert pick.max_score == 1


def test_min_window_full_horizon_ok():
    scores = pd.Series([0, 0, 0, 0], index=pd.date_range("2026-08-01", periods=4, freq="1h", tz="UTC"))
    pick = min_window(scores, window_samples=4)
    assert pick.start_idx == 0
    assert pick.end_idx == 3
    assert pick.score_sum == 0.0


def test_min_window_rejects_bad_inputs():
    scores = _bool_series([0, 1]).astype(int)
    with pytest.raises(ValueError):
        min_window(scores, window_samples=0)
    with pytest.raises(ValueError):
        min_window(scores, window_samples=5)


def test_min_window_finds_true_minimum_over_ties():
    # Ensure ties on non-min scores are irrelevant.
    scores = pd.Series([5, 5, 0, 0, 5, 5, 0, 0], index=pd.date_range("2026-08-01", periods=8, freq="1h", tz="UTC"))
    pick = min_window(scores, window_samples=2)
    assert pick.start_idx == 2
    assert pick.score_sum == 0.0
