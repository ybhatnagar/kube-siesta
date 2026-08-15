"""Stage 2 — STL seasonal strength: high for periodic, low for flat."""
import numpy as np

from engine.analysis_core.seasonality import seasonal_strength
from engine.synth.generate import flat_series, spike_series


def test_seasonal_strength_high_for_periodic():
    pts = spike_series(baseline=0.2, spike_height=1.8, period_h=8, spike_width_h=2, seed=0)
    vals = np.array([v for _, v in pts])
    assert seasonal_strength(vals, 8) > 0.3


def test_seasonal_strength_low_for_flat():
    pts = flat_series(baseline=1.0, noise=0.02, seed=2)
    vals = np.array([v for _, v in pts])
    assert seasonal_strength(vals, 8) < 0.3


def test_seasonal_strength_guards_short_series():
    assert seasonal_strength(np.array([1.0, 2.0, 1.0]), 8) == 0.0
