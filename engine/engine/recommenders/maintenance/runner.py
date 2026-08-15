"""Maintenance recommender runner — CLI + API path.

Assembles the target + upstream deps, forecasts each active/idle mask to the
deadline, scores each instant by active-app count, and slides a window of the
requested length to find the earliest lowest-impact slot. Persists a single
maintenance_result row (docs/07 §1).
"""
from __future__ import annotations

import random
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd

from ...analysis_core.config import EngineConfig
from ...analysis_core.io.statestore import StateStore, _iso, _now
from ..job.runner import generate_name  # reuse the same slug generator
from .deps import all_workload_uids, upstream_deps
from .multi_app import build_forecasts
from .scoring import WindowPick, min_window, score_instants
from .types import (
    ImpactedApp,
    MaintenanceConfig,
    MaintenanceEvidence,
    MaintenanceResult,
    WorkloadForecast,
)


@dataclass
class RunResult:
    run_id: int
    name: str
    status: str
    data_as_of: Optional[str]
    stale: bool
    result_id: Optional[int] = None
    max_score: Optional[int] = None
    recommended_start: Optional[str] = None
    recommended_end: Optional[str] = None


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def run_maintenance_analysis(
    store: StateStore,
    *,
    cluster: Any,
    target_workload_uid: str,
    duration: str | float,
    deadline: str | datetime,
    scope: Any = "all",
    config_overrides: Optional[dict] = None,
    ttl_hours: int = 24,
    name: Optional[str] = None,
    rng: Optional[random.Random] = None,
    now: Optional[datetime] = None,
) -> RunResult:
    rng = rng or random.Random()
    now_utc = now or _now()

    cluster_id = cluster if isinstance(cluster, int) else store.ensure_cluster(str(cluster))

    settings = store.get_settings()
    cfg = EngineConfig.from_settings(settings).with_overrides(**_map_overrides(config_overrides))

    duration_minutes = _parse_duration_minutes(duration)
    deadline_dt = _parse_deadline(deadline, now_utc)
    if deadline_dt <= now_utc + timedelta(minutes=duration_minutes):
        raise ValueError(
            f"deadline ({_iso(deadline_dt)}) is too close to now — need at least "
            f"{duration_minutes:.0f} minutes of headroom",
        )

    maint_cfg = MaintenanceConfig(
        target_workload_uid=target_workload_uid,
        duration_minutes=duration_minutes,
        deadline=deadline_dt,
        resample_freq=cfg.resample_freq,
    )

    data_as_of = store.max_collected_at(cluster_id)
    metric_ttl_h = int((settings or {}).get("metric_ttl_hours", 24))
    stale = data_as_of is None or data_as_of < now_utc - timedelta(hours=metric_ttl_h)

    run_name = name or _unique_name(store, rng)
    run_id = store.create_analysis_run(
        name=run_name, cluster_id=cluster_id, scope=scope,
        config=_config_dict(cfg, maint_cfg),
        data_as_of=data_as_of, stale=stale, ttl_hours=ttl_hours,
        run_type="maintenance",
    )

    try:
        deps = upstream_deps(store, cluster_id, target_workload_uid)
        all_uids = all_workload_uids(target_workload_uid, deps)

        future_index = _future_index(now_utc, deadline_dt, cfg.resample_freq)
        window_samples = max(1, int(round(duration_minutes / _minutes_per_sample(cfg.resample_freq))))
        if len(future_index) < window_samples:
            raise ValueError(
                f"forecast horizon ({len(future_index)} samples) shorter than the "
                f"requested window ({window_samples} samples). Increase deadline or "
                f"shorten resample_freq.",
            )

        forecasts = build_forecasts(store, cluster_id, all_uids, cfg, future_index)
        scores = score_instants(f.active_forecast for f in forecasts.values())
        pick = min_window(scores, window_samples)

        result = _assemble_result(
            store=store,
            cluster_id=cluster_id,
            target_uid=target_workload_uid,
            maint_cfg=maint_cfg,
            future_index=future_index,
            forecasts=forecasts,
            scores=scores,
            pick=pick,
        )
        result_id = store.insert_maintenance_result(run_id, result.to_row())

        store.finish_analysis_run(run_id, "completed")
        return RunResult(
            run_id=run_id, name=run_name, status="completed",
            data_as_of=_iso(data_as_of), stale=stale,
            result_id=result_id, max_score=pick.max_score,
            recommended_start=_iso(result.recommended_start),
            recommended_end=_iso(result.recommended_end),
        )
    except Exception as exc:  # pragma: no cover - defensive; surfaced to caller
        store.finish_analysis_run(run_id, "failed", error=str(exc))
        raise


# ---------------------------------------------------------------------------
# Result assembly
# ---------------------------------------------------------------------------

def _assemble_result(
    *,
    store: StateStore,
    cluster_id: int,
    target_uid: str,
    maint_cfg: MaintenanceConfig,
    future_index: pd.DatetimeIndex,
    forecasts: dict[str, WorkloadForecast],
    scores: pd.Series,
    pick: WindowPick,
) -> MaintenanceResult:
    window_start_ts = future_index[pick.start_idx].to_pydatetime()
    # Report the window as [start, start + L]. `pick.end_idx` marks the last
    # forecast sample inside the window (N samples cover (N-1) intervals), so
    # deriving the end from duration matches the user's requested length.
    window_end_ts = window_start_ts + timedelta(minutes=maint_cfg.duration_minutes)

    target_ident = store.get_identity(cluster_id, target_uid) or {}
    ns, kind, name = _resolve_identity(target_ident, target_uid)

    impacted: list[ImpactedApp] = []
    for uid, fc in forecasts.items():
        if uid == target_uid:
            continue
        ident = store.get_identity(cluster_id, uid) or {}
        dep_ns, dep_kind, dep_name = _resolve_identity(ident, uid)
        active_frac = float(fc.active_forecast.astype(int).mean()) if len(fc.active_forecast) else 0.0
        # samples in the chosen window where this dep is projected active
        in_window = fc.active_forecast.iloc[pick.start_idx: pick.end_idx + 1]
        impact = float(in_window.astype(int).sum())
        note = fc.note or (f"detected {fc.period_hours:.1f}h cycle" if fc.period_hours else "")
        impacted.append(ImpactedApp(
            workload_uid=uid, workload_kind=dep_kind, workload_name=dep_name, namespace=dep_ns,
            period_hours=fc.period_hours, active_fraction=active_frac, impact_score=impact, note=note,
        ))

    evidence = [_evidence_for(uid, fc) for uid, fc in forecasts.items()]
    confidence = _confidence(pick, n_workloads=len(forecasts))
    summary = _summary_text(pick, window_start_ts, window_end_ts, impacted, forecasts, target_uid)

    return MaintenanceResult(
        maintenance_for_uid=target_uid,
        workload_kind=kind, workload_name=name, namespace=ns,
        recommended_start=window_start_ts, recommended_end=window_end_ts,
        duration_min=maint_cfg.duration_minutes, deadline=maint_cfg.deadline,
        impact_score=pick.score_sum, confidence=confidence, summary_text=summary,
        impacted_apps=impacted, evidence=evidence,
    )


def _evidence_for(uid: str, fc: WorkloadForecast) -> MaintenanceEvidence:
    series = fc.active_forecast
    # Downsample to at most 512 points for chart display; store as JSON-friendly list.
    step = max(1, len(series) // 512)
    points = [
        {"ts": ts.isoformat().replace("+00:00", "Z"), "value": bool(v)}
        for ts, v in zip(series.index[::step], series.to_numpy()[::step])
    ]
    return MaintenanceEvidence(
        workload_uid=uid, resource="union",
        forecast_series=points, active_windows=_windows_from_mask(series),
    )


def _windows_from_mask(active: pd.Series) -> list[dict]:
    """Contiguous True runs → list of {start, end} timestamps (ISO-8601 Z)."""
    arr = active.to_numpy().astype(bool)
    idx = active.index
    out: list[dict] = []
    start: Optional[int] = None
    for i, v in enumerate(arr):
        if v and start is None:
            start = i
        elif not v and start is not None:
            out.append({"start": _iso(idx[start].to_pydatetime()),
                        "end": _iso(idx[i - 1].to_pydatetime())})
            start = None
    if start is not None:
        out.append({"start": _iso(idx[start].to_pydatetime()),
                    "end": _iso(idx[-1].to_pydatetime())})
    return out


def _confidence(pick: WindowPick, n_workloads: int) -> str:
    """Simple max-concurrent heuristic. Nothing scientific — the field can be
    refined once we have payload verification signal.
    """
    if pick.max_score <= 1:
        return "high"
    if pick.max_score <= 2:
        return "medium"
    return "low"


def _summary_text(pick, start_ts, end_ts, impacted, forecasts, target_uid) -> str:
    date_str = start_ts.strftime("%Y-%m-%d")
    time_str = f"{start_ts.strftime('%H:%M')}–{end_ts.strftime('%H:%M')} UTC"
    n_deps_active = sum(1 for a in impacted if a.impact_score > 0)
    n_aperiodic_active = sum(
        1 for a in impacted
        if a.impact_score > 0 and a.period_hours is None
    )
    target_forecast = forecasts[target_uid].active_forecast
    target_active_in_window = bool(target_forecast.iloc[pick.start_idx: pick.end_idx + 1].any())

    parts = [f"Recommended window: {time_str} on {date_str}."]
    if pick.max_score == 0:
        parts.append("All workloads projected idle — best-case slot.")
    else:
        who = []
        if target_active_in_window:
            who.append("target")
        if n_deps_active:
            who.append(f"{n_deps_active} caller{'s' if n_deps_active != 1 else ''}")
        parts.append(f"Active during window: {', '.join(who) or 'none'} (peak concurrent: {pick.max_score}).")
    if n_aperiodic_active:
        parts.append(
            f"{n_aperiodic_active} aperiodic caller"
            f"{'s' if n_aperiodic_active != 1 else ''} projected always-active (pessimistic).",
        )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(min|m|h|d)\s*$", re.IGNORECASE)


def _parse_duration_minutes(v: str | float) -> float:
    if isinstance(v, (int, float)):
        return float(v)
    m = _DURATION_RE.match(str(v))
    if not m:
        raise ValueError(f"unrecognized duration {v!r}; expected e.g. '30m', '2h', '1d'")
    n = float(m.group(1))
    unit = m.group(2).lower()
    if unit in ("min", "m"):
        return n
    if unit == "h":
        return n * 60.0
    return n * 60.0 * 24.0


def _parse_deadline(v: str | datetime, now_utc: datetime) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = str(v).strip()
    m = _DURATION_RE.match(s)
    if m:
        return now_utc + timedelta(minutes=_parse_duration_minutes(s))
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ValueError(f"unrecognized deadline {v!r}; use '3d', '48h', or ISO-8601") from exc


def _minutes_per_sample(freq: str) -> float:
    f = freq.lower().strip()
    try:
        if f.endswith("min"):
            return float(f[:-3] or 1)
        if f.endswith("h"):
            return float(f[:-1] or 1) * 60.0
        if f.endswith("d"):
            return float(f[:-1] or 1) * 60.0 * 24.0
    except ValueError:
        pass
    return 60.0


def _future_index(now_utc: datetime, deadline: datetime, freq: str) -> pd.DatetimeIndex:
    return pd.date_range(start=now_utc, end=deadline, freq=freq, tz="UTC")


def _resolve_identity(identity: dict, uid: str):
    ns = identity.get("namespace")
    kind = identity.get("kind")
    name = identity.get("name")
    if not (ns and kind and name):
        parts = uid.split("/")
        if len(parts) >= 3:
            p_ns, p_kind, p_name = parts[0], parts[1], "/".join(parts[2:])
        else:
            p_ns, p_kind, p_name = "default", "Deployment", uid
        ns, kind, name = ns or p_ns, kind or p_kind, name or p_name
    return ns, kind, name


def _unique_name(store: StateStore, rng: random.Random) -> str:
    for _ in range(20):
        candidate = generate_name(rng)
        if store._fetchone("SELECT 1 AS x FROM analysis_runs WHERE name = ?", (candidate,)) is None:
            return candidate
    return generate_name(rng)


def _map_overrides(overrides: Optional[dict]) -> dict:
    """Only pass through the EngineConfig fields the maintenance path cares about."""
    if not overrides:
        return {}
    out: dict[str, Any] = {}
    for k in ("resources", "resample_freq", "min_period", "concurrency"):
        if overrides.get(k) is not None:
            out[k] = overrides[k]
    return out


def _config_dict(cfg: EngineConfig, maint_cfg: MaintenanceConfig) -> dict:
    return {
        "resources": cfg.resources,
        "resample_freq": cfg.resample_freq,
        "min_period": cfg.min_period,
        "maintenance": {
            "target_workload_uid": maint_cfg.target_workload_uid,
            "duration_minutes": maint_cfg.duration_minutes,
            "deadline": _iso(maint_cfg.deadline),
        },
    }
