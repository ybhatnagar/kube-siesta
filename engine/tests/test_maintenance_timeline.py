"""analysis_core.timeline.detect_timeline — recover mask+period even when the
job filters would reject."""
from __future__ import annotations

from engine.analysis_core.config import EngineConfig
from engine.analysis_core.prepare import prepare_series
from engine.analysis_core.timeline import detect_timeline
from engine.synth.generate import candidate_workload, make_busy_noncandidate, noncandidate_workload


def _prep(resources, cfg):
    return {k: prepare_series(v, cfg.resample_freq) for k, v in resources.items()}


def test_periodic_candidate_yields_timeline():
    cfg = EngineConfig()
    tl = detect_timeline(_prep(candidate_workload(), cfg), cfg)
    assert tl is not None
    assert tl.period_hours > 0
    assert tl.active_series.dtype == bool


def test_flat_workload_returns_none():
    cfg = EngineConfig()
    assert detect_timeline(_prep(noncandidate_workload(), cfg), cfg) is None


def test_busy_workload_still_yields_a_timeline():
    """The job pipeline rejects busy-but-periodic workloads (union_max / ratio_max);
    the maintenance timeline keeps them (docs/07 §1 step 5)."""
    cfg = EngineConfig()
    busy = make_busy_noncandidate("chatty-svc", seed=99).resources
    tl = detect_timeline(_prep(busy, cfg), cfg)
    # Even though job would drop this, detect_timeline surfaces a periodic mask
    # so the maintenance scoring can penalize windows where it's active.
    assert tl is not None
    assert tl.period_hours > 0
    assert tl.active_series.any()
