"""Synthetic series + fixtures with known answers.

Series = trend + seasonal spikes + noise, additive or multiplicative, with a
configurable period / spike width / phase. Deterministic (seeded) so tests have
known candidates and known non-candidates, plus interaction fixtures where a peer
mirrors a source's seasonality. Fixtures export to CSV/JSON (see synth/export.py)
so the same data can seed the DB directly or drive the CSV import connector.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

DEFAULT_START = "2026-07-01T00:00:00Z"

Points = list  # list[tuple[datetime, float]]


# ---------------------------------------------------------------------------
# Core generator
# ---------------------------------------------------------------------------

@dataclass
class SeriesSpec:
    baseline: float
    spike_height: float             # additive: absolute add; multiplicative: fractional boost
    period_h: int = 8
    spike_width_h: int = 2
    mode: str = "additive"          # "additive" | "multiplicative"
    trend_per_day: float = 0.0      # linear drift added to the baseline over time
    noise: float = 0.0
    phase_offset_h: int = 0         # shift the spike within the period (peer alignment)
    start: str = DEFAULT_START
    hours: int = 336                # 14 days
    freq_h: int = 1
    seed: int = 0


def generate_series(spec: SeriesSpec) -> Points:
    """Build a (timestamp, value) series from a SeriesSpec."""
    rng = np.random.default_rng(spec.seed)
    n = spec.hours // spec.freq_h
    idx = pd.date_range(start=spec.start, periods=n, freq=f"{spec.freq_h}h", tz="UTC")
    hours = np.arange(n) * spec.freq_h

    trend = spec.baseline + spec.trend_per_day * (hours / 24.0)
    phase = (hours - spec.phase_offset_h) % spec.period_h
    mask = phase < spec.spike_width_h

    if spec.mode == "multiplicative":
        vals = trend * (1.0 + spec.spike_height * mask)
    else:
        vals = trend + spec.spike_height * mask

    if spec.noise > 0:
        vals = vals + rng.normal(0.0, spec.noise, n)
    vals = np.clip(vals, 0.0, None)
    return list(zip(idx.to_pydatetime(), vals.tolist()))


# ---------------------------------------------------------------------------
# Backward-compatible convenience series
# ---------------------------------------------------------------------------

def spike_series(
    *, start: str = DEFAULT_START, hours: int = 336, period_h: int = 6, spike_width_h: int = 2,
    baseline: float = 100.0, spike_height: float = 300.0, noise: float = 0.0, seed: int = 0,
) -> Points:
    """A periodic burst: `baseline` plus `spike_height` for `spike_width_h` each period."""
    return generate_series(SeriesSpec(
        baseline=baseline, spike_height=spike_height, period_h=period_h, spike_width_h=spike_width_h,
        noise=noise, seed=seed, start=start, hours=hours,
    ))


def flat_series(
    *, start: str = DEFAULT_START, hours: int = 336, baseline: float = 100.0, noise: float = 5.0, seed: int = 1,
) -> Points:
    """A steady, mostly-flat series — a known non-candidate."""
    return generate_series(SeriesSpec(
        baseline=baseline, spike_height=0.0, period_h=1, spike_width_h=0,
        noise=noise, seed=seed, start=start, hours=hours,
    ))


def candidate_workload(seed: int = 0) -> dict[str, Points]:
    """CPU + memory spiking together every 8h (2h burst) — a known candidate (CronJob).

    active/idle ratio = 2/6 ≈ 0.33 (< ratio_max 0.5); union fraction = 2/8 = 0.25
    (< union_max 0.5), so it passes the filters with margin.
    """
    return {
        "cpu": spike_series(baseline=0.2, spike_height=1.8, noise=0.02, seed=seed, period_h=8, spike_width_h=2),
        "memory": spike_series(baseline=200e6, spike_height=1.2e9, noise=5e6, seed=seed + 100, period_h=8, spike_width_h=2),
    }


def noncandidate_workload(seed: int = 1) -> dict[str, Points]:
    """CPU + memory both steady — a known non-candidate."""
    return {
        "cpu": flat_series(baseline=0.8, noise=0.03, seed=seed),
        "memory": flat_series(baseline=700e6, noise=10e6, seed=seed + 100),
    }


def triple_resource_candidate(seed: int = 0) -> dict[str, Points]:
    """CPU + memory + net_tx spiking together — exercises 3-resource aggregation."""
    res = {
        "cpu": spike_series(baseline=0.2, spike_height=1.8, noise=0.02, seed=seed, period_h=8, spike_width_h=2),
        "memory": spike_series(baseline=200e6, spike_height=1.2e9, noise=5e6, seed=seed + 100, period_h=8, spike_width_h=2),
        "net_tx": spike_series(baseline=1e6, spike_height=5e7, noise=1e4, seed=seed + 200, period_h=8, spike_width_h=2),
    }
    return res


# ---------------------------------------------------------------------------
# Workload + cluster fixtures (known answers)
# ---------------------------------------------------------------------------

@dataclass
class SynthWorkload:
    uid: str
    namespace: str
    kind: str
    name: str
    replicas: int
    requests_cpu_m: int
    requests_mem_bytes: int
    resources: dict            # resource -> Points
    is_candidate: bool
    note: str = ""


@dataclass
class SynthCluster:
    name: str
    workloads: list            # list[SynthWorkload]
    interactions: list = field(default_factory=list)  # {src_uid, dst_uid, avg_count}

    @property
    def candidate_uids(self) -> set:
        return {w.uid for w in self.workloads if w.is_candidate}

    def by_uid(self, uid: str) -> SynthWorkload:
        return next(w for w in self.workloads if w.uid == uid)


def _uid(ns: str, kind: str, name: str) -> str:
    return f"{ns}/{kind}/{name}"


def _spiky_pair(seed, *, period_h=8, spike_width_h=2, mode="additive",
                cpu_trend=0.0, phase_offset_h=0) -> dict[str, Points]:
    """CPU + memory spiking together (aligned) — the candidate shape."""
    return {
        "cpu": generate_series(SeriesSpec(
            baseline=0.2, spike_height=1.8, period_h=period_h, spike_width_h=spike_width_h,
            mode=mode, trend_per_day=cpu_trend, noise=0.02, phase_offset_h=phase_offset_h, seed=seed)),
        "memory": generate_series(SeriesSpec(
            baseline=200e6, spike_height=(1.2e9 if mode == "additive" else 5.0), period_h=period_h,
            spike_width_h=spike_width_h, mode=mode, noise=5e6, phase_offset_h=phase_offset_h, seed=seed + 100)),
    }


def make_candidate(name, seed, *, ns="vmw-costing", period_h=8, spike_width_h=2, mode="additive",
                   cpu_trend=0.0, phase_offset_h=0, replicas=2, req_cpu_m=500, req_mem=1_500_000_000,
                   note="") -> SynthWorkload:
    return SynthWorkload(
        uid=_uid(ns, "Deployment", name), namespace=ns, kind="Deployment", name=name,
        replicas=replicas, requests_cpu_m=req_cpu_m, requests_mem_bytes=req_mem,
        resources=_spiky_pair(seed, period_h=period_h, spike_width_h=spike_width_h, mode=mode,
                              cpu_trend=cpu_trend, phase_offset_h=phase_offset_h),
        is_candidate=True, note=note,
    )


def make_flat_noncandidate(name, seed, *, ns="vmw-costing") -> SynthWorkload:
    return SynthWorkload(
        uid=_uid(ns, "Deployment", name), namespace=ns, kind="Deployment", name=name,
        replicas=3, requests_cpu_m=1000, requests_mem_bytes=2_000_000_000,
        resources={"cpu": flat_series(baseline=0.8, noise=0.03, seed=seed),
                   "memory": flat_series(baseline=700e6, noise=10e6, seed=seed + 100)},
        is_candidate=False, note="steady load",
    )


def make_busy_noncandidate(name, seed, *, ns="vmw-costing") -> SynthWorkload:
    """Periodic + seasonal, but active > 50% of wall-clock — dropped by union/ratio filters."""
    return SynthWorkload(
        uid=_uid(ns, "Deployment", name), namespace=ns, kind="Deployment", name=name,
        replicas=4, requests_cpu_m=800, requests_mem_bytes=1_000_000_000,
        resources={
            "cpu": spike_series(baseline=0.3, spike_height=1.5, noise=0.02, seed=seed, period_h=8, spike_width_h=5),
            "memory": spike_series(baseline=300e6, spike_height=9e8, noise=5e6, seed=seed + 100, period_h=8, spike_width_h=5),
        },
        is_candidate=False, note="active too often to shift",
    )


def synthetic_cluster(name: str = "synth", seed: int = 0) -> SynthCluster:
    """A small cluster with known candidates, non-candidates, and one interaction edge.

    Candidates: costing1 (CronJob), benchmark2 (aligned peer of costing1),
    trending-batch (candidate with an upward CPU trend).
    Non-candidates: steady-svc (flat), chatty-svc (busy > 50% of the time).
    """
    costing1 = make_candidate("vmw-costing1", seed=seed, note="clean 8h burst")
    benchmark2 = make_candidate("vmw-benchmark2", seed=seed + 1, phase_offset_h=0,
                                replicas=1, req_cpu_m=600, req_mem=1_800_000_000,
                                note="downstream peer, aligned seasonality")
    trending = make_candidate("trending-batch", seed=seed + 2, cpu_trend=0.03, note="candidate with trend")
    steady = make_flat_noncandidate("steady-svc", seed=seed + 3)
    chatty = make_busy_noncandidate("chatty-svc", seed=seed + 4)

    interactions = [
        {"src_uid": costing1.uid, "dst_uid": benchmark2.uid, "avg_count": 42.0},
    ]
    return SynthCluster(name=name, workloads=[costing1, benchmark2, trending, steady, chatty],
                        interactions=interactions)


def interaction_fixture(seed: int = 0) -> SynthCluster:
    """A source workload and a peer that mirrors its seasonality at the same time.

    The peer expansion stage should surface `peer` from
    `source` because they share P and their active windows align.
    """
    source = make_candidate("source-svc", seed=seed, note="source")
    peer = make_candidate("peer-svc", seed=seed + 1, phase_offset_h=0, note="mirrors source seasonality")
    misaligned = make_candidate("unrelated-svc", seed=seed + 2, phase_offset_h=4, note="periodic but phase-shifted")
    return SynthCluster(
        name="interactions", workloads=[source, peer, misaligned],
        interactions=[
            {"src_uid": source.uid, "dst_uid": peer.uid, "avg_count": 30.0},
            {"src_uid": source.uid, "dst_uid": misaligned.uid, "avg_count": 5.0},
        ],
    )
