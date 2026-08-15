"""Job recommender runner — orchestrates per-workload analysis and persists results.

The one code path shared by the CLI (`engine run --type job`) and the API
(`POST /runs` with `run_type: job`). Recovers each workload's periodic Signal via
the shared analysis_core, uses the dependency graph to (a) flag ad-hoc inbound
traffic for the target heuristic and (b) expand aligned peers, then builds and
stores the recommendations. Only this module and analysis_core/io touch the DB;
the shared stages stay pure.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import timedelta
from typing import Any, Optional

from ...analysis_core.config import EngineConfig
from ...analysis_core.interaction_graph import has_adhoc_inbound, shares_period, windows_align
from ...analysis_core.io.statestore import StateStore, _iso, _now
from ...analysis_core.prepare import prepare_series
from ...analysis_core.signal import Signal, analyze_signal
from .builder import build_recommendation
from .types import Peer

_ADJ = ["brave", "calm", "clever", "eager", "gentle", "jolly", "keen", "lively",
        "merry", "noble", "proud", "swift", "witty", "zesty", "amber", "bold"]
_ANIMALS = ["otter", "falcon", "lynx", "panda", "koala", "heron", "ibis", "marmot",
            "narwhal", "quokka", "raven", "stoat", "tapir", "viper", "yak", "wren"]


@dataclass
class RunResult:
    run_id: int
    name: str
    status: str
    recommendations: int
    data_as_of: Optional[str]
    stale: bool


def generate_name(rng: random.Random) -> str:
    return f"{rng.choice(_ADJ)}-{rng.choice(_ANIMALS)}-{rng.randint(1000, 9999)}"


def run_job_analysis(
    store: StateStore,
    *,
    cluster: Any,
    scope: Any = "all",
    config_overrides: Optional[dict] = None,
    ttl_hours: int = 24,
    collect_data: bool = False,
    name: Optional[str] = None,
    rng: Optional[random.Random] = None,
) -> RunResult:
    rng = rng or random.Random()
    cluster_id = cluster if isinstance(cluster, int) else store.ensure_cluster(str(cluster))

    settings = store.get_settings()
    cfg = EngineConfig.from_settings(settings).with_overrides(**_map_overrides(config_overrides))

    # Optionally collect fresh metrics first (failure-tolerant: proceed on stored data).
    collection_run_id = _maybe_collect(store, cluster_id, scope, cfg) if collect_data else None

    data_as_of = store.max_collected_at(cluster_id)
    metric_ttl_h = int((settings or {}).get("metric_ttl_hours", 24))
    stale = data_as_of is None or data_as_of < _now() - timedelta(hours=metric_ttl_h)

    run_name = name or _unique_name(store, rng)
    run_id = store.create_analysis_run(
        name=run_name, cluster_id=cluster_id, scope=scope,
        config=_config_dict(cfg), data_as_of=data_as_of, stale=stale, ttl_hours=ttl_hours,
        collection_run_id=collection_run_id,
    )

    # Memoize signals/identities so peers and inbound callers aren't re-analyzed.
    signals: dict[str, Optional[Signal]] = {}
    identities: dict[str, Optional[dict]] = {}

    def signal_for(uid: str) -> Optional[Signal]:
        if uid not in signals:
            series = {}
            for res in cfg.resources:
                points = store.load_series(cluster_id, uid, res)
                if points:
                    series[res] = prepare_series(points, cfg.resample_freq)
            signals[uid] = analyze_signal(series, cfg) if series else None
        return signals[uid]

    def identity_for(uid: str) -> Optional[dict]:
        if uid not in identities:
            identities[uid] = store.get_identity(cluster_id, uid)
        return identities[uid]

    n_recs = 0
    try:
        for uid in store.list_workload_uids(cluster_id, scope):
            signal = signal_for(uid)
            if signal is None:
                continue

            has_adhoc = _detect_adhoc(store, cluster_id, uid, signal, signal_for)
            peers = _expand_peers(store, cluster_id, uid, signal, cfg, signal_for, identity_for)

            rec = build_recommendation(
                uid, signal, identity_for(uid), cfg,
                has_adhoc_interactions=has_adhoc, peers=peers,
            )
            store.insert_recommendation(run_id, rec)
            n_recs += 1
        store.finish_analysis_run(run_id, "completed")
        status = "completed"
    except Exception as exc:  # pragma: no cover - defensive; surfaced to caller
        store.finish_analysis_run(run_id, "failed", error=str(exc))
        raise

    return RunResult(
        run_id=run_id, name=run_name, status=status, recommendations=n_recs,
        data_as_of=_iso(data_as_of), stale=stale,
    )


def _maybe_collect(store, cluster_id, scope, cfg) -> Optional[int]:
    """Trigger the collector and wait for it; return the collection_run id, or None when
    the collector is unavailable (the run then proceeds on whatever data is stored)."""
    from ...collector import CollectorUnavailable, trigger_collection, wait_for_collection
    try:
        res = trigger_collection(cluster_id, scope, cfg.resources, cfg.window)
    except CollectorUnavailable:
        return None
    collection_id = int(res["collection_id"])
    wait_for_collection(store, collection_id)
    return collection_id


def _detect_adhoc(store, cluster_id, uid, signal, signal_for) -> bool:
    """Any inbound caller whose active window falls in this workload's idle time."""
    incoming_actives = []
    for edge in store.get_incoming_interactions(cluster_id, uid):
        src_sig = signal_for(edge["src_workload_uid"])
        if src_sig is not None:
            incoming_actives.append(src_sig.active_series)
    return has_adhoc_inbound(signal.active_series, incoming_actives)


def _expand_peers(store, cluster_id, uid, signal, cfg, signal_for, identity_for) -> list:
    """Suggest downstream peers that share the period and spike at the same time."""
    peers = []
    for edge in store.get_outgoing_interactions(cluster_id, uid):
        peer_uid = edge["dst_workload_uid"]
        peer_sig = signal_for(peer_uid)
        if peer_sig is None:
            continue
        if not shares_period(signal.period_hours, peer_sig.period_hours, cfg.period_tolerance):
            continue
        aligned, frac = windows_align(signal.active_series, peer_sig.active_series, cfg.peer_overlap_min)
        if not aligned:
            continue
        peer_rec = build_recommendation(peer_uid, peer_sig, identity_for(peer_uid), cfg, with_evidence=False)
        peers.append(Peer(
            workload=peer_rec.workload_name,
            shared_seasonality=True,
            savings_amount=peer_rec.savings_amount,
            to_target=peer_rec.to_target,
            note=f"peaks at the same time (overlap {round(frac * 100)}%)",
        ))
    return peers


def _unique_name(store: StateStore, rng: random.Random) -> str:
    for _ in range(20):
        candidate = generate_name(rng)
        if store._fetchone("SELECT 1 AS x FROM analysis_runs WHERE name = ?", (candidate,)) is None:
            return candidate
    return generate_name(rng)  # extremely unlikely to still collide


def _map_overrides(overrides: Optional[dict]) -> dict:
    """Map a run request's `config` body / CLI flags onto EngineConfig fields."""
    if not overrides:
        return {}
    out: dict[str, Any] = {}
    for k in ("resources", "window", "min_period", "concurrency", "resample_freq"):
        if overrides.get(k) is not None:
            out[k] = overrides[k]
    thr = overrides.get("thresholds") or {}
    mapping = {
        "seasonality_gain": "seasonality_strength_min",
        "band": "band_pct",
        "jump_min": "jump_min",
        "ratio_max": "ratio_max",
        "min_period": "min_period",
    }
    for src, dst in mapping.items():
        if thr.get(src) is not None:
            out[dst] = thr[src]
    return out


def _config_dict(cfg: EngineConfig) -> dict:
    return {
        "resources": cfg.resources,
        "window": cfg.window,
        "min_period": cfg.min_period,
        "thresholds": {
            "seasonality_gain": cfg.seasonality_strength_min,
            "band": cfg.band_pct,
            "jump_min": cfg.jump_min,
            "ratio_max": cfg.ratio_max,
        },
    }
