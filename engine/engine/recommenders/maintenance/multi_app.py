"""Build a WorkloadForecast for the target + each dep.

Reuses `analysis_core.timeline.detect_timeline` — the maintenance-friendly
counterpart to `analyze_signal` that keeps every workload's timeline instead of
rejecting non-candidates. Aperiodic workloads (or ones with no metric data at
all) become always-active per the confirmed pessimistic default.
"""
from __future__ import annotations

from typing import Optional

import pandas as pd

from ...analysis_core.config import EngineConfig
from ...analysis_core.prepare import prepare_series
from ...analysis_core.timeline import detect_timeline
from .forecaster import Forecaster, SeasonalNaive
from .types import WorkloadForecast


def build_forecast(
    store,
    cluster_id: int,
    workload_uid: str,
    cfg: EngineConfig,
    future_index: pd.DatetimeIndex,
    forecaster_factory=SeasonalNaive,
) -> WorkloadForecast:
    """Recover the workload's timeline and project it onto `future_index`.

    Aperiodic / no-data workloads always yield an all-True forecast (option 1
    from the M3 planning discussion). The `note` field records which branch was
    taken so the DTO surfaces it — useful when payload verification suggests a
    recommendation is being unfairly penalized by unpredictable callers.
    """
    series_by_resource: dict[str, pd.Series] = {}
    for res in cfg.resources:
        pts = store.load_series(cluster_id, workload_uid, res)
        if pts:
            series_by_resource[res] = prepare_series(pts, cfg.resample_freq)

    timeline = detect_timeline(series_by_resource, cfg) if series_by_resource else None

    forecaster: Forecaster = forecaster_factory()
    if timeline is None:
        forecaster.fit(None, None)
        note = "no periodic signal; assumed always-active" if series_by_resource else "no metric data; assumed always-active"
        return WorkloadForecast(
            workload_uid=workload_uid,
            period_hours=None,
            active_forecast=forecaster.project(future_index),
            is_periodic=False,
            note=note,
        )

    forecaster.fit(timeline.active_series, timeline.period_hours)
    return WorkloadForecast(
        workload_uid=workload_uid,
        period_hours=timeline.period_hours,
        active_forecast=forecaster.project(future_index),
        is_periodic=True,
        note="",
    )


def build_forecasts(
    store,
    cluster_id: int,
    workload_uids: list[str],
    cfg: EngineConfig,
    future_index: pd.DatetimeIndex,
    forecaster_factory=SeasonalNaive,
) -> dict[str, WorkloadForecast]:
    """Build a forecast per workload uid. Order preserved for downstream display."""
    return {
        uid: build_forecast(store, cluster_id, uid, cfg, future_index, forecaster_factory)
        for uid in workload_uids
    }
