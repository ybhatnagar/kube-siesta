"""Pipeline composition — a known candidate yields a recommendation, a steady one doesn't."""
from engine.analysis_core.config import EngineConfig
from engine.recommenders.job.builder import analyze_workload
from engine.analysis_core.prepare import prepare_series
from engine.synth.generate import candidate_workload, noncandidate_workload


def _prepared(resource_points):
    return {res: prepare_series(points, "1h") for res, points in resource_points.items()}


def test_candidate_produces_recommendation():
    cfg = EngineConfig()
    identity = {"namespace": "vmw-costing", "kind": "Deployment", "name": "vmw-costing1",
                "replicas": 2, "requests_cpu_m": 500, "requests_mem_bytes": 1_500_000_000}
    rec = analyze_workload("vmw-costing/Deployment/vmw-costing1", _prepared(candidate_workload()), identity, cfg)

    assert rec is not None
    assert rec.to_target in ("CronJob", "Job", "KEDA")
    assert rec.confidence in ("high", "medium")
    assert "8h" in rec.cadence or "every" in rec.cadence
    assert rec.savings_amount and rec.savings_amount > 0
    # CPU + memory both survive as evidence, spiking together.
    resources = {e.resource for e in rec.evidence}
    assert "cpu" in resources and "memory" in resources
    assert rec.evidence[0].series  # downsampled chart points present


def test_noncandidate_returns_none():
    cfg = EngineConfig()
    rec = analyze_workload("vmw-costing/Deployment/steady-svc", _prepared(noncandidate_workload()), None, cfg)
    assert rec is None
