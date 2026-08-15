"""Stage 3 — active/idle recovers the known active windows."""
import numpy as np

from engine.analysis_core.active_idle import classify, longest_run_len


def test_classify_recovers_spike_windows():
    # period 8, 2h spike each period, baseline 0.2, spike to 2.0
    n = 320
    vals = np.array([2.0 if (i % 8) < 2 else 0.2 for i in range(n)], dtype=float)
    res = classify(vals, period=8, band_pct=0.10)

    # 40 periods -> 40 spike windows, each 2 samples wide.
    assert len(res.windows) == 40
    assert longest_run_len(res.windows) == 2
    # Active exactly on the spike samples.
    assert int(res.active.sum()) == 80
    assert res.eps_max > res.trend_value  # upper band sits above the rolling median
