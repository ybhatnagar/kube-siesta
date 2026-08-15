"""Stage 4 — jump % and usage-ratio math."""
import numpy as np

from engine.analysis_core.filter import baseline_ratio, peak_load_jump_pct, usage_ratio


def test_peak_load_jump_pct():
    vals = np.array([1.0, 1.0, 4.0, 1.0], dtype=float)  # active = the 4.0
    active = np.array([False, False, True, False])
    # (4 - 1) / 1 * 100 = 300
    assert peak_load_jump_pct(vals, active) == 300.0


def test_usage_ratio():
    active = np.array([True, True, False, False, False, False])  # 2 active / 4 idle
    assert usage_ratio(active) == 0.5


def test_baseline_ratio():
    vals = np.array([1.0, 1.0, 5.0], dtype=float)
    active = np.array([False, False, True])
    # mean_idle 1.0 / mean_active 5.0 = 0.2
    assert abs(baseline_ratio(vals, active) - 0.2) < 1e-9
