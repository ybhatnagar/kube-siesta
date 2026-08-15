"""Stage 7 — interaction peer expansion + ad-hoc target detection."""
import pandas as pd

from engine.analysis_core.interaction_graph import (
    adhoc_overlap,
    has_adhoc_inbound,
    shares_period,
    windows_align,
)
from engine.runner import run_analysis
from engine.synth import seed_cluster
from engine.synth.generate import SynthCluster, interaction_fixture, make_candidate

IDX = pd.date_range("2026-01-01", periods=8, freq="1h", tz="UTC")


def _mask(bits):
    return pd.Series([bool(b) for b in bits], index=IDX)


# --- pure helpers ----------------------------------------------------------

def test_shares_period():
    assert shares_period(8.0, 8.2, 0.25)
    assert not shares_period(8.0, 24.0, 0.25)


def test_windows_align():
    a = _mask([1, 1, 0, 0, 0, 0, 0, 0])
    aligned = _mask([1, 1, 0, 0, 0, 0, 0, 0])
    misaligned = _mask([0, 0, 1, 1, 0, 0, 0, 0])
    assert windows_align(a, aligned, 0.5) == (True, 1.0)
    assert windows_align(a, misaligned, 0.5)[0] is False


def test_adhoc_detection():
    target = _mask([1, 1, 0, 0, 0, 0, 0, 0])          # active hours 0–1
    caller_idle = _mask([0, 0, 0, 0, 1, 1, 0, 0])     # active while target is idle
    caller_aligned = _mask([1, 1, 0, 0, 0, 0, 0, 0])  # active while target is active
    assert adhoc_overlap(target, caller_idle) == 1.0
    assert has_adhoc_inbound(target, [caller_idle])
    assert not has_adhoc_inbound(target, [caller_aligned])


# --- expansion through the runner -----------------------------------------

def test_peer_expansion_picks_aligned_peer_only(store):
    # source → peer (aligned) and source → unrelated (phase-shifted): only the peer aligns.
    fx = interaction_fixture()
    cid = seed_cluster(store, fx)
    result = run_analysis(store, cluster=cid, scope="all")

    src_uid = fx.interactions[0]["src_uid"]  # source-svc
    src_name = fx.by_uid(src_uid).name
    src_rec = next(r for r in store.get_recommendations(result.run_id) if r["workload_name"] == src_name)

    peers = store.get_peers(src_rec["id"])
    peer_names = {p["peer_workload"] for p in peers}
    assert peer_names == {"peer-svc"}          # aligned peer surfaced
    assert "unrelated-svc" not in peer_names   # phase-shifted peer excluded
    assert peers[0]["shared_seasonality"] is True
    assert peers[0]["savings_amount"] > 0


def test_adhoc_inbound_flips_target_to_knative(store):
    # caller fires during the workload's idle window → request-driven → Knative.
    w = make_candidate("w-svc", seed=0)                       # active hours 0–1
    caller = make_candidate("caller-svc", seed=5, phase_offset_h=4)  # active hours 4–5 (w's idle)
    cluster = SynthCluster(
        name="adhoc", workloads=[w, caller],
        interactions=[{"src_uid": caller.uid, "dst_uid": w.uid, "avg_count": 10.0}],
    )
    cid = seed_cluster(store, cluster)
    result = run_analysis(store, cluster=cid, scope="all")

    recs = {r["workload_name"]: r for r in store.get_recommendations(result.run_id)}
    assert recs["w-svc"]["to_target"] == "Knative"      # ad-hoc inbound during idle
    assert recs["caller-svc"]["to_target"] == "CronJob"  # no inbound of its own
