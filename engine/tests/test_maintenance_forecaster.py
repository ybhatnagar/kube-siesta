"""SeasonalNaive — repeat the last period forward; aperiodic → all-True."""
from __future__ import annotations

import numpy as np
import pandas as pd

from engine.recommenders.maintenance.forecaster import SeasonalNaive


def _mask(hours, pattern, start="2026-08-01T00:00:00Z"):
    """Build a bool Series over `hours` samples spaced 1h; `pattern` tiles across."""
    idx = pd.date_range(start=start, periods=hours, freq="1h", tz="UTC")
    vals = np.array([pattern[i % len(pattern)] for i in range(hours)], dtype=bool)
    return pd.Series(vals, index=idx)


def test_aperiodic_projects_all_true():
    fc = SeasonalNaive()
    fc.fit(None, None)
    future = pd.date_range("2026-08-05T00:00:00Z", periods=12, freq="1h", tz="UTC")
    projection = fc.project(future)
    assert projection.all()
    assert projection.dtype == bool


def test_period_repeats_forward_exactly():
    # 8h period: active for hours 0-1 of each period.
    hist = _mask(48, [True, True, False, False, False, False, False, False])
    fc = SeasonalNaive()
    fc.fit(hist, period_hours=8.0)

    # Two full future periods immediately after the last observed hour.
    future_start = hist.index[-1] + pd.Timedelta(hours=1)
    future = pd.date_range(start=future_start, periods=16, freq="1h", tz="UTC")
    proj = fc.project(future)
    # Anchor: template starts at the beginning of the last period; that period
    # begins at hist.index[-8]. `future_start` is 8 hours later → phase 0 again.
    expected = [True, True, False, False, False, False, False, False] * 2
    assert proj.tolist() == expected


def test_short_history_still_yields_a_forecast():
    # 2 hours of history at [True, False], 2h period → template starts at
    # hour 0 with True. The next 6 future samples continue the pattern.
    hist = _mask(2, [True, False])
    fc = SeasonalNaive()
    fc.fit(hist, period_hours=2.0)
    future = pd.date_range(start=hist.index[-1] + pd.Timedelta(hours=1),
                           periods=6, freq="1h", tz="UTC")
    proj = fc.project(future)
    assert proj.tolist() == [True, False, True, False, True, False]
