"""Job recommender tail: turn a Signal into a WorkloadRecommendation card.

Stage 6 — cost / target-tier / confidence / "why" + evidence. Runs after the
shared front-half (`analysis_core.signal.analyze_signal`). Kept pure and
DB-free; the runner (`recommenders/job/runner.py`) handles persistence.
"""
from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from ...analysis_core.config import EngineConfig
from ...analysis_core.filter import baseline_ratio
from ...analysis_core.signal import Signal, analyze_signal
from ...analysis_core.types import Interval, ResourceEvidence
from ...why import templates
from ...why.series import downsample
from . import target as target_stage
from .cost import estimate as estimate_cost
from .types import WorkloadRecommendation


def build_recommendation(
    workload_uid: str,
    signal: Signal,
    identity: Optional[dict],
    cfg: EngineConfig,
    *,
    has_adhoc_interactions: bool = False,
    peers: Optional[list] = None,
    with_evidence: bool = True,
) -> WorkloadRecommendation:
    """Stage 6 — turn a Signal into a recommendation card (cost, target, confidence, why)."""
    kept, ai, jumps, seas, agg = signal.kept, signal.ai, signal.jumps, signal.seas, signal.agg
    index, aligned = signal.index, signal.aligned
    n = len(index)

    windows = agg.windows
    longest = max(windows, key=lambda w: w[1] - w[0])
    num_windows = len(windows)
    idle_fraction = float((~agg.union).sum()) / n
    active_minutes = _median_window_minutes(windows, signal.hps)
    run_time = target_stage.format_run_time(index[longest[0]])
    cadence = target_stage.format_cadence(signal.period_hours)
    duration = target_stage.format_duration(active_minutes)

    primary = "cpu" if "cpu" in kept else max(kept, key=lambda r: jumps[r])
    identity = identity or {}
    cost = estimate_cost(
        idle_fraction, cfg,
        requests_cpu_m=identity.get("requests_cpu_m"),
        requests_mem_bytes=identity.get("requests_mem_bytes"),
        replicas=identity.get("replicas"),
        usage_cpu_cores=_mean_active(aligned.get("cpu"), agg.union),
        usage_mem_bytes=_mean_active(aligned.get("memory"), agg.union),
    )
    to_target = target_stage.choose_target(
        baseline_ratio=baseline_ratio(aligned[primary].to_numpy(), ai[primary].active),
        num_active_windows=num_windows,
        has_adhoc_interactions=has_adhoc_interactions,
    )
    conf = target_stage.confidence(
        seasonal_strength=seas[primary],
        resources_agree=len(kept) > 1,
        overlap_frac=agg.overlap_frac,
        jump_pct=jumps[primary],
        jump_min=cfg.jump_min,
    )

    others = [r for r in kept if r != primary]
    summary_text = templates.summary(
        primary_resource=primary,
        jump_pct=jumps[primary],
        duration_str=duration,
        cadence_str=cadence,
        other_resources=others,
        overlap_pct=agg.overlap_frac * 100.0,
    )

    ns, kind, name = _resolve_identity(identity, workload_uid)
    evidence = (
        [_evidence(res, aligned[res], ai[res], jumps[res], signal.period_hours, agg, signal.hps) for res in kept]
        if with_evidence else []
    )
    return WorkloadRecommendation(
        workload_uid=workload_uid, workload_kind=kind, workload_name=name, namespace=ns,
        from_type="Pod", to_target=to_target, cadence=cadence, run_time=run_time, duration=duration,
        savings_amount=cost.monthly_savings, savings_currency=cfg.currency, savings_period="month",
        confidence=conf, summary_text=summary_text, evidence=evidence, peers=peers or [],
    )


def analyze_workload(
    workload_uid: str,
    series_by_resource: dict[str, pd.Series],
    identity: Optional[dict],
    cfg: EngineConfig,
) -> Optional[WorkloadRecommendation]:
    """Convenience: recover the signal and build a recommendation (no peers)."""
    signal = analyze_signal(series_by_resource, cfg)
    if signal is None:
        return None
    return build_recommendation(workload_uid, signal, identity, cfg)


# --- helpers ---------------------------------------------------------------

def _evidence(res, series, ai, jump, period_hours, agg, hps) -> ResourceEvidence:
    from ...analysis_core.filter import usage_ratio
    idx = series.index
    windows = [Interval(start=idx[s], end=idx[e]) for s, e in ai.windows]
    points = downsample(idx, series.to_numpy())
    return ResourceEvidence(
        resource=res,
        jump_pct=_finite(jump),
        active_idle_ratio=_finite(usage_ratio(ai.active)),
        period_hours=period_hours,
        active_duration_min=_median_window_minutes(ai.windows, hps),
        overlap_pct=agg.overlap_frac * 100.0,
        trend_value=ai.trend_value,
        eps_min=ai.eps_min,
        eps_max=ai.eps_max,
        active_windows=windows,
        series=points,
    )


def _resolve_identity(identity: dict, uid: str):
    ns, kind, name = identity.get("namespace"), identity.get("kind"), identity.get("name")
    if not (ns and kind and name):
        p_ns, p_kind, p_name = _parse_uid(uid)
        ns, kind, name = ns or p_ns, kind or p_kind, name or p_name
    return ns, kind, name


def _parse_uid(uid: str):
    parts = uid.split("/")
    if len(parts) >= 3:
        return parts[0], parts[1], "/".join(parts[2:])
    return "default", "Deployment", uid


def _mean_active(series: Optional[pd.Series], mask: np.ndarray) -> Optional[float]:
    if series is None:
        return None
    vals = series.to_numpy()
    if len(vals) != len(mask) or not mask.any():
        return None
    return float(vals[mask].mean())


def _median_window_minutes(windows: list[tuple], hours_per_sample: float) -> float:
    if not windows:
        return 0.0
    lengths = [(e - s + 1) for s, e in windows]
    return float(np.median(lengths)) * hours_per_sample * 60.0


def _finite(x: float) -> float:
    return float(x) if np.isfinite(x) else 1e9
