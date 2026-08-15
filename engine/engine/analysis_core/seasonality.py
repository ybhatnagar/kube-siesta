"""Stage 2 — Seasonality confirmation via STL seasonal strength.

seasonal_strength = max(0, 1 − Var(resid) / Var(resid + seasonal)); keep if it beats
`seasonality_strength_min` (~0.30, the "beats trend by 30%" analogue). The patent's
S-ARIMA additive/multiplicative-vs-trend RMSE test on a 70/30 split swaps in here later.
"""
from __future__ import annotations

import numpy as np
from statsmodels.tsa.seasonal import STL


def seasonal_strength(values: np.ndarray, period: float) -> float:
    """STL seasonal strength at the given period; 0.0 when it can't be computed."""
    p = int(round(period))
    if p < 2 or len(values) < 2 * p:
        return 0.0
    try:
        res = STL(np.asarray(values, dtype="float64"), period=p, robust=True).fit()
    except Exception:
        return 0.0
    resid = res.resid
    seasonal = res.seasonal
    denom = float(np.var(resid + seasonal))
    if denom == 0.0:
        return 0.0
    return float(max(0.0, 1.0 - np.var(resid) / denom))
