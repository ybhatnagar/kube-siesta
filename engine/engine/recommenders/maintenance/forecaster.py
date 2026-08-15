"""Forecast a workload's active/idle mask forward to the maintenance deadline.

`Forecaster` is the swappable interface docs/07 §4.3 mandates — SeasonalNaive is
the shipped default (repeat the detected period forward). Holt-Winters / SARIMA
/ statsforecast drop in behind the same protocol later.

The confirmed decision (see chat history + real-deployment memory): aperiodic
workloads project as always-active — a pessimistic default that keeps the
maintenance window quiet even against unpredictable callers. The trade-off is
that a truly-idle-but-aperiodic dep will incorrectly inflate the window's
impact score; watch for this during payload verification.
"""
from __future__ import annotations

from typing import Optional, Protocol

import numpy as np
import pandas as pd


class Forecaster(Protocol):
    """Project an active/idle mask onto a future DatetimeIndex."""

    def fit(self, active_series: Optional[pd.Series], period_hours: Optional[float]) -> None: ...

    def project(self, future_index: pd.DatetimeIndex) -> pd.Series: ...


class SeasonalNaive:
    """Repeat the most recent full period of the observed active mask forward.

    Aperiodic input (`period_hours is None` or `active_series is None`) → always-
    active projection, per the confirmed pessimistic default.
    """

    def __init__(self) -> None:
        self._template: Optional[np.ndarray] = None
        self._period_hours: Optional[float] = None
        self._step_hours: Optional[float] = None
        self._period_start: Optional[pd.Timestamp] = None

    def fit(self, active_series: Optional[pd.Series], period_hours: Optional[float]) -> None:
        if active_series is None or period_hours is None or len(active_series) < 2:
            self._template = None
            self._period_hours = None
            return

        idx = active_series.index
        # Infer the sample step (hours) from the first two timestamps — cheap and
        # exact when the mask was resampled to a regular freq (which it is
        # coming from detect_timeline).
        step_hours = (idx[1] - idx[0]).total_seconds() / 3600.0
        n_per_period = max(1, int(round(period_hours / step_hours)))
        template = active_series.to_numpy().astype(bool)[-n_per_period:]

        self._template = template
        self._period_hours = period_hours
        self._step_hours = step_hours
        # Anchor: template[0] represents this timestamp on the historical grid.
        self._period_start = idx[-n_per_period]

    def project(self, future_index: pd.DatetimeIndex) -> pd.Series:
        if self._template is None or self._step_hours is None or self._period_start is None:
            # Aperiodic → treat as always-active.
            return pd.Series(np.ones(len(future_index), dtype=bool), index=future_index)

        n = len(self._template)
        # Phase in samples relative to the template's anchor timestamp.
        deltas_h = (future_index - self._period_start).total_seconds() / 3600.0
        phases = np.mod(np.round(deltas_h / self._step_hours).astype(int), n)
        return pd.Series(self._template[phases], index=future_index)
