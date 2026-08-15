"""Chart-series downsampling for the evidence endpoint.

Caps each resource series to ~200–500 points so the recommendation list stays light;
the UI draws the overlay (trend line, ε band, active windows) client-side.
"""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd


def downsample(index: pd.DatetimeIndex, values: np.ndarray, max_points: int = 300) -> list[dict]:
    """Stride-downsample to <= max_points as [{"t": iso, "v": float}]."""
    n = len(values)
    if n == 0:
        return []
    step = max(1, n // max_points)
    out = []
    for i in range(0, n, step):
        out.append({"t": _iso(index[i]), "v": round(float(values[i]), 6)})
    # Always include the final point so the tail is visible.
    if (n - 1) % step != 0:
        out.append({"t": _iso(index[n - 1]), "v": round(float(values[n - 1]), 6)})
    return out


def _iso(ts) -> str:
    if isinstance(ts, (pd.Timestamp, datetime)):
        return pd.Timestamp(ts).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ") if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
    return str(ts)
