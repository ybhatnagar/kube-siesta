"""Stage 1 — Periodicity P (cheap first pass): autocorrelation + periodogram.

A cheap first pass avoids fitting heavy seasonal models to non-periodic series. The
patent's S-ARIMA seasonal-differencing/PACF machinery is a later swap-in behind this
same function signature.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.signal import find_peaks, periodogram


def _autocorr(x: np.ndarray, nlags: int) -> np.ndarray:
    """Normalized autocorrelation for lags 0..nlags."""
    x = x - x.mean()
    var = float(np.dot(x, x))
    if var == 0.0:
        return np.zeros(nlags + 1)
    full = np.correlate(x, x, mode="full")[len(x) - 1:]
    return full[: nlags + 1] / var


def detect_period(values: np.ndarray, min_period: int, max_period: Optional[int] = None) -> tuple[Optional[float], float]:
    """Estimate the dominant period (in samples) and a 0..1 strength score.

    Detrends, then takes the strongest autocorrelation peak at lag >= min_period,
    cross-checked against the periodogram's dominant frequency. Returns (None, 0.0)
    when no period is found.
    """
    n = len(values)
    if n < 2 * max(min_period, 2):
        return None, 0.0

    t = np.arange(n)
    trend = np.polyval(np.polyfit(t, values, 1), t)
    x = values - trend

    if max_period is None:
        max_period = n // 2
    max_period = min(max_period, n // 2)
    if max_period < min_period:
        return None, 0.0

    acf = _autocorr(x, max_period)
    peaks, _ = find_peaks(acf)
    candidates = [p for p in peaks if min_period <= p <= max_period]

    if candidates:
        best = max(candidates, key=lambda p: acf[p])
        period, strength = float(best), float(max(0.0, acf[best]))
    else:
        period, strength = None, 0.0

    # Cross-check / fallback with the periodogram's dominant frequency.
    freqs, power = periodogram(x)
    if len(power) > 1:
        k = int(np.argmax(power[1:])) + 1
        if freqs[k] > 0:
            pgram_period = 1.0 / freqs[k]
            if period is None and min_period <= pgram_period <= max_period:
                period = float(pgram_period)
                strength = float(max(0.0, acf[int(round(pgram_period))])) if int(round(pgram_period)) <= max_period else 0.3

    return period, strength


def consolidate_period(periods: dict[str, float], tolerance: float) -> Optional[float]:
    """Return a single period if the per-resource estimates agree within `tolerance`.

    Uses the median as the consensus and requires every estimate to fall within the
    relative tolerance band. Returns None when resources disagree (reject workload).
    """
    vals = [p for p in periods.values() if p and p > 0]
    if not vals:
        return None
    consensus = float(np.median(vals))
    for p in vals:
        if abs(p - consensus) / consensus > tolerance:
            return None
    return consensus


def resources_within_tolerance(periods: dict[str, float], consensus: float, tolerance: float) -> list[str]:
    """Resources whose period agrees with the consensus within tolerance."""
    out = []
    for res, p in periods.items():
        if p and p > 0 and abs(p - consensus) / consensus <= tolerance:
            out.append(res)
    return out
