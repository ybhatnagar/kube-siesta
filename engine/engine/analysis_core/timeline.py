"""Recover a workload's active/idle timeline for downstream recommenders.

Sibling of `signal.analyze_signal`. Where `analyze_signal` bakes in the job
recommender's candidate-rejection filters (jump%, ratio, union, overlap),
`detect_timeline` skips them: it returns whenever a periodic active/idle mask
can be recovered at all. Maintenance uses this so aperiodic or always-busy
workloads still contribute to multi-app scoring (see docs/07 §1 step 5 —
"maintenance keeps every app's timeline, it doesn't discard non-candidates").

Returns None when there is genuinely no usable signal — no data, no detected
period, or no seasonal strength on any resource. The maintenance head treats
that as aperiodic and, per the confirmed decision, projects it as always-active
(see recommenders/maintenance/multi_app.py).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .active_idle import classify
from .aggregate import aggregate
from .config import EngineConfig
from .periodicity import consolidate_period, detect_period, resources_within_tolerance
from .seasonality import seasonal_strength
from .signal import align_series, freq_hours


@dataclass
class WorkloadTimeline:
    """The active/idle mask + period recovered for one workload.

    A superset of what maintenance needs; job-shaped filters intentionally not
    applied. `active_series` is a bool Series aligned to the observed time
    index; the forecaster projects this forward to the maintenance deadline.
    """
    period_hours: float
    active_series: pd.Series          # bool mask, DatetimeIndex, freq = cfg.resample_freq
    index: pd.DatetimeIndex
    hps: float                        # hours per sample
    kept_resources: list[str]         # resources that contributed to the mask


def detect_timeline(series_by_resource: dict[str, pd.Series], cfg: EngineConfig) -> Optional[WorkloadTimeline]:
    """Recover the union active/idle mask + period, or None if aperiodic.

    Runs the shared front-half stages (align → periodicity → seasonality →
    active/idle → aggregate) with no candidate-rejection filters. The union
    of active masks across resources becomes the workload's timeline.
    """
    hps = freq_hours(cfg.resample_freq)
    aligned, index = align_series(series_by_resource, cfg.resources)
    if not aligned:
        return None
    n = len(index)
    max_period = n // 2

    # Periodicity per resource.
    periods: dict[str, float] = {}
    for res, s in aligned.items():
        p, _ = detect_period(s.to_numpy(), cfg.min_period, max_period)
        if p is not None and p >= cfg.min_period:
            periods[res] = p
    if not periods:
        return None

    consensus = consolidate_period(periods, cfg.period_tolerance)
    if consensus is None:
        return None
    kept = resources_within_tolerance(periods, consensus, cfg.period_tolerance)

    # Seasonality gate — if nothing is seasonal we can't project periodically.
    seas = {res: seasonal_strength(aligned[res].to_numpy(), consensus) for res in kept}
    kept = [res for res in kept if seas[res] >= cfg.seasonality_strength_min]
    if not kept:
        return None

    ai = {res: classify(aligned[res].to_numpy(), consensus, cfg.band_pct) for res in kept}
    agg = aggregate({res: ai[res].active for res in kept})

    return WorkloadTimeline(
        period_hours=consensus * hps,
        active_series=pd.Series(agg.union, index=index),
        index=index,
        hps=hps,
        kept_resources=kept,
    )
