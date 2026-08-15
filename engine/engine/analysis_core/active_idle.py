"""Stage 3 — Active/idle classification via a rolling-median band.

Rolling median over ~2P is the trend T_t; the band is median·(1 ± band_pct). Points
above the upper band are active, the rest idle. (The patent's exact ±10%·trend S-ARIMA
band is a later swap-in behind this same function.)
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class ActiveIdle:
    active: np.ndarray        # bool mask
    trend_value: float        # representative rolling-median level
    eps_min: float            # representative lower band
    eps_max: float            # representative upper band
    windows: list[tuple]      # contiguous active runs as (start_idx, end_idx_inclusive)


def classify(values: np.ndarray, period: float, band_pct: float) -> ActiveIdle:
    values = np.asarray(values, dtype="float64")
    n = len(values)
    window = max(3, int(round(2 * period)))
    med = pd.Series(values).rolling(window=window, center=True, min_periods=1).median().to_numpy()

    eps_max_arr = med * (1.0 + band_pct)
    eps_min_arr = med * (1.0 - band_pct)
    active = values > eps_max_arr

    windows = _contiguous_true(active)
    return ActiveIdle(
        active=active,
        trend_value=float(np.median(med)) if n else 0.0,
        eps_min=float(np.median(eps_min_arr)) if n else 0.0,
        eps_max=float(np.median(eps_max_arr)) if n else 0.0,
        windows=windows,
    )


def _contiguous_true(mask: np.ndarray) -> list[tuple]:
    """Return (start, end_inclusive) index ranges for each run of True."""
    runs = []
    start = None
    for i, v in enumerate(mask):
        if v and start is None:
            start = i
        elif not v and start is not None:
            runs.append((start, i - 1))
            start = None
    if start is not None:
        runs.append((start, len(mask) - 1))
    return runs


def longest_run_len(windows: list[tuple]) -> int:
    return max((e - s + 1 for s, e in windows), default=0)
