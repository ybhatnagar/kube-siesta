"""Instant scoring + sliding-window minimization (docs/07 §1 steps 8-9).

Score(t) = number of workloads projected active at t (target + all deps).
`min_window` finds the earliest-tie sliding window of a given length with the
smallest sum of scores in [now, deadline - L].
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd


@dataclass
class WindowPick:
    start_idx: int
    end_idx: int         # inclusive
    score_sum: float     # summed active-app-count over the window
    max_score: int       # peak active-app count within the window


def score_instants(forecasts: Iterable[pd.Series]) -> pd.Series:
    """Element-wise sum of a set of bool forecasts sharing one DatetimeIndex.

    Passing an empty iterable is a caller bug (no target → nothing to score);
    the runner should reject earlier, but if it slips through we raise so a
    silent zero-score doesn't produce a bogus "window".
    """
    forecasts = list(forecasts)
    if not forecasts:
        raise ValueError("score_instants requires at least one forecast")
    total = forecasts[0].astype(int).copy()
    for f in forecasts[1:]:
        total = total.add(f.astype(int), fill_value=0)
    return total


def min_window(scores: pd.Series, window_samples: int) -> WindowPick:
    """Rolling-sum minimization; return the earliest window of length W with min sum.

    `window_samples` counts *inclusive* samples in the window (>=1). The rolling
    sum at position i covers samples [i - W + 1 .. i]; we take the earliest i
    that hits the minimum so ties resolve as "sooner is better" (matches
    docs/07 §1 step 9: the recommendation should not be later than necessary).
    """
    if window_samples < 1:
        raise ValueError("window_samples must be >= 1")
    n = len(scores)
    if n < window_samples:
        raise ValueError(f"scores length {n} shorter than window {window_samples}")

    vals = scores.to_numpy()
    # Cumulative-sum trick avoids pandas rolling overhead and is O(n).
    csum = np.concatenate(([0.0], np.cumsum(vals, dtype="float64")))
    window_sums = csum[window_samples:] - csum[:-window_samples]  # length n - W + 1
    best_start = int(np.argmin(window_sums))                       # earliest tie
    best_end = best_start + window_samples - 1
    best_sum = float(window_sums[best_start])
    max_score = int(vals[best_start:best_end + 1].max())
    return WindowPick(start_idx=best_start, end_idx=best_end, score_sum=best_sum, max_score=max_score)
