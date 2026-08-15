"""Shared analysis: recover the periodic Signal from a workload's series.

Stages 0–5 as a pure, DB-free function: prepare → periodicity → seasonality →
active/idle → filter → aggregate. Returns None when the workload is not a
candidate / is inconclusive.

Both recommender heads (job, maintenance) build on this. Job's builder turns the
Signal into a card + cost + tiered target; maintenance's head will use the
active masks + detected period as the input to forward projection.

Note: `analyze_signal` currently keeps the job-shaped candidate-rejection
filters (`ratio_max`, `union_max`, `overlap_min`) inline for behavior-preserving
parity with the pre-refactor pipeline. Splitting those out so the maintenance
head can keep every workload's timeline (docs/07 §1 step 5) lands in a later
milestone.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd

from .active_idle import classify
from .aggregate import Aggregate, aggregate
from .config import EngineConfig
from .filter import peak_load_jump_pct, usage_ratio
from .periodicity import consolidate_period, detect_period, resources_within_tolerance
from .seasonality import seasonal_strength


@dataclass
class Signal:
    """The periodic signal recovered from a workload (stages 0–5)."""
    aligned: dict            # resource -> pd.Series (hourly, gap-filled)
    index: pd.DatetimeIndex
    kept: list               # resources that survived the filters
    consensus: float         # period in samples
    period_hours: float
    hps: float               # hours per sample
    seas: dict               # resource -> seasonal strength
    ai: dict                 # resource -> ActiveIdle
    jumps: dict              # resource -> peak-load jump %
    agg: Aggregate
    active_series: pd.Series  # union active mask, indexed by timestamp (for peer alignment)


def analyze_signal(series_by_resource: dict[str, pd.Series], cfg: EngineConfig) -> Optional[Signal]:
    """Run stages 0–5. Returns None if the workload is not a candidate / inconclusive."""
    hps = freq_hours(cfg.resample_freq)
    aligned, index = align_series(series_by_resource, cfg.resources)
    if not aligned:
        return None
    n = len(index)
    max_period = n // 2

    # Stage 1 — periodicity per resource.
    periods: dict[str, float] = {}
    strengths: dict[str, float] = {}
    for res, s in aligned.items():
        p, st = detect_period(s.to_numpy(), cfg.min_period, max_period)
        if p is not None and p >= cfg.min_period:
            periods[res] = p
            strengths[res] = st
    if not periods:
        return None

    consensus = consolidate_period(periods, cfg.period_tolerance)
    if consensus is None:
        return None  # resources disagree on P → reject
    kept = resources_within_tolerance(periods, consensus, cfg.period_tolerance)

    # Stage 2 — seasonality confirmation.
    seas = {res: seasonal_strength(aligned[res].to_numpy(), consensus) for res in kept}
    kept = [res for res in kept if seas[res] >= cfg.seasonality_strength_min]
    if not kept:
        return None

    # Stage 3 — active/idle classification.
    ai = {res: classify(aligned[res].to_numpy(), consensus, cfg.band_pct) for res in kept}

    # Stage 4 — jump filter (per resource).
    jumps = {res: peak_load_jump_pct(aligned[res].to_numpy(), ai[res].active) for res in kept}
    kept = [res for res in kept if jumps[res] >= cfg.jump_min]
    if not kept:
        return None

    # Stage 5 — cross-resource aggregation + workload-level filters.
    agg = aggregate({res: ai[res].active for res in kept})
    if usage_ratio(agg.union) >= cfg.ratio_max:
        return None  # active too often to shift
    if agg.union_frac >= cfg.union_max:
        return None  # active spans too much of wall-clock
    if agg.overlap_frac < cfg.overlap_min:
        return None  # resources don't overlap → inconclusive

    return Signal(
        aligned=aligned, index=index, kept=kept, consensus=consensus,
        period_hours=consensus * hps, hps=hps, seas=seas, ai=ai, jumps=jumps, agg=agg,
        active_series=pd.Series(agg.union, index=index),
    )


def align_series(series_by_resource: dict[str, pd.Series], resources: list[str]):
    """Reindex per-resource series onto a common time grid, gap-fill lightly.

    Returns (aligned_dict, common_index) or ({}, None) when nothing usable.
    """
    present = {r: s for r, s in series_by_resource.items() if r in resources and s is not None and not s.empty}
    if not present:
        return {}, None
    common = None
    for s in present.values():
        common = s.index if common is None else common.union(s.index)
    common = common.sort_values()
    aligned = {}
    for r, s in present.items():
        rs = s.reindex(common).interpolate(limit=2, limit_direction="both").ffill().bfill()
        if not rs.isna().all():
            aligned[r] = rs.fillna(0.0)
    return aligned, common


def freq_hours(freq: str) -> float:
    """Parse a pandas resample freq string (e.g. '1h', '2min', '1d') to hours."""
    f = freq.lower().strip()
    try:
        if f.endswith("min"):
            return float(f[:-3] or 1) / 60.0
        if f.endswith("h"):
            return float(f[:-1] or 1)
        if f.endswith("t"):
            return float(f[:-1] or 1) / 60.0
        if f.endswith("d"):
            return float(f[:-1] or 1) * 24.0
    except ValueError:
        pass
    return 1.0
