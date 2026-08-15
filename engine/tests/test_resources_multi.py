"""Remaining resources: the pipeline aggregates 3 resources (CPU + memory + net_tx)."""
from engine.analysis_core.config import EngineConfig
from engine.recommenders.job.builder import analyze_workload
from engine.analysis_core.prepare import prepare_series
from engine.synth.generate import triple_resource_candidate


def test_three_resource_candidate():
    cfg = EngineConfig(resources=["cpu", "memory", "net_tx"])
    workload = triple_resource_candidate()
    prepared = {res: prepare_series(pts, "1h") for res, pts in workload.items()}

    rec = analyze_workload("vmw-costing/Deployment/three", prepared, None, cfg)
    assert rec is not None
    # All three aligned resources survive as evidence.
    assert {e.resource for e in rec.evidence} == {"cpu", "memory", "net_tx"}
    # Overlap is high because all three spike together.
    assert rec.evidence[0].overlap_pct >= 50.0


def test_extra_resource_ignored_when_not_configured():
    # With only cpu+memory configured, net_tx data is not analyzed.
    cfg = EngineConfig(resources=["cpu", "memory"])
    workload = triple_resource_candidate()
    prepared = {res: prepare_series(pts, "1h") for res, pts in workload.items()}

    rec = analyze_workload("vmw-costing/Deployment/three", prepared, None, cfg)
    assert rec is not None
    assert {e.resource for e in rec.evidence} == {"cpu", "memory"}
