"""Stage 1 — periodicity recovers a known period."""
import numpy as np

from engine.analysis_core.periodicity import consolidate_period, detect_period


def test_detect_period_recovers_known_period():
    # 40 periods of a clean period-8 square wave.
    n = 320
    vals = np.array([2.0 if (i % 8) < 2 else 0.2 for i in range(n)], dtype=float)
    period, strength = detect_period(vals, min_period=3, max_period=n // 2)
    assert period is not None
    assert abs(period - 8) <= 1
    assert strength > 0.3


def test_detect_period_rejects_pure_noise():
    rng = np.random.default_rng(0)
    vals = rng.normal(10.0, 1.0, 300)
    period, strength = detect_period(vals, min_period=3, max_period=150)
    # No stable seasonality: either no period, or a weak one we won't trust downstream.
    assert period is None or strength < 0.3


def test_consolidate_period_agreement():
    assert consolidate_period({"cpu": 8.0, "memory": 8.2}, tolerance=0.25) is not None
    assert consolidate_period({"cpu": 8.0, "memory": 24.0}, tolerance=0.25) is None
