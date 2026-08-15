"""Stage 5 aggregation + Stage 6 cost math."""
import numpy as np

from engine.analysis_core.config import EngineConfig
from engine.analysis_core.aggregate import aggregate
from engine.recommenders.job.cost import estimate


def test_aggregate_union_and_overlap():
    cpu = np.array([True, True, False, False])
    mem = np.array([True, False, False, False])
    agg = aggregate({"cpu": cpu, "memory": mem})
    assert list(agg.union) == [True, True, False, False]
    assert agg.union_frac == 0.5
    # intersection = 1 sample, union = 2 samples -> 0.5
    assert abs(agg.overlap_frac - 0.5) < 1e-9


def test_cost_uses_requests_when_present():
    cfg = EngineConfig(node_cpu_m=4000, node_mem_bytes=16 * 1024 ** 3, node_hourly_cost=0.20, hours_per_month=730)
    cost = estimate(
        idle_fraction=0.75, cfg=cfg,
        requests_cpu_m=500, requests_mem_bytes=1_500_000_000, replicas=2,
    )
    # node_fraction = 0.5*(500/4000 + 1.5e9/1.72e10) ≈ 0.106
    assert 0.09 < cost.node_fraction < 0.12
    assert cost.monthly_cost > 0
    assert abs(cost.monthly_savings - cost.monthly_cost * 0.75) < 0.01


def test_cost_falls_back_to_usage_without_requests():
    cfg = EngineConfig()
    cost = estimate(idle_fraction=0.5, cfg=cfg, usage_cpu_cores=2.0, usage_mem_bytes=4 * 1024 ** 3)
    assert cost.node_fraction > 0
    assert cost.monthly_savings > 0
