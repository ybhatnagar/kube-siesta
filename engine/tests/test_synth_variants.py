"""Synthetic variants: additive / multiplicative / trending candidates all detected;
interaction-fixture peer mirrors the source's seasonality."""
import numpy as np

from engine.analysis_core.config import EngineConfig
from engine.recommenders.job.builder import analyze_workload
from engine.analysis_core.prepare import prepare_series
from engine.synth.generate import interaction_fixture, make_candidate


def _prep(w):
    return {res: prepare_series(pts, "1h") for res, pts in w.resources.items()}


def test_additive_candidate_detected():
    w = make_candidate("add-svc", seed=0, mode="additive")
    assert analyze_workload(w.uid, _prep(w), None, EngineConfig()) is not None


def test_multiplicative_candidate_detected():
    w = make_candidate("mult-svc", seed=0, mode="multiplicative")
    assert analyze_workload(w.uid, _prep(w), None, EngineConfig()) is not None


def test_trending_candidate_detected():
    w = make_candidate("trend-svc", seed=0, cpu_trend=0.03)
    rec = analyze_workload(w.uid, _prep(w), None, EngineConfig())
    assert rec is not None  # seasonality survives an underlying trend


def test_interaction_fixture_peer_is_aligned():
    fx = interaction_fixture()
    edge = fx.interactions[0]
    src = fx.by_uid(edge["src_uid"])
    peer = fx.by_uid(edge["dst_uid"])
    s = np.array([v for _, v in src.resources["cpu"]])
    p = np.array([v for _, v in peer.resources["cpu"]])
    s_active, p_active = s > s.mean(), p > p.mean()
    jaccard = (s_active & p_active).sum() / max(1, (s_active | p_active).sum())
    assert jaccard > 0.8  # peer spikes at the same time as the source
